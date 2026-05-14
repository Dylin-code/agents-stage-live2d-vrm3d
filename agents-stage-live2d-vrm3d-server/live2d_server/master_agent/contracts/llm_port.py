"""LLM port for the master agent.

Richer than Kokoro's :class:`ChatModelPort` because the master agent
needs native tool calling. Providers that support it (Anthropic, OpenAI)
attach tool calls into :attr:`ChatModelResult.tool_calls`. Providers
without native tool calling can still satisfy the port by returning an
empty list and emitting JSON in :attr:`ChatModelResult.text`; a separate
parser layer turns that JSON into tool calls before dispatch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional, Protocol


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One message in the LLM conversation.

    Roles follow Anthropic / OpenAI conventions:
    - ``user``/``assistant`` for normal turns
    - ``tool`` for tool-result content; ``tool_use_id`` references the
      tool_call_id from the previous assistant turn
    """

    role: Literal["user", "assistant", "tool"]
    content: Any
    tool_use_id: Optional[str] = None
    tool_calls: Optional[Sequence["ToolCall"]] = None


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ToolSchema:
    """JSON-schema-lite description of one tool, fed into the LLM request."""

    name: str
    description: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ChatModelResult:
    """Non-streaming result."""

    text: str = ""
    tool_calls: Sequence[ToolCall] = field(default_factory=tuple)
    stop_reason: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChatModelDelta:
    """Streaming chunk.

    ``kind`` discriminates: ``text_delta`` carries token text,
    ``tool_call`` carries a fully-formed ToolCall (most providers
    flush tool_use blocks at end-of-block, not mid-stream),
    ``stop`` signals the model is done.
    """

    kind: Literal["text_delta", "tool_call", "stop"]
    text: str = ""
    tool_call: Optional[ToolCall] = None
    stop_reason: str = ""


class ChatModelPort(Protocol):
    """Master-agent LLM provider abstraction."""

    provider_id: str
    default_model: str
    supports_native_tool_calling: bool

    async def generate(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] = (),
        model: Optional[str] = None,
    ) -> ChatModelResult: ...

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] = (),
        model: Optional[str] = None,
    ) -> AsyncIterator[ChatModelDelta]: ...
