import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from live2d_server.router import router
from live2d_server.stage_config import (
    FrontendConfigSnapshot,
    StageConfigRepository,
    build_empty_frontend_config_snapshot,
)


class StageConfigRepositoryTest(unittest.TestCase):
    def test_load_returns_empty_snapshot_when_file_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = StageConfigRepository(Path(temp_dir) / "missing.json")
            snapshot = repo.load()
            self.assertEqual(snapshot, build_empty_frontend_config_snapshot())

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = StageConfigRepository(Path(temp_dir) / "stage-settings.json")
            saved = repo.save(
                FrontendConfigSnapshot(
                    schemaVersion=1,
                    source="test-suite",
                    exportedAt="2026-03-24T00:00:00.000Z",
                    entries={
                        "live2d-viewer-settings": "{\"systemSettings\":{}}",
                        "vrm-stage-actor-scale-v1": "1.6",
                    },
                )
            )

            loaded = repo.load()
            self.assertEqual(loaded, saved)


class StageConfigApiTest(unittest.TestCase):
    def _create_client(self, config_path: Path) -> TestClient:
        app = FastAPI()
        app.state.stage_config_repository = StageConfigRepository(config_path)
        app.include_router(router)
        return TestClient(app)

    def test_get_stage_config_returns_empty_snapshot_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client = self._create_client(Path(temp_dir) / "stage-settings.json")
            response = client.get("/api/stage-config")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), build_empty_frontend_config_snapshot().model_dump())

    def test_put_stage_config_persists_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "stage-settings.json"
            client = self._create_client(config_path)
            payload = {
                "schemaVersion": 1,
                "source": "frontend-test",
                "exportedAt": "2026-03-24T01:02:03.000Z",
                "entries": {
                    "live2d-viewer-settings": "{\"systemSettings\":{}}",
                },
            }

            put_response = client.put("/api/stage-config", json=payload)
            self.assertEqual(put_response.status_code, 200)
            self.assertEqual(put_response.json(), payload)

            get_response = client.get("/api/stage-config")
            self.assertEqual(get_response.status_code, 200)
            self.assertEqual(get_response.json(), payload)

            self.assertTrue(config_path.exists())
