from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse
from fastapi import HTTPException
import os
import requests
import base64
import json
import asyncio
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import List, Optional, AsyncGenerator
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from pydantic import BaseModel
from live2d_server.configuration import Config
from live2d_server.client import get_mcp_client, MCPClientConfig, init_mcp_client
import logging
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph
from langgraph.types import Command
from langchain_core.messages import ToolMessage
from live2d_server.agent.agent import Agent
from live2d_server.agent.model import AgentConfig
from live2d_server.search.searx import search
from datetime import datetime
from live2d_server.rag.rag import search_knowledge_base
from live2d_server.live2d_motion_mapping import (
    compute_motion_hash,
    extract_motion_display_names,
    get_cached_motion_semantic_map,
    start_live2d_motion_mapping_warmup,
)

logger = logging.getLogger(__name__)

router = APIRouter()
_FRONTEND_DIR_NAMES = ("agents-stage-live2d-vrm3d-fe", "live2d-assistant-fe")

_PREVIEW_FILENAME = "__preview__.png"
_preview_warmup_lock = threading.Lock()
_preview_warmup_started = False

class ChatRequest(BaseModel):
    model: str
    chat_id: str
    messages: List[dict]
    is_resume: Optional[bool] = False
    web_search: Optional[bool] = False
    rag: Optional[bool] = False
    tts_enabled: Optional[bool] = False
    agents: Optional[List[AgentConfig]] = None

class ChatResponse(BaseModel):
    message: str
    wav_data: List[str]

class TTSRequest(BaseModel):
    text: str

class SelectDirectoryRequest(BaseModel):
    title: Optional[str] = None
    default_path: Optional[str] = None


def _resolve_live2d_model_root(config: Config | None) -> Path:
    env_path = os.getenv("LIVE2D_MODEL_DIR", "").strip()
    candidates: List[Path] = []
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


def _normalize_request_host(request: Request) -> str:
    host = (request.client.host if request.client else "").strip().lower()
    if host.startswith("::ffff:"):
        host = host.replace("::ffff:", "", 1)
    if "%" in host:
        host = host.split("%", 1)[0]
    return host


def _is_loopback_request(request: Request) -> bool:
    return _normalize_request_host(request) in {"127.0.0.1", "::1", "localhost"}


