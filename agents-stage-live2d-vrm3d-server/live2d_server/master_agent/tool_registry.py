"""Tool registry (mirrors Kokoro-Link InMemoryToolRegistry).

The orchestrator looks tools up by name. Registration is by reference,
so the same ToolPort instance can be reused across invocations.
"""

from __future__ import annotations

from typing import Optional

from .contracts.llm_port import ToolSchema
from .contracts.tool_port import ToolPort


class InMemoryToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolPort] = {}

    def register(self, tool: ToolPort) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[ToolPort]:
        return self._tools.get(name)

    def all(self) -> list[ToolPort]:
        return list(self._tools.values())

    def schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters_schema,
            )
            for tool in self._tools.values()
        ]
