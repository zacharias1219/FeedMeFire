from __future__ import annotations

import hashlib
import base64
import logging
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db as dbm, logging_setup
from .config import settings
from .errors import AppError
from .models import Base
from .routes import api
from .services import analytics, metrics

BASE = Path(__file__).parent
log = logging.getLogger("rmf.web")
templates = Jinja2Templates(directory=str(BASE / "templates"))


def _asset_version() -> str:
    """Content hash of the JS/CSS/JSON assets. Appended as ?v= so a deploy always produces new URLs and a browser
    can never keep running a stale script against a newer server (the ES-module entry points are versioned)."""
    h = hashlib.sha256()
    for p in sorted((BASE / "static").rglob("*")):
        if p.is_file() and p.suffix in (".js", ".css", ".json"):
            h.update(p.relative_to(BASE).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


ASSET_V = _asset_version()
templates.env.globals["asset_v"] = ASSET_V

# --- Content-Security-Policy ---------------------------------------------------------------------
IMG_HOSTS = "https://d8j0ntlcm91z4.cloudfront.net"
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)


def _csp(script_hashes: list[str] | None = None) -> str:
    scripts = " ".join(f"'sha256-{h}'" for h in (script_hashes or []))
    return "; ".join([
        "default-src 'self'",
        f"script-src 'self' {scripts}".strip(),
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",  # fitted type uses inline styles
        "font-src 'self' https://fonts.gstatic.com",
        f"img-src 'self' data: blob: {IMG_HOSTS}",
        "connect-src 'self'", "object-src 'none'", "base-uri 'none'", "form-action 'self'",
        "frame-ancestors 'none'",
    ])


_landing_cache: dict = {}


def landing() -> tuple[str, list[str]]:
    if not _landing_cache:
        html = (BASE / "static" / "landing.html").read_text(encoding="utf-8")
        hashes = [base64.b64encode(hashlib.sha256(m.encode()).digest()).decode() for m in _INLINE_SCRIPT.findall(html)]
        _landing_cache.update(html=html, hashes=hashes)
    return _landing_cache["html"], _landing_cache["hashes"]


# --- worker embedding (dev) ----------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logging_setup.setup()
    settings.validate_for_runtime()
    if settings.auto_create_tables:
        Base.metadata.create_all(dbm.engine)
    stop = threading.Event()
    if settings.embedded_worker:
        from .worker import start_workers
        start_workers(stop)
    yield
    stop.set()


app = FastAPI(title="RoastMyFeed", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(api.router)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

PRIVATE_PREFIXES = ("/api/", "/start")


@app.middleware("http")
async def envelope_and_headers(request: Request, call_next):
    request.state.request_id = rid = str(uuid.uuid4())
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.secure_cookie:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"  # revalidate (ETag) so a deploy is never masked by a stale script
    if request.url.path.startswith(PRIVATE_PREFIXES):
        response.headers["Cache-Control"] = "no-store"
    if "content-security-policy" not in response.headers:
        response.headers["Content-Security-Policy"] = _csp()
    log.info("request", extra={"request_id": rid, "method": request.method, "path": request.url.path,
                               "status": response.status_code, "duration_ms": round((time.perf_counter() - t0) * 1000)})
    return response


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid.uuid4()))


@app.exception_handler(AppError)
async def _app_error(request: Request, exc: AppError):
    return JSONResponse(exc.envelope(_rid(request)), status_code=exc.status, headers={"Cache-Control": "no-store"})


@app.exception_handler(RequestValidationError)
async def _validation(request: Request, exc: RequestValidationError):
    # Report where the problem is, never what the user typed.
    fields = sorted({".".join(str(p) for p in e["loc"][1:]) for e in exc.errors()})
    err = AppError("VALIDATION_ERROR", details={"fields": fields})
    return JSONResponse(err.envelope(_rid(request)), status_code=422, headers={"Cache-Control": "no-store"})


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    code = "NOT_FOUND" if exc.status_code == 404 else "VALIDATION_ERROR"
    err = AppError(code, status=exc.status_code, message="Not found." if exc.status_code == 404 else str(exc.detail))
    if request.url.path.startswith("/api/"):
        return JSONResponse(err.envelope(_rid(request)), status_code=exc.status_code)
    return HTMLResponse(templates.get_template("404.html").render(request=request, s=settings, provider=settings.provider_display_name),
                        status_code=exc.status_code)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    log.error("unhandled %s", type(exc).__name__, extra={"request_id": _rid(request)})  # type only: never content
    return JSONResponse(AppError("INTERNAL_ERROR").envelope(_rid(request)), status_code=500)


# --- health & metrics ----------------------------------------------------------------------------
@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def ready():
    try:
        with dbm.session_scope() as d:
            d.execute(text("select 1"))
    except Exception:
        return JSONResponse({"status": "unavailable", "database": False}, status_code=503)
    return {"status": "ok", "database": True, "provider": settings.provider,
            "generation_enabled": settings.generation_enabled}


@app.get("/internal/metrics")
def internal_metrics(request: Request, format: str = "json", db: Session = Depends(dbm.get_db)):
    token = settings.metrics_token
    if not token or request.headers.get("authorization") != f"Bearer {token}":
        raise StarletteHTTPException(404)  # indistinguishable from a missing route
    m = metrics.collect(db)
    return PlainTextResponse(metrics.prometheus(m)) if format == "prom" else JSONResponse(m)


# --- pages ---------------------------------------------------------------------------------------
def page(name: str, **ctx):
    def handler(request: Request):
        return templates.TemplateResponse(request, name, {"s": settings, "provider": settings.provider_display_name, **ctx})
    return handler


@app.get("/", response_class=HTMLResponse)
def home(db: Session = Depends(dbm.get_db)):
    analytics.track(db, "landing_viewed")
    db.commit()
    html, hashes = landing()
    return HTMLResponse(html, headers={"Content-Security-Policy": _csp(hashes), "Cache-Control": "no-cache"})


@app.get("/demo", response_class=HTMLResponse)
def demo(request: Request, db: Session = Depends(dbm.get_db)):
    analytics.track(db, "demo_opened")
    db.commit()
    return templates.TemplateResponse(request, "demo.html", {"s": settings})


app.add_api_route("/start", page("start.html"), response_class=HTMLResponse)
app.add_api_route("/how-it-works", page("how.html"), response_class=HTMLResponse)
app.add_api_route("/safety", page("safety.html"), response_class=HTMLResponse)
app.add_api_route("/privacy", page("privacy.html"), response_class=HTMLResponse)
app.add_api_route("/terms", page("terms.html"), response_class=HTMLResponse)
app.add_api_route("/support", page("support.html"), response_class=HTMLResponse)


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)
