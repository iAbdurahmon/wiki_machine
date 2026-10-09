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
-- Sentinel valid_to value for the CURRENT revision (MariaDB 13, unsigned 32-bit TIMESTAMP):
--   '2106-02-07 06:28:15.999999'
-- Only with this value is the row considered current. Any other value means the row
-- moves to history and is invisible to a standard SELECT without FOR SYSTEM_TIME.

CREATE DATABASE IF NOT EXISTS wiki_machine
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

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
  KEY idx_revid (revid),
  CONSTRAINT fk_article FOREIGN KEY (page_id) REFERENCES articles(page_id)
) ENGINE=InnoDB WITH SYSTEM VERSIONING;
