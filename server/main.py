"""FastAPI app: security headers, visitor cookie, same-origin rule, dev `?force=`, static web app."""

import json
import logging
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from . import providers as p
from .api import error_response, router, same_origin
from .annotate import router as annotate_router
from .config import HAND_GATE_PARALLEL
from .providers import ApiError

WEB = Path(__file__).resolve().parent.parent / "web"
CSP = "default-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'"
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()",
}
# Route-specific wording for errors forced in development (UX doc S5).
FORCE_MESSAGES = {("/api/sessions", "limit_reached"): "You've used today's 2 live sessions. Watch a replay or try again later."}

# Structured JSON lines from our own loggers (gateway: one per model call). httpx2's request log is muted.
_log = logging.getLogger("followalong")
_log.setLevel(os.environ.get("FA_LOG_LEVEL", "info").upper())
_log.addHandler(logging.StreamHandler())
_log.propagate = False
logging.getLogger("httpx2").setLevel(logging.WARNING)

app = FastAPI(title="FollowAlong", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)
app.include_router(annotate_router)  # #/annotate: human verification, local only


def body_limit(method: str, path: str) -> int:
    """Largest request body per route, in bytes. JSON routes are small; frames are JPEGs; the upload has its cap."""
    if method == "POST" and path == "/api/tutorials":
        return (p.UPLOAD_MAX_MB + 1) * 1024 * 1024  # + the multipart wrapping
    if path.endswith(("/checks", "/questions", "/watch")):
        return 4 * 1024 * 1024
    if path.endswith("/transcribe"):
        return 1024 * 1024  # 10 s of 24 kHz 16-bit mono is 480 KB
    return 64 * 1024  # an edited plan is ~10 KB


class BodyLimit:
    """413 past body_limit(): at once from Content-Length, else while reading (a chunked body has none)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = body_limit(scope["method"], scope["path"])
        length = dict(scope["headers"]).get(b"content-length")
        if length and length.isdigit() and int(length) > limit:
            return await error_response(ApiError("too_long", "The request is too large."))(scope, receive, send)
        seen = 0

        async def limited():
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > limit:
                raise ApiError("too_long", "The request is too large.")  # answered by on_api_error below
            return message
        await self.app(scope, limited, send)


app.add_middleware(BodyLimit)


@app.exception_handler(ApiError)
async def on_api_error(request: Request, err: ApiError):
    return error_response(err)


@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, err: RequestValidationError):
    return error_response(ApiError("bad_request"))


if HAND_GATE_PARALLEL:
    _log.warning(json.dumps({"event": "config_warning", "setting": "FA_HAND_GATE_PARALLEL",
                             "message": "The hand request runs in parallel with the object requests. This breaks "
                                        "NFR-13 (hand gate before any object request) and needs an approved change request."}))


def cross_origin(request: Request) -> bool:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return False
    if request.headers.get("sec-fetch-site") in ("cross-site", "same-site"):
        return True
    origin = request.headers.get("origin")
    return bool(origin) and not same_origin(origin, request.url.scheme, request.headers.get("host"))


@app.middleware("http")
async def guard(request: Request, call_next):
    cookie = request.cookies.get("fa_visitor")
    request.state.visitor = cookie or secrets.token_hex(16)
    path = request.url.path
    force = request.query_params.get("force")
    if cross_origin(request):
        response = error_response(ApiError("cross_origin"))
    elif force in ApiError.STATUS and not p.HOSTED and path.startswith("/api/"):
        response = error_response(ApiError(force, FORCE_MESSAGES.get((path, force)),
                                           3600 if force == "cap_reached" else None))
    else:
        response = await call_next(request)
    for k, v in HEADERS.items():
        response.headers[k] = v
    if path.startswith("/api/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"  # media routes set their own (revalidating) policy
    elif "cache-control" not in response.headers:  # the web app: revalidate (ETag, a 304 when unchanged), so a
        response.headers["Cache-Control"] = "no-cache"  # deploy never leaves a browser on stale JS modules
    if not cookie:
        response.set_cookie("fa_visitor", request.state.visitor, max_age=86400, httponly=True,
                            samesite="lax", secure=p.HOSTED)
    return response


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
