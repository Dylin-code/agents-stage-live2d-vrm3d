"""Claude Code CLI chat service — mirrors CodexSessionChatService for the Claude brand."""

import asyncio
import base64
import json
import logging
import math
import mimetypes
import os
import shlex
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from .session_bridge_shared import (
    PERMISSION_MODE_AUTO,
    PERMISSION_MODE_DEFAULT,
    PERMISSION_MODE_FULL,
    PERMISSION_MODE_PLAN,
    _build_session_bridge_prompt,
    _claude_model_context_window,
    _ensure_stream_reader_limit,
    _extract_message_content,
    _format_subprocess_spawn_error,
    _isolated_subprocess_kwargs,
    _kill_process_tree,
    _resolve_default_chat_cwd,
    _resolve_permission_settings,
)

logger = logging.getLogger(__name__)


class ClaudeSessionChatError(RuntimeError):
    pass


DEFAULT_CLAUDE_CLI_IDLE_TIMEOUT_SEC = 180.0
DEFAULT_CLAUDE_CLI_MAX_TIMEOUT_SEC = 1800.0
CLAUDE_CLI_IDLE_TIMEOUT_ENV = "CLAUDE_CLI_IDLE_TIMEOUT_SEC"
CLAUDE_CLI_MAX_TIMEOUT_ENV = "CLAUDE_CLI_MAX_TIMEOUT_SEC"


def _read_timeout_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    text = str(raw).strip()
    if not text:
        return default
    try:
        value = float(text)
    except ValueError:
        logger.warning("Invalid %s=%r. Fallback to default %s", name, raw, default)
        return default
    if not math.isfinite(value) or value <= 0:
        logger.warning("Non-positive %s=%r. Fallback to default %s", name, raw, default)
        return default
    return value


