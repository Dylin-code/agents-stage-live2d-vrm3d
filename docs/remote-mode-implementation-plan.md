# Remote Mode Implementation Plan

> **Status**: Implemented (2026-03-21)
> **Deviation**: Step 5 uses `fastapi-sso` instead of manual `httpx` token exchange, reducing OAuth boilerplate by ~60 lines.

## Overview

Add a remote access mode to the existing local-only application, allowing secure
access from anywhere via Cloudflare Tunnel + Google OAuth2 authentication.

**Design principle**: Local mode remains completely unchanged. Remote mode adds a
single authentication gate — once authenticated, the experience is identical to
local mode. Think of it as a remote desktop service for your AI agent stage.

---

## Architecture

### Local Mode (default, unchanged)

```
Frontend :5173 (vite dev)  ──HTTP/WS──>  Backend :8000 (FastAPI)
                                              │
                                         Codex / Claude CLI
```

### Remote Mode (new)

```
Browser (anywhere)
    │ HTTPS
    ▼
Cloudflare Tunnel ──> FastAPI :8000
                        ├── /api/auth/*        (Google OAuth2)
                        ├── /api/session-bridge/* (existing API, behind auth)
                        └── /* static           (frontend dist/, behind auth)
```

Key insight: frontend is served by FastAPI via `--static-path`, so everything
is same-origin. No CORS issues, cookies work automatically.

---

## Scope

### In scope
- `--mode local|remote` CLI flag
- Google OAuth2 login with email whitelist
- JWT HttpOnly cookie for session
- Auth middleware (only active in remote mode)
- WebSocket auth (cookie check on handshake)
- Login page (single Google sign-in button)
- Frontend router guard
- Makefile `dev-remote` target
- TDD: unit tests before implementation
- Integration tests: end-to-end auth flow

### Out of scope
- Multi-user isolation (single user, single machine)
- Database / ORM (email whitelist lives in config file)
- Docker / Kubernetes / Nginx
- Multi-provider OAuth (GitHub, etc.) — future extension
- API versioning

---

## File Change Summary

### New files (3)

| File | Est. lines | Purpose |
|------|-----------|---------|
| `live2d_server/auth.py` | ~150 | OAuth2 flow, JWT, middleware |
| `live2d_server/auth_test.py` | ~250 | Unit + integration tests for auth |
| `src/pages/Login.vue` | ~40 | Login page UI |

### Modified files (6)

| File | Change size | Purpose |
|------|------------|---------|
| `main.py` | ~15 lines | `--mode` flag, conditional middleware mount |
| `configuration.py` | ~10 lines | `RemoteConfig` model |
| `session_bridge_api.py` | ~5 lines | WebSocket cookie check |
| `src/router/index.ts` | ~15 lines | `/login` route + nav guard |
| `Makefile` | ~8 lines | `dev-remote` target |
| `pyproject.toml` | ~2 lines | Add python-jose, httpx |

### Not modified
- `sessionBridge.ts` — same-origin, no changes needed
- `requests.ts` — same-origin, no changes needed
- CORS config — same-origin, no changes needed
- Session bridge core logic — unchanged
- All existing tests — must continue to pass

---

## Implementation Steps (TDD)

Each step follows: **write tests -> run tests (red) -> implement -> run tests (green)**.

### Step 1: Backend — RemoteConfig model

**Test first** (`session_bridge_test.py` or new `configuration_test.py`):

```python
class RemoteConfigTest(unittest.TestCase):
    def test_default_remote_config(self):
        """RemoteConfig has sensible defaults and is optional."""
        config = Config()
        self.assertEqual(config.remote.allowed_emails, [])
        self.assertEqual(config.remote.google_client_id, "")
        self.assertEqual(config.remote.cookie_max_age, 86400)

    def test_remote_config_from_dict(self):
        """RemoteConfig can be constructed from a dictionary."""
        config = Config(remote={
            "google_client_id": "test-id",
            "google_client_secret": "test-secret",
            "allowed_emails": ["user@example.com"],
        })
        self.assertEqual(config.remote.google_client_id, "test-id")
        self.assertEqual(config.remote.allowed_emails, ["user@example.com"])
```

**Implement** (`configuration.py`):

