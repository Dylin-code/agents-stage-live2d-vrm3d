"""Master agent package — single point of contact that orchestrates codex/claude CLIs as tools."""

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


def __getattr__(name: str):
    if name == "master_agent_router":
        from .api import router

        return router
    if name == "MasterAgentService":
        from .service import MasterAgentService

        return MasterAgentService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
