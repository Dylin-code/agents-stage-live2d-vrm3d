/**
 * TUI Bridge API helpers.
 *
 * Mirrors the shape of ``webTerminal.ts`` so the floating window component
 * can stay close in structure to ``WebTerminal.vue``. Talks to the backend
 * router registered in ``live2d_server/tui_bridge_api.py``.
 */
import { getDefaultServerUrl } from '../serverUrl'

const API_PREFIX = '/api/tui'
const WS_PATH = `${API_PREFIX}/ws`

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '')
}

export interface TuiSession {
  session_id: string
  label: string
  cwd: string
  command: string
  created_at: number
  attached_clients: number
  windows: number
  last_activity_at: number
}

export interface TuiBridgeConfig {
  enabled: boolean
  has_tmux: boolean
  max_sessions: number
  active_sessions: number
}

export interface TuiCreatePayload {
  label?: string
  cwd?: string
  command?: string
}

export async function fetchTuiBridgeConfig(serverUrl?: string): Promise<TuiBridgeConfig> {
  const base = trimTrailingSlash(serverUrl || getDefaultServerUrl())
  const res = await fetch(`${base}${API_PREFIX}/config`)
  if (!res.ok) throw new Error(`Failed to fetch tui-bridge config: ${res.status}`)
  return res.json()
}

export async function listTuiSessions(serverUrl?: string): Promise<TuiSession[]> {
  const base = trimTrailingSlash(serverUrl || getDefaultServerUrl())
  const res = await fetch(`${base}${API_PREFIX}/sessions`)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Failed to list tui sessions: ${res.status} ${text}`)
  }
  const data = await res.json()
  return Array.isArray(data?.sessions) ? (data.sessions as TuiSession[]) : []
}

export async function createTuiSession(payload: TuiCreatePayload, serverUrl?: string): Promise<TuiSession> {
  const base = trimTrailingSlash(serverUrl || getDefaultServerUrl())
  const res = await fetch(`${base}${API_PREFIX}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      label: payload.label || '',
      cwd: payload.cwd || '',
      command: payload.command || '',
    }),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Failed to create tui session: ${res.status} ${text}`)
  }
  return res.json()
}

export async function killTuiSession(sessionId: string, serverUrl?: string): Promise<boolean> {
  const base = trimTrailingSlash(serverUrl || getDefaultServerUrl())
  const res = await fetch(`${base}${API_PREFIX}/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Failed to kill tui session: ${res.status} ${text}`)
  }
  const data = await res.json()
  return Boolean(data?.killed)
}

/**
 * Resolve the WebSocket URL for attaching to a TUI session.
 * Mirrors ``resolveTerminalWsUrl`` so the protocol logic stays uniform.
 */
export function resolveTuiBridgeWsUrl(
  sessionId: string,
  cols: number,
  rows: number,
  serverUrl?: string,
): string {
  const query = `?session_id=${encodeURIComponent(sessionId)}&cols=${cols}&rows=${rows}`

  if (typeof window === 'undefined') {
    const base = trimTrailingSlash(serverUrl || getDefaultServerUrl())
    return `${base.replace(/^http/, 'ws')}${WS_PATH}${query}`
  }

  const { hostname, protocol, host } = window.location
  if (hostname === '127.0.0.1' || hostname === 'localhost') {
    const backendHost = import.meta.env.VITE_BACKEND_HOST || '127.0.0.1'
    const backendPort = import.meta.env.VITE_BACKEND_PORT || '8000'
    return `ws://${backendHost}:${backendPort}${WS_PATH}${query}`
  }

  const wsProtocol = protocol === 'https:' ? 'wss:' : 'ws:'
  return `${wsProtocol}//${host}${WS_PATH}${query}`
}
