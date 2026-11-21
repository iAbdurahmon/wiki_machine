"""Fill the database from the Wikipedia Action API.

This is an offline batch job, not part of the web app -- run it before anyone uses the
site, as the README asks. It stores identifiers only (page_id + revid); the HTML is
fetched from the REST API at display time by app.py.

    python -m backend.loader                         # articles.txt, skipping what is loaded
    python -m backend.loader Agapanthus "Michael Jackson"
    python -m backend.loader --refresh Tashkent      # pick up edits made since it was loaded
    python -m backend.loader --reset                 # wipe revisions and load the list again
    python -m backend.loader --reset --max-revisions 500   # newest 500 each, quick to test

To add an article: put its title in articles.txt and run the loader. Articles already
in the database are skipped, so that run only fetches the new one -- which matters,
because re-reading Michael Jackson is 68 API calls.

The one piece of real logic is build_intervals(): Wikipedia gives MOMENTS ("revision
764138197 was created at 2017-02-07") but the schema wants INTERVALS ("it was live from
2017-02-07 until 2017-03-19"). The closing bound of each revision is the timestamp of
the next one; the newest revision gets the sentinel that marks a row current.

Why an article cannot simply be topped up in place: PRIMARY KEY (page_id) is implicitly
(page_id, valid_to) on a versioned table, so a new current row collides with the stored
one and is dropped -- while the intermediate intervals insert fine and OVERLAP it. Two
rows are then live at the same instant, FOR SYSTEM_TIME AS OF returns both, and the API
quietly reads one of them. Repairing the stored current row in place would take an UPDATE
or DELETE, and on a versioned table both write the server's own clock into valid_to rather
than the value we need; DELETE HISTORY takes no WHERE clause, so history cannot be purged
per article either.

So the plain load refuses to touch a loaded article, and --refresh goes around the
problem instead of through it: replace_articles() builds a NEW versioned table, copies
every other article's rows into it with their timestamps intact, writes the refreshed
articles' fresh intervals, checks it, and swaps it in with one atomic RENAME TABLE. The
live table answers queries untouched until the swap, and a failed check leaves it as it was.
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

ARTICLE_LIST = Path(__file__).parent / "articles.txt"
API = "https://en.wikipedia.org/w/api.php"
# Wikimedia blocks default library user agents, so identify the project.
USER_AGENT = "wiki_machine/0.1 (student project; https://github.com/iAbdurahmon/wiki_machine)"
REVISIONS_PER_REQUEST = 500  # the API cap for ordinary accounts
SKIPPED = (
    "already in the database -- skipped. To pick up edits made since it was loaded, "
    "run the loader with --refresh and this title."
)
LIVE = "article_revision"
REBUILD = "article_revision_new"
RETIRED = "article_revision_old"


def fetch_revisions(client: httpx.Client, title: str, max_revisions: int | None = None):
    """Return (page_id, canonical_title, [(revid, timestamp), ...]) oldest first."""
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "revisions",
        "titles": title,
        # Follow redirects. Without this, "NYC" loads the redirect PAGE -- a few
        # revisions whose content is "#REDIRECT [[New York City]]" -- rather than the
        # city, and the iframe shows a stub. page["title"] then carries the target.
        "redirects": "1",
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


def load(
    titles: list[str],
    reset: bool = False,
    refresh: bool = False,
    max_revisions: int | None = None,
) -> None:
    conn = connect(for_loading=True)
    if reset:
        reset_revisions(conn)
    sentinel = current_row_valid_to(conn)
    # --reset and --refresh both mean "fetch these again", so nothing is skipped. Note
    # that --reset recreates article_revision but not articles, so the titles are still
    # in there -- this cannot be left to loaded_articles() coming back empty.
    skip = not (reset or refresh)
    already, already_ids = loaded_articles(conn) if skip else (set(), set())
    batch: list[tuple[int, str, list]] = []

    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0) as client:
        for title in titles:
            print(f"\n{title}")
            # Fast path: an exact title match saves the API calls. Underscores and
            # spaces are the same title to Wikipedia, but not to a string compare.
            if title.replace("_", " ") in already:
                print(f"  {SKIPPED}")
                continue
            try:
                page_id, canonical, revisions = fetch_revisions(client, title, max_revisions)
            except LookupError as error:
                print(f"  skipped: {error}")
                continue
            if canonical != title:
                print(f"  -> {canonical}")
            # The real check, now that the identity is known and before anything is
            # written: a different spelling, or a redirect, reaches a loaded article.
            if page_id in already_ids:
                print(f"  {SKIPPED}")
                continue
            if any(page_id == queued for queued, _, _ in batch):
                print("  same article as an earlier title -- skipped")
                continue

            rows = build_intervals(revisions, sentinel)
            if refresh:
                print(f"  page_id={page_id}  revisions={len(revisions)}  fetched")
                batch.append((page_id, canonical, rows))
                continue

            with conn.cursor() as cur:
                inserted = write_article(cur, LIVE, page_id, canonical, rows)
            print(
                f"  page_id={page_id}  revisions={len(revisions)}  "
                f"inserted={inserted}  skipped={len(rows) - inserted}  "
                f"span={rows[0][1][:10]}..{rows[-1][1][:10]}"
            )
            check_current(conn, LIVE, page_id, canonical, rows, sentinel)

    if refresh and batch:
        replace_articles(conn, batch, sentinel)
    conn.close()


def write_article(cur, table: str, page_id: int, canonical: str, rows) -> int:
    """Upsert the article and insert its intervals into `table`. Returns rows inserted."""
    cur.execute(
        "INSERT INTO articles (page_id, title) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE title = VALUES(title)",
        (page_id, canonical),
    )
    # INSERT IGNORE turns the expected duplicate-interval collision (1062) into a skip,
    # which is what makes a re-run safe. `table` is one of the constants above, never input.
    cur.executemany(
        f"INSERT IGNORE INTO {table} "
        "(page_id, revid, valid_from, valid_to) VALUES (%s, %s, %s, %s)",
        [(page_id, revid, start, end) for revid, start, end in rows],
    )
    return cur.rowcount


def check_current(conn, table: str, page_id: int, canonical: str, rows, sentinel: str) -> None:
    """A current row must be visible to a PLAIN select, and it must be the newest revision.

    If it is not, the sentinel was wrong and every row landed in history -- which breaks
    silently, so fail loudly here instead.
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT revid FROM {table} WHERE page_id = %s", (page_id,))
        current = cur.fetchall()
    if len(current) != 1 or current[0]["revid"] != rows[-1][0]:
        raise RuntimeError(
            f"{canonical}: expected exactly one current row (revid {rows[-1][0]}), found "
            f"{[row['revid'] for row in current]}. The server's sentinel is {sentinel!r}."
        )


