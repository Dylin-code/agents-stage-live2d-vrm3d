"""Tests for the Telegram binding store."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from live2d_server.master_agent.telegram.binding_store import (
    BindingStore,
    FileBindingStore,
)


class _MutableClock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _SeqRng:
    """Deterministic ``randrange`` for collision tests."""

    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randrange(self, _stop: int) -> int:
        if not self._values:
            raise RuntimeError("rng exhausted")
        return self._values.pop(0)


class BindingStoreInMemoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_issue_code_pads_to_six_digits(self) -> None:
        clock = _MutableClock()
        store = BindingStore(code_ttl_seconds=600, clock=clock, rng=_SeqRng([42]))
        code, expires_at = await store.issue_code()
        self.assertEqual(code, "000042")
        self.assertAlmostEqual(expires_at, clock.now + 600)

    async def test_consume_code_burns_on_use(self) -> None:
        clock = _MutableClock()
        store = BindingStore(clock=clock, rng=_SeqRng([123456]))
        code, _ = await store.issue_code()
        self.assertTrue(await store.consume_code(code))
        self.assertFalse(await store.consume_code(code))  # one-shot

    async def test_consume_code_rejects_expired(self) -> None:
        clock = _MutableClock()
        store = BindingStore(code_ttl_seconds=60, clock=clock, rng=_SeqRng([111111]))
        code, _ = await store.issue_code()
        clock.advance(120)
        self.assertFalse(await store.consume_code(code))

    async def test_consume_rejects_unknown_code(self) -> None:
        store = BindingStore()
        self.assertFalse(await store.consume_code("000000"))
        self.assertFalse(await store.consume_code(""))

    async def test_bind_creates_persistent_record(self) -> None:
        store = BindingStore()
        binding = await store.bind(
            chat_id=42,
            conversation_id="conv-abc",
            tg_user_id=999,
            tg_username="danny",
        )
        self.assertEqual(binding.chat_id, 42)
        self.assertEqual(binding.conversation_id, "conv-abc")
        fetched = await store.get_by_chat_id(42)
        self.assertIsNotNone(fetched)
        assert fetched is not None  # narrow for type-checker
        self.assertEqual(fetched.tg_username, "danny")

    async def test_bind_replaces_existing(self) -> None:
        store = BindingStore()
        await store.bind(chat_id=1, conversation_id="old", tg_user_id=7)
        await store.bind(chat_id=1, conversation_id="new", tg_user_id=7)
        binding = await store.get_by_chat_id(1)
        assert binding is not None
        self.assertEqual(binding.conversation_id, "new")

    async def test_set_conversation_rotates_id(self) -> None:
        store = BindingStore()
        await store.bind(chat_id=1, conversation_id="old", tg_user_id=7)
        rotated = await store.set_conversation(1, "fresh")
        assert rotated is not None
        self.assertEqual(rotated.conversation_id, "fresh")

    async def test_set_conversation_returns_none_when_unbound(self) -> None:
        store = BindingStore()
        result = await store.set_conversation(99, "x")
        self.assertIsNone(result)

    async def test_unbind_removes_record(self) -> None:
        store = BindingStore()
        await store.bind(chat_id=1, conversation_id="x", tg_user_id=7)
        self.assertTrue(await store.unbind(1))
        self.assertFalse(await store.unbind(1))

    async def test_concurrent_issue_codes_are_unique(self) -> None:
        store = BindingStore()
        results = await asyncio.gather(*(store.issue_code() for _ in range(20)))
        codes = {code for code, _ in results}
        self.assertEqual(len(codes), 20)


class FileBindingStorePersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_bindings_persist_across_instances(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "telegram_bindings.json"
            store_a = FileBindingStore(path)
            await store_a.bind(
                chat_id=42, conversation_id="conv-1", tg_user_id=7, tg_username="d"
            )

            store_b = FileBindingStore(path)
            binding = await store_b.get_by_chat_id(42)
            self.assertIsNotNone(binding)
            assert binding is not None
            self.assertEqual(binding.conversation_id, "conv-1")
            self.assertEqual(binding.tg_username, "d")

    async def test_pending_codes_are_not_persisted(self) -> None:
        """Codes are short-lived secrets; surviving a restart weakens them."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "telegram_bindings.json"
            store_a = FileBindingStore(path)
            code, _ = await store_a.issue_code()
            store_b = FileBindingStore(path)
            self.assertFalse(await store_b.consume_code(code))

    async def test_unbind_persists(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "telegram_bindings.json"
            store_a = FileBindingStore(path)
            await store_a.bind(chat_id=1, conversation_id="c", tg_user_id=7)
            await store_a.unbind(1)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["bindings"], [])


if __name__ == "__main__":
    unittest.main()
