"""Agent provider router — dispatches to the correct CLI chat service based on brand."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

from .session_bridge_chat import CodexSessionChatService
from .session_bridge_claude_chat import ClaudeSessionChatService
from .session_bridge_shared import AGENT_BRAND_CLAUDE, AGENT_BRAND_CODEX

ChatService = Union[CodexSessionChatService, ClaudeSessionChatService]


@dataclass(frozen=True)
class AgentBrandMetadata:
    brand: str
    display_name: str
    badge_icon: str
    models: tuple[str, ...]
    session_dir_env: str
    session_dir_default: str


_BRAND_METADATA: dict[str, AgentBrandMetadata] = {
    AGENT_BRAND_CODEX: AgentBrandMetadata(
        brand=AGENT_BRAND_CODEX,
        display_name="Codex",
        badge_icon="/brand/codex-badge.svg",
        models=("gpt-5.3-codex", "gpt-5.4", "gpt-5.2-codex", "gpt-5.1-codex-max", "gpt-5.2"),
        session_dir_env="CODEX_SESSION_DIR",
        session_dir_default="~/.codex/sessions",
    ),
    AGENT_BRAND_CLAUDE: AgentBrandMetadata(
        brand=AGENT_BRAND_CLAUDE,
        display_name="Claude",
        badge_icon="/brand/claude-badge.svg",
        models=("claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001", "sonnet", "opus", "haiku"),
        session_dir_env="CLAUDE_SESSION_DIR",
        session_dir_default="~/.claude/projects",
    ),
}


class AgentProviderRouter:
    """Lazily initialises and caches per-brand chat services."""

    def __init__(self, default_cwd: str | None = None) -> None:
        self._default_cwd = default_cwd
        self._codex_service: CodexSessionChatService | None = None
        self._claude_service: ClaudeSessionChatService | None = None

    # ------------------------------------------------------------------
    # Chat service accessors
    # ------------------------------------------------------------------

    def get_chat_service(self, brand: str) -> ChatService:
        normalized = self.normalize_brand(brand)
        if normalized == AGENT_BRAND_CLAUDE:
            return self._get_claude_service()
        return self._get_codex_service()

    def _get_codex_service(self) -> CodexSessionChatService:
        if self._codex_service is None:
            self._codex_service = CodexSessionChatService(default_cwd=self._default_cwd)
        return self._codex_service

    def _get_claude_service(self) -> ClaudeSessionChatService:
        if self._claude_service is None:
            self._claude_service = ClaudeSessionChatService(default_cwd=self._default_cwd)
        return self._claude_service

    # ------------------------------------------------------------------
    # Session directory accessors (for runtime file watchers)
    # ------------------------------------------------------------------

    @staticmethod
    def get_session_dir(brand: str) -> Path:
        metadata = AgentProviderRouter.brand_metadata(brand)
        return Path(os.getenv(metadata.session_dir_env, metadata.session_dir_default)).expanduser()

    @staticmethod
    def get_all_session_dirs() -> dict[str, Path]:
        return {
            metadata.brand: Path(os.getenv(metadata.session_dir_env, metadata.session_dir_default)).expanduser()
            for metadata in _BRAND_METADATA.values()
        }

    # ------------------------------------------------------------------
    # Brand utilities
    # ------------------------------------------------------------------

    @staticmethod
    def supported_brands() -> list[str]:
        return list(_BRAND_METADATA.keys())

    @staticmethod
    def normalize_brand(value: str | None) -> str:
        normalized = (value or "").strip().lower()
        if normalized in _BRAND_METADATA:
            return normalized
        raise ValueError(f"unsupported agent brand: {value}")

    @staticmethod
    def brand_metadata(brand: str) -> AgentBrandMetadata:
        normalized = AgentProviderRouter.normalize_brand(brand)
        return _BRAND_METADATA[normalized]

    @staticmethod
    def default_models(brand: str) -> list[str]:
        return list(AgentProviderRouter.brand_metadata(brand).models)

    @staticmethod
    def brand_catalog() -> list[dict[str, Any]]:
        return [
            {
                "brand": metadata.brand,
                "display_name": metadata.display_name,
                "badge_icon": metadata.badge_icon,
                "models": list(metadata.models),
            }
            for metadata in _BRAND_METADATA.values()
        ]
