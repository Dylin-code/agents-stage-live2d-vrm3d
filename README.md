# Agents Stage Live2D / VRM3D

本專案從 Live2D Assistant 衍生為一個面向本地 AI coding agent 工作流的可視化控制台，核心能力不再只是 2D 角色展示，而是：

- 多品牌 agent 統一控制與會話橋接
- 2D Live2D 與 3D VRM 雙舞台渲染
- 省電模式可停止 2D / 3D 動畫渲染，狀態保存在瀏覽器 localStorage
- Session / 對話 / 審批 / 品牌模型切換的整合操作
- 可擴充的 3D 場景、互動點、行為流與 VRMA 動畫框架

目前專案重心是 `Codex Session`、`Claude Code`、`OpenCode` 這類本地 agent CLI 的視覺化調度與舞台化呈現。

## 重要更新：TUI Bridge（2026-05-25，取代舊 Web Terminal）

舊 Web Terminal（單純把 shell PTY 直送 xterm.js）已下架，改由 **TUI Bridge** 取代：每條前端終端視窗背後接的是一個 **tmux session**，session 由 backend 透過 tmux/psmux 維持壽命，跟 WebSocket 解耦。

### 跟舊版的差別

- **可 detach / 重新 attach**：關掉瀏覽器視窗只切斷 WebSocket，session 內的進程繼續活著；之後從 sessions 列表點同一個 id 又能接回去，xterm.js 拿到完整歷史輸出
- **能跑互動式 TUI**：tmux + alternate screen + bracketed paste / mouse 都正常，可以在裡面跑 `claude`、`codex`、`vim`、`btop` 這類重型 TUI
- **支援多 session**：每個 session 是獨立的 tmux instance，由前端右下角面板管理
- **跨平台**：
  - macOS / Linux：標準 `tmux` + Python `pty.fork()`
  - Windows：`psmux`（tmux for Windows，提供 `tmux.exe` 別名）+ `pywinpty`

### 環境變數

| 變數 | 預設值 | 說明 |
|---|---|---|
| `TUI_BRIDGE_ENABLED` | `false` | 設 `true` 啟用，前端右下面板才會出現 |
| `TUI_BRIDGE_MAX_SESSIONS` | `8` | 同時可存在的 tmux session 上限 |
| `TUI_BRIDGE_DEFAULT_CMD` | _(空)_ | 留空：Unix 用 `$SHELL -l`，Windows 用 pwsh / powershell |
| `TUI_BRIDGE_METADATA_PATH` | _(空)_ | label / cwd / created_at sidecar 路徑（預設 `~/.cache/agents-stage-live2d-vrm3d/tui-sessions.json`） |
| `TUI_BRIDGE_TMUX_PATH` | _(空)_ | 手動指定 tmux/psmux 絕對路徑（PATH 找不到時的逃生口） |

### API 端點

- `GET    /api/tui/config` — 回 `{enabled, has_tmux, max_sessions, active_sessions}`
- `GET    /api/tui/sessions` — 列出現存 tmux session（含 label / cwd / command / 連線數）
- `POST   /api/tui/sessions` — 新建一個 session（payload：`{label, cwd, command}`）
- `DELETE /api/tui/sessions/{id}` — kill 指定 session
- `WS     /api/tui/ws?session_id=<id>&cols=&rows=` — attach 到指定 session

### 安全警語

> **此功能等同於將後端主機的完整 shell 暴露至瀏覽器。**
>
> - **預設關閉**：須在 `.env` 設 `TUI_BRIDGE_ENABLED=true` 才啟用。
> - **Local 模式**：僅限本機存取，仍請注意不要在公開網路上啟動未受保護的 local 模式。
> - **Remote 模式**：WebSocket 受現有 JWT 認證保護，但本質仍是遠端 shell —— 嚴格控管 email 白名單、必走 HTTPS、避免在不受信任網路使用。
> - 跟舊 Web Terminal 不同的是，session **跨連線存活**：使用者離線後 session 仍在後端運行，須透過 DELETE API 主動 kill 才會釋放。

## 重要更新：總控 Agent（2026-05-13）

新增「總控 Agent」單一對口入口，前端只需跟總控對話，由總控自主決定何時派發任務給 codex 或 claude 工人，並追蹤子任務狀態。

### 設計重點

- **總控自身不直接操作系統**：所有 CLI 指令組裝與 subprocess 都仍由既有 `AgentProviderRouter` / `SessionBridgeService` 負責；總控只透過 ToolPort 抽象呼叫這些既有服務。
- **非同步派發**：`*_send_prompt` 工具立即回傳 `subtask_id`，子任務在背景透過 `asyncio.create_task` 跑 `stream_prompt`，由 `SubTaskTracker` 追蹤狀態與透穿事件。
- **多 LLM provider**：透過 env 切換 Anthropic API、OpenAI API、或本地 OpenAI-compatible（Ollama / LM Studio）。
- **新頁面、新 API、不影響既有**：舊 `/api/session-bridge/*` 路由與 `/session-stage` 頁面完全保留。