def replace_articles(conn, batch, sentinel: str) -> None:
    """Swap fresh history for the articles in `batch`, leaving every other article as is.

    In-place repair is impossible on a versioned table (see the module docstring), so this
    builds the table again beside the live one and swaps:

      1. CREATE TABLE ... LIKE copies the columns, the system versioning AND the
         partitioning -- the new table is the same kind of table, not a lookalike.
      2. INSERT ... SELECT ... FOR SYSTEM_TIME ALL copies every other article, history
         included. With system_versioning_insert_history on (db.connect(for_loading=True))
         the original valid_from / valid_to are kept instead of being restamped "now".
      3. The refreshed articles' intervals go in, and each is checked.
      4. RENAME TABLE swaps both names in one atomic step, so a reader sees the old table
         or the new one, never neither.

    The cost is a copy of the whole table -- about a second for 82,000 rows -- rather than
    of the articles that changed. That is the price of letting the server own the clock.
    """
    ids = [page_id for page_id, _, _ in batch]
    marks = ", ".join(["%s"] * len(ids))
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {REBUILD}")
            cur.execute(f"CREATE TABLE {REBUILD} LIKE {LIVE}")
            cur.execute(
                f"INSERT INTO {REBUILD} (page_id, revid, valid_from, valid_to) "
                f"SELECT page_id, revid, valid_from, valid_to "
                f"FROM {LIVE} FOR SYSTEM_TIME ALL WHERE page_id NOT IN ({marks})",
                ids,
            )
            print(f"\ncopied {cur.rowcount} rows of the other articles into {REBUILD}")
            for page_id, canonical, rows in batch:
                inserted = write_article(cur, REBUILD, page_id, canonical, rows)
                print(
                    f"  {canonical}: inserted={inserted}  "
                    f"span={rows[0][1][:10]}..{rows[-1][1][:10]}"
                )
        for page_id, canonical, rows in batch:
            check_current(conn, REBUILD, page_id, canonical, rows, sentinel)
    except Exception:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {REBUILD}")
        print(f"refresh failed -- {LIVE} was not touched")
        raise

    with conn.cursor() as cur:
        cur.execute(f"RENAME TABLE {LIVE} TO {RETIRED}, {REBUILD} TO {LIVE}")
        cur.execute(f"DROP TABLE {RETIRED}")  # DROP is also the only way to purge its history
    print(f"swapped in: {LIVE} now holds the refreshed articles")


def loaded_articles(conn) -> tuple[set[str], set[int]]:
    """What is already loaded, by title and by page_id.

    Both are needed. The title match avoids the API calls entirely, but it cannot be
    trusted on its own: Wikipedia only upper-cases the FIRST letter of a title, so
    "tashkent" is a perfectly good way to ask for the stored "Tashkent" and would slip
    past a string compare. page_id is the real identity, and it is only known after the
    fetch -- which is still before anything is written.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT page_id, title FROM articles")
        rows = cur.fetchall()
    return {row["title"] for row in rows}, {row["page_id"] for row in rows}


def read_article_list() -> list[str]:
    """The shared list every machine loads, so databases come out the same."""
    if not ARTICLE_LIST.exists():
        raise SystemExit(f"no titles given and {ARTICLE_LIST.name} is missing")
    titles = [
        line.strip()
        for line in ARTICLE_LIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not titles:
        raise SystemExit(f"{ARTICLE_LIST.name} lists no articles")
    return titles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "titles",
        nargs="*",
        help="article titles as they appear on Wikipedia; defaults to articles.txt",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="recreate article_revision and load every title again from scratch",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="fetch these titles again and swap their history in, keeping every other article",
    )
    parser.add_argument(
        "--max-revisions",
        type=int,
        help="keep only this many of the most recent revisions per article",
    )
    args = parser.parse_args()
    if args.reset and args.refresh:
        parser.error("--reset already reloads everything; use one or the other")
    titles = args.titles or read_article_list()
    load(titles, reset=args.reset, refresh=args.refresh, max_revisions=args.max_revisions)


if __name__ == "__main__":
    main()
