import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from .session_bridge_chat import CodexSessionChatError, CodexSessionChatService
from .session_bridge_claude_chat import ClaudeSessionChatError
from .session_bridge_provider import AgentProviderRouter
from .session_bridge_runtime import SessionBridgeService
from .session_bridge_shared import (
    AGENT_BRAND_CLAUDE,
    AGENT_BRAND_CODEX,
    AgentChatApprovalRequest,
    AgentChatRequest,
    AgentConversationRequest,
    AgentNewSessionRequest,
    PERMISSION_MODE_DEFAULT,
    SESSION_STATES,
    GitBranchSwitchRequest,
    _SessionRecord,
    _resolve_permission_mode,
    _resolve_permission_settings,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/session-bridge", tags=["session-bridge"])

bridge_service = SessionBridgeService()
codex_chat_service = CodexSessionChatService()
agent_provider = AgentProviderRouter()


@router.get("/health")
async def bridge_health() -> dict[str, Any]:
    return await bridge_service.get_health()


@router.get("/snapshot")
async def bridge_snapshot() -> dict[str, Any]:
    return await bridge_service.get_snapshot()


@router.get("/history")
async def bridge_history(limit: int = 20) -> dict[str, Any]:
    return await bridge_service.get_history(limit)


@router.get("/conversation/{session_id}")
async def bridge_conversation(session_id: str, request: AgentConversationRequest = Depends()) -> dict[str, Any]:
    return await bridge_service.get_conversation(session_id=session_id, limit=request.limit)


def _run_git_command(cwd: str, args: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", cwd, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _resolve_session_cwd(record: Optional[_SessionRecord], cwd_override: Optional[str]) -> str:
    if cwd_override and cwd_override.strip():
        return str(Path(cwd_override).expanduser())
    if record and record.cwd:
        return record.cwd
    return codex_chat_service.default_cwd


def _resolve_runtime_value(request_value: Optional[str], record_value: str, default_value: str = "") -> str:
    if request_value is not None and str(request_value).strip():
        return str(request_value).strip()
    if record_value:
        return record_value
    return default_value


def _safe_non_negative_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:
            return 0
        return max(0, int(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return max(0, int(float(text)))
        except ValueError:
            return 0
    return 0


def _read_history_runtime_snapshot(session_id: str) -> dict[str, Any]:
    session_key = (session_id or "").strip()
    if not session_key:
        return {}
    history = bridge_service._collect_history_from_files()
    item = history.get(session_key)
    if not isinstance(item, dict):
        return {}
    context = item.get("context") if isinstance(item.get("context"), dict) else {}
    return {
        "cwd": str(item.get("cwd") or ""),
        "branch": str(item.get("branch") or ""),
        "model": str(context.get("model") or ""),
        "effort": str(context.get("effort") or ""),
        "permission_mode": _resolve_permission_mode(
            context.get("permission_mode"),
            approval_policy=context.get("approval_policy"),
            sandbox_mode=context.get("sandbox_mode"),
        ),
        "approval_policy": str(context.get("approval_policy") or ""),
        "sandbox_mode": str(context.get("sandbox_mode") or ""),
        "plan_mode": context.get("plan_mode") if isinstance(context.get("plan_mode"), bool) else None,
        "plan_mode_fallback": context.get("plan_mode_fallback") if isinstance(context.get("plan_mode_fallback"), bool) else None,
        "total_tokens": _safe_non_negative_int(context.get("total_tokens")),
        "model_context_window": _safe_non_negative_int(context.get("model_context_window")),
        "primary_rate_remaining_percent": context.get("primary_rate_remaining_percent"),
        "secondary_rate_remaining_percent": context.get("secondary_rate_remaining_percent"),
    }


async def _ensure_session_record(session_id: str) -> Optional[_SessionRecord]:
    session_key = (session_id or "").strip()
    if not session_key:
        return None
    existing = await bridge_service.get_session_record(session_key)
    if existing is not None:
        return existing

    history = bridge_service._collect_history_from_files()
    item = history.get(session_key)
    if not item:
        return None
    context = item.get("context") if isinstance(item.get("context"), dict) else {}
    await bridge_service.upsert_runtime_context(
        session_key,
        cwd=str(item.get("cwd") or ""),
        branch=str(item.get("branch") or ""),
        model=str(context.get("model") or ""),
        effort=str(context.get("effort") or ""),
        permission_mode=_resolve_permission_mode(
            context.get("permission_mode"),
            approval_policy=context.get("approval_policy"),
            sandbox_mode=context.get("sandbox_mode"),
        ),
        approval_policy=str(context.get("approval_policy") or ""),
        sandbox_mode=str(context.get("sandbox_mode") or ""),
        plan_mode=context.get("plan_mode") if isinstance(context.get("plan_mode"), bool) else None,
        plan_mode_fallback=context.get("plan_mode_fallback") if isinstance(context.get("plan_mode_fallback"), bool) else None,
        total_tokens=_safe_non_negative_int(context.get("total_tokens")),
        model_context_window=_safe_non_negative_int(context.get("model_context_window")),
        primary_rate_remaining_percent=context.get("primary_rate_remaining_percent")
        if isinstance(context.get("primary_rate_remaining_percent"), (int, float))
        else None,
        secondary_rate_remaining_percent=context.get("secondary_rate_remaining_percent")
        if isinstance(context.get("secondary_rate_remaining_percent"), (int, float))
        else None,
    )
    async with bridge_service._sessions_lock:
        session = bridge_service._sessions.get(session_key)
        if session is not None:
            session.display_name = str(item.get("display_name") or session.display_name)
            state = str(item.get("state") or "").strip().upper()
            if state in SESSION_STATES:
                session.state = state
            session.last_seen_at = str(item.get("last_seen_at") or session.last_seen_at)
            session.last_seen_epoch = float(item.get("last_seen_epoch") or session.last_seen_epoch)
            session.originator = str(item.get("originator") or session.originator)
            session.last_event_type = str(item.get("last_event_type") or session.last_event_type)
            session.active = True
    return await bridge_service.get_session_record(session_key)


async def _bridge_chat_with_service(
    request: AgentChatRequest,
    *,
    session: Optional[_SessionRecord],
    brand: str,
    chat_service: Any,
) -> StreamingResponse:
    effective_cwd = _resolve_session_cwd_for_brand(session, request.cwd_override, brand)
    effective_model = _resolve_runtime_value(request.model, session.model if session else "")
    effective_effort = _resolve_runtime_value(request.reasoning_effort, session.effort if session else "")
    requested_mode = request.permission_mode if request.permission_mode is not None and str(request.permission_mode).strip() else None
    session_mode = session.permission_mode if (session and str(session.permission_mode or "").strip()) else None
    mode_source = requested_mode if requested_mode is not None else session_mode
    if requested_mode is not None:
        effective_permission_mode, effective_approval_policy, effective_sandbox_mode = _resolve_permission_settings(
            permission_mode=mode_source,
            approval_policy=None,
            sandbox_mode=None,
        )
    elif mode_source is not None:
        effective_permission_mode, effective_approval_policy, effective_sandbox_mode = _resolve_permission_settings(
            permission_mode=mode_source,
            approval_policy=None,
            sandbox_mode=None,
        )
    else:
        effective_permission_mode, effective_approval_policy, effective_sandbox_mode = _resolve_permission_settings(
            permission_mode=None,
            approval_policy=request.approval_policy if request.approval_policy is not None else (session.approval_policy if session else None),
            sandbox_mode=request.sandbox_mode if request.sandbox_mode is not None else (session.sandbox_mode if session else None),
        )
    effective_plan_mode = request.plan_mode if request.plan_mode is not None else (session.plan_mode if session else None)
    logger.info(
        "bridge_chat brand=%s session=%s req.mode=%s req.approval=%s req.sandbox=%s eff.mode=%s",
        brand,
        request.session_id,
        request.permission_mode,
        request.approval_policy,
        request.sandbox_mode,
        effective_permission_mode,
    )

    if request.git_branch:
        completed = _run_git_command(effective_cwd, ["switch", request.git_branch], timeout=20)
        if completed.returncode != 0:
            fallback = _run_git_command(effective_cwd, ["checkout", request.git_branch], timeout=20)
            if fallback.returncode != 0:
                raise CodexSessionChatError(
                    f"failed to switch branch: {(fallback.stderr or completed.stderr or '').strip()}"
                )

    async def process():
        try:
            runtime_context: dict[str, Any] = {}
            async for event in chat_service.stream_prompt(
                session_id=request.session_id,
                prompt=request.message,
                cwd=effective_cwd,
                images=request.images,
                model=effective_model or None,
                reasoning_effort=effective_effort or None,
                permission_mode=effective_permission_mode,
                approval_policy=effective_approval_policy or None,
                sandbox_mode=effective_sandbox_mode or None,
                plan_mode=effective_plan_mode,
            ):
                if event.get("type") == "context" and isinstance(event.get("content"), dict):
                    # Merge (not overwrite) so earlier fields (model_context_window)
                    # survive when later events add total_tokens.
                    runtime_context.update(event.get("content") or {})
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            branch = ""
            try:
                branch_result = _run_git_command(effective_cwd, ["branch", "--show-current"], timeout=5)
                if branch_result.returncode == 0:
                    branch = (branch_result.stdout or "").strip()
            except Exception:
                branch = ""

            history_runtime = _read_history_runtime_snapshot(request.session_id)
            runtime_cwd = str(
                history_runtime.get("cwd")
                or runtime_context.get("cwd")
                or effective_cwd
                or ""
            )
            runtime_model = str(
                history_runtime.get("model")
                or runtime_context.get("model")
                or effective_model
                or ""
            )
            runtime_effort = str(
                history_runtime.get("effort")
                or runtime_context.get("effort")
                or effective_effort
                or ""
            )
            if requested_mode is not None:
                runtime_permission_mode = effective_permission_mode
                _, runtime_approval, runtime_sandbox = _resolve_permission_settings(
                    permission_mode=runtime_permission_mode,
                    approval_policy=None,
                    sandbox_mode=None,
                )
            else:
                runtime_approval = str(
                    history_runtime.get("approval_policy")
                    or runtime_context.get("approval_policy")
                    or effective_approval_policy
                    or ""
                )
                runtime_sandbox = str(
                    history_runtime.get("sandbox_mode")
                    or runtime_context.get("sandbox_mode")
                    or effective_sandbox_mode
                    or ""
                )
                runtime_permission_mode = _resolve_permission_mode(
                    history_runtime.get("permission_mode") or runtime_context.get("permission_mode") or effective_permission_mode,
                    approval_policy=runtime_approval,
                    sandbox_mode=runtime_sandbox,
                )
            runtime_plan_mode = (
                history_runtime.get("plan_mode")
                if isinstance(history_runtime.get("plan_mode"), bool)
                else (
                    bool(runtime_context.get("plan_mode"))
                    if isinstance(runtime_context.get("plan_mode"), bool)
                    else effective_plan_mode
                )
            )

            # Extract token data from accumulated runtime_context.
            runtime_total_tokens = None
            runtime_model_context_window = None
            raw_tt = runtime_context.get("total_tokens")
            if raw_tt is not None:
                try:
                    runtime_total_tokens = max(0, int(raw_tt))
                except (ValueError, TypeError):
                    pass
            raw_mcw = runtime_context.get("model_context_window")
            if raw_mcw is not None:
                try:
                    runtime_model_context_window = max(0, int(raw_mcw))
                except (ValueError, TypeError):
                    pass

            await bridge_service.upsert_runtime_context(
                request.session_id,
                cwd=runtime_cwd,
                branch=branch or None,
                model=runtime_model or None,
                effort=runtime_effort or None,
                permission_mode=runtime_permission_mode,
                approval_policy=runtime_approval or None,
                sandbox_mode=runtime_sandbox or None,
                plan_mode=runtime_plan_mode,
                total_tokens=runtime_total_tokens,
                model_context_window=runtime_model_context_window,
                agent_brand=brand,
            )
            final_ctx: dict[str, Any] = {
                "cwd": runtime_cwd,
                "model": runtime_model,
                "effort": runtime_effort,
                "permission_mode": runtime_permission_mode,
                "approval_policy": runtime_approval,
                "sandbox_mode": runtime_sandbox,
                "plan_mode": runtime_plan_mode,
                "agent_brand": brand,
            }
            if runtime_total_tokens is not None:
                final_ctx["total_tokens"] = runtime_total_tokens
            if runtime_model_context_window is not None:
                final_ctx["model_context_window"] = runtime_model_context_window
            yield f"data: {json.dumps({'type': 'context', 'content': final_ctx}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except (CodexSessionChatError, ClaudeSessionChatError) as exc:
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.exception("bridge chat failed (brand=%s)", brand)
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(process(), media_type="text/event-stream")


@router.post("/codex/chat")
async def bridge_codex_chat(request: AgentChatRequest) -> StreamingResponse:
    session = await _ensure_session_record(request.session_id)
    codex_request = request.model_copy(update={"agent_brand": AGENT_BRAND_CODEX})
    return await _bridge_chat_with_service(
        codex_request,
        session=session,
        brand=AGENT_BRAND_CODEX,
        chat_service=codex_chat_service,
    )


@router.post("/codex/chat/approval")
async def bridge_codex_chat_approval(request: AgentChatApprovalRequest) -> dict[str, Any]:
    ok = await codex_chat_service.submit_approval(
        pending_id=request.pending_id,
        decision=request.decision,
        prefix_rule=request.prefix_rule,
    )
    return {
        "ok": ok,
        "pending_id": request.pending_id,
        "decision": request.decision,
        "agent_brand": AGENT_BRAND_CODEX,
    }


@router.post("/codex/session/new")
async def bridge_codex_new_session(request: AgentNewSessionRequest) -> dict[str, Any]:
    payload = await codex_chat_service.create_session(
        cwd=request.cwd,
        model=request.model,
        reasoning_effort=request.reasoning_effort,
        permission_mode=request.permission_mode,
        approval_policy=request.approval_policy,
        sandbox_mode=request.sandbox_mode,
        plan_mode=request.plan_mode,
    )
    payload["agent_brand"] = AGENT_BRAND_CODEX
    history_runtime = _read_history_runtime_snapshot(str(payload.get("session_id") or ""))
    if history_runtime:
        payload["cwd"] = history_runtime.get("cwd") or payload.get("cwd") or ""
        payload["branch"] = history_runtime.get("branch") or payload.get("branch") or ""
        payload["model"] = history_runtime.get("model") or payload.get("model") or ""
        payload["effort"] = history_runtime.get("effort") or payload.get("effort") or ""
        payload["permission_mode"] = history_runtime.get("permission_mode") or payload.get("permission_mode") or PERMISSION_MODE_DEFAULT
        payload["approval_policy"] = history_runtime.get("approval_policy") or payload.get("approval_policy") or ""
        payload["sandbox_mode"] = history_runtime.get("sandbox_mode") or payload.get("sandbox_mode") or ""
        if isinstance(history_runtime.get("plan_mode"), bool):
            payload["plan_mode"] = bool(history_runtime.get("plan_mode"))
        if isinstance(history_runtime.get("plan_mode_fallback"), bool):
            payload["plan_mode_fallback"] = bool(history_runtime.get("plan_mode_fallback"))
    if request.permission_mode is not None and str(request.permission_mode).strip():
        mode, approval, sandbox = _resolve_permission_settings(
            permission_mode=request.permission_mode,
            approval_policy=None,
            sandbox_mode=None,
        )
        payload["permission_mode"] = mode
        payload["approval_policy"] = approval
        payload["sandbox_mode"] = sandbox
    if not payload.get("permission_mode"):
        payload["permission_mode"] = _resolve_permission_mode(
            request.permission_mode,
            approval_policy=payload.get("approval_policy"),
            sandbox_mode=payload.get("sandbox_mode"),
        )
    await bridge_service.upsert_runtime_context(
        payload["session_id"],
        cwd=payload.get("cwd"),
        branch=payload.get("branch"),
        model=payload.get("model"),
        effort=payload.get("effort"),
        permission_mode=payload.get("permission_mode"),
        approval_policy=payload.get("approval_policy"),
        sandbox_mode=payload.get("sandbox_mode"),
        plan_mode=payload.get("plan_mode"),
        plan_mode_fallback=payload.get("plan_mode_fallback"),
        agent_brand=AGENT_BRAND_CODEX,
    )
    return payload


# ---------------------------------------------------------------------------
# Unified multi-brand endpoints
# ---------------------------------------------------------------------------


def _resolve_session_cwd_for_brand(
    record: Optional[_SessionRecord],
    cwd_override: Optional[str],
    brand: str,
) -> str:
    """Resolve CWD, falling back to the provider-specific default."""
    if cwd_override and cwd_override.strip():
        return str(Path(cwd_override).expanduser())
    if record and record.cwd:
        return record.cwd
    service = agent_provider.get_chat_service(brand)
    return service.default_cwd


def _normalize_agent_brand_or_400(value: Optional[str]) -> str:
    try:
        return AgentProviderRouter.normalize_brand(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/agent/chat")
async def bridge_agent_chat(request: AgentChatRequest) -> StreamingResponse:
    """Unified chat endpoint — routes to Codex or Claude based on agent_brand."""
    session = await _ensure_session_record(request.session_id)
    brand = _normalize_agent_brand_or_400(request.agent_brand or AGENT_BRAND_CODEX)
    if session and session.agent_brand:
        brand = _normalize_agent_brand_or_400(session.agent_brand)
    chat_service = agent_provider.get_chat_service(brand)
    request_for_brand = request.model_copy(update={"agent_brand": brand})
    return await _bridge_chat_with_service(
        request_for_brand,
        session=session,
        brand=brand,
        chat_service=chat_service,
    )


@router.post("/agent/chat/approval")
async def bridge_agent_chat_approval(request: AgentChatApprovalRequest) -> dict[str, Any]:
    """Unified approval endpoint — tries both providers."""
    if request.agent_brand:
        brand = _normalize_agent_brand_or_400(request.agent_brand)
        ok = await agent_provider.get_chat_service(brand).submit_approval(
            pending_id=request.pending_id, decision=request.decision,
            prefix_rule=request.prefix_rule,
        )
        return {"ok": ok, "pending_id": request.pending_id, "decision": request.decision, "agent_brand": brand}

    ok = False
    for brand in AgentProviderRouter.supported_brands():
        ok = await agent_provider.get_chat_service(brand).submit_approval(
            pending_id=request.pending_id, decision=request.decision,
            prefix_rule=request.prefix_rule,
        )
        if ok:
            return {"ok": ok, "pending_id": request.pending_id, "decision": request.decision, "agent_brand": brand}
    return {"ok": ok, "pending_id": request.pending_id, "decision": request.decision}


@router.post("/agent/session/new")
async def bridge_agent_new_session(request: AgentNewSessionRequest) -> dict[str, Any]:
    """Unified new-session endpoint — routes to correct CLI by agent_brand."""
    brand = _normalize_agent_brand_or_400(request.agent_brand or AGENT_BRAND_CODEX)
    chat_service = agent_provider.get_chat_service(brand)

    payload = await chat_service.create_session(
        cwd=request.cwd,
        model=request.model,
        reasoning_effort=request.reasoning_effort,
        permission_mode=request.permission_mode,
        approval_policy=request.approval_policy,
        sandbox_mode=request.sandbox_mode,
        plan_mode=request.plan_mode,
    )
    payload["agent_brand"] = brand

    history_runtime = _read_history_runtime_snapshot(str(payload.get("session_id") or ""))
    if history_runtime:
        payload["cwd"] = history_runtime.get("cwd") or payload.get("cwd") or ""
        payload["branch"] = history_runtime.get("branch") or payload.get("branch") or ""
        payload["model"] = history_runtime.get("model") or payload.get("model") or ""
        payload["effort"] = history_runtime.get("effort") or payload.get("effort") or ""
        payload["permission_mode"] = history_runtime.get("permission_mode") or payload.get("permission_mode") or PERMISSION_MODE_DEFAULT
        payload["approval_policy"] = history_runtime.get("approval_policy") or payload.get("approval_policy") or ""
        payload["sandbox_mode"] = history_runtime.get("sandbox_mode") or payload.get("sandbox_mode") or ""
        if isinstance(history_runtime.get("plan_mode"), bool):
            payload["plan_mode"] = bool(history_runtime.get("plan_mode"))
        if isinstance(history_runtime.get("plan_mode_fallback"), bool):
            payload["plan_mode_fallback"] = bool(history_runtime.get("plan_mode_fallback"))
    if request.permission_mode is not None and str(request.permission_mode).strip():
        mode, approval, sandbox = _resolve_permission_settings(
            permission_mode=request.permission_mode, approval_policy=None, sandbox_mode=None,
        )
        payload["permission_mode"] = mode
        payload["approval_policy"] = approval
        payload["sandbox_mode"] = sandbox
    if not payload.get("permission_mode"):
        payload["permission_mode"] = _resolve_permission_mode(
            request.permission_mode,
            approval_policy=payload.get("approval_policy"),
            sandbox_mode=payload.get("sandbox_mode"),
        )
    await bridge_service.upsert_runtime_context(
        payload["session_id"],
        cwd=payload.get("cwd"), branch=payload.get("branch"),
        model=payload.get("model"), effort=payload.get("effort"),
        permission_mode=payload.get("permission_mode"),
        approval_policy=payload.get("approval_policy"),
        sandbox_mode=payload.get("sandbox_mode"),
        plan_mode=payload.get("plan_mode"),
        plan_mode_fallback=payload.get("plan_mode_fallback"),
        agent_brand=brand,
    )
    return payload


@router.get("/agent/brands")
async def bridge_agent_brands() -> dict[str, Any]:
    """Return supported agent brands and their default models."""
    return {"brands": AgentProviderRouter.brand_catalog()}


@router.get("/git/branches")
async def bridge_git_branches(session_id: Optional[str] = None, cwd: Optional[str] = None) -> dict[str, Any]:
    record = await bridge_service.get_session_record(session_id or "")
    effective_cwd = _resolve_session_cwd(record, cwd)
    if not effective_cwd:
        return {
            "cwd": "",
            "current": "",
            "branches": [],
        }
    listing = _run_git_command(effective_cwd, ["branch", "--format", "%(refname:short)"], timeout=10)
    if listing.returncode != 0:
        raise CodexSessionChatError(f"failed to list branches: {(listing.stderr or '').strip()}")
    current = _run_git_command(effective_cwd, ["branch", "--show-current"], timeout=10)
    current_branch = (current.stdout or "").strip() if current.returncode == 0 else ""
    branches = [
        line.strip()
        for line in (listing.stdout or "").splitlines()
        if line.strip()
    ]
    if session_id:
        await bridge_service.upsert_runtime_context(session_id, cwd=effective_cwd, branch=current_branch or None)
    return {
        "cwd": effective_cwd,
        "current": current_branch,
        "branches": branches,
    }


@router.post("/git/switch")
async def bridge_git_switch(request: GitBranchSwitchRequest) -> dict[str, Any]:
    record = await bridge_service.get_session_record(request.session_id or "")
    effective_cwd = _resolve_session_cwd(record, request.cwd)
    if not effective_cwd:
        raise CodexSessionChatError("cwd is required for git switch")
    switch_cmd = _run_git_command(effective_cwd, ["switch", request.branch], timeout=20)
    if switch_cmd.returncode != 0:
        fallback = _run_git_command(effective_cwd, ["checkout", request.branch], timeout=20)
        if fallback.returncode != 0:
            detail = (fallback.stderr or switch_cmd.stderr or "").strip()
            raise CodexSessionChatError(f"failed to switch branch: {detail}")
    current = _run_git_command(effective_cwd, ["branch", "--show-current"], timeout=10)
    current_branch = (current.stdout or "").strip() if current.returncode == 0 else request.branch
    if request.session_id:
        await bridge_service.upsert_runtime_context(request.session_id, cwd=effective_cwd, branch=current_branch)
    return {
        "cwd": effective_cwd,
        "current": current_branch,
    }


@router.websocket("/ws")
async def bridge_ws(websocket: WebSocket) -> None:
    # Remote mode: verify JWT cookie before accepting
    app = websocket.app
    if getattr(app.state, "mode", "local") == "remote":
        from .auth import verify_ws_auth
        if not await verify_ws_auth(websocket, app.state.remote_config):
            return

    await websocket.accept()
    await bridge_service.register_ws_client(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Session bridge websocket failed")
    finally:
        await bridge_service.unregister_ws_client(websocket)


async def start_session_bridge() -> None:
    await bridge_service.start()


async def stop_session_bridge() -> None:
    await bridge_service.stop()
