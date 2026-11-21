"""Measure how article_revision grows and what partitioning by SYSTEM_TIME does about it.

    python -m backend.growth

Prints Markdown tables, so the output can go straight into the README:

  1. one row per history partition: the Wikipedia year it holds, exact row count, size
  2. totals: current rows vs history rows, bytes per row
  3. the same rows in an UNPARTITIONED copy, side by side -- the baseline. Without it
     every number above is a description, not a finding.
  4. which partitions each query touches and how many rows the optimizer expects to
     examine, partitioned vs unpartitioned, from EXPLAIN PARTITIONS

The baseline is built as article_revision_baseline -- CREATE TABLE ... LIKE, REMOVE
PARTITIONING, a copy with the timestamps kept -- and dropped at the end, so the database
is left as it was found. Nothing in article_revision is changed.

Row counts are exact (COUNT(*) per partition). Sizes come from information_schema after
ANALYZE TABLE and are InnoDB's allocated pages, so a nearly empty partition still reports
16 KB per index: that floor is part of what partitioning costs.
"""

import sys
from pathlib import Path

if __package__:
    from .db import connect
else:
    sys.path.insert(0, str(Path(__file__).parent))
    from db import connect

TABLE = "article_revision"
BASELINE = "article_revision_baseline"
AS_OF_DATES = ["2004-06-30", "2010-06-30", "2016-06-30", "2022-06-30"]


def partitions(cur) -> list[dict]:
    cur.execute(
        """
        SELECT PARTITION_NAME AS name, PARTITION_DESCRIPTION AS upper_bound,
               DATA_LENGTH + INDEX_LENGTH AS bytes
        FROM information_schema.PARTITIONS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        ORDER BY PARTITION_ORDINAL_POSITION
        """,
        (TABLE,),
    )
    parts = cur.fetchall()
    if not parts or parts[0]["name"] is None:
        raise SystemExit(
            f"{TABLE} is not partitioned. Run `python -m backend.loader --reset` "
            "to recreate it from schema.sql."
        )
    for part in parts:
        # The name comes from information_schema, not from input; PARTITION () takes
        # no placeholder.
        cur.execute(f"SELECT COUNT(*) AS n FROM {TABLE} PARTITION ({part['name']})")
        part["rows"] = cur.fetchone()["n"]
    return parts


def label(part: dict) -> str:
    if part["upper_bound"] == "CURRENT":
        return "current"
    return f"superseded in {int(part['upper_bound'][:4]) - 1}"


def kb(n: int) -> str:
    return f"{n / 1024:,.0f} KB"


def explain(cur, sql: str, params) -> tuple[int, int, str]:
    cur.execute("EXPLAIN PARTITIONS " + sql, params)
    plan = cur.fetchone()
    touched = plan["partitions"].split(",") if plan["partitions"] else []
    return len(touched), int(plan["rows"] or 0), plan["partitions"] or ""


def build_baseline(cur) -> int:
    """An unpartitioned copy of the same rows. Returns its allocated size in bytes."""
    cur.execute(f"DROP TABLE IF EXISTS {BASELINE}")
    cur.execute(f"CREATE TABLE {BASELINE} LIKE {TABLE}")
    cur.execute(f"ALTER TABLE {BASELINE} REMOVE PARTITIONING")
    cur.execute(
        f"INSERT INTO {BASELINE} (page_id, revid, valid_from, valid_to) "
        f"SELECT page_id, revid, valid_from, valid_to FROM {TABLE} FOR SYSTEM_TIME ALL"
    )
    cur.execute(f"ANALYZE TABLE {BASELINE}")
    cur.fetchall()
    cur.execute(
        "SELECT DATA_LENGTH + INDEX_LENGTH AS bytes FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (BASELINE,),
    )
    return cur.fetchone()["bytes"]


def queries(page_id: int) -> list[tuple[str, str, tuple]]:
    """(label, SQL with {t} for the table, params). Dates are constants, not input."""
    found = [
        ("plain `SELECT`, one article (its current revision)",
         "SELECT revid FROM {t} WHERE page_id = %s", (page_id,)),
        ("plain `SELECT`, every article (all current revisions)",
         "SELECT page_id, revid FROM {t}", ()),
    ]
    for day in AS_OF_DATES:
        found.append((
            f"`AS OF '{day}'`, one article",
            "SELECT revid FROM {t} "
            f"FOR SYSTEM_TIME AS OF TIMESTAMP '{day} 23:59:59.999999' WHERE page_id = %s",
            (page_id,),
        ))
    return found


def main() -> None:
    # for_loading: the baseline copy writes explicit valid_from / valid_to.
    conn = connect(for_loading=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"ANALYZE TABLE {TABLE}")
            cur.fetchall()
            parts = partitions(cur)

            total_rows = sum(p["rows"] for p in parts)
            total_bytes = sum(p["bytes"] for p in parts)
            current = next(p for p in parts if p["upper_bound"] == "CURRENT")
            filled = [p for p in parts if p["rows"]]

            print(f"## {TABLE}: {len(parts)} partitions, {len(filled)} with rows\n")
            print("| partition | holds revisions | rows | size |")
            print("|---|---|---:|---:|")
            for p in filled:
                print(f"| `{p['name']}` | {label(p)} | {p['rows']:,} | {kb(p['bytes'])} |")
            empty = [p for p in parts if not p["rows"]]
            if empty:
                print(f"| {len(empty)} empty | | 0 | {kb(sum(p['bytes'] for p in empty))} |")

            baseline_bytes = build_baseline(cur)

            print("\n## Totals\n")
            print("| | partitioned | unpartitioned |")
            print("|---|---:|---:|")
            print(f"| rows | {total_rows:,} | {total_rows:,} |")
            print(f"| current rows (one per article) | {current['rows']:,} | |")
            print(f"| history rows | {total_rows - current['rows']:,} | |")
            print(f"| allocated size | {kb(total_bytes)} | {kb(baseline_bytes)} |")
            print(
                f"| bytes per row, all in | {total_bytes / max(total_rows, 1):,.0f} "
                f"| {baseline_bytes / max(total_rows, 1):,.0f} |"
            )

            cur.execute(
                f"""
                SELECT r.page_id, a.title, COUNT(*) AS n
                FROM {TABLE} FOR SYSTEM_TIME ALL r JOIN articles a ON a.page_id = r.page_id
                GROUP BY r.page_id, a.title ORDER BY n DESC LIMIT 1
                """
            )
            biggest = cur.fetchone()

            print(
                f"\n## What a lookup reads -- {biggest['title']} "
                f"({biggest['n']:,} revisions), from EXPLAIN PARTITIONS\n"
            )
            print(
                f"| query | partitions read (of {len(parts)}) "
                "| rows examined, partitioned | rows examined, unpartitioned |"
            )
            print("|---|---:|---:|---:|")
            for text, sql, params in queries(biggest["page_id"]):
                n, rows, _ = explain(cur, sql.format(t=TABLE), params)
                _, base_rows, _ = explain(cur, sql.format(t=BASELINE), params)
                print(f"| {text} | {n} | {rows:,} | {base_rows:,} |")
            print("\nRow counts in the last table are the optimizer's estimates.")
    finally:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {BASELINE}")
        conn.close()


if __name__ == "__main__":
    main()
