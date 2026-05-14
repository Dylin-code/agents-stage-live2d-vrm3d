"""Parse a raw model reply into a structured tool call (prompt-mode fallback).

Used when the underlying provider doesn't support native tool calling
(``MASTER_AGENT_LLM_TOOL_MODE=prompt``). The system prompt instructs the
model to emit:

    {"tool": "<name>", "args": {...}}

…either as the entire response or inside a ``\u0060\u0060\u0060json`` fence. This module
extracts the first such object, tolerating:

- leading natural-language preamble
- code fences
- truncated JSON (auto-close strings + braces, then retry)

Adapted from Kokoro-Link's ``application/services/tool_call_parser.py``.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional

from .contracts.llm_port import ToolCall

_TOOL_CALL_HINT_RE = re.compile(r'\{\s*"tool"\s*:\s*"', re.DOTALL)


def parse_tool_call(raw: str) -> Optional[ToolCall]:
    if not raw or not raw.strip():
        return None
    obj = _extract_first_object(raw)
    if obj is None:
        obj = _repair_truncated_object(raw)
    if obj is None:
        return None
    name = obj.get("tool")
    if not isinstance(name, str) or not name.strip():
        return None
    args_raw = obj.get("args", {})
    if not isinstance(args_raw, dict):
        return None
    return ToolCall(id=uuid.uuid4().hex, name=name.strip(), arguments=args_raw)


def looks_like_tool_call_attempt(raw: str) -> bool:
    if not raw:
        return False
    return _TOOL_CALL_HINT_RE.search(raw) is not None


def _extract_first_object(text: str) -> Optional[dict[str, Any]]:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def _repair_truncated_object(text: str) -> Optional[dict[str, Any]]:
    if not looks_like_tool_call_attempt(text):
        return None
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
    if depth <= 0 and not in_string:
        return None
    suffix = ""
    if in_string:
        if text.endswith("\\"):
            suffix += "\\"
        suffix += '"'
    candidate = text[start:] + suffix
    candidate = candidate.rstrip()
    if candidate.endswith(","):
        candidate = candidate[:-1]
    candidate += "}" * max(depth, 0)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
