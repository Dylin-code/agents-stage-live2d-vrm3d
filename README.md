# Agents Stage Live2D / VRM3D

本專案從 Live2D Assistant 衍生為一個面向本地 AI coding agent 工作流的可視化控制台，核心能力不再只是 2D 角色展示，而是：

- 多品牌 agent 統一控制與會話橋接
- 2D Live2D 與 3D VRM 雙舞台渲染
- Session / 對話 / 審批 / 品牌模型切換的整合操作
- 可擴充的 3D 場景、互動點、行為流與 VRMA 動畫框架

目前專案重心是 `Codex Session` 與 `Claude Code` 這類本地 agent CLI 的視覺化調度與舞台化呈現。

## 核心能力

### 1. 多品牌 agent 控制

後端已提供統一的 `session-bridge` 能力，前端可直接以同一套 UI 管理不同品牌 agent。

目前內建品牌：

- `Codex`
- `Claude`

支援能力包含：

- 建立 brand-aware 新 session
- 依品牌切換可選模型清單
- 統一聊天串流與工具審批流程
- 讀取不同品牌的本地 session 目錄並同步狀態
- 對外提供品牌 catalog API：`/api/session-bridge/agent/brands`

預設 session 目錄：

- `CODEX_SESSION_DIR`，預設 `~/.codex/sessions`
- `CLAUDE_SESSION_DIR`，預設 `~/.claude/projects`

### 2. 2D Session Stage

`/` 與 `/session-stage` 為 2D Live2D 入口，適合用來：

- 觀察目前活躍 session 狀態
- 直接建立與切換 session
- 開啟對話視窗與送出 prompt
- 處理 approval / sandbox 相關互動
- 以 Live2D 角色方式展示 session

### 3. 3D Session Stage

`/session-stage-3d` 提供完整的 3D VRM 舞台執行環境，不只是把 2D 角色換成 3D 模型，而是一套更完整的場景 runtime：

- 最多同時顯示 4 個活躍 session actor
- 使用 `three.js`、`@pixiv/three-vrm`、`@pixiv/three-vrm-animation`
- 支援固定 actor slot 與 VRM 模型配置
- 支援 VRMA 動畫播放、漫遊、跳躍、待機等行為
- 支援自訂 camera 視角儲存
- 支援 global actor scale / ground offset
- 支援互動點編輯器
- 支援行為流編輯器與測試執行
- 支援 3D 場景載入與 actor 路徑/互動行為調度

這個 3D 框架已可作為後續擴充多角色舞台演出、品牌角色映射、事件編排與互動敘事的基礎。

## 系統架構

### Frontend

路徑：`agents-stage-live2d-vrm3d-fe`

技術棧：

- `Vue 3`
- `Vite`
- `PixiJS` + `pixi-live2d-display`
- `three.js`
- `@pixiv/three-vrm`
- `@pixiv/three-vrm-animation`

主要路由：

- `/`：Session Stage（預設首頁）
- `/session-stage`：相容舊入口
- `/session-stage-3d`：3D VRM 舞台

### Backend

路徑：`agents-stage-live2d-vrm3d-server`

技術棧：

- `FastAPI`
- `uvicorn`
- 本地 agent CLI bridge

主要能力：

- Session snapshot / history / conversation 聚合
- WebSocket session event 推送
- Codex / Claude brand router
- 統一 agent chat / approval / new session API
- Live2D 預覽與動作語意映射 warmup

## 安裝需求

建議先準備以下環境：

- Node.js / npm
- Python `>= 3.13`
- `uv`
- 已可在本機執行的 `codex` CLI
- 如需 Claude 品牌，另需可在本機執行的 `claude` CLI

## 安裝

### 1. 安裝前端依賴

```bash
cd agents-stage-live2d-vrm3d-fe
npm install
```

### 2. 建立後端虛擬環境並安裝依賴

