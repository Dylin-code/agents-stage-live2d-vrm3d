from typing import Any, Dict, List
from pydantic import BaseModel
from live2d_server.client import LLMConfig, MCPServerConfig

class TTSConfig(BaseModel):
    enabled: bool = False
    modulePath: str = ""
    promptPath: str = ""
    promptText: str = ""
    sampleRate: int = 16000
    cosyvoiceInstallPath: str = ""

class ServerConfig(BaseModel):
    pythonExec: str = ""
    serverPath: str = ""
    port: int = 5000
    host: str = '0.0.0.0'
    staticPath: str = ''
    tts: TTSConfig = TTSConfig()
    mcp_servers: List[MCPServerConfig] = []
    llm: LLMConfig | None = None
    
class RemoteConfig(BaseModel):
    google_client_id: str = ""
    google_client_secret: str = ""
    jwt_secret: str = ""
    allowed_emails: List[str] = []
    allowed_origin: str = ""
    cookie_max_age: int = 86400  # 24 hours

class Config(BaseModel):
    debug: bool = False
    server: ServerConfig = ServerConfig()
    remote: RemoteConfig = RemoteConfig()