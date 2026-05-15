"""Tests for FileConversationStore (server-side master conversation persistence)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from live2d_server.master_agent.conversation_store import (
    ConversationStore,
    FileConversationStore,
)


class InMemoryConversationStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_save_is_noop_on_base_class(self) -> None:
        store = ConversationStore()
        conv = await store.create()
        # Should not raise — base class save() is a documented no-op.
        await store.save(conv.id)


class FileConversationStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_writes_file_under_root(self) -> None:
        with TemporaryDirectory() as tmp:
            store = FileConversationStore(root=Path(tmp))
            conv = await store.create()
            saved = Path(tmp) / f"{conv.id}.json"
            self.assertTrue(saved.exists(), "create should persist immediately")
            data = json.loads(saved.read_text(encoding="utf-8"))
            self.assertEqual(data["id"], conv.id)
            self.assertEqual(data["messages"], [])

    async def test_save_persists_messages_after_append(self) -> None:
        with TemporaryDirectory() as tmp:
            store = FileConversationStore(root=Path(tmp))
            conv = await store.create()
            conv.append("user", "hello")
            conv.append("assistant", {"text": "hi", "tool_calls": []})
            await store.save(conv.id)
            data = json.loads((Path(tmp) / f"{conv.id}.json").read_text(encoding="utf-8"))
            self.assertEqual(len(data["messages"]), 2)
            self.assertEqual(data["messages"][0]["role"], "user")
            self.assertEqual(data["messages"][1]["role"], "assistant")

    async def test_get_or_create_hydrates_from_disk(self) -> None:
        """Regression: after server restart, get_or_create with a known
        conversation_id should load the JSON file and rebuild the
        conversation object — not silently start a new empty one."""
        with TemporaryDirectory() as tmp:
            store_a = FileConversationStore(root=Path(tmp))
            conv = await store_a.create()
            conv.append("user", "first")
            await store_a.save(conv.id)

            # Brand-new store instance (simulates server restart).
            store_b = FileConversationStore(root=Path(tmp))
            restored = await store_b.get_or_create(conv.id)
            self.assertEqual(restored.id, conv.id)
            self.assertEqual(len(restored.messages), 1)
            self.assertEqual(restored.messages[0]["content"], "first")

    async def test_get_or_create_unknown_id_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            store = FileConversationStore(root=Path(tmp))
            conv = await store.get_or_create("brand-new-id")
            self.assertEqual(conv.id, "brand-new-id")
            self.assertEqual(conv.messages, [])
            # Should have been persisted as an empty conversation.
            self.assertTrue((Path(tmp) / "brand-new-id.json").exists())

    async def test_rejects_path_traversal_ids(self) -> None:
        """Storage IDs are filename components; reject anything that
        could escape the root dir."""
        with TemporaryDirectory() as tmp:
            store = FileConversationStore(root=Path(tmp))
            # Should NOT write outside the root.
            await store.save("../escape")
            self.assertFalse(any(p.name.startswith("..") for p in Path(tmp).iterdir()))

    async def test_corrupted_file_falls_back_to_fresh(self) -> None:
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "abc-123.json").write_text("not json{", encoding="utf-8")
            store = FileConversationStore(root=Path(tmp))
            conv = await store.get_or_create("abc-123")
            # Should have created a fresh empty conversation rather than crashing.
            self.assertEqual(conv.id, "abc-123")
            self.assertEqual(conv.messages, [])

    async def test_list_persisted_ids_returns_only_safe_names(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "abc-123.json").write_text("{}", encoding="utf-8")
            (root / "def_456.json").write_text("{}", encoding="utf-8")
            (root / "..hidden.json").write_text("{}", encoding="utf-8")
            (root / "ignored.txt").write_text("ignored", encoding="utf-8")
            store = FileConversationStore(root=root)
            ids = await store.list_persisted_ids()
            self.assertEqual(set(ids), {"abc-123", "def_456"})


if __name__ == "__main__":
    unittest.main()
