import { getDefaultServerUrl } from '../serverUrl'

const API_PREFIX = '/api/terminal'
const WS_PATH = `${API_PREFIX}/ws`

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '')
}

/**
 * Resolve the WebSocket URL for the web terminal endpoint.
 * Supports both local dev (explicit backend) and remote (same-origin wss).
 */
export function resolveTerminalWsUrl(cols: number, rows: number, serverUrl?: string): string {
  if (typeof window === 'undefined') {
    const base = trimTrailingSlash(serverUrl || getDefaultServerUrl())
    return `${base.replace(/^http/, 'ws')}${WS_PATH}?cols=${cols}&rows=${rows}`
  }

  const { hostname, protocol, host } = window.location
  if (hostname === '127.0.0.1' || hostname === 'localhost') {
    const backendHost = import.meta.env.VITE_BACKEND_HOST || '127.0.0.1'
    const backendPort = import.meta.env.VITE_BACKEND_PORT || '8000'
    return `ws://${backendHost}:${backendPort}${WS_PATH}?cols=${cols}&rows=${rows}`
  }

  const wsProtocol = protocol === 'https:' ? 'wss:' : 'ws:'
  return `${wsProtocol}//${host}${WS_PATH}?cols=${cols}&rows=${rows}`
}

export interface TerminalConfig {
  enabled: boolean
  max_sessions: number
  active_sessions: number
  is_windows: boolean
}

export async function fetchTerminalConfig(): Promise<TerminalConfig> {
  const base = getDefaultServerUrl()
  const url = `${base}${API_PREFIX}/config`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Failed to fetch terminal config: ${res.status}`)
  return res.json()
}
