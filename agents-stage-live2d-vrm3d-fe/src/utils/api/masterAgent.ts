import { getDefaultServerUrl } from '../serverUrl'

const DEFAULT_SERVER_URL = getDefaultServerUrl()
const BASE_PATH = '/api/master-agent'
const WS_PATH = '/api/master-agent/ws'

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '')
}

function resolveBase(serverUrl?: string): string {
  return trimTrailingSlash(serverUrl || DEFAULT_SERVER_URL)
}

export function resolveMasterAgentWsUrl(serverUrl?: string): string {
  // Mirror the session-bridge WS resolver: derive ws:// from http://,
  // wss:// from https://, otherwise fall back to the current page origin.
  const base = resolveBase(serverUrl)
  if (base) {
    try {
      const parsed = new URL(base)
      parsed.protocol = parsed.protocol === 'https:' ? 'wss:' : 'ws:'
      parsed.pathname = WS_PATH
      parsed.search = ''
      parsed.hash = ''
      return parsed.toString()
    } catch {
      // base wasn't a valid URL; fall through to relative.
    }
  }
  if (typeof window !== 'undefined') {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${window.location.host}${WS_PATH}`
  }
  return `ws://localhost${WS_PATH}`
}

export interface MasterAgentWsEnvelope {
  event: 'subtask' | 'conversation' | string
  type: string
  conversation_id?: string
  subtask?: SubTaskRecord
  content?: Record<string, unknown>
}

export interface MasterEvent {
  type: string
  content?: unknown
}

export interface SubTaskRecord {
  id: string
  conversation_id: string
  agent_brand: string
  session_id: string
  prompt: string
  cwd: string
  status: 'pending' | 'running' | 'awaiting_approval' | 'done' | 'failed' | 'aborted'
  created_at: number
  updated_at: number
  final_text: string
  last_event_type: string
  error: string
}

export interface MasterAgentSnapshot {
  conversation: {
    id: string
    messages: Array<{ role: string; content: unknown }>
    created_at: number
    updated_at: number
  }
  subtasks: SubTaskRecord[]
}

export interface MasterLlmInfo {
  provider: string
  model: string
  base_url: string
}

export async function createMasterAgentConversation(
  serverUrl?: string,
  defaultCwd?: string,
): Promise<{ conversation_id: string }> {
  const response = await fetch(`${resolveBase(serverUrl)}${BASE_PATH}/conversation/new`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ default_cwd: defaultCwd ?? null }),
  })
  if (!response.ok) {
    throw new Error(`failed to create master conversation: ${response.status}`)
  }
  return (await response.json()) as { conversation_id: string }
}

export interface SubmitMasterChatRequest {
  conversation_id: string
  message: string
  default_cwd?: string
  /** Set when the user typed ``#full`` somewhere in the message — opens
   *  the gate so the LLM's ``permission_mode=full`` requests are
   *  honored for this single chat turn. Stripped from ``message``. */
  permit_full_access?: boolean
}

/**
 * Submit a message and return the raw Response — caller is responsible for
 * draining the SSE stream via {@link consumeMasterEventStream}. Splitting
 * fetch from the iterator keeps cancellation easy (caller controls the
 * AbortController) and lets the runtime layer unit-test the parser with
 * a synthetic ReadableStream.
 */
