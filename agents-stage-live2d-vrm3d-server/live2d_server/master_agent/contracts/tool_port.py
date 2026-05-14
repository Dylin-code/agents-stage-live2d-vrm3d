"""Tool port (mirrors Kokoro-Link's contracts/tool.py).

Each concrete tool subclasses :class:`ToolPort` and implements ``invoke``.
The orchestrator never imports concrete tool classes — it goes through
:class:`InMemoryToolRegistry` (see ``tool_registry.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Per-invocation context handed to a tool.

    Tools that need to talk back to runtime services (provider router,
    session bridge service, SubTaskTracker) pick the pieces they need
    from ``services``. ``arguments`` is the LLM-produced JSON, already
    parsed; tools validate their own schema.
    """

    conversation_id: str
    arguments: Mapping[str, Any]
    services: "ToolServices"
    default_cwd: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Unified tool return shape.

    ``data`` carries structured fields the LLM should be able to act on
    in the next hop (e.g. ``subtask_id``, ``session_id``). ``output_text``
    is the short human-readable string shown in the SSE event stream
    and fed back to the LLM as the tool-result content.
    """

    ok: bool
    output_text: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""

    @staticmethod
    def success(output_text: str, data: Optional[Mapping[str, Any]] = None) -> "ToolResult":
        return ToolResult(ok=True, output_text=output_text, data=data or {})

    @staticmethod
    def failure(error: str, output_text: str = "") -> "ToolResult":
        return ToolResult(
            ok=False,
            output_text=output_text or f"tool error: {error}",
            error=error,
        )


class ToolServices(Protocol):
    """Aggregate runtime services exposed to tools.

    Kept as a Protocol so tests can pass a stub without instantiating
    the real services. The concrete implementation lives in
    ``master_agent/service.py``.
    """

    @property
    def agent_provider(self) -> Any: ...
    @property
    def bridge_service(self) -> Any: ...
    @property
    def task_tracker(self) -> Any: ...
    @property
    def loop(self) -> Any: ...
    @property
    def permit_full_access(self) -> bool:
        """Whether the user has unlocked the ``permission_mode=full``
        path for this chat turn (via ``#full`` keyword). When False,
        tools downgrade any explicit ``full`` request to ``auto`` so
        the LLM can't shed all sandboxing on its own."""
        ...


class ToolPort(Protocol):
    """One concrete tool — codex_new_session, claude_send_prompt, etc.

    Implementations should be stateless per call; long-lived resources
    (HTTP clients, etc.) belong on the registry or service singleton.
    """

    name: str
    description: str
    parameters_schema: Mapping[str, Any]

    async def invoke(self, ctx: ToolContext) -> ToolResult: ...
