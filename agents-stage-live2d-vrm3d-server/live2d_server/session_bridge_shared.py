import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

SESSION_STATES = {"IDLE", "THINKING", "TOOLING", "RESPONDING", "WAITING"}
STATE_PRIORITY = {"IDLE": 0, "RESPONDING": 1, "THINKING": 2, "TOOLING": 3, "WAITING": 4}
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
WHITESPACE_PATTERN = re.compile(r"\s+")
PERMISSION_MODE_DEFAULT = "default"
PERMISSION_MODE_FULL = "full"
DEFAULT_APPROVAL_POLICY = "on-request"
DEFAULT_SANDBOX_MODE = "workspace-write"
_STREAM_LIMIT_ENV = os.getenv("SESSION_BRIDGE_STREAM_LIMIT_BYTES")
try:
    STREAM_READER_LINE_LIMIT = max(256 * 1024, int(_STREAM_LIMIT_ENV)) if _STREAM_LIMIT_ENV else 4 * 1024 * 1024
except ValueError:
    STREAM_READER_LINE_LIMIT = 4 * 1024 * 1024

# Agent brand constants
AGENT_BRAND_CODEX = "codex"
AGENT_BRAND_CLAUDE = "claude"
SUPPORTED_AGENT_BRANDS = {AGENT_BRAND_CODEX, AGENT_BRAND_CLAUDE}
_FRONTEND_DIR_NAMES = ("agents-stage-live2d-vrm3d-fe", "live2d-assistant-fe")
_BACKEND_DIR_NAMES = ("agents-stage-live2d-vrm3d-server", "live2d-assistant-server")

# Known Claude model context windows (tokens).
_CLAUDE_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "sonnet": 200_000,
    "opus": 200_000,
    "haiku": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000,
}

_AUTO_INJECTED_USER_PROMPTS = {
    "initialize a new codex session. reply with: session_ready",
    "initialize a new session. reply with: session_ready",
    "please produce a detailed implementation plan before any code edits.",
    "tool loaded.",
    "tool loaded",
}
_AUTO_INJECTED_ASSISTANT_MESSAGES = {
    "session_ready",
    "tool loaded.",
    "tool loaded",
}
_AUTO_INJECTED_MESSAGE_PREFIXES = (
    "# agents.md instructions for ",
    "warning: apply_patch was requested via",
)


def _ensure_stream_reader_limit(
    stream: Optional[asyncio.StreamReader],
    limit: int = STREAM_READER_LINE_LIMIT,
) -> None:
    """Raise StreamReader line limit to avoid LimitOverrunError on large JSON lines."""
    if stream is None or limit <= 0:
        return
    current_limit = getattr(stream, "_limit", None)
    if isinstance(current_limit, int) and current_limit >= limit:
        return
    try:
        stream._limit = limit
    except AttributeError:
        pass


