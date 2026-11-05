"""HTTP API for wiki_machine.

    uvicorn backend.app:app --reload

Serves three endpoints plus the frontend itself, so everything is same-origin:

    GET /api/articles                 -> list for the <select>
    GET /api/revision?page_id=&date=  -> which revision was live on that date
    GET /api/revision/{revid}/html    -> that revision's HTML, <head> patched

The HTML is never stored: the database holds identifiers only, and this process fetches
and rewrites the page on request. That rewrite is the whole reason a proxy exists -- see
patch_head() for what Wikipedia's own output gets wrong when embedded elsewhere.
"""

import re
import sys
from datetime import date as date_type
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

if __package__:
    from .db import CURRENT_ROW_VALID_TO, connect
else:
    sys.path.insert(0, str(Path(__file__).parent))
    from db import CURRENT_ROW_VALID_TO, connect

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
USER_AGENT = "wiki_machine/0.1 (student project; https://github.com/iAbdurahmon/wiki_machine)"
REST_HTML = "https://en.wikipedia.org/w/rest.php/v1/revision/{revid}/html"
FULL_PAGE = "https://en.wikipedia.org/w/index.php?oldid={revid}"

MINERVA_CSS = (
    "https://en.wikipedia.org/w/load.php?lang=en"
    "&modules=skins.minerva.styles%7Csite.styles&only=styles&skin=minerva"
)

app = FastAPI(title="wiki_machine")
# The frontend is served from here, so this is only for opening wikimachine.HTML
# straight off the filesystem during development.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"]
)


@app.get("/api/articles")
def list_articles():
    """Every loaded article, with the date its first revision appeared."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.page_id, a.title, s.first_date, s.last_edit, s.revisions
                FROM articles a
                LEFT JOIN (
                    SELECT page_id,
                           MIN(valid_from) AS first_date,
                           MAX(valid_from) AS last_edit,
                           COUNT(*)        AS revisions
                    FROM article_revision FOR SYSTEM_TIME ALL
                    GROUP BY page_id
                ) s ON s.page_id = a.page_id
                ORDER BY a.title
                """
            )
            return cur.fetchall()
    finally:
        conn.close()


@app.get("/api/revision")
def revision_at(page_id: int, date: date_type = Query(description="YYYY-MM-DD")):
    """Which revision was live at 00:00 UTC on `date`.

    This is the query the whole schema exists for. The date is interpolated rather than
    bound as a parameter because FOR SYSTEM_TIME takes no placeholder; FastAPI has
    already parsed it into a date object, so there is nothing injectable left.
    """
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT r.page_id, r.revid, r.valid_from, r.valid_to, a.title
                FROM article_revision FOR SYSTEM_TIME AS OF TIMESTAMP '{date} 00:00:00' r
                JOIN articles a ON a.page_id = r.page_id
                WHERE r.page_id = %s
                """,
                (page_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(
            404, f"no revision of page {page_id} was live on {date} -- too early, or not loaded"
        )
    row["is_current"] = str(row["valid_to"]) == CURRENT_ROW_VALID_TO
    row["html_url"] = f"/api/revision/{row['revid']}/html"
    return row


@app.get("/api/revision/{revid}/html", response_class=HTMLResponse)
def revision_html(revid: int, full: bool = False):
    """One revision as an embeddable HTML document.

    full=false (default) uses the REST API: article content only, which suits an iframe.
    full=true uses index.php?oldid=, which returns the complete page with Wikipedia's
    skin, header and sidebar.
    """
    try:
        return HTMLResponse(_fetch_html(revid, full))
    except httpx.HTTPStatusError as error:
        raise HTTPException(error.response.status_code, f"Wikipedia returned {error.response.status_code} for revision {revid}")


@lru_cache(maxsize=32)
def _fetch_html(revid: int, full: bool) -> str:
    """Fetch and patch one revision. Cached, because re-rendering a date is common."""
    url = (FULL_PAGE if full else REST_HTML).format(revid=revid)
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0) as client:
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
    return patch_head(response.text, full=full)


def patch_head(html: str, full: bool = False) -> str:
    """Make Wikipedia's HTML render correctly outside Wikipedia.

    Three things are wrong with it as shipped, and all three are in the <head>:

    1. <base href="//en.wikipedia.org/wiki/"> has no protocol. Under file:// it resolves
       to file://en.wikipedia.org/, so every relative link and image breaks.
    2. The stylesheet href is root-relative ("/w/load.php?..."), which takes its scheme
       and host from that broken <base> -- so the CSS 404s and the page renders unstyled.
       Fixing the <base> is what actually repairs this; we also swap vector for minerva,
       whose single-column layout suits a small embedded window.
    3. There is no viewport meta, which minerva's CSS needs, and no margin reset.

    index.php?oldid= output (full=True) ships a viewport and a working stylesheet set but
    no <base> at all, so it needs only that one injection.
    """
    if full:
        return _insert_into_head(html, '<base href="https://en.wikipedia.org/">')

    html = html.replace(
        '<base href="//en.wikipedia.org/wiki/"/>',
        '<base href="https://en.wikipedia.org/wiki/"/>',
    )
    html = re.sub(
        r'<link rel="stylesheet" href="/w/load\.php[^"]*"/>',
        f'<link rel="stylesheet" href="{MINERVA_CSS}"/>',
        html,
        count=1,
    )
    return _insert_into_head(
        html,
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        "<style>html,body{margin:0;padding:8px}</style>",
    )


def _insert_into_head(html: str, snippet: str) -> str:
    """Insert right after the opening <head ...> tag, whatever attributes it carries."""
    match = re.search(r"<head[^>]*>", html)
    if not match:
        return snippet + html
    return html[: match.end()] + snippet + html[match.end() :]


@app.get("/")
def index():
    """The frontend's entry point is wikimachine.HTML, not index.html."""
    return RedirectResponse("/wikimachine.HTML")


# Must be mounted last, so it does not shadow /api.
app.mount("/", StaticFiles(directory=FRONTEND), name="frontend")
