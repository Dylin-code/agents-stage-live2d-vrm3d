"""Tests for remote mode authentication: JWT, email whitelist, middleware, OAuth endpoints."""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from live2d_server.auth import (
    AuthGuardMiddleware,
    JwtError,
    create_auth_router,
    create_jwt,
    is_email_allowed,
    verify_jwt,
)
from live2d_server.configuration import Config, RemoteConfig


# ---------------------------------------------------------------------------
# JWT utility tests
# ---------------------------------------------------------------------------

class JwtUtilsTest(unittest.TestCase):
    def test_create_and_verify_token(self):
        token = create_jwt(email="user@example.com", secret="s3cret", expires_seconds=3600)
        payload = verify_jwt(token, secret="s3cret")
        self.assertEqual(payload["email"], "user@example.com")

    def test_expired_token_raises(self):
        token = create_jwt(email="user@example.com", secret="s3cret", expires_seconds=-1)
        with self.assertRaises(JwtError):
            verify_jwt(token, secret="s3cret")

    def test_invalid_token_raises(self):
        with self.assertRaises(JwtError):
            verify_jwt("garbage.token.here", secret="s3cret")

    def test_wrong_secret_raises(self):
        token = create_jwt(email="user@example.com", secret="s3cret", expires_seconds=3600)
        with self.assertRaises(JwtError):
            verify_jwt(token, secret="different-secret")


# ---------------------------------------------------------------------------
# Email whitelist tests
# ---------------------------------------------------------------------------

class EmailWhitelistTest(unittest.TestCase):
    def test_allowed_email_passes(self):
        self.assertTrue(is_email_allowed("danny@gmail.com", ["danny@gmail.com"]))

    def test_disallowed_email_rejected(self):
        self.assertFalse(is_email_allowed("hacker@evil.com", ["danny@gmail.com"]))

    def test_empty_whitelist_rejects_all(self):
        self.assertFalse(is_email_allowed("anyone@gmail.com", []))

    def test_case_insensitive(self):
        self.assertTrue(is_email_allowed("Danny@Gmail.COM", ["danny@gmail.com"]))


# ---------------------------------------------------------------------------
# Auth middleware tests
# ---------------------------------------------------------------------------

class AuthMiddlewareTest(unittest.TestCase):
    def _create_app(self, mode: str, allowed_emails: list[str] | None = None):
        app = FastAPI()
        remote_config = RemoteConfig(
            jwt_secret="test-secret",
            allowed_emails=allowed_emails or ["user@example.com"],
        )
        app.state.mode = mode
        app.state.remote_config = remote_config

        if mode == "remote":
            app.add_middleware(AuthGuardMiddleware, remote_config=remote_config)

        @app.get("/api/test")
        def test_endpoint():
            return {"ok": True}

        @app.get("/api/auth/login")
        def login():
            return {"login": True}

        return app

    def test_local_mode_no_auth_required(self):
        app = self._create_app(mode="local")
        client = TestClient(app)
        resp = client.get("/api/test")
        self.assertEqual(resp.status_code, 200)

    def test_remote_mode_rejects_unauthenticated(self):
        app = self._create_app(mode="remote")
        client = TestClient(app)
        resp = client.get("/api/test")
        self.assertEqual(resp.status_code, 401)

    def test_remote_mode_allows_auth_endpoints(self):
        app = self._create_app(mode="remote")
        client = TestClient(app)
        resp = client.get("/api/auth/login")
        self.assertEqual(resp.status_code, 200)

    def test_remote_mode_valid_cookie_passes(self):
        app = self._create_app(mode="remote")
        client = TestClient(app)
        token = create_jwt("user@example.com", "test-secret", 3600)
        resp = client.get("/api/test", cookies={"auth_token": token})
        self.assertEqual(resp.status_code, 200)

    def test_remote_mode_expired_cookie_rejected(self):
        app = self._create_app(mode="remote")
        client = TestClient(app)
        token = create_jwt("user@example.com", "test-secret", -1)
        resp = client.get("/api/test", cookies={"auth_token": token})
        self.assertEqual(resp.status_code, 401)

    def test_remote_mode_wrong_email_rejected(self):
        app = self._create_app(mode="remote", allowed_emails=["admin@example.com"])
        client = TestClient(app)
        token = create_jwt("hacker@evil.com", "test-secret", 3600)
        resp = client.get("/api/test", cookies={"auth_token": token})
        self.assertEqual(resp.status_code, 403)

    def test_remote_mode_page_redirects_to_login(self):
        """Non-API page requests without auth should redirect to /login."""
        app = self._create_app(mode="remote")
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/")
        self.assertEqual(resp.status_code, 307)
        self.assertIn("/login", resp.headers["location"])

    def test_remote_mode_allows_static_assets(self):
        """Static assets (/assets/*) should pass through without auth."""
        app = self._create_app(mode="remote")

        @app.get("/assets/test.js")
        def fake_asset():
            return {"asset": True}

        client = TestClient(app)
        resp = client.get("/assets/test.js")
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Google OAuth endpoint tests
# ---------------------------------------------------------------------------