```python
class RemoteConfig(BaseModel):
    google_client_id: str = ""
    google_client_secret: str = ""
    jwt_secret: str = ""
    allowed_emails: list[str] = []
    allowed_origin: str = ""        # e.g. https://agents-stage.example.com
    cookie_max_age: int = 86400     # 24 hours

class Config(BaseModel):
    debug: bool = False
    server: ServerConfig = ServerConfig()
    remote: RemoteConfig = RemoteConfig()   # <-- add this
```

---

### Step 2: Backend — JWT utility functions

**Test first** (`auth_test.py`):

```python
class JwtUtilsTest(unittest.TestCase):
    def test_create_and_verify_token(self):
        """Round-trip: create a JWT then verify it."""
        token = create_jwt(email="user@example.com", secret="s3cret", expires_seconds=3600)
        payload = verify_jwt(token, secret="s3cret")
        self.assertEqual(payload["email"], "user@example.com")

    def test_expired_token_raises(self):
        """Expired JWT should raise."""
        token = create_jwt(email="user@example.com", secret="s3cret", expires_seconds=-1)
        with self.assertRaises(JwtError):
            verify_jwt(token, secret="s3cret")

    def test_invalid_token_raises(self):
        """Tampered JWT should raise."""
        with self.assertRaises(JwtError):
            verify_jwt("garbage.token.here", secret="s3cret")

    def test_wrong_secret_raises(self):
        """JWT signed with different secret should fail."""
        token = create_jwt(email="user@example.com", secret="s3cret", expires_seconds=3600)
        with self.assertRaises(JwtError):
            verify_jwt(token, secret="different-secret")
```

**Implement** (`auth.py`):

```python
from jose import jwt, JWTError as JoseJWTError
from datetime import datetime, timedelta, timezone

class JwtError(Exception):
    pass

def create_jwt(email: str, secret: str, expires_seconds: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(seconds=expires_seconds)
    return jwt.encode({"email": email, "exp": expire}, secret, algorithm="HS256")

def verify_jwt(token: str, secret: str) -> dict:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except JoseJWTError as e:
        raise JwtError(str(e))
```

---

### Step 3: Backend — Email whitelist check

**Test first** (`auth_test.py`):

```python
class EmailWhitelistTest(unittest.TestCase):
    def test_allowed_email_passes(self):
        self.assertTrue(is_email_allowed("danny@gmail.com", ["danny@gmail.com"]))

    def test_disallowed_email_rejected(self):
        self.assertFalse(is_email_allowed("hacker@evil.com", ["danny@gmail.com"]))

    def test_empty_whitelist_rejects_all(self):
        self.assertFalse(is_email_allowed("anyone@gmail.com", []))

    def test_case_insensitive(self):
        self.assertTrue(is_email_allowed("Danny@Gmail.COM", ["danny@gmail.com"]))
```

**Implement** (`auth.py`):

```python
def is_email_allowed(email: str, allowed_emails: list[str]) -> bool:
    return email.lower() in [e.lower() for e in allowed_emails]
```

---

### Step 4: Backend — Auth middleware (core logic)

**Test first** (`auth_test.py`):

