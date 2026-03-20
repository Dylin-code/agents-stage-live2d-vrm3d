import asyncio
import hashlib
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from live2d_server.configuration import Config
from live2d_server.session_bridge_chat import CodexSessionChatError, CodexSessionChatService

logger = logging.getLogger(__name__)

MOTION_SEMANTIC_SLOTS = [
    "IDLE",
    "THINKING",
    "TOOLING",
    "RESPONDING",
    "WAITING",
    "SUMMON",
    "TAP",
    "ERROR",
    "COMPLETE",
]
DEFAULT_MAPPING_MODEL = os.getenv("LIVE2D_MOTION_MAPPING_MODEL", "gpt-5-codex").strip() or "gpt-5-codex"
DEFAULT_MAPPING_CODEX_BIN = os.getenv("LIVE2D_MOTION_MAPPING_CODEX_BIN", "codex").strip() or "codex"
WARMUP_LIMIT = max(0, int(os.getenv("LIVE2D_MOTION_MAPPING_WARMUP_LIMIT", "0") or "0"))
_cache_lock = threading.Lock()
_warmup_started = False
_FRONTEND_DIR_NAMES = ("agents-stage-live2d-vrm3d-fe", "live2d-assistant-fe")
_BACKEND_DIR_NAMES = ("agents-stage-live2d-vrm3d-server", "live2d-assistant-server")


def _cache_file_path() -> Path:
    env_path = os.getenv("LIVE2D_MOTION_MAPPING_CACHE", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    repo_root = Path(__file__).resolve().parents[2]
    for dirname in _BACKEND_DIR_NAMES:
        candidate = repo_root / dirname
        if candidate.exists():
            return candidate / ".cache" / "live2d_motion_semantic_map.json"
    return repo_root / _BACKEND_DIR_NAMES[0] / ".cache" / "live2d_motion_semantic_map.json"


def _resolve_live2d_model_root(config: Config | None) -> Path:
    env_path = os.getenv("LIVE2D_MODEL_DIR", "").strip()
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path).expanduser())
    if config and config.server and config.server.staticPath:
        static_root = Path(config.server.staticPath).expanduser()
        candidates.append(static_root / "assets" / "models")
    repo_root = Path(__file__).resolve().parents[2]
    for dirname in _FRONTEND_DIR_NAMES:
        candidates.append(repo_root / dirname / "public" / "assets" / "models")
        candidates.append(Path.cwd().resolve().parent / dirname / "public" / "assets" / "models")
    for path in candidates:
        if path.exists() and path.is_dir():
            return path
    return candidates[0]


def _to_web_asset_path(abs_path: Path, models_root: Path) -> str:
    rel = abs_path.resolve().relative_to(models_root.resolve())
    return f"assets/models/{rel.as_posix()}"


def extract_motion_display_names(model_data: dict[str, Any]) -> list[str]:
    file_refs = model_data.get("FileReferences") if isinstance(model_data, dict) else {}
    motions = file_refs.get("Motions") if isinstance(file_refs, dict) else {}
    if not isinstance(motions, dict):
        return []
    names: list[str] = []
    for group, raw_items in motions.items():
        group_name = str(group or "").strip()
        if group_name:
            names.append(group_name)
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            file_path = str(item.get("File", "")).strip() if isinstance(item, dict) else ""
            file_name = file_path.split("/")[-1] if file_path else ""
            normalized = (
                file_name
                .replace(".motion3.json", "")
                .replace(".mtn", "")
                .strip()
            )
            if normalized:
                names.append(normalized)
    dedup: list[str] = []
    seen = set()
    for name in names:
        token = name.strip()
        if not token:
            continue
        lower = token.lower()
        if lower in seen:
            continue
        seen.add(lower)
        dedup.append(token)
    return dedup


def compute_motion_hash(motion_names: list[str]) -> str:
    normalized = [x.strip().lower() for x in motion_names if x and x.strip()]
    normalized.sort()
    digest = hashlib.sha1("\n".join(normalized).encode("utf-8")).hexdigest()
    return digest


def _read_cache_file() -> dict[str, Any]:
    path = _cache_file_path()
    if not path.exists():
        return {"version": 1, "models": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "models": {}}
        models = data.get("models")
        if not isinstance(models, dict):
            data["models"] = {}
        return data
    except Exception as error:
        logger.warning("Failed to read live2d motion mapping cache: %s", error)
        return {"version": 1, "models": {}}


def _write_cache_file(data: dict[str, Any]) -> None:
    path = _cache_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_cached_motion_semantic_map(model_path: str, motion_hash: str) -> dict[str, list[str]] | None:
    with _cache_lock:
        data = _read_cache_file()
    models = data.get("models")
    if not isinstance(models, dict):
        return None
    entry = models.get(model_path)
    if not isinstance(entry, dict):
        return None
    if str(entry.get("motion_hash", "")) != str(motion_hash):
        return None
    semantic = entry.get("semantic_motions")
    if not isinstance(semantic, dict):
        return None
    result: dict[str, list[str]] = {}
    for slot in MOTION_SEMANTIC_SLOTS:
        raw = semantic.get(slot)
        if isinstance(raw, list):
            result[slot] = [str(x).strip() for x in raw if str(x).strip()]
    return result


