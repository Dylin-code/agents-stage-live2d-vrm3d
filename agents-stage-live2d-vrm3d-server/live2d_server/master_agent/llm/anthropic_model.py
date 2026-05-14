"""Anthropic /v1/messages adapter with native tool calling.

Uses httpx directly (no anthropic SDK dependency) to keep the surface
minimal — only the fields the master agent needs are wired up.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any, Optional

import httpx

from ..contracts.llm_port import (
    ChatMessage,
    ChatModelDelta,
    ChatModelPort,
    ChatModelResult,
    ToolCall,
    ToolSchema,
)

_LOGGER = logging.getLogger(__name__)
_REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


class AnthropicChatModel(ChatModelPort):
    """Anthropic Messages API adapter."""

    provider_id: str = "anthropic"
    supports_native_tool_calling: bool = True

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str = "claude-sonnet-4-6",
        base_url: str = "https://api.anthropic.com",
        anthropic_version: str = "2023-06-01",
        max_tokens: int = 8192,
    ) -> None:
        if not api_key:
            raise ValueError("AnthropicChatModel requires an api_key")
        self.default_model = default_model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._version = anthropic_version
        self._max_tokens = max_tokens

    def _resolve_model(self, override: Optional[str]) -> str:
        if override and override.strip():
            return override.strip()
        return self.default_model

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": self._version,
            "content-type": "application/json",
        }

    @staticmethod
    def _to_anthropic_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "tool":
                out.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_use_id or "",
                        "content": _stringify_tool_content(msg.content),
                    }],
                })
                continue
            if msg.role == "assistant":
                content_blocks: list[dict[str, Any]] = []
                if isinstance(msg.content, str) and msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                for call in (msg.tool_calls or ()):
                    content_blocks.append({
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": dict(call.arguments or {}),
                    })
                if not content_blocks:
                    content_blocks.append({"type": "text", "text": ""})
                out.append({"role": "assistant", "content": content_blocks})
                continue
            # user
            content = msg.content
            if isinstance(content, str):
                out.append({"role": "user", "content": content})
            else:
                out.append({"role": "user", "content": content})
        return out

    @staticmethod
    def _to_anthropic_tools(tools: Sequence[ToolSchema]) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": dict(t.parameters),
            }
            for t in tools
        ]

    def _build_payload(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema],
        model: Optional[str],
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._resolve_model(model),
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": self._to_anthropic_messages(messages),
        }
        if tools:
            payload["tools"] = self._to_anthropic_tools(tools)
        if stream:
            payload["stream"] = True
        return payload

    async def generate(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] = (),
        model: Optional[str] = None,
    ) -> ChatModelResult:
        payload = self._build_payload(
            system=system, messages=messages, tools=tools, model=model, stream=False,
        )
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.post(
                f"{self._base_url}/v1/messages",
                json=payload,
                headers=self._headers(),
            )
        _raise_with_body(response)
        data = response.json()
        return _result_from_payload(data)

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] = (),
        model: Optional[str] = None,
    ) -> AsyncIterator[ChatModelDelta]:
        payload = self._build_payload(
            system=system, messages=messages, tools=tools, model=model, stream=True,
        )
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/messages",
                json=payload,
                headers=self._headers(),
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    text = body.decode("utf-8", errors="replace")
                    _LOGGER.error("Anthropic stream %s: %s", response.status_code, text[:2000])
                    raise httpx.HTTPStatusError(
                        f"{response.status_code} from anthropic: {text[:500]}",
                        request=response.request,
                        response=response,
                    )
                # Track in-progress tool_use blocks by index → {id, name, json_buf}
                tool_buffers: dict[int, dict[str, Any]] = {}
                stop_reason = ""
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    event_type = chunk.get("type")
                    if event_type == "content_block_start":
                        block = chunk.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            idx = int(chunk.get("index", 0))
                            tool_buffers[idx] = {
                                "id": str(block.get("id") or ""),
                                "name": str(block.get("name") or ""),
                                "json_buf": "",
                            }
                        continue
                    if event_type == "content_block_delta":
                        delta = chunk.get("delta") or {}
                        delta_type = delta.get("type")
                        if delta_type == "text_delta":
                            text = str(delta.get("text") or "")
                            if text:
                                yield ChatModelDelta(kind="text_delta", text=text)
                        elif delta_type == "input_json_delta":
                            idx = int(chunk.get("index", 0))
                            buf = tool_buffers.get(idx)
                            if buf is not None:
                                buf["json_buf"] += str(delta.get("partial_json") or "")
                        continue
                    if event_type == "content_block_stop":
                        idx = int(chunk.get("index", 0))
                        buf = tool_buffers.pop(idx, None)
                        if buf is not None:
                            raw = buf["json_buf"] or "{}"
                            try:
                                args = json.loads(raw)
                            except json.JSONDecodeError:
                                args = {}
                            yield ChatModelDelta(
                                kind="tool_call",
                                tool_call=ToolCall(
                                    id=buf["id"], name=buf["name"], arguments=args,
                                ),
                            )
                        continue
                    if event_type == "message_delta":
                        delta = chunk.get("delta") or {}
                        reason = str(delta.get("stop_reason") or "")
                        if reason:
                            stop_reason = reason
                        continue
                    if event_type == "message_stop":
                        yield ChatModelDelta(kind="stop", stop_reason=stop_reason)
                        return


def _stringify_tool_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def _result_from_payload(data: dict[str, Any]) -> ChatModelResult:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in data.get("content", []) or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text") or ""
            if isinstance(text, str):
                text_parts.append(text)
        elif btype == "tool_use":
            tool_calls.append(ToolCall(
                id=str(block.get("id") or ""),
                name=str(block.get("name") or ""),
                arguments=dict(block.get("input") or {}),
            ))
    return ChatModelResult(
        text="".join(text_parts),
        tool_calls=tuple(tool_calls),
        stop_reason=str(data.get("stop_reason") or ""),
        raw=data,
    )


def _raise_with_body(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    body = response.text
    _LOGGER.error("Anthropic %s for %s: %s", response.status_code, response.request.url, body[:2000])
    raise httpx.HTTPStatusError(
        f"{response.status_code} from {response.request.url}: {body[:500]}",
        request=response.request,
        response=response,
    )
