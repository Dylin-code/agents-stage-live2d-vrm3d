"""Tool orchestrator — exception isolation + duplicate detection.

Mirrors the responsibilities Kokoro-Link splits across permission,
audit, and orchestrator layers, kept minimal for the master agent:

- Resolve tool by name; unknown name → :class:`ToolResult.failure`.
- Catch any exception and convert to a failure result so the LLM can
  decide how to recover.
- Detect identical-args repeats (hash of name+args). On detection,
  short-circuit with a warning result so the LLM stops looping.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from .contracts.tool_port import ToolContext, ToolResult
from .tool_registry import InMemoryToolRegistry

_LOGGER = logging.getLogger(__name__)


class ToolOrchestrator:
    def __init__(self, registry: InMemoryToolRegistry) -> None:
        self._registry = registry
        self._recent_calls: dict[str, list[str]] = {}

    async def invoke(self, name: str, ctx: ToolContext) -> ToolResult:
        tool = self._registry.get(name)
        if tool is None:
            return ToolResult.failure(
                f"unknown tool: {name}",
                output_text=f"tool {name!r} is not registered",
            )
        signature = _hash_call(name, ctx.arguments)
        history = self._recent_calls.setdefault(ctx.conversation_id, [])
        if history.count(signature) >= 2:
            return ToolResult.failure(
                "tool called with identical arguments repeatedly; stop and report to user",
                output_text=(
                    f"refusing to invoke {name!r} again with identical arguments; "
                    "if you have nothing else useful to do, call report_to_user."
                ),
            )
        history.append(signature)
        if len(history) > 16:
            del history[: len(history) - 16]
        try:
            return await tool.invoke(ctx)
        except Exception as exc:  # noqa: BLE001 — isolation boundary
            _LOGGER.exception("tool %s raised", name)
            return ToolResult.failure(f"{name} raised: {exc}")


def _hash_call(name: str, arguments: Any) -> str:
    try:
        encoded = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        encoded = repr(arguments)
    return hashlib.sha256(f"{name}::{encoded}".encode("utf-8")).hexdigest()