### 新增 API 路由（`/api/master-agent/*`）

- `POST /conversation/new` — 建立主控對話
- `POST /chat` — SSE，串流總控 hop 事件（thinking / tool_call_begin / tool_call_end / final_text / error）
- `POST /abort` — 中止總控 LLM 迴圈
- `GET  /snapshot?conversation_id=` — 主對話 + 子任務快照
- `GET  /subtasks?conversation_id=` — 子任務列表
- `GET  /subtasks/{id}` — 子任務詳情
- `GET  /llm/info` — 目前啟用的 LLM provider / model
- `WS   /ws` — 廣播 SubTask 狀態變動到所有 client（多 tab / 桌面 widget 同步），前端按 `conversation_id` 過濾、exponential backoff 重連

### 前端入口

`/master-agent` 為新獨立頁面，左側為總控對話 + 思考軌跡，右側為子任務卡片列表（顯示 brand、session、狀態、最新事件、final_text）。

**RWD / 行動裝置**：頁面採 dark theme + radial gradient 底圖，沒有 Live2D 元素（純功能介面）。寬螢幕雙欄；手機（≤768px）切換為單欄聊天 + 任務列表收進右側 drawer，topbar 顯示「任務 (N)」按鈕展開。`100dvh` + `env(safe-area-inset-*)` 處理 iOS notch / Android 手勢列，輸入框字級 ≥16px 避免 iOS 自動 zoom。

**對話持久化**：master agent 對話以 JSON 寫入 `config/master-agent/conversations/<conversation_id>.json`（env `MASTER_AGENT_CONVERSATION_DIR` 可改路徑）。每個 user message / hop 完成 / tool result 都 atomic write。Server 重啟後同 conversation_id 可從 disk hydrate 繼續聊。前端 localStorage 存 conversation_id，refresh 後自動接回；server 端找不到（手動刪檔）則自動建新對話。

**派工確認 gate**：總控第一次要呼叫 `*_new_session` 前必須先 `report_to_user` 印出完整計畫（agent_brand / cwd / model / reasoning_effort / permission_mode / plan_mode / 預計 first prompt）讓使用者檢查 + 修改。使用者回 OK / 確認後下一回合才實際派出。Resume 既有 session、唯讀工具（query / list / browse / search）**不受**此 gate 規範。

**Chat 快捷指令**：
- `#new` — 開新對話（等同點「開新對話」按鈕）
- `#new <text>` — 開新對話並把 `<text>` 當成第一句訊息送出
- `#full` — 出現在訊息任何位置（會被剝離）→ 該回合允許 `permission_mode=full`（完全沒沙箱）。沒這關鍵字時，LLM 試圖用 `full` 會被自動降為 `auto`
- 可組合：`#new #full do dangerous thing` = 開新對話 + 解鎖 full + 送出 `do dangerous thing`

**權限策略（Codex 自動模式預設、`full` gated）**：
- Master agent 預設使用 provider default：
  - codex CLI 跑 `codex exec --sandbox <automation-sandbox> …`；非 Windows 使用 `workspace-write`，Windows 因 Codex CLI sandbox 會在 shell tool 噴 `CreateProcessAsUserW failed: 5`，改用 `danger-full-access`
  - claude CLI 跑 `claude -p --permission-mode auto …` → 走 claude 內建 auto classifier
- LLM 明確指定 `permission_mode=auto` → codex 走 `-a on-request` + `approvals_reviewer="auto_review"`；claude 走 `--permission-mode auto`
- LLM 明確指定 `permission_mode=plan` → 照用
- LLM 明確指定 `permission_mode=full` → **預設拒絕、降回 provider default**；只有使用者訊息含 `#full` 時 API 端會帶 `permit_full_access=true`，這時 `full` 才被認可，CLI 才會掛 `--dangerously-bypass-approvals-and-sandbox` / `--dangerously-skip-permissions`

### 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `MASTER_AGENT_LLM_PROVIDER` | `anthropic` | `anthropic` / `openai` / `local` |
| `MASTER_AGENT_LLM_MODEL` | provider-specific | 例 `claude-sonnet-4-6` / `gpt-4o-mini` / `qwen2.5:14b` |
| `MASTER_AGENT_LLM_API_KEY` | — | Anthropic / OpenAI key；`local` 可省略 |
| `MASTER_AGENT_LLM_BASE_URL` | — | local 模式必填，例 `http://localhost:11434/v1` |
| `MASTER_AGENT_LLM_TOOL_MODE` | `auto` | `auto` / `native` / `prompt`；auto 時 local→prompt，其餘→native |

### Tool mode 說明

