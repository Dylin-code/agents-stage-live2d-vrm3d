"""Tests for tmux-backed TUI automation helpers."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from live2d_server.tui_automation import (
    TuiAutomationService,
    command_base_name,
    compute_delta,
    ensure_command_allowed,
    normalize_key,
    normalize_terminal_text,
)
from live2d_server.tui_session_manager import TuiBridgeError


class _FakeManager:
    def __init__(self, captures: list[str] | None = None) -> None:
        self.captures = list(captures or [])
        self.sent_literals: list[tuple[str, str]] = []
        self.sent_keys: list[tuple[str, str]] = []

    def list_sessions(self):
        return []

    def create_session(self, *, label: str = "", cwd: str = "", command: str = ""):
        return SimpleNamespace(
            session_id="tui-12345678",
            label=label,
            cwd=cwd,
            command=command,
            created_at=1.0,
            attached_clients=0,
            windows=1,
            last_activity_at=1.0,
        )

    def send_literal(self, session_id: str, text: str) -> None:
        self.sent_literals.append((session_id, text))

    def send_key(self, session_id: str, key: str) -> None:
        self.sent_keys.append((session_id, key))

    def capture_pane(self, session_id: str, *, history_lines: int = 200) -> str:
        if self.captures:
            return self.captures.pop(0)
        return "stable"


class TuiAutomationServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_capture_delta_uses_previous_tail_for_same_session(self) -> None:
        manager = _FakeManager([
            "prompt\nold output\n",
            "prompt\nold output\nnew output\n",
        ])
        service = TuiAutomationService(manager)
        with _ready_env():
            first = service.capture_screen("tui-12345678")
            second = service.capture_screen("tui-12345678")
        self.assertEqual(first.delta_text, "")
        self.assertEqual(second.delta_text, "new output")
        self.assertTrue(second.delta_matched_previous_tail)

    async def test_capture_delta_reports_output_after_empty_baseline(self) -> None:
        manager = _FakeManager([
            "",
            "prompt\nnew output\n",
        ])
        service = TuiAutomationService(manager)
        with _ready_env():
            first = service.capture_screen("tui-12345678")
            second = service.capture_screen("tui-12345678")
        self.assertEqual(first.delta_text, "")
        self.assertEqual(second.delta_text, "prompt\nnew output")
        self.assertFalse(second.delta_matched_previous_tail)

    async def test_wait_until_stable_updates_tail_once_at_end(self) -> None:
        manager = _FakeManager([
            "loading",
            "done",
            "done",
            "done",
            "done",
        ])
        service = TuiAutomationService(manager)
        with _ready_env():
            result = await service.wait_until_stable(
                "tui-12345678",
                timeout_sec=1.0,
                stable_for_sec=0.02,
                min_wait_sec=0.0,
                poll_interval_sec=0.01,
            )
        self.assertTrue(result.stable)
        self.assertEqual(result.text, "done")

    async def test_wait_until_stable_does_not_finish_on_empty_screen_by_default(self) -> None:
        manager = _FakeManager([
            "",
            "",
            "",
            "ready",
            "ready",
            "ready",
            "ready",
        ])
        service = TuiAutomationService(manager)
        with _ready_env():
            result = await service.wait_until_stable(
                "tui-12345678",
                timeout_sec=1.0,
                stable_for_sec=0.02,
                min_wait_sec=0.0,
                poll_interval_sec=0.01,
            )
        self.assertTrue(result.stable)
        self.assertEqual(result.text, "ready")

    async def test_send_input_normalizes_submit_key(self) -> None:
        manager = _FakeManager()
        service = TuiAutomationService(manager)
        with _ready_env():
            service.send_input("tui-12345678", "/help", submit=True)
        self.assertEqual(manager.sent_literals, [("tui-12345678", "/help")])
        self.assertEqual(manager.sent_keys, [("tui-12345678", "Enter")])

    async def test_send_key_normalizes_shift_tab(self) -> None:
        manager = _FakeManager()
        service = TuiAutomationService(manager)
        with _ready_env():
            service.send_key("tui-12345678", "shift+tab")
        self.assertEqual(manager.sent_keys, [("tui-12345678", "BTab")])

    async def test_create_session_rejects_disallowed_command_by_default(self) -> None:
        manager = _FakeManager()
        service = TuiAutomationService(manager)
        with _ready_env():
            with self.assertRaises(TuiBridgeError):
                service.create_session(cwd="/repo", command="cmd.exe")

    async def test_create_session_allows_default_agent_commands(self) -> None:
        manager = _FakeManager()
        service = TuiAutomationService(manager)
        with _ready_env():
            info = service.create_session(cwd="/repo", command="claude")
        self.assertEqual(info.command, "claude")

    async def test_create_session_rejects_empty_default_shell_by_default(self) -> None:
        manager = _FakeManager()
        service = TuiAutomationService(manager)
        with _ready_env():
            with self.assertRaises(TuiBridgeError):
                service.create_session(cwd="/repo", command="")


class TuiAutomationHelpersTest(unittest.TestCase):
    def test_compute_delta_falls_back_to_overlap(self) -> None:
        delta, matched = compute_delta("abcXYZ", "XYZ123")
        self.assertTrue(matched)
        self.assertEqual(delta, "123")

    def test_compute_delta_with_empty_previous_returns_current(self) -> None:
        delta, matched = compute_delta("", "new")
        self.assertFalse(matched)
        self.assertEqual(delta, "new")

    def test_normalize_terminal_text_strips_ansi_and_trailing_blanks(self) -> None:
        self.assertEqual(
            normalize_terminal_text("\x1b[31mred\x1b[0m   \r\n\n"),
            "red",
        )

    def test_normalize_key_aliases_common_names(self) -> None:
        self.assertEqual(normalize_key("ctrl-c"), "C-c")
        self.assertEqual(normalize_key("esc"), "Escape")

    def test_normalize_key_supports_modifier_combos(self) -> None:
        self.assertEqual(normalize_key("shift+tab"), "BTab")
        self.assertEqual(normalize_key("Shift-Tab"), "BTab")
        self.assertEqual(normalize_key("ctrl+u"), "C-u")
        self.assertEqual(normalize_key("alt+x"), "M-x")
        self.assertEqual(normalize_key("ctrl+shift+left"), "C-S-Left")

    def test_command_base_name_handles_paths_and_exe(self) -> None:
        self.assertEqual(command_base_name(r"C:\Tools\codex.exe --help"), "codex")
        self.assertEqual(command_base_name("claude --dangerously-skip-permissions"), "claude")

    def test_allowlist_can_be_customized(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "MASTER_AGENT_TUI_ALLOWED_COMMANDS": "claude,codex,pwsh",
                "MASTER_AGENT_TUI_ALLOW_ANY_COMMAND": "false",
            },
            clear=False,
        ):
            ensure_command_allowed("pwsh -NoLogo")

    def test_allow_any_command_flag_bypasses_allowlist(self) -> None:
        with patch.dict(
            "os.environ",
            {"MASTER_AGENT_TUI_ALLOW_ANY_COMMAND": "true"},
            clear=False,
        ):
            ensure_command_allowed("cmd.exe")


@contextmanager
def _ready_env():
    with patch.dict("os.environ", {"MASTER_AGENT_TUI_TOOLS_ENABLED": "true"}):
        with patch("live2d_server.tui_automation.tmux_available", return_value=True):
            yield


if __name__ == "__main__":
    unittest.main()
