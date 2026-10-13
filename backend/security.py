"""HTTP hardening for a local app that can run commands.

- **DNS rebinding**: requests must carry a local ``Host`` (localhost / loopback) unless the server was
  started for the LAN with an access token (``server.py --host 0.0.0.0``) or the host is listed in
  ``ORCH_ALLOWED_HOSTS``.
- **CSRF**: state-changing requests (POST / PUT / PATCH / DELETE) coming from a browser must come from
  this app's own origin (or the dev UI); a page on another site cannot even trigger a body-less POST.
- **CORS**: only the dev UI (Vite on :4000) may read responses cross-origin.
- **Access token** (``ORCH_TOKEN`` / ``server.py --token``): required on every API call, given once in
  the URL (``/?token=...``) and then kept in an HttpOnly, SameSite=Strict cookie.
- **Headers**: nosniff, no referrer, framing limited to this origin, and a Content-Security-Policy on
  the UI that only lets it talk to this server (no third-party scripts, images, fonts or beacons).
"""
from __future__ import annotations

import hmac
import os
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

# The dev UI (Vite on :4000) goes through its proxy, which presents the API's own origin: no cross-origin access
# is needed. ORCH_CORS_ORIGINS lists extra trusted origins (comma separated) if you really need one.
CORS_ORIGINS = [o.strip() for o in os.environ.get("ORCH_CORS_ORIGINS", "").split(",") if o.strip()]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
COOKIE = "sc_token"

# The UI may embed previews of local dev servers (any port) and talk to nothing but this server.
APP_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
           "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
           "frame-ancestors 'self'; frame-src 'self' http://localhost:* http://127.0.0.1:* https://localhost:* "
           "https://127.0.0.1:* http://[::1]:*")
# Workspace files shown in the preview: sandboxed (opaque origin, cannot call the API), no remote loads.
PREVIEW_CSP = ("sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads; default-src 'self' data: blob: "
               "'unsafe-inline' 'unsafe-eval'; connect-src 'self'; frame-ancestors 'self'")


def token() -> str:
    return os.environ.get("ORCH_TOKEN", "").strip()


def allowed_hosts() -> set[str]:
    extra = {h.strip().lower() for h in os.environ.get("ORCH_ALLOWED_HOSTS", "").split(",") if h.strip()}
    bind = os.environ.get("ORCH_BIND_HOST", "").strip().lower()
    if bind and bind not in ("0.0.0.0", "::"):
        extra.add(bind)
    return LOCAL_HOSTS | extra


def _host_only(value: str) -> str:
    value = (value or "").strip().lower()
    if value.startswith("["):  # [::1]:4001
        return value[1:value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _same_origin(origin: str, host_header: str) -> bool:
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    return bool(parts.netloc) and parts.netloc.lower() == (host_header or "").lower()


class GuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        host_header = request.headers.get("host", "")
        tok = token()
        # 1. DNS rebinding: a local Host, unless a token protects a LAN deployment.
        if not tok and _host_only(host_header) not in allowed_hosts():
            return JSONResponse({"detail": f"Host '{host_header}' is not allowed (DNS rebinding protection). "
                                           "Start with --host and --token to use SuperCode from another machine."}, 403)
        # 2. Access token (LAN deployments).
        if tok:
            given = request.query_params.get("token") or ""
            if given and hmac.compare_digest(given, tok) and request.method == "GET" and not request.url.path.startswith("/api/"):
                res = RedirectResponse(str(request.url.remove_query_params("token")), 303)
                res.set_cookie(COOKIE, tok, httponly=True, samesite="strict")
                return res
            presented = request.cookies.get(COOKIE) or request.headers.get("x-supercode-token") or ""
            if request.url.path.startswith("/api/") and not (presented and hmac.compare_digest(presented, tok)):
                return JSONResponse({"detail": "Access token required: open the URL printed by the server (…/?token=…)."}, 401)
        # 3. CSRF: browsers send Origin / Sec-Fetch-Site on cross-site requests.
        if request.method in UNSAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and origin != "null" and not _same_origin(origin, host_header) and origin not in CORS_ORIGINS:
                return JSONResponse({"detail": "Cross-site request refused."}, 403)
            if origin == "null" or (request.headers.get("sec-fetch-site") == "cross-site" and origin not in CORS_ORIGINS):
                return JSONResponse({"detail": "Cross-site request refused."}, 403)
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if "content-security-policy" not in response.headers:
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response