def _escape_applescript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _pick_directory_via_applescript(title: str, default_path: str) -> str:
    prompt = _escape_applescript_string(title)
    script_lines: List[str] = []
    if default_path:
        escaped_default = _escape_applescript_string(default_path)
        script_lines.append(
            f'set selectedFolder to choose folder with prompt "{prompt}" default location POSIX file "{escaped_default}"'
        )
    else:
        script_lines.append(f'set selectedFolder to choose folder with prompt "{prompt}"')
    script_lines.append("POSIX path of selectedFolder")
    process = subprocess.run(
        ["osascript", *sum([["-e", line] for line in script_lines], [])],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        return ""
    return process.stdout.strip()


def _pick_directory_via_tkinter(title: str, default_path: str) -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return ""

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(
            title=title,
            initialdir=default_path or os.getcwd(),
            mustexist=True,
            parent=root,
        )
        root.destroy()
        return (path or "").strip()
    except Exception:
        return ""


def _pick_directory_local(title: str, default_path: str) -> str:
    normalized_default = (default_path or "").strip()
    if normalized_default and not os.path.isdir(normalized_default):
        normalized_default = ""
    if sys.platform == "darwin":
        picked = _pick_directory_via_applescript(title, normalized_default)
        if picked:
            return picked
    return _pick_directory_via_tkinter(title, normalized_default)


def _guess_preview_image(model_json_path: Path, model_data: dict) -> str | None:
    generated_preview = model_json_path.parent / _PREVIEW_FILENAME
    if generated_preview.exists() and generated_preview.is_file():
        return str(generated_preview.resolve())

    file_refs = model_data.get("FileReferences") if isinstance(model_data, dict) else None
    textures = file_refs.get("Textures") if isinstance(file_refs, dict) else None
    if isinstance(textures, list) and textures:
        first_texture = str(textures[0]).strip()
        if first_texture:
            texture_path = (model_json_path.parent / first_texture).resolve()
            if texture_path.exists() and texture_path.is_file():
                return str(texture_path)

    for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        for candidate in sorted(model_json_path.parent.glob(ext)):
            if candidate.is_file():
                return str(candidate.resolve())
    return None


def _to_web_asset_path(abs_path: Path, models_root: Path) -> str:
    rel = abs_path.resolve().relative_to(models_root.resolve())
    return f"assets/models/{rel.as_posix()}"


def _resolve_renderer_libs() -> dict[str, Path] | None:
    repo_root = Path(__file__).resolve().parents[2]
    all_missing: list[str] = []
    for dirname in _FRONTEND_DIR_NAMES:
        libs = {
            "pixi.min.js": repo_root / dirname / "node_modules" / "pixi.js" / "dist" / "browser" / "pixi.min.js",
            "index.min.js": repo_root / dirname / "node_modules" / "pixi-live2d-display" / "dist" / "index.min.js",
            "live2dcubismcore.min.js": repo_root / dirname / "public" / "assets" / "live2dcubismcore.min.js",
            "live2d.min.js": repo_root / dirname / "public" / "assets" / "live2d.min.js",
        }
        missing = [name for name, path in libs.items() if not path.exists()]
        if not missing:
            return libs
        all_missing = missing
    logger.warning(f"Live2D preview warmup skipped (missing renderer assets): {all_missing}")
    return None


class _PreviewAssetsHandler(BaseHTTPRequestHandler):
    models_root: Path
    libs: dict[str, Path]
    renderer_html: bytes

    def log_message(self, fmt, *args):
        # 靜默本地渲染器請求，避免刷 log
        return

    def _send_file(self, path: Path, content_type: str):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path or "/")
        req_path = parsed.path or "/"
        if req_path == "/" or req_path == "/renderer.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(self.renderer_html)))
            self.end_headers()
            self.wfile.write(self.renderer_html)
            return

        if req_path.startswith("/libs/"):
            name = unquote(req_path.removeprefix("/libs/"))
            path = self.libs.get(name)
            if not path:
                self.send_error(404)
                return
            content_type = "application/javascript" if name.endswith(".js") else "application/octet-stream"
            self._send_file(path, content_type)
            return

        if req_path.startswith("/models/"):
            rel = unquote(req_path.removeprefix("/models/"))
            candidate = (self.models_root / rel).resolve()
            try:
                candidate.relative_to(self.models_root.resolve())
            except Exception:
                self.send_error(403)
                return
            suffix = candidate.suffix.lower()
            content_type = {
                ".json": "application/json; charset=utf-8",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".moc3": "application/octet-stream",
                ".moc": "application/octet-stream",
            }.get(suffix, "application/octet-stream")
            self._send_file(candidate, content_type)
            return

        self.send_error(404)


def _start_preview_asset_server(models_root: Path, libs: dict[str, Path]):
    renderer_html = """<!doctype html>
<html><head><meta charset="utf-8"><title>Live2D Preview</title>
<style>html,body{margin:0;background:transparent;overflow:hidden}</style>
<script src="/libs/pixi.min.js"></script>
<script src="/libs/live2dcubismcore.min.js"></script>
<script src="/libs/live2d.min.js"></script>
<script src="/libs/index.min.js"></script>
</head><body></body></html>""".encode("utf-8")
    _PreviewAssetsHandler.models_root = models_root.resolve()
    _PreviewAssetsHandler.libs = libs
    _PreviewAssetsHandler.renderer_html = renderer_html
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _PreviewAssetsHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="live2d-preview-assets-server")
    server_thread.start()
    host, port = httpd.server_address
    return httpd, server_thread, f"http://{host}:{port}"