```python
class AuthMiddlewareTest(unittest.IsolatedAsyncioTestCase):
    """Test auth middleware using FastAPI TestClient."""

    def _create_app(self, mode: str, allowed_emails: list[str] | None = None):
        """Helper: create a minimal FastAPI app with auth middleware."""
        app = FastAPI()
        remote_config = RemoteConfig(
            jwt_secret="test-secret",
            allowed_emails=allowed_emails or ["user@example.com"],
        )
        app.state.mode = mode
        app.state.remote_config = remote_config

        if mode == "remote":
            app.add_middleware(BaseHTTPMiddleware, dispatch=auth_guard_dispatch(remote_config))

        @app.get("/api/test")
        def test_endpoint():
            return {"ok": True}

        @app.get("/api/auth/login")
        def login():
            return {"login": True}

        return app

    def test_local_mode_no_auth_required(self):
        """Local mode: all requests pass through without auth."""
        app = self._create_app(mode="local")
        client = TestClient(app)
        resp = client.get("/api/test")
        self.assertEqual(resp.status_code, 200)

    def test_remote_mode_rejects_unauthenticated(self):
        """Remote mode: request without JWT cookie returns 401."""
        app = self._create_app(mode="remote")
        client = TestClient(app)
        resp = client.get("/api/test")
        self.assertEqual(resp.status_code, 401)

    def test_remote_mode_allows_auth_endpoints(self):
        """Remote mode: /api/auth/* endpoints are always accessible."""
        app = self._create_app(mode="remote")
        client = TestClient(app)
        resp = client.get("/api/auth/login")
        self.assertEqual(resp.status_code, 200)

    def test_remote_mode_valid_cookie_passes(self):
        """Remote mode: valid JWT cookie allows access."""
        app = self._create_app(mode="remote")
        client = TestClient(app)
        token = create_jwt("user@example.com", "test-secret", 3600)
        resp = client.get("/api/test", cookies={"auth_token": token})
        self.assertEqual(resp.status_code, 200)

    def test_remote_mode_expired_cookie_rejected(self):
        """Remote mode: expired JWT cookie returns 401."""
        app = self._create_app(mode="remote")
        client = TestClient(app)
        token = create_jwt("user@example.com", "test-secret", -1)
        resp = client.get("/api/test", cookies={"auth_token": token})
        self.assertEqual(resp.status_code, 401)

    def test_remote_mode_wrong_email_rejected(self):
        """Remote mode: valid JWT but email not in whitelist returns 403."""
        app = self._create_app(mode="remote", allowed_emails=["admin@example.com"])
        client = TestClient(app)
        token = create_jwt("hacker@evil.com", "test-secret", 3600)
        resp = client.get("/api/test", cookies={"auth_token": token})
        self.assertEqual(resp.status_code, 403)
```

**Implement** (`auth.py`):

```python
async def auth_guard_dispatch(remote_config: RemoteConfig):
    async def dispatch(request: Request, call_next):
        # Auth endpoints are always accessible
        if request.url.path.startswith("/api/auth/"):
            return await call_next(request)

        # Check JWT cookie
        token = request.cookies.get("auth_token")
        if not token:
            return JSONResponse(status_code=401, content={"error": "unauthorized"})

        try:
            payload = verify_jwt(token, remote_config.jwt_secret)
        except JwtError:
            return JSONResponse(status_code=401, content={"error": "invalid_token"})

        if not is_email_allowed(payload["email"], remote_config.allowed_emails):
            return JSONResponse(status_code=403, content={"error": "email_not_allowed"})

        return await call_next(request)
    return dispatch
```

---

### Step 5: Backend — Google OAuth2 endpoints

**Test first** (`auth_test.py`):

```python
class GoogleOAuthFlowTest(unittest.IsolatedAsyncioTestCase):
    """Test OAuth endpoints with mocked Google responses."""

    def _create_auth_app(self, allowed_emails=None):
        """Helper: create app with auth router mounted."""
        remote_config = RemoteConfig(
            google_client_id="test-client-id",
            google_client_secret="test-client-secret",
            jwt_secret="test-secret",
            allowed_emails=allowed_emails or ["user@example.com"],
        )
        app = FastAPI()
        app.state.remote_config = remote_config
        app.include_router(create_auth_router(remote_config))
        return app

    def test_login_redirects_to_google(self):
        """GET /api/auth/login should redirect to Google OAuth."""
        app = self._create_auth_app()
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/api/auth/login")
        self.assertEqual(resp.status_code, 307)
        self.assertIn("accounts.google.com", resp.headers["location"])

    @patch("live2d_server.auth.exchange_google_code")
    async def test_callback_success_sets_cookie(self, mock_exchange):
        """GET /api/auth/callback with valid code sets auth_token cookie."""
        mock_exchange.return_value = {"email": "user@example.com"}
        app = self._create_auth_app()
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/api/auth/callback?code=test-code")
        self.assertEqual(resp.status_code, 307)   # redirect to /
        self.assertIn("auth_token", resp.cookies)

    @patch("live2d_server.auth.exchange_google_code")
    async def test_callback_disallowed_email_403(self, mock_exchange):
        """Callback with email not in whitelist returns 403."""
        mock_exchange.return_value = {"email": "hacker@evil.com"}
        app = self._create_auth_app(allowed_emails=["admin@example.com"])
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/api/auth/callback?code=test-code")
        self.assertEqual(resp.status_code, 403)

    def test_me_with_valid_cookie(self):
        """GET /api/auth/me returns user info from JWT."""
        app = self._create_auth_app()
        client = TestClient(app)
        token = create_jwt("user@example.com", "test-secret", 3600)
        resp = client.get("/api/auth/me", cookies={"auth_token": token})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["email"], "user@example.com")

    def test_me_without_cookie_401(self):
        """GET /api/auth/me without cookie returns 401."""
        app = self._create_auth_app()
        client = TestClient(app)
        resp = client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 401)

    def test_logout_clears_cookie(self):
        """POST /api/auth/logout clears the auth_token cookie."""
        app = self._create_auth_app()
        client = TestClient(app)
        token = create_jwt("user@example.com", "test-secret", 3600)
        resp = client.post("/api/auth/logout", cookies={"auth_token": token})
        self.assertEqual(resp.status_code, 200)
        # Cookie should be deleted (max_age=0)
        set_cookie = resp.headers.get("set-cookie", "")
        self.assertIn("auth_token", set_cookie)
        self.assertIn("Max-Age=0", set_cookie)
```

