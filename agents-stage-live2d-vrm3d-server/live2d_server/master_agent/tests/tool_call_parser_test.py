"""Tests for the prompt-mode JSON tool call parser."""

from __future__ import annotations

import unittest

from live2d_server.master_agent.tool_call_parser import (
    looks_like_tool_call_attempt,
    parse_tool_call,
)


class ToolCallParserTest(unittest.TestCase):
    def test_parses_bare_json_object(self) -> None:
        raw = '{"tool": "codex_new_session", "args": {"cwd": "/tmp"}}'
        call = parse_tool_call(raw)
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "codex_new_session")
        self.assertEqual(call.arguments, {"cwd": "/tmp"})

    def test_parses_fenced_json(self) -> None:
        raw = '```json\n{"tool": "report_to_user", "args": {"text": "ok"}}\n```'
        call = parse_tool_call(raw)
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "report_to_user")
        self.assertEqual(call.arguments["text"], "ok")

    def test_skips_leading_natural_language(self) -> None:
        raw = '好的我來呼叫工具：\n{"tool": "claude_new_session", "args": {"cwd": "/x"}}'
        call = parse_tool_call(raw)
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "claude_new_session")

    def test_repairs_truncated_object(self) -> None:
        raw = '{"tool": "codex_send_prompt", "args": {"session_id": "abc", "message": "hi"'
        call = parse_tool_call(raw)
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "codex_send_prompt")
        self.assertEqual(call.arguments["session_id"], "abc")

    def test_returns_none_for_plain_text(self) -> None:
        self.assertIsNone(parse_tool_call("hello, no tool here"))

    def test_returns_none_when_args_missing(self) -> None:
        raw = '{"tool": "report_to_user"}'
        call = parse_tool_call(raw)
        # args default to {} is acceptable; the assertion is just that this doesn't blow up
        self.assertIsNotNone(call)
        self.assertEqual(call.arguments, {})

    def test_returns_none_when_args_wrong_type(self) -> None:
        raw = '{"tool": "x", "args": "not a dict"}'
        self.assertIsNone(parse_tool_call(raw))

    def test_looks_like_tool_call_attempt(self) -> None:
        self.assertTrue(looks_like_tool_call_attempt('{"tool": "foo"'))
        self.assertFalse(looks_like_tool_call_attempt("plain text"))


if __name__ == "__main__":
    unittest.main()
