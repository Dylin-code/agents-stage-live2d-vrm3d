"""Tests for ToolOrchestrator."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from live2d_server.master_agent.contracts.tool_port import ToolContext, ToolPort, ToolResult
from live2d_server.master_agent.tool_orchestrator import ToolOrchestrator
from live2d_server.master_agent.tool_registry import InMemoryToolRegistry


class _OkTool(ToolPort):
    name = "ok"
    description = "ok"
    parameters_schema = {"type": "object"}

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        return ToolResult.success("ok", data={"value": ctx.arguments.get("v")})


class _BoomTool(ToolPort):
    name = "boom"
    description = "boom"
    parameters_schema = {"type": "object"}

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        raise RuntimeError("kaboom")


def _make_ctx(args: dict, conversation_id: str = "c1") -> ToolContext:
    return ToolContext(
        conversation_id=conversation_id,
        arguments=args,
        services=SimpleNamespace(
            agent_provider=None, bridge_service=None, task_tracker=None, loop=None,
        ),
    )


class ToolOrchestratorTest(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_tool_returns_failure(self) -> None:
        registry = InMemoryToolRegistry()
        orch = ToolOrchestrator(registry)
        result = await orch.invoke("missing", _make_ctx({}))
        self.assertFalse(result.ok)
        self.assertIn("unknown tool", result.error)

    async def test_isolates_tool_exception(self) -> None:
        registry = InMemoryToolRegistry()
        registry.register(_BoomTool())
        orch = ToolOrchestrator(registry)
        result = await orch.invoke("boom", _make_ctx({}))
        self.assertFalse(result.ok)
        self.assertIn("kaboom", result.error)

    async def test_blocks_third_identical_call(self) -> None:
        registry = InMemoryToolRegistry()
        registry.register(_OkTool())
        orch = ToolOrchestrator(registry)
        first = await orch.invoke("ok", _make_ctx({"v": 1}))
        second = await orch.invoke("ok", _make_ctx({"v": 1}))
        third = await orch.invoke("ok", _make_ctx({"v": 1}))
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertFalse(third.ok)
        self.assertIn("identical", third.error)

    async def test_distinguishes_different_args(self) -> None:
        registry = InMemoryToolRegistry()
        registry.register(_OkTool())
        orch = ToolOrchestrator(registry)
        for v in (1, 2, 3, 4):
            result = await orch.invoke("ok", _make_ctx({"v": v}))
            self.assertTrue(result.ok, f"call v={v} should succeed")


if __name__ == "__main__":
    unittest.main()
