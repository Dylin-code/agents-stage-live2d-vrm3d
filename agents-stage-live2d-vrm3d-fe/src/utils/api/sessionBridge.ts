import type { AgentBrand, SessionConversationResponse, SessionHistoryResponse, SessionSnapshotResponse } from '../../types/sessionState'

const DEFAULT_SERVER_URL = 'http://127.0.0.1:8000'
const SNAPSHOT_PATH = '/api/session-bridge/snapshot'
const HISTORY_PATH = '/api/session-bridge/history'
const CONVERSATION_PATH = '/api/session-bridge/conversation'
const GIT_BRANCHES_PATH = '/api/session-bridge/git/branches'
const GIT_SWITCH_PATH = '/api/session-bridge/git/switch'
const WS_PATH = '/api/session-bridge/ws'
// Unified multi-brand endpoints
const AGENT_CHAT_PATH = '/api/session-bridge/agent/chat'
const AGENT_APPROVAL_PATH = '/api/session-bridge/agent/chat/approval'
const AGENT_NEW_SESSION_PATH = '/api/session-bridge/agent/session/new'
const AGENT_BRANDS_PATH = '/api/session-bridge/agent/brands'

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '')
}

export function resolveSnapshotUrl(serverUrl?: string): string {
  const base = trimTrailingSlash(serverUrl || DEFAULT_SERVER_URL)
  return `${base}${SNAPSHOT_PATH}`
}

export function resolveHistoryUrl(serverUrl?: string, limit = 20): string {
  const base = trimTrailingSlash(serverUrl || DEFAULT_SERVER_URL)
  const safeLimit = Math.max(1, Math.min(200, Number(limit || 20)))
  return `${base}${HISTORY_PATH}?limit=${safeLimit}`
}

export function resolveConversationUrl(serverUrl: string | undefined, sessionId: string, limit = 1000): string {
  const base = trimTrailingSlash(serverUrl || DEFAULT_SERVER_URL)
  const safeLimit = Math.max(1, Math.min(5000, Number(limit || 1000)))
  return `${base}${CONVERSATION_PATH}/${encodeURIComponent(sessionId)}?limit=${safeLimit}`
}

export function resolveBridgeWsUrl(serverUrl?: string, bridgeUrl?: string): string {
  if (bridgeUrl && bridgeUrl.trim()) {
    return bridgeUrl.trim()
  }

  const base = trimTrailingSlash(serverUrl || DEFAULT_SERVER_URL)
  const parsed = new URL(base)
  parsed.protocol = parsed.protocol === 'https:' ? 'wss:' : 'ws:'
  parsed.pathname = WS_PATH
  parsed.search = ''
  parsed.hash = ''
  return parsed.toString()
}

export async function fetchSessionBridgeSnapshot(serverUrl?: string): Promise<SessionSnapshotResponse> {
  const response = await fetch(resolveSnapshotUrl(serverUrl), {
    method: 'GET',
  })
  if (!response.ok) {
    throw new Error(`failed to fetch snapshot: ${response.status}`)
  }
  return (await response.json()) as SessionSnapshotResponse
}

export async function fetchSessionBridgeHistory(serverUrl?: string, limit = 20): Promise<SessionHistoryResponse> {
  const response = await fetch(resolveHistoryUrl(serverUrl, limit), {
    method: 'GET',
  })
  if (!response.ok) {
    throw new Error(`failed to fetch history: ${response.status}`)
  }
  return (await response.json()) as SessionHistoryResponse
}

export async function fetchSessionBridgeConversation(
  serverUrl: string | undefined,
  sessionId: string,
  limit = 1000,
): Promise<SessionConversationResponse> {
  const response = await fetch(resolveConversationUrl(serverUrl, sessionId, limit), {
    method: 'GET',
  })
  if (!response.ok) {
    throw new Error(`failed to fetch conversation: ${response.status}`)
  }
  return (await response.json()) as SessionConversationResponse
}

export interface SessionBridgeApprovalRequest {
  pending_id: string
  decision: 'allow_once' | 'deny_once' | 'allow_prefix'
  prefix_rule?: string[]
  agent_brand?: AgentBrand
}