def _coerce_semantic_map(raw: Any, motion_names: list[str]) -> dict[str, list[str]]:
    valid = {name.strip().lower(): name for name in motion_names if name.strip()}
    result: dict[str, list[str]] = {slot: [] for slot in MOTION_SEMANTIC_SLOTS}
    if not isinstance(raw, dict):
        return result
    for slot in MOTION_SEMANTIC_SLOTS:
        values = raw.get(slot)
        if not isinstance(values, list):
            continue
        picked: list[str] = []
        seen = set()
        for item in values:
            key = str(item).strip().lower()
            if not key or key not in valid or key in seen:
                continue
            seen.add(key)
            picked.append(valid[key])
        result[slot] = picked
    return result


def _extract_first_json_object(content: str) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    if text.startswith("```"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return text[start:end + 1]
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return ""


def _build_mapping_prompt(model_name: str, motion_names: list[str]) -> str:
    return (
        "你是 Live2D 動作語意映射助手。\n"
        "請把動作名稱映射到語意槽位，僅回傳 JSON 物件，不要輸出其他文字。\n"
        f"槽位固定為: {', '.join(MOTION_SEMANTIC_SLOTS)}。\n"
        "規則:\n"
        "1) 每個槽位值是陣列，可為空陣列。\n"
        "2) 只能使用提供的動作名稱，不能自創。\n"
        "3) 同一動作可出現在多個槽位。\n"
        "4) 無法判斷就留空陣列。\n"
        f"模型: {model_name}\n"
        "可用動作:\n"
        + json.dumps(motion_names, ensure_ascii=False)
    )


async def _infer_semantic_map_with_codex(
    service: CodexSessionChatService,
    session_id: str,
    cwd: str,
    model_name: str,
    motion_names: list[str],
) -> dict[str, list[str]] | None:
    prompt = (
        _build_mapping_prompt(model_name=model_name, motion_names=motion_names)
    )
    try:
        content = await service.run_prompt(
            session_id=session_id,
            prompt=prompt,
            cwd=cwd,
            model=DEFAULT_MAPPING_MODEL,
            permission_mode="default",
        )
    except CodexSessionChatError as error:
        logger.warning("Codex semantic mapping failed for %s: %s", model_name, error)
        return None
    json_text = _extract_first_json_object(content)
    if not json_text:
        logger.warning("No JSON semantic mapping for %s: %s", model_name, content)
        return None
    try:
        parsed = json.loads(json_text)
    except Exception:
        logger.warning("Invalid semantic mapping json for %s: %s", model_name, json_text)
        return None
    return _coerce_semantic_map(parsed, motion_names)


def _collect_model_entries(config: Config | None) -> list[dict[str, Any]]:
    models_root = _resolve_live2d_model_root(config)
    if not models_root.exists() or not models_root.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    candidates = sorted(list(models_root.rglob("*.model3.json")) + list(models_root.rglob("*.model.json")))
    for model_json in candidates:
        try:
            data = json.loads(model_json.read_text(encoding="utf-8"))
        except Exception:
            continue
        motion_names = extract_motion_display_names(data)
        if not motion_names:
            continue
        try:
            model_path = _to_web_asset_path(model_json, models_root)
        except Exception:
            continue
        entries.append({
            "model_path": model_path,
            "model_name": model_json.parent.name,
            "motion_names": motion_names,
            "motion_hash": compute_motion_hash(motion_names),
        })
    return entries


def warmup_live2d_motion_mapping(config: Config | None = None) -> None:
    entries = _collect_model_entries(config)
    if WARMUP_LIMIT > 0:
        entries = entries[:WARMUP_LIMIT]
    if not entries:
        return
    with _cache_lock:
        cache = _read_cache_file()
    models = cache.get("models")
    if not isinstance(models, dict):
        models = {}
        cache["models"] = models
    default_cwd = str(Path.cwd().resolve())
    service = CodexSessionChatService(
        codex_bin=DEFAULT_MAPPING_CODEX_BIN,
        default_cwd=default_cwd,
    )
    mapper_session_id = ""
    try:
        payload = asyncio.run(service.create_session(
            cwd=default_cwd,
            model=DEFAULT_MAPPING_MODEL,
            permission_mode="default",
        ))
        mapper_session_id = str(payload.get("session_id") or "").strip()
    except Exception as error:
        logger.warning("Failed to create codex mapper session: %s", error)
        return
    if not mapper_session_id:
        logger.warning("Missing codex mapper session id")
        return
    changed = False
    for entry in entries:
        model_path = entry["model_path"]
        motion_hash = entry["motion_hash"]
        current = models.get(model_path)
        if isinstance(current, dict) and str(current.get("motion_hash", "")) == motion_hash:
            continue
        semantic = asyncio.run(_infer_semantic_map_with_codex(
            service=service,
            session_id=mapper_session_id,
            cwd=default_cwd,
            model_name=entry["model_name"],
            motion_names=entry["motion_names"],
        ))
        if not semantic:
            continue
        models[model_path] = {
            "motion_hash": motion_hash,
            "semantic_motions": semantic,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        changed = True
    if changed:
        with _cache_lock:
            _write_cache_file(cache)
        logger.info("Live2D motion semantic cache updated: %s", _cache_file_path())


def start_live2d_motion_mapping_warmup(config: Config | None = None, force: bool = False) -> None:
    global _warmup_started
    with _cache_lock:
        if _warmup_started and not force:
            return
        _warmup_started = True
    thread = threading.Thread(
        target=warmup_live2d_motion_mapping,
        args=(config,),
        daemon=True,
        name="live2d-motion-mapping-warmup",
    )
    thread.start()
