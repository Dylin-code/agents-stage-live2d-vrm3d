import { describe, expect, it, vi } from 'vitest'
import type { SessionHistoryResponse, SessionStateEvent } from '../../types/sessionState'
import {
  applyDesktopWidgetStateEvent,
  isDesktopWidgetWarmupSession,
  pickDesktopWidgetActiveSession,
  useDesktopWidgetMonitor,
} from './desktopWidgetMonitor'

class FakeWebSocket {
  static latest: FakeWebSocket | null = null
  url: string
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.latest = this
  }

  close(): void {
    // test double
  }
}

function historyResponse(sessions: SessionHistoryResponse['sessions']): SessionHistoryResponse {
  return {
    version: '1',
    generated_at: '2026-04-27T10:00:00.000Z',
    sessions,
  }
}

function event(overrides: Partial<SessionStateEvent> = {}): SessionStateEvent {
  return {
    version: '1',
    event: 'session_state',
    session_id: 's-1',
    display_name: 'Codex task',
    state: 'THINKING',
    ts: '2026-04-27T10:02:00.000Z',
    source: 'codex',
    agent_brand: 'codex',
    has_real_user_input: true,
    meta: {
      cwd: '/repo/widget',
      cwd_basename: 'widget',
      last_event_type: 'agent_reasoning',
      branch: 'main',
      context: {
        primary_rate_remaining_percent: 72,
      },
    },
    ...overrides,
  }
}

describe('desktop widget monitor', () => {
  it('initializes active session from history and ignores internal warmup sessions', async () => {
    const monitor = useDesktopWidgetMonitor({
      autoStart: false,
      serverUrl: 'http://127.0.0.1:8000',
      fetchHistory: async () => historyResponse([
        {
          session_id: 'warmup',
          display_name: 'session-deadbeef',
          state: 'RESPONDING',
          last_seen_at: '2026-04-27T10:05:00.000Z',
          active: true,
          has_real_user_input: false,
        },
        {
          session_id: 'real',
          display_name: 'Implement widget',
          state: 'TOOLING',
          last_seen_at: '2026-04-27T10:00:00.000Z',
          active: true,
          has_real_user_input: true,
          agent_brand: 'claude',
          cwd: '/repo/live2d',
          cwd_basename: 'live2d',
        },
      ]),
    })

    await monitor.refreshHistory()

    expect(monitor.sessions.value).toHaveLength(2)
    expect(monitor.activeSession.value?.session_id).toBe('real')
    expect(monitor.activeState.value).toBe('TOOLING')
    expect(monitor.brandName.value).toBe('Claude')
    expect(monitor.cwdLabel.value).toBe('live2d')
  })

  it('updates active session details from websocket session_state events', () => {
    const monitor = useDesktopWidgetMonitor({
      autoStart: false,
      serverUrl: 'http://127.0.0.1:8000',
      webSocketCtor: FakeWebSocket as any,
      fetchHistory: async () => historyResponse([]),
    })

    monitor.connect()
    FakeWebSocket.latest?.onopen?.()
    FakeWebSocket.latest?.onmessage?.({ data: JSON.stringify(event()) })

    expect(monitor.connectionStatus.value).toBe('connected')
    expect(monitor.activeSession.value?.session_id).toBe('s-1')
    expect(monitor.activeState.value).toBe('THINKING')
    expect(monitor.brandName.value).toBe('Codex')
    expect(monitor.cwdLabel.value).toBe('widget')
    expect(monitor.rateLimitText.value).toBe('P 72%')
    expect(monitor.lastEventText.value).toBe('agent_reasoning')
  })

  it('marks the bridge disconnected without clearing the last displayable session', () => {
    const setTimeoutFn = vi.fn(() => 1) as any
    const monitor = useDesktopWidgetMonitor({
      autoStart: false,
      serverUrl: 'http://127.0.0.1:8000',
      webSocketCtor: FakeWebSocket as any,
      fetchHistory: async () => historyResponse([]),
      setTimeoutFn,
      clearTimeoutFn: vi.fn() as any,
    })

    monitor.connect()
    FakeWebSocket.latest?.onopen?.()
    FakeWebSocket.latest?.onmessage?.({ data: JSON.stringify(event({ state: 'RESPONDING' })) })
    FakeWebSocket.latest?.onclose?.()

    expect(monitor.connectionStatus.value).toBe('disconnected')
    expect(monitor.activeSession.value?.session_id).toBe('s-1')
    expect(monitor.activeStateText.value).toBe('Bridge Disconnected')
    expect(setTimeoutFn).toHaveBeenCalled()
  })

  it('applies session_state events through merge semantics', () => {
    const sessions = applyDesktopWidgetStateEvent([], event({ state: 'WAITING' }))
    const updated = applyDesktopWidgetStateEvent(sessions, event({
      state: 'TOOLING',
      ts: '2026-04-27T10:03:00.000Z',
      meta: {
        cwd_basename: 'widget',
        last_event_type: 'function_call',
      },
    }))

    expect(updated).toHaveLength(1)
    expect(updated[0].state).toBe('TOOLING')
    expect(updated[0].last_event_type).toBe('function_call')
  })

  it('keeps the warmup filter consistent with stage defaults', () => {
    expect(isDesktopWidgetWarmupSession({
      display_name: '# AGENTS.md instructions for /repo',
      has_real_user_input: false,
    })).toBe(true)
    expect(isDesktopWidgetWarmupSession({
      display_name: 'session-deadbeef',
      has_real_user_input: false,
    })).toBe(true)
    expect(pickDesktopWidgetActiveSession([
      {
        session_id: 'warmup',
        display_name: 'session-deadbeef',
        state: 'IDLE',
        last_seen_at: '2026-04-27T10:05:00.000Z',
        active: true,
        has_real_user_input: false,
      },
      {
        session_id: 'real',
        display_name: 'session-deadbeef',
        state: 'IDLE',
        last_seen_at: '2026-04-27T10:00:00.000Z',
        active: true,
        has_real_user_input: true,
      },
    ])?.session_id).toBe('real')
  })
})
