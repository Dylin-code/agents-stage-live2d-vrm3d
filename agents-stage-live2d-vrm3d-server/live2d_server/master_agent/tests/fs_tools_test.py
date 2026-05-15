"""Tests for browse_directories tool (master-agent cwd discovery)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from live2d_server.master_agent.contracts.tool_port import ToolContext
from live2d_server.master_agent.task_tracker import SubTaskTracker
from live2d_server.master_agent.tools.fs_tools import BrowseDirectoriesTool


def _ctx(args: dict) -> ToolContext:
    return ToolContext(
        conversation_id="c1",
        arguments=args,
        services=SimpleNamespace(
            agent_provider=None, bridge_service=None,
            task_tracker=SubTaskTracker(), loop=None,
            permit_full_access=False,
        ),
    )


class BrowseDirectoriesToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_lists_subdirectories(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "alpha").mkdir()
            (root / "beta").mkdir()
            (root / "ignored.txt").write_text("not a dir", encoding="utf-8")
            (root / "Zeta").mkdir()

            result = await BrowseDirectoriesTool().invoke(_ctx({"path": str(root)}))
            self.assertTrue(result.ok)
            names = [d["name"] for d in result.data["directories"]]
            # Files should be filtered out; dirs should be sorted case-insensitively.
            self.assertEqual(set(names), {"alpha", "beta", "Zeta"})
            self.assertEqual(names, sorted(names, key=str.casefold))
            self.assertEqual(result.data["current_path"], str(root.resolve()))

    async def test_returns_roots_when_no_path(self) -> None:
        result = await BrowseDirectoriesTool().invoke(_ctx({}))
        self.assertTrue(result.ok)
        self.assertEqual(result.data["current_path"], "")
        self.assertEqual(result.data["parent_path"], None)
        self.assertTrue(len(result.data["directories"]) >= 1)

    async def test_builds_ancestor_chain(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub = root / "a" / "b" / "c"
            sub.mkdir(parents=True)
            result = await BrowseDirectoriesTool().invoke(_ctx({"path": str(sub)}))
            self.assertTrue(result.ok)
            ancestors = result.data["ancestors"]
            # Should include the chain from filesystem root down to sub.
            ancestor_paths = [a["path"] for a in ancestors]
            self.assertIn(str(sub.resolve()), ancestor_paths)
            self.assertEqual(ancestors[-1]["path"], str(sub.resolve()))

    async def test_nonexistent_path_fails_gracefully(self) -> None:
        result = await BrowseDirectoriesTool().invoke(
            _ctx({"path": "/definitely/does/not/exist/zzz-master-agent-test"}),
        )
        self.assertFalse(result.ok)
        self.assertIn("not found", result.error)

    async def test_file_path_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "afile.txt"
            file_path.write_text("hi", encoding="utf-8")
            result = await BrowseDirectoriesTool().invoke(
                _ctx({"path": str(file_path)}),
            )
            self.assertFalse(result.ok)
            self.assertIn("not a directory", result.error)

    async def test_truncates_when_too_many_entries(self) -> None:
        """If a directory contains more than the cap, return only that
        many plus a truncated flag so the LLM knows to drill deeper."""
        from live2d_server.master_agent.tools import fs_tools

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(fs_tools._MAX_DIRECTORY_ENTRIES + 5):
                (root / f"dir-{i:04d}").mkdir()
            result = await BrowseDirectoriesTool().invoke(_ctx({"path": str(root)}))
            self.assertTrue(result.ok)
            self.assertEqual(
                len(result.data["directories"]),
                fs_tools._MAX_DIRECTORY_ENTRIES,
            )
            self.assertTrue(result.data["truncated"])

    async def test_expands_user_home(self) -> None:
        # ``~`` should resolve to the user's home; the directory should
        # exist on any reasonable test environment.
        result = await BrowseDirectoriesTool().invoke(_ctx({"path": "~"}))
        self.assertTrue(result.ok)
        home_path = result.data["current_path"]
        self.assertTrue(Path(home_path).exists())
        self.assertNotIn("~", home_path)


if __name__ == "__main__":
    unittest.main()
