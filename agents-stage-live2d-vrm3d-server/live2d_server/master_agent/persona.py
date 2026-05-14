"""Persona configuration for the master agent.

The master agent is the *director* of an actor stage — keeping it
purely mechanical works, but giving it a voice makes the experience
feel more alive. A :class:`PersonaConfig` describes the agent's
self-name, personality traits, speaking style and boundaries; the
system prompt then frames the assistant's user-facing prose around
them.

Design borrowed from Kokoro-Link's character system, trimmed for an
orchestrator agent: no affection/fatigue/NPC state, just the bits
that shape voice. Tool-calling JSON is **never** affected by the
persona — the prompt explicitly walls that off.

The persona is process-wide (one user, one director on stage). If
``enabled=False`` the prompt builder drops the section entirely so
the prompt is byte-identical to the pre-persona behavior.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)

DEFAULT_DISPLAY_NAME = "導演"


@dataclass(slots=True)
class PersonaConfig:
    """A snapshot of the master agent's user-facing voice.

    ``enabled=False`` is the legacy *pure tool* mode: the system prompt
    omits the persona block and the agent identifies itself only by
    the product name.

    All list/text fields are free-form. The prompt builder embeds them
    verbatim, so feel free to use full sentences (e.g. ``speaking_style``
    is best as a paragraph, not a single adjective).
    """

    enabled: bool = True
    display_name: str = DEFAULT_DISPLAY_NAME
    summary: str = ""
    personality: list[str] = field(default_factory=list)
    speaking_style: str = ""
    catchphrase: str = ""
    boundaries: list[str] = field(default_factory=list)

    def normalized(self) -> "PersonaConfig":
        """Return a copy with whitespace trimmed and empty entries dropped."""
        return PersonaConfig(
            enabled=bool(self.enabled),
            display_name=(self.display_name or "").strip() or DEFAULT_DISPLAY_NAME,
            summary=(self.summary or "").strip(),
            personality=[p.strip() for p in (self.personality or []) if p and p.strip()],
            speaking_style=(self.speaking_style or "").strip(),
            catchphrase=(self.catchphrase or "").strip(),
            boundaries=[b.strip() for b in (self.boundaries or []) if b and b.strip()],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "display_name": self.display_name,
            "summary": self.summary,
            "personality": list(self.personality),
            "speaking_style": self.speaking_style,
            "catchphrase": self.catchphrase,
            "boundaries": list(self.boundaries),
        }

    @staticmethod
    def from_dict(data: Any) -> Optional["PersonaConfig"]:
        if not isinstance(data, dict):
            return None
        try:
            return PersonaConfig(
                enabled=bool(data.get("enabled", True)),
                display_name=str(data.get("display_name") or DEFAULT_DISPLAY_NAME),
                summary=str(data.get("summary") or ""),
                personality=[str(p) for p in (data.get("personality") or []) if p],
                speaking_style=str(data.get("speaking_style") or ""),
                catchphrase=str(data.get("catchphrase") or ""),
                boundaries=[str(b) for b in (data.get("boundaries") or []) if b],
            )
        except (TypeError, ValueError):
            return None


def default_persona() -> PersonaConfig:
    """The fallback persona — imported from the presets module so
    there's one source of truth for what 「導演」looks like."""
    from .persona_presets import get_preset
    preset = get_preset("director")
    if preset is None:  # pragma: no cover — preset is always shipped
        return PersonaConfig()
    return preset


class PersonaStore:
    """In-memory persona store. ``FilePersonaStore`` adds persistence."""

    def __init__(self, initial: Optional[PersonaConfig] = None) -> None:
        self._lock = asyncio.Lock()
        self._persona = (initial or default_persona()).normalized()

    async def get(self) -> PersonaConfig:
        async with self._lock:
            return self._copy(self._persona)

    async def set(self, persona: PersonaConfig) -> PersonaConfig:
        normalized = persona.normalized()
        async with self._lock:
            self._persona = normalized
            await self._after_set_locked()
            return self._copy(self._persona)

    async def reset_to_default(self) -> PersonaConfig:
        return await self.set(default_persona())

    @staticmethod
    def _copy(p: PersonaConfig) -> PersonaConfig:
        return PersonaConfig(
            enabled=p.enabled,
            display_name=p.display_name,
            summary=p.summary,
            personality=list(p.personality),
            speaking_style=p.speaking_style,
            catchphrase=p.catchphrase,
            boundaries=list(p.boundaries),
        )

    # Subclass extension point — runs with the lock held.
    async def _after_set_locked(self) -> None:
        return None


class FilePersonaStore(PersonaStore):
    """Persona persisted to a JSON file.

    Missing / unreadable file → falls back to :func:`default_persona`,
    so first launch and corrupted state both behave gracefully.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path).resolve()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _LOGGER.warning(
                "persona dir %s not writable (%s); changes won't persist",
                self._path.parent, exc,
            )
        super().__init__(initial=self._load_from_disk())

    @property
    def path(self) -> Path:
        return self._path

    def _load_from_disk(self) -> PersonaConfig:
        if not self._path.exists():
            return default_persona()
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            _LOGGER.warning("persona load failed (%s); using default", exc)
            return default_persona()
        loaded = PersonaConfig.from_dict(data)
        return (loaded or default_persona()).normalized()

    async def _after_set_locked(self) -> None:
        payload = {
            "schema": "master-agent-persona/1",
            **self._persona.to_dict(),
        }
        try:
            await asyncio.to_thread(_write_atomic, self._path, payload)
        except OSError as exc:
            _LOGGER.warning("persona save failed: %s", exc)


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


__all__ = [
    "PersonaConfig",
    "PersonaStore",
    "FilePersonaStore",
    "default_persona",
    "DEFAULT_DISPLAY_NAME",
]