// Legacy compatibility adapter for old callers.
export async function submitSessionBridgeApproval(
  serverUrl: string | undefined,
  payload: SessionBridgeApprovalRequest,
): Promise<Record<string, unknown>> {
  return submitAgentApproval(serverUrl, payload)
}

export function resolveAgentApprovalUrl(serverUrl?: string): string {
  const base = trimTrailingSlash(serverUrl || DEFAULT_SERVER_URL)
  return `${base}${AGENT_APPROVAL_PATH}`
}

export interface SessionBridgeNewSessionRequest {
  cwd: string
  model?: string
  reasoning_effort?: string
  permission_mode?: string
  plan_mode?: boolean
  agent_brand?: AgentBrand
}

// Legacy compatibility adapter for old callers.
export async function createSessionBridgeSession(
  serverUrl: string | undefined,
  payload: SessionBridgeNewSessionRequest,
): Promise<Record<string, unknown>> {
  return createAgentSession(serverUrl, payload)
}

export async function fetchSessionBridgeBranches(
  serverUrl: string | undefined,
  sessionId?: string,
  cwd?: string,
): Promise<Record<string, unknown>> {
  const base = trimTrailingSlash(serverUrl || DEFAULT_SERVER_URL)
  const params = new URLSearchParams()
  if (sessionId) params.set('session_id', sessionId)
  if (cwd) params.set('cwd', cwd)
  const suffix = params.toString() ? `?${params.toString()}` : ''
  const response = await fetch(`${base}${GIT_BRANCHES_PATH}${suffix}`, {
    method: 'GET',
  })
  if (!response.ok) {
    throw new Error(`failed to fetch branches: ${response.status}`)
  }
  return (await response.json()) as Record<string, unknown>
}

export async function switchSessionBridgeBranch(
  serverUrl: string | undefined,
  payload: {
    session_id?: string
    cwd?: string
    branch: string
  },
): Promise<Record<string, unknown>> {
  const base = trimTrailingSlash(serverUrl || DEFAULT_SERVER_URL)
  const response = await fetch(`${base}${GIT_SWITCH_PATH}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    throw new Error(`failed to switch branch: ${response.status}`)
  }
  return (await response.json()) as Record<string, unknown>
}

// ---------------------------------------------------------------------------
// Unified multi-brand agent endpoints
// ---------------------------------------------------------------------------

export function resolveAgentChatUrl(serverUrl?: string): string {
  const base = trimTrailingSlash(serverUrl || DEFAULT_SERVER_URL)
  return `${base}${AGENT_CHAT_PATH}`
}

export function resolveAgentNewSessionUrl(serverUrl?: string): string {
  const base = trimTrailingSlash(serverUrl || DEFAULT_SERVER_URL)
  return `${base}${AGENT_NEW_SESSION_PATH}`
}

export async function createAgentSession(
  serverUrl: string | undefined,
  payload: SessionBridgeNewSessionRequest,
): Promise<Record<string, unknown>> {
  const base = trimTrailingSlash(serverUrl || DEFAULT_SERVER_URL)
  const response = await fetch(`${base}${AGENT_NEW_SESSION_PATH}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    throw new Error(`failed to create agent session: ${response.status}`)
  }
  return (await response.json()) as Record<string, unknown>
}

export async function submitAgentApproval(
  serverUrl: string | undefined,
  payload: SessionBridgeApprovalRequest,
): Promise<Record<string, unknown>> {
  const response = await fetch(resolveAgentApprovalUrl(serverUrl), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    throw new Error(`failed to submit agent approval: ${response.status}`)
  }
  return (await response.json()) as Record<string, unknown>
}

export interface AgentBrandInfo {
  brand: string
  display_name: string
  badge_icon: string
  models: string[]
}

export async function fetchAgentBrands(
  serverUrl?: string,
): Promise<{ brands: AgentBrandInfo[] }> {
  const base = trimTrailingSlash(serverUrl || DEFAULT_SERVER_URL)
  const response = await fetch(`${base}${AGENT_BRANDS_PATH}`, { method: 'GET' })
  if (!response.ok) {
    throw new Error(`failed to fetch agent brands: ${response.status}`)
  }
  return (await response.json()) as { brands: AgentBrandInfo[] }
}
