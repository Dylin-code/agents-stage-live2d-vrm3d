"""Opencode CLI chat service - wraps `opencode run --format json` in the same streaming bridge interface."""

import asyncio
import json
import logging
import math
import os
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
    _ensure_stream_reader_limit,
    _isolated_subprocess_kwargs,
    _kill_process_tree,
    _resolve_permission_mode,
    _resolve_default_chat_cwd,
)

logger = logging.getLogger(__name__)


class OpencodeSessionChatError(RuntimeError):
    pass


DEFAULT_OPENCODE_CLI_IDLE_TIMEOUT_SEC = 180.0
DEFAULT_OPENCODE_CLI_MAX_TIMEOUT_SEC = 1800.0
OPENCODE_CLI_IDLE_TIMEOUT_ENV = "OPENCODE_CLI_IDLE_TIMEOUT_SEC"
OPENCODE_CLI_MAX_TIMEOUT_ENV = "OPENCODE_CLI_MAX_TIMEOUT_SEC"


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


def resolve_opencode_permission_settings(
    permission_mode: Optional[str],
    approval_policy: Optional[str],
    sandbox_mode: Optional[str],
) -> tuple[str, str, str]:
    """Map bridge permission metadata to OpenCode's limited non-interactive flags."""
    effective_mode = _resolve_permission_mode(
        permission_mode=permission_mode,
        approval_policy=approval_policy,
        sandbox_mode=sandbox_mode,
    )
    if effective_mode == PERMISSION_MODE_FULL:
        return effective_mode, "never", "danger-full-access"
    if effective_mode in {PERMISSION_MODE_AUTO, PERMISSION_MODE_PLAN}:
        return effective_mode, "", ""
    return PERMISSION_MODE_DEFAULT, "", ""