class GoogleOAuthFlowTest(unittest.TestCase):
    def _create_auth_app(self, allowed_emails=None):
        remote_config = RemoteConfig(
            google_client_id="test-client-id",
            google_client_secret="test-client-secret",
            jwt_secret="test-secret",
            allowed_emails=allowed_emails or ["user@example.com"],
            allowed_origin="http://localhost:8000",
        )
        app = FastAPI()
        app.state.remote_config = remote_config
        app.include_router(create_auth_router(remote_config))
        return app

    def test_login_redirects_to_google(self):
        app = self._create_auth_app()
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/api/auth/login")
        self.assertIn(resp.status_code, [303, 307])
        self.assertIn("accounts.google.com", resp.headers["location"])

    @patch("fastapi_sso.sso.google.GoogleSSO.verify_and_process", new_callable=AsyncMock)
    def test_callback_success_sets_cookie(self, mock_verify):
        from fastapi_sso.sso.base import OpenID
        mock_verify.return_value = OpenID(
            id="123", email="user@example.com", display_name="User",
            provider="google",
        )
        app = self._create_auth_app()
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/api/auth/callback?code=test-code&state=test-state")
        self.assertEqual(resp.status_code, 307)
        self.assertIn("auth_token", resp.cookies)

    @patch("fastapi_sso.sso.google.GoogleSSO.verify_and_process", new_callable=AsyncMock)
    def test_callback_disallowed_email_403(self, mock_verify):
        from fastapi_sso.sso.base import OpenID
        mock_verify.return_value = OpenID(
            id="456", email="hacker@evil.com", display_name="Hacker",
            provider="google",
        )
        app = self._create_auth_app(allowed_emails=["admin@example.com"])
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/api/auth/callback?code=test-code&state=test-state")
        self.assertEqual(resp.status_code, 403)

    def test_me_with_valid_cookie(self):
        app = self._create_auth_app()
        client = TestClient(app)
        token = create_jwt("user@example.com", "test-secret", 3600)
        resp = client.get("/api/auth/me", cookies={"auth_token": token})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["email"], "user@example.com")

    def test_me_without_cookie_401(self):
        app = self._create_auth_app()
        client = TestClient(app)
        resp = client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 401)

    def test_logout_clears_cookie(self):
        app = self._create_auth_app()
        client = TestClient(app)
        token = create_jwt("user@example.com", "test-secret", 3600)
        resp = client.post("/api/auth/logout", cookies={"auth_token": token})
        self.assertEqual(resp.status_code, 200)
        set_cookie = resp.headers.get("set-cookie", "")
        self.assertIn("auth_token", set_cookie)


# ---------------------------------------------------------------------------
# WebSocket auth tests
# ---------------------------------------------------------------------------

class WebSocketAuthTest(unittest.TestCase):
    def _create_ws_app(self, mode="remote"):
        from fastapi import WebSocket, WebSocketDisconnect
        from live2d_server.auth import verify_ws_auth

        app = FastAPI()
        remote_config = RemoteConfig(
            jwt_secret="test-secret",
            allowed_emails=["user@example.com"],
        )
        app.state.mode = mode
        app.state.remote_config = remote_config

        @app.websocket("/ws")
        async def ws_endpoint(websocket: WebSocket):
            if mode == "remote":
                if not await verify_ws_auth(websocket, remote_config):
                    return
            await websocket.accept()
            await websocket.send_json({"status": "connected"})
            await websocket.close()

        return app

    def test_ws_local_mode_no_auth(self):
        app = self._create_ws_app(mode="local")
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            data = ws.receive_json()
            self.assertEqual(data["status"], "connected")

    def test_ws_remote_mode_rejects_no_cookie(self):
        app = self._create_ws_app(mode="remote")
        client = TestClient(app)
        with self.assertRaises(Exception):
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()

    def test_ws_remote_mode_valid_cookie(self):
        app = self._create_ws_app(mode="remote")
        client = TestClient(app)
        token = create_jwt("user@example.com", "test-secret", 3600)
        with client.websocket_connect("/ws", cookies={"auth_token": token}) as ws:
            data = ws.receive_json()
            self.assertEqual(data["status"], "connected")


if __name__ == "__main__":
    unittest.main()
