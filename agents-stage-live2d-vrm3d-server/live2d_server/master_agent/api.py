"""FastAPI router for the master agent.

Endpoints (all prefixed with ``/api/master-agent``):

- ``POST /conversation/new`` — create a new master-agent conversation.
- ``POST /chat`` — SSE stream of master events.
- ``POST /abort`` — abort the in-flight LLM loop (subtasks must be
  aborted separately via the abort_session tool).
- ``GET  /snapshot`` — main conversation + subtask snapshot.
- ``GET  /subtasks`` — subtask list per conversation.
- ``GET  /subtasks/{id}`` — one subtask.
- ``GET  /llm/info`` — describe the env-configured LLM.

The active singleton is lazily constructed on first chat call so that
modules importing this router don't pay the cost of building the LLM
client (which requires env vars) at import time.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from pathlib import Path

from pydantic import BaseModel, Field

from .broadcaster import MasterAgentBroadcaster, subtask_event
from .conversation_store import FileConversationStore
from .llm.factory import build_chat_model, describe_active_llm, resolve_tool_mode
from .persona import DEFAULT_DISPLAY_NAME, FilePersonaStore, PersonaConfig
from .persona_presets import get_preset, list_presets
from .service import MasterAgentService
from .shared import (
    MasterAgentAbortRequest,
    MasterAgentChatRequest,
    MasterAgentNewConversationRequest,
    SubTask,
)
from .task_tracker import SubTaskTracker
from .telegram import get_telegram_runtime
from .tool_registry import InMemoryToolRegistry
from .tools import (
    AbortSessionTool,
    ApprovePendingTool,
    BrowseDirectoriesTool,
    ClaudeNewSessionTool,
    ClaudeSendPromptTool,
    CodexNewSessionTool,
    CodexSendPromptTool,
    GetSessionConversationTool,
    ListAvailableModelsTool,
    ListBranchesTool,
    ListHistorySessionsTool,
    ListSessionsTool,
    ListSubTasksTool,
    QuerySessionStatusTool,
    ReportToUserTool,
    SearchSessionsTool,
    SwitchBranchTool,
    WaitForSubTaskTool,
)

_LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api/master-agent", tags=["master-agent"])

_service_lock = asyncio.Lock()
_service_instance: Optional[MasterAgentService] = None
_broadcaster = MasterAgentBroadcaster()


async def _on_subtask_change(kind: str, subtask: SubTask) -> None:
    """Tracker hook → forward state changes to all WS subscribers."""
    await _broadcaster.publish(subtask_event(kind, subtask.to_dict()))


def _build_default_registry() -> InMemoryToolRegistry:
    registry = InMemoryToolRegistry()
    # Session lifecycle.
    registry.register(CodexNewSessionTool())
    registry.register(CodexSendPromptTool())
    registry.register(ClaudeNewSessionTool())
    registry.register(ClaudeSendPromptTool())
    # Query / introspection.
    registry.register(QuerySessionStatusTool())
    registry.register(ListSessionsTool())
    registry.register(ListSubTasksTool())
    registry.register(ListHistorySessionsTool())
    registry.register(GetSessionConversationTool())
    registry.register(SearchSessionsTool())
    registry.register(ListAvailableModelsTool())
    # Wait + control.
    registry.register(WaitForSubTaskTool())
    registry.register(AbortSessionTool())
    registry.register(ApprovePendingTool())
    # Filesystem discovery.
    registry.register(BrowseDirectoriesTool())
    # Git.
    registry.register(ListBranchesTool())
    registry.register(SwitchBranchTool())
    # Terminator.
    registry.register(ReportToUserTool())
    return registry


async def _get_service() -> MasterAgentService:
    global _service_instance
    if _service_instance is not None:
        return _service_instance
    async with _service_lock:
        if _service_instance is not None:
            return _service_instance
        # Reuse the existing session-bridge singletons so codex/claude
        # subprocesses are managed by the same service that powers the
        # legacy /api/session-bridge routes.
        from ..session_bridge_api import agent_provider, bridge_service

        chat_model = build_chat_model()
        tracker = SubTaskTracker(state_change_hook=_on_subtask_change)
        conversation_store = FileConversationStore()
        persona_store = FilePersonaStore(_resolve_persona_path())
        _service_instance = MasterAgentService(
            chat_model=chat_model,
            agent_provider=agent_provider,
            bridge_service=bridge_service,
            tool_registry=_build_default_registry(),
            tool_mode=resolve_tool_mode(),
            task_tracker=tracker,
            conversation_store=conversation_store,
            persona_store=persona_store,
        )
        return _service_instance


def _resolve_persona_path() -> Path:
    """Persona file sits next to conversations under <repo>/config/master-agent.

    Walk up four levels: api.py → master_agent → live2d_server → server-pkg
    → repo root. Matches the same convention used by
    :class:`FileConversationStore`.
    """
    return (
        Path(__file__).resolve().parents[3]
        / "config" / "master-agent" / "persona.json"
    )


def configure_service(service: MasterAgentService) -> None:
    """Inject a service instance (used by tests)."""
    global _service_instance
    _service_instance = service


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/conversation/new")
async def conversation_new(_: MasterAgentNewConversationRequest) -> dict[str, str]:
    service = await _get_service()
    conversation = await service.new_conversation()
    return {"conversation_id": conversation.id}


@router.post("/chat")
async def chat(request: MasterAgentChatRequest) -> StreamingResponse:
    service = await _get_service()

    async def event_stream():
        try:
            async for event in service.run_stream(
                conversation_id=request.conversation_id,
                message=request.message,
                default_cwd=request.default_cwd,
                permit_full_access=request.permit_full_access,
            ):
                payload = json.dumps(event.to_dict(), ensure_ascii=False)
                yield f"data: {payload}\n\n"
        except Exception as exc:  # noqa: BLE001 — surface to client
            _LOGGER.exception("master agent stream crashed")
            err = json.dumps({"type": "error", "content": f"server error: {exc}"}, ensure_ascii=False)
            yield f"data: {err}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/abort")
async def abort(request: MasterAgentAbortRequest) -> dict[str, bool]:
    service = await _get_service()
    aborted = await service.abort(request.conversation_id)
    return {"aborted": aborted}


@router.get("/snapshot")
async def snapshot(conversation_id: str) -> dict[str, object]:
    service = await _get_service()
    conversation = await service.conversation_store.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    subtasks = await service.task_tracker.list_for_conversation(conversation_id)
    return {
        "conversation": {
            "id": conversation.id,
            "messages": conversation.messages,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
        },
        "subtasks": [t.to_dict() for t in subtasks],
    }


@router.get("/subtasks")
async def subtasks(conversation_id: str) -> dict[str, object]:
    service = await _get_service()
    items = await service.task_tracker.list_for_conversation(conversation_id)
    return {"subtasks": [t.to_dict() for t in items]}


@router.get("/subtasks/{subtask_id}")
async def subtask_detail(subtask_id: str) -> dict[str, object]:
    service = await _get_service()
    task = await service.task_tracker.get(subtask_id)
    if task is None:
        raise HTTPException(status_code=404, detail="subtask not found")
    return task.to_dict()


@router.get("/llm/info")
async def llm_info() -> dict[str, str]:
    return describe_active_llm()


# ---------------------------------------------------------------------------
# Persona endpoints
# ---------------------------------------------------------------------------


class PersonaUpdateRequest(BaseModel):
    enabled: bool = True
    display_name: str = Field(default=DEFAULT_DISPLAY_NAME, max_length=80)
    summary: str = Field(default="", max_length=600)
    personality: list[str] = Field(default_factory=list, max_length=20)
    speaking_style: str = Field(default="", max_length=1200)
    catchphrase: str = Field(default="", max_length=200)
    boundaries: list[str] = Field(default_factory=list, max_length=20)


@router.get("/persona")
async def persona_get() -> dict[str, object]:
    service = await _get_service()
    store = service.persona_store
    if store is None:
        # Should not happen in production wiring, but keep the API
        # honest if a test injects a service without a persona store.
        return {"persona": None, "presets": list_presets()}
    persona = await store.get()
    return {"persona": persona.to_dict(), "presets": list_presets()}


@router.put("/persona")
async def persona_update(request: PersonaUpdateRequest) -> dict[str, object]:
    service = await _get_service()
    store = service.persona_store
    if store is None:
        raise HTTPException(status_code=503, detail="persona store not initialized")
    updated = await store.set(PersonaConfig(
        enabled=request.enabled,
        display_name=request.display_name,
        summary=request.summary,
        personality=list(request.personality),
        speaking_style=request.speaking_style,
        catchphrase=request.catchphrase,
        boundaries=list(request.boundaries),
    ))
    return {"persona": updated.to_dict()}


@router.post("/persona/reset")
async def persona_reset() -> dict[str, object]:
    service = await _get_service()
    store = service.persona_store
    if store is None:
        raise HTTPException(status_code=503, detail="persona store not initialized")
    persona = await store.reset_to_default()
    return {"persona": persona.to_dict()}


@router.post("/persona/apply-preset")
async def persona_apply_preset(payload: dict[str, str]) -> dict[str, object]:
    """Apply a built-in preset and save it as the current persona.

    The preset id comes from :func:`list_presets`; unknown ids 404.
    Unlike ``GET /persona`` (which returns the preset *list*), this
    endpoint commits the preset to the store so the next chat hop
    picks it up.
    """
    preset_id = (payload.get("preset_id") or "").strip()
    if not preset_id:
        raise HTTPException(status_code=400, detail="preset_id is required")
    preset = get_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"preset {preset_id} not found")
    service = await _get_service()
    store = service.persona_store
    if store is None:
        raise HTTPException(status_code=503, detail="persona store not initialized")
    applied = await store.set(preset)
    return {"persona": applied.to_dict(), "preset_id": preset_id}


# ---------------------------------------------------------------------------
# Telegram binding endpoints
# ---------------------------------------------------------------------------


@router.get("/telegram/status")
async def telegram_status() -> dict[str, object]:
    """Report whether the TG bot is configured and how many chats are bound."""
    runtime = get_telegram_runtime()
    cfg = runtime.config()
    binding_count = 0
    if cfg.is_enabled():
        binding_count = await runtime.store().binding_count()
    return {
        "enabled": cfg.is_enabled(),
        "running": runtime.is_running(),
        "bot_username": cfg.bot_username,
        "binding_count": binding_count,
        "binding_code_ttl_seconds": cfg.binding_code_ttl_seconds,
    }


@router.post("/telegram/binding-code")
async def telegram_issue_binding_code() -> dict[str, object]:
    """Mint a one-shot binding code for the requesting user to type in TG."""
    runtime = get_telegram_runtime()
    cfg = runtime.config()
    if not cfg.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="Telegram bot 未啟用，請設定 TELEGRAM_BOT_TOKEN",
        )
    code, expires_at = await runtime.store().issue_code()
    return {
        "code": code,
        "expires_at": expires_at,
        "ttl_seconds": cfg.binding_code_ttl_seconds,
        "bot_username": cfg.bot_username,
    }


@router.websocket("/ws")
async def master_agent_ws(websocket: WebSocket) -> None:
    """Broadcast channel for SubTask state changes + master events.

    All connected clients receive every event; the frontend filters by
    ``conversation_id`` to render only its own conversation's subtasks.
    Multi-tab / desktop-widget viewers stay in sync without polling.
    """
    # Match the session-bridge auth pattern for remote mode.
    app = websocket.app
    if getattr(app.state, "mode", "local") == "remote":
        from ..auth import verify_ws_auth
        if not await verify_ws_auth(websocket, app.state.remote_config):
            return
    await websocket.accept()
    await _broadcaster.register(websocket)
    try:
        # Ensure the service exists so the tracker hook is installed;
        # otherwise the first chat call would be the first publisher.
        await _get_service()
    except Exception:  # noqa: BLE001 — initial WS connection shouldn't fail open
        _LOGGER.exception("master agent service init failed during WS connect")
    try:
        while True:
            # Keep the socket open. Master agent is broadcast-only; we
            # don't accept client commands here. ``receive_text`` raises
            # on disconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        _LOGGER.exception("master agent ws errored")
    finally:
        await _broadcaster.unregister(websocket)
