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
Wikipedia when you ask for it and never stored, which is the difference between a 3 MB
database and a 60 GB one.

---

## Install and run

Five steps. Commands are for macOS and Linux; the Windows column is right below.

### 1. Install the two things it needs

| | |
|---|---|
| **Python** | 3.10 or newer |
| **MariaDB** | 10.11 or newer — **MySQL will not work**, it has no system-versioned tables |

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
takes two or three minutes: ~170 API calls for the five articles in the list, which come
out as 82,000 rows and 9 MB. (Storing the HTML instead would be tens of gigabytes.)

```bash
.venv/bin/python -m backend.loader
```

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
.venv/bin/python -m backend.loader --reset                   # throw it all away, load the list again
```

`--reset` is also the only way to **update** or **remove** an article, and that is a
real limitation rather than a missing feature. New revisions cannot be appended: the
stored newest row already claims the "this row is current" marker, and because
`PRIMARY KEY (page_id)` is implicitly `(page_id, valid_to)` on a versioned table, the
incoming current row collides and is dropped while the intervals behind it insert fine
and overlap it. Repairing that row would take an `UPDATE` or `DELETE`, and on a versioned
table both write the server's own clock into `valid_to` instead of the value needed —
and `DELETE HISTORY` takes no `WHERE` clause, so history cannot be purged per article.

The loader therefore skips any article already in the database and tells you so. To pick
up edits made since the last load, run `--reset`.

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
Putting the title in the same table would duplicate it across all 33,943 Michael Jackson
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

---

## Layout

```
backend/
  schema.sql        database, tables, app user     — step 3
  loader.py         Action API -> MariaDB          — step 4, offline batch job
  app.py            the API, and serves frontend/  — step 5
  db.py             connection and session settings
  articles.txt      the shared list of articles
  README.md         deeper notes and troubleshooting
frontend/
  wikimachine.HTML  the page
  wikimachine.css   its styling
  wikimachine.js    fills the dropdown, calls the API, points the iframe
```

Connection settings come from the environment, defaulting to the local dev user:
`DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=wiki DB_PASSWORD=wiki_dev_pass DB_NAME=wiki_machine`.
The password is a local development value only.