export async function submitMasterAgentChat(
  serverUrl: string | undefined,
  payload: SubmitMasterChatRequest,
  init?: { signal?: AbortSignal },
): Promise<Response> {
  const response = await fetch(`${resolveBase(serverUrl)}${BASE_PATH}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(payload),
    signal: init?.signal,
  })
  if (!response.ok || !response.body) {
    throw new Error(`failed to start master chat stream: ${response.status}`)
  }
  return response
}

/**
 * Parse an SSE stream of ``data: {...}\n\n`` lines into MasterEvent objects.
 *
 * Exported separately so unit tests can drive it with a fake ReadableStream.
 */
export async function* consumeMasterEventStream(
  response: Response,
): AsyncGenerator<MasterEvent, void, void> {
  if (!response.body) return
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const chunk = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const event = parseSseChunk(chunk)
      if (event) yield event
      boundary = buffer.indexOf('\n\n')
    }
  }
  buffer += decoder.decode()
  if (buffer.trim()) {
    const event = parseSseChunk(buffer)
    if (event) yield event
  }
}

function parseSseChunk(chunk: string): MasterEvent | null {
  const lines = chunk.split(/\r?\n/)
  const dataLines = lines
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())
  if (dataLines.length === 0) return null
  const dataText = dataLines.join('\n')
  try {
    const parsed = JSON.parse(dataText)
    if (parsed && typeof parsed === 'object' && typeof parsed.type === 'string') {
      return parsed as MasterEvent
    }
  } catch {
    return null
  }
  return null
}

export async function fetchMasterAgentSnapshot(
  conversationId: string,
  serverUrl?: string,
): Promise<MasterAgentSnapshot> {
  const params = new URLSearchParams({ conversation_id: conversationId })
  const response = await fetch(
    `${resolveBase(serverUrl)}${BASE_PATH}/snapshot?${params.toString()}`,
  )
  if (!response.ok) {
    throw new Error(`failed to fetch master snapshot: ${response.status}`)
  }
  return (await response.json()) as MasterAgentSnapshot
}

export async function fetchMasterAgentSubtasks(
  conversationId: string,
  serverUrl?: string,
): Promise<SubTaskRecord[]> {
  const params = new URLSearchParams({ conversation_id: conversationId })
  const response = await fetch(
    `${resolveBase(serverUrl)}${BASE_PATH}/subtasks?${params.toString()}`,
  )
  if (!response.ok) {
    throw new Error(`failed to fetch master subtasks: ${response.status}`)
  }
  const payload = (await response.json()) as { subtasks: SubTaskRecord[] }
  return payload.subtasks || []
}

export async function abortMasterAgent(
  conversationId: string,
  serverUrl?: string,
): Promise<{ aborted: boolean }> {
  const response = await fetch(`${resolveBase(serverUrl)}${BASE_PATH}/abort`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversation_id: conversationId }),
  })
  if (!response.ok) {
    throw new Error(`failed to abort master agent: ${response.status}`)
  }
  return (await response.json()) as { aborted: boolean }
}

export async function fetchMasterAgentLlmInfo(serverUrl?: string): Promise<MasterLlmInfo> {
  const response = await fetch(`${resolveBase(serverUrl)}${BASE_PATH}/llm/info`)
  if (!response.ok) {
    throw new Error(`failed to fetch master agent llm info: ${response.status}`)
  }
  return (await response.json()) as MasterLlmInfo
}

// ---------------------------------------------------------------------------
// Telegram binding
// ---------------------------------------------------------------------------

export interface TelegramStatus {
  enabled: boolean
  running: boolean
  bot_username: string
  binding_count: number
  binding_code_ttl_seconds: number
}

export interface TelegramBindingCode {
  code: string
  expires_at: number
  ttl_seconds: number
  bot_username: string
}

export async function fetchTelegramStatus(serverUrl?: string): Promise<TelegramStatus> {
  const response = await fetch(`${resolveBase(serverUrl)}${BASE_PATH}/telegram/status`)
  if (!response.ok) {
    throw new Error(`failed to fetch telegram status: ${response.status}`)
  }
  return (await response.json()) as TelegramStatus
}

export async function issueTelegramBindingCode(serverUrl?: string): Promise<TelegramBindingCode> {
  const response = await fetch(
    `${resolveBase(serverUrl)}${BASE_PATH}/telegram/binding-code`,
    { method: 'POST' },
  )
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new Error(text || `failed to issue telegram binding code: ${response.status}`)
  }
  return (await response.json()) as TelegramBindingCode
}