class OpencodeSessionChatService:
    """Wraps the `opencode` CLI (v1.x) in the same streaming bridge interface as CodexSessionChatService."""

    def __init__(
        self,
        opencode_bin: str = "opencode",
        idle_timeout_sec: Optional[float] = None,
        max_timeout_sec: Optional[float] = None,
        default_cwd: Optional[str] = None,
    ) -> None:
        self.opencode_bin = opencode_bin
        self.idle_timeout_sec = (
            float(idle_timeout_sec)
            if idle_timeout_sec is not None
            else _read_timeout_env(OPENCODE_CLI_IDLE_TIMEOUT_ENV, DEFAULT_OPENCODE_CLI_IDLE_TIMEOUT_SEC)
        )
        self.max_timeout_sec = (
            float(max_timeout_sec)
            if max_timeout_sec is not None
            else _read_timeout_env(OPENCODE_CLI_MAX_TIMEOUT_ENV, DEFAULT_OPENCODE_CLI_MAX_TIMEOUT_SEC)
        )
        self.default_cwd = _resolve_default_chat_cwd(default_cwd)
        self._per_process_registry: dict[str, asyncio.subprocess.Process] = {}
        self._per_process_registry_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Process registry (for abort)
    # ------------------------------------------------------------------

    async def _register_process(self, session_id: str, process: asyncio.subprocess.Process) -> None:
        async with self._per_process_registry_lock:
            previous = self._per_process_registry.get(session_id)
            self._per_process_registry[session_id] = process
        if previous is not None and previous is not process and previous.returncode is None:
            try:
                previous.kill()
            except ProcessLookupError:
                pass

    async def _unregister_process(self, session_id: str, process: asyncio.subprocess.Process) -> None:
        async with self._per_process_registry_lock:
            current = self._per_process_registry.get(session_id)
            if current is process:
                self._per_process_registry.pop(session_id, None)

    async def abort_session(self, session_id: str) -> bool:
        key = (session_id or "").strip()
        if not key:
            return False
        async with self._per_process_registry_lock:
            process = self._per_process_registry.get(key)
        if process is None or process.returncode is not None:
            return False
        try:
            _kill_process_tree(process)
        except ProcessLookupError:
            return False
        logger.info("Opencode stream aborted by user session=%s pid=%s", key, process.pid)
        return True

    # ------------------------------------------------------------------
    # Session creation
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
        plan_mode_fallback = False
        init_prompt = "Initialize a new session. Reply with: SESSION_READY"
        if plan_mode is True:
            init_prompt = "Please produce a detailed implementation plan before any code edits."
            plan_mode_fallback = True

        cmd = self._build_cli_command(
            session_id=None,
            prompt=init_prompt,
            cwd=cwd_value,
            image_paths=[],
            model=model,
            reasoning_effort=reasoning_effort,
            permission_mode=permission_mode,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
        )
        logger.debug(
            "Creating opencode session cwd=%s model=%s effort=%s command=%s",
            cwd_value, model or "", reasoning_effort or "", cmd,
        )

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd_value,
            **_isolated_subprocess_kwargs(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.max_timeout_sec)
        except asyncio.TimeoutError as exc:
            logger.warning("Opencode create_session timeout cwd=%s model=%s", cwd_value, model or "")
            process.kill()
            await process.wait()
            raise OpencodeSessionChatError("opencode cli timeout while creating session") from exc

        stdout_text = stdout.decode("utf-8", errors="ignore")
        stderr_text = stderr.decode("utf-8", errors="ignore").strip()
        if process.returncode != 0:
            detail = stderr_text or stdout_text.strip() or f"exit_code={process.returncode}"
            raise OpencodeSessionChatError(f"opencode cli failed: {detail}")

        session_id = ""
        model_from_output = ""
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
            sid = str(event.get("sessionID") or "").strip()
            if sid:
                session_id = sid
                break

        if not session_id:
            raise OpencodeSessionChatError("failed to parse new session id from opencode output")

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

        runtime_permission_mode, runtime_approval, runtime_sandbox = resolve_opencode_permission_settings(
            permission_mode=permission_mode,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
        )

        logger.debug(
            "Opencode create_session completed session=%s cwd=%s branch=%s plan_mode=%s",
            session_id, cwd_value, branch, bool(plan_mode),
        )
        return {
            "session_id": session_id,
            "cwd": cwd_value,
            "branch": branch,
            "model": model_from_output or model or "",
            "effort": str(reasoning_effort or ""),
            "permission_mode": runtime_permission_mode,
            "approval_policy": runtime_approval,
            "sandbox_mode": runtime_sandbox,
            "plan_mode": bool(plan_mode),
            "plan_mode_fallback": plan_mode_fallback,
        }

    # ------------------------------------------------------------------
    # CLI command building
    # ------------------------------------------------------------------

    def _build_cli_command(
        self,
        session_id: Optional[str],
        prompt: str,
        cwd: str,
        image_paths: list[str],
        model: Optional[str],
        reasoning_effort: Optional[str],
        permission_mode: Optional[str],
        approval_policy: Optional[str],
        sandbox_mode: Optional[str],
    ) -> list[str]:
        cmd: list[str] = [self.opencode_bin, "run", "--format", "json"]
        if session_id:
            cmd.extend(["-s", session_id])
        if model:
            cmd.extend(["-m", model])
        if reasoning_effort:
            cmd.extend(["--variant", reasoning_effort])
        effective_mode, _, _ = resolve_opencode_permission_settings(
            permission_mode=permission_mode,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
        )
        if effective_mode == PERMISSION_MODE_FULL:
            cmd.append("--dangerously-skip-permissions")
        if image_paths:
            for img_path in image_paths:
                cmd.extend(["-f", img_path])
        cmd.append(prompt)
        return cmd

    # ------------------------------------------------------------------
    # Image support
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_data_url_image(data_url: str) -> tuple[bytes, str]:
        header, encoded = data_url.split(",", 1)
        mime = "application/octet-stream"
        if header.startswith("data:") and ";" in header:
            mime = header[5:].split(";", 1)[0] or mime
        import base64
        decoded = base64.b64decode(encoded)
        import mimetypes
        extension = mimetypes.guess_extension(mime) or ".bin"
        return decoded, extension

    async def _materialize_images(
        self, session_id: str, images: list[dict[str, Any]]
    ) -> tuple[list[str], list[Path]]:
        paths: list[str] = []
        created: list[Path] = []
        if not images:
            return paths, created
        root = Path("/tmp/session-bridge-images") / f"opencode-{session_id}"
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
    # Stream prompt - core
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
            raise OpencodeSessionChatError("session_id is required")
        if not prompt_value:
            raise OpencodeSessionChatError("message is required")

        effective_cwd = str(Path((cwd or self.default_cwd)).expanduser())
        plan_mode_fallback = bool(plan_mode)

        prompt_for_exec = _build_session_bridge_prompt(
            prompt_value,
            persona_id=persona_id,
            persona_name=persona_name,
            persona_content=persona_content,
            plan_mode=plan_mode,
        )

        image_paths, created_images = await self._materialize_images(session_id_value, images or [])
        runtime_permission_mode, runtime_approval, runtime_sandbox = resolve_opencode_permission_settings(
            permission_mode=permission_mode,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
        )
        command = self._build_cli_command(
            session_id=session_id_value,
            prompt=prompt_for_exec,
            cwd=effective_cwd,
            image_paths=image_paths,
            model=model,
            reasoning_effort=reasoning_effort,
            permission_mode=runtime_permission_mode,
            approval_policy=runtime_approval,
            sandbox_mode=runtime_sandbox,
        )
        logger.debug(
            "Starting opencode stream session=%s cwd=%s model=%s effort=%s idle_timeout=%.1f max_timeout=%.1f command=%s",
            session_id_value, effective_cwd, model or "", reasoning_effort or "",
            self.idle_timeout_sec, self.max_timeout_sec, command,
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=effective_cwd,
                **_isolated_subprocess_kwargs(),
            )
            _ensure_stream_reader_limit(process.stdout)
        except FileNotFoundError as exc:
            await self._cleanup_images(created_images)
            raise OpencodeSessionChatError(f"opencode cli not found: {self.opencode_bin}") from exc

        await self._register_process(session_id_value, process)
        start_mono = time.monotonic()
        last_activity_mono = start_mono
        context_emitted = False
        text_emitted = False

        try:
            while True:
                now = time.monotonic()
                idle_elapsed = now - last_activity_mono
                total_elapsed = now - start_mono
                if idle_elapsed > self.idle_timeout_sec:
                    logger.warning(
                        "Opencode stream idle timeout session=%s idle=%.1fs total=%.1fs",
                        session_id_value, idle_elapsed, total_elapsed,
                    )
                    _kill_process_tree(process)
                    await process.wait()
                    raise OpencodeSessionChatError("opencode cli idle timeout")
                if total_elapsed > self.max_timeout_sec:
                    logger.warning(
                        "Opencode stream max timeout session=%s total=%.1fs",
                        session_id_value, total_elapsed,
                    )
                    _kill_process_tree(process)
                    await process.wait()
                    raise OpencodeSessionChatError("opencode cli max timeout")
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

                if event_type == "step_start":
                    if not context_emitted:
                        yield {
                            "type": "context",
                            "content": {
                                "model": str(model or ""),
                                "effort": str(reasoning_effort or ""),
                                "approval_policy": runtime_approval,
                                "sandbox_mode": runtime_sandbox,
                                "permission_mode": runtime_permission_mode,
                                "plan_mode": bool(plan_mode),
                                "plan_mode_fallback": plan_mode_fallback,
                            },
                        }
                        context_emitted = True
                    continue

                if event_type == "text":
                    part = event.get("part") if isinstance(event.get("part"), dict) else {}
                    text = str(part.get("text") or "").strip()
                    if text:
                        text_emitted = True
                        yield {"type": "text", "content": text}
                    continue

                if event_type == "step_finish":
                    part = event.get("part") if isinstance(event.get("part"), dict) else {}
                    tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
                    total_tokens = None
                    if isinstance(tokens, dict) and tokens.get("total"):
                        try:
                            total_tokens = max(0, int(tokens["total"]))
                        except (ValueError, TypeError):
                            pass
                    if total_tokens is not None:
                        yield {"type": "context", "content": {"total_tokens": total_tokens}}
                    if not text_emitted:
                        text = str(part.get("text") or "").strip()
                        if text:
                            yield {"type": "text", "content": text}
                    continue

                if event_type == "error":
                    error_data = event.get("error") if isinstance(event.get("error"), dict) else {}
                    error_name = str(error_data.get("name") or "")
                    error_msg_data = error_data.get("data")
                    error_message = str(error_msg_data.get("message") if isinstance(error_msg_data, dict) else str(error_msg_data) or "")
                    detail = error_message or error_name or "opencode cli error"
                    yield {"type": "error", "content": detail}
                    if process.returncode is None:
                        _kill_process_tree(process)
                        await process.wait()
                    return

            stderr_text = ""
            if process.stderr is not None:
                stderr_text = (await process.stderr.read()).decode("utf-8", errors="ignore").strip()
            code = await process.wait()
            if code != 0:
                detail = stderr_text or f"exit_code={code}"
                logger.warning(
                    "Opencode stream failed session=%s returncode=%s stderr=%s",
                    session_id_value, code, detail,
                )
                raise OpencodeSessionChatError(f"opencode cli failed: {detail}")
        finally:
            await self._unregister_process(session_id_value, process)
            await self._cleanup_images(created_images)

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
        raise OpencodeSessionChatError("opencode cli returned empty response")

    # ------------------------------------------------------------------
    # Approval (no-op - opencode manages permissions internally)
    # ------------------------------------------------------------------

    async def submit_approval(
        self,
        pending_id: str,
        decision: str,
        prefix_rule: Optional[list[str]] = None,
    ) -> bool:
        return False
