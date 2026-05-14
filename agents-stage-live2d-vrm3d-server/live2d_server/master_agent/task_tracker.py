"""In-memory tracker for SubTasks dispatched by the master agent.

Design:
- One dict keyed by ``subtask_id`` holds the SubTask record.
- One asyncio.Event per subtask flips when the task reaches a terminal
  state (done / failed / aborted). ``wait_with_progress`` consumers
  await it.
- One asyncio.Queue per subtask buffers SSE-style progress events
  (forwarded from codex/claude :func:`stream_prompt`). Consumers drain
  it until the terminal event arrives.
- An ``asyncio.Lock`` guards dict mutations.

Per-conversation LRU eviction keeps memory bounded (oldest done/failed
subtasks dropped when count exceeds ``per_conversation_limit``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Optional

from .shared import SubTask, SubTaskStatus

_LOGGER = logging.getLogger(__name__)

_TERMINAL_STATUSES: frozenset[SubTaskStatus] = frozenset(
    {"done", "failed", "aborted", "detached"},
)

# A hook called whenever a SubTask state transition happens. Kept
# optional so tests + the legacy non-WS path can run without wiring
# the broadcaster.
SubTaskHook = Callable[[str, SubTask], Awaitable[None]]


class SubTaskTracker:
    def __init__(
        self,
        per_conversation_limit: int = 50,
        *,
        state_change_hook: Optional[SubTaskHook] = None,
    ) -> None:
        self._per_conversation_limit = per_conversation_limit
        self._lock = asyncio.Lock()
        self._tasks: dict[str, SubTask] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._state_change_hook = state_change_hook

    def set_state_change_hook(self, hook: Optional[SubTaskHook]) -> None:
        self._state_change_hook = hook

    async def _emit_state_change(self, kind: str, subtask: SubTask) -> None:
        hook = self._state_change_hook
        if hook is None:
            return
        try:
            await hook(kind, subtask)
        except Exception:  # noqa: BLE001 — never let a hook break tracking
            _LOGGER.exception("subtask state-change hook failed")

    async def create(self, subtask: SubTask) -> None:
        async with self._lock:
            self._tasks[subtask.id] = subtask
            self._events[subtask.id] = asyncio.Event()
            self._queues[subtask.id] = asyncio.Queue()
            self._evict_old_for_conversation(subtask.conversation_id)
        await self._emit_state_change("created", subtask)

    async def get(self, subtask_id: str) -> Optional[SubTask]:
        async with self._lock:
            return self._tasks.get(subtask_id)

    async def list_for_conversation(
        self,
        conversation_id: str,
        *,
        status: Optional[SubTaskStatus] = None,
    ) -> list[SubTask]:
        async with self._lock:
            items = [
                t for t in self._tasks.values()
                if t.conversation_id == conversation_id
                and (status is None or t.status == status)
            ]
        return sorted(items, key=lambda t: t.created_at)

    async def update_status(
        self,
        subtask_id: str,
        *,
        status: SubTaskStatus,
        last_event_type: str = "",
        final_text: str = "",
        error: str = "",
    ) -> Optional[SubTask]:
        async with self._lock:
            task = self._tasks.get(subtask_id)
            if task is None:
                return None
            task.status = status
            task.updated_at = time.time()
            if last_event_type:
                task.last_event_type = last_event_type
            if final_text:
                task.final_text = final_text
            if error:
                task.error = error
            event = self._events.get(subtask_id)
        if status in _TERMINAL_STATUSES and event is not None:
            event.set()
        await self._emit_state_change("status", task)
        return task

    async def append_event(self, subtask_id: str, event: dict[str, Any]) -> None:
        async with self._lock:
            queue = self._queues.get(subtask_id)
            task = self._tasks.get(subtask_id)
            if task is not None:
                event_type = str(event.get("type") or "")
                if event_type:
                    task.last_event_type = event_type
                    task.updated_at = time.time()
        if queue is not None:
            await queue.put(event)

    async def wait_with_progress(
        self,
        subtask_id: str,
        *,
        timeout_sec: float = 60.0,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield events until terminal status or ``timeout_sec`` elapses.

        Final yield is always a synthetic ``{"type": "subtask_status",
        "content": {...}}`` event reflecting the current SubTask record,
        so the caller can build a tool result without a second lookup.
        """

        async with self._lock:
            queue = self._queues.get(subtask_id)
            event = self._events.get(subtask_id)
            task = self._tasks.get(subtask_id)
        if queue is None or event is None or task is None:
            return

        deadline = time.monotonic() + timeout_sec
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=min(remaining, 1.0))
                yield item
            except asyncio.TimeoutError:
                if event.is_set():
                    break
                continue
            if event.is_set() and queue.empty():
                break

        async with self._lock:
            current = self._tasks.get(subtask_id)
        if current is not None:
            yield {"type": "subtask_status", "content": current.to_dict()}

    def _evict_old_for_conversation(self, conversation_id: str) -> None:
        """Caller holds the lock. Trim oldest terminal subtasks past the limit."""
        items = [t for t in self._tasks.values() if t.conversation_id == conversation_id]
        if len(items) <= self._per_conversation_limit:
            return
        items.sort(key=lambda t: (t.status not in _TERMINAL_STATUSES, t.created_at))
        for stale in items[: len(items) - self._per_conversation_limit]:
            if stale.status not in _TERMINAL_STATUSES:
                continue
            self._tasks.pop(stale.id, None)
            self._events.pop(stale.id, None)
            self._queues.pop(stale.id, None)
