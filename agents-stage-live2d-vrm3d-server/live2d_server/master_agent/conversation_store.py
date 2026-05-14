"""Store for master-agent conversation history.

Base :class:`ConversationStore` keeps everything in-memory. Subclass
:class:`FileConversationStore` persists each conversation as JSON so the
master agent's context survives server restarts (worker subtasks already
survive via codex/claude's own JSONL files, but the master agent's own
chat needed its own storage).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from .shared import MasterAgentConversation

_LOGGER = logging.getLogger(__name__)

# UUID/hex characters only; rejects anything that could escape the
# storage dir (path traversal) when used as a filename.
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _resolve_default_conversation_dir() -> Path:
    env = (os.getenv("MASTER_AGENT_CONVERSATION_DIR", "") or "").strip()
    if env:
        return Path(env).expanduser()
    # Project root: walk up from this file (live2d_server/master_agent/) to
    # the repo root and drop a sibling ``config/master-agent/conversations``
    # directory next to other config artifacts.
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "config" / "master-agent" / "conversations"


class ConversationStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._conversations: dict[str, MasterAgentConversation] = {}

    async def create(self) -> MasterAgentConversation:
        conversation = MasterAgentConversation.new()
        async with self._lock:
            self._conversations[conversation.id] = conversation
        await self.save(conversation.id)
        return conversation

    async def get(self, conversation_id: str) -> Optional[MasterAgentConversation]:
        async with self._lock:
            return self._conversations.get(conversation_id)

    async def get_or_create(self, conversation_id: str) -> MasterAgentConversation:
        async with self._lock:
            existing = self._conversations.get(conversation_id)
            if existing is not None:
                return existing
        # Try to hydrate from persistent storage before creating a fresh
        # entry — lets the user pick up an old conversation_id after a
        # server restart instead of starting over.
        hydrated = await self._load(conversation_id)
        async with self._lock:
            existing = self._conversations.get(conversation_id)
            if existing is not None:
                return existing
            if hydrated is not None:
                self._conversations[conversation_id] = hydrated
                return hydrated
            new = MasterAgentConversation(id=conversation_id)
            self._conversations[conversation_id] = new
        await self.save(conversation_id)
        return new

    async def list_ids(self) -> list[str]:
        async with self._lock:
            return list(self._conversations.keys())

    # Subclass extension points — default impls are no-ops so in-memory
    # mode keeps working unchanged.

    async def save(self, conversation_id: str) -> None:
        """Persist the named conversation. Base = no-op."""
        return None

    async def _load(self, conversation_id: str) -> Optional[MasterAgentConversation]:
        """Hydrate from persistent storage. Base = nothing on disk."""
        return None


class FileConversationStore(ConversationStore):
    """ConversationStore backed by ``<root>/<conversation_id>.json``.

    Writes are atomic (tempfile + rename) and use ``asyncio.to_thread``
    so the event loop isn't blocked even on slow Windows + AV setups.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        super().__init__()
        self._root = (root or _resolve_default_conversation_dir()).resolve()
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _LOGGER.warning(
                "Failed to ensure conversation dir %s (%s); persistence "
                "will be skipped silently for this run.", self._root, exc,
            )

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, conversation_id: str) -> Optional[Path]:
        if not _SAFE_ID_PATTERN.match(conversation_id or ""):
            return None
        return self._root / f"{conversation_id}.json"

    async def save(self, conversation_id: str) -> None:
        path = self._path_for(conversation_id)
        if path is None:
            return
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                return
            payload = self._serialize(conversation)
        try:
            await asyncio.to_thread(self._write_atomic, path, payload)
        except OSError as exc:
            _LOGGER.warning("conversation %s save failed: %s", conversation_id, exc)

    async def _load(self, conversation_id: str) -> Optional[MasterAgentConversation]:
        path = self._path_for(conversation_id)
        if path is None or not path.exists():
            return None
        try:
            data = await asyncio.to_thread(self._read, path)
        except (OSError, ValueError) as exc:
            _LOGGER.warning("conversation %s load failed: %s", conversation_id, exc)
            return None
        return self._deserialize(data)

    async def list_persisted_ids(self) -> list[str]:
        try:
            return await asyncio.to_thread(self._scan_ids)
        except OSError:
            return []

    # ------------------------------------------------------------------
    # Synchronous helpers (run inside asyncio.to_thread)
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize(conversation: MasterAgentConversation) -> dict[str, Any]:
        return {
            "schema": "master-agent-conversation/1",
            "id": conversation.id,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
            "messages": conversation.messages,
        }

    @staticmethod
    def _deserialize(data: Any) -> Optional[MasterAgentConversation]:
        if not isinstance(data, dict):
            return None
        conv_id = str(data.get("id") or "").strip()
        if not conv_id:
            return None
        messages = data.get("messages")
        if not isinstance(messages, list):
            messages = []
        created_at = data.get("created_at") or time.time()
        updated_at = data.get("updated_at") or created_at
        try:
            created_at = float(created_at)
            updated_at = float(updated_at)
        except (TypeError, ValueError):
            now = time.time()
            created_at = now
            updated_at = now
        return MasterAgentConversation(
            id=conv_id,
            messages=list(messages),
            created_at=created_at,
            updated_at=updated_at,
        )

    @staticmethod
    def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Same-dir tempfile so the rename is atomic on the same filesystem.
        fd, tmp_path_str = tempfile.mkstemp(
            prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent),
        )
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, default=str)
            os.replace(tmp_path, path)
        except Exception:
            with contextlib_suppress(FileNotFoundError):
                tmp_path.unlink()
            raise

    @staticmethod
    def _read(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _scan_ids(self) -> list[str]:
        return sorted(
            p.stem for p in self._root.glob("*.json")
            if p.is_file() and _SAFE_ID_PATTERN.match(p.stem)
        )


class contextlib_suppress:
    """Local mini-version of ``contextlib.suppress`` — kept inline to
    avoid an extra import in this hot-ish module."""

    def __init__(self, *exc_types: type[BaseException]) -> None:
        self._exc_types = exc_types

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, self._exc_types)
