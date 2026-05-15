"""report_to_user — terminal tool that ends the hop loop.

The master agent loop checks the tool name; when it matches
:data:`REPORT_TOOL_NAME`, the orchestrator emits the ``final_text``
event and breaks out of the loop. No actual side-effect inside ``invoke``;
the tool just echoes the args back so the orchestrator has structured
output to forward.
"""

from __future__ import annotations

from ..contracts.tool_port import ToolContext, ToolPort, ToolResult

REPORT_TOOL_NAME = "report_to_user"


class ReportToUserTool(ToolPort):
    name = REPORT_TOOL_NAME
    description = (
        "End the current run and deliver a final message to the user. Call this "
        "as the LAST tool every turn. Include a brief summary of what was done "
        "and (if relevant) which subtasks are still running so the user knows "
        "what to expect."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Final message rendered to the user.",
            },
        },
        "required": ["text"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        text = str(ctx.arguments.get("text") or "").strip()
        if not text:
            return ToolResult.failure("text is required")
        return ToolResult.success(output_text=text, data={"final_text": text})