**Implement** (`auth.py`):

```python
def create_auth_router(remote_config: RemoteConfig) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.get("/login")
    async def login(request: Request):
        # Build Google OAuth URL and redirect
        ...

    @router.get("/callback")
    async def callback(request: Request):
        # Exchange code for tokens, verify email, set JWT cookie
        ...

    @router.get("/me")
    async def me(request: Request):
        # Read JWT from cookie, return user info
        ...

    @router.post("/logout")
    async def logout():
        # Clear cookie
        ...

    return router
```

---

### Step 6: Backend — WebSocket auth

**Test first** (`auth_test.py`):

```python
class WebSocketAuthTest(unittest.IsolatedAsyncioTestCase):
    """Test WebSocket handshake authentication."""

    def _create_ws_app(self, mode="remote"):
        app = FastAPI()
        remote_config = RemoteConfig(
            jwt_secret="test-secret",
            allowed_emails=["user@example.com"],
        )
        app.state.mode = mode
        app.state.remote_config = remote_config

        @app.websocket("/api/session-bridge/ws")
        async def ws_endpoint(websocket: WebSocket):
            if mode == "remote":
                token = websocket.cookies.get("auth_token")
                if not token:
                    await websocket.close(code=4001, reason="unauthorized")
                    return
                try:
                    payload = verify_jwt(token, remote_config.jwt_secret)
                    if not is_email_allowed(payload["email"], remote_config.allowed_emails):
                        await websocket.close(code=4003, reason="forbidden")
                        return
                except JwtError:
                    await websocket.close(code=4001, reason="invalid_token")
                    return
            await websocket.accept()
            await websocket.send_json({"status": "connected"})
            await websocket.close()

        return app

    def test_ws_local_mode_no_auth(self):
        """Local mode: WebSocket connects without auth."""
        app = self._create_ws_app(mode="local")
        client = TestClient(app)
        with client.websocket_connect("/api/session-bridge/ws") as ws:
            data = ws.receive_json()
            self.assertEqual(data["status"], "connected")

    def test_ws_remote_mode_rejects_no_cookie(self):
        """Remote mode: WebSocket without cookie is closed with 4001."""
        app = self._create_ws_app(mode="remote")
        client = TestClient(app)
        with self.assertRaises(Exception):
            with client.websocket_connect("/api/session-bridge/ws") as ws:
                ws.receive_json()

    def test_ws_remote_mode_valid_cookie(self):
        """Remote mode: WebSocket with valid cookie connects."""
        app = self._create_ws_app(mode="remote")
        client = TestClient(app)
        token = create_jwt("user@example.com", "test-secret", 3600)
        with client.websocket_connect(
            "/api/session-bridge/ws",
            cookies={"auth_token": token}
        ) as ws:
            data = ws.receive_json()
            self.assertEqual(data["status"], "connected")
```

**Implement** (`session_bridge_api.py`): Add ~5 lines to existing WS endpoint.

---

### Step 7: Backend — main.py startup mode

**Test first** (`auth_test.py`):

