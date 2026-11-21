-- The road not taken: the same revisions, modelled with an APPLICATION-TIME period.
--
--     mariadb wiki_machine < examples/application_time.sql        (after the loader)
--
-- wiki_machine stores Wikipedia time in the SYSTEM period (valid_from / valid_to are
-- ROW START / ROW END, written with system_versioning_insert_history). The area brief
-- names a second option: keep Wikipedia time in an application-time period -- ordinary
-- columns the application owns -- and let system versioning record what the DATABASE
-- did and when. Two clocks in one table: that is a bitemporal table.
--
-- This script builds that table from the rows the loader already fetched and puts the
-- two models side by side on four questions. It leaves article_revision untouched, and
-- leaves article_revision_apptime behind for you to explore (DROP it when done).
-- Verified on MariaDB 13.0.2.

SET time_zone = '+00:00';

DROP TABLE IF EXISTS article_revision_apptime;
CREATE TABLE article_revision_apptime (
  page_id   BIGINT UNSIGNED NOT NULL,
  revid     BIGINT UNSIGNED NOT NULL,
  wiki_from DATETIME(6) NOT NULL,      -- application time: plain columns, any type,
  wiki_to   DATETIME(6) NOT NULL,      -- any value, UPDATE-able like anything else
  PERIOD FOR wiki_time (wiki_from, wiki_to),
  -- The guard article_revision cannot have: no two revisions of one article live at
  -- the same instant. On the system period this is a convention the loader enforces;
  -- here the server enforces it.
  UNIQUE KEY one_revision_at_a_time (page_id, wiki_time WITHOUT OVERLAPS)
) ENGINE=InnoDB
  WITH SYSTEM VERSIONING;              -- the second clock: when THIS table changed

-- The current row's system-time sentinel (2106-02-07 ...) becomes an ordinary "open end".
SET @current_end := (SELECT MAX(valid_to) FROM article_revision);
INSERT INTO article_revision_apptime (page_id, revid, wiki_from, wiki_to)
SELECT page_id, revid, valid_from,
       IF(valid_to = @current_end, '9999-12-31 00:00:00', valid_to)
FROM article_revision FOR SYSTEM_TIME ALL;

SELECT (SELECT COUNT(*) FROM article_revision FOR SYSTEM_TIME ALL) AS system_time_rows,
       (SELECT COUNT(*) FROM article_revision_apptime)              AS application_time_rows;


-- ── 1. "Which revision was live on 2009-06-26?" ─────────────────────────────────────
-- Same answer. System time says it with the standard clause and lets the server work out
-- the range; application time has no AS OF in MariaDB, so the range is written by hand.

SET @page := (SELECT page_id FROM articles WHERE title = 'Michael Jackson');

SELECT 'system time' AS model, revid
FROM article_revision FOR SYSTEM_TIME AS OF TIMESTAMP '2009-06-26 23:59:59.999999'
WHERE page_id = @page
UNION ALL
SELECT 'application time', revid
FROM article_revision_apptime
WHERE page_id = @page
  AND wiki_from <= '2009-06-26 23:59:59.999999'
  AND wiki_to   >  '2009-06-26 23:59:59.999999';


-- ── 2. A bad interval: an extra revision overlapping the live one ────────────────────
-- Application time refuses it (INSERT IGNORE turns the error into a warning so this
-- script keeps going; a plain INSERT fails with ERROR 1062). On article_revision the
-- same row would go in silently, AS OF would return two rows, and only the loader's
-- refusal to touch a loaded article stands in the way.

INSERT IGNORE INTO article_revision_apptime (page_id, revid, wiki_from, wiki_to)
VALUES (@page, 1, '2009-06-01 00:00:00', '2009-07-01 00:00:00');
SHOW WARNINGS;


-- ── 3. Wikipedia gets a new edit: append it ─────────────────────────────────────────
-- On article_revision this is impossible in place (the stored current row's valid_to
-- belongs to the server) and --refresh rebuilds the whole table. Here it is one
-- statement: FOR PORTION OF splits the open-ended row at the edit's moment.
-- The revid is invented -- this is what an append WOULD look like.
--
-- Careful: FOR PORTION OF rewrites EVERY row that overlaps the range. An edit moment
-- older than the current revision would overwrite all the revisions after it -- the
-- first draft of this script did exactly that with a fixed date. So the moment is
-- derived: one hour after the current revision began.

SET @edit := (SELECT wiki_from + INTERVAL 1 HOUR FROM article_revision_apptime
              WHERE page_id = @page AND wiki_to = '9999-12-31 00:00:00');
SET @before_edit := NOW(6);
DO SLEEP(0.01);

UPDATE article_revision_apptime
  FOR PORTION OF wiki_time FROM @edit TO '9999-12-31 00:00:00'
  SET revid = 999999999999
WHERE page_id = @page;

SELECT revid, wiki_from, wiki_to
FROM article_revision_apptime
WHERE page_id = @page
ORDER BY wiki_from DESC
LIMIT 3;


-- ── 4. "What did WE believe before that edit arrived?" ──────────────────────────────
-- The question only a bitemporal table can answer: Wikipedia time from the period
-- columns, database time from FOR SYSTEM_TIME. Before the update, the table still said
-- the old revision was live with no end.

SELECT revid, wiki_from, wiki_to
FROM article_revision_apptime FOR SYSTEM_TIME AS OF @before_edit
WHERE page_id = @page AND wiki_to = '9999-12-31 00:00:00';


-- ── Undo the invented edit ──────────────────────────────────────────────────────────
-- Ordinary columns, ordinary statements: delete the invented row, re-open the old one.
-- (System versioning still remembers both steps -- try FOR SYSTEM_TIME ALL.)

DELETE FROM article_revision_apptime WHERE page_id = @page AND revid = 999999999999;
UPDATE article_revision_apptime SET wiki_to = '9999-12-31 00:00:00'
WHERE page_id = @page AND wiki_to = @edit;

SELECT COUNT(*) AS rows_this_article_ever_had
FROM article_revision_apptime FOR SYSTEM_TIME ALL
WHERE page_id = @page;
