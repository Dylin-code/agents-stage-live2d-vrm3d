"""WebSocket broadcaster for master-agent state changes.

Mirrors the session-bridge WS pattern: every connected client receives
a JSON envelope whenever a SubTask transitions state or a master-agent
conversation produces a final reply. Lets multi-tab / desktop-widget
viewers stay in sync without re-fetching.

Designed to be cheap: clients are stored in a set, dead-write attempts
prune the set automatically. No heartbeat (the underlying ASGI server
handles ping/pong) and no per-conversation filtering — every WS
subscriber sees every event. The frontend filters by conversation_id.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import WebSocket

_LOGGER = logging.getLogger(__name__)


class MasterAgentBroadcaster:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def publish(self, envelope: dict[str, Any]) -> None:
        """Send ``envelope`` to every connected client.

        Failed writes drop the offending client — the next ASGI poll
        will detect the disconnect anyway, but pruning here avoids
        repeated retries on a dead socket.
        """
        if not self._clients:
            return
        try:
            payload = json.dumps(envelope, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as exc:
            _LOGGER.warning("master agent broadcast skipped (encode failed): %s", exc)
            return
        async with self._lock:
            targets = list(self._clients)
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001 — disconnected mid-write
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)

    def client_count(self) -> int:
        return len(self._clients)


# Convenience event constructors so callers don't have to remember the
# envelope shape every time.


def subtask_event(event_type: str, subtask_dict: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": "subtask",
        "type": event_type,
        "conversation_id": subtask_dict.get("conversation_id"),
        "subtask": subtask_dict,
    }


def conversation_event(
    conversation_id: str,
    event_type: str,
    content: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "event": "conversation",
        "type": event_type,
        "conversation_id": conversation_id,
        "content": content or {},
    }