```python
class StartupModeTest(unittest.TestCase):
    """Test that --mode flag controls middleware registration."""

    def test_local_mode_no_auth_middleware(self):
        """Local mode app should not have auth middleware."""
        app = create_app(mode="local", config=Config())
        # Verify no auth middleware by making unauthenticated request
        client = TestClient(app)
        resp = client.get("/api/session-bridge/health")
        self.assertEqual(resp.status_code, 200)

    def test_remote_mode_has_auth_middleware(self):
        """Remote mode app should require auth on API endpoints."""
        config = Config(remote=RemoteConfig(
            jwt_secret="test-secret",
            allowed_emails=["user@example.com"],
        ))
        app = create_app(mode="remote", config=config)
        client = TestClient(app)
        resp = client.get("/api/session-bridge/health")
        self.assertEqual(resp.status_code, 401)
```

**Implement** (`main.py`):

```python
parser.add_argument('--mode', choices=['local', 'remote'], default='local',
                    help='local: no auth (default), remote: Google OAuth2 required')

def create_app(mode: str, config: Config) -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.state.mode = mode

    if mode == "remote":
        from live2d_server.auth import auth_guard_dispatch, create_auth_router
        app.state.remote_config = config.remote
        app.add_middleware(BaseHTTPMiddleware, dispatch=auth_guard_dispatch(config.remote))
        app.include_router(create_auth_router(config.remote))

    # ... existing router mounts (unchanged)
    return app
```

---

### Step 8: Frontend — Login page

**No unit test needed** (pure UI, covered by integration tests).

**Implement** (`src/pages/Login.vue`):

```vue
<template>
  <div class="login-container">
    <div class="login-card">
      <h2>Agents Stage</h2>
      <p>Sign in to continue</p>
      <a :href="loginUrl">
        <button class="google-btn">Sign in with Google</button>
      </a>
    </div>
  </div>
</template>

<script setup lang="ts">
const loginUrl = `${window.location.origin}/api/auth/login`
</script>
```

---

### Step 9: Frontend — Router guard

**Test first** (`src/router/index.test.ts`, extend existing):

```typescript
describe('auth navigation guard', () => {
  it('allows /login without auth check', () => {
    const route = routes.find(r => r.path === '/login')
    expect(route).toBeDefined()
  })

  it('redirects to /login when /api/auth/me returns 401', async () => {
    // Mock fetch to return 401
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, { status: 401 })
    )
    // Simulate navigation guard logic
    const result = await checkAuth('/session-stage')
    expect(result).toBe('/login')
  })

  it('allows navigation when /api/auth/me returns 200', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ email: 'user@example.com' }), { status: 200 })
    )
    const result = await checkAuth('/session-stage')
    expect(result).toBe(true)
  })

  it('allows navigation when /api/auth/me fails (local mode)', async () => {
    // In local mode, /api/auth/me doesn't exist -> fetch throws
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('Network error'))
    const result = await checkAuth('/session-stage')
    expect(result).toBe(true)
  })
})
```

**Implement** (`src/router/index.ts`):

```typescript
import { createRouter, createWebHistory } from 'vue-router'

export async function checkAuth(targetPath: string): Promise<true | string> {
  if (targetPath === '/login') return true
  try {
    const res = await fetch('/api/auth/me')
    if (res.ok) return true
    return '/login'
  } catch {
    return true  // Local mode: auth endpoint doesn't exist, allow through
  }
}

// Add route
{ path: '/login', name: 'Login', component: () => import('../pages/Login.vue') }

// Add guard
router.beforeEach(async (to) => {
  return await checkAuth(to.path)
})
```

---

### Step 10: Makefile

**No test needed** (infrastructure).

**Implement** (`Makefile`):

```makefile
dev:
	@echo "Local mode: http://127.0.0.1:8000"
	@trap 'kill 0' INT TERM EXIT; \
	( cd agents-stage-live2d-vrm3d-server && \
	  .venv/bin/python main.py --host 127.0.0.1 --port 8000 ) & \
	( cd agents-stage-live2d-vrm3d-fe && npm run dev ) & \
	wait

dev-remote:
	@echo "Remote mode: building frontend..."
	cd agents-stage-live2d-vrm3d-fe && npm run build
	@echo "Starting remote server with auth..."
	@trap 'kill 0' INT TERM EXIT; \
	( cd agents-stage-live2d-vrm3d-server && \
	  .venv/bin/python main.py --host 127.0.0.1 --port 8000 \
	  --mode remote \
	  --static-path ../agents-stage-live2d-vrm3d-fe/dist ) & \
	( cloudflared tunnel run agents-stage ) & \
	wait

build-h5:
	cd agents-stage-live2d-vrm3d-fe && npm run build
```

