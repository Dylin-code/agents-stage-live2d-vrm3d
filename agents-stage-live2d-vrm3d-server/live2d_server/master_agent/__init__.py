"""Master agent package — single point of contact that orchestrates codex/claude CLIs as tools."""

from .api import router as master_agent_router
from .service import MasterAgentService
from .shared import (
    MasterAgentChatRequest,
    MasterAgentConversation,
    MasterEvent,
    SubTask,
    SubTaskStatus,
)

__all__ = [
    "master_agent_router",
    "MasterAgentService",
    "MasterAgentChatRequest",
    "MasterAgentConversation",
    "MasterEvent",
    "SubTask",
    "SubTaskStatus",
]
