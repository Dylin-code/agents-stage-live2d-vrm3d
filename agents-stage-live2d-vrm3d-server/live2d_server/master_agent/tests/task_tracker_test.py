"""Tests for SubTaskTracker."""

from __future__ import annotations

import asyncio
import unittest

from live2d_server.master_agent.shared import SubTask
from live2d_server.master_agent.task_tracker import SubTaskTracker


class SubTaskTrackerTest(unittest.IsolatedAsyncioTestCase):
    async def _make_subtask(self, conversation_id: str = "conv-1") -> SubTask:
        return SubTask.new(
            conversation_id=conversation_id,
            agent_brand="codex",
            session_id="sess-1",
            prompt="hello",
            cwd="/tmp",
        )

    async def test_create_get_round_trip(self) -> None:
        tracker = SubTaskTracker()
        subtask = await self._make_subtask()
        await tracker.create(subtask)
        fetched = await tracker.get(subtask.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, subtask.id)
        self.assertEqual(fetched.status, "pending")

    async def test_update_status_marks_terminal_and_releases_waiters(self) -> None:
        tracker = SubTaskTracker()
        subtask = await self._make_subtask()
        await tracker.create(subtask)
        await tracker.update_status(subtask.id, status="running")
        running = await tracker.get(subtask.id)
        self.assertEqual(running.status, "running")

        async def drain():
            events = []
            async for event in tracker.wait_with_progress(subtask.id, timeout_sec=2.0):
                events.append(event)
            return events

        consumer = asyncio.create_task(drain())
        await asyncio.sleep(0.05)
        await tracker.append_event(subtask.id, {"type": "text", "content": "hi"})
        await tracker.update_status(subtask.id, status="done", final_text="hi")
        events = await consumer
        self.assertTrue(any(e.get("type") == "text" for e in events))
        self.assertTrue(any(e.get("type") == "subtask_status" for e in events))

    async def test_list_for_conversation_filters_and_sorts(self) -> None:
        tracker = SubTaskTracker()
        a = await self._make_subtask("conv-a")
        b = await self._make_subtask("conv-b")
        c = await self._make_subtask("conv-a")
        await tracker.create(a)
        await tracker.create(b)
        await tracker.create(c)
        items = await tracker.list_for_conversation("conv-a")
        self.assertEqual([t.id for t in items], [a.id, c.id])

    async def test_evicts_old_terminal_subtasks(self) -> None:
        tracker = SubTaskTracker(per_conversation_limit=2)
        first = await self._make_subtask()
        await tracker.create(first)
        await tracker.update_status(first.id, status="done")
        second = await self._make_subtask()
        await tracker.create(second)
        await tracker.update_status(second.id, status="done")
        third = await self._make_subtask()
        await tracker.create(third)
        remaining = await tracker.list_for_conversation("conv-1")
        ids = {t.id for t in remaining}
        # The oldest terminal one should have been evicted.
        self.assertNotIn(first.id, ids)
        self.assertIn(third.id, ids)


if __name__ == "__main__":
    unittest.main()
