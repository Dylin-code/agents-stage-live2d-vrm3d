"""Tool implementations for the master agent."""

from .abort_approval_tools import AbortSessionTool, ApprovePendingTool
from .claude_session_tools import ClaudeNewSessionTool, ClaudeSendPromptTool
from .codex_session_tools import CodexNewSessionTool, CodexSendPromptTool
from .fs_tools import BrowseDirectoriesTool
from .git_tools import ListBranchesTool, SwitchBranchTool
from .model_catalog_tool import ListAvailableModelsTool
from .project_tools import (
    ListProjectsTool,
    RegisterProjectTool,
    ResolveProjectTool,
)
from .report_tool import REPORT_TOOL_NAME, ReportToUserTool
from .session_query_tools import (
    GetSessionConversationTool,
    ListHistorySessionsTool,
    ListSessionsTool,
    ListSubTasksTool,
    QuerySessionStatusTool,
    SearchSessionsTool,
)
from .subtask_tools import WaitForSubTaskTool

__all__ = [
    "CodexNewSessionTool",
    "CodexSendPromptTool",
    "ClaudeNewSessionTool",
    "ClaudeSendPromptTool",
    "ReportToUserTool",
    "REPORT_TOOL_NAME",
    "QuerySessionStatusTool",
    "ListSessionsTool",
    "ListSubTasksTool",
    "ListHistorySessionsTool",
    "GetSessionConversationTool",
    "SearchSessionsTool",
    "WaitForSubTaskTool",
    "AbortSessionTool",
    "ApprovePendingTool",
    "ListBranchesTool",
    "SwitchBranchTool",
    "ListAvailableModelsTool",
    "BrowseDirectoriesTool",
    "ListProjectsTool",
    "ResolveProjectTool",
    "RegisterProjectTool",
]
