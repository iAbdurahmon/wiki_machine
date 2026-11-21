# wiki_machine
project 3 System-versioned tables: history for free for mariaDB student project 2026

the project does not have any features that could be usefully graphed or otherwise measured, it works fine and is quite versatile
due to its ability to download and display any article it can

the aim for the visual elements of the project was to faithfully 
recreate wikipidea's modern style both for the articles and the student made page itself ( we thought this would be fun and fitting)
photos and links still work, though the HTML styling was standardised to the modern version using minerva

we used system versioning and changed the dates to reflect when the
revision of an article was added/changed instead of using it to track when we added the articles into our database (which would be useless as we download them at the same time)

here is a screenshot of the website rendered on my computer
image.png

made by abdurakhmon rakhmanov and nikoloz tsikaridze



Pick a Wikipedia article and a date, and see the article as it looked on that date.

A student project by two students. The database stores **identifiers only** — which
revision of an article was live during which interval of time. The HTML is fetched from
Wikipedia when you ask for it and never stored, which is the difference between a 14 MB
database and a 60 GB one.

---

## Install and run

Five steps. Commands are for macOS and Linux; the Windows column is right below.

### 1. Install the two things it needs

| | |
|---|---|
| **Python** | 3.10 or newer |
| **MariaDB** | 10.11 or newer — **MySQL will not work**, it has no system-versioned tables. Developed and measured on **MariaDB 13.0.2** (Homebrew, macOS, Apple Silicon) |

```bash
# macOS
brew install mariadb && brew services start mariadb

# Debian / Ubuntu
sudo apt install mariadb-server python3-venv && sudo systemctl start mariadb
```

### 2. Get the code and its Python packages

```bash
git clone https://github.com/iAbdurahmon/wiki_machine.git
cd wiki_machine
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

### 3. Create the database

This creates the database, both tables, and the `wiki` user the app logs in as. It is
idempotent — re-running it changes nothing.

```bash
mariadb < backend/schema.sql
```

If that says *access denied*, your account has no admin rights on the server: use
`sudo mariadb < backend/schema.sql`.

### 4. Fill it from Wikipedia

Nothing works before this — the site reads the database, not Wikipedia's search. It
takes two or three minutes: ~180 API calls for the six articles in the list, which come
out as about 84,700 rows and 14 MB. (Storing the HTML instead would be tens of gigabytes.)

```bash
.venv/bin/python -m backend.loader
```

Loaded your database before `article_revision` was partitioned? `schema.sql` will not
alter an existing table, so run `.venv/bin/python -m backend.loader --reset` once — it
recreates the table from the current schema and loads the list again.

### 5. Start the server

```bash
.venv/bin/uvicorn backend.app:app
```

Open **http://127.0.0.1:8000/** . Pick an article, pick a date, press the button.

### The same five steps on Windows

PowerShell uses `.venv\Scripts\` instead of `.venv/bin/`, and has no `<` redirection,
so the schema goes in through a pipe. MariaDB's Windows installer sets a root password,
hence `-u root -p`:

```powershell
git clone https://github.com/iAbdurahmon/wiki_machine.git
cd wiki_machine
python -m venv .venv
.venv\Scripts\pip install -r backend\requirements.txt
Get-Content backend\schema.sql | mariadb -u root -p
.venv\Scripts\python -m backend.loader
.venv\Scripts\uvicorn backend.app:app
```

If PowerShell says **"the term 'mariadb' is not recognized"**, it is a PATH problem and
not a missing install — [backend/README.md](backend/README.md#on-windows) walks through it.

---

## Adding more articles

**Any article on English Wikipedia works** — nothing has to be prepared for it. Put the
title in [`backend/articles.txt`](backend/articles.txt), one per line, spelled the way
Wikipedia spells it in the URL. Redirects are followed, so `NYC` loads New York City,
and the first letter is case-insensitive. A title that does not exist is reported and
skipped, and the rest still load. Then:

```bash
.venv/bin/python -m backend.loader
```

Articles already in the database are skipped, so this only fetches the new ones — a few
seconds, not another two minutes. `articles.txt` is shared, so everyone who pulls and
re-runs the loader ends up with the same database.

Two other forms, when you need them:

```bash
.venv/bin/python -m backend.loader "Pluto" "Mount Everest"   # load these now, list untouched
.venv/bin/python -m backend.loader --refresh Tashkent        # pick up edits made since it was loaded
.venv/bin/python -m backend.loader --reset                   # throw it all away, load the list again
```

### Why updating an article needs `--refresh`

New revisions cannot be appended to an article that is already loaded. The stored newest
row already holds the "this row is current" marker, and because `PRIMARY KEY (page_id)` is
implicitly `(page_id, valid_to)` on a versioned table, the incoming current row collides
and is dropped while the intervals behind it insert fine and overlap it. Repairing that
row in place would take an `UPDATE` or `DELETE`, and on a versioned table both write the
server's own clock into `valid_to` instead of the value needed. `DELETE HISTORY` takes no
`WHERE` clause, so history cannot be purged per article either.

So a plain load skips any article already in the database and says so. `--refresh` goes
around the problem instead of through it:

1. `CREATE TABLE article_revision_new LIKE article_revision` copies the columns, the system
   versioning and the partitioning.
2. `INSERT … SELECT … FOR SYSTEM_TIME ALL` copies every *other* article's rows, history
   included, with their timestamps intact (`system_versioning_insert_history` is on).
3. The refreshed articles' fresh intervals go in, and each is checked for exactly one
   current row.
4. `RENAME TABLE` swaps the two names in one atomic step and the old table is dropped.

Until the swap the live table keeps answering queries, and a failed check leaves it
untouched. The cost is copying the whole table, not just the article that changed — the
price of letting the server own the clock (see
[Why system time](#why-system-time-and-not-an-application-time-period)).

---

## How it works

```
  loader.py  ──  Action API      ──>  MariaDB            (offline, run once)
                                       page_id + revid + the interval it was live

  browser    ──  app.py          ──>  MariaDB            "what was live on 2009-06-26?"
             <─  app.py          ──   REST API           that revision's HTML, patched