def _render_preview_with_driver(driver, renderer_url: str, model_url: str, output_path: Path) -> bool:
    try:
        driver.set_script_timeout(40)
        driver.get(renderer_url)
        ready = driver.execute_script(
            "return !!(window.PIXI && PIXI.live2d && PIXI.live2d.Live2DModel);"
        )
        if not ready:
            return False
        script = """
const modelUrl = arguments[0];
const done = arguments[arguments.length - 1];
const LOAD_TIMEOUT_MS = 18000;
(async () => {
  try {
    const size = 512;
    const app = new PIXI.Application({
      width: size,
      height: size,
      backgroundAlpha: 0,
      antialias: true,
      preserveDrawingBuffer: true
    });
    document.body.innerHTML = '';
    document.body.appendChild(app.view);

    const loadWithTimeout = async (url, ms) => {
      return await Promise.race([
        PIXI.live2d.Live2DModel.from(url),
        new Promise((_, reject) => setTimeout(() => reject(new Error('model_load_timeout')), ms))
      ]);
    };
    const model = await loadWithTimeout(modelUrl, LOAD_TIMEOUT_MS);
    app.stage.addChild(model);
    model.anchor.set(0.5, 0.5);

    app.render();
    const bounds = model.getLocalBounds();
    const w = Math.max(bounds.width || 0, 1);
    const h = Math.max(bounds.height || 0, 1);
    const scale = Math.min((size * 0.82) / w, (size * 0.82) / h);
    model.scale.set(scale > 0 ? scale : 1);
    model.x = size / 2;
    model.y = size * 0.56;

    await new Promise((r) => setTimeout(r, 900));
    app.render();
    const dataUrl = app.view.toDataURL('image/png');
    done({ ok: true, dataUrl });
  } catch (err) {
    let detail = String(err);
    try {
      if (err && err.stack) detail = `${String(err)}\n${err.stack}`;
    } catch (_e) {}
    done({ ok: false, error: detail });
  }
})();
"""
        result = driver.execute_async_script(script, model_url)
        if not isinstance(result, dict):
            logger.warning(f"render preview failed model={model_url} error=invalid-script-result")
            return False
        if not result.get("ok"):
            logger.warning(f"render preview failed model={model_url} js_error={result.get('error')}")
            return False
        data_url = result.get("dataUrl", "")
        if not isinstance(data_url, str) or not data_url.startswith("data:image/png;base64,"):
            return False
        encoded = data_url.split(",", 1)[1]
        png_bytes = base64.b64decode(encoded)
        output_path.write_bytes(png_bytes)
        return True
    except Exception as e:
        logger.warning(f"render preview failed model={model_url} error={e}")
        return False


def _preview_runtime_ready(driver, renderer_url: str) -> bool:
    try:
        driver.get(renderer_url)
        return bool(driver.execute_script("return !!(window.PIXI && PIXI.live2d && PIXI.live2d.Live2DModel);"))
    except Exception:
        return False


def warmup_live2d_previews(config: Config | None = None):
    if os.getenv("LIVE2D_PREVIEW_WARMUP", "1").strip().lower() in ("0", "false", "no"):
        logger.info("Live2D preview warmup disabled by LIVE2D_PREVIEW_WARMUP")
        return
    models_root = _resolve_live2d_model_root(config)
    if not models_root.exists() or not models_root.is_dir():
        return

    try:
        limit_raw = os.getenv("LIVE2D_PREVIEW_WARMUP_LIMIT", "").strip()
        limit = int(limit_raw) if limit_raw else 0
    except Exception:
        limit = 0

    candidates = sorted(list(models_root.rglob("*.model3.json")) + list(models_root.rglob("*.model.json")))
    if limit > 0:
        candidates = candidates[:limit]

    try:
        from utils.selenium import WebDriverManager
    except Exception as e:
        logger.warning(f"Live2D preview warmup skipped (selenium utils unavailable): {e}")
        return

    manager = WebDriverManager()
    driver = None
    httpd = None
    server_thread = None
    try:
        libs = _resolve_renderer_libs()
        if not libs:
            return
        httpd, server_thread, base_url = _start_preview_asset_server(models_root, libs)
        renderer_url = f"{base_url}/renderer.html"
        driver = manager.create_webdriver()
        if not driver:
            logger.warning("Live2D preview warmup skipped (no webdriver/browser available)")
            return
        if not _preview_runtime_ready(driver, renderer_url):
            logger.warning("Live2D preview warmup skipped (renderer runtime unavailable in browser environment)")
            return
        ok_count = 0
        fail_count = 0
        for model_json in candidates:
            out_path = model_json.parent / _PREVIEW_FILENAME
            if out_path.exists() and out_path.is_file():
                continue
            rel_model = model_json.resolve().relative_to(models_root.resolve()).as_posix()
            model_url = f"{base_url}/models/{quote(rel_model, safe='/')}"
            success = _render_preview_with_driver(driver, renderer_url, model_url, out_path)
            if success:
                ok_count += 1
            else:
                fail_count += 1
        logger.info(f"Live2D preview warmup finished ok={ok_count} fail={fail_count} root={models_root}")
    except Exception as e:
        logger.warning(f"Live2D preview warmup failed: {e}")
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass
        try:
            if httpd:
                httpd.shutdown()
                httpd.server_close()
        except Exception:
            pass
        try:
            if server_thread:
                server_thread.join(timeout=1)
        except Exception:
            pass


