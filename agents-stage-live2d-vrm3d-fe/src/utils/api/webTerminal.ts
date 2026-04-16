import { getDefaultServerUrl } from '../serverUrl'

const WS_PATH = '/api/terminal/ws'

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
