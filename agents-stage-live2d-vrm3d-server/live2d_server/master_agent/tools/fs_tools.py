"""Filesystem discovery tools for the master agent.

``browse_directories`` mirrors the legacy ``/api/session-bridge/fs/directories``
endpoint that powers the SessionStage cwd picker. The LLM uses it to
resolve user-mentioned folders ("the foo project on my desktop") to
absolute paths it can pass to ``*_new_session`` as ``cwd``.

All disk I/O runs in :func:`asyncio.to_thread` so the event loop stays
responsive even when the user is on a slow network drive.
"""

from __future__ import annotations

import asyncio
import logging
import platform
from pathlib import Path
from typing import Any

from ..contracts.tool_port import ToolContext, ToolPort, ToolResult

_LOGGER = logging.getLogger(__name__)

# Hard ceiling so we don't fan out a list of thousands of entries into
# LLM context when the user accidentally points at ``C:/Windows``.
_MAX_DIRECTORY_ENTRIES = 200


class BrowseDirectoriesTool(ToolPort):
    name = "browse_directories"
    description = (
        "List subdirectories under a path so you can resolve a "
        "user-mentioned folder (e.g. \"在桌面的 my-repo\") to the absolute "
        "path needed for *_new_session's cwd. Omit ``path`` to get the "
        "filesystem roots (drive letters on Windows, \"/\" on Unix). "
        "Returns the resolved current_path, parent_path, list of "
        "subdirectories (name + absolute path), and ancestor chain so "
        "you can walk up if needed. Capped at 200 entries per call — "
        "search a more specific subdir if you hit the limit."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Directory to inspect. Supports ``~`` expansion. "
                    "Omit to list drive roots / filesystem root."
                ),
            },
        },
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        raw = ctx.arguments.get("path")
        path_str = str(raw or "").strip()
        try:
            payload = await asyncio.to_thread(_browse_directories_blocking, path_str)
        except PermissionError as exc:
            return ToolResult.failure(f"permission denied: {exc}")
        except FileNotFoundError as exc:
            return ToolResult.failure(f"path not found: {exc}")
        except NotADirectoryError as exc:
            return ToolResult.failure(f"not a directory: {exc}")
        except OSError as exc:
            return ToolResult.failure(f"cannot list directory: {exc}")
        current = payload.get("current_path") or "(roots)"
        count = len(payload.get("directories", []))
        truncated_note = " (truncated)" if payload.get("truncated") else ""
        return ToolResult.success(
            output_text=f"{count} entry(ies) under {current}{truncated_note}",
            data=payload,
        )


# ---------------------------------------------------------------------------
# Sync helpers (run inside asyncio.to_thread). Mirrored from
# session_bridge_api._normalize_directory_browse_path /
# _list_directory_roots / _build_directory_ancestors / _list_subdirectories.
# Kept local to avoid pulling the API module into the master_agent
# dependency graph.
# ---------------------------------------------------------------------------


def _browse_directories_blocking(path_str: str) -> dict[str, Any]:
    target = _normalize_browse_path(path_str)
    if target is None:
        return {
            "current_path": "",
            "parent_path": None,
            "directories": _list_roots(),
            "ancestors": [],
            "truncated": False,
        }
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_dir():
        raise NotADirectoryError(str(target))
    directories, truncated = _list_subdirectories(target)
    parent_path = None if target.parent == target else str(target.parent)
    return {
        "current_path": str(target),
        "parent_path": parent_path,
        "directories": directories,
        "ancestors": _build_ancestors(target),
        "truncated": truncated,
    }


def _normalize_browse_path(raw: str) -> Path | None:
    text = raw.strip()
    if not text:
        return None
    # On Windows, ``D:`` (no trailing slash) refers to the current dir on
    # drive D, not the drive root. Normalize so users can pass either.
    if platform.system() == "Windows" and len(text) == 2 and text[1] == ":":
        text = f"{text}\\"
    return Path(text).expanduser().resolve(strict=False)


def _list_roots() -> list[dict[str, str]]:
    if platform.system() == "Windows":
        entries: list[dict[str, str]] = []
        for code in range(ord("A"), ord("Z") + 1):
            root = Path(f"{chr(code)}:/")
            if root.exists():
                entries.append({"name": str(root), "path": str(root)})
        if entries:
            return entries
    return [{"name": "/", "path": "/"}]


def _build_ancestors(path: Path) -> list[dict[str, str]]:
    chain: list[Path] = []
    current = path
    while True:
        chain.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    chain.reverse()
    return [
        {"name": item.name or item.anchor or str(item), "path": str(item)}
        for item in chain
    ]


def _list_subdirectories(path: Path) -> tuple[list[dict[str, str]], bool]:
    children = sorted(path.iterdir(), key=lambda c: c.name.casefold())
    directories: list[dict[str, str]] = []
    truncated = False
    for child in children:
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        if len(directories) >= _MAX_DIRECTORY_ENTRIES:
            truncated = True
            break
        directories.append({
            "name": child.name or str(child),
            "path": str(child.resolve(strict=False)),
        })
    return directories, truncated
