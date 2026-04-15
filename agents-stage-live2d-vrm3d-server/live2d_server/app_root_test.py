import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main


class AppRootHeartbeatTest(unittest.TestCase):
    @patch("main.start_live2d_motion_mapping_warmup")
    @patch("main.start_live2d_preview_warmup")
    @patch("main.stop_session_bridge", new_callable=AsyncMock)
    @patch("main.start_session_bridge", new_callable=AsyncMock)
    def test_root_returns_heartbeat_when_static_site_not_mounted(
        self,
        _start_session_bridge: AsyncMock,
        _stop_session_bridge: AsyncMock,
        _start_preview_warmup,
        _start_motion_warmup,
    ) -> None:
        original_static_path = main.static_path
        main.static_path = None
        try:
            app = main.create_app(mode="local")
            client = TestClient(app)
            response = client.get("/")
        finally:
            main.static_path = original_static_path

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": "true",
                "service": "agents-stage-live2d-vrm3d-server",
                "mode": "local",
            },
        )
