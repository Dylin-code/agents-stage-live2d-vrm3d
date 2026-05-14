/**
 * UI 操作狀態本地快取。
 *
 * 主要用途：在手機/桌面端離開應用（切到背景、刷新頁面、被瀏覽器丟掉）後，
 * 仍能還原當前操作上下文（正在輸入到一半的訊息、正在打開的 chat session、
 * 當前 session 的 agent 設定），避免回來就被「整個刷新」。
 *
 * 設計重點：
 * - 與既有 conversationItems 快取分離，避免大量寫入污染既有 key。
 * - draft 以 conversation key 分桶儲存，輸入長文不會卡 UI（呼叫端自行 debounce）。
 * - draft 帶 updatedAt 時間戳，提供 prune 過期 API 避免無限膨脹。
 * - 全部以 try/catch 包覆 storage 操作，避免 quota / 私密瀏覽模式造成 UI 崩潰。
 */

export const CHAT_DRAFT_STORAGE_KEY = 'live2d-viewer-chat-drafts'
export const CHAT_UI_STATE_STORAGE_KEY = 'live2d-viewer-chat-ui-state'
export const SESSION_AGENT_OPTIONS_STORAGE_KEY = 'live2d-viewer-session-agent-options'

export const CHAT_DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000

export interface ChatDraftEntry {
  value: string
  updatedAt: number
}

export type ChatDraftMap = Record<string, ChatDraftEntry>

export interface ChatUiState {
  selectedChatSessionId?: string
  chatModalVisible?: boolean
}

export interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

function getDefaultStorage(): StorageLike | null {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function readJson<T>(storage: StorageLike | null, key: string): T | null {
  if (!storage) return null
  try {
    const raw = storage.getItem(key)
    if (!raw) return null
    return JSON.parse(raw) as T
  } catch {
    return null
  }
}

function writeJson(storage: StorageLike | null, key: string, value: unknown): void {
  if (!storage) return
  try {
    storage.setItem(key, JSON.stringify(value))
  } catch {
    // quota exceeded / private mode — silently swallow
  }
}

function removeKey(storage: StorageLike | null, key: string): void {
  if (!storage) return
  try {
    storage.removeItem(key)
  } catch {
    // ignore
  }
}

function loadDraftMap(storage: StorageLike | null): ChatDraftMap {
  const data = readJson<ChatDraftMap>(storage, CHAT_DRAFT_STORAGE_KEY)
  if (!data || typeof data !== 'object') return {}
  const result: ChatDraftMap = {}
  for (const [key, entry] of Object.entries(data)) {
    if (!entry || typeof entry !== 'object') continue
    const value = typeof (entry as ChatDraftEntry).value === 'string' ? (entry as ChatDraftEntry).value : ''
    const updatedAt = Number((entry as ChatDraftEntry).updatedAt) || 0
    result[key] = { value, updatedAt }
  }
  return result
}

function persistDraftMap(storage: StorageLike | null, map: ChatDraftMap): void {
  if (Object.keys(map).length === 0) {
    removeKey(storage, CHAT_DRAFT_STORAGE_KEY)
    return
  }
  writeJson(storage, CHAT_DRAFT_STORAGE_KEY, map)
}

export function loadChatDraft(
  conversationKey: string,
  storage: StorageLike | null = getDefaultStorage(),
): string {
  if (!conversationKey) return ''
  const map = loadDraftMap(storage)
  return map[conversationKey]?.value || ''
}

export function saveChatDraft(
  conversationKey: string,
  value: string,
  storage: StorageLike | null = getDefaultStorage(),
  now: number = Date.now(),
): void {
  if (!conversationKey) return
  const map = loadDraftMap(storage)
  if (!value) {
    if (!(conversationKey in map)) return
    delete map[conversationKey]
    persistDraftMap(storage, map)
    return
  }
  map[conversationKey] = { value, updatedAt: now }
  persistDraftMap(storage, map)
}

export function clearChatDraft(
  conversationKey: string,
  storage: StorageLike | null = getDefaultStorage(),
): void {
  if (!conversationKey) return
  const map = loadDraftMap(storage)
  if (!(conversationKey in map)) return
  delete map[conversationKey]
  persistDraftMap(storage, map)
}

export function pruneStaleChatDrafts(
  ttlMs: number = CHAT_DRAFT_TTL_MS,
  storage: StorageLike | null = getDefaultStorage(),
  now: number = Date.now(),
): void {
  const map = loadDraftMap(storage)
  let mutated = false
  for (const [key, entry] of Object.entries(map)) {
    if (now - entry.updatedAt > ttlMs) {
      delete map[key]
      mutated = true
    }
  }
  if (mutated) persistDraftMap(storage, map)
}

export function loadChatUiState(
  storage: StorageLike | null = getDefaultStorage(),
): ChatUiState {
  const data = readJson<ChatUiState>(storage, CHAT_UI_STATE_STORAGE_KEY)
  if (!data || typeof data !== 'object') return {}
  const result: ChatUiState = {}
  if (typeof data.selectedChatSessionId === 'string') {
    result.selectedChatSessionId = data.selectedChatSessionId
  }
  if (typeof data.chatModalVisible === 'boolean') {
    result.chatModalVisible = data.chatModalVisible
  }
  return result
}

export function saveChatUiState(
  state: ChatUiState,
  storage: StorageLike | null = getDefaultStorage(),
): void {
  const isEmpty = !state.selectedChatSessionId && !state.chatModalVisible
  if (isEmpty) {
    removeKey(storage, CHAT_UI_STATE_STORAGE_KEY)
    return
  }
  writeJson(storage, CHAT_UI_STATE_STORAGE_KEY, state)
}

export function loadSessionAgentOptionsMap<T extends Record<string, unknown>>(
  storage: StorageLike | null = getDefaultStorage(),
): Record<string, T> {
  const data = readJson<Record<string, T>>(storage, SESSION_AGENT_OPTIONS_STORAGE_KEY)
  if (!data || typeof data !== 'object' || Array.isArray(data)) return {}
  const result: Record<string, T> = {}
  for (const [key, value] of Object.entries(data)) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      result[key] = value as T
    }
  }
  return result
}

export function saveSessionAgentOptionsMap<T extends Record<string, unknown>>(
  map: Record<string, T>,
  storage: StorageLike | null = getDefaultStorage(),
): void {
  if (!map || Object.keys(map).length === 0) {
    removeKey(storage, SESSION_AGENT_OPTIONS_STORAGE_KEY)
    return
  }
  writeJson(storage, SESSION_AGENT_OPTIONS_STORAGE_KEY, map)
}

/**
 * 簡易 debounce 工具，避免每次按鍵都打 localStorage。
 * 回傳的物件提供 schedule 與 flush，方便在 unmount/送出訊息時主動寫入。
 */
export function createDebouncedWriter<TArgs extends unknown[]>(
  fn: (...args: TArgs) => void,
  delayMs: number,
) {
  let timer: ReturnType<typeof setTimeout> | null = null
  let pendingArgs: TArgs | null = null

  const flush = () => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    if (pendingArgs) {
      const args = pendingArgs
      pendingArgs = null
      fn(...args)
    }
  }

  const schedule = (...args: TArgs) => {
    pendingArgs = args
    if (timer !== null) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = null
      if (pendingArgs) {
        const next = pendingArgs
        pendingArgs = null
        fn(...next)
      }
    }, Math.max(0, delayMs))
  }

  const cancel = () => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    pendingArgs = null
  }

  return { schedule, flush, cancel }
}