```bash
cd ../agents-stage-live2d-vrm3d-server
uv venv
uv sync
```

## 啟動方式

### 一鍵啟動

在專案根目錄執行：

```bash
make dev
```

預設服務位址：

- Frontend: `http://127.0.0.1:5173`
- Backend: `http://127.0.0.1:8000`

### 手動啟動

```bash
# terminal 1
cd agents-stage-live2d-vrm3d-server
.venv/bin/python main.py --host 127.0.0.1 --port 8000

# terminal 2
cd agents-stage-live2d-vrm3d-fe
npm run dev
```

## 使用方式

### 1. 開啟 Session Stage

進入：

- `http://127.0.0.1:5173/`
- 或 `http://127.0.0.1:5173/session-stage`

這裡是預設的 2D 控制台，適合進行日常 session 管理與對話操作。

### 2. 建立新 session

在介面中建立 session 時，可直接設定：

- `agent brand`，例如 `codex` 或 `claude`
- `model`
- `cwd`
- `permission mode`
- `plan mode`

前端會透過統一 API 建立 brand-aware session，不需要為不同品牌切換不同頁面。

### 3. 在 3D 舞台檢視 agent

進入：

`http://127.0.0.1:5173/session-stage-3d`

3D 舞台會將目前可見 session 映射為 VRM actor，並依狀態、互動與行為流驅動舞台中的角色表現。

適合用來：

- 同步觀察多個 session 的活躍狀態
- 測試 VRM / VRMA 資源
- 編輯互動點與角色行為流
- 驗證 3D 場景與角色調度效果

### 4. Session Bridge API

常用 API：

- `GET /api/session-bridge/health`
- `GET /api/session-bridge/snapshot`
- `GET /api/session-bridge/history`
- `GET /api/session-bridge/conversation/{session_id}`
- `POST /api/session-bridge/agent/session/new`
- `POST /api/session-bridge/agent/chat`
- `POST /api/session-bridge/agent/chat/approval`
- `GET /api/session-bridge/agent/brands`
- `WS /api/session-bridge/ws`

如果你要把這個專案接到其他前端或自動化流程，優先從這組 API 開始整合。

## 專案目錄

```text
.
├── agents-stage-live2d-vrm3d-fe      # Vue / Live2D / VRM3D 前端
├── agents-stage-live2d-vrm3d-server  # FastAPI / session bridge 後端
├── Makefile                          # 一鍵啟動與建置入口
└── README.md
```

## 資源與相關工具

