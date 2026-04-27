import { computed, getCurrentInstance, onMounted, onUnmounted, ref } from 'vue'
import type { ComputedRef, Ref } from 'vue'
import type {
  AgentBrand,
  SessionHistoryItem,
  SessionHistoryResponse,
  SessionSnapshotItem,
  SessionState,
  SessionStateEvent,
} from '../../types/sessionState'
import {
  fetchSessionBridgeHistory,
  resolveBridgeWsUrl,
} from '../../utils/api/sessionBridge'
import { getDefaultServerUrl } from '../../utils/serverUrl'
import { stateText } from '../../utils/session-stage/stateText'
import { getSessionActivityEpoch, mergeHistorySessions } from '../../utils/sessionStageState'

export type DesktopWidgetConnectionStatus = 'connecting' | 'connected' | 'disconnected'

export interface DesktopWidgetSession {
  session_id: string
  display_name: string
  state: SessionState
  last_seen_at: string
  active: boolean
  agent_brand?: AgentBrand
  has_real_user_input?: boolean
  originator?: string
  cwd?: string
  cwd_basename?: string
  branch?: string
  last_event_type?: string
  context?: SessionSnapshotItem['context']
}

export interface DesktopWidgetMonitor {
  connectionStatus: Ref<DesktopWidgetConnectionStatus>
  sessions: Ref<DesktopWidgetSession[]>
  activeSession: ComputedRef<DesktopWidgetSession | null>
  activeState: ComputedRef<SessionState>
  activeStateText: ComputedRef<string>
  brandName: ComputedRef<string>
  cwdLabel: ComputedRef<string>
  rateLimitText: ComputedRef<string>
  lastEventText: ComputedRef<string>
  refreshHistory: () => Promise<void>
  connect: () => void
  disconnect: () => void
}

interface WebSocketLike {
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: (() => void) | null
  onerror: (() => void) | null
  close: () => void
}

interface WebSocketCtor {
  new (url: string): WebSocketLike
}

export interface DesktopWidgetMonitorOptions {
  serverUrl?: string
  bridgeUrl?: string
  historyLimit?: number
  reconnectBaseDelayMs?: number
  reconnectMaxDelayMs?: number
  autoStart?: boolean
  webSocketCtor?: WebSocketCtor
  fetchHistory?: (serverUrl: string | undefined, limit: number) => Promise<SessionHistoryResponse>
  setTimeoutFn?: Window['setTimeout']
  clearTimeoutFn?: Window['clearTimeout']
}

const DEFAULT_HISTORY_LIMIT = 100
const DEFAULT_RECONNECT_BASE_DELAY_MS = 1000
const DEFAULT_RECONNECT_MAX_DELAY_MS = 10000

function toSession(item: SessionHistoryItem | DesktopWidgetSession): DesktopWidgetSession {
  return {
    session_id: item.session_id,
    display_name: item.display_name,
    state: item.state,
    last_seen_at: item.last_seen_at,
    active: item.active,
    agent_brand: item.agent_brand,
    has_real_user_input: item.has_real_user_input,
    originator: item.originator,
    cwd: item.cwd,
    cwd_basename: item.cwd_basename,
    branch: item.branch,
    last_event_type: item.last_event_type,
    context: item.context,
  }
}

export function isDesktopWidgetWarmupSession(session: Pick<DesktopWidgetSession, 'display_name' | 'has_real_user_input'>): boolean {
  const name = (session.display_name || '').trim().toLowerCase()
  if (!name) return false
  if (name.startsWith('# agents.md instructions for ')) return true
  if (name.startsWith('warning: apply_patch was requested via')) return true
  if (name === 'tool loaded.' || name === 'tool loaded') return true
  const isDefaultSessionName = /^session-[0-9a-f]{8}$/i.test((session.display_name || '').trim())
  return isDefaultSessionName && !session.has_real_user_input
}

export function pickDesktopWidgetActiveSession(sessions: DesktopWidgetSession[]): DesktopWidgetSession | null {
  const visible = sessions
    .filter((session) => !isDesktopWidgetWarmupSession(session))
    .sort((a, b) => getSessionActivityEpoch(b) - getSessionActivityEpoch(a))
  return visible[0] || null
}

export function applyDesktopWidgetStateEvent(
  sessions: DesktopWidgetSession[],
  event: SessionStateEvent,
  limit = DEFAULT_HISTORY_LIMIT,
): DesktopWidgetSession[] {
  const incoming: DesktopWidgetSession = {
    session_id: event.session_id,
    display_name: event.display_name || `session-${event.session_id.slice(0, 8)}`,
    state: event.state,
    last_seen_at: event.ts,
    active: true,
    agent_brand: event.agent_brand,
    has_real_user_input: !!event.has_real_user_input,
    originator: event.meta?.originator,
    cwd: event.meta?.cwd,
    cwd_basename: event.meta?.cwd_basename,
    branch: event.meta?.branch,
    last_event_type: event.meta?.last_event_type,
    context: event.meta?.context,
  }
  return mergeHistorySessions(sessions, [incoming], limit).map(toSession)
}