class ClaudeSessionChatService:
    """Wraps the `claude` CLI (Claude Code) in the same streaming bridge interface as CodexSessionChatService."""

    def __init__(
        self,
        claude_bin: str = "claude",
        idle_timeout_sec: Optional[float] = None,
        max_timeout_sec: Optional[float] = None,
        default_cwd: Optional[str] = None,
    ) -> None:
        self.claude_bin = claude_bin
        self.idle_timeout_sec = (
            float(idle_timeout_sec)
            if idle_timeout_sec is not None
            else _read_timeout_env(
                CLAUDE_CLI_IDLE_TIMEOUT_ENV, DEFAULT_CLAUDE_CLI_IDLE_TIMEOUT_SEC,
            )
        )
        self.max_timeout_sec = (
            float(max_timeout_sec)
            if max_timeout_sec is not None
            else _read_timeout_env(
                CLAUDE_CLI_MAX_TIMEOUT_ENV, DEFAULT_CLAUDE_CLI_MAX_TIMEOUT_SEC,
            )
        )
        self.default_cwd = _resolve_default_chat_cwd(default_cwd)
        self.approval_timeout_sec = 300
        self._pending_approvals: dict[str, asyncio.Future] = {}
        self._pending_approvals_lock = asyncio.Lock()
        self._approved_prefix_rules: set[tuple[str, ...]] = set()
        self._approved_prefix_rules_lock = asyncio.Lock()
        # Per-session active subprocess registry. See CodexSessionChatService for rationale.
        self._active_processes: dict[str, asyncio.subprocess.Process] = {}
        self._active_processes_lock = asyncio.Lock()

    async def _register_active_process(
        self, session_id: str, process: asyncio.subprocess.Process
    ) -> None:
        async with self._active_processes_lock:
            previous = self._active_processes.get(session_id)
            self._active_processes[session_id] = process
        if previous is not None and previous is not process and previous.returncode is None:
            try:
                previous.kill()
            except ProcessLookupError:
                pass

    async def _unregister_active_process(
        self, session_id: str, process: asyncio.subprocess.Process
    ) -> None:
        async with self._active_processes_lock:
            current = self._active_processes.get(session_id)
            if current is process:
                self._active_processes.pop(session_id, None)

    async def abort_session(self, session_id: str) -> bool:
        """Force-kill the in-flight Claude CLI subprocess for the given session."""
        key = (session_id or "").strip()
        if not key:
            return False
        async with self._active_processes_lock:
            process = self._active_processes.get(key)
        if process is None or process.returncode is not None:
            return False
        try:
            _kill_process_tree(process)
        except ProcessLookupError:
            return False
        logger.info("Claude stream aborted by user session=%s pid=%s", key, process.pid)
        return True

    # ------------------------------------------------------------------
    # Environment
    # ------------------------------------------------------------------

    @staticmethod
    def _build_claude_subprocess_env() -> dict[str, str]:
        """Spawn claude in a clean environment to avoid nested-session errors."""
        env = os.environ.copy()
        # Claude Code refuses to start inside another Claude Code session.
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        return env

    # ------------------------------------------------------------------
    # CLI command building
    # ------------------------------------------------------------------

    def _build_cli_command(
        self,
        session_id: str,
        prompt: str,
        cwd: str,
        image_paths: list[str],
        model: Optional[str],
        reasoning_effort: Optional[str],
        permission_mode: Optional[str],
        approval_policy: Optional[str],
        sandbox_mode: Optional[str],
    ) -> list[str]:
        cmd: list[str] = [self.claude_bin]
        # Non-interactive print mode with streaming JSON.
        # --verbose is required when combining --print with --output-format=stream-json.
        cmd.extend(["-p", "--verbose", "--output-format", "stream-json"])
        # Resume existing session (--session-id creates new; --resume continues existing).
        if session_id:
            cmd.extend(["--resume", session_id])
        if model:
            cmd.extend(["--model", model])
        if reasoning_effort:
            cmd.extend(["--effort", reasoning_effort])
        # Permission handling.
        self._append_permission_mode_args(
            cmd,
            permission_mode=permission_mode,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
        )
        if image_paths:
            # Use stream-json input to pass multimodal content (images + text) via stdin.
            cmd.extend(["--input-format", "stream-json"])
        else:
            # Text-only: prompt is the last positional argument.
            cmd.append(prompt)
        return cmd

    @staticmethod
    def _append_permission_mode_args(
        cmd: list[str],
        *,
        permission_mode: Optional[str],
        approval_policy: Optional[str],
        sandbox_mode: Optional[str],
    ) -> None:
        effective_mode, _, _ = _resolve_permission_settings(
            permission_mode=permission_mode,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
        )
        if effective_mode == PERMISSION_MODE_FULL:
            cmd.append("--dangerously-skip-permissions")
        elif effective_mode == PERMISSION_MODE_AUTO:
            cmd.extend(["--permission-mode", "auto"])
        elif effective_mode == PERMISSION_MODE_PLAN:
            cmd.extend(["--permission-mode", "plan"])
        else:
            cmd.extend(["--permission-mode", "bypassPermissions"])

    # ------------------------------------------------------------------
    # Multimodal stdin builder (stream-json input for images)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_multimodal_stdin(prompt: str, image_paths: list[str]) -> bytes:
        """Build stream-json stdin payload containing image blocks + text.

        Claude Code ``--input-format stream-json`` expects one JSON object per
        line on stdin with the structure::

            {"type": "user", "message": {"role": "user", "content": [...]}}

        The ``content`` array may include ``image`` blocks (base64) followed
        by a ``text`` block.
        """
        content_blocks: list[dict[str, Any]] = []
        for img_path in image_paths:
            path = Path(img_path)
            if not path.exists() or not path.is_file():
                continue
            raw = path.read_bytes()
            media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(raw).decode("ascii"),
                },
            })
        content_blocks.append({"type": "text", "text": prompt})
        message = {
            "type": "user",
            "message": {
                "role": "user",
                "content": content_blocks,
            },
        }
        return (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")

    # ------------------------------------------------------------------
    # Image support (matching Codex interface)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_data_url_image(data_url: str) -> tuple[bytes, str]:
        header, encoded = data_url.split(",", 1)
        mime = "application/octet-stream"
        if header.startswith("data:") and ";" in header:
            mime = header[5:].split(";", 1)[0] or mime
        decoded = base64.b64decode(encoded)
        extension = mimetypes.guess_extension(mime) or ".bin"
        return decoded, extension

    async def _materialize_images(
        self, session_id: str, images: list[dict[str, Any]]
    ) -> tuple[list[str], list[Path]]:
        paths: list[str] = []
        created: list[Path] = []
        if not images:
            return paths, created
        root = Path("/tmp/session-bridge-images") / f"claude-{session_id}"
        root.mkdir(parents=True, exist_ok=True)
        for idx, image in enumerate(images):
            if not isinstance(image, dict):
                continue
            source_path = str(image.get("path") or "").strip()
            if source_path:
                path = Path(source_path).expanduser()
                if path.exists() and path.is_file():
                    paths.append(str(path))
                continue
            data_url = str(image.get("data_url") or "").strip()
            if not data_url.startswith("data:"):
                continue
            try:
                content, extension = self._parse_data_url_image(data_url)
            except Exception:
                continue
            filename = str(image.get("name") or f"image-{idx}{extension}").strip()
            if "." not in filename:
                filename = f"{filename}{extension}"
            target = root / f"{uuid.uuid4().hex}-{filename}"
            target.write_bytes(content)
            created.append(target)
            paths.append(str(target))
        return paths, created

    async def _cleanup_images(self, created_paths: list[Path]) -> None:
        for path in created_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Approval — bridge-level interception (matches Codex pattern)
    # ------------------------------------------------------------------

    # Tools that require bridge approval (Bash commands, destructive ops).
    _TOOLS_REQUIRING_APPROVAL: set[str] = {"Bash", "bash"}

    async def submit_approval(
        self,
        pending_id: str,
        decision: str,
        prefix_rule: Optional[list[str]] = None,
    ) -> bool:
        pending_key = (pending_id or "").strip()
        if not pending_key:
            return False
        async with self._pending_approvals_lock:
            future = self._pending_approvals.get(pending_key)
        if future is None:
            return False
        normalized_rule = self._normalize_prefix_rule(prefix_rule)
        if decision == "allow_prefix" and normalized_rule:
            async with self._approved_prefix_rules_lock:
                self._approved_prefix_rules.add(normalized_rule)
        payload = {
            "decision": decision,
            "prefix_rule": list(normalized_rule),
        }
        if not future.done():
            future.set_result(payload)
        return True

    def _suggest_prefix_rule(self, command: str) -> list[str]:
        if not command:
            return []
        try:
            parts = shlex.split(command)
        except ValueError:
            parts = command.split()
        return parts[:2]

    @staticmethod
    def _normalize_prefix_rule(prefix_rule: Optional[list[str]]) -> tuple[str, ...]:
        if not prefix_rule:
            return tuple()
        return tuple(str(part).strip() for part in prefix_rule if str(part).strip())

    async def _is_allowed_by_prefix_rule(self, command: str) -> bool:
        try:
            parts = shlex.split(command)
        except ValueError:
            parts = command.split()
        if not parts:
            return False
        async with self._approved_prefix_rules_lock:
            for rule in self._approved_prefix_rules:
                if not rule:
                    continue
                if tuple(parts[: len(rule)]) == rule:
                    return True
        return False

    @staticmethod
    def _extract_command_for_approval(tool_name: str, arguments: Any) -> tuple[str, str]:
        """Extract command text and justification from tool arguments."""
        if not isinstance(arguments, dict):
            return "", ""
        command = arguments.get("command") or arguments.get("cmd") or ""
        if isinstance(command, list):
            cmd_text = " ".join(str(x) for x in command)
        elif isinstance(command, str):
            cmd_text = command
        else:
            cmd_text = ""
        description = str(arguments.get("description") or arguments.get("justification") or "")
        return cmd_text.strip(), description.strip()

    def _needs_bridge_approval(self, tool_use: dict[str, Any]) -> bool:
        """Check if a tool_use block requires bridge-level approval."""
        tool_name = str(tool_use.get("name") or "")
        return tool_name in self._TOOLS_REQUIRING_APPROVAL

    def _build_approval_request_event(
        self, pending_id: str, command: str, justification: str, session_id: str,
    ) -> dict[str, Any]:
        return {
            "type": "approval_request",
            "content": {
                "pending_id": pending_id,
                "command": command,
                "justification": justification,
                "suggested_prefix": self._suggest_prefix_rule(command),
                "session_id": session_id,
            },
        }

    async def _await_approval_decision(
        self,
        pending_id: str,
        future: asyncio.Future,
        process: asyncio.subprocess.Process,
    ) -> Optional[dict[str, Any]]:
        """Wait for the approval decision after approval_request has been sent.

        Returns an error event if denied/timed out, or None if approved.
        """
        try:
            result = await asyncio.wait_for(future, timeout=self.approval_timeout_sec)
        except asyncio.TimeoutError:
            await self._cleanup_pending_approval(pending_id)
            process.kill()
            await process.wait()
            raise ClaudeSessionChatError("approval timeout")
        finally:
            await self._cleanup_pending_approval(pending_id)
        decision = str((result or {}).get("decision") or "").strip()
        if decision == "deny_once":
            process.kill()
            await process.wait()
            return {"type": "error", "content": "使用者拒絕本次權限請求"}
        if decision == "allow_prefix":
            normalized = self._normalize_prefix_rule((result or {}).get("prefix_rule"))
            if normalized:
                async with self._approved_prefix_rules_lock:
                    self._approved_prefix_rules.add(normalized)
        return None

    async def _register_pending_approval(self) -> tuple[str, asyncio.Future]:
        pending_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        async with self._pending_approvals_lock:
            self._pending_approvals[pending_id] = future
        return pending_id, future

    async def _cleanup_pending_approval(self, pending_id: str) -> None:
        async with self._pending_approvals_lock:
            self._pending_approvals.pop(pending_id, None)

    # ------------------------------------------------------------------
    # Stream prompt — core
    # ------------------------------------------------------------------

    async def stream_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        cwd: Optional[str] = None,
        images: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        permission_mode: Optional[str] = None,
        approval_policy: Optional[str] = None,
        sandbox_mode: Optional[str] = None,
        plan_mode: Optional[bool] = None,
        persona_id: Optional[str] = None,
        persona_name: Optional[str] = None,
        persona_content: Optional[str] = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        session_id_value = (session_id or "").strip()
        prompt_value = (prompt or "").strip()
        if not session_id_value:
            raise ClaudeSessionChatError("session_id is required")
        if not prompt_value:
            raise ClaudeSessionChatError("message is required")

        effective_cwd = str(Path((cwd or self.default_cwd)).expanduser())
        effective_permission_mode, _, _ = _resolve_permission_settings(
            permission_mode=permission_mode,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
        )
        require_bridge_approval = effective_permission_mode not in {
            PERMISSION_MODE_FULL, PERMISSION_MODE_AUTO,
        }

        prompt_for_exec = _build_session_bridge_prompt(
            prompt_value,
            persona_id=persona_id,
            persona_name=persona_name,
            persona_content=persona_content,
            plan_mode=plan_mode,
        )
        plan_mode_fallback = bool(plan_mode)

        image_paths, created_images = await self._materialize_images(session_id_value, images or [])
        has_images = bool(image_paths)
        command = self._build_cli_command(
            session_id=session_id_value,
            prompt=prompt_for_exec,
            cwd=effective_cwd,
            image_paths=image_paths,
            model=model,
            reasoning_effort=reasoning_effort,
            permission_mode=permission_mode,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
        )

        # When images are present, build multimodal stdin payload.
        stdin_data: Optional[bytes] = None
        if has_images:
            stdin_data = self._build_multimodal_stdin(prompt_for_exec, image_paths)

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE if has_images else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=effective_cwd,
                env=self._build_claude_subprocess_env(),
                **_isolated_subprocess_kwargs(),
            )
            # Feed multimodal payload via stdin then close.
            if stdin_data and process.stdin is not None:
                process.stdin.write(stdin_data)
                await process.stdin.drain()
                process.stdin.close()
                await process.stdin.wait_closed()
            _ensure_stream_reader_limit(process.stdout)
        except FileNotFoundError as exc:
            await self._cleanup_images(created_images)
            raise ClaudeSessionChatError(f"claude cli not found: {self.claude_bin}") from exc
        except Exception as exc:
            await self._cleanup_images(created_images)
            detail = _format_subprocess_spawn_error(exc)
            raise ClaudeSessionChatError(f"claude cli failed to start: {detail}") from exc

        await self._register_active_process(session_id_value, process)
        start_mono = time.monotonic()
        last_activity_mono = start_mono
        context_emitted = False
        streamed_via_delta = False  # Track if content_block_delta streamed text for current turn
        text_emitted = False  # Track if any text was emitted (avoid result event duplication)
        # Set when claude CLI emits the terminal ``result`` event. Some CLI
        # versions don't close stdout immediately after, which used to leave
        # us spinning until ``idle_timeout``. We now break out, kill the
        # leftover process, and suppress the non-zero returncode check.
        result_terminated = False

        try:
            while True:
                now = time.monotonic()
                idle_elapsed = now - last_activity_mono
                total_elapsed = now - start_mono
                if idle_elapsed > self.idle_timeout_sec:
                    logger.warning(
                        "Claude stream idle timeout session=%s idle=%.1fs total=%.1fs",
                        session_id_value, idle_elapsed, total_elapsed,
                    )
                    _kill_process_tree(process)
                    await process.wait()
                    raise ClaudeSessionChatError("claude cli idle timeout")
                if total_elapsed > self.max_timeout_sec:
                    logger.warning(
                        "Claude stream max timeout session=%s total=%.1fs",
                        session_id_value, total_elapsed,
                    )
                    _kill_process_tree(process)
                    await process.wait()
                    raise ClaudeSessionChatError("claude cli max timeout")
                if process.stdout is None:
                    break
                try:
                    raw_line = await asyncio.wait_for(process.stdout.readline(), timeout=1.0)
                except asyncio.TimeoutError:
                    if process.returncode is not None:
                        break
                    continue
                if not raw_line:
                    if process.returncode is not None:
                        break
                    continue
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue

                last_activity_mono = time.monotonic()
                event_type = str(event.get("type") or "")

                # ---- init event → emit context ----
                if event_type == "init":
                    if not context_emitted:
                        init_model = str(event.get("model") or model or "")
                        init_ctx: dict[str, Any] = {
                            "model": init_model,
                            "effort": str(reasoning_effort or ""),
                            "approval_policy": "" if effective_permission_mode == PERMISSION_MODE_FULL else "on-request",
                            "sandbox_mode": "danger-full-access" if effective_permission_mode == PERMISSION_MODE_FULL else "workspace-write",
                            "permission_mode": effective_permission_mode,
                            "plan_mode": bool(plan_mode),
                            "plan_mode_fallback": plan_mode_fallback,
                        }
                        ctx_window = _claude_model_context_window(init_model)
                        if ctx_window > 0:
                            init_ctx["model_context_window"] = ctx_window
                        yield {"type": "context", "content": init_ctx}
                        context_emitted = True
                    continue

                # ---- system event (partial message marker) ----
                if event_type == "system":
                    # Includes subtype like "init", "result" etc. — mostly ignored.
                    subtype = str(event.get("subtype") or "")
                    if subtype == "init" and not context_emitted:
                        sys_model = str(event.get("model") or model or "")
                        sys_ctx: dict[str, Any] = {
                            "model": sys_model,
                            "effort": str(reasoning_effort or ""),
                            "permission_mode": effective_permission_mode,
                            "plan_mode": bool(plan_mode),
                            "plan_mode_fallback": plan_mode_fallback,
                        }
                        ctx_window = _claude_model_context_window(sys_model)
                        if ctx_window > 0:
                            sys_ctx["model_context_window"] = ctx_window
                        yield {"type": "context", "content": sys_ctx}
                        context_emitted = True
                    continue

                # ---- content_block_delta (real-time streaming) ----
                if event_type == "content_block_delta":
                    delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
                    delta_type = str(delta.get("type") or "")
                    if delta_type == "text_delta":
                        text = str(delta.get("text") or "")
                        if text:
                            streamed_via_delta = True
                            text_emitted = True
                            yield {"type": "text", "content": text}
                    continue

                # ---- content_block_start / content_block_stop ----
                if event_type in ("content_block_start", "content_block_stop"):
                    continue

                # ---- assistant message → text (chunked for streaming fallback) ----
                if event_type == "assistant":
                    # If content_block_delta already streamed text incrementally,
                    # skip re-sending the full message to avoid duplication.
                    if not streamed_via_delta:
                        message_content = self._extract_assistant_text(event)
                        if message_content:
                            text_emitted = True
                            for chunk in self._chunk_text(message_content):
                                yield {"type": "text", "content": chunk}
                                await asyncio.sleep(0.03)  # real delay for SSE flush
                    streamed_via_delta = False  # reset for next turn
                    # Emit per-turn token usage if available.
                    usage_ctx = self._extract_usage_context(event)
                    if usage_ctx:
                        yield {"type": "context", "content": usage_ctx}
                    tool_uses = self._extract_tool_uses(event)
                    for tu in tool_uses:
                        yield {"type": "tool_calls", "content": [tu]}
                        if require_bridge_approval and self._needs_bridge_approval(tu):
                            cmd_text, justification = self._extract_command_for_approval(
                                str(tu.get("name") or ""), tu.get("arguments"),
                            )
                            if cmd_text and not await self._is_allowed_by_prefix_rule(cmd_text):
                                pending_id, future = await self._register_pending_approval()
                                yield self._build_approval_request_event(
                                    pending_id, cmd_text, justification, session_id_value,
                                )
                                err = await self._await_approval_decision(pending_id, future, process)
                                if err is not None:
                                    yield err
                                    return
                    continue

                # ---- message (generic) ----
                if event_type == "message":
                    role = str(event.get("role") or "").lower()
                    if role == "assistant":
                        if not streamed_via_delta:
                            text = self._extract_assistant_text(event)
                            if text:
                                text_emitted = True
                                for chunk in self._chunk_text(text):
                                    yield {"type": "text", "content": chunk}
                                    await asyncio.sleep(0.03)
                        streamed_via_delta = False
                        usage_ctx = self._extract_usage_context(event)
                        if usage_ctx:
                            yield {"type": "context", "content": usage_ctx}
                        tool_uses = self._extract_tool_uses(event)
                        for tu in tool_uses:
                            yield {"type": "tool_calls", "content": [tu]}
                            if require_bridge_approval and self._needs_bridge_approval(tu):
                                cmd_text, justification = self._extract_command_for_approval(
                                    str(tu.get("name") or ""), tu.get("arguments"),
                                )
                                if cmd_text and not await self._is_allowed_by_prefix_rule(cmd_text):
                                    pending_id, future = await self._register_pending_approval()
                                    yield self._build_approval_request_event(
                                        pending_id, cmd_text, justification, session_id_value,
                                    )
                                    err = await self._await_approval_decision(pending_id, future, process)
                                    if err is not None:
                                        yield err
                                        return
                    continue

                # ---- rate_limit_event → extract rate limit info ----
                if event_type == "rate_limit_event":
                    rate_info = event.get("rate_limit_info") if isinstance(event.get("rate_limit_info"), dict) else {}
                    if rate_info:
                        utilization = rate_info.get("utilization")
                        if utilization is not None:
                            try:
                                remaining = max(0.0, 100.0 - float(utilization))
                                yield {
                                    "type": "context",
                                    "content": {"primary_rate_remaining_percent": round(remaining, 2)},
                                }
                            except (ValueError, TypeError):
                                pass
                    continue

                # ---- tool_use event ----
                if event_type == "tool_use":
                    name = str(event.get("name") or "unknown")
                    arguments = event.get("input") or {}
                    tu = {
                        "name": name,
                        "arguments": arguments,
                        "call_id": event.get("tool_use_id") or event.get("id"),
                    }
                    yield {"type": "tool_calls", "content": [tu]}
                    if require_bridge_approval and self._needs_bridge_approval(tu):
                        cmd_text, justification = self._extract_command_for_approval(name, arguments)
                        if cmd_text and not await self._is_allowed_by_prefix_rule(cmd_text):
                            pending_id, future = await self._register_pending_approval()
                            yield self._build_approval_request_event(
                                pending_id, cmd_text, justification, session_id_value,
                            )
                            err = await self._await_approval_decision(pending_id, future, process)
                            if err is not None:
                                yield err
                                return
                    continue

                # ---- tool_result event ----
                if event_type == "tool_result":
                    # We don't forward tool results as text — they are internal.
                    continue

                # ---- result event → end of stream ----
                if event_type == "result":
                    # Check for error result from Claude CLI.
                    if event.get("is_error"):
                        errors = event.get("errors")
                        if isinstance(errors, list) and errors:
                            error_msg = "; ".join(str(e) for e in errors)
                        elif isinstance(errors, str) and errors.strip():
                            error_msg = errors.strip()
                        else:
                            error_msg = str(event.get("result") or "Claude session error")
                        logger.warning(
                            "Claude result is_error=True session=%s errors=%s",
                            session_id_value, errors,
                        )
                        yield {"type": "error", "content": error_msg}
                        return
                    # Only emit final text if nothing was already streamed via
                    # content_block_delta or assistant events.
                    if not text_emitted:
                        result_text = str(event.get("result") or "").strip()
                        if result_text:
                            yield {"type": "text", "content": result_text}
                    # Extract token usage for context % display.
                    usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
                    model_usage = event.get("modelUsage") if isinstance(event.get("modelUsage"), dict) else {}
                    usage_payload = usage if isinstance(usage, dict) and usage else model_usage
                    total_tokens = None
                    if isinstance(usage_payload, dict) and usage_payload:
                        total = self._coerce_usage_total(usage_payload)
                        if total is None:
                            total = self._coerce_non_negative_int(
                                usage_payload.get("total_tokens") or usage_payload.get("total")
                            )
                        if total is not None and total > 0:
                            total_tokens = total
                    # Also check top-level total_tokens
                    if total_tokens is None:
                        total_tokens = self._coerce_non_negative_int(event.get("total_tokens"))
                    # Model context window from result or session metadata.
                    model_context_window = None
                    for key in ("model_context_window", "context_window", "max_tokens"):
                        val = event.get(key)
                        if val is not None:
                            model_context_window = self._coerce_non_negative_int(val)
                            if model_context_window is not None:
                                break
                    self._log_result_event_schema(
                        session_id_value,
                        event,
                        usage,
                        model_usage,
                        total_tokens,
                        model_context_window,
                    )
                    # Emit updated context with token data if we found any.
                    if total_tokens is not None or model_context_window is not None:
                        token_ctx: dict[str, Any] = {}
                        if total_tokens is not None:
                            token_ctx["total_tokens"] = total_tokens
                        if model_context_window is not None:
                            token_ctx["model_context_window"] = model_context_window
                        yield {"type": "context", "content": token_ctx}
                    # ``result`` is the terminal event in claude's stream-json
                    # contract. Stop reading further; if the CLI process is
                    # still running we'll terminate it below.
                    result_terminated = True
                    break

                # (content_block_delta / start / stop handled earlier above)

            if result_terminated and process.returncode is None:
                # Process didn't close stdout after the terminal event; kill
                # the tree so the post-loop wait() completes immediately.
                _kill_process_tree(process)
            stderr_text = ""
            if process.stderr is not None:
                try:
                    stderr_text = (
                        await asyncio.wait_for(process.stderr.read(), timeout=5.0)
                    ).decode("utf-8", errors="ignore").strip()
                except asyncio.TimeoutError:
                    stderr_text = ""
            code = await process.wait()
            if code != 0 and not result_terminated:
                detail = stderr_text or f"exit_code={code}"
                raise ClaudeSessionChatError(f"claude cli failed: {detail}")
        finally:
            await self._unregister_active_process(session_id_value, process)
            await self._cleanup_images(created_images)

    # ------------------------------------------------------------------
    # Helpers for parsing Claude stream-json events
    # ------------------------------------------------------------------

    # Chunk size for simulated streaming (Claude CLI emits full messages).
    _STREAM_CHUNK_SIZE = 20

    @staticmethod
    def _chunk_text(text: str, size: int = 20) -> list[str]:
        """Split text into chunks for simulated streaming output.

        Splits on newlines first, then by character count within each line,
        so markdown structure is preserved.
        """
        if not text:
            return []
        chunks: list[str] = []
        for line in text.split("\n"):
            if not line:
                chunks.append("\n")
                continue
            while len(line) > size:
                chunks.append(line[:size])
                line = line[size:]
            if line:
                chunks.append(line + "\n")
        # Remove trailing newline from the very last chunk.
        if chunks and chunks[-1].endswith("\n"):
            chunks[-1] = chunks[-1][:-1]
        return [c for c in chunks if c]

    @staticmethod
    def _coerce_non_negative_int(value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            if not value == value:
                return None
            return max(0, int(value))
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            try:
                return max(0, int(raw))
            except ValueError:
                try:
                    return max(0, int(float(raw)))
                except ValueError:
                    return None
        if isinstance(value, dict):
            preferred_keys = (
                "value",
                "total",
                "count",
                "tokens",
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "max_tokens",
                "context_window",
                "model_context_window",
            )
            for key in preferred_keys:
                if key not in value:
                    continue
                coerced = ClaudeSessionChatService._coerce_non_negative_int(value.get(key))
                if coerced is not None:
                    return coerced
            for nested in value.values():
                coerced = ClaudeSessionChatService._coerce_non_negative_int(nested)
                if coerced is not None:
                    return coerced
        return None

    @classmethod
    def _coerce_usage_total(cls, usage: Any) -> Optional[int]:
        if not isinstance(usage, dict):
            return None
        parts = (
            usage.get("input_tokens", usage.get("input")),
            usage.get("output_tokens", usage.get("output")),
            usage.get("cache_read_input_tokens", usage.get("cache_read")),
            usage.get("cache_creation_input_tokens", usage.get("cache_creation")),
        )
        values: list[int] = []
        for part in parts:
            coerced = cls._coerce_non_negative_int(part)
            if coerced is not None:
                values.append(coerced)
        if values:
            total = sum(values)
            if total > 0:
                return total
        return cls._coerce_non_negative_int(usage.get("total_tokens", usage.get("total")))

    @staticmethod
    def _value_shape(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): type(item).__name__ for key, item in value.items()}
        if isinstance(value, list):
            return [type(item).__name__ for item in value[:5]]
        return type(value).__name__

    @classmethod
    def _log_result_event_schema(
        cls,
        session_id: str,
        event: dict[str, Any],
        usage: Any,
        model_usage: Any,
        total_tokens: Optional[int],
        model_context_window: Optional[int],
    ) -> None:
        # Known nested dict fields in Claude API usage — not suspicious.
        _KNOWN_NESTED_USAGE_KEYS = {"server_tool_use", "cache_creation", "cache_read"}
        suspicious_usage = isinstance(usage, dict) and any(
            isinstance(val, dict) for key, val in usage.items() if key not in _KNOWN_NESTED_USAGE_KEYS
        )
        suspicious_model_usage = isinstance(model_usage, dict) and any(
            isinstance(val, dict) for key, val in model_usage.items() if key not in _KNOWN_NESTED_USAGE_KEYS
        )
        suspicious_context = any(
            isinstance(event.get(key), dict)
            for key in ("model_context_window", "context_window", "max_tokens", "total_tokens")
        )
        if not suspicious_usage and not suspicious_model_usage and not suspicious_context and total_tokens is not None:
            return
        logger.warning(
            "Claude result event schema session=%s keys=%s usage_shape=%s model_usage_shape=%s total_tokens_shape=%s max_tokens_shape=%s context_window_shape=%s model_context_window_shape=%s parsed_total_tokens=%s parsed_model_context_window=%s",
            session_id,
            sorted(str(key) for key in event.keys()),
            cls._value_shape(usage),
            cls._value_shape(model_usage),
            cls._value_shape(event.get("total_tokens")),
            cls._value_shape(event.get("max_tokens")),
            cls._value_shape(event.get("context_window")),
            cls._value_shape(event.get("model_context_window")),
            total_tokens,
            model_context_window,
        )

    @staticmethod
    def _extract_usage_context(event: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Extract token usage from an assistant/message event's usage field.

        Each turn's usage already represents the full context window size
        (input + cache tokens = conversation history), so we emit the latest
        total rather than accumulating.
        """
        usage = event.get("usage")
        if not isinstance(usage, dict):
            # Also check nested message.usage (JSONL-style events).
            msg = event.get("message")
            if isinstance(msg, dict):
                usage = msg.get("usage")
            if not isinstance(usage, dict):
                return None
        total = ClaudeSessionChatService._coerce_usage_total(usage)
        if total is None:
            return None
        if total <= 0:
            return None
        return {"total_tokens": total}

    @staticmethod
    def _extract_assistant_text(event: dict[str, Any]) -> str:
        """Pull text from a message/assistant event."""
        # Direct text field.
        text = event.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        # Content array with text blocks.
        content = event.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "") == "text":
                    t = str(item.get("text") or "").strip()
                    if t:
                        parts.append(t)
            if parts:
                return " ".join(parts)
        # Message field (fallback).
        message = event.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        return ""

    @staticmethod
    def _extract_tool_uses(event: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract tool_use blocks from a message content array."""
        content = event.get("content")
        if not isinstance(content, list):
            return []
        results: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "") == "tool_use":
                results.append({
                    "name": str(item.get("name") or "unknown"),
                    "arguments": item.get("input") or {},
                    "call_id": item.get("id"),
                })
        return results

    # ------------------------------------------------------------------
    # Run prompt (blocking, collects all text)
    # ------------------------------------------------------------------

    async def run_prompt(
        self,
        session_id: str,
        prompt: str,
        **kwargs: Any,
    ) -> str:
        chunks: list[str] = []
        async for event in self.stream_prompt(session_id, prompt, **kwargs):
            if event.get("type") == "text":
                content = str(event.get("content") or "").strip()
                if content:
                    chunks.append(content)
        if chunks:
            return "\n\n".join(chunks)
        raise ClaudeSessionChatError("claude cli returned empty response")

    # ------------------------------------------------------------------
    # Create session
    # ------------------------------------------------------------------

    async def create_session(
        self,
        *,
        cwd: str,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        permission_mode: Optional[str] = None,
        approval_policy: Optional[str] = None,
        sandbox_mode: Optional[str] = None,
        plan_mode: Optional[bool] = None,
    ) -> dict[str, Any]:
        cwd_value = str(Path(cwd).expanduser()) if cwd else self.default_cwd

        # Generate a new session UUID.
        session_id = str(uuid.uuid4())

        bootstrap_prompt = "Initialize a new session. Reply with: SESSION_READY"
        prompt = bootstrap_prompt
        plan_mode_fallback = False
        if plan_mode is True:
            prompt = "Please produce a detailed implementation plan before any code edits."
            plan_mode_fallback = True

        cmd: list[str] = [self.claude_bin, "-p", "--output-format", "json"]
        cmd.extend(["--session-id", session_id])
        if model:
            cmd.extend(["--model", model])
        if reasoning_effort:
            cmd.extend(["--effort", reasoning_effort])
        self._append_permission_mode_args(
            cmd,
            permission_mode=permission_mode,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
        )
        cmd.append(prompt)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd_value,
                env=self._build_claude_subprocess_env(),
            )
        except FileNotFoundError as exc:
            raise ClaudeSessionChatError(f"claude cli not found: {self.claude_bin}") from exc
        except Exception as exc:
            detail = _format_subprocess_spawn_error(exc)
            raise ClaudeSessionChatError(f"claude cli failed to start: {detail}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.max_timeout_sec)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise ClaudeSessionChatError("claude cli timeout while creating session") from exc

        stdout_text = stdout.decode("utf-8", errors="ignore")
        stderr_text = stderr.decode("utf-8", errors="ignore").strip()
        if process.returncode != 0:
            detail = stderr_text or stdout_text.strip() or f"exit_code={process.returncode}"
            raise ClaudeSessionChatError(f"claude cli failed: {detail}")

        # Try to extract session_id from output (claude --output-format json returns a JSON object).
        runtime_context: dict[str, Any] = {}
        for raw_line in stdout_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                # The JSON output may include session_id.
                if data.get("session_id"):
                    session_id = str(data["session_id"])
                if data.get("model"):
                    runtime_context["model"] = str(data["model"])

        import subprocess as _subprocess
        branch = ""
        try:
            branch_completed = _subprocess.run(
                ["git", "-C", cwd_value, "branch", "--show-current"],
                check=False, capture_output=True, text=True, timeout=5,
            )
            if branch_completed.returncode == 0:
                branch = (branch_completed.stdout or "").strip()
        except Exception:
            branch = ""

        requested_mode, requested_approval, requested_sandbox = _resolve_permission_settings(
            permission_mode=permission_mode,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
        )

        return {
            "session_id": session_id,
            "cwd": cwd_value,
            "branch": branch,
            "model": str(runtime_context.get("model") or model or ""),
            "effort": str(reasoning_effort or ""),
            "permission_mode": requested_mode,
            "approval_policy": requested_approval,
            "sandbox_mode": requested_sandbox,
            "plan_mode": bool(plan_mode),
            "plan_mode_fallback": plan_mode_fallback,
            "agent_brand": "claude",
        }
