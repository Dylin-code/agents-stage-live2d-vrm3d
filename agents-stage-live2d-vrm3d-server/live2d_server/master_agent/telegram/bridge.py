"""Convert master-agent SSE events into Telegram messages.

The master agent emits a stream of fine-grained events (thinking
deltas, tool_call begin/end, final_text, errors). TG only does discrete
messages, so we fold the stream:

- ``thinking_delta`` → suppressed (way too chatty, and TG would
  rate-limit us anyway). A single ``typing`` action per LLM hop signals
  liveness instead.
- ``tool_call_begin`` → post ``🔧 <label>...`` and remember the message id.
- ``tool_call_end``  → edit that message to ``✅ <label>`` or
  ``❌ <label> — <error>``.
- ``final_text``     → send the full reply, chunked at TG's 4096 char limit.
- ``error`` / ``hop_limit_reached`` → send as a single notice message.

The :class:`TelegramMessenger` Protocol keeps :class:`EventBridge`
testable — we inject a fake messenger in unit tests and assert on the
recorded calls.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Optional, Protocol

from ..shared import (
    MASTER_EVENT_ERROR,
    MASTER_EVENT_FINAL_TEXT,
    MASTER_EVENT_HOP_LIMIT,
    MASTER_EVENT_THINKING_DELTA,
    MASTER_EVENT_TOOL_CALL_BEGIN,
    MASTER_EVENT_TOOL_CALL_END,
    MasterEvent,
)

_LOGGER = logging.getLogger(__name__)

# TG hard limit is 4096; leave headroom for the leading emoji prefix
# we add when chunking.
_MAX_MESSAGE_LEN = 3900
_TOOL_OUTPUT_PREVIEW_LEN = 300

_TOOL_LABELS: dict[str, str] = {
    "codex_new_session": "啟動 Codex 子任務",
    "codex_send_prompt": "Codex 發送指令",
    "claude_new_session": "啟動 Claude 子任務",
    "claude_send_prompt": "Claude 發送指令",
    "wait_for_subtask": "等待子任務完成",
    "abort_session": "中止子任務",
    "approve_pending": "核准待處理項目",
    "query_session_status": "查詢子任務狀態",
    "list_sessions": "列出進行中子任務",
    "list_subtasks": "列出對話子任務",
    "list_history_sessions": "列出歷史子任務",
    "list_available_models": "列出可用模型",
    "get_session_conversation": "讀取子任務對話",
    "search_sessions": "搜尋子任務",
    "browse_directories": "瀏覽目錄",
    "list_branches": "列出 git 分支",
    "switch_branch": "切換 git 分支",
    "tui_new_session": "啟動 TUI 終端",
    "tui_send_input": "輸入 TUI 文字",
    "tui_send_key": "送出 TUI 按鍵",
    "tui_capture_screen": "擷取 TUI 畫面",
    "tui_wait_for_stable": "等待 TUI 穩定",
    "report_to_user": "回報結果",
}


class TelegramMessenger(Protocol):
    """Minimal surface the bridge needs from the TG client.

    Implemented in production by a thin wrapper around ``Application.bot``
    bound to a specific ``chat_id``. Tests pass in a fake recorder.
    """

    async def send_text(self, text: str) -> int:
        """Send ``text`` to the bound chat. Return the new message id."""

    async def edit_text(self, message_id: int, text: str) -> None:
        """Replace the body of ``message_id`` in the bound chat."""

    async def send_typing(self) -> None:
        """Best-effort typing indicator. May silently no-op."""


class EventBridge:
    """Drive a :class:`TelegramMessenger` from a ``MasterEvent`` async iterator."""

    def __init__(self, messenger: TelegramMessenger) -> None:
        self._messenger = messenger
        # tool_use_id → message_id we sent for that call's "begin" line
        self._tool_messages: dict[str, int] = {}

    async def consume(self, events: AsyncIterator[MasterEvent]) -> None:
        try:
            await self._messenger.send_typing()
        except Exception:  # noqa: BLE001 — typing is best-effort
            pass
        async for event in events:
            try:
                await self._dispatch(event)
            except Exception:  # noqa: BLE001 — one bad event must not kill the stream
                _LOGGER.exception("telegram bridge dispatch failed for %s", event.type)

    async def _dispatch(self, event: MasterEvent) -> None:
        kind = event.type
        if kind == MASTER_EVENT_THINKING_DELTA:
            return  # suppress — see module docstring.
        if kind == MASTER_EVENT_TOOL_CALL_BEGIN:
            await self._on_tool_begin(_as_dict(event.content))
            return
        if kind == MASTER_EVENT_TOOL_CALL_END:
            await self._on_tool_end(_as_dict(event.content))
            return
        if kind == MASTER_EVENT_FINAL_TEXT:
            text = str(_as_dict(event.content).get("text") or "").strip()
            if text:
                await self._send_chunked(text)
            return
        if kind == MASTER_EVENT_ERROR:
            text = _stringify(event.content)
            await self._messenger.send_text(f"❌ 錯誤: {text}")
            return
        if kind == MASTER_EVENT_HOP_LIMIT:
            text = _stringify(event.content)
            await self._messenger.send_text(f"⚠️ 超過跳轉上限: {text}")
            return

    async def _on_tool_begin(self, payload: dict[str, Any]) -> None:
        call_id = str(payload.get("id") or "")
        name = str(payload.get("name") or "?")
        label = _TOOL_LABELS.get(name, name)
        msg_id = await self._messenger.send_text(f"🔧 {label}…")
        if call_id:
            self._tool_messages[call_id] = msg_id

    async def _on_tool_end(self, payload: dict[str, Any]) -> None:
        call_id = str(payload.get("id") or "")
        name = str(payload.get("name") or "?")
        ok = bool(payload.get("ok"))
        label = _TOOL_LABELS.get(name, name)
        output = _truncate(_stringify(payload.get("output_text") or ""), _TOOL_OUTPUT_PREVIEW_LEN)
        error = _truncate(_stringify(payload.get("error") or ""), _TOOL_OUTPUT_PREVIEW_LEN)

        if ok:
            body = f"✅ {label}"
            if output:
                body += f"\n{output}"
        else:
            body = f"❌ {label}"
            if error:
                body += f"\n{error}"
            elif output:
                body += f"\n{output}"

        msg_id = self._tool_messages.pop(call_id, None) if call_id else None
        if msg_id is not None:
            try:
                await self._messenger.edit_text(msg_id, body)
                return
            except Exception:  # noqa: BLE001 — fall back to a fresh send
                _LOGGER.debug("edit_text failed; sending as new message", exc_info=True)
        await self._messenger.send_text(body)

    async def _send_chunked(self, text: str) -> None:
        for chunk in _chunk(text, _MAX_MESSAGE_LEN):
            await self._messenger.send_text(chunk)


# ---------------------------------------------------------------------------
# Helpers (pure, easy to unit test if needed)
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001
        return ""


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _chunk(text: str, size: int) -> list[str]:
    """Split ``text`` into TG-friendly chunks at line boundaries when possible."""
    if len(text) <= size:
        return [text]
    out: list[str] = []
    remaining = text
    while len(remaining) > size:
        cut = remaining.rfind("\n", 0, size)
        if cut < int(size * 0.5):
            # No reasonable newline near the boundary; hard cut.
            cut = size
        out.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        out.append(remaining)
    return out


__all__ = ["EventBridge", "TelegramMessenger"]
