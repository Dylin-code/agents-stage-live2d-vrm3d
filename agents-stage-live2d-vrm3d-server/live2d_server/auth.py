"""Remote mode authentication: JWT utilities, email whitelist, middleware, and Google OAuth2 endpoints."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse
from jose import JWTError as JoseJWTError
from jose import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from fastapi_sso.sso.google import GoogleSSO

from .configuration import RemoteConfig

logger = logging.getLogger(__name__)

COOKIE_NAME = "auth_token"


# ---------------------------------------------------------------------------
# JWT utilities
# ---------------------------------------------------------------------------

class JwtError(Exception):
    pass


def create_jwt(email: str, secret: str, expires_seconds: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(seconds=expires_seconds)
    return jwt.encode({"email": email, "exp": expire}, secret, algorithm="HS256")


def verify_jwt(token: str, secret: str) -> dict:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except JoseJWTError as e:
        raise JwtError(str(e)) from e


# ---------------------------------------------------------------------------
# Email whitelist
# ---------------------------------------------------------------------------

def is_email_allowed(email: str, allowed_emails: list[str]) -> bool:
    return email.lower() in [e.lower() for e in allowed_emails]


# ---------------------------------------------------------------------------
# Auth guard middleware (remote mode only)
# ---------------------------------------------------------------------------

_PUBLIC_PREFIXES = (
    "/api/auth/",
    "/assets/",      # Vite built static assets (JS/CSS/images)
    "/favicon",
)
_PUBLIC_PATHS = {"/login"}


class AuthGuardMiddleware(BaseHTTPMiddleware):
    """Guards requests in remote mode.

    - Public paths (``/api/auth/*``, ``/assets/*``, ``/login``) are always allowed.
    - ``/api/*`` without valid JWT returns 401 JSON (for fetch/AJAX).
    - Non-API pages without valid JWT redirect to ``/login`` (for browser navigation).
    """

    def __init__(self, app: ASGIApp, remote_config: RemoteConfig) -> None:
        super().__init__(app)
        self._remote_config = remote_config

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # CORS preflight (OPTIONS) never carries cookies — let it through
        # so the CORSMiddleware can respond with proper headers.
        if request.method == "OPTIONS":
            return await call_next(request)

        # Public prefixes & paths are always accessible
        if any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
            return await call_next(request)
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        # Check JWT cookie
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return self._unauthorized(path)

        try:
            payload = verify_jwt(token, self._remote_config.jwt_secret)
        except JwtError:
            return self._unauthorized(path)

        if not is_email_allowed(payload["email"], self._remote_config.allowed_emails):
            return JSONResponse(status_code=403, content={"error": "email_not_allowed"})

        return await call_next(request)

    @staticmethod
    def _unauthorized(path: str):
        """API requests get 401 JSON; browser page requests get redirected to /login."""
        if path.startswith("/api/"):
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        return RedirectResponse(url="/login", status_code=307)


# ---------------------------------------------------------------------------
# WebSocket auth helper
# ---------------------------------------------------------------------------

async def verify_ws_auth(websocket: WebSocket, remote_config: RemoteConfig) -> bool:
    """Verify JWT cookie on WebSocket handshake. Returns True if allowed."""
    token = websocket.cookies.get(COOKIE_NAME)
    if not token:
        await websocket.close(code=4001, reason="unauthorized")
        return False
    try:
        payload = verify_jwt(token, remote_config.jwt_secret)
    except JwtError:
        await websocket.close(code=4001, reason="invalid_token")
        return False
    if not is_email_allowed(payload["email"], remote_config.allowed_emails):
        await websocket.close(code=4003, reason="forbidden")
        return False
    return True


# ---------------------------------------------------------------------------
# Google OAuth2 endpoints (using fastapi-sso)
# ---------------------------------------------------------------------------

def create_auth_router(remote_config: RemoteConfig) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    google_sso = GoogleSSO(
        client_id=remote_config.google_client_id,
        client_secret=remote_config.google_client_secret,
        redirect_uri=f"{remote_config.allowed_origin}/api/auth/callback",
        allow_insecure_http=(not remote_config.allowed_origin.startswith("https")),
    )

    @router.get("/login")
    async def login():
        async with google_sso:
            return await google_sso.get_login_redirect()

    @router.get("/callback")
    async def callback(request: Request):
        async with google_sso:
            user = await google_sso.verify_and_process(request)

        if user is None:
            return JSONResponse(status_code=401, content={"error": "oauth_failed"})

        if not is_email_allowed(user.email, remote_config.allowed_emails):
            return JSONResponse(status_code=403, content={"error": "email_not_allowed"})

        token = create_jwt(user.email, remote_config.jwt_secret, remote_config.cookie_max_age)
        response = RedirectResponse(url="/", status_code=307)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=remote_config.cookie_max_age,
            path="/",
            httponly=True,
            secure=remote_config.allowed_origin.startswith("https"),
            samesite="lax",
        )
        return response

    @router.get("/me")
    async def me(request: Request):
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        try:
            payload = verify_jwt(token, remote_config.jwt_secret)
        except JwtError:
            return JSONResponse(status_code=401, content={"error": "invalid_token"})
        return {"email": payload["email"]}

    @router.post("/logout")
    async def logout():
        response = JSONResponse(content={"ok": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    return router
