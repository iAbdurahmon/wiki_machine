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

# A row is CURRENT only when valid_to holds exactly this value (max unsigned 32-bit
# TIMESTAMP in MariaDB 13). Any other value makes it a history row, invisible to a
# plain SELECT. Getting this wrong fails silently, so it lives in one place.
CURRENT_ROW_VALID_TO = "2106-02-07 06:28:15.999999"

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
