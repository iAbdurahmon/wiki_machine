# backend

Install and run instructions are in the [root README](../README.md). This file is the
deeper notes: what the server must support, what to do when something fails, and the
constraints that are not obvious from reading the code.

The database stores identifiers only — `page_id` and `revid`. HTML is fetched from
Wikipedia on request and rewritten by the proxy, never stored.

## What MariaDB has to support

Check the server directly rather than going by version number. It must know this
variable, which is what lets the loader write history rows:

```bash
mariadb -e "SHOW VARIABLES LIKE 'system_versioning_insert_history'"
```

An empty result means the server is too old and the loader cannot write anything.
**MySQL will not work at all** — it has no system-versioned tables.

## Troubleshooting

### `ERROR 1698: Access denied for user 'root'@'localhost'`

On macOS and Linux the local MariaDB uses unix_socket authentication, so there is no
root password to give. Either run `mariadb` as your own user (Homebrew grants it full
rights) or use `sudo mariadb`.

### `ERROR 2002 (HY000): Can't connect to server on 'localhost' (10061)`

The server is not running. `brew services start mariadb` on macOS,
`sudo systemctl start mariadb` on Linux, `Get-Service -Name "MariaDB*"` to check on
Windows. The same thing reaches the app as PyMySQL's
`(2003, "Can't connect to MySQL server on '127.0.0.1'")` and every endpoint returning
HTTP 500.

### `Access denied for user 'wiki'@'localhost'`

The `wiki` account does not exist yet, which means `schema.sql` has not been run against
this server — see step 3 of the root README. A clone from before the schema created that
user needs a `git pull` first.

### On Windows

PowerShell has no `<` input redirection, so the schema goes in through a pipe, and
MariaDB's Windows installer sets a root password rather than using passwordless local
auth:

```powershell
Get-Content backend\schema.sql | mariadb -u root -p
```

**"The term 'mariadb' is not recognized"** means PowerShell cannot find the client, not
that MariaDB is missing. In order of likelihood:

1. The terminal was open before MariaDB was installed. PowerShell reads PATH once at
   startup. Close it, reopen, try `mariadb --version`. This is usually it.
2. Use the "MariaDB ... Command Prompt" shortcut from the Start menu, where PATH is
   already set.
3. Find the client and add it to PATH yourself — the version in the path will differ
   from the example, so take it from the first command's output:

   ```powershell
   Get-ChildItem "C:\Program Files\MariaDB*\bin\maria*.exe" | Select-Object FullName
   [Environment]::SetEnvironmentVariable(
       "Path", $env:Path + ";C:\Program Files\MariaDB 13.0\bin", "User")
   ```

   Then reopen the terminal.

Older builds ship the client as `mysql.exe` rather than `mariadb.exe`; the arguments are
the same.

## The loader

An offline batch job — run it before anyone uses the site. The database is not in git,
so every machine loads its own copy; `articles.txt` is the shared list that keeps those
copies identical.

```bash
.venv/bin/python -m backend.loader                  # the list, skipping what is already loaded
.venv/bin/python -m backend.loader Agapanthus "Michael Jackson"   # load these now
.venv/bin/python -m backend.loader --refresh Tashkent   # pick up edits made since it was loaded
.venv/bin/python -m backend.loader --reset          # drop the revisions, load the list again
.venv/bin/python -m backend.loader --reset --max-revisions 1500   # quick, for testing
```

An article already in the database is skipped, so adding a title to `articles.txt` and
re-running fetches only that one. That skip is a correctness guard as much as a speed
one — see the fourth constraint below for why revisions cannot be appended to an article
that is already loaded, and `--refresh` for the way around it. Titles must match Wikipedia's own, as they appear in the URL; a
title that does not exist is reported and skipped, and the rest still load.

`--max-revisions` keeps the most **recent** revisions, not the oldest — otherwise the
"this row is current" sentinel would land on a decades-old revision that then claims to
still be live.

## Constraints worth knowing before you change the schema

These six cost real debugging time, and four of them fail silently:

1. **Period columns must be `TIMESTAMP(6)`, not `DATETIME(6)`** — `DATETIME` gives
   `ERROR 4110`. `TIMESTAMP` is stored as UTC and converted on the way out, which is why
   `db.connect()` sets `time_zone = '+00:00'` on every connection. Without that, the
   rows shift by the client's offset.

2. **The "current row" marker is the server's maximum timestamp**, not a value you pick.
   On MariaDB 13 it is `2106-02-07 06:28:15.999999`; older servers use
   `2038-01-19 03:14:07.999999`. Guess wrong and every inserted row lands in the history
   partition, where a plain `SELECT` cannot see it and nothing reports an error.
   `db.current_row_valid_to()` asks the server instead, and the loader asserts afterwards
   that a current row is actually visible.

3. **There is no `INSERT HISTORY` privilege** — `GRANT INSERT HISTORY` is `ERROR 1064`.
   Writing explicit `valid_from`/`valid_to` needs the session variable
   `SET @@system_versioning_insert_history = ON`, which `db.connect(for_loading=True)` does.

4. **`PRIMARY KEY (page_id)` is implicitly `(page_id, valid_to)`** on a versioned table.
   That is what makes re-running the loader safe: a repeated interval collides with
   `ERROR 1062` and `INSERT IGNORE` turns it into a skip. It also means a second row with
   the sentinel `valid_to` cannot exist, so topping an article up with *newer* revisions
   in place is not possible. `--refresh` builds a new table beside the live one and swaps
   it in with `RENAME TABLE` instead — see `replace_articles()` in `loader.py`.

5. **A partitioned table cannot have a `FOREIGN KEY`** — `ERROR 1506`. Partitioning by
   `SYSTEM_TIME` therefore cost `article_revision` its foreign key to `articles`; the
   loader writing the `articles` row first is the guarantee that is left.

6. **`PARTITION BY SYSTEM_TIME … AUTO` does nothing for backfilled history.** `AUTO` adds
   a partition when the server's clock crosses an interval boundary. With a `STARTS` date
   in the past it creates a single history partition and every backfilled row lands in
   it, without a warning. The partitions are listed (`PARTITIONS 28`) for that reason.
   Rows are routed by `valid_to`, so with real timestamps a partition holds the revisions
   Wikipedia superseded in that year. Only the oldest partitions can be dropped
   (`ERROR 4116` otherwise).

Also: `<script src="wikimachine.ts">` does not run in a browser. The `.ts` extension maps
to the MIME type `video/mp2t` and scripts with a non-executable type are refused silently.
The file is `wikimachine.js` for that reason; real TypeScript would need `tsc` to compile
to `.js`, which is what the browser must be handed either way.