---

## Integration Tests

### Backend integration test suite (`auth_test.py`)

```python
class RemoteModeIntegrationTest(unittest.IsolatedAsyncioTestCase):
    """
    End-to-end integration tests simulating the full remote mode flow.
    Uses FastAPI TestClient with all middleware and routes mounted.
    """

    @classmethod
    def setUpClass(cls):
        cls.config = Config(remote=RemoteConfig(
            google_client_id="test-client-id",
            google_client_secret="test-client-secret",
            jwt_secret="integration-test-secret",
            allowed_emails=["allowed@example.com"],
        ))
        cls.app = create_app(mode="remote", config=cls.config)
        cls.client = TestClient(cls.app)

    # --- Auth flow ---

    def test_unauthenticated_api_returns_401(self):
        """API requests without auth cookie are rejected."""
        resp = self.client.get("/api/session-bridge/health")
        self.assertEqual(resp.status_code, 401)

    def test_login_redirects_to_google(self):
        """Login endpoint redirects to Google OAuth consent screen."""
        resp = self.client.get("/api/auth/login", follow_redirects=False)
        self.assertEqual(resp.status_code, 307)
        self.assertIn("accounts.google.com", resp.headers["location"])
        self.assertIn("test-client-id", resp.headers["location"])

    @patch("live2d_server.auth.exchange_google_code")
    def test_full_login_flow(self, mock_exchange):
        """
        Complete flow: login -> callback -> access API -> logout.
        Mocks only the Google token exchange (external dependency).
        """
        # Step 1: Callback with valid code
        mock_exchange.return_value = {"email": "allowed@example.com"}
        resp = self.client.get(
            "/api/auth/callback?code=auth-code",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 307)
        auth_cookie = resp.cookies.get("auth_token")
        self.assertIsNotNone(auth_cookie)

        # Step 2: Access protected API with cookie
        resp = self.client.get(
            "/api/session-bridge/health",
            cookies={"auth_token": auth_cookie},
        )
        self.assertEqual(resp.status_code, 200)

        # Step 3: Check /me
        resp = self.client.get(
            "/api/auth/me",
            cookies={"auth_token": auth_cookie},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["email"], "allowed@example.com")

        # Step 4: Logout
        resp = self.client.post(
            "/api/auth/logout",
            cookies={"auth_token": auth_cookie},
        )
        self.assertEqual(resp.status_code, 200)

        # Step 5: Old cookie should still work until it expires
        # (stateless JWT — no server-side revocation needed for single user)

    @patch("live2d_server.auth.exchange_google_code")
    def test_disallowed_email_cannot_access(self, mock_exchange):
        """User with valid Google account but not in whitelist is rejected."""
        mock_exchange.return_value = {"email": "stranger@example.com"}
        resp = self.client.get(
            "/api/auth/callback?code=auth-code",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 403)

    # --- WebSocket ---

    def test_ws_unauthenticated_closed(self):
        """WebSocket without auth cookie is closed."""
        with self.assertRaises(Exception):
            with self.client.websocket_connect("/api/session-bridge/ws") as ws:
                ws.receive_json()

    def test_ws_authenticated_connects(self):
        """WebSocket with valid auth cookie connects successfully."""
        token = create_jwt("allowed@example.com", "integration-test-secret", 3600)
        with self.client.websocket_connect(
            "/api/session-bridge/ws",
            cookies={"auth_token": token},
        ) as ws:
            # Connection accepted = success
            # Close gracefully
            pass

    # --- Static files (remote mode) ---

    def test_static_html_requires_auth(self):
        """In remote mode, even static pages require authentication."""
        resp = self.client.get("/", follow_redirects=False)
        # Should redirect to login or return 401
        self.assertIn(resp.status_code, [301, 302, 307, 401])

    # --- Local mode regression ---

    def test_local_mode_unchanged(self):
        """Verify local mode has no auth at all (regression guard)."""
        local_app = create_app(mode="local", config=Config())
        local_client = TestClient(local_app)
        resp = local_client.get("/api/session-bridge/health")
        self.assertEqual(resp.status_code, 200)
```

