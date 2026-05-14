"""Telegram bot integration for the master agent.

The bot runs in long-polling mode (no public webhook required). Users
bind their TG chat to a master-agent ``user container`` by issuing a
short-lived code from the frontend and replying ``/bind <code>`` to the
bot in a private chat. Subsequent plain-text messages are dispatched
into ``MasterAgentService.run_stream`` and the resulting SSE events are
folded into TG messages by :mod:`bridge`.

Layout — one concern per file:

- :mod:`config`         — env reading, dataclass + ``is_enabled()``.
- :mod:`binding_store`  — on-disk store for codes and chat→conversation bindings.
- :mod:`bridge`         — converts ``MasterEvent`` SSE stream into TG messages.
- :mod:`bot`            — handler registration + polling lifecycle.
- :mod:`runtime`        — module-level start/stop helpers used by FastAPI lifespan.
"""

from .config import TelegramConfig, load_telegram_config
from .binding_store import Binding, BindingStore, FileBindingStore
from .bridge import EventBridge, TelegramMessenger
from .runtime import (
    get_telegram_runtime,
    start_telegram_bot,
    stop_telegram_bot,
)

__all__ = [
    "TelegramConfig",
    "load_telegram_config",
    "Binding",
    "BindingStore",
    "FileBindingStore",
    "EventBridge",
    "TelegramMessenger",
    "get_telegram_runtime",
    "start_telegram_bot",
    "stop_telegram_bot",
]
