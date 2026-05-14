"""Module-level runtime helpers wiring the bot into the FastAPI lifespan.

The runtime owns a single :class:`TelegramBotApp` plus its
:class:`FileBindingStore`. ``start_telegram_bot`` is called from the
FastAPI ``lifespan`` startup; ``stop_telegram_bot`` from shutdown.
Both are safe to call when the integration is disabled — they no-op.

We expose ``get_binding_store`` separately so the API router can mint
codes / read status without having to drag the whole bot into the API
module.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .binding_store import FileBindingStore
from .bot import ServiceProvider, TelegramBotApp
from .config import TelegramConfig, load_telegram_config

_LOGGER = logging.getLogger(__name__)


class _Runtime:
    """Holds the singleton bot + binding store for the process."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._config: Optional[TelegramConfig] = None
        self._store: Optional[FileBindingStore] = None
        self._bot: Optional[TelegramBotApp] = None
        self._started = False

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def config(self) -> TelegramConfig:
        if self._config is None:
            self._config = load_telegram_config()
        return self._config

    def store(self) -> FileBindingStore:
        if self._store is None:
            cfg = self.config()
            assert cfg.bindings_file is not None
            self._store = FileBindingStore(
                cfg.bindings_file,
                code_ttl_seconds=cfg.binding_code_ttl_seconds,
            )
        return self._store

    @property
    def bot(self) -> Optional[TelegramBotApp]:
        return self._bot

    def is_running(self) -> bool:
        return self._started and self._bot is not None

    async def start(self, service_provider: ServiceProvider) -> bool:
        async with self._lock:
            if self._started:
                return True
            cfg = self.config()
            if not cfg.is_enabled():
                _LOGGER.info(
                    "telegram bot disabled (TELEGRAM_BOT_TOKEN not set) — skipping"
                )
                return False
            self._bot = TelegramBotApp(
                config=cfg,
                service_provider=service_provider,
                binding_store=self.store(),
            )
            try:
                await self._bot.start()
            except Exception:  # noqa: BLE001 — boot failure must not crash FastAPI
                _LOGGER.exception("telegram bot failed to start")
                self._bot = None
                return False
            self._started = True
            return True

    async def stop(self) -> None:
        async with self._lock:
            if not self._started or self._bot is None:
                return
            try:
                await self._bot.stop()
            finally:
                self._bot = None
                self._started = False


_runtime = _Runtime()


def get_telegram_runtime() -> _Runtime:
    """Return the process-wide runtime (mainly used by the API router)."""
    return _runtime


async def start_telegram_bot(service_provider: ServiceProvider) -> bool:
    """Start the bot polling loop. No-op when disabled. Returns started flag."""
    return await _runtime.start(service_provider)


async def stop_telegram_bot() -> None:
    """Stop the bot polling loop. Safe to call multiple times."""
    await _runtime.stop()


__all__ = [
    "get_telegram_runtime",
    "start_telegram_bot",
    "stop_telegram_bot",
]
