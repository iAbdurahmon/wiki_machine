# backend

The database stores identifiers only -- `page_id` and `revid`. HTML is fetched from
Wikipedia on request and rewritten by the proxy, never stored.

## Requirements

- Python 3.10+
- MariaDB new enough to support writing history rows. Check it directly rather than
  going by version number -- the server must know this variable:

  ```bash
  mariadb -e "SHOW VARIABLES LIKE 'system_versioning_insert_history'"
  ```

  An empty result means the server is too old and the loader cannot write anything.
  **MySQL will not work at all** -- it has no system-versioned tables.

## Setup

```bash
# macOS
brew install mariadb && brew services start mariadb
# Debian/Ubuntu
sudo apt install mariadb-server && sudo systemctl start mariadb
# Windows: install MariaDB from mariadb.org/download, it runs as a service

# .venv is gitignored, so a fresh clone has none -- create it. venv ships with
# Python itself; on Debian/Ubuntu it may need `sudo apt install python3-venv`.
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

# Creates the database, the tables, and the `wiki` user the app logs in as.
# Run it as a MariaDB administrator -- `sudo mariadb` if your account has no rights.
mariadb < backend/schema.sql          # idempotent, safe to re-run
```

Connection settings come from the environment, defaulting to the local dev user:
`DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=wiki DB_PASSWORD=wiki_dev_pass DB_NAME=wiki_machine`.

### On Windows

Same steps, different paths -- `.venv\Scripts\` instead of `.venv/bin/`:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r backend\requirements.txt
.venv\Scripts\python -m backend.loader Agapanthus
.venv\Scripts\uvicorn backend.app:app --reload
```

PowerShell has no `<` input redirection, so loading the schema needs a pipe. MariaDB's
Windows installer sets a root password rather than using passwordless local auth:

```powershell
Get-Content backend\schema.sql | mariadb -u root -p
```

**"The term 'mariadb' is not recognized"** means PowerShell cannot find the client, not
that MariaDB is missing. In order of likelihood:

1. The terminal was open before MariaDB was installed. PowerShell reads PATH once at
   startup. Close it, reopen, try `mariadb --version`. This is usually it.
2. Use the "MariaDB ... Command Prompt" shortcut from the Start menu, where PATH is
   already set.
3. Find the client and add it to PATH yourself (the version in the path will differ):

   ```powershell
   Get-ChildItem "C:\Program Files\MariaDB*\bin\maria*.exe" | Select-Object FullName
   [Environment]::SetEnvironmentVariable(
       "Path", $env:Path + ";C:\Program Files\MariaDB 11.8\bin", "User")
   ```

   Then reopen the terminal.

Older builds ship the client as `mysql.exe` rather than `mariadb.exe`; the arguments are
the same. `Get-Service -Name "MariaDB*"` tells you whether the server is actually running.

## Fill the database

An offline batch job -- run it before anyone uses the site.

The database is not in git, so every machine loads its own copy. `backend/articles.txt`
is the shared list that keeps those copies the same: add a title there rather than
loading it only locally, and everyone gets it on their next run.

```bash
.venv/bin/python -m backend.loader                             # everything in articles.txt
.venv/bin/python -m backend.loader Agapanthus "Michael Jackson"  # or name them directly
.venv/bin/python -m backend.loader --reset                     # wipe revisions first
.venv/bin/python -m backend.loader --max-revisions 1500 "Albert Einstein"
```

Titles must match Wikipedia's own, the way they appear in the URL. A title that does not
exist is reported and skipped, the rest still load.

Removing an article takes `--reset` and a full reload: a plain DELETE on a versioned
table moves rows into history instead of erasing them, and DELETE HISTORY accepts no
WHERE clause, so there is no way to purge just one article.

`--max-revisions` keeps the most RECENT revisions. Re-running is safe, and extending an
article further back in time is safe too, because an interval is fixed by the timestamps
of its neighbours. Adding revisions *newer* than what is stored is not: the stored newest
row still claims the "current" sentinel and would overlap. Use `--reset` for that.

## Run

```bash
.venv/bin/uvicorn backend.app:app --reload
```

Then open http://127.0.0.1:8000/ -- the frontend is served from the same origin, so there
is no CORS to worry about. Interactive API docs at http://127.0.0.1:8000/docs.

| Endpoint | Purpose |
|---|---|
| `GET /api/articles` | loaded articles, for the `<select>` |
| `GET /api/revision?page_id=&date=YYYY-MM-DD` | which revision was live that day |
| `GET /api/revision/{revid}/html` | that revision, `<head>` patched for embedding |
| `GET /api/revision/{revid}/html?full=true` | full page with Wikipedia's skin instead |

## Wiring the frontend

`wikimachine.HTML` already has `#articleselect`, `#dateselect` and `#submitbutton`. What
is missing is filling the select from the API and pointing the iframe at it:

```js
const select = document.getElementById("articleselect");
const dateInput = document.getElementById("dateselect");
const frame = document.querySelector(".window-content");

// Fill the dropdown with what the loader actually put in the database.
fetch("/api/articles").then(r => r.json()).then(articles => {
    select.innerHTML = "";
    for (const a of articles) {
        const option = document.createElement("option");
        option.value = String(a.page_id);
        option.textContent = `${a.title} (${a.revisions} revisions)`;
        option.dataset.firstDate = a.first_date.slice(0, 10);
        select.append(option);
    }
    clampDate();
});

// Stop the user picking a date before the article existed.
function clampDate() {
    dateInput.min = select.selectedOptions[0]?.dataset.firstDate ?? "2001-01-15";
    dateInput.max = new Date().toISOString().slice(0, 10);
    if (!dateInput.value) dateInput.value = dateInput.max;
}
select.addEventListener("change", clampDate);

document.getElementById("submitbutton").addEventListener("click", async () => {
    const response = await fetch(
        `/api/revision?page_id=${select.value}&date=${dateInput.value}`
    );
    if (!response.ok) {
        alert((await response.json()).detail);
        return;
    }
    const revision = await response.json();
    frame.src = revision.html_url;
});
```

Note: `<script src="wikimachine.ts">` does not run in a browser -- the `.ts` extension
maps to the MIME type `video/mp2t`, and scripts with a non-executable type are refused.
Either compile it (`tsc wikimachine.ts` and point `src` at the `.js`) or rename the file.
