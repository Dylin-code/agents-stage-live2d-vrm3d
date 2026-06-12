"""Tests for TUI tmux session manager helpers."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from live2d_server.tui_session_manager import TuiSessionManager


class TuiSessionManagerTest(unittest.TestCase):
    def test_capture_pane_reads_alternate_screen(self) -> None:
        manager = TuiSessionManager(sidecar_path=Path("unused.json"))
        calls: list[list[str]] = []

        def fake_run(args, *, check=True):
            calls.append(args)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="screen", stderr="")

        with patch.object(manager, "has_session", return_value=True):
            with patch("live2d_server.tui_session_manager._run_tmux", side_effect=fake_run):
                text = manager.capture_pane("tui-12345678", history_lines=80)

        self.assertEqual(text, "screen")
        self.assertEqual(calls[0], [
            "capture-pane", "-t", "tui-12345678", "-a", "-p", "-S", "-80",
        ])


if __name__ == "__main__":
    unittest.main()