```

**Two tables.** `articles` holds the things that do not change — `page_id` and title.
`article_revision` holds what does: which revision is current. It is declared `WITH
SYSTEM VERSIONING`, so MariaDB keeps every superseded row in a hidden history partition
and answers `FOR SYSTEM_TIME AS OF TIMESTAMP '...'` with the row that was live then.
Putting the title in the same table would duplicate it across all 33,959 Michael Jackson
rows, and the question "which revision was live then" is one `SELECT`, not a join in a
loop. An article is identified by `page_id`, never by its title — Wikipedia upper-cases
only a title's first letter, so `tashkent` and `Tashkent` are the same article while
`michael jackson` and `Michael Jackson` are two different pages.

**Why not one date column?** One column gives you the moment a revision appeared. To
find the one that was live on a date you would then need a correlated subquery picking
the newest revision not after that date. The period columns `valid_from`/`valid_to` make
it a range lookup the server does for you — and the loader's one piece of real work is
turning Wikipedia's *moments* ("revision 764138197 was created at 2017-02-07") into
*intervals* ("it was live from 2017-02-07 until 2017-03-19").

**A date means the end of that day.** Asking at `00:00` would miss the day an article
was created — Michael Jackson's first revision landed at 07:48 — so the first day the
date picker offers would answer "too early".

**The HTML needs patching** before it can go in an iframe: Wikipedia's `<base href>` has
no protocol, the stylesheet link is root-relative and therefore breaks with it, and
there is no viewport meta. `patch_head()` in `backend/app.py` fixes those three things.

| Endpoint | Purpose |
|---|---|
| `GET /api/articles` | loaded articles, with each one's first and last date |
| `GET /api/revision?page_id=&date=YYYY-MM-DD` | which revision was live at the end of that day |
| `GET /api/revision/{revid}/html` | that revision, `<head>` patched for embedding |
| `GET /api/revision/{revid}/html?full=true` | the full page with Wikipedia's own skin |

Interactive API docs while the server runs: http://127.0.0.1:8000/docs

**What this replaces.** Without system versioning, the same app needs a revisions table
whose `valid_to` the application code (or a trigger) closes on every edit. Every
point-in-time read then needs a correlated "newest revision not after D" subquery. Here
that is one table definition and one clause, `FOR SYSTEM_TIME AS OF`, and a plain
`SELECT` returns the current revision with no `WHERE` on dates at all.

---

## Growth, and partitioning by `SYSTEM_TIME`

A versioned table only grows: every Wikipedia edit adds a row and none is ever removed.
Six articles come to 84,710 rows, and 84,704 of them are history.

`article_revision` is partitioned by `SYSTEM_TIME`, one history partition per year from
2001 to 2027 plus the current partition ([`backend/schema.sql`](backend/schema.sql)).
MariaDB puts a row in the partition that holds its `valid_to`. **This only means
something because of the time model:** the loader backfills Wikipedia's real timestamps,
so a partition holds the revisions Wikipedia superseded in that year. With import-time
stamps, all 84,710 rows would have been superseded on the afternoon the loader ran, and
they would all sit in one partition.

| revisions superseded on Wikipedia in | rows | |
|---|---:|---|
| 2001–2005 | 12,471 | |
| 2006–2010 | 43,544 | Wikipedia's editing boom: 2006 alone is 14,673 |
| 2011–2015 | 9,807 | |
| 2016–2020 | 7,919 | |
| 2021–2025 | 9,605 | |
| 2026 so far | 1,358 | |
| still current | 6 | one per article, in the current partition |

Measured against an unpartitioned copy of the same rows. The `growth.py` script builds
that copy, measures both tables, and drops the copy:

| | partitioned | unpartitioned |
|---|---:|---:|
| allocated size | 13.6 MB | 11.3 MB |
| plain `SELECT` of every current revision: rows examined | **6** | 85,946 |
| plain `SELECT` of one article's current revision | 1 | 1 |
| `AS OF '2004-06-30'`, Michael Jackson: rows examined | 50,273 (25 of 28 partitions) | 67,982 |
| `AS OF '2010-06-30'` | **9,077** (19 partitions) | 18,812 |
| `AS OF '2016-06-30'` | **5,529** (13 partitions) | 11,146 |
| `AS OF '2022-06-30'` | 2,116 (7 partitions) | 2,115 |

What that says:

- **Current data stays small however long history gets.** Anything that reads current
  rows without a `page_id` touches 6 rows instead of the whole table. A lookup by
  `page_id` gains nothing: the primary key `(page_id, valid_to)` already finds it directly.
- **`AS OF t` skips every partition that closed before `t`**, and only those. A row
  superseded in 2020 may have started before `t`, so later partitions cannot be ruled
  out. The gain is about half for mid-history dates and nothing for recent ones, where
  the primary-key range scan was already short.
- **It costs disk:** about 20% more here, because InnoDB allocates at least 16 KB per index
  in every partition, the empty ones included. At six articles that floor matters; at
  thousands it would not.
- **History can be dropped by era:** `DELETE HISTORY FROM article_revision BEFORE
  SYSTEM_TIME '2006-01-01'` clears every article's pre-2006 revisions at once, or
  `ALTER TABLE … DROP PARTITION` the oldest years. Only the oldest can be dropped
  (`ERROR 4116`). A time machine would not want to; a log table would.

What did not work, and why the schema looks the way it does:

- **`AUTO` partitioning does nothing for backfilled history.** It adds a partition when
  the server's clock crosses a boundary. With `STARTS` in the past it creates one history
  partition and every row lands there, with no warning. The partitions are listed instead
  (`PARTITIONS 28`); after 2027, `ALTER TABLE article_revision ADD PARTITION (PARTITION
  p27 HISTORY)` adds the next year.
- **Partitioning removed the foreign key** to `articles`. Partitioned InnoDB tables refuse
  one (`ERROR 1506`).
- **`OPTIMIZE TABLE` is refused** on a versioned table unless
  `system_versioning_alter_history` is changed first.

These are single runs on one laptop. Row counts are the optimizer's estimates from
`EXPLAIN PARTITIONS`, and the numbers drift slightly as Wikipedia keeps editing. To
reproduce them on your own load:

```bash
.venv/bin/python -m backend.growth
```

---

## Why system time, and not an application-time period

The central design question is how Wikipedia's dates get into the table. There were
three options:

| option | what `valid_from` would mean | |
|---|---|---|
| replay revisions in order | the moment the loader ran | rejected: `AS OF '2009-06-26'` would find nothing, since every row would date from 2026 |
| **`system_versioning_insert_history`** | **the moment Wikipedia saved the edit** | **used** |
| application-time period | the same, in ordinary columns; system time then records when *our* table changed | built for comparison in [`examples/application_time.sql`](examples/application_time.sql) |

**Why system time is the right fit here:**

- **A Wikipedia revision timestamp already is a transaction time.** It is the moment
  MediaWiki's database committed the edit. We are moving one system's history into
  another, not modelling when something was true in the world. Application time is for
  facts that get corrected after the fact ("the price was really valid from March"), and
  `FOR PORTION OF` exists for those corrections. A revision is never corrected after the
  fact, so that machinery would have nothing to do.
- **The standard clause keeps its meaning.** `FOR SYSTEM_TIME AS OF '2009-06-26'` means
  2009 on Wikipedia, and the server works out the range. MariaDB has no `AS OF` for
  application periods: the range predicate is written by hand, and `FOR SYSTEM_TIME`
  would only answer "when did our loader run".
- **Partitioning by `SYSTEM_TIME` becomes a partitioning by era** (previous section).
  Under the other two options it only records when the loader ran.

**What it costs, and what an application-time period would do instead:**

| | system time (this project) | application time |
|---|---|---|
| a new revision appears | impossible in place; `--refresh` copies the table and swaps it | one `UPDATE … FOR PORTION OF` |
| two revisions live at once | prevented only by the loader | `UNIQUE (page_id, wiki_time WITHOUT OVERLAPS)` rejects it |
| "current" marker | the server's maximum `TIMESTAMP`, which differs by version | any value you choose, e.g. `9999-12-31` |
| column type | `TIMESTAMP(6)` only: UTC session required, 1970–2106 | any `DATE`/`DATETIME` |
| fix or delete one article | only by rebuilding the table | ordinary `UPDATE`/`DELETE` |
| "what did our database say last week?" | not answerable: system time is spent on Wikipedia's time | answerable, if the table is also system-versioned (bitemporal) |

**Where it stops being the right answer:** as soon as the upstream data keeps changing.
For a one-time migration of immutable history, which this project is, system time is the
better fit. For an archive kept in sync with Wikipedia indefinitely, the bitemporal table
wins: every edit is an append, and the database also knows when it learned of it.

`examples/application_time.sql` builds that bitemporal table from the rows the loader
fetched, and puts the two side by side: the same revision for 2009-06-26, an overlapping
interval rejected, a new revision appended in one statement, and "what did we believe
before that edit?" answered with `FOR SYSTEM_TIME`.

```bash
mariadb wiki_machine < examples/application_time.sql
```

It does not touch `article_revision`; drop `article_revision_apptime` when done.

---

## Layout

```
backend/
  schema.sql        database, tables, app user     — step 3
  loader.py         Action API -> MariaDB          — step 4, offline batch job
  app.py            the API, and serves frontend/  — step 5
  db.py             connection and session settings
  growth.py         measures partitions, size, and which partitions a query reads
  articles.txt      the shared list of articles
  README.md         deeper notes and troubleshooting
examples/
  application_time.sql  the same data as a bitemporal table, side by side
frontend/
  wikimachine.HTML  the page
  wikimachine.css   its styling
  wikimachine.js    fills the dropdown, calls the API, points the iframe
```

Connection settings come from the environment, defaulting to the local dev user:
`DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=wiki DB_PASSWORD=wiki_dev_pass DB_NAME=wiki_machine`.
The password is a local development value only.
