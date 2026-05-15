"""Expose the per-brand model catalog so the master agent can validate
model choices before passing them to ``*_new_session`` / ``*_send_prompt``.

Backed by :class:`AgentProviderRouter.brand_catalog` so the list stays
in sync with the legacy ``/api/session-bridge/agent/brands`` endpoint.
"""

from __future__ import annotations

from typing import Any

from ..contracts.tool_port import ToolContext, ToolPort, ToolResult


class ListAvailableModelsTool(ToolPort):
    name = "list_available_models"
    description = (
        "Return the catalog of brands and the model ids each supports, "
        "plus the platform-default permission mode. Call this when the "
        "user asks for a specific model or you need to pick one — passing "
        "an unknown model id to *_new_session may fall back to provider "
        "defaults silently."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "agent_brand": {
                "type": "string",
                "enum": ["codex", "claude"],
                "description": "Optional: narrow the response to one brand.",
            },
        },
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        brand_filter = ctx.arguments.get("agent_brand")
        if brand_filter is not None:
            brand_filter = str(brand_filter).strip().lower() or None
        if brand_filter and brand_filter not in ("codex", "claude"):
            return ToolResult.failure("agent_brand must be 'codex' or 'claude'")
        # AgentProviderRouter exposes brand_catalog as a static method.
        try:
            catalog: list[dict[str, Any]] = ctx.services.agent_provider.brand_catalog()
        except Exception as exc:  # noqa: BLE001 — degrade to failure
            return ToolResult.failure(f"failed to read brand catalog: {exc}")
        if brand_filter:
            catalog = [entry for entry in catalog if entry.get("brand") == brand_filter]
        summary_lines = [
            f"{entry.get('brand')}: {len(entry.get('models', []))} model(s); "
            f"default_permission_mode={entry.get('default_permission_mode', '?')}"
            for entry in catalog
        ]
        return ToolResult.success(
            output_text="; ".join(summary_lines) or "no brands available",
            data={"brands": catalog},
        )
