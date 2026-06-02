"""Agent provider router — dispatches to the correct CLI chat service based on brand."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

from .session_bridge_chat import CodexSessionChatService
from .session_bridge_claude_chat import ClaudeSessionChatService
from .session_bridge_opencode_chat import OpencodeSessionChatService
from .session_bridge_shared import AGENT_BRAND_CLAUDE, AGENT_BRAND_CODEX, AGENT_BRAND_OPENCODE

ChatService = Union[CodexSessionChatService, ClaudeSessionChatService, OpencodeSessionChatService]

_CODEX_MODELS: tuple[str, ...] = (
    "gpt-5.5",
    "gpt-5.5-pro",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.1-codex-max",
    "gpt-5.2",
)

_CLAUDE_MODELS: tuple[str, ...] = (
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
    "sonnet",
    "opus",
    "haiku",
)

_OPENCODE_MODELS: tuple[str, ...] = (
    "opencode/deepseek-v4-flash-free",
    "opencode/minimax-m3-free",
    "opencode/mimo-v2.5-free",
    "opencode/gpt-5.5",
    "opencode/gpt-5.4",
    "opencode/gpt-5.4-mini",
    "opencode/gpt-5.4-nano",
    "opencode/gpt-5.3-codex",
    "opencode/big-pickle",
    "ollama-cloud/deepseek-v4-flash",
    "ollama-cloud/gemini-3-flash-preview",
    "ollama-cloud/claude-sonnet-4-6",
    "ollama-cloud/claude-opus-4-7",
)


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
        models=_CODEX_MODELS,
        session_dir_env="CODEX_SESSION_DIR",
        session_dir_default="~/.codex/sessions",
    ),
    AGENT_BRAND_CLAUDE: AgentBrandMetadata(
        brand=AGENT_BRAND_CLAUDE,
        display_name="Claude",
        badge_icon="/brand/claude-badge.svg",
        models=_CLAUDE_MODELS,
        session_dir_env="CLAUDE_SESSION_DIR",
        session_dir_default="~/.claude/projects",
    ),
    AGENT_BRAND_OPENCODE: AgentBrandMetadata(
        brand=AGENT_BRAND_OPENCODE,
        display_name="OpenCode",
        badge_icon="/brand/opencode-badge.svg",
        models=_OPENCODE_MODELS,
        session_dir_env="OPENCODE_DATA_DIR",
        session_dir_default="~/.local/share/opencode",
    ),
}


class AgentProviderRouter:
    """Lazily initialises and caches per-brand chat services."""

    def __init__(self, default_cwd: str | None = None) -> None:
        self._default_cwd = default_cwd
        self._codex_service: CodexSessionChatService | None = None
        self._claude_service: ClaudeSessionChatService | None = None
        self._opencode_service: OpencodeSessionChatService | None = None

    # ------------------------------------------------------------------
    # Chat service accessors
    # ------------------------------------------------------------------

    def get_chat_service(self, brand: str) -> ChatService:
        normalized = self.normalize_brand(brand)
        if normalized == AGENT_BRAND_OPENCODE:
            return self._get_opencode_service()
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

    def _get_opencode_service(self) -> OpencodeSessionChatService:
        if self._opencode_service is None:
            self._opencode_service = OpencodeSessionChatService(default_cwd=self._default_cwd)
        return self._opencode_service

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
    def default_permission_mode(brand: str) -> str:
        AgentProviderRouter.normalize_brand(brand)
        return "default"

    @staticmethod
    def brand_catalog() -> list[dict[str, Any]]:
        return [
            {
                "brand": metadata.brand,
                "display_name": metadata.display_name,
                "badge_icon": metadata.badge_icon,
                "models": list(metadata.models),
                "default_permission_mode": AgentProviderRouter.default_permission_mode(metadata.brand),
            }
            for metadata in _BRAND_METADATA.values()
        ]