def start_live2d_preview_warmup(config: Config | None = None):
    global _preview_warmup_started
    with _preview_warmup_lock:
        if _preview_warmup_started:
            return
        _preview_warmup_started = True
    thread = threading.Thread(
        target=warmup_live2d_previews,
        args=(config,),
        daemon=True,
        name="live2d-preview-warmup",
    )
    thread.start()

# 全局变量
config = None
tts_server = None
llm_adapter = None
# 一个agent缓存，用于处理agent再入的问题
agents = {}

def set_config(_config: Config):
    global config
    logger.info(f"set_config: {_config}")
    config = _config

def get_config():
    global config
    logger.info(f"get_config: {config}")
    return config

def get_tts_server():
    return tts_server

def get_llm_adapter():
    return llm_adapter

def init_tts_if_needed(config: Config, tts_server=None):
    """初始化TTS服务"""
    if config.server.tts.enabled and tts_server is None:
        from tts import tts_init, TtsServer
        cosyvoice, prompt_speech_16k = tts_init(
            config.server.tts.cosyvoiceInstallPath,
            config.server.tts.modulePath,
            config.server.tts.promptPath,
            config.server.tts.sampleRate,
            config.server.tts.promptText
        )
        return TtsServer(cosyvoice, prompt_speech_16k, config.server.tts.promptText)
    return tts_server

async def stream_chat_response(
    request: ChatRequest,
    config: Config = Depends(get_config),
    llm_adapter = Depends(get_llm_adapter),
    tts_server = Depends(get_tts_server)
) -> AsyncGenerator[str, None]:
    """生成流式聊天响应"""
    try:
        if request.web_search:
            # 由大模型构建搜索词之后进行搜索
            # yield f"data: {json.dumps({'type': 'text', 'content': '正在搜索中，请稍等...'})}\n\n"
            search_query = await get_mcp_client().get_llm_adapter().generate(model=request.model, prompt=f"""
            你是一个搜索专家，请根据用户的历史对话内容和当前的问题构建搜索词并返回，你应该仅返回搜索词，不要返回任何其他内容。
            用户的问题是：{request.messages[-1]['content']}
            用户的对话历史是：{request.messages[1:]}
            当前时间是：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            """)
            logger.info(f"search query: {search_query}")
            search_result = search(search_query)
            # 替换掉最后一个消息的content
            request.messages[-1]['content'] = f"使用如下的信息回答用户的问题：{search_result}，用户的问题是：{request.messages[-1]['content']}"
        if request.rag:
            # 使用RAG进行查询            
            search_query = await get_mcp_client().get_llm_adapter().generate(model=request.model, prompt=f"""
            你是一个搜索专家，请根据用户的历史对话内容和当前的问题构建RAG搜索词并返回，你应该仅返回搜索词，不要返回任何其他内容。
            用户的问题是：{request.messages[-1]['content']}
            用户的对话历史是：{request.messages[1:]}
            当前时间是：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            """)
            logger.info(f"search query: {search_query}")
            search_result = await search_knowledge_base(search_query)
            logger.info(f"search result: {search_result}")
            request.messages[-1]['content'] = f"使用如下的信息回答用户的问题：{search_result}，用户的问题是：{request.messages[-1]['content']}"
        async for chunk in get_mcp_client().stream_process_query(request.model, request.messages[-1]['content'], request.messages[:-1]):
            logger.info(f"stream_chat_response: {chunk}")
            yield f"data: {json.dumps(chunk)}\n\n"
            await asyncio.sleep(0)  # 让出控制权给其他协程
        
        # 如果需要TTS，生成音频数据c
        if request.tts_enabled:
            tts_server = init_tts_if_needed(config, tts_server)
            wav_data = tts_server.tts(chunk)
            for data in wav_data:
                base64_wave = base64.b64encode(data).decode('utf-8')
                yield f"data: {json.dumps({'type': 'audio', 'content': base64_wave})}\n\n"
                await asyncio.sleep(0)
        
        # 发送完成信号
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Error in stream_chat_response: {str(e)}")
        yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

