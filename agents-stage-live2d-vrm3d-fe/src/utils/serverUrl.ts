/**
 * Auto-detect the default server URL.
 * - Local dev (localhost/127.0.0.1): explicit http://127.0.0.1:8000
 * - Remote mode (Cloudflare Tunnel etc.): empty string = relative paths (same origin)
 */
export function getDefaultServerUrl(): string {
  if (typeof window === 'undefined') return 'http://127.0.0.1:8000'
  const { hostname } = window.location
  if (hostname === '127.0.0.1' || hostname === 'localhost') {
    return 'http://127.0.0.1:8000'
  }
  return ''
}

/**
 * Auto-detect the default WebSocket URL for session bridge.
 * - Local dev: ws://127.0.0.1:8000/api/session-bridge/ws
 * - Remote mode: derive from current page origin (wss://...)
 */
export function getDefaultBridgeWsUrl(): string {
  if (typeof window === 'undefined') return 'ws://127.0.0.1:8000/api/session-bridge/ws'
  const { hostname, protocol, host } = window.location
  if (hostname === '127.0.0.1' || hostname === 'localhost') {
    return 'ws://127.0.0.1:8000/api/session-bridge/ws'
  }
  const wsProtocol = protocol === 'https:' ? 'wss:' : 'ws:'
  return `${wsProtocol}//${host}/api/session-bridge/ws`
}