- **native**：用 provider 原生的 function calling（Anthropic `tools=` / OpenAI `tools=` 欄位）。Anthropic、OpenAI 正規模型穩定可用。
- **prompt**：把工具規格塞進 system prompt，要求模型輸出 `{"tool": "name", "args": {...}}` JSON；由 `tool_call_parser.py` 解析。適用：
  - 本地 vLLM / LM Studio 沒開 `--enable-auto-tool-choice`、或 model 沒對應 tool parser 時
  - 雜牌 OpenAI-compatible endpoint
  - 任何純文字 chat model
- 每回合僅一次工具呼叫（連續派工要分多 hop，`MAX_HOPS=8`）。

### 工具清單（13 個）

**Session 派發（每回合一個）**
- `codex_new_session` / `claude_new_session` — 在指定 cwd 開新 session
- `codex_send_prompt` / `claude_send_prompt` — 非同步派任務，立即回 `subtask_id`

**診斷（讀取，無副作用）**
- `query_session_status(session_id? | subtask_id?)` — 查單一 session/subtask 狀態
- `list_sessions(active_only?)` — 列出 bridge 中所有 session
- `list_subtasks(status?)` — 列出本對話派出的子任務
- `list_history_sessions(agent_brand?, cwd_substring?, limit?)` — 列出磁碟上的歷史 session（用來找要 resume 的 session_id）
- `get_session_conversation(session_id, limit?)` — 讀某個 session 最近 N 條訊息
- `search_sessions(query, agent_brand?, cwd_substring?, limit?)` — 用關鍵字 substring 掃 codex/claude session JSONL 內容，回傳命中行 snippet（用來模糊找「上次改 auth 那個 session」）
- `list_available_models(agent_brand?)` — 查每個品牌支援的 model 清單與預設 permission_mode
- `browse_directories(path?)` — 列指定路徑下的子目錄（同舞台 cwd picker 的後端），用來把「桌面的 my-repo」這種模糊描述解成絕對路徑；不給 path 回 drive roots / `/`，上限 200 entries

**模型參數調整**：`*_new_session` 與 `*_send_prompt` 都接受 `model` / `reasoning_effort` (`minimal`/`low`/`medium`/`high`/`xhigh`) / `permission_mode` (`default`/`auto`/`plan`/`full`) / `plan_mode`。new_session 設的是 session 預設值；send_prompt 設的是該單回合 override。Codex 的 `default` 會走平台自動模式；只有明確選 `full` 才會掛 `--dangerously-bypass-approvals-and-sandbox`。

**長任務 timeout**：

- 總控 `wait_for_subtask` 預設 5 分鐘、上限 30 分鐘；env `MASTER_AGENT_WAIT_DEFAULT_SEC` / `MASTER_AGENT_WAIT_MAX_SEC` 可調。
- Timeout 時會回傳 `partial_text`（已串流出來的內容），system prompt 指示總控可再次 `wait_for_subtask` 繼續等。
- 底層 CLI 自己也有 idle/max timeout（預設 180s / 1800s），env 變數：`CODEX_CLI_IDLE_TIMEOUT_SEC`、`CODEX_CLI_MAX_TIMEOUT_SEC`、`CLAUDE_CLI_IDLE_TIMEOUT_SEC`、`CLAUDE_CLI_MAX_TIMEOUT_SEC`。Idle = subprocess 多久沒輸出就 kill；reasoning model 跑久的話建議 bump 到 300+。

**斷線復原（detached status）**：

當 master 與 worker 的 stream 死掉（idle timeout、parser error、subprocess hang）但 bridge_service 的 disk scan 顯示 session JSONL 在最近 60 秒仍有事件，subtask 不會被標 `failed`，而是改標**`detached`**（terminal）。`wait_for_subtask` 看到 detached 時會明示總控用 `query_session_status(session_id=...)` 走 disk-backed 路徑追蹤後續、或用 `*_send_prompt` 同 session 接續對話。這條 fallback 走的是 `_scan_once_claude` / `_scan_once` 那條 disk truth 軌道，跟原本 `/session-stage` 看到的是同一份資料。

**Resume**：沒有專屬 `resume_*` 工具 — `codex_send_prompt` / `claude_send_prompt` 對歷史 session_id 自動續聊（CLI 內建 `resume`）。流程：`list_history_sessions` → 挑 session_id → 可選 `get_session_conversation` 看上下文 → `*_send_prompt` 帶該 session_id 派新 prompt。

**Windows async subprocess**：Codex / Claude worker 由 backend 透過 `asyncio.create_subprocess_exec` 啟動。Windows 必須使用 `WindowsProactorEventLoopPolicy`，否則 Selector loop 會丟出空字串 `NotImplementedError`，導致導演只看到 `*_create_session failed:`。`main.py` 會在啟動時強制設定 Proactor policy，bridge 也會把 subprocess spawn 失敗轉成可診斷訊息。

