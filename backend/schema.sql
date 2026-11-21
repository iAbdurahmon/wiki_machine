-- wiki_machine schema
--
-- Only identifiers are stored: page_id + revid.
-- HTML is not stored; it is fetched from the REST API at display time:
--   https://en.wikipedia.org/w/rest.php/v1/revision/{revid}/html
--
-- The lifespan of each revision is stored in a system-versioned table,
-- so determining "which revision was active on date D" requires only a single
-- FOR SYSTEM_TIME AS OF query, without ORDER BY / LIMIT on timestamps.
--
-- Loader session requirements:
--   SET time_zone = '+00:00'; -- Wikipedia timestamps are in UTC
--   SET @@system_versioning_insert_history = ON; -- allow explicit valid_from/valid_to
--
-- The CURRENT revision needs a sentinel valid_to: the largest TIMESTAMP the server
-- supports ('2106-02-07 06:28:15.999999' here, but older MariaDB stops at 2038).
-- Only with that exact value is a row considered current -- anything else puts it in
-- history, invisible to a plain SELECT, with no error. Never hardcode it: the loader
-- asks the server via db.current_row_valid_to().

CREATE DATABASE IF NOT EXISTS wiki_machine
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- The app logs in as its own user, not as an administrator. This password is a local
-- development one; in production it comes from the environment (see DB_* in db.py).
-- Run this file as a MariaDB administrator -- creating a user needs those rights.
CREATE USER IF NOT EXISTS 'wiki'@'localhost' IDENTIFIED BY 'wiki_dev_pass';
GRANT ALL PRIVILEGES ON wiki_machine.* TO 'wiki'@'localhost';
FLUSH PRIVILEGES;

USE wiki_machine;

CREATE TABLE IF NOT EXISTS articles (
  page_id   BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  title     VARCHAR(255) NOT NULL UNIQUE,
  loaded_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS article_revision (
  page_id    BIGINT UNSIGNED NOT NULL,
  revid      BIGINT UNSIGNED NOT NULL,
  valid_from TIMESTAMP(6) GENERATED ALWAYS AS ROW START,
  valid_to   TIMESTAMP(6) GENERATED ALWAYS AS ROW END,
  PERIOD FOR SYSTEM_TIME(valid_from, valid_to),
-- in the versioned table, the PRIMARY KEY is implicitly extended to (page_id, valid_to),
-- providing free protection against reloading the same interval
  PRIMARY KEY (page_id),
  KEY idx_revid (revid)
-- No FOREIGN KEY to articles: partitioned InnoDB tables refuse one (ERROR 1506). The loader
-- writes the articles row before any revision, which is the guarantee that is left.
) ENGINE=InnoDB WITH SYSTEM VERSIONING
-- One history partition per calendar year of WIKIPEDIA time, 2001 (Wikipedia's founding)
-- to 2027, plus the current partition. A row goes to the partition holding its valid_to,
-- and because the loader backfills real timestamps, that is the year the revision was
-- superseded on Wikipedia -- not the day the loader ran.
--
-- What it buys and costs, measured against an unpartitioned copy (backend/growth.py):
--   * a plain SELECT of every current revision reads one partition: 6 rows, not ~85,000
--   * FOR SYSTEM_TIME AS OF t skips every partition that closed before t -- about half
--     the rows examined for a 2010 or 2016 date, no gain for a recent one
--   * DELETE HISTORY ... BEFORE SYSTEM_TIME and DROP PARTITION remove whole eras at once
--   * cost: ~20% more disk (13.6 MB vs 11.3 MB), 16 KB per partition even when empty
--
-- The partitions are listed (PARTITIONS 28), not AUTO: AUTO only adds a partition when
-- the server's clock crosses an interval boundary, so with a STARTS date in the past it
-- creates a single history partition and every backfilled row lands in it. Revisions
-- superseded after 2027 go to the last history partition until one is added with
--   ALTER TABLE article_revision ADD PARTITION (PARTITION p27 HISTORY);
PARTITION BY SYSTEM_TIME INTERVAL 1 YEAR STARTS '2001-01-01 00:00:00' PARTITIONS 28;
