"""Database access for wiki_machine.

Two session settings are mandatory and easy to forget:

  time_zone = '+00:00'
      Wikipedia timestamps are UTC. The period columns are TIMESTAMP, which MariaDB
      converts using the session time zone -- without this, every stored date shifts.

  system_versioning_insert_history = ON
      Required to write explicit valid_from / valid_to at all. Only the loader needs it.

See schema.sql for the rest of the constraints this schema depends on.
"""

import os

import pymysql
import pymysql.cursors

# A row is CURRENT only when valid_to holds exactly one magic value: the largest
# TIMESTAMP the server supports. Any other value makes it a history row, invisible to a
# plain SELECT -- and nothing errors, so a wrong value is found days later.
#
# The value is NOT a constant across installs. Older MariaDB tops out at 2038, newer
# servers at 2106 after TIMESTAMP went unsigned. Hardcoding it works on the machine you
# wrote it on and quietly corrupts a teammate's database. So ask the server instead;
# this is only the fallback for error messages.
FALLBACK_CURRENT_ROW_VALID_TO = "2106-02-07 06:28:15.999999"

_sentinel: str | None = None


def current_row_valid_to(conn) -> str:
    """Ask the server what valid_to it stamps on a current row.

    Probes with a throwaway versioned table rather than guessing from @@version.
    Cached for the life of the process.
    """
    global _sentinel
    if _sentinel is not None:
        return _sentinel

    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS _sentinel_probe")
        cur.execute(
            """
            CREATE TABLE _sentinel_probe (
              id        INT PRIMARY KEY,
              row_start TIMESTAMP(6) GENERATED ALWAYS AS ROW START,
              row_end   TIMESTAMP(6) GENERATED ALWAYS AS ROW END,
              PERIOD FOR SYSTEM_TIME(row_start, row_end)
            ) ENGINE=InnoDB WITH SYSTEM VERSIONING
            """
        )
        cur.execute("INSERT INTO _sentinel_probe (id) VALUES (1)")
        cur.execute("SELECT row_end FROM _sentinel_probe WHERE id = 1")
        row_end = cur.fetchone()["row_end"]
        cur.execute("DROP TABLE _sentinel_probe")  # also purges its history

    _sentinel = row_end.strftime("%Y-%m-%d %H:%M:%S.%f")
    return _sentinel

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "user": os.environ.get("DB_USER", "wiki"),
    "password": os.environ.get("DB_PASSWORD", "wiki_dev_pass"),
    "database": os.environ.get("DB_NAME", "wiki_machine"),
}

DUPLICATE_ENTRY = 1062


def connect(for_loading: bool = False) -> pymysql.connections.Connection:
    """Open a connection with the session settings this schema requires."""
    conn = pymysql.connect(
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        **DB_CONFIG,
    )
    with conn.cursor() as cur:
        cur.execute("SET time_zone = '+00:00'")
        if for_loading:
            cur.execute("SET @@system_versioning_insert_history = ON")
    return conn