- Live2D 模型資源：
  - [Eikanya/Live2d-model](https://github.com/Eikanya/Live2d-model)
- VRM / 3D 場景資源：
  - [ニコニ立体](https://3d.nicovideo.jp/)
  - [Sketchfab](https://sketchfab.com/)
- FBX 轉 VRMA 工具：
  - [tk256ailab/fbx2vrma-converter](https://github.com/tk256ailab/fbx2vrma-converter)

## 更新紀錄

### 2026-03-21：Remote Mode — 遠端登入與 Google OAuth2 認證

新增 remote mode，讓你可以透過 Cloudflare Tunnel、ngrok 等方式從外部安全存取本專案，不再限於本機使用。

#### 新增功能

- **`--mode local|remote` 啟動旗標**：後端新增模式切換，`local`（預設）行為完全不變，`remote` 模式會啟用認證閘道
- **Google OAuth2 登入**：透過 `fastapi-sso` 整合 Google 登入，使用者以 Google 帳號認證後取得 JWT HttpOnly cookie
- **Email 白名單**：僅允許 `config.json` 中 `allowed_emails` 清單內的信箱登入
- **Auth Guard Middleware**：remote 模式下所有 API 與頁面皆受 JWT 驗證保護，未登入自動導向 `/login`
- **WebSocket 認證**：WebSocket handshake 時同樣檢查 JWT cookie
- **前端 Login 頁面與 Router Guard**：新增 `/login` 頁面與前端路由守衛，未認證時自動跳轉
- **Makefile `dev-remote` target**：一鍵以 remote 模式啟動

#### 設定方式

1. **Google Cloud Console 設定 OAuth2**
   - 前往 [Google Cloud Console](https://console.cloud.google.com/) 建立或選擇專案
   - 啟用 OAuth consent screen，設定應用程式名稱與授權網域
   - 建立 OAuth 2.0 Client ID（Web application 類型）
   - 在「Authorized redirect URIs」加入：`{your_origin}/api/auth/callback`（例如 `https://agents-stage.your-domain.com/api/auth/callback`）
   - 記下 `Client ID` 與 `Client Secret`

2. **建立 `config.json`**（參考 `config.example.json`）

   ```json
   {
     "remote": {
       "google_client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
       "google_client_secret": "GOCSPX-YOUR_SECRET",
       "jwt_secret": "",
       "allowed_emails": ["your-email@gmail.com"],
       "allowed_origin": "https://agents-stage.your-domain.com",
       "cookie_max_age": 86400
     }
   }
   ```

   - `jwt_secret`：留空會自動產生隨機 secret（每次重啟會失效，建議自行填入固定值）
   - `allowed_emails`：Email 白名單，僅清單中的 Google 帳號可登入
   - `allowed_origin`：你的外部存取網域

3. **啟動 remote 模式**

   ```bash
   make dev-remote
   ```

   或手動：

   ```bash
   cd agents-stage-live2d-vrm3d-server
   .venv/bin/python main.py --mode remote --host 0.0.0.0 --port 8000 --static-path ../agents-stage-live2d-vrm3d-fe/dist
   ```

#### 注意事項

- Remote 模式下前端需先 build（`npm run build`），由 FastAPI 提供靜態檔案，確保 same-origin 避免 CORS 問題
- 本功能設計為單人單機使用，不包含多使用者隔離機制
- Local 模式完全不受影響，無需任何設定即可照常使用

### 2026-03-22：手機直式瀏覽優化（2D 舞台）

針對手機直式（portrait）螢幕進行 2D 舞台 UI 重新排版，提升行動裝置瀏覽體驗。

#### 畫面佈局

- **角色單隻顯示**：直式模式下僅顯示一隻角色，左右滑動切換，移除上方角色數量指示點
- **歷史對話列表收合**：預設隱藏側邊欄，點擊右上角 ☰ 按鈕展開
- **頂部狀態列精簡**：狀態 chip、切換 3D 按鈕、齒輪設定等元素縮小並強制單行排列，可橫向滑動

#### 互動方式統一

- **點擊角色 → 播放動作**：2D 模式下不分直式橫式，單擊角色統一觸發隨機動作（與 3D 版雙擊行為對齊）
- **點擊頭頂氣泡 → 開啟聊天**：統一由角色頭頂的對話氣泡開啟聊天視窗，移除右下角 💬 浮動按鈕

#### 對話視窗

- **全透明底色 + 半透明氣泡**：對話視窗背景全透明，氣泡採半透明設計，可透視角色動作
- **文字顏色適配深色背景**：Agent 回應文字、Markdown 標題等統一改為白色系，確保在半透明深色氣泡上清晰可讀
- **推理設定區塊收合**：Model 推理參數區塊預設收合，點擊展開
- **角色不再上縮**：直式模式下開啟對話框時，角色位置維持不變

## 版權與素材聲明

本專案雖然包含部分 2D / 3D 模型與相關範例資源，但這些資源均取自公開可取得的資源網站，僅供本專案測試、研究與功能驗證用途。

若你要在實際產品、商業場景、公開散布、二次創作或其他正式用途使用這些模型與素材，應自行確認並遵守各原始資源作者、發布頁面或來源平台所附帶的版權聲明、授權條款與使用限制。本專案的程式碼授權不等同於這些第三方模型素材的授權範圍。
