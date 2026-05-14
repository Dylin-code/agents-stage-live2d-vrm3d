"""Fake chat model — deterministic replies for tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Optional

from ..contracts.llm_port import (
    ChatMessage,
    ChatModelDelta,
    ChatModelPort,
    ChatModelResult,
    ToolCall,
    ToolSchema,
)


class FakeChatModel(ChatModelPort):
    """Replays a scripted sequence of :class:`ChatModelResult` objects.

    Tests construct a FakeChatModel with a list of results; each call to
    :meth:`generate` or :meth:`stream` pops the next result. Useful for
    driving the master agent run loop through a known tool-call sequence.
    """

    provider_id: str = "fake"
    default_model: str = "fake-1"
    supports_native_tool_calling: bool = True

    def __init__(self, scripted_results: Sequence[ChatModelResult]) -> None:
        self._results: list[ChatModelResult] = list(scripted_results)
        self._cursor = 0
        self.calls: list[dict[str, Any]] = []

    def _next(self) -> ChatModelResult:
        if self._cursor >= len(self._results):
            return ChatModelResult(text="(fake exhausted)")
        result = self._results[self._cursor]
        self._cursor += 1
        return result

    async def generate(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] = (),
        model: Optional[str] = None,
    ) -> ChatModelResult:
        self.calls.append({
            "system": system,
            "messages": list(messages),
            "tools": list(tools),
            "model": model,
        })
        return self._next()

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] = (),
        model: Optional[str] = None,
    ) -> AsyncIterator[ChatModelDelta]:
        result = await self.generate(system=system, messages=messages, tools=tools, model=model)
        if result.text:
            yield ChatModelDelta(kind="text_delta", text=result.text)
        for call in result.tool_calls:
            yield ChatModelDelta(kind="tool_call", tool_call=call)
        yield ChatModelDelta(kind="stop", stop_reason=result.stop_reason or "end_turn")
