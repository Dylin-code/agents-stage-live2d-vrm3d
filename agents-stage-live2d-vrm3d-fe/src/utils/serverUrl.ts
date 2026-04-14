const DEFAULT_LOCAL_BACKEND_HOST = import.meta.env.VITE_BACKEND_HOST || '127.0.0.1'
const DEFAULT_LOCAL_BACKEND_PORT = import.meta.env.VITE_BACKEND_PORT || '8000'
const DEFAULT_LOCAL_SERVER_URL = `http://${DEFAULT_LOCAL_BACKEND_HOST}:${DEFAULT_LOCAL_BACKEND_PORT}`
const DEFAULT_LOCAL_BRIDGE_WS_URL = `ws://${DEFAULT_LOCAL_BACKEND_HOST}:${DEFAULT_LOCAL_BACKEND_PORT}/api/session-bridge/ws`

/**
 * Auto-detect the default server URL.
 * - Local dev (localhost/127.0.0.1): explicit backend origin from root .env
 * - Remote mode (Cloudflare Tunnel etc.): empty string = relative paths (same origin)
 */
export function getDefaultServerUrl(): string {
  if (typeof window === 'undefined') return DEFAULT_LOCAL_SERVER_URL
  const { hostname } = window.location
  if (hostname === '127.0.0.1' || hostname === 'localhost') {
    return DEFAULT_LOCAL_SERVER_URL
  }
  return ''
}

/**
 * Auto-detect the default WebSocket URL for session bridge.
 * - Local dev: backend websocket URL from root .env
 * - Remote mode: derive from current page origin (wss://...)
 */
export function getDefaultBridgeWsUrl(): string {
  if (typeof window === 'undefined') return DEFAULT_LOCAL_BRIDGE_WS_URL
  const { hostname, protocol, host } = window.location
  if (hostname === '127.0.0.1' || hostname === 'localhost') {
    return DEFAULT_LOCAL_BRIDGE_WS_URL
  }
  const wsProtocol = protocol === 'https:' ? 'wss:' : 'ws:'
  return `${wsProtocol}//${host}/api/session-bridge/ws`
}