@router.post('/api/chat')
async def chat(
    request: ChatRequest,
    config: Config = Depends(get_config),
    llm_adapter = Depends(get_llm_adapter),
    tts_server = Depends(get_tts_server)
):
    """流式聊天接口"""
    return StreamingResponse(
        stream_chat_response(request, config, llm_adapter, tts_server),
        media_type="text/event-stream"
    )

@router.post('/api/agentic/chat')
async def agentic_chat(
    request: ChatRequest
):
    """Agent 入口"""
    mcp_client = get_mcp_client()
    # from langchain_community.tools.searx_search.tool import SearxSearchRun
    # from langchain_community.utilities import SearxSearchWrapper
    # search_tool= SearxSearchRun(wrapper=SearxSearchWrapper(searx_host="http://127.0.0.1:8001", k=3))
    if request.chat_id not in agents:
        llm_adapter = mcp_client.get_llm_adapter()
        llm = ChatOpenAI(model=request.model, base_url=llm_adapter.base_url, api_key=llm_adapter.api_key)
        logger.info(f"agentic_chat: {request.agents}")
        agent = Agent(
            model_name=request.model,
            llm=llm,
            agents=request.agents,
            mcp_client=mcp_client,
        )
        graph: StateGraph = await agent.build()
        agents[request.chat_id] = graph
    else:
        graph = agents[request.chat_id]
    async def process():
        input_data = None
        if request.is_resume:
            input_data = Command(resume=request.messages[-1]['content'])
        else:
            input_data = {
                "messages": [
                    (
                        "user",
                        request.messages[-1]['content']
                    )
                ],
                "thread_id": request.chat_id,
                "waiting_for_input": False
            }
        stream = graph.astream(
            input_data,
            {"configurable": {"thread_id": request.chat_id}, "recursion_limit": 150},
            subgraphs=True,
            stream_mode="messages",
        )
        cache_tool_calls = {}
        async for s in stream:
            logger.info('-'*100)
            logger.info(s)
            logger.info('-'*100)
            agent, message_chunk = s
            message_chunk, _ = message_chunk
            if isinstance(message_chunk, ToolMessage):
                continue
            if hasattr(message_chunk, 'tool_call_chunks') and len(message_chunk.tool_call_chunks) > 0:
                tool_call_chunk = message_chunk.tool_call_chunks[0]
                logger.info(f"agentic_chat: {message_chunk.tool_call_chunks}")
                index = tool_call_chunk.get('index', 0)
                if not index:
                    index = 0
                name = tool_call_chunk.get('name', '')
                if not name:
                    name = ''
                arguments = tool_call_chunk.get('args', '')
                if not arguments:
                    arguments = ''
                if index not in cache_tool_calls:
                    cache_tool_calls[index] = {
                        "name": name,
                        "arguments": arguments
                    }
                else:
                    cache_tool_calls[index]['name'] += name
                    cache_tool_calls[index]['arguments'] += arguments
            elif hasattr(message_chunk, 'response_metadata') and message_chunk.response_metadata.get('finish_reason') == 'tool_calls':
                logger.info(f"agentic_chat: {message_chunk}")
                call = []
                logger.info(f"agentic_chat: {cache_tool_calls}")
                for c in cache_tool_calls.values():
                    c['arguments'] = json.loads(c['arguments'])
                    call.append(c)
                cache_tool_calls = {}
                # 如果工具调用中有 request_user_input 工具，则需要等待用户输入
                # if any(c['name'] == 'request_user_input' for c in call):
                #     tc = next(c for c in call if c['name'] == 'request_user_input')
                #     yield f"data: {json.dumps({'type': 'text', 'content': tc['arguments']['prompt']})}\n\n"
                #     break
                yield f"data: {json.dumps({'type': 'tool_calls', 'content': call})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'text', 'content': message_chunk.content})}\n\n" 
    
    return StreamingResponse(
        process(),
        media_type="text/event-stream",
    )

