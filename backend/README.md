# backend

The database stores identifiers only -- `page_id` and `revid`. HTML is fetched from
Wikipedia on request and rewritten by the proxy, never stored.

## Setup

```bash
brew services start mariadb
mariadb < backend/schema.sql          # idempotent
.venv/bin/pip install -r backend/requirements.txt
```

Connection settings come from the environment, defaulting to the local dev user:
`DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=wiki DB_PASSWORD=wiki_dev_pass DB_NAME=wiki_machine`.

## Fill the database

An offline batch job -- run it before anyone uses the site.

```bash
.venv/bin/python -m backend.loader Agapanthus "Michael Jackson"
.venv/bin/python -m backend.loader --reset Agapanthus          # wipe revisions first
.venv/bin/python -m backend.loader --max-revisions 1500 "Albert Einstein"
```

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
