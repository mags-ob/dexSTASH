"""The FastAPI app: sign-in cookies, the checks every request gets, and the pages from app/routes/."""
import html
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from . import __version__, settings
from . import auth, db, jobs, startup, web
from .web import (_from_elsewhere, _Route, conn, log, LoginRequired, nightly, picture_saver, SECRET_KEY,
                  SESSION_DAYS, SetupPending)
from .routes import (account, binders, cards, collection, data_sources, home, imports, pictures, pokemon, scans, search,
                     sets, shared, spending, wants, welcome)


@asynccontextmanager
async def _lifespan(_app):
    # Startup in three phases (startup.py). If the upgrade fails, every page says so and nothing else starts.
    if not startup.start(conn, web.BACKUPS):
        yield
        return
    nightly.start()  # the scheduler: the approved nightly price update and its chores, as jobs
    if web.scanner_child:  # DEXSTASH_SCANNER=inside: the photo reader as a low-priority child
        web.scanner_child.start()
    web.light_work.start()  # scanning off: followed cards' pictures, at the lowest priority
    yield
    nightly.stop()
    web.background.stop()
    web.light_work.stop()
    if web.scanner_child:
        web.scanner_child.stop()


FAILED_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>The upgrade didn't finish</title>
<style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;line-height:1.5}}
pre{{white-space:pre-wrap;background:#f3f3f3;padding:12px;border-radius:8px}}</style></head><body>
<h1>The upgrade didn't finish</h1>
<p>{name} {version} couldn't upgrade its database, so it isn't opening. Your cards haven't been lost.</p>
<pre>{error}</pre>
<p>{backup}</p>
<p>To go back: stop the app, copy that backup over <code>data/dexstash.db</code>, and install the version you had
before. DEPLOY.md's Backups section has the steps. To get help, send the error above and the server's log.</p>
</body></html>"""


def _failed_page() -> Response:
    failed = startup.STATE["failed"]
    copy = failed.get("backup")
    backup_line = (f"A copy of your database from just before the upgrade is <code>data/backups/{html.escape(copy.name)}</code>."
                   if copy else "The newest file in <code>data/backups/</code> ending in <code>-upgrade.db</code> is your "
                                "database from before the upgrade (if there's none, your database wasn't changed).")
    return HTMLResponse(FAILED_PAGE.format(name=html.escape(settings.app_name()), version=__version__,
                                           error=html.escape(failed["error"]), backup=backup_line),
                        status_code=503, headers={"Cache-Control": "no-store", "Retry-After": "60", **APP_ERROR})


# On an error page the app itself sends, so the service worker shows it instead of "Can't reach your server" (pwa.py).
APP_ERROR = {"X-Dexstash-Page": "error"}


# No /docs, /redoc or /openapi.json.
app = FastAPI(title=settings.app_name(), lifespan=_lifespan, docs_url=None, redoc_url=None, openapi_url=None)


app.router.route_class = _Route
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    https_only=settings.on("HTTPS_ONLY"),
    session_cookie="dexstash_session",  # A new name, so everyone signs in once after the rename
    same_site="lax",
    max_age=60 * 60 * 24 * SESSION_DAYS,
)


@app.middleware("http")
async def same_site_forms(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and _from_elsewhere(request):
        log.warning("a form sent from another site was refused (%s %s)", request.method, request.url.path)
        return Response("This form was sent from another site, so it wasn't accepted.", status_code=403,
                        media_type="text/plain")
    return await call_next(request)


# Pages run only the app's own script files: no inline scripts or handlers, nothing from other sites, so script
# slipped into a page (a card name, a note) can't run. Card pictures may come from any https site; styles may be
# inline (each login's theme colours).
CONTENT_POLICY = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data: blob:; "
                  "media-src 'self' blob:; font-src 'self'; connect-src 'self'; worker-src 'self'; manifest-src 'self'; "
                  "object-src 'none'; base-uri 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def no_stored_pages(request: Request, call_next):
    """Pages are always fetched fresh; pictures keep their own caching. No page can be shown inside
    another site's frame, files are taken as the type they say, and links don't pass on addresses."""
    response = await call_next(request)
    # Nothing a request wrote stays uncommitted, which would keep the scanner container (and the
    # app's own background jobs) from writing. sync pages settle their own
    # thread's connection; this catches the event loop's.
    db.settle(conn, f"{request.method} {request.url.path}")
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-store"
        response.headers.setdefault("Content-Security-Policy", CONTENT_POLICY)
    elif request.url.path.startswith(("/static/css/", "/static/js/")) and request.query_params.get("v"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # The address changes with the file
    elif request.url.path.startswith(("/static/fonts/", "/static/htmx-")):
        response.headers["Cache-Control"] = "public, max-age=2592000"  # Nunito, fetched once a month at most
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.method == "POST" and response.status_code < 400 and not request.url.path.startswith(("/login", "/logout", "/setup")):
        picture_saver.poke_if_changed(conn)  # a card added or chased gets its picture saved (E6: only then)
    return response


# Status checks pages make by themselves while something runs: they don't count as browsing (J5).
POLLS = ("/static/", "/healthz", "/account/data/running", "/prices/status", "/pictures/found", "/pictures/lookup/",
         "/welcome/ready", "/sw.js", "/manifest", "/favicon")


@app.middleware("http")
async def pages_first(request: Request, call_next):
    """While a page is being answered, background jobs wait between their steps and downloads (jobs.pause_for_pages)."""
    if request.url.path.startswith(POLLS) or request.headers.get("X-Refresh"):
        return await call_next(request)
    jobs.page_started()
    try:
        return await call_next(request)
    finally:
        jobs.page_ended()


class _SecureCookiesOverHttps:
    """Sign-in cookies are marked Secure whenever the visit is over https (directly or through the proxy),
    even with DEXSTASH_HTTPS_ONLY=0 for a plain-http home network."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("scheme") != "https":
            return await self.app(scope, receive, send)

        async def send_secure(message):
            if message["type"] == "http.response.start":
                message["headers"] = [(k, v + b"; secure") if k.lower() == b"set-cookie" and b"secure" not in v.lower()
                                      else (k, v) for k, v in message.get("headers", [])]
            await send(message)
        return await self.app(scope, receive, send_secure)


app.add_middleware(_SecureCookiesOverHttps)
# Who may pass on the visitor's address and https (Caddy, PikaPods' proxy): private addresses, or DEXSTASH_PROXY_IPS.
# Here rather than on uvicorn's command line, so a plain `docker run` of the image (what PikaPods does) gets it too,
# the sign-in lockout counts each visitor rather than the proxy, and the same-site form check sees the real host.
PROXY_IPS = settings.get("PROXY_IPS") or "127.0.0.1,172.16.0.0/12,192.168.0.0/16,10.0.0.0/8"
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=[h.strip() for h in PROXY_IPS.split(",") if h.strip()])


class _UpgradeFailed:
    """Outermost: while the upgrade at start has failed, every address (the health check too) gets the failure page."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and startup.STATE["failed"]:
            return await _failed_page()(scope, receive, send)
        return await self.app(scope, receive, send)


app.add_middleware(_UpgradeFailed)

app.mount("/static", StaticFiles(directory=Path(__file__).with_name("static")), name="static")


@app.exception_handler(LoginRequired)
def _to_login(request: Request, exc: LoginRequired):
    target = "/setup" if auth.user_count(conn) == 0 else "/login"
    if request.headers.get("HX-Request"):
        # A background request: send the whole page to the login once, not the login page into a row.
        return Response(status_code=204, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


@app.exception_handler(SetupPending)
def _to_welcome(request: Request, exc: SetupPending):
    """Until the first-run wizard is finished, every page goes to it (others see that it's being set up)."""
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Redirect": "/welcome"})
    return RedirectResponse("/welcome", status_code=303)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    """A 502 (another site, mostly TCGdex, didn't answer) on a page load: the app's own "Couldn't load this card"
    page, marked so the service worker doesn't swap it for the offline page. Everything else as FastAPI does it."""
    if exc.status_code != 502 or request.method != "GET" or request.headers.get("HX-Request"):
        return await http_exception_handler(request, exc)
    log.warning("%s: %s", request.url.path, exc.detail)
    try:
        user = web.signed_in(request)
    except LoginRequired:
        user = None
    path = request.url.path
    what = "card" if path.startswith(("/preview/", "/cards/")) else "set" if path.startswith(("/set-preview/", "/sets/")) else "page"
    return web.templates.TemplateResponse(request, "source_error.html", {"user": user, "what": what, "again": str(request.url)},
                                          status_code=502, headers={**APP_ERROR, "Cache-Control": "no-store"})


# In the order the pages were first written, so no address reaches a different page than before.
for _pages in (account, home, sets, cards, pictures, search, collection, binders, shared, pokemon, wants, spending,
               imports, scans, data_sources, welcome):
    app.include_router(_pages.router)
