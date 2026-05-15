"""Tests for the Telegram config loader (env-driven)."""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from typing import Iterator

from live2d_server.master_agent.telegram.config import load_telegram_config


@contextmanager
def _env(**values: str) -> Iterator[None]:
    """Temporarily set/unset env vars for one test scope."""
    previous = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v == "":
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, original in previous.items():
            if original is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = original


class LoadTelegramConfigTest(unittest.TestCase):
    def test_disabled_when_token_missing(self) -> None:
        with _env(TELEGRAM_BOT_TOKEN=""):
            cfg = load_telegram_config()
            self.assertFalse(cfg.is_enabled())

    def test_enabled_when_token_present(self) -> None:
        with _env(TELEGRAM_BOT_TOKEN="abc:123"):
            cfg = load_telegram_config()
            self.assertTrue(cfg.is_enabled())
            self.assertEqual(cfg.token, "abc:123")

    def test_user_allowed_by_default(self) -> None:
        with _env(TELEGRAM_BOT_TOKEN="t", TELEGRAM_ALLOWED_USERS=""):
            cfg = load_telegram_config()
            self.assertTrue(cfg.is_user_allowed(12345))

    def test_whitelist_restricts_users(self) -> None:
        with _env(TELEGRAM_BOT_TOKEN="t", TELEGRAM_ALLOWED_USERS="111, 222"):
            cfg = load_telegram_config()
            self.assertTrue(cfg.is_user_allowed(111))
            self.assertTrue(cfg.is_user_allowed(222))
            self.assertFalse(cfg.is_user_allowed(333))

    def test_whitelist_ignores_garbage(self) -> None:
        with _env(TELEGRAM_BOT_TOKEN="t", TELEGRAM_ALLOWED_USERS="111, abc, 222"):
            cfg = load_telegram_config()
            self.assertTrue(cfg.is_user_allowed(111))
            self.assertTrue(cfg.is_user_allowed(222))
            self.assertFalse(cfg.is_user_allowed(0))

    def test_username_is_stripped_of_at_sign(self) -> None:
        with _env(TELEGRAM_BOT_TOKEN="t", TELEGRAM_BOT_USERNAME="@my_bot"):
            cfg = load_telegram_config()
            self.assertEqual(cfg.bot_username, "my_bot")

    def test_ttl_minimum_clamped(self) -> None:
        with _env(TELEGRAM_BOT_TOKEN="t", TELEGRAM_BINDING_CODE_TTL_SECONDS="5"):
            cfg = load_telegram_config()
            self.assertGreaterEqual(cfg.binding_code_ttl_seconds, 30)


if __name__ == "__main__":
    unittest.main()
