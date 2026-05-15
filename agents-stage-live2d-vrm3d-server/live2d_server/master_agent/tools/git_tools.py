"""Git tools — list branches, switch branch within a worker's cwd."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any

from ..contracts.tool_port import ToolContext, ToolPort, ToolResult

_LOGGER = logging.getLogger(__name__)


def _resolve_cwd(ctx: ToolContext) -> str | None:
    cwd = str(ctx.arguments.get("cwd") or ctx.default_cwd or "").strip()
    return cwd or None


def _run_git_sync(cwd: str, args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Sync git invocation — wrapped via ``asyncio.to_thread`` so the
    event loop stays free during the (possibly slow) subprocess.
    Mirrors :func:`session_bridge_api._run_git_command` but kept local
    to avoid pulling the API module into the master_agent dependency graph."""
    return subprocess.run(
        ["git", "-C", cwd, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class ListBranchesTool(ToolPort):
    name = "list_branches"
    description = (
        "List local git branches in a working directory. Pass cwd OR rely "
        "on the conversation's default_cwd. Returns the current branch and "
        "the full local-branch list."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "cwd": {"type": "string", "description": "Repo path; falls back to default_cwd."},
        },
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        cwd = _resolve_cwd(ctx)
        if not cwd:
            return ToolResult.failure("cwd is required (no default_cwd set)")
        try:
            current = await asyncio.to_thread(_run_git_sync, cwd, ["branch", "--show-current"], 5.0)
            listing = await asyncio.to_thread(
                _run_git_sync, cwd, ["branch", "--list", "--format=%(refname:short)"], 10.0,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.failure(f"git timed out in {cwd}")
        except FileNotFoundError:
            return ToolResult.failure("git executable not found on PATH")
        if current.returncode != 0:
            return ToolResult.failure(
                f"git branch --show-current failed: {(current.stderr or '').strip()}"
            )
        if listing.returncode != 0:
            return ToolResult.failure(
                f"git branch --list failed: {(listing.stderr or '').strip()}"
            )
        current_branch = (current.stdout or "").strip()
        branches = [
            line.strip() for line in (listing.stdout or "").splitlines() if line.strip()
        ]
        return ToolResult.success(
            output_text=f"{len(branches)} branch(es) in {cwd}; current={current_branch or '-'}",
            data={"cwd": cwd, "current": current_branch, "branches": branches},
        )


class SwitchBranchTool(ToolPort):
    name = "switch_git_branch"
    description = (
        "Switch the working directory to a given git branch. Tries `git switch` "
        "first and falls back to `git checkout` when the local git is older. "
        "Won't create new branches — pass an existing branch name."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "branch": {"type": "string", "description": "Existing branch name to check out."},
            "cwd": {"type": "string", "description": "Repo path; falls back to default_cwd."},
        },
        "required": ["branch"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        branch = str(ctx.arguments.get("branch") or "").strip()
        if not branch:
            return ToolResult.failure("branch is required")
        cwd = _resolve_cwd(ctx)
        if not cwd:
            return ToolResult.failure("cwd is required (no default_cwd set)")
        try:
            switch = await asyncio.to_thread(_run_git_sync, cwd, ["switch", branch], 20.0)
        except subprocess.TimeoutExpired:
            return ToolResult.failure(f"git switch {branch} timed out in {cwd}")
        except FileNotFoundError:
            return ToolResult.failure("git executable not found on PATH")
        if switch.returncode == 0:
            return ToolResult.success(
                output_text=f"switched to {branch} in {cwd}",
                data={"cwd": cwd, "branch": branch, "via": "switch"},
            )
        # Fall back to checkout for older git versions.
        try:
            checkout = await asyncio.to_thread(_run_git_sync, cwd, ["checkout", branch], 20.0)
        except subprocess.TimeoutExpired:
            return ToolResult.failure(f"git checkout {branch} timed out in {cwd}")
        if checkout.returncode == 0:
            return ToolResult.success(
                output_text=f"switched to {branch} in {cwd} (via checkout fallback)",
                data={"cwd": cwd, "branch": branch, "via": "checkout"},
            )
        err = (checkout.stderr or switch.stderr or "").strip() or "unknown git error"
        return ToolResult.failure(f"failed to switch branch: {err}")
