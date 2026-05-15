"""Project-registry tools — let the director look up cwds by name.

The registry is also injected into the system prompt at conversation
start, so most of the time the LLM can pick a cwd without calling
these tools. They exist for two cases:

- The user names a project not currently in the prompt-time snapshot
  (registry got edited mid-conversation, or aliases are non-obvious).
- The LLM wants to disambiguate a fuzzy name ("the gentleman project")
  by listing all candidates explicitly.
"""

from __future__ import annotations

import logging
from typing import Any

from ..contracts.tool_port import ToolContext, ToolPort, ToolResult

_LOGGER = logging.getLogger(__name__)


class ListProjectsTool(ToolPort):
    name = "list_projects"
    description = (
        "List all known development projects (name, cwd, aliases, "
        "services). Sourced from the user's dev-registry "
        "(~/.config/dev-registry/services.yaml) plus the master agent's "
        "override file. Use this when the user mentions a project by "
        "name and you need to map it to an absolute cwd before "
        "dispatching a worker."
    )
    parameters_schema = {
        "type": "object",
        "properties": {},
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        registry = getattr(ctx.services, "project_registry", None)
        if registry is None:
            return ToolResult.success(
                output_text="(no project registry configured)",
                data={"projects": []},
            )
        try:
            projects = registry.list_projects()
        except Exception as exc:  # noqa: BLE001 — registry IO must not kill a turn
            _LOGGER.exception("list_projects failed")
            return ToolResult.failure(f"list_projects failed: {exc}")
        items = [p.to_dict() for p in projects]
        summary = ", ".join(p["name"] for p in items) or "(none)"
        return ToolResult.success(
            output_text=f"{len(items)} project(s): {summary}",
            data={"projects": items},
        )


class ResolveProjectTool(ToolPort):
    name = "resolve_project"
    description = (
        "Look up a project by name, alias, or cwd basename and return "
        "its absolute cwd. Fuzzy match: exact > substring. Use when the "
        "user says \"派工到 kokoro\" — call resolve_project(name=\"kokoro\") "
        "and pass the returned cwd straight to *_new_session. If no "
        "match, fall back to ``browse_directories`` or ask the user."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "User-facing project name, alias, or cwd basename.",
            },
        },
        "required": ["name"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        name = str(ctx.arguments.get("name") or "").strip()
        if not name:
            return ToolResult.failure("name is required")
        registry = getattr(ctx.services, "project_registry", None)
        if registry is None:
            return ToolResult.failure("project registry is not configured")
        try:
            project = registry.resolve(name)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("resolve_project failed")
            return ToolResult.failure(f"resolve_project failed: {exc}")
        if project is None:
            return ToolResult.failure(
                f"no project matched {name!r}; try list_projects to see candidates",
            )
        return ToolResult.success(
            output_text=f"{project.name} -> {project.cwd}",
            data={"project": project.to_dict()},
        )


class RegisterProjectTool(ToolPort):
    name = "register_project"
    description = (
        "Persist a project so future conversations (and the next server "
        "restart) know its cwd without asking the user again. Call this "
        "AFTER you've confirmed the user's intent and resolved the cwd — "
        "typically right after a successful ``browse_directories`` lookup "
        "for a project the user named but ``list_projects`` didn't cover. "
        "Writes to the local override file (``config/master-agent/"
        "projects.yaml``); existing same-``name`` entries are updated in "
        "place, others are preserved. Do NOT call this speculatively for "
        "every cwd you touch — only when the user introduced a new "
        "project by name."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Project name as the user calls it (lowercase preferred, "
                    "no spaces)."
                ),
            },
            "cwd": {
                "type": "string",
                "description": "Absolute cwd the project lives at.",
            },
            "aliases": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional extra names / nicknames the user might use."
                ),
            },
            "description": {
                "type": "string",
                "description": "Optional one-line note for future you.",
            },
        },
        "required": ["name", "cwd"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        registry = getattr(ctx.services, "project_registry", None)
        if registry is None:
            return ToolResult.failure("project registry is not configured")
        name = str(ctx.arguments.get("name") or "").strip()
        cwd = str(ctx.arguments.get("cwd") or "").strip()
        if not name:
            return ToolResult.failure("name is required")
        if not cwd:
            return ToolResult.failure("cwd is required")
        aliases_raw = ctx.arguments.get("aliases") or []
        if not isinstance(aliases_raw, list):
            return ToolResult.failure("aliases must be a list of strings")
        aliases = [str(a).strip() for a in aliases_raw if a]
        description = str(ctx.arguments.get("description") or "").strip()
        try:
            project = registry.upsert_override(
                name=name,
                cwd=cwd,
                aliases=aliases,
                description=description,
            )
        except ValueError as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # noqa: BLE001 — disk IO must not kill a turn
            _LOGGER.exception("register_project failed")
            return ToolResult.failure(f"register_project failed: {exc}")
        return ToolResult.success(
            output_text=f"remembered {project.name} → {project.cwd}",
            data={"project": project.to_dict()},
        )


__all__ = ["ListProjectsTool", "ResolveProjectTool", "RegisterProjectTool"]
