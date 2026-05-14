"""OpenAI-compatible chat model adapter.

Targets OpenAI, Ollama (``OLLAMA_BASE_URL=http://localhost:11434/v1``),
LM Studio and any other endpoint that exposes
``/v1/chat/completions`` with function calling. Uses the official
``openai`` async client which is already a project dependency.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any, Optional

from openai import AsyncOpenAI, BadRequestError

from ..contracts.llm_port import (
    ChatMessage,
    ChatModelDelta,
    ChatModelPort,
    ChatModelResult,
    ToolCall,
    ToolSchema,
)

_LOGGER = logging.getLogger(__name__)


class OpenAICompatibleChatModel(ChatModelPort):
    """OpenAI-compatible chat completions adapter."""

    provider_id: str
    supports_native_tool_calling: bool = True

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str = "gpt-4o-mini",
        base_url: Optional[str] = None,
        provider_id: str = "openai",
        timeout_seconds: float = 300.0,
    ) -> None:
        # Local providers (Ollama/LM Studio) often accept any string for api_key.
        self._client = AsyncOpenAI(
            api_key=api_key or "sk-noop",
            base_url=base_url or None,
            timeout=timeout_seconds,
        )
        self.provider_id = provider_id
        self.default_model = default_model

    @staticmethod
    def _to_openai_messages(system: str, messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        for msg in messages:
            if msg.role == "tool":
                out.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_use_id or "",
                    "content": _stringify_tool_content(msg.content),
                })
                continue
            if msg.role == "assistant":
                entry: dict[str, Any] = {"role": "assistant"}
                if isinstance(msg.content, str) and msg.content:
                    entry["content"] = msg.content
                else:
                    entry["content"] = None
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments or {}, ensure_ascii=False),
                            },
                        }
                        for call in msg.tool_calls
                    ]
                out.append(entry)
                continue
            # user
            content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False)
            out.append({"role": "user", "content": content})
        return out

    @staticmethod
    def _to_openai_tools(tools: Sequence[ToolSchema]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": dict(t.parameters),
                },
            }
            for t in tools
        ]

    def _resolve_model(self, override: Optional[str]) -> str:
        if override and override.strip():
            return override.strip()
        return self.default_model

    async def generate(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] = (),
        model: Optional[str] = None,
    ) -> ChatModelResult:
        params: dict[str, Any] = {
            "model": self._resolve_model(model),
            "messages": self._to_openai_messages(system, messages),
        }
        if tools:
            params["tools"] = self._to_openai_tools(tools)
        try:
            completion = await self._client.chat.completions.create(**params)
        except BadRequestError as exc:
            raise _wrap_tool_choice_error(exc, self.provider_id) from exc
        choice = completion.choices[0] if completion.choices else None
        if choice is None:
            return ChatModelResult()
        message = choice.message
        tool_calls: list[ToolCall] = []
        for call in (message.tool_calls or []):
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=call.id, name=call.function.name, arguments=args))
        return ChatModelResult(
            text=str(message.content or ""),
            tool_calls=tuple(tool_calls),
            stop_reason=str(choice.finish_reason or ""),
            raw=completion.model_dump() if hasattr(completion, "model_dump") else {},
        )

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] = (),
        model: Optional[str] = None,
    ) -> AsyncIterator[ChatModelDelta]:
        params: dict[str, Any] = {
            "model": self._resolve_model(model),
            "messages": self._to_openai_messages(system, messages),
            "stream": True,
        }
        if tools:
            params["tools"] = self._to_openai_tools(tools)

        # Tool calls arrive incrementally: accumulate by index.
        tool_buffers: dict[int, dict[str, Any]] = {}
        stop_reason = ""

        try:
            stream = await self._client.chat.completions.create(**params)
        except BadRequestError as exc:
            raise _wrap_tool_choice_error(exc, self.provider_id) from exc
        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta is None:
                continue
            text = str(delta.content or "")
            if text:
                yield ChatModelDelta(kind="text_delta", text=text)
            for call in (delta.tool_calls or []):
                idx = int(getattr(call, "index", 0) or 0)
                buf = tool_buffers.setdefault(
                    idx,
                    {"id": "", "name": "", "args": ""},
                )
                if call.id:
                    buf["id"] = call.id
                fn = getattr(call, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        buf["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        buf["args"] += fn.arguments
            if choice.finish_reason:
                stop_reason = str(choice.finish_reason)
                for buf in tool_buffers.values():
                    raw = buf["args"] or "{}"
                    try:
                        args = json.loads(raw)
                    except json.JSONDecodeError:
                        args = {}
                    if buf["name"]:
                        yield ChatModelDelta(
                            kind="tool_call",
                            tool_call=ToolCall(
                                id=buf["id"] or "",
                                name=buf["name"],
                                arguments=args,
                            ),
                        )
                tool_buffers.clear()
                yield ChatModelDelta(kind="stop", stop_reason=stop_reason)
                return


def _stringify_tool_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def _wrap_tool_choice_error(exc: BadRequestError, provider_id: str) -> RuntimeError:
    """Turn vLLM / LM Studio tool-calling 400 errors into actionable messages.

    The master agent fundamentally depends on tool calling. If the local
    server rejects ``tool_choice=auto`` we cannot fall back silently
    without producing nonsense — surface the cause and the fix instead.
    """
    raw = str(getattr(exc, "message", "") or exc) or ""
    raw_lower = raw.lower()
    hint = ""
    if "enable-auto-tool-choice" in raw_lower or "tool-call-parser" in raw_lower:
        hint = (
            " vLLM requires --enable-auto-tool-choice and --tool-call-parser "
            "(e.g. hermes for Hermes/Qwen models, llama3_json for Llama 3.x). "
            "Restart your vLLM server with both flags, or switch "
            "MASTER_AGENT_LLM_PROVIDER to a backend with built-in tool support "
            "(Anthropic, OpenAI, or Ollama with a tool-capable model)."
        )
    elif "tools" in raw_lower or "function" in raw_lower:
        hint = (
            " The local model/backend appears not to support OpenAI-style "
            "function calling. Switch to a tool-capable model or backend."
        )
    return RuntimeError(
        f"{provider_id} LLM rejected tool-call request: {raw}.{hint}"
    )
