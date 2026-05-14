"""Shared data models for the master agent.

Kept deliberately small — only types crossing module boundaries belong here.
Per-module helpers stay private to their files.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SubTaskStatus = Literal[
    "pending",
    "running",
    "awaiting_approval",
    "done",
    "failed",
    "aborted",
    # "detached" — the master agent's stream to the worker died (idle
    # timeout, parse error, exception), but the underlying codex/claude
    # CLI subprocess appears to still be writing to its on-disk session
    # JSONL. Treated as terminal for SubTaskTracker purposes; the LLM
    # should follow up via ``query_session_status(session_id=...)`` to
    # read disk-backed state.
    "detached",
]

AgentBrand = Literal["codex", "claude"]


@dataclass(slots=True)
class SubTask:
    """Worker task dispatched to a codex/claude session by the master agent."""

    id: str
    conversation_id: str
    agent_brand: str
    session_id: str
    prompt: str
    cwd: str
    status: SubTaskStatus = "pending"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    final_text: str = ""
    last_event_type: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "agent_brand": self.agent_brand,
            "session_id": self.session_id,
            "prompt": self.prompt,
            "cwd": self.cwd,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "final_text": self.final_text,
            "last_event_type": self.last_event_type,
            "error": self.error,
        }

    @staticmethod
    def new(
        *,
        conversation_id: str,
        agent_brand: str,
        session_id: str,
        prompt: str,
        cwd: str,
    ) -> "SubTask":
        return SubTask(
            id=uuid.uuid4().hex,
            conversation_id=conversation_id,
            agent_brand=agent_brand,
            session_id=session_id,
            prompt=prompt,
            cwd=cwd,
        )


@dataclass(slots=True)
class MasterEvent:
    """SSE event emitted by the master agent run loop."""

    type: str
    content: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "content": self.content}


MASTER_EVENT_THINKING_DELTA = "master_thinking_delta"
MASTER_EVENT_TOOL_CALL_BEGIN = "tool_call_begin"
MASTER_EVENT_TOOL_CALL_END = "tool_call_end"
MASTER_EVENT_SUBTASK_PROGRESS = "subtask_progress"
MASTER_EVENT_FINAL_TEXT = "final_text"
MASTER_EVENT_ERROR = "error"
MASTER_EVENT_HOP_LIMIT = "hop_limit_reached"


@dataclass(slots=True)
class MasterAgentConversation:
    """In-memory main-loop conversation history (master agent's own chat)."""

    id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def append(self, role: str, content: Any) -> None:
        self.messages.append({"role": role, "content": content})
        self.updated_at = time.time()

    @staticmethod
    def new() -> "MasterAgentConversation":
        return MasterAgentConversation(id=uuid.uuid4().hex)


# ---------------------------------------------------------------------------
# Pydantic request models (API surface)
# ---------------------------------------------------------------------------


class MasterAgentNewConversationRequest(BaseModel):
    """Body for POST /conversation/new — currently no fields required."""

    default_cwd: Optional[str] = Field(default=None, description="Optional fallback cwd for tools")


class MasterAgentChatRequest(BaseModel):
    conversation_id: str
    message: str
    default_cwd: Optional[str] = Field(
        default=None,
        description="Caller-provided cwd hint; the LLM may still choose a different cwd via tool args",
    )
    permit_full_access: bool = Field(
        default=False,
        description=(
            "When True, allow the LLM to set ``permission_mode=full`` "
            "(no sandbox, no approval) on this chat turn. The frontend "
            "sets this when the user types ``#full`` in their message. "
            "Without it, the master agent silently downgrades any "
            "``full`` request to ``auto`` (auto-review classifier)."
        ),
    )


class MasterAgentAbortRequest(BaseModel):
    conversation_id: str
