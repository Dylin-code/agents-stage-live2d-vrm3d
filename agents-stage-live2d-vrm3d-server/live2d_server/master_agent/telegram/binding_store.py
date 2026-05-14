"""Persistent store for one-shot binding codes and chat→conversation bindings.

The frontend asks the API to mint a code → user replies ``/bind <code>``
in TG → the bot consumes the code and writes a :class:`Binding` keyed
by TG ``chat_id``. After that, plain-text messages from the chat are
routed to the bound conversation. ``/new`` rotates the conversation id
on the same binding; ``/unbind`` removes it.

The file is JSON for easy inspection; writes are atomic via tempfile +
rename. We keep the structure tiny so a human can fix it by hand if
something goes sideways.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)

_CODE_DIGITS = 6
_CODE_MAX = 10 ** _CODE_DIGITS  # 1_000_000 — codes are 0-padded to 6.
_CODE_ISSUE_LIMIT = 50  # collision attempts before we give up


@dataclass(slots=True)
class _PendingCode:
    code: str
    issued_at: float
    expires_at: float

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass(slots=True)
class Binding:
    """A TG chat → master-agent ``user container`` binding.

    ``conversation_id`` rotates when the user runs ``/new`` so the
    binding outlives any one master-agent conversation. ``tg_user_id``
    and ``tg_username`` are snapshotted at bind time for audit/UI; the
    bot trusts ``chat_id`` for routing.
    """

    chat_id: int
    conversation_id: str
    tg_user_id: int
    tg_username: str = ""
    tg_first_name: str = ""
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "conversation_id": self.conversation_id,
            "tg_user_id": self.tg_user_id,
            "tg_username": self.tg_username,
            "tg_first_name": self.tg_first_name,
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Optional["Binding"]:
        try:
            chat_id = int(data["chat_id"])
            conversation_id = str(data["conversation_id"]).strip()
            tg_user_id = int(data.get("tg_user_id") or 0)
        except (KeyError, TypeError, ValueError):
            return None
        if not conversation_id:
            return None
        return Binding(
            chat_id=chat_id,
            conversation_id=conversation_id,
            tg_user_id=tg_user_id,
            tg_username=str(data.get("tg_username") or ""),
            tg_first_name=str(data.get("tg_first_name") or ""),
            created_at=float(data.get("created_at") or time.time()),
            last_active_at=float(data.get("last_active_at") or time.time()),
        )


class BindingStore:
    """In-memory binding store. ``FileBindingStore`` adds JSON persistence."""

    def __init__(
        self,
        *,
        code_ttl_seconds: int = 600,
        clock: Any = time.time,
        rng: Any = None,
    ) -> None:
        self._lock = asyncio.Lock()
        self._pending: dict[str, _PendingCode] = {}
        self._bindings: dict[int, Binding] = {}
        self._code_ttl = max(30, int(code_ttl_seconds))
        self._clock = clock
        self._rng = rng or random.SystemRandom()

    # ------------------------------------------------------------------
    # Code lifecycle
    # ------------------------------------------------------------------

    async def issue_code(self) -> tuple[str, float]:
        """Mint a fresh 6-digit code. Returns (code, expires_at_unix)."""
        now = float(self._clock())
        async with self._lock:
            self._prune_expired_locked(now)
            for _ in range(_CODE_ISSUE_LIMIT):
                candidate = f"{self._rng.randrange(_CODE_MAX):0{_CODE_DIGITS}d}"
                if candidate in self._pending:
                    continue
                pending = _PendingCode(
                    code=candidate,
                    issued_at=now,
                    expires_at=now + self._code_ttl,
                )
                self._pending[candidate] = pending
                await self._after_mutate_locked()
                return candidate, pending.expires_at
        raise RuntimeError("could not allocate binding code (too many collisions)")

    async def consume_code(self, code: str) -> bool:
        """Pop ``code`` if it exists and is unexpired. Returns success.

        Codes are single-use; consuming a code removes it from the
        pending set even if the subsequent bind fails — this matches
        the user's mental model (they typed it, it's burned).
        """
        normalized = (code or "").strip()
        if not normalized:
            return False
        now = float(self._clock())
        async with self._lock:
            self._prune_expired_locked(now)
            pending = self._pending.pop(normalized, None)
            if pending is None:
                return False
            await self._after_mutate_locked()
            return not pending.is_expired(now)

    async def pending_count(self) -> int:
        now = float(self._clock())
        async with self._lock:
            self._prune_expired_locked(now)
            return len(self._pending)

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    async def bind(
        self,
        *,
        chat_id: int,
        conversation_id: str,
        tg_user_id: int,
        tg_username: str = "",
        tg_first_name: str = "",
    ) -> Binding:
        """Create or replace the binding for ``chat_id``.

        Replacing is intentional: a user who bound to a stale
        conversation can re-issue a code and rebind to a fresh one
        without manually ``/unbind``ing first.
        """
        now = float(self._clock())
        binding = Binding(
            chat_id=int(chat_id),
            conversation_id=conversation_id.strip(),
            tg_user_id=int(tg_user_id),
            tg_username=tg_username,
            tg_first_name=tg_first_name,
            created_at=now,
            last_active_at=now,
        )
        async with self._lock:
            self._bindings[binding.chat_id] = binding
            await self._after_mutate_locked()
        return binding

    async def unbind(self, chat_id: int) -> bool:
        async with self._lock:
            removed = self._bindings.pop(int(chat_id), None) is not None
            if removed:
                await self._after_mutate_locked()
            return removed

    async def get_by_chat_id(self, chat_id: int) -> Optional[Binding]:
        async with self._lock:
            return self._bindings.get(int(chat_id))

    async def set_conversation(self, chat_id: int, conversation_id: str) -> Optional[Binding]:
        """Rotate the conversation on an existing binding (used by ``/new``)."""
        async with self._lock:
            existing = self._bindings.get(int(chat_id))
            if existing is None:
                return None
            existing.conversation_id = conversation_id.strip()
            existing.last_active_at = float(self._clock())
            await self._after_mutate_locked()
            return existing

    async def touch(self, chat_id: int) -> None:
        async with self._lock:
            existing = self._bindings.get(int(chat_id))
            if existing is None:
                return
            existing.last_active_at = float(self._clock())
            await self._after_mutate_locked()

    async def list_bindings(self) -> list[Binding]:
        async with self._lock:
            return list(self._bindings.values())

    async def binding_count(self) -> int:
        async with self._lock:
            return len(self._bindings)

    # ------------------------------------------------------------------
    # Subclass extension points
    # ------------------------------------------------------------------

    async def _after_mutate_locked(self) -> None:
        """Hook for persistent subclasses. Called with ``self._lock`` held."""
        return None

    def _prune_expired_locked(self, now: float) -> None:
        if not self._pending:
            return
        expired = [code for code, p in self._pending.items() if p.is_expired(now)]
        for code in expired:
            self._pending.pop(code, None)


class FileBindingStore(BindingStore):
    """JSON-file backed binding store.

    Bindings persist across server restarts. Pending codes are
    deliberately **not** persisted — they're short-lived secrets and
    surviving a restart would only weaken them.
    """

    def __init__(
        self,
        path: Path,
        *,
        code_ttl_seconds: int = 600,
        clock: Any = time.time,
        rng: Any = None,
    ) -> None:
        super().__init__(code_ttl_seconds=code_ttl_seconds, clock=clock, rng=rng)
        self._path = Path(path).resolve()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _LOGGER.warning(
                "telegram bindings dir %s not writable (%s); changes won't persist",
                self._path.parent, exc,
            )
        self._load_from_disk()

    @property
    def path(self) -> Path:
        return self._path

    def _load_from_disk(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            _LOGGER.warning("telegram bindings load failed (%s); starting empty", exc)
            return
        raw_bindings = data.get("bindings") if isinstance(data, dict) else None
        if not isinstance(raw_bindings, list):
            return
        for entry in raw_bindings:
            if not isinstance(entry, dict):
                continue
            binding = Binding.from_dict(entry)
            if binding is None:
                continue
            self._bindings[binding.chat_id] = binding

    async def _after_mutate_locked(self) -> None:
        payload = {
            "schema": "master-agent-telegram-bindings/1",
            "bindings": [b.to_dict() for b in self._bindings.values()],
        }
        path = self._path
        try:
            await asyncio.to_thread(_write_atomic, path, payload)
        except OSError as exc:
            _LOGGER.warning("telegram bindings save failed: %s", exc)


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent),
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
