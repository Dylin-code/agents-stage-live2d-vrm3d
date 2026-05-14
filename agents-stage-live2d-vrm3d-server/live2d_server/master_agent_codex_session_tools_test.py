import unittest
from types import SimpleNamespace

from live2d_server.master_agent.contracts.tool_port import ToolContext
from live2d_server.master_agent.tools.codex_session_tools import (
    CodexNewSessionTool,
    _resolve_default_permission_mode,
)


def _ctx(permit_full_access: bool = False) -> ToolContext:
    return ToolContext(
        conversation_id="conversation-1",
        arguments={},
        services=SimpleNamespace(permit_full_access=permit_full_access),
    )


class MasterAgentCodexSessionToolsTest(unittest.TestCase):
    def test_codex_default_permission_mode_uses_full_auto_path(self) -> None:
        self.assertEqual(_resolve_default_permission_mode(_ctx(), "codex", None), "default")
        self.assertEqual(_resolve_default_permission_mode(_ctx(), "codex", "default"), "default")

    def test_codex_full_without_gate_downgrades_to_default(self) -> None:
        self.assertEqual(_resolve_default_permission_mode(_ctx(), "codex", "full"), "default")

    def test_codex_full_with_gate_is_honored(self) -> None:
        self.assertEqual(_resolve_default_permission_mode(_ctx(permit_full_access=True), "codex", "full"), "full")

    def test_claude_default_permission_mode_stays_auto(self) -> None:
        self.assertEqual(_resolve_default_permission_mode(_ctx(), "claude", None), "auto")

    def test_codex_new_session_schema_allows_default_permission_mode(self) -> None:
        permission_schema = CodexNewSessionTool.parameters_schema["properties"]["permission_mode"]
        self.assertIn("default", permission_schema["enum"])


if __name__ == "__main__":
    unittest.main()