def _claude_model_context_window(model: str) -> int:
    """Return known context window size for a Claude model, or 200000 as default."""
    normalized = (model or "").strip().lower()
    if not normalized:
        return 0
    if normalized in _CLAUDE_MODEL_CONTEXT_WINDOWS:
        return _CLAUDE_MODEL_CONTEXT_WINDOWS[normalized]
    # Fuzzy match: if model contains a known key prefix.
    for key, window in _CLAUDE_MODEL_CONTEXT_WINDOWS.items():
        if key in normalized or normalized in key:
            return window
    # All current Claude models have 200k context.
    if "claude" in normalized or normalized in ("sonnet", "opus", "haiku"):
        return 200_000
    return 0


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_ts(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate:
        return ""
    try:
        normalized = candidate[:-1] + "+00:00" if candidate.endswith("Z") else candidate
        datetime.fromisoformat(normalized)
        return candidate
    except ValueError:
        return ""


def _ts_to_epoch(value: str) -> float:
    candidate = (value or "").strip()
    if not candidate:
        return 0.0
    try:
        normalized = candidate[:-1] + "+00:00" if candidate.endswith("Z") else candidate
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0.0


def _extract_message_content(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            candidate = item.get("text")
            if not isinstance(candidate, str) or not candidate.strip():
                candidate = item.get("content")
            if isinstance(candidate, str) and candidate.strip():
                chunks.append(candidate.strip())
        return " ".join(chunks).strip()
    message = payload.get("message")
    if isinstance(message, str):
        return message.strip()
    return ""


def _normalize_message_for_compare(text: str) -> str:
    normalized = WHITESPACE_PATTERN.sub(" ", str(text or "")).strip().lower()
    return normalized


def _is_auto_injected_message(role: str, content: str) -> bool:
    normalized_role = str(role or "").strip().lower()
    normalized_content = _normalize_message_for_compare(content)
    if not normalized_content:
        return False
    for prefix in _AUTO_INJECTED_MESSAGE_PREFIXES:
        if normalized_content.startswith(prefix):
            return True
    if normalized_role == "user":
        return normalized_content in _AUTO_INJECTED_USER_PROMPTS
    if normalized_role == "assistant":
        return normalized_content in _AUTO_INJECTED_ASSISTANT_MESSAGES
    return False


def _resolve_default_chat_cwd(default_cwd: Optional[str]) -> str:
    if default_cwd:
        return str(Path(default_cwd).expanduser())
    env_cwd = os.getenv("CODEX_CHAT_CWD")
    if env_cwd:
        return str(Path(env_cwd).expanduser())

    cwd = Path.cwd().resolve()
    candidates: list[Path] = []
    if cwd.name in _BACKEND_DIR_NAMES:
        candidates.append(cwd.parent)
    candidates.append(cwd)

    for candidate in candidates:
        if any((candidate / name).exists() for name in _FRONTEND_DIR_NAMES) and any(
            (candidate / name).exists() for name in _BACKEND_DIR_NAMES
        ):
            return str(candidate)
    return str(cwd)


def _basename_from_cwd(cwd: str) -> str:
    value = (cwd or "").strip()
    if not value:
        return ""
    return Path(value).name or value


def _normalize_permission_mode(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {
        "full",
        "complete",
        "full-access",
        "danger",
        "danger-full-access",
        "dangerously-bypass-approvals-and-sandbox",
    }:
        return PERMISSION_MODE_FULL
    return PERMISSION_MODE_DEFAULT


def _resolve_permission_mode(
    permission_mode: Optional[str],
    approval_policy: Optional[str] = None,
    sandbox_mode: Optional[str] = None,
) -> str:
    if permission_mode is not None and str(permission_mode).strip():
        return _normalize_permission_mode(permission_mode)
    sandbox_value = str(sandbox_mode or "").strip().lower()
    if sandbox_value == "danger-full-access":
        return PERMISSION_MODE_FULL
    return PERMISSION_MODE_DEFAULT


def _normalize_approval_policy(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"untrusted", "on-failure", "on-request", "never"}:
        return normalized
    return DEFAULT_APPROVAL_POLICY


def _normalize_sandbox_mode(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"read-only", "workspace-write", "danger-full-access"}:
        return normalized
    return DEFAULT_SANDBOX_MODE


def _resolve_permission_settings(
    permission_mode: Optional[str],
    approval_policy: Optional[str],
    sandbox_mode: Optional[str],
) -> tuple[str, str, str]:
    effective_mode = _resolve_permission_mode(
        permission_mode=permission_mode,
        approval_policy=approval_policy,
        sandbox_mode=sandbox_mode,
    )
    if effective_mode == PERMISSION_MODE_FULL:
        return effective_mode, "never", "danger-full-access"
    return (
        effective_mode,
        _normalize_approval_policy(approval_policy),
        _normalize_sandbox_mode(sandbox_mode),
    )


@dataclass
class _FileCursor:
    path: Path
    offset: int
    inode: int
    session_id: Optional[str] = None
    align_line_on_next_read: bool = False


@dataclass
class _SessionRecord:
    session_id: str
    display_name: str
    state: str = "IDLE"
    last_seen_at: str = field(default_factory=_iso_now)
    last_seen_epoch: float = field(default_factory=time.time)
    last_state_change_mono: float = field(default_factory=time.monotonic)
    pending_state: Optional[str] = None
    pending_due_mono: float = 0.0
    idle_due_epoch: Optional[float] = None
    active: bool = True
    originator: str = "Codex Desktop"
    agent_brand: str = AGENT_BRAND_CODEX
    cwd: str = ""
    cwd_basename: str = ""
    branch: str = ""
    model: str = ""
    effort: str = ""
    permission_mode: str = PERMISSION_MODE_DEFAULT
    approval_policy: str = ""
    sandbox_mode: str = ""
    plan_mode: Optional[bool] = None
    plan_mode_fallback: bool = False
    total_tokens: int = 0
    model_context_window: int = 0
    primary_rate_remaining_percent: Optional[float] = None
    secondary_rate_remaining_percent: Optional[float] = None
    last_event_type: str = ""
    has_real_user_input: bool = False


class AgentChatRequest(BaseModel):
    session_id: str
    message: str
    images: list[dict[str, Any]] = []
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    permission_mode: Optional[str] = None
    approval_policy: Optional[str] = None
    sandbox_mode: Optional[str] = None
    plan_mode: Optional[bool] = None
    cwd_override: Optional[str] = None
    git_branch: Optional[str] = None
    agent_brand: Optional[str] = None


class AgentChatApprovalRequest(BaseModel):
    pending_id: str
    decision: str
    prefix_rule: Optional[list[str]] = None
    agent_brand: Optional[str] = None


class AgentNewSessionRequest(BaseModel):
    cwd: str
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    permission_mode: Optional[str] = None
    approval_policy: Optional[str] = None
    sandbox_mode: Optional[str] = None
    plan_mode: Optional[bool] = None
    agent_brand: Optional[str] = None


class GitBranchSwitchRequest(BaseModel):
    session_id: Optional[str] = None
    cwd: Optional[str] = None
    branch: str


class AgentConversationRequest(BaseModel):
    limit: int = 1000


# Backward-compatibility aliases for legacy codex-specific names.
CodexChatRequest = AgentChatRequest
CodexChatApprovalRequest = AgentChatApprovalRequest
CodexNewSessionRequest = AgentNewSessionRequest
CodexConversationRequest = AgentConversationRequest
