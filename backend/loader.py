"""Fill the database from the Wikipedia Action API.

This is an offline batch job, not part of the web app -- run it before anyone uses the
site, as the README asks. It stores identifiers only (page_id + revid); the HTML is
fetched from the REST API at display time by app.py.

    python -m backend.loader Agapanthus "Michael Jackson"
    python -m backend.loader --reset Agapanthus      # wipe revisions first
    python -m backend.loader --max-revisions 500 "Albert Einstein"   # newest 500 only

The one piece of real logic is build_intervals(): Wikipedia gives MOMENTS ("revision
764138197 was created at 2017-02-07") but the schema wants INTERVALS ("it was live from
2017-02-07 until 2017-03-19"). The closing bound of each revision is the timestamp of
the next one; the newest revision gets the sentinel that marks a row current.

Re-running on an article that already has rows is safe -- PRIMARY KEY (page_id) is
implicitly (page_id, valid_to) on a versioned table, so repeated intervals collide and
are skipped. But topping up an article with NEW revisions is NOT supported: the stored
newest row still claims the sentinel and would overlap. Use --reset for that, which
recreates the table (DELETE alone cannot purge history selectively -- DELETE HISTORY
takes no WHERE clause).
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

if __package__:
    from .db import connect, current_row_valid_to
else:  # allow `python backend/loader.py` as well as `python -m backend.loader`
    sys.path.insert(0, str(Path(__file__).parent))
    from db import connect, current_row_valid_to

API = "https://en.wikipedia.org/w/api.php"
# Wikimedia blocks default library user agents, so identify the project.
USER_AGENT = "wiki_machine/0.1 (student project; https://github.com/iAbdurahmon/wiki_machine)"
REVISIONS_PER_REQUEST = 500  # the API cap for ordinary accounts


def fetch_revisions(client: httpx.Client, title: str, max_revisions: int | None = None):
    """Return (page_id, canonical_title, [(revid, timestamp), ...]) oldest first."""
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "revisions",
        "titles": title,
        "rvprop": "ids|timestamp",
        # Newest first, so that --max-revisions keeps the RECENT history and drops the
        # ancient part. The other direction would leave the sentinel on a decades-old
        # revision, making it claim to still be current. Reversed before use.
        "rvdir": "older",
        "rvlimit": str(REVISIONS_PER_REQUEST),
    }
    revisions: list[tuple[int, str]] = []
    page_id = None
    canonical = title

    while True:
        response = client.get(API, params=params)
        response.raise_for_status()
        payload = response.json()

        page = payload["query"]["pages"][0]
        if page.get("missing"):
            raise LookupError(f"no such article: {title!r}")
        page_id, canonical = page["pageid"], page["title"]
        revisions += [(r["revid"], r["timestamp"]) for r in page.get("revisions", [])]

        if max_revisions and len(revisions) >= max_revisions:
            revisions = revisions[:max_revisions]
            break
        if "continue" not in payload:
            break
        params.update(payload["continue"])
        print(f"    ... {len(revisions)} revisions so far", flush=True)
        time.sleep(0.1)  # be a polite API citizen

    revisions.reverse()  # back to oldest first, which build_intervals() expects
    return page_id, canonical, revisions


def build_intervals(revisions: list[tuple[int, str]], sentinel: str):
    """Turn (revid, timestamp) moments into (revid, valid_from, valid_to) intervals.

    `sentinel` is the server's own "this row is current" value -- see db.current_row_valid_to.
    """
    rows = []
    for index, (revid, timestamp) in enumerate(revisions):
        valid_from = _to_sql(timestamp)
        if index + 1 < len(revisions):
            valid_to = _to_sql(revisions[index + 1][1])
            # Two edits within the same second would give a zero-length interval, which
            # the period column rejects. The later revision supersedes this one anyway.
            if valid_to <= valid_from:
                continue
        else:
            valid_to = sentinel
        rows.append((revid, valid_from, valid_to))
    return rows


def _to_sql(timestamp: str) -> str:
    """'2017-02-07T08:21:34Z' -> '2017-02-07 08:21:34' (already UTC)."""
    return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d %H:%M:%S")


def reset_revisions(conn) -> None:
    """Recreate article_revision. DROP is the only way to purge versioned history.

    Replays only the CREATE TABLE statements from schema.sql -- the account the loader
    runs as owns the tables but deliberately has no CREATE USER or database-level rights,
    which the rest of that file needs.
    """
    source = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    source = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("--")
    )
    statements = [
        statement
        for statement in source.split(";")
        if statement.strip().upper().startswith("CREATE TABLE")
    ]
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS article_revision")
        for statement in statements:
            cur.execute(statement)
    print("article_revision recreated")


def load(titles: list[str], reset: bool = False, max_revisions: int | None = None) -> None:
    conn = connect(for_loading=True)
    if reset:
        reset_revisions(conn)
    sentinel = current_row_valid_to(conn)

    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0) as client:
        for title in titles:
            print(f"\n{title}")
            try:
                page_id, canonical, revisions = fetch_revisions(client, title, max_revisions)
            except LookupError as error:
                print(f"  skipped: {error}")
                continue

            rows = build_intervals(revisions, sentinel)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO articles (page_id, title) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE title = VALUES(title)",
                    (page_id, canonical),
                )
                # INSERT IGNORE turns the expected duplicate-interval collision (1062)
                # into a skip, which is what makes a re-run safe.
                cur.executemany(
                    "INSERT IGNORE INTO article_revision "
                    "(page_id, revid, valid_from, valid_to) VALUES (%s, %s, %s, %s)",
                    [(page_id, revid, start, end) for revid, start, end in rows],
                )
                inserted = cur.rowcount

            print(
                f"  page_id={page_id}  revisions={len(revisions)}  "
                f"inserted={inserted}  skipped={len(rows) - inserted}  "
                f"span={rows[0][1][:10]}..{rows[-1][1][:10]}"
            )

            # A current row must now be visible to a PLAIN select. If it is not, the
            # sentinel was wrong and every row landed in history -- which breaks
            # silently, so fail loudly here instead.
            with conn.cursor() as cur:
                cur.execute("SELECT revid FROM article_revision WHERE page_id = %s", (page_id,))
                current = cur.fetchone()
            if current is None or current["revid"] != rows[-1][0]:
                raise RuntimeError(
                    f"{canonical}: no current row after loading. The server's sentinel is "
                    f"{sentinel!r}; rows went into history and a plain SELECT cannot see them."
                )
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("titles", nargs="+", help="article titles as they appear on Wikipedia")
    parser.add_argument(
        "--reset", action="store_true", help="recreate article_revision before loading"
    )
    parser.add_argument(
        "--max-revisions",
        type=int,
        help="keep only this many of the most recent revisions per article",
    )
    args = parser.parse_args()
    load(args.titles, reset=args.reset, max_revisions=args.max_revisions)


if __name__ == "__main__":
    main()