### Frontend integration test (manual checklist)

Since the frontend auth guard relies on `/api/auth/me` which is a simple fetch,
and the login page is a single button, integration testing is done via:

```
[ ] Local mode: `make dev` → all pages load without login
[ ] Remote mode: `make dev-remote` → redirected to Google login
[ ] Remote mode: login with allowed email → access granted
[ ] Remote mode: login with disallowed email → 403 page
[ ] Remote mode: WebSocket reconnects after page refresh (cookie persists)
[ ] Remote mode: /api/auth/me returns current user
[ ] Remote mode: logout → redirected to login
[ ] Existing tests: `npm run test:unit` all pass (frontend)
[ ] Existing tests: `python -m unittest` all pass (backend)
```

---

## Test Execution Commands

```bash
# Backend: run all tests (existing + new auth tests)
cd agents-stage-live2d-vrm3d-server
.venv/bin/python -m unittest discover -s live2d_server -p '*_test.py' -v

# Frontend: run all tests (existing + new router tests)
cd agents-stage-live2d-vrm3d-fe
npm run test:unit

# Run only auth tests
cd agents-stage-live2d-vrm3d-server
.venv/bin/python -m unittest live2d_server.auth_test -v
```

---

## Dependencies to Add

### Backend (`pyproject.toml`)

```toml
[project.dependencies]
# ... existing deps ...
python-jose = { version = ">=3.3.0", extras = ["cryptography"] }
httpx = ">=0.27.0"    # For async Google token exchange
```

### Frontend

No new dependencies needed.

---

## Configuration Reference

### config.json (new, gitignored)

```json
{
  "remote": {
    "google_client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
    "google_client_secret": "GOCSPX-YOUR_SECRET",
    "jwt_secret": "auto-generated-on-first-run-if-empty",
    "allowed_emails": ["your-email@gmail.com"],
    "cookie_max_age": 86400
  }
}
```

### Google Cloud Console setup (one-time)

1. Create project at https://console.cloud.google.com/
2. Enable "Google People API"
3. Create OAuth 2.0 Client ID (Web application)
4. Add authorized redirect URI: `https://YOUR-TUNNEL.example.com/api/auth/callback`
5. Copy client_id and client_secret to config.json

### Cloudflare Tunnel setup (one-time)

```bash
# Install
brew install cloudflared

# Login
cloudflared tunnel login

# Create tunnel
cloudflared tunnel create agents-stage

# Configure (~/.cloudflared/config.yml)
tunnel: agents-stage
credentials-file: /Users/USERNAME/.cloudflared/TUNNEL_ID.json
ingress:
  - hostname: agents-stage.YOUR-DOMAIN.com
    service: http://127.0.0.1:8000
  - service: http_status:404

# Add DNS record
cloudflared tunnel route dns agents-stage agents-stage.YOUR-DOMAIN.com
```

---

## Security Considerations

| Concern | Mitigation |
|---------|-----------|
| JWT secret leakage | Stored in gitignored config.json, auto-generated if empty |
| Cookie theft (XSS) | HttpOnly + Secure + SameSite=Lax flags on cookie |
| OAuth code interception | State parameter in OAuth flow prevents CSRF |
| Email spoofing | Google verifies email; we only trust Google's response |
| Cloudflare bypass | Backend binds to 127.0.0.1, not 0.0.0.0 in remote mode |

---

## Implementation Order

```
Step 1   configuration.py — RemoteConfig model + tests
Step 2   auth.py — JWT utils + tests
Step 3   auth.py — email whitelist + tests
Step 4   auth.py — auth middleware + tests
Step 5   auth.py — Google OAuth endpoints + tests
Step 6   session_bridge_api.py — WebSocket auth + tests
Step 7   main.py — startup mode + tests
Step 8   Login.vue — login page
Step 9   router/index.ts — nav guard + tests
Step 10  Makefile — dev-remote target
Step 11  Integration tests — full flow
Step 12  Manual testing checklist
```
