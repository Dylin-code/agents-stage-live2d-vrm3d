"""Build the active chat model from environment variables.

Env knobs (all read at process startup; restart to switch):

- ``MASTER_AGENT_LLM_PROVIDER`` — ``anthropic`` (default) | ``openai`` | ``local``
- ``MASTER_AGENT_LLM_MODEL``    — provider-specific default model id
- ``MASTER_AGENT_LLM_API_KEY``  — required for anthropic / openai;
                                 ignored (or any sentinel) for ``local``
- ``MASTER_AGENT_LLM_BASE_URL`` — required for ``local``; optional for
                                 ``openai`` (custom proxies); ignored
                                 for ``anthropic``
- ``MASTER_AGENT_LLM_TOOL_MODE`` — ``auto`` (default) | ``native`` | ``prompt``
    * ``auto``   — anthropic / openai → native; local → prompt
    * ``native`` — force native function-calling (provider must support it)
    * ``prompt`` — Kokoro-style JSON-in-prompt fallback; works on any
                   text model but yields one tool call per turn
"""

from __future__ import annotations

import logging
import os
from typing import Literal, Optional

from ..contracts.llm_port import ChatModelPort
from .anthropic_model import AnthropicChatModel
from .openai_model import OpenAICompatibleChatModel

ToolMode = Literal["native", "prompt"]

_LOGGER = logging.getLogger(__name__)

_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
_DEFAULT_LOCAL_MODEL = "qwen2.5:14b"
_DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1"


def _env(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    text = value.strip()
    return text or None


def build_chat_model() -> ChatModelPort:
    provider = (_env("MASTER_AGENT_LLM_PROVIDER") or "anthropic").lower()
    model = _env("MASTER_AGENT_LLM_MODEL")
    api_key = _env("MASTER_AGENT_LLM_API_KEY")
    base_url = _env("MASTER_AGENT_LLM_BASE_URL")

    if provider == "anthropic":
        if not api_key:
            api_key = _env("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "MASTER_AGENT_LLM_API_KEY (or ANTHROPIC_API_KEY) is required for provider=anthropic",
            )
        return AnthropicChatModel(
            api_key=api_key,
            default_model=model or _DEFAULT_ANTHROPIC_MODEL,
        )

    if provider == "openai":
        if not api_key:
            api_key = _env("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "MASTER_AGENT_LLM_API_KEY (or OPENAI_API_KEY) is required for provider=openai",
            )
        return OpenAICompatibleChatModel(
            api_key=api_key,
            default_model=model or _DEFAULT_OPENAI_MODEL,
            base_url=base_url,
            provider_id="openai",
        )

    if provider == "local":
        return OpenAICompatibleChatModel(
            api_key=api_key or "sk-noop",
            default_model=model or _DEFAULT_LOCAL_MODEL,
            base_url=base_url or _DEFAULT_LOCAL_BASE_URL,
            provider_id="local",
        )

    raise ValueError(
        f"Unsupported MASTER_AGENT_LLM_PROVIDER={provider!r}; "
        "expected anthropic | openai | local",
    )


def resolve_tool_mode() -> ToolMode:
    """Resolve the tool-call mode from env.

    ``auto`` (default) picks ``native`` for anthropic / openai (they
    have first-class function calling) and ``prompt`` for ``local``
    (most local backends either don't enable function calling or use
    inconsistent parsers — JSON-in-prompt is the reliable baseline).
    """
    raw = (_env("MASTER_AGENT_LLM_TOOL_MODE") or "auto").lower()
    if raw in ("native", "prompt"):
        return raw  # type: ignore[return-value]
    provider = (_env("MASTER_AGENT_LLM_PROVIDER") or "anthropic").lower()
    if provider == "local":
        return "prompt"
    return "native"


def describe_active_llm() -> dict[str, str]:
    """Return a dict describing the env-configured LLM for the /llm/info endpoint.

    Does NOT instantiate the client (avoids requiring keys when only
    introspecting). Mirrors :func:`build_chat_model` resolution rules.
    """
    provider = (_env("MASTER_AGENT_LLM_PROVIDER") or "anthropic").lower()
    model = _env("MASTER_AGENT_LLM_MODEL")
    base_url = _env("MASTER_AGENT_LLM_BASE_URL")
    if provider == "anthropic":
        resolved_model = model or _DEFAULT_ANTHROPIC_MODEL
        resolved_base = "https://api.anthropic.com"
    elif provider == "openai":
        resolved_model = model or _DEFAULT_OPENAI_MODEL
        resolved_base = base_url or "https://api.openai.com/v1"
    elif provider == "local":
        resolved_model = model or _DEFAULT_LOCAL_MODEL
        resolved_base = base_url or _DEFAULT_LOCAL_BASE_URL
    else:
        resolved_model = model or "unknown"
        resolved_base = base_url or ""
    return {
        "provider": provider,
        "model": resolved_model,
        "base_url": resolved_base,
        "tool_mode": resolve_tool_mode(),
    }