@router.post('/api/tts')
async def text_to_speech(
    request: TTSRequest,
    config: Config = Depends(get_config),
    tts_server = Depends(get_tts_server)
):
    tts_server = init_tts_if_needed(config, tts_server)
    wav_data = tts_server.tts(request.text)
    return wav_data

@router.get('/api/tags')
async def tags(config: Config = Depends(get_config)):
    resp = requests.get(config.OLLAMA_HOST + '/api/tags')
    return resp.json()

@router.get('/api/settings')
async def get_settings(config: Config = Depends(get_config)):
    '''
    获取系统设置
    '''
    return config

@router.post('/api/settings')
async def set_settings(request: Request):
    '''
    设置系统设置
    '''
    json_data = await request.body()
    try:
        request_data = MCPClientConfig.model_validate_json(json_data)
    except Exception as e:
        logger.error(f"Invalid /api/settings payload: {e}")
        raise HTTPException(status_code=400, detail=f"invalid_settings_payload: {e}")
    await init_mcp_client(request_data)
    start_live2d_motion_mapping_warmup(get_config(), force=True)
    return {"status": "ok"}

@router.get('/api/mcp_servers/{name}/status')
async def get_mcp_servers(name: str, config: Config = Depends(get_config)):
    '''
    获取MCP服务器状态
    '''
    return await get_mcp_client().get_server_details(name)


@router.get('/api/live2d/models')
async def list_live2d_models(config: Config = Depends(get_config)):
    models_root = _resolve_live2d_model_root(config)
    if not models_root.exists() or not models_root.is_dir():
        return {
            "root_dir": str(models_root),
            "models": [],
            "error": "models_root_not_found",
        }

    models = []
    candidates = sorted(list(models_root.rglob("*.model3.json")) + list(models_root.rglob("*.model.json")))
    for model_json in candidates:
        try:
            with model_json.open("r", encoding="utf-8") as f:
                model_data = json.load(f)
        except Exception as e:
            logger.warning(f"Invalid model json skipped: {model_json} error={e}")
            continue

        file_refs = model_data.get("FileReferences") if isinstance(model_data, dict) else {}
        motions = file_refs.get("Motions") if isinstance(file_refs, dict) else {}
        motion_names = sorted(list(motions.keys())) if isinstance(motions, dict) else []
        motion_display_names = extract_motion_display_names(model_data)
        motion_hash = compute_motion_hash(motion_display_names)
        preview_abs = _guess_preview_image(model_json, model_data)
        preview_web = None
        if preview_abs:
            try:
                preview_web = _to_web_asset_path(Path(preview_abs), models_root)
            except Exception:
                preview_web = None

        try:
            model_web = _to_web_asset_path(model_json, models_root)
        except Exception:
            continue

        models.append({
            "id": model_web,
            "name": model_json.parent.name,
            "model_path": model_web,
            "preview_image": preview_web,
            "motion_keys": motion_names,
            "motion_semantic_map": get_cached_motion_semantic_map(model_web, motion_hash) or {},
        })

    return {
        "root_dir": str(models_root),
        "models": models,
    }


@router.post('/api/local/select-directory')
async def select_local_directory(
    request: Request,
    payload: SelectDirectoryRequest,
):
    if not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="local_only")
    title = (payload.title or "選擇工作目錄").strip() or "選擇工作目錄"
    default_path = (payload.default_path or "").strip()
    picked = await asyncio.to_thread(_pick_directory_local, title, default_path)
    return {
        "path": picked,
        "canceled": not bool(picked),
    }



@router.get('/health')
async def health(config: Config = Depends(get_config)):
    return {
        'status': 'OK',
        'wwwPath': config.server.staticPath,
        'indexExists': os.path.exists(os.path.join(config.server.staticPath, 'index.html'))
    }
