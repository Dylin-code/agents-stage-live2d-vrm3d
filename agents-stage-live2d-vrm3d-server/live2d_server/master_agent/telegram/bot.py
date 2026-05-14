"""Telegram bot application and command handlers.

This module builds the ``python-telegram-bot`` :class:`Application`,
registers handlers, and exposes :class:`TelegramBotApp` with explicit
``start``/``stop`` async methods that the FastAPI lifespan drives.

Handler responsibilities are intentionally thin — they validate input,
talk to :class:`BindingStore`, and (for plain text) drive the master
agent service through :class:`bridge.EventBridge`. All TG-specific
formatting lives in the bridge.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..service import MasterAgentService
from .binding_store import Binding, BindingStore
from .bridge import EventBridge, TelegramMessenger
from .config import TelegramConfig

_LOGGER = logging.getLogger(__name__)

ServiceProvider = Callable[[], Awaitable[MasterAgentService]]


_HELP_TEXT = (
    "🤖 *總控 Agent Telegram Bot*\n\n"
    "請先到網頁的『總控 Agent → 綁定 Telegram』取得 6 位數綁定碼，"
    "然後在這裡輸入：\n"
    "`/bind 123456`\n\n"
    "綁定後可直接傳訊息派工給總控。\n\n"
    "*指令*\n"
    "/bind <code> — 綁定到網頁端會話\n"
    "/unbind — 解除綁定\n"
    "/new — 開啟一段新對話\n"
    "/abort — 中止當前任務\n"
    "/status — 顯示綁定狀態\n"
    "/whoami — 顯示你的 TG user id（用來設白名單）\n"
    "/help — 顯示說明"
)


class _ChatMessenger:
    """Adapter from :class:`TelegramMessenger` to ``Application.bot``."""

    def __init__(self, bot: Any, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = int(chat_id)

    async def send_text(self, text: str) -> int:
        msg = await self._bot.send_message(chat_id=self._chat_id, text=text)
        return int(msg.message_id)

    async def edit_text(self, message_id: int, text: str) -> None:
        await self._bot.edit_message_text(
            chat_id=self._chat_id,
            message_id=int(message_id),
            text=text,
        )

    async def send_typing(self) -> None:
        try:
            await self._bot.send_chat_action(chat_id=self._chat_id, action=ChatAction.TYPING)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("send_chat_action failed", exc_info=True)


class TelegramBotApp:
    """Telegram bot wired to the master agent.

    The bot is single-tenant: one ``MasterAgentService`` powers all
    bound chats. The service provider is async so we reuse the
    lazy-init pattern already used by ``api._get_service``.
    """

    def __init__(
        self,
        *,
        config: TelegramConfig,
        service_provider: ServiceProvider,
        binding_store: BindingStore,
        messenger_factory: Optional[Callable[[Any, int], TelegramMessenger]] = None,
    ) -> None:
        if not config.is_enabled():
            raise ValueError("TelegramBotApp requires TELEGRAM_BOT_TOKEN to be set")
        self._config = config
        self._service_provider = service_provider
        self._store = binding_store
        self._messenger_factory = messenger_factory or _ChatMessenger
        self._chat_locks: dict[int, asyncio.Lock] = {}
        self._chat_locks_guard = asyncio.Lock()
        self._app: Optional[Application] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._app is not None:
            return
        application = (
            ApplicationBuilder()
            .token(self._config.token)
            .concurrent_updates(True)
            .build()
        )
        self._register_handlers(application)
        await application.initialize()
        await application.start()
        # ``updater`` is the long-polling driver; ``drop_pending_updates``
        # avoids re-processing messages queued while the server was off.
        if application.updater is None:
            raise RuntimeError("python-telegram-bot updater is missing")
        await application.updater.start_polling(drop_pending_updates=True)
        self._app = application
        _LOGGER.info("telegram bot polling started")

    async def stop(self) -> None:
        app = self._app
        if app is None:
            return
        self._app = None
        try:
            if app.updater is not None and app.updater.running:
                await app.updater.stop()
            if app.running:
                await app.stop()
            await app.shutdown()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("telegram bot stop failed")
        _LOGGER.info("telegram bot polling stopped")

    @property
    def application(self) -> Optional[Application]:
        return self._app

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def _register_handlers(self, application: Application) -> None:
        only_private = filters.ChatType.PRIVATE
        application.add_handler(CommandHandler("start", self._on_start, filters=only_private))
        application.add_handler(CommandHandler("help", self._on_start, filters=only_private))
        application.add_handler(CommandHandler("bind", self._on_bind, filters=only_private))
        application.add_handler(CommandHandler("unbind", self._on_unbind, filters=only_private))
        application.add_handler(CommandHandler("new", self._on_new, filters=only_private))
        application.add_handler(CommandHandler("abort", self._on_abort, filters=only_private))
        application.add_handler(CommandHandler("status", self._on_status, filters=only_private))
        # /whoami stays outside the whitelist guard on purpose: a brand-new
        # operator needs a way to read their own TG user id so they can add
        # it to TELEGRAM_ALLOWED_USERS. The reply only echoes data the user
        # already owns about themselves.
        application.add_handler(CommandHandler("whoami", self._on_whoami, filters=only_private))
        application.add_handler(
            MessageHandler(only_private & filters.TEXT & ~filters.COMMAND, self._on_message)
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_user(update):
            return
        chat_id = self._chat_id_of(update)
        if chat_id is None:
            return
        await context.bot.send_message(
            chat_id=chat_id,
            text=_HELP_TEXT,
            parse_mode="Markdown",
        )

    async def _on_bind(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_user(update):
            return
        chat_id = self._chat_id_of(update)
        if chat_id is None:
            return
        args = context.args or []
        if not args:
            await context.bot.send_message(
                chat_id=chat_id, text="用法：/bind <6 位數綁定碼>"
            )
            return
        code = args[0].strip()
        if not await self._store.consume_code(code):
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ 綁定碼無效或已過期。請到網頁端重新產生。",
            )
            return
        service = await self._service_provider()
        conversation = await service.new_conversation()
        user = update.effective_user
        binding = await self._store.bind(
            chat_id=chat_id,
            conversation_id=conversation.id,
            tg_user_id=user.id if user else 0,
            tg_username=(user.username or "") if user else "",
            tg_first_name=(user.first_name or "") if user else "",
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "✅ 已綁定！\n"
                f"對話 ID: `{binding.conversation_id[:12]}`\n\n"
                "現在直接傳訊息就會派給總控 Agent。/help 查看指令。"
            ),
            parse_mode="Markdown",
        )

    async def _on_unbind(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_user(update):
            return
        chat_id = self._chat_id_of(update)
        if chat_id is None:
            return
        removed = await self._store.unbind(chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text="✅ 已解除綁定。" if removed else "（本帳號目前沒有綁定）",
        )

    async def _on_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_user(update):
            return
        chat_id = self._chat_id_of(update)
        if chat_id is None:
            return
        binding = await self._store.get_by_chat_id(chat_id)
        if binding is None:
            await context.bot.send_message(
                chat_id=chat_id, text="尚未綁定。請先 /bind <code>。"
            )
            return
        service = await self._service_provider()
        conversation = await service.new_conversation()
        await self._store.set_conversation(chat_id, conversation.id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✨ 已開啟新對話 `{conversation.id[:12]}`",
            parse_mode="Markdown",
        )

    async def _on_abort(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_user(update):
            return
        chat_id = self._chat_id_of(update)
        if chat_id is None:
            return
        binding = await self._store.get_by_chat_id(chat_id)
        if binding is None:
            await context.bot.send_message(chat_id=chat_id, text="尚未綁定。")
            return
        service = await self._service_provider()
        aborted = await service.abort(binding.conversation_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text="🛑 已送出中止訊號。" if aborted else "（目前沒有進行中的任務）",
        )

    async def _on_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_user(update):
            return
        chat_id = self._chat_id_of(update)
        if chat_id is None:
            return
        binding = await self._store.get_by_chat_id(chat_id)
        if binding is None:
            await context.bot.send_message(chat_id=chat_id, text="尚未綁定。/bind <code> 開始。")
            return
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "📍 綁定狀態\n"
                f"對話 ID: `{binding.conversation_id}`\n"
                f"TG user: {binding.tg_first_name or binding.tg_username or binding.tg_user_id}"
            ),
            parse_mode="Markdown",
        )

    async def _on_whoami(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = self._chat_id_of(update)
        user = update.effective_user
        if chat_id is None or user is None:
            return
        whitelist = self._config.allowed_user_ids
        whitelist_line = (
            "（未設定 TELEGRAM_ALLOWED_USERS — 目前所有人都可綁定）"
            if not whitelist
            else f"白名單: {'允許' if user.id in whitelist else '不在白名單'}"
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🪪 你的 Telegram 資訊\n"
                f"user id: `{user.id}`\n"
                f"username: @{user.username or '(無)'}\n"
                f"chat id: `{chat_id}`\n"
                f"{whitelist_line}"
            ),
            parse_mode="Markdown",
        )

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_user(update):
            return
        chat_id = self._chat_id_of(update)
        if chat_id is None or update.message is None:
            return
        text = (update.message.text or "").strip()
        if not text:
            return

        binding = await self._store.get_by_chat_id(chat_id)
        if binding is None:
            await context.bot.send_message(
                chat_id=chat_id,
                text="尚未綁定。請先到網頁端取得綁定碼，再 /bind <code>。",
            )
            return

        lock = await self._lock_for(chat_id)
        if lock.locked():
            await context.bot.send_message(
                chat_id=chat_id,
                text="⏳ 上一個任務還在跑，請稍候或 /abort 中止。",
            )
            return

        async with lock:
            await self._store.touch(chat_id)
            service = await self._service_provider()
            messenger = self._messenger_factory(context.bot, chat_id)
            bridge = EventBridge(messenger)
            try:
                events = service.run_stream(
                    conversation_id=binding.conversation_id,
                    message=text,
                    default_cwd=None,
                    permit_full_access=False,
                )
                await bridge.consume(events)
            except Exception as exc:  # noqa: BLE001 — surface to user
                _LOGGER.exception("master agent run_stream crashed (tg)")
                await context.bot.send_message(
                    chat_id=chat_id, text=f"❌ 內部錯誤: {exc}"
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _guard_user(self, update: Update) -> bool:
        user = update.effective_user
        if user is None:
            return False
        if not self._config.is_user_allowed(user.id):
            chat_id = self._chat_id_of(update)
            if chat_id is not None and self._app is not None:
                try:
                    await self._app.bot.send_message(
                        chat_id=chat_id,
                        text="🚫 你的 Telegram 帳號不在白名單。",
                    )
                except Exception:  # noqa: BLE001
                    pass
            return False
        return True

    @staticmethod
    def _chat_id_of(update: Update) -> Optional[int]:
        chat = update.effective_chat
        return int(chat.id) if chat is not None else None

    async def _lock_for(self, chat_id: int) -> asyncio.Lock:
        async with self._chat_locks_guard:
            lock = self._chat_locks.get(chat_id)
            if lock is None:
                lock = asyncio.Lock()
                self._chat_locks[chat_id] = lock
            return lock


__all__ = ["TelegramBotApp", "ServiceProvider"]
