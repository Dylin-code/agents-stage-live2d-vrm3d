import asyncio
import base64
import json
import logging
import mimetypes
import os
import shlex
import subprocess
import time
import uuid
import math
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from .session_bridge_shared import (
    PERMISSION_MODE_DEFAULT,
    PERMISSION_MODE_FULL,
    _ensure_stream_reader_limit,
    _extract_message_content,
    _resolve_default_chat_cwd,
    _resolve_permission_mode,
    _resolve_permission_settings,
)

logger = logging.getLogger(__name__)

DEFAULT_CODEX_CLI_IDLE_TIMEOUT_SEC = 180.0
DEFAULT_CODEX_CLI_MAX_TIMEOUT_SEC = 1800.0
DEFAULT_CODEX_APPROVAL_TIMEOUT_SEC = 300.0
CODEX_CLI_IDLE_TIMEOUT_ENV = "CODEX_CLI_IDLE_TIMEOUT_SEC"
CODEX_CLI_MAX_TIMEOUT_ENV = "CODEX_CLI_MAX_TIMEOUT_SEC"
CODEX_CLI_APPROVAL_TIMEOUT_ENV = "CODEX_CLI_APPROVAL_TIMEOUT_SEC"


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

class CodexSessionChatError(RuntimeError):
    pass


