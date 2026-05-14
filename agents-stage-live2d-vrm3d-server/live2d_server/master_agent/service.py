"""Master agent service — multi-hop tool loop with SSE streaming.

The :class:`MasterAgentService` is the single entry point used by the
FastAPI router. It owns a :class:`ConversationStore`, a tool
:class:`InMemoryToolRegistry`, the :class:`SubTaskTracker`, and a
:class:`ChatModelPort` instance. The :class:`ToolServices` Protocol
is satisfied via an internal adapter so tools can reach the runtime
services without depending on the service module directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Optional

from .contracts.llm_port import ChatMessage, ChatModelPort, ToolCall
from .contracts.tool_port import ToolContext, ToolPort
from .conversation_store import ConversationStore
from .prompt_builder import ToolMode, build_system_prompt
from .tool_call_parser import looks_like_tool_call_attempt, parse_tool_call
from .shared import (
    MASTER_EVENT_ERROR,
    MASTER_EVENT_FINAL_TEXT,
    MASTER_EVENT_HOP_LIMIT,
    MASTER_EVENT_THINKING_DELTA,
    MASTER_EVENT_TOOL_CALL_BEGIN,
    MASTER_EVENT_TOOL_CALL_END,
    MasterAgentConversation,
    MasterEvent,
)
from .task_tracker import SubTaskTracker
from .tool_orchestrator import ToolOrchestrator
from .tool_registry import InMemoryToolRegistry
from .tools.report_tool import REPORT_TOOL_NAME

_LOGGER = logging.getLogger(__name__)
_MAX_HOPS = 8


@dataclass(frozen=True, slots=True)
class _ToolServicesAdapter:
    agent_provider: Any
    bridge_service: Any
    task_tracker: SubTaskTracker
    loop: Any
    permit_full_access: bool = False

    # Satisfies the ToolServices Protocol; @property not needed since
    # the dataclass attributes resolve to the same names.


class MasterAgentService:
    def __init__(
        self,
        *,
        chat_model: ChatModelPort,
        agent_provider: Any,
        bridge_service: Any,
        tool_registry: InMemoryToolRegistry,
        task_tracker: Optional[SubTaskTracker] = None,
        conversation_store: Optional[ConversationStore] = None,
        tool_mode: ToolMode = "native",
    ) -> None:
        self._chat_model = chat_model
        self._agent_provider = agent_provider
        self._bridge_service = bridge_service
        self._registry = tool_registry
        self._orchestrator = ToolOrchestrator(tool_registry)
        self._task_tracker = task_tracker or SubTaskTracker()
        self._store = conversation_store or ConversationStore()
        self._tool_mode: ToolMode = tool_mode
        self._abort_events: dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------------
    # Public surface for the API layer
    # ------------------------------------------------------------------

    @property
    def chat_model(self) -> ChatModelPort:
        return self._chat_model

    @property
    def task_tracker(self) -> SubTaskTracker:
        return self._task_tracker

    @property
    def conversation_store(self) -> ConversationStore:
        return self._store

    @property
    def tool_registry(self) -> InMemoryToolRegistry:
        return self._registry

    @property
    def tool_mode(self) -> ToolMode:
        return self._tool_mode

    async def new_conversation(self) -> MasterAgentConversation:
        return await self._store.create()

    async def abort(self, conversation_id: str) -> bool:
        event = self._abort_events.get(conversation_id)
        if event is None:
            return False
        event.set()
        return True

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run_stream(
        self,
        *,
        conversation_id: str,
        message: str,
        default_cwd: Optional[str] = None,
        permit_full_access: bool = False,
    ) -> AsyncIterator[MasterEvent]:
        message = (message or "").strip()
        if not message:
            yield MasterEvent(type=MASTER_EVENT_ERROR, content="message is required")
            return
        conversation = await self._store.get_or_create(conversation_id)
        conversation.append("user", message)
        # Persist the user message immediately so even an LLM stream
        # crash doesn't drop what the user just said.
        await self._store.save(conversation.id)

        abort_event = asyncio.Event()
        self._abort_events[conversation_id] = abort_event
        try:
            async for event in self._hop_loop(
                conversation=conversation,
                default_cwd=default_cwd,
                abort_event=abort_event,
                permit_full_access=permit_full_access,
            ):
                yield event
        finally:
            self._abort_events.pop(conversation_id, None)

    async def _hop_loop(
        self,
        *,
        conversation: MasterAgentConversation,
        default_cwd: Optional[str],
        abort_event: asyncio.Event,
        permit_full_access: bool = False,
    ) -> AsyncIterator[MasterEvent]:
        services = _ToolServicesAdapter(
            agent_provider=self._agent_provider,
            bridge_service=self._bridge_service,
            task_tracker=self._task_tracker,
            loop=asyncio.get_running_loop(),
            permit_full_access=permit_full_access,
        )
        tool_schemas = self._registry.schemas()
        # In prompt mode we suppress the native tools field and instead embed
        # the tool descriptors into the system prompt; the model then emits
        # `{"tool": ..., "args": {...}}` JSON which we parse with
        # :func:`parse_tool_call`.
        native_tools = tool_schemas if self._tool_mode == "native" else ()

        for hop in range(_MAX_HOPS):
            if abort_event.is_set():
                yield MasterEvent(type=MASTER_EVENT_ERROR, content="aborted by user")
                return

            subtasks = await self._task_tracker.list_for_conversation(conversation.id)
            system_prompt = build_system_prompt(
                subtasks=subtasks,
                tool_mode=self._tool_mode,
                tools=tool_schemas if self._tool_mode == "prompt" else (),
            )
            llm_messages = _conversation_to_llm_messages(conversation)

            text_accum = ""
            tool_calls: list[ToolCall] = []
            try:
                async for delta in self._chat_model.stream(
                    system=system_prompt,
                    messages=llm_messages,
                    tools=native_tools,
                ):
                    if delta.kind == "text_delta":
                        text_accum += delta.text
                        yield MasterEvent(
                            type=MASTER_EVENT_THINKING_DELTA,
                            content={"text": delta.text, "hop": hop},
                        )
                    elif delta.kind == "tool_call" and delta.tool_call is not None:
                        tool_calls.append(delta.tool_call)
                    elif delta.kind == "stop":
                        break
            except Exception as exc:  # noqa: BLE001 — propagate to user
                _LOGGER.exception("LLM stream failed for conversation %s", conversation.id)
                yield MasterEvent(type=MASTER_EVENT_ERROR, content=f"llm error: {exc}")
                return

            # Prompt mode: extract single tool call from text_accum if present.
            if self._tool_mode == "prompt" and not tool_calls and text_accum:
                parsed = parse_tool_call(text_accum)
                if parsed is not None:
                    tool_calls = [parsed]
                    # Strip the JSON from text_accum so we don't echo it back
                    # as both a tool call and a thinking blob.
                    text_accum = ""

            assistant_msg = ChatMessage(
                role="assistant",
                content=text_accum,
                tool_calls=tuple(tool_calls) if tool_calls else None,
            )
            conversation.append(
                "assistant",
                {
                    "text": text_accum,
                    "tool_calls": [
                        {"id": call.id, "name": call.name, "arguments": dict(call.arguments)}
                        for call in tool_calls
                    ],
                },
            )
            # Keep the llm-shaped copy too (used to rebuild messages on next hop).
            conversation.messages[-1]["_llm"] = _serialize_assistant(assistant_msg)

            # Persist after the assistant turn is written so a crash
            # mid-tool-loop doesn't lose the model's reasoning.
            await self._store.save(conversation.id)

            if not tool_calls:
                # No tool calls — treat as direct final text (model forgot to use report_to_user).
                final_text = text_accum.strip() or "(no reply)"
                if self._tool_mode == "prompt" and looks_like_tool_call_attempt(final_text):
                    # Looked like the model was trying to call a tool but the
                    # JSON was malformed — surface as error so the user knows.
                    yield MasterEvent(
                        type=MASTER_EVENT_ERROR,
                        content=f"could not parse tool call: {final_text[:300]}",
                    )
                    return
                yield MasterEvent(type=MASTER_EVENT_FINAL_TEXT, content={"text": final_text})
                return

            done = False
            for call in tool_calls:
                yield MasterEvent(
                    type=MASTER_EVENT_TOOL_CALL_BEGIN,
                    content={"id": call.id, "name": call.name, "arguments": dict(call.arguments)},
                )
                ctx = ToolContext(
                    conversation_id=conversation.id,
                    arguments=call.arguments,
                    services=services,
                    default_cwd=default_cwd,
                )
                result = await self._orchestrator.invoke(call.name, ctx)
                yield MasterEvent(
                    type=MASTER_EVENT_TOOL_CALL_END,
                    content={
                        "id": call.id,
                        "name": call.name,
                        "ok": result.ok,
                        "output_text": result.output_text,
                        "data": dict(result.data),
                        "error": result.error,
                    },
                )
                tool_message_payload = _tool_result_to_message_content(result)
                conversation.append(
                    "tool",
                    {
                        "tool_use_id": call.id,
                        "name": call.name,
                        "content": tool_message_payload,
                        "_llm": {
                            "role": "tool",
                            "tool_use_id": call.id,
                            "content": tool_message_payload,
                        },
                    },
                )
                if call.name == REPORT_TOOL_NAME and result.ok:
                    final = str(result.data.get("final_text") or result.output_text)
                    yield MasterEvent(type=MASTER_EVENT_FINAL_TEXT, content={"text": final})
                    done = True
                    break
            # Persist after tool results so we don't lose them on crash.
            await self._store.save(conversation.id)
            if done:
                return
            if abort_event.is_set():
                yield MasterEvent(type=MASTER_EVENT_ERROR, content="aborted by user")
                return

        yield MasterEvent(
            type=MASTER_EVENT_HOP_LIMIT,
            content=f"reached max hop limit ({_MAX_HOPS})",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _conversation_to_llm_messages(conversation: MasterAgentConversation) -> list[ChatMessage]:
    """Rebuild the LLM-shaped message list from the stored conversation log.

    The store keeps both a human-readable record and an ``_llm`` shadow
    payload on each assistant/tool entry. We materialize ChatMessage
    objects from the shadow when available so tool_use ids round-trip
    correctly.
    """

    out: list[ChatMessage] = []
    for entry in conversation.messages:
        role = entry.get("role")
        content = entry.get("content")
        if role == "user":
            out.append(ChatMessage(role="user", content=str(content) if isinstance(content, str) else content))
            continue
        # ``_llm`` shadow can live at the entry level (assistant turn —
        # populated in :meth:`_hop_loop` via ``messages[-1]["_llm"]``) or
        # inside the content dict (tool turn — appended directly with
        # the shadow). Check both spots.
        llm_payload = entry.get("_llm")
        if llm_payload is None and isinstance(content, dict):
            llm_payload = content.get("_llm")
        if role == "assistant":
            if llm_payload:
                tool_calls_raw = llm_payload.get("tool_calls") or []
                tool_calls = tuple(
                    ToolCall(
                        id=str(item.get("id") or ""),
                        name=str(item.get("name") or ""),
                        arguments=dict(item.get("arguments") or {}),
                    )
                    for item in tool_calls_raw
                )
                out.append(ChatMessage(
                    role="assistant",
                    content=str(llm_payload.get("text") or ""),
                    tool_calls=tool_calls or None,
                ))
            else:
                out.append(ChatMessage(role="assistant", content=str(content or "")))
            continue
        if role == "tool":
            if llm_payload:
                out.append(ChatMessage(
                    role="tool",
                    content=llm_payload.get("content"),
                    tool_use_id=str(llm_payload.get("tool_use_id") or ""),
                ))
            else:
                out.append(ChatMessage(role="tool", content=content))
            continue
    return out


def _serialize_assistant(msg: ChatMessage) -> dict[str, Any]:
    return {
        "role": "assistant",
        "text": str(msg.content or ""),
        "tool_calls": [
            {"id": c.id, "name": c.name, "arguments": dict(c.arguments)}
            for c in (msg.tool_calls or ())
        ],
    }


def _tool_result_to_message_content(result: Any) -> str:
    payload: dict[str, Any] = {
        "ok": result.ok,
        "output_text": result.output_text,
    }
    if result.data:
        payload["data"] = dict(result.data)
    if result.error:
        payload["error"] = result.error
    try:
        return json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(payload)