**等待 / 控制平面**
- `wait_for_subtask(subtask_id, timeout_sec=60)` — 阻塞至子任務 done/failed/aborted 或 timeout，把進度事件透穿到主 SSE
- `abort_session(session_id, agent_brand)` — 強殺 codex/claude subprocess
- `approve_pending(pending_id, decision, agent_brand, prefix_rule?)` — 處理 worker 的 `approval_request`

**Git**
- `list_branches(cwd?)` — 列分支 + 當前分支
- `switch_git_branch(branch, cwd?)` — 切分支（先 `git switch`，舊版 git fallback `git checkout`）

**Terminator**
- `report_to_user(text)` — 結束本回合，把 text 顯示給使用者

## 重要更新：專案登錄表 → 導演直接知道每個專案在哪（2026-05-16）

不必再每次告訴導演「Kokoro-Link 在哪個資料夾」。導演啟動時會把已知專案的名稱與 cwd 寫進 system prompt——說「派工到 kokoro」就能直接帶著正確 cwd 派出，不必反問。導演也能在用戶第一次提到新專案、用 `browse_directories` 找到位置後**自己把該專案記下來**，下次就直接認得。

### 資料來源（兩層，全部選填）

1. **`config/master-agent/projects.yaml`**（隨專案的「導演記憶」檔）
   導演透過 `register_project` 工具自動寫入這個檔，也可以手動編輯。每個 clone 各自獨立——他人 clone 不會繼承你的本地映射。範例：
   ```yaml
   projects:
     - name: scratch
       cwd: C:\Users\User\Desktop\scratch
       aliases: [scratchpad, 草稿]
       description: 一次性實驗用
   ```
2. **`~/.config/dev-registry/services.yaml`**（選填的全機共用面板）
   若你像本機作者一樣維護一份 dev-registry（port + cwd 統一登錄），導演會額外讀取，按 `cwd` 收斂同一個 cwd 下的所有 service（如 kokoro-api / kokoro / kokoro-postgres / kokoro-tts）歸為一個專案。**沒有這個檔案完全不影響**——導演會 fallback 到「Known projects 空、需要時用 `browse_directories` + `register_project` 自學」流程。

兩個檔案在每次新對話開始時讀，編輯 YAML 不必重啟伺服器。

### 系統提示注入

導演的 system prompt 多一段「Known projects」清單，每個專案一行：

```
Known projects — use these cwds directly without asking the user. ...
After ``browse_directories`` finds a project the user named but isn't in
this list, call ``register_project`` to persist it ...
- agents-stage → C:\Users\User\Desktop\agents-stage-live2d-vrm3d\... [aliases: ...]
- kokoro-link → C:\Users\User\Desktop\Kokoro-Link [aliases: kokoro-api, kokoro, ...]
- ...
```

兩個檔案都沒有時，這段會降級成一行學習提示：「No projects registered yet. When the user names a project, use ``browse_directories`` ... then call ``register_project`` ...」，讓 LLM 知道有自學途徑。

### 新增工具

| 工具 | 用途 |
|---|---|
| `list_projects` | 列出所有已知專案（name / cwd / aliases / services） |
| `resolve_project(name)` | 模糊比對 name / alias / cwd basename，回傳對應 cwd。用在「派工到 kokoro」這類語句 |
| `register_project(name, cwd, aliases?, description?)` | 把新專案寫進 `config/master-agent/projects.yaml`。同名 upsert、其他條目原樣保留、atomic write |

### 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `MASTER_AGENT_PROJECTS_FILE` | `config/master-agent/projects.yaml` | 導演記憶檔位置（讀+寫） |
| `DEV_REGISTRY_SERVICES_FILE` | `~/.config/dev-registry/services.yaml` | 選填的 dev-registry 位置（唯讀）|

## 重要更新：導演（總控 Agent 角色設定）（2026-05-15）

總控 Agent 預設換上「**導演**」這個角色——以舞台導演的視角接收需求、調度 codex 與 claude 工人。使用者可以隨時改名、換口吻、套用內建預設，或關掉角色回到純工具模式。

工具呼叫的決策與參數**不會**被角色影響；角色只改變導演對你說的話。

### 內建角色（一鍵套用，可再編輯）

| 預設 | 顯示名稱 | 風格 |
|---|---|---|
| `director`（出廠預設） | 導演 | 沉穩、鏡頭感，用「下一個鏡頭」「場記開始──」這類舞台語言 |
| `calm-assistant` | 助理 | 冷靜、極簡、條列，先講結論 |
| `fellow-coder` | 阿凱 | 熱血工程師夥伴，輕鬆口語，但派工指令精準 |
| `tool-only` | 導演 | 純工具模式：保留「導演」這個名字但完全不注入角色語氣 |

### 可自訂欄位

在前端 `/master-agent` 頁面標題的「🎭 導演」chip 點下去就會開啟角色設定面板：