class CodexSessionChatService:
    def __init__(
        self,
        codex_bin: str = "codex",
        idle_timeout_sec: Optional[float] = None,
        max_timeout_sec: Optional[float] = None,
        approval_timeout_sec: Optional[float] = None,
        default_cwd: Optional[str] = None,
    ) -> None:
        self.codex_bin = codex_bin
        self.idle_timeout_sec = (
            float(idle_timeout_sec)
            if idle_timeout_sec is not None
            else _read_timeout_env(CODEX_CLI_IDLE_TIMEOUT_ENV, DEFAULT_CODEX_CLI_IDLE_TIMEOUT_SEC)
        )
        self.max_timeout_sec = (
            float(max_timeout_sec)
            if max_timeout_sec is not None
            else _read_timeout_env(CODEX_CLI_MAX_TIMEOUT_ENV, DEFAULT_CODEX_CLI_MAX_TIMEOUT_SEC)
        )
        self.default_cwd = _resolve_default_chat_cwd(default_cwd)
        self.approval_timeout_sec = (
            float(approval_timeout_sec)
            if approval_timeout_sec is not None
            else _read_timeout_env(CODEX_CLI_APPROVAL_TIMEOUT_ENV, DEFAULT_CODEX_APPROVAL_TIMEOUT_SEC)
        )
        self._pending_approvals: dict[str, asyncio.Future] = {}
        self._pending_approvals_lock = asyncio.Lock()
        self._approved_prefix_rules: set[tuple[str, ...]] = set()
        self._approved_prefix_rules_lock = asyncio.Lock()
        logger.info(
            "Initialized CodexSessionChatService idle_timeout=%.1f max_timeout=%.1f approval_timeout=%.1f default_cwd=%s",
            self.idle_timeout_sec,
            self.max_timeout_sec,
            self.approval_timeout_sec,
            self.default_cwd,
        )

    @staticmethod
    def _build_codex_subprocess_env() -> dict[str, str]:
        """Spawn codex in a clean environment instead of inheriting parent Codex app runtime."""
        env = os.environ.copy()
        # Avoid nested-session lock-in (approval/sandbox/thread) from parent runtime.
        for key in (
            "CODEX_THREAD_ID",
            "CODEX_CI",
            "CODEX_SANDBOX",
            "CODEX_SANDBOX_NETWORK_DISABLED",
            "CODEX_SHELL",
            "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
        ):
            env.pop(key, None)
        return env

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
        cmd: list[str] = [self.codex_bin]
        if cwd:
            cmd.extend(["-C", cwd])
        self._append_permission_mode_args(
            cmd,
            permission_mode=permission_mode,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
        )
        cmd.append("exec")
        if model:
            cmd.extend(["-m", model])
        if reasoning_effort:
            cmd.extend(["-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}"])
        cmd.extend(["resume", "--skip-git-repo-check", "--json"])
        for image_path in image_paths:
            cmd.extend(["-i", image_path])
        cmd.extend([session_id, prompt])
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
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            cmd.append("--full-auto")

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

    @staticmethod
    def _extract_tool_call_payload(payload: dict[str, Any]) -> dict[str, Any]:
        raw_args = payload.get("arguments")
        parsed_args: Any = raw_args
        if isinstance(raw_args, str):
            try:
                parsed_args = json.loads(raw_args)
            except json.JSONDecodeError:
                parsed_args = raw_args
        name = str(payload.get("name") or "").strip()
        return {
            "name": name or "unknown",
            "arguments": parsed_args,
            "call_id": payload.get("call_id"),
        }

    @staticmethod
    def _extract_command_for_approval(parsed_args: Any) -> tuple[str, str]:
        if not isinstance(parsed_args, dict):
            return "", ""
        command = parsed_args.get("command")
        if isinstance(command, list):
            cmd_text = " ".join(str(x) for x in command)
        elif isinstance(command, str):
            cmd_text = command
        else:
            cmd_text = ""
        justification = str(parsed_args.get("justification") or "")
        return cmd_text.strip(), justification.strip()

    @staticmethod
    def _parse_data_url_image(data_url: str) -> tuple[bytes, str]:
        header, encoded = data_url.split(",", 1)
        mime = "application/octet-stream"
        if header.startswith("data:") and ";" in header:
            mime = header[5:].split(";", 1)[0] or mime
        decoded = base64.b64decode(encoded)
        extension = mimetypes.guess_extension(mime) or ".bin"
        return decoded, extension

    async def _materialize_images(self, session_id: str, images: list[dict[str, Any]]) -> tuple[list[str], list[Path]]:
        paths: list[str] = []
        created: list[Path] = []
        if not images:
            return paths, created

        root = Path("/tmp/session-bridge-images") / session_id
        root.mkdir(parents=True, exist_ok=True)
        for idx, image in enumerate(images):
            if not isinstance(image, dict):
                continue
            # 支援前端傳 data_url 或預先存在的路徑
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
    ) -> AsyncGenerator[dict[str, Any], None]:
        session_id_value = (session_id or "").strip()
        prompt_value = (prompt or "").strip()
        if not session_id_value:
            raise CodexSessionChatError("session_id is required")
        if not prompt_value:
            raise CodexSessionChatError("message is required")

        effective_cwd = str(Path((cwd or self.default_cwd)).expanduser())
        effective_permission_mode, _, _ = _resolve_permission_settings(
            permission_mode=permission_mode,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
        )
        require_bridge_approval = effective_permission_mode != PERMISSION_MODE_FULL
        prompt_for_exec = prompt_value
        plan_mode_fallback = False
        if plan_mode is True:
            # codex CLI 尚未穩定提供 plan mode 參數，先使用保守 fallback。
            prompt_for_exec = (
                "你現在必須先輸出完整實作計劃，暫時不要直接執行修改；"
                "若資訊不足，先列出需確認項目。\n\n"
                f"{prompt_value}"
            )
            plan_mode_fallback = True
        image_paths, created_images = await self._materialize_images(session_id_value, images or [])
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
        logger.debug(
            "Starting codex stream session=%s cwd=%s model=%s effort=%s permission_mode=%s plan_mode=%s idle_timeout=%.1f max_timeout=%.1f command=%s",
            session_id_value,
            effective_cwd,
            model or "",
            reasoning_effort or "",
            effective_permission_mode,
            bool(plan_mode),
            self.idle_timeout_sec,
            self.max_timeout_sec,
            command,
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=effective_cwd,
                env=self._build_codex_subprocess_env(),
            )
            _ensure_stream_reader_limit(process.stdout)
        except FileNotFoundError as exc:
            await self._cleanup_images(created_images)
            raise CodexSessionChatError(f"codex cli not found: {self.codex_bin}") from exc

        start_mono = time.monotonic()
        last_activity_mono = start_mono
        event_count = 0
        last_event_summary = ""
        try:
            while True:
                now = time.monotonic()
                idle_elapsed = now - last_activity_mono
                total_elapsed = now - start_mono
                if idle_elapsed > self.idle_timeout_sec:
                    logger.warning(
                        "Codex stream idle timeout session=%s idle=%.1fs total=%.1fs last_event=%s",
                        session_id_value,
                        idle_elapsed,
                        total_elapsed,
                        last_event_summary or "<none>",
                    )
                    process.kill()
                    await process.wait()
                    raise CodexSessionChatError("codex cli idle timeout")
                if total_elapsed > self.max_timeout_sec:
                    logger.warning(
                        "Codex stream max timeout session=%s total=%.1fs last_event=%s",
                        session_id_value,
                        total_elapsed,
                        last_event_summary or "<none>",
                    )
                    process.kill()
                    await process.wait()
                    raise CodexSessionChatError("codex cli max timeout")
                if process.stdout is None:
                    logger.debug("Codex stream missing stdout session=%s", session_id_value)
                    break
                try:
                    raw_line = await asyncio.wait_for(process.stdout.readline(), timeout=1.0)
                except asyncio.TimeoutError:
                    if process.returncode is not None:
                        logger.debug(
                            "Codex stream exited while waiting for line session=%s returncode=%s last_event=%s",
                            session_id_value,
                            process.returncode,
                            last_event_summary or "<none>",
                        )
                        break
                    continue
                if not raw_line:
                    if process.returncode is not None:
                        logger.debug(
                            "Codex stream reached EOF session=%s returncode=%s last_event=%s",
                            session_id_value,
                            process.returncode,
                            last_event_summary or "<none>",
                        )
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
                top_type = str(event.get("type") or "")
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                payload_type = str(payload.get("type") or "")
                event_count += 1
                last_activity_mono = time.monotonic()
                last_event_summary = f"{top_type}:{payload_type or '-'}"

                if top_type == "turn_context":
                    sandbox = payload.get("sandbox_policy") if isinstance(payload.get("sandbox_policy"), dict) else {}
                    collaboration = payload.get("collaboration_mode") if isinstance(payload.get("collaboration_mode"), dict) else {}
                    collaboration_mode = str(collaboration.get("mode") or "").strip().lower()
                    runtime_approval = str(payload.get("approval_policy") or "")
                    runtime_sandbox = str(sandbox.get("type") or "")
                    if permission_mode is not None and str(permission_mode).strip():
                        _, runtime_approval, runtime_sandbox = _resolve_permission_settings(
                            permission_mode=effective_permission_mode,
                            approval_policy=None,
                            sandbox_mode=None,
                        )
                    yield {
                        "type": "context",
                        "content": {
                            "model": str(payload.get("model") or ""),
                            "effort": str(payload.get("effort") or ""),
                            "approval_policy": runtime_approval,
                            "sandbox_mode": runtime_sandbox,
                            "permission_mode": effective_permission_mode,
                            "plan_mode": collaboration_mode == "plan" if collaboration_mode else bool(plan_mode),
                            "plan_mode_fallback": plan_mode_fallback,
                        },
                    }
                    continue

                if top_type == "response_item" and str(payload.get("type") or "") in {"function_call", "custom_tool_call"}:
                    tool_payload = self._extract_tool_call_payload(payload)
                    yield {"type": "tool_calls", "content": [tool_payload]}
                    command_text, justification = self._extract_command_for_approval(tool_payload.get("arguments"))
                    args = tool_payload.get("arguments")
                    needs_approval = (
                        isinstance(args, dict)
                        and str(args.get("sandbox_permissions") or "").strip() == "require_escalated"
                    )
                    if (
                        require_bridge_approval
                        and needs_approval
                        and not await self._is_allowed_by_prefix_rule(command_text)
                    ):
                        pending_id, future = await self._register_pending_approval()
                        suggested = self._suggest_prefix_rule(command_text)
                        yield {
                            "type": "approval_request",
                            "content": {
                                "pending_id": pending_id,
                                "command": command_text,
                                "justification": justification,
                                "suggested_prefix": suggested,
                                "session_id": session_id_value,
                            },
                        }
                        try:
                            result = await asyncio.wait_for(future, timeout=self.approval_timeout_sec)
                        except asyncio.TimeoutError:
                            await self._cleanup_pending_approval(pending_id)
                            process.kill()
                            await process.wait()
                            raise CodexSessionChatError("approval timeout")
                        finally:
                            await self._cleanup_pending_approval(pending_id)
                        decision = str((result or {}).get("decision") or "").strip()
                        if decision == "deny_once":
                            process.kill()
                            await process.wait()
                            yield {"type": "error", "content": "使用者拒絕本次權限請求"}
                            return
                        if decision == "allow_prefix":
                            normalized = self._normalize_prefix_rule((result or {}).get("prefix_rule"))
                            if normalized:
                                async with self._approved_prefix_rules_lock:
                                    self._approved_prefix_rules.add(normalized)
                    continue

                if top_type == "custom_tool_call":
                    custom_payload = payload if payload else event
                    tool_payload = self._extract_tool_call_payload(custom_payload)
                    yield {"type": "tool_calls", "content": [tool_payload]}
                    command_text, justification = self._extract_command_for_approval(tool_payload.get("arguments"))
                    args = tool_payload.get("arguments")
                    needs_approval = (
                        isinstance(args, dict)
                        and str(args.get("sandbox_permissions") or "").strip() == "require_escalated"
                    )
                    if (
                        require_bridge_approval
                        and needs_approval
                        and not await self._is_allowed_by_prefix_rule(command_text)
                    ):
                        pending_id, future = await self._register_pending_approval()
                        suggested = self._suggest_prefix_rule(command_text)
                        yield {
                            "type": "approval_request",
                            "content": {
                                "pending_id": pending_id,
                                "command": command_text,
                                "justification": justification,
                                "suggested_prefix": suggested,
                                "session_id": session_id_value,
                            },
                        }
                        try:
                            result = await asyncio.wait_for(future, timeout=self.approval_timeout_sec)
                        except asyncio.TimeoutError:
                            await self._cleanup_pending_approval(pending_id)
                            process.kill()
                            await process.wait()
                            raise CodexSessionChatError("approval timeout")
                        finally:
                            await self._cleanup_pending_approval(pending_id)
                        decision = str((result or {}).get("decision") or "").strip()
                        if decision == "deny_once":
                            process.kill()
                            await process.wait()
                            yield {"type": "error", "content": "使用者拒絕本次權限請求"}
                            return
                        if decision == "allow_prefix":
                            normalized = self._normalize_prefix_rule((result or {}).get("prefix_rule"))
                            if normalized:
                                async with self._approved_prefix_rules_lock:
                                    self._approved_prefix_rules.add(normalized)
                    continue

                if top_type == "response_item" and str(payload.get("type") or "") == "message":
                    role = str(payload.get("role") or "").strip().lower()
                    if role == "assistant":
                        content = _extract_message_content(payload)
                        if content:
                            yield {"type": "text", "content": content}
                    continue

                if top_type == "event_msg":
                    if payload_type == "agent_message":
                        text = str(payload.get("message") or "").strip()
                        if text:
                            yield {"type": "text", "content": text}
                        continue
                    if payload_type == "agent_reasoning":
                        text = str(payload.get("text") or "").strip()
                        if text:
                            yield {"type": "tool_calls", "content": [{"name": "agent_reasoning", "arguments": text}]}
                        continue

                if top_type == "item.completed":
                    item = event.get("item") if isinstance(event.get("item"), dict) else {}
                    item_type = str(item.get("type") or "")
                    if item_type in {"agent_message", "assistant_message"}:
                        text = str(item.get("text") or "").strip()
                        if text:
                            yield {"type": "text", "content": text}
                    elif item_type == "message" and str(item.get("role") or "").lower() == "assistant":
                        text = _extract_message_content(item)
                        if text:
                            yield {"type": "text", "content": text}
                    continue

                logger.debug(
                    "Codex stream ignored event session=%s event=%s",
                    session_id_value,
                    last_event_summary or "<none>",
                )

            stderr_text = ""
            if process.stderr is not None:
                stderr_text = (await process.stderr.read()).decode("utf-8", errors="ignore").strip()
            code = await process.wait()
            if code != 0:
                detail = stderr_text or f"exit_code={code}"
                logger.warning(
                    "Codex stream failed session=%s returncode=%s events=%s last_event=%s stderr=%s",
                    session_id_value,
                    code,
                    event_count,
                    last_event_summary or "<none>",
                    detail,
                )
                raise CodexSessionChatError(f"codex cli failed: {detail}")
            logger.debug(
                "Codex stream completed session=%s returncode=0 events=%s last_event=%s stderr_present=%s",
                session_id_value,
                event_count,
                last_event_summary or "<none>",
                bool(stderr_text),
            )
        finally:
            await self._cleanup_images(created_images)

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
        raise CodexSessionChatError("codex cli returned empty response")

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
        bootstrap_prompt = "Initialize a new codex session. Reply with: SESSION_READY"
        prompt = bootstrap_prompt
        plan_mode_fallback = False
        if plan_mode is True:
            prompt = "Please produce a detailed implementation plan before any code edits."
            plan_mode_fallback = True

        cmd: list[str] = [self.codex_bin]
        if cwd_value:
            cmd.extend(["-C", cwd_value])
        self._append_permission_mode_args(
            cmd,
            permission_mode=permission_mode,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
        )
        cmd.append("exec")
        if model:
            cmd.extend(["-m", model])
        if reasoning_effort:
            cmd.extend(["-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}"])
        cmd.extend(["--skip-git-repo-check", "--json", prompt])
        logger.debug(
            "Creating codex session cwd=%s model=%s effort=%s permission_mode=%s plan_mode=%s max_timeout=%.1f command=%s",
            cwd_value,
            model or "",
            reasoning_effort or "",
            permission_mode or "",
            bool(plan_mode),
            self.max_timeout_sec,
            cmd,
        )

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd_value,
            env=self._build_codex_subprocess_env(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.max_timeout_sec)
        except asyncio.TimeoutError as exc:
            logger.warning(
                "Codex create_session timeout cwd=%s model=%s elapsed_limit=%.1fs",
                cwd_value,
                model or "",
                self.max_timeout_sec,
            )
            process.kill()
            await process.wait()
            raise CodexSessionChatError("codex cli timeout while creating session") from exc

        stdout_text = stdout.decode("utf-8", errors="ignore")
        stderr_text = stderr.decode("utf-8", errors="ignore").strip()
        if process.returncode != 0:
            detail = stderr_text or stdout_text.strip() or f"exit_code={process.returncode}"
            logger.warning(
                "Codex create_session failed cwd=%s model=%s returncode=%s stderr=%s",
                cwd_value,
                model or "",
                process.returncode,
                detail,
            )
            raise CodexSessionChatError(f"codex cli failed: {detail}")

        session_id = ""
        runtime_context: dict[str, Any] = {}
        for raw_line in stdout_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "")
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if event_type == "thread.started":
                session_id = str(event.get("thread_id") or "").strip()
                if session_id:
                    continue
            if event_type == "turn_context":
                sandbox = payload.get("sandbox_policy") if isinstance(payload.get("sandbox_policy"), dict) else {}
                runtime_context = {
                    "model": str(payload.get("model") or ""),
                    "effort": str(payload.get("effort") or ""),
                    "approval_policy": str(payload.get("approval_policy") or ""),
                    "sandbox_mode": str(sandbox.get("type") or ""),
                }
        if not session_id:
            raise CodexSessionChatError("failed to parse new session id from codex output")
        branch = ""
        try:
            branch_completed = subprocess.run(
                ["git", "-C", cwd_value, "branch", "--show-current"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if branch_completed.returncode == 0:
                branch = (branch_completed.stdout or "").strip()
        except Exception:
            branch = ""
        runtime_model = str(runtime_context.get("model") or model or "")
        runtime_effort = str(runtime_context.get("effort") or reasoning_effort or "")
        requested_mode, requested_approval, requested_sandbox = _resolve_permission_settings(
            permission_mode=permission_mode,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
        )
        if permission_mode is not None and str(permission_mode).strip():
            runtime_permission_mode = requested_mode
            runtime_approval = requested_approval
            runtime_sandbox = requested_sandbox
        else:
            runtime_approval = str(runtime_context.get("approval_policy") or requested_approval or "")
            runtime_sandbox = str(runtime_context.get("sandbox_mode") or requested_sandbox or "")
            runtime_permission_mode = _resolve_permission_mode(
                None,
                approval_policy=runtime_approval,
                sandbox_mode=runtime_sandbox,
            )
        logger.debug(
            "Codex create_session completed session=%s cwd=%s branch=%s permission_mode=%s approval_policy=%s sandbox_mode=%s plan_mode=%s",
            session_id,
            cwd_value,
            branch,
            runtime_permission_mode,
            runtime_approval,
            runtime_sandbox,
            bool(plan_mode),
        )
        return {
            "session_id": session_id,
            "cwd": cwd_value,
            "branch": branch,
            "model": runtime_model,
            "effort": runtime_effort,
            "permission_mode": runtime_permission_mode,
            "approval_policy": runtime_approval,
            "sandbox_mode": runtime_sandbox,
            "plan_mode": bool(plan_mode),
            "plan_mode_fallback": plan_mode_fallback,
        }
