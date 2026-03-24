import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Dict

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

STAGE_CONFIG_SCHEMA_VERSION = 1
STAGE_CONFIG_FILENAME = "stage-settings.json"


class FrontendConfigSnapshot(BaseModel):
    schemaVersion: int = STAGE_CONFIG_SCHEMA_VERSION
    source: str = "agents-stage-live2d-vrm3d-fe"
    exportedAt: str = ""
    entries: Dict[str, str] = Field(default_factory=dict)


def build_empty_frontend_config_snapshot() -> FrontendConfigSnapshot:
    return FrontendConfigSnapshot(
        schemaVersion=STAGE_CONFIG_SCHEMA_VERSION,
        source="agents-stage-live2d-vrm3d-fe-server",
        exportedAt="",
        entries={},
    )


class StageConfigRepository:
    def __init__(self, config_path: Path | None = None):
        self._config_path = config_path or resolve_stage_config_path()

    @property
    def config_path(self) -> Path:
        return self._config_path

    def load(self) -> FrontendConfigSnapshot:
        path = self._config_path
        if not path.exists() or not path.is_file():
            return build_empty_frontend_config_snapshot()
        try:
            return FrontendConfigSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load stage config from %s: %s", path, exc)
            return build_empty_frontend_config_snapshot()

    def save(self, snapshot: FrontendConfigSnapshot) -> FrontendConfigSnapshot:
        normalized = FrontendConfigSnapshot.model_validate(snapshot)
        path = self._config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(normalized.model_dump(), indent=2, ensure_ascii=False)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
        ) as handle:
            handle.write(payload)
            temp_path = Path(handle.name)
        temp_path.replace(path)
        return normalized


def resolve_stage_config_path() -> Path:
    env_path = os.getenv("STAGE_CONFIG_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "config" / STAGE_CONFIG_FILENAME