| 欄位 | 用途 |
|---|---|
| `display_name` | 角色名稱，會顯示在前端標題、聊天輸入框、TG bot 自稱 |
| `summary` | 一句話介紹角色 |
| `personality` | 性格特質（逗號 / 頓號分隔） |
| `speaking_style` | 自由文本：語氣詞、人稱代名詞、節奏，越具體越好 |
| `catchphrase` | 開場語 / 口頭禪（選填，自然帶入即可） |
| `boundaries` | 角色不可碰的界線（例如「不假裝親自寫程式碼」） |
| `enabled` | 關閉 = 角色完全不注入 LLM，回到純工具口吻；名字仍然保留 |

設定寫入 `config/master-agent/persona.json`，伺服器重啟保留。改名後**前端與 TG bot 同步生效**，毋需重啟。

### API

- `GET /api/master-agent/persona` — 取得目前角色與內建預設清單
- `PUT /api/master-agent/persona` — 全量覆寫角色設定
- `POST /api/master-agent/persona/reset` — 重置為預設「導演」
- `POST /api/master-agent/persona/apply-preset` — body `{preset_id}`，套用內建預設

## 重要更新：Telegram 整合（2026-05-15）

把導演接到 Telegram 私訊，**離開電腦也能派工**。Bot 以長輪詢（long polling）取得訊息，後端只做 outbound 連線到 `api.telegram.org`——不必開放公網 webhook、不必把伺服器暴露到外網。

### 啟用步驟

