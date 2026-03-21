import argparse
import json
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from live2d_server.configuration import Config, RemoteConfig
from live2d_server.live2d_motion_mapping import start_live2d_motion_mapping_warmup
from live2d_server.rag.router import router as rag_router
from live2d_server.router import router, start_live2d_preview_warmup
from live2d_server.session_bridge import router as session_bridge_router
from live2d_server.session_bridge import start_session_bridge, stop_session_bridge

class SPAStaticFiles(StaticFiles):
    """SPA-friendly static files: serves index.html for any path without a matching file."""

    def __init__(self, *, directory: str, **kwargs):
        self._spa_directory = Path(directory)
        super().__init__(directory=directory, **kwargs)

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
            if response.status_code == 404:
                return FileResponse(self._spa_directory / "index.html")
            return response
        except Exception:
            return FileResponse(self._spa_directory / "index.html")


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

static_path = None
run_mode = "local"
app_config = Config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.include_router(router)
    app.include_router(rag_router)
    app.include_router(session_bridge_router)

    # Remote mode: mount auth router
    if run_mode == "remote":
        from live2d_server.auth import create_auth_router
        app.include_router(create_auth_router(app_config.remote))

    await start_session_bridge()
    start_live2d_preview_warmup()
    start_live2d_motion_mapping_warmup()
    if static_path:
        app.mount("/", SPAStaticFiles(directory=static_path, html=True), name="static")
    try:
        yield
    finally:
        await stop_session_bridge()


def _load_config(config_path: str | None) -> Config:
    """Load Config from a JSON file, falling back to defaults."""
    if not config_path:
        return Config()
    p = Path(config_path)
    if not p.exists():
        return Config()
    with open(p, encoding="utf-8") as f:
        return Config(**json.load(f))


def _ensure_jwt_secret(config: Config, config_path: str | None) -> Config:
    """Auto-generate jwt_secret if empty and persist it back to the config file."""
    if config.remote.jwt_secret:
        return config
    config.remote.jwt_secret = secrets.token_urlsafe(32)
    if config_path:
        p = Path(config_path)
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        data.setdefault("remote", {})["jwt_secret"] = config.remote.jwt_secret
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Auto-generated jwt_secret and saved to %s", config_path)
    return config


def create_app(mode: str = "local", config: Config | None = None) -> FastAPI:
    """Create the FastAPI application. Extracted for testability."""
    global run_mode, app_config
    run_mode = mode
    app_config = config or Config()

    app = FastAPI(lifespan=lifespan)

    # Store mode & config on app.state for middleware/WebSocket access
    app.state.mode = mode
    if mode == "remote":
        app.state.remote_config = app_config.remote

    # CORS — in remote mode, restrict to allowed_origin
    if mode == "remote" and app_config.remote.allowed_origin:
        origins = [app_config.remote.allowed_origin]
    else:
        origins = ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auth middleware (remote mode only) — added after CORS so CORS headers are set first
    if mode == "remote":
        from live2d_server.auth import AuthGuardMiddleware
        app.add_middleware(AuthGuardMiddleware, remote_config=app_config.remote)

    return app


def main():
    parser = argparse.ArgumentParser(description="啟動 Web 服務器")
    parser.add_argument('--host', type=str, default='0.0.0.0', help='服務器主機')
    parser.add_argument('--port', type=int, default=3000, help='服務器端口')
    parser.add_argument('--static-path', type=str, default=None, help='靜態文件路徑')
    parser.add_argument('--mode', choices=['local', 'remote'], default='local',
                        help='local: 無驗證 (預設), remote: Google OAuth2 驗證')
    parser.add_argument('--config', type=str, default=None, help='配置檔案路徑 (JSON)')
    args = parser.parse_args()

    global static_path
    static_path = args.static_path

    config = _load_config(args.config)
    if args.mode == "remote":
        config = _ensure_jwt_secret(config, args.config)

    app = create_app(mode=args.mode, config=config)
    uvicorn.run(app, host=args.host, port=args.port)


# Backward-compatible: module-level app for `uvicorn main:app`
app = create_app()

if __name__ == '__main__':
    main()
