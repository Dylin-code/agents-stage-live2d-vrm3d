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

## 版權與素材聲明

本專案雖然包含部分 2D / 3D 模型與相關範例資源，但這些資源均取自公開可取得的資源網站，僅供本專案測試、研究與功能驗證用途。

若你要在實際產品、商業場景、公開散布、二次創作或其他正式用途使用這些模型與素材，應自行確認並遵守各原始資源作者、發布頁面或來源平台所附帶的版權聲明、授權條款與使用限制。本專案的程式碼授權不等同於這些第三方模型素材的授權範圍。
