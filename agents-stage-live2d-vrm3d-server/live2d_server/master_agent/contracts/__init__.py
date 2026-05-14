"""Contracts (Protocols + value objects) used across the master_agent package."""

from .llm_port import (
    ChatMessage,
    ChatModelDelta,
    ChatModelPort,
    ChatModelResult,
    ToolCall,
    ToolSchema,
)
from .tool_port import ToolContext, ToolPort, ToolResult

__all__ = [
    "ChatMessage",
    "ChatModelDelta",
    "ChatModelPort",
    "ChatModelResult",
    "ToolCall",
    "ToolSchema",
    "ToolContext",
    "ToolPort",
    "ToolResult",
]