function formatBrand(value: string | undefined): string {
  const normalized = String(value || 'codex').trim()
  if (!normalized) return 'Codex'
  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

function formatRateLimit(session: DesktopWidgetSession | null): string {
  const primary = session?.context?.primary_rate_remaining_percent
  const secondary = session?.context?.secondary_rate_remaining_percent
  const parts: string[] = []
  if (typeof primary === 'number') parts.push(`P ${Math.round(primary)}%`)
  if (typeof secondary === 'number') parts.push(`S ${Math.round(secondary)}%`)
  return parts.join(' / ')
}

export function useDesktopWidgetMonitor(options: DesktopWidgetMonitorOptions = {}): DesktopWidgetMonitor {
  const serverUrl = options.serverUrl ?? getDefaultServerUrl()
  const bridgeUrl = resolveBridgeWsUrl(serverUrl, options.bridgeUrl)
  const historyLimit = options.historyLimit ?? DEFAULT_HISTORY_LIMIT
  const fetchHistory = options.fetchHistory ?? fetchSessionBridgeHistory
  const setTimeoutFn = options.setTimeoutFn ?? window.setTimeout.bind(window)
  const clearTimeoutFn = options.clearTimeoutFn ?? window.clearTimeout.bind(window)
  const WebSocketClass = options.webSocketCtor ?? window.WebSocket
  const reconnectBaseDelayMs = options.reconnectBaseDelayMs ?? DEFAULT_RECONNECT_BASE_DELAY_MS
  const reconnectMaxDelayMs = options.reconnectMaxDelayMs ?? DEFAULT_RECONNECT_MAX_DELAY_MS

  const connectionStatus = ref<DesktopWidgetConnectionStatus>('connecting')
  const sessions = ref<DesktopWidgetSession[]>([])
  const activeSession = computed(() => pickDesktopWidgetActiveSession(sessions.value))
  const activeState = computed<SessionState>(() => activeSession.value?.state || 'IDLE')
  const activeStateText = computed(() => {
    if (connectionStatus.value === 'disconnected') return 'Bridge Disconnected'
    return stateText(activeState.value)
  })
  const brandName = computed(() => formatBrand(activeSession.value?.agent_brand))
  const cwdLabel = computed(() => activeSession.value?.cwd_basename || activeSession.value?.cwd || 'No active session')
  const rateLimitText = computed(() => formatRateLimit(activeSession.value))
  const lastEventText = computed(() => activeSession.value?.last_event_type || '')

  let ws: WebSocketLike | null = null
  let disposed = false
  let reconnectAttempt = 0
  let reconnectTimer: number | null = null

  function clearReconnectTimer(): void {
    if (reconnectTimer === null) return
    clearTimeoutFn(reconnectTimer)
    reconnectTimer = null
  }

  function scheduleReconnect(): void {
    if (disposed) return
    clearReconnectTimer()
    const delay = Math.min(reconnectBaseDelayMs * 2 ** reconnectAttempt, reconnectMaxDelayMs)
    reconnectAttempt += 1
    reconnectTimer = setTimeoutFn(() => {
      connect()
    }, delay)
  }

  async function refreshHistory(): Promise<void> {
    const response = await fetchHistory(serverUrl, historyLimit)
    const incoming = (response.sessions || []).map(toSession)
    sessions.value = mergeHistorySessions(sessions.value, incoming, historyLimit).map(toSession)
  }

  function connect(): void {
    clearReconnectTimer()
    if (ws) {
      try {
        ws.close()
      } catch {
        // ignore stale sockets
      }
      ws = null
    }

    connectionStatus.value = 'connecting'
    ws = new WebSocketClass(bridgeUrl)

    ws.onopen = () => {
      if (disposed) return
      connectionStatus.value = 'connected'
      reconnectAttempt = 0
      void refreshHistory()
    }

    ws.onmessage = (message) => {
      if (disposed) return
      try {
        const data = JSON.parse(message.data) as SessionStateEvent
        if (data.event !== 'session_state') return
        sessions.value = applyDesktopWidgetStateEvent(sessions.value, data, historyLimit)
      } catch (error) {
        console.error('Invalid desktop widget bridge event', error)
      }
    }

    ws.onclose = () => {
      if (disposed) return
      connectionStatus.value = 'disconnected'
      scheduleReconnect()
    }

    ws.onerror = () => {
      if (disposed) return
      connectionStatus.value = 'disconnected'
      ws?.close()
    }
  }

  function disconnect(): void {
    disposed = true
    clearReconnectTimer()
    if (!ws) return
    try {
      ws.close()
    } catch {
      // ignore
    }
    ws = null
  }

  if (getCurrentInstance()) {
    onMounted(async () => {
      try {
        await refreshHistory()
      } catch {
        connectionStatus.value = 'disconnected'
      }
      if (options.autoStart === false) return
      disposed = false
      connect()
    })

    onUnmounted(() => {
      disconnect()
    })
  }

  return {
    connectionStatus,
    sessions,
    activeSession,
    activeState,
    activeStateText,
    brandName,
    cwdLabel,
    rateLimitText,
    lastEventText,
    refreshHistory,
    connect,
    disconnect,
  }
}
