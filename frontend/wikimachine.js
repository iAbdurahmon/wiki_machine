// Wiki Machine -- front end wiring.
//
// Talks to the backend in backend/app.py:
//   GET /api/articles                  -> what the loader actually put in the database
//   GET /api/revision?page_id=&date=   -> which revision was live on that date
//   GET /api/revision/{revid}/html     -> that revision, <head> patched for embedding
//
// This file used to be wikimachine.ts. Browsers map the .ts extension to the MIME type
// video/mp2t and refuse to execute it, so the script never ran. If we want real
// TypeScript later, keep a .ts source and compile it with tsc -- the browser must still
// be handed .js.

const articleSelect = document.getElementById("articleselect");
const dateInput = document.getElementById("dateselect");
const submitButton = document.getElementById("submitbutton");
const frame = document.querySelector(".window-content");

// The backend serves this page, so a relative path reaches the API. Opening the file
// straight off disk still works as long as the dev server is up on its default port.
const API = location.protocol === "file:" ? "http://127.0.0.1:8000" : "";

const status = document.createElement("p");
status.className = "introtext";
document.querySelector(".inputs").after(status);

function setStatus(message, isError = false) {
    status.textContent = message;
    status.style.color = isError ? "#b32d2e" : "inherit";
}

const today = new Date().toISOString().slice(0, 10);

async function loadArticles() {
    setStatus("loading articles...");
    let articles;
    try {
        const response = await fetch(`${API}/api/articles`);
        if (!response.ok) throw new Error(`API returned ${response.status}`);
        articles = await response.json();
    } catch (error) {
        setStatus(`cannot reach the backend (${error.message}). is uvicorn running?`, true);
        return;
    }

    if (articles.length === 0) {
        setStatus("the database is empty -- run the loader first", true);
        return;
    }

    articleSelect.innerHTML = "";
    for (const article of articles) {
        const option = document.createElement("option");
        option.value = String(article.page_id);
        option.textContent = `${article.title} (${article.revisions} revisions)`;
        // Remember when the article first existed, so the date picker can refuse
        // anything earlier -- there is no revision to show before that.
        option.dataset.firstDate = article.first_date.slice(0, 10);
        articleSelect.append(option);
    }

    clampDate();
    setStatus("");
    show();
}

function clampDate() {
    const firstDate = articleSelect.selectedOptions[0]?.dataset.firstDate;
    dateInput.min = firstDate ?? "2001-01-15";
    dateInput.max = today;
    if (!dateInput.value || dateInput.value < dateInput.min) {
        dateInput.value = today;
    }
}

async function show() {
    if (!articleSelect.value) return;

    submitButton.disabled = true;
    setStatus("looking up that date...");
    try {
        const response = await fetch(
            `${API}/api/revision?page_id=${articleSelect.value}&date=${dateInput.value}`
        );
        const body = await response.json();
        if (!response.ok) {
            setStatus(body.detail ?? `API returned ${response.status}`, true);
            return;
        }
        frame.src = `${API}${body.html_url}`;
        setStatus(
            `revision ${body.revid}, live from ${body.valid_from.replace("T", " ")}` +
            (body.is_current ? " until now" : "")
        );
    } catch (error) {
        setStatus(`request failed: ${error.message}`, true);
    } finally {
        submitButton.disabled = false;
    }
}

articleSelect.addEventListener("change", () => {
    clampDate();
    show();
});
dateInput.addEventListener("change", show);
submitButton.addEventListener("click", show);

loadArticles();