1. 用 [@BotFather](https://t.me/BotFather) 申請 bot 並拿到 token
2. 在後端 `.env` 設定：

   ```env
   TELEGRAM_BOT_TOKEN=<your-token>
   # 選填，前端會用來顯示直連 https://t.me/<username>
   TELEGRAM_BOT_USERNAME=<your-bot-username>
   # 強烈建議：白名單，避免陌生人嘗試綁定
   TELEGRAM_ALLOWED_USERS=<your-numeric-tg-user-id>
   ```

3. 重啟伺服器，啟動 log 看到 `telegram bot polling started` 即可

不知道自己的 TG `user_id`？私訊你的 bot 輸入 `/whoami`，它會回給你（這個指令不過白名單，避免把自己鎖在外面）。

### 綁定流程

1. 開啟前端 `/master-agent`，點 topbar 的「綁定 Telegram」
2. 取得 6 位數綁定碼（一次性，10 分鐘 TTL）
3. 私訊 bot 輸入 `/bind 123456`
4. 看到「✅ 已綁定！」之後，之後 TG 純文字訊息會直接派給導演

### Bot 指令

| 指令 | 說明 |
|---|---|
| `/start` 或 `/help` | 顯示說明（會用你設定的角色名稱） |
| `/bind <code>` | 用網頁端產生的綁定碼綁定到一個對話 |
| `/unbind` | 解除綁定 |
| `/new` | 開啟一段新對話（保留綁定） |
| `/abort` | 中止當前進行中的任務 |
| `/status` | 顯示綁定狀態與目前對話 ID |
| `/whoami` | 顯示自己的 TG `user_id` / `chat_id` |
| 純文字訊息 | 直接派工給導演；同一個 chat 一次只能跑一個任務，需 `/abort` 才能插隊 |

### 訊息呈現

導演在內部會跑多步工具呼叫，呈現到 TG 上時會折疊成易讀格式：

- 工具呼叫開始 → 「🔧 啟動 Codex 子任務…」短訊
- 工具呼叫完成 → 同一則訊息 edit 為「✅ 啟動 Codex 子任務」或「❌ … — error」，附 300 字以內輸出 preview
- 最終回覆 → 完整文字（超過 3900 字會在換行處切塊分多則送）
- 思考片段 → 不送（避免被 TG rate limit），改為每回合送一次 typing indicator

### 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | BotFather 給的 token。**留空 = 整個整合 disable，啟動時跳過** |
| `TELEGRAM_ALLOWED_USERS` | — | 逗號分隔 TG numeric user id 白名單。留空 = 不限制（仍需綁定碼） |
| `TELEGRAM_BOT_USERNAME` | — | 選填，bot 帳號（不含 `@`）；前端用來顯示 `https://t.me/<username>` 直連 |
| `TELEGRAM_BINDINGS_FILE` | `config/master-agent/telegram_bindings.json` | 綁定持久化檔位置 |
| `TELEGRAM_BINDING_CODE_TTL_SECONDS` | `600` | 綁定碼有效秒數（最少 30） |

### API 端點

- `GET /api/master-agent/telegram/status` — 回傳 `{enabled, running, bot_username, binding_count, binding_code_ttl_seconds}`
- `POST /api/master-agent/telegram/binding-code` — 產生一次性綁定碼，回 `{code, expires_at, ttl_seconds, bot_username}`

### 使用須知

- **網頁與 TG 是兩條獨立的對話**：網頁的 `conversation_id` 存在 localStorage，TG 的存在 `telegram_bindings.json`。同一個導演會分別在兩條對話跟你互動，但訊息歷史不互通
- **重啟伺服器不必重新綁定**：綁定持久化；只有「已產生但還沒用掉」的 6 位綁定碼會失效（這是刻意設計——短期 secret 不該倖存重啟）
- **角色設定是 process-wide**：在前端改名後，下一則 TG 訊息就會用新名字
- **只支援私訊**，所有指令在群組聊天中都不會生效

### 安全

- Bot 走 outbound polling，**不需要對外開 port**，比 webhook 模式安全
- 綁定碼為 6 位數字 + TTL + 一次性消耗，不持久化
- 綁定記錄含 TG `chat_id` / `user_id` / `username`，是 PII；`config/master-agent/` 整個目錄已在 `.gitignore` 內，但備份時仍請當作 secret 處理
- 強烈建議設定 `TELEGRAM_ALLOWED_USERS` 白名單

## 補充（2026-03-24）

- 已新增 session / 對話層級的「角色個性」功能，可在建立 session 或續聊時切換 persona
- 2D / 3D 舞台左上角齒輪已加入角色個性 CRUD 編輯
- 新建 session 的 `cwd` 已改成可透過後端目錄瀏覽器挑選，遠端模式下會列出 server 主機的目錄而不是前端裝置本機目錄
- 建立 `Codex` session 時，預設 `permission mode` 使用 `default`，後端會交給 Codex CLI 的平台自動模式，避免誤用危險 bypass flag

## 核心能力

### 1. 多品牌 agent 控制

後端已提供統一的 `session-bridge` 能力，前端可直接以同一套 UI 管理不同品牌 agent。

目前內建品牌：

- `Codex`
- `Claude`
- `OpenCode`

支援能力包含：

- 建立 brand-aware 新 session
- 依品牌切換可選模型清單
- 依品牌與平台提供預設 `permission mode`
- 統一聊天串流與工具審批流程
- 讀取不同品牌的本地 session 目錄並同步狀態
- 對外提供品牌 catalog API：`/api/session-bridge/agent/brands`

預設 session 目錄：

- `CODEX_SESSION_DIR`，預設 `~/.codex/sessions`
- `CLAUDE_SESSION_DIR`，預設 `~/.claude/projects`
- `OPENCODE_DATA_DIR`，預設 `~/.local/share/opencode`；後端會讀取 `opencode.db` 恢復 history / conversation

### 2. 2D Session Stage

`/` 與 `/session-stage` 為 2D Live2D 入口，適合用來：

- 觀察目前活躍 session 狀態
- 直接建立與切換 session
- 開啟對話視窗與送出 prompt
- 處理 approval / sandbox 相關互動
- 以 Live2D 角色方式展示 session

### 3. Desktop Widget（mac / Windows，dev）

`/desktop-widget` 是 Electron 桌面監看小工具使用的精簡前端入口（目前支援 macOS 與 Windows）。它重用現有 Live2D 與 `session-bridge` API / WebSocket，只顯示最新活躍 session 的狀態，不載入完整控制台、側欄、聊天視窗或 terminal。

v1 限制：

- 只監看 session 狀態，不建立 session、不聊天、不處理 approval
- 不自動啟動 Python 後端，需先用既有方式啟動 backend
- 目前只提供 dev 啟動流程，不包含 dmg、簽章、公證、自動更新
- 後端連不上時會顯示 `Bridge Disconnected` 並保留最後可顯示的 session

### 4. 3D Session Stage

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
- `/desktop-widget`：Electron 桌面監看小工具前端入口

### Backend

路徑：`agents-stage-live2d-vrm3d-server`

技術棧：

- `FastAPI`
- `uvicorn`
- 本地 agent CLI bridge

主要能力：

- Session snapshot / history / conversation 聚合
- WebSocket session event 推送
- Codex / Claude / OpenCode brand router
- 統一 agent chat / approval / new session API
- Live2D 預覽與動作語意映射 warmup

## 安裝需求

建議先準備以下環境：

- Node.js / npm
- Python `>= 3.13`
- `uv`
- 已可在本機執行的 `codex` CLI
- 如需 Claude 品牌，另需可在本機執行的 `claude` CLI
- 如需 OpenCode 品牌，另需可在本機執行的 `opencode` CLI

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

### 3. 確認統一開發設定

專案根目錄的 [`.env`](/Users/dannylin/Desktop/agents-stage-live2d-vrm3d/.env:1) 是前後端共用的單一來源，預設如下：

```bash
VITE_BACKEND_HOST=127.0.0.1
VITE_BACKEND_PORT=8000
VITE_FRONTEND_HOST=0.0.0.0
VITE_FRONTEND_PORT=5173
# 反向代理 / 內網 DNS 使用：逗號分隔的 host 白名單，或填 "all" 關閉檢查
# VITE_ALLOWED_HOSTS=dev.example.com,stage.example.com
```

之後若要調整前端或後端 port，優先修改這份檔案，不要分別去改 `Makefile`、Vite 或前端 API fallback。

若透過 Caddy / Nginx 等反向代理搭配內網 DNS 存取 dev server，Vite 會因為 host 檢查（DNS rebinding 防護）回應 `Blocked request`，請在 `.env` 設定 `VITE_ALLOWED_HOSTS` 把網域加進白名單。

## 啟動方式

### 一鍵啟動

在專案根目錄執行：

```bash
make dev
```

在 macOS 與 Windows 上，`make dev` 會在前後端就緒後自動啟動桌面 widget（Electron，`/desktop-widget`）。

預設服務位址：

- Frontend: 依 `.env` 的 `VITE_FRONTEND_PORT`，預設為 `http://127.0.0.1:5173`
- Backend: 依 `.env` 的 `VITE_BACKEND_PORT`，預設為 `http://127.0.0.1:8000`

### 手動啟動

```bash
# terminal 1
cd agents-stage-live2d-vrm3d-server
.venv/bin/python main.py

# terminal 2
cd agents-stage-live2d-vrm3d-fe
npm run dev
```

`main.py`、Vite dev server、前端預設 API / WebSocket URL 都會從根目錄 `.env` 自動讀取設定。

### Desktop Widget dev 啟動（手動）

先啟動 backend 與 frontend dev server：

```bash
# terminal 1
cd agents-stage-live2d-vrm3d-server
.venv/bin/python main.py

# terminal 2
cd agents-stage-live2d-vrm3d-fe
npm run dev
```

再啟動 Electron 桌面小工具：

```bash
# terminal 3
cd agents-stage-live2d-vrm3d-fe
npm run electron:dev
```

Electron 預設載入 `http://127.0.0.1:5173/desktop-widget`，視窗為透明、無框、置頂，主要區域可拖曳；hover 視窗右上角會顯示關閉與重新載入控制。若要改載入位置，可設定 `DESKTOP_WIDGET_URL`。

平台差異：

- macOS：透明、無框、置頂、顯示於所有 workspace，視窗可縮放
- Windows：透明、無框、置頂，視窗尺寸固定不可縮放（Electron 在 Windows 上 `transparent: true` 與 `resizable: true` 並用會出現殘影/黑邊，因此鎖定固定尺寸換取穩定度）

## 使用方式

### 1. 開啟 Session Stage

進入：

- 以 `.env` 預設值為例：`http://127.0.0.1:5173/`
- 或 `http://127.0.0.1:5173/session-stage`

這裡是預設的 2D 控制台，適合進行日常 session 管理與對話操作。

### 2. 建立新 session

在介面中建立 session 時，可直接設定：

- `agent brand`，例如 `codex` 或 `claude`
- `model`
- `cwd`
- `permission mode`
- `plan mode`
- `角色個性`

`cwd` 欄位除了手動輸入，也可直接打開後端目錄瀏覽器，列出 server 所在主機的目錄結構供前端選取；這樣在遠端模式下，其他裝置仍可正確挑選遠端工作目錄。

`permission mode` 的預設值會依品牌 catalog 決定：

- Codex：預設 `default`（CLI 平台自動模式）
- 其他情況：預設 `default`

目前設計避免把新 session 預設成 `full`，因為 `full` 會掛危險 bypass flag；若使用者確實需要完全存取，必須在介面明確選擇或在導演訊息中使用 `#full`。

前端會透過統一 API 建立 brand-aware session，不需要為不同品牌切換不同頁面。

### 3. 在 3D 舞台檢視 agent

進入：

以 `.env` 預設值為例：`http://127.0.0.1:5173/session-stage-3d`

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
- `GET /api/session-bridge/fs/directories`
- `WS /api/session-bridge/ws`
- `GET /api/tui/sessions`、`POST /api/tui/sessions`、`DELETE /api/tui/sessions/{id}`、`WS /api/tui/ws` — TUI Bridge（見重要更新章節）

如果你要把這個專案接到其他前端或自動化流程，優先從這組 API 開始整合。

其中：

- `GET /api/session-bridge/agent/brands` 會回傳品牌顯示名稱、badge icon、可選模型，以及 `default_permission_mode`
- `GET /api/session-bridge/fs/directories` 會列出後端主機的目錄結構，供新建 session 時挑選 `cwd`

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

以下依日期由新到舊排列。

### 2026-05-09：對話強制中止按鈕（kill running CLI）

過去訊息送出後，agent CLI（Codex / Claude Code）一定會跑到結束才能介入；遇到 agent 走偏（無限改檔、進入死巷）時只能等。新增「⏹ 強制中止」按鈕：

- **後端**：`CodexSessionChatService` 與 `ClaudeSessionChatService` 新增 per-session 子程序註冊表（`_active_processes`），`stream_prompt` 啟動時 register、結束時 unregister；新增 `abort_session(session_id)`。
- **kill 整個 process tree**（重要）：Claude / Codex 在執行任務時會 spawn sub-agent、MCP server、tool subprocess（npm / git / …），若只 `process.kill()` 父行程，子孫程序會變孤兒繼續跑（這就是 v1 按鈕「按了沒反應」的根因）。改為：
  - spawn 時加入 `_isolated_subprocess_kwargs()` — POSIX 用 `start_new_session=True`，Windows 用 `creationflags=CREATE_NEW_PROCESS_GROUP`，把整個任務隔離到新 session / process group。
  - abort / timeout / approval-deny 時改用 `_kill_process_tree(process)` — POSIX 走 `os.killpg(SIGKILL)`，Windows 走 `taskkill /F /T`，把整棵樹殺乾淨。
- **新端點**：`POST /api/session-bridge/agent/chat/abort`，body `{ session_id, agent_brand? }`。未指定 brand 時會輪詢已註冊的 provider，誰持有該 session 的 process 就由誰中止。
- **前端**：`chat.vue` 對每次 `fetchEventData` 建立 `AbortController`，「正在思考...」旁邊顯示紅色「⏹ 強制中止」按鈕；按下後先 await abort API 等後端把 process tree 殺乾淨，再 abort 本地 SSE，並在訊息列加入「⏹ 已中止當前任務。」。
- **新 API 函式**：`abortAgentSession(serverUrl, { session_id, agent_brand })` in `src/utils/api/sessionBridge.ts`。
- 後端補 17 條 unittest（kill_process_tree 跨平台行為、abort_session、API 路由、process group 隔離），前端補 3 條 vitest。

### 2026-05-09：前端操作狀態本地快取

針對手機/桌面端切到背景或重整頁面後「整個刷新」的問題，前端新增 UI 操作狀態本地快取：

- **聊天輸入草稿**：在對話視窗輸入到一半時，依 conversation key 自動寫入 `localStorage`（debounced 220ms），切到背景或重整回來都能還原；訊息成功送出後自動清除該 key 的草稿。草稿帶 7 天 TTL，避免無限膨脹。
- **目前打開的 chat session**：`selectedChatSessionId` 與 `chatModalVisible` 寫入 `live2d-viewer-chat-ui-state`，重新整理頁面後若該 session 仍存在會自動重新打開。
- **Agent 設定**：每個 session 的 model / reasoning / permission_mode / persona / plan_mode 等寫入 `live2d-viewer-session-agent-options`，重整後維持上次的設定值。
- 統一封裝在 `src/utils/uiStateCache.ts`，附 16 條 vitest 單元測試覆蓋讀寫、TTL、debounce 與例外情境。

### 2026-04-27：Desktop Widget Windows 透明背景

- Windows 上的 Electron desktop widget 改為與 macOS 一致的透明、無框、置頂呈現
- 為避開 Electron 在 Windows 上「透明 + 可縮放」並用會出現殘影/黑邊的限制，Windows 視窗鎖定為固定尺寸（`resizable: false`）
- macOS 行為不變

### 2026-03-24：角色個性功能與 VRM 表情調查

#### 新增功能

- 新建 session 時可指定角色個性，預設為無
- 續聊中的 agent session 可切換角色個性
- 2D / 3D 舞台左上角齒輪新增角色個性編輯器
- 後端 session bridge 會以結構化 JSON prompt 注入 persona 與 user input
- conversation / history 會自動過濾注入 envelope，只保留真正的使用者輸入

#### 文檔補充

- 新增 `docs/vrm-expression-investigation-2026-03-24.md`
- 記錄目前專案內 4 個 VRM 模型皆有表情資料，但 3D stage 尚未接表情控制邏輯

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
   .venv/bin/python main.py --mode remote --host 127.0.0.1 --port 8000 --static-path ../agents-stage-live2d-vrm3d-fe/dist
   ```

#### 注意事項

- Remote 模式下前端需先 build（`npm run build`），由 FastAPI 提供靜態檔案，確保 same-origin 避免 CORS 問題
- 本功能設計為單人單機使用，不包含多使用者隔離機制
- Local 模式完全不受影響，無需任何設定即可照常使用

## 版權與素材聲明

本專案雖然包含部分 2D / 3D 模型與相關範例資源，但這些資源均取自公開可取得的資源網站，僅供本專案測試、研究與功能驗證用途。

若你要在實際產品、商業場景、公開散布、二次創作或其他正式用途使用這些模型與素材，應自行確認並遵守各原始資源作者、發布頁面或來源平台所附帶的版權聲明、授權條款與使用限制。本專案的程式碼授權不等同於這些第三方模型素材的授權範圍。
