import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createSessionBridgeSession,
  resolveBridgeWsUrl,
  resolveConversationUrl,
  resolveHistoryUrl,
  resolveSnapshotUrl,
  resolveAgentApprovalUrl,
  submitSessionBridgeApproval,
} from './sessionBridge'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('resolveConversationUrl', () => {
  it('builds conversation path with encoded session id', () => {
    const url = resolveConversationUrl('http://127.0.0.1:8000/', 'session/id', 123)
    expect(url).toBe('http://127.0.0.1:8000/api/session-bridge/conversation/session%2Fid?limit=123')
  })

  it('clamps limit to allowed range', () => {
    expect(resolveConversationUrl(undefined, 'abc', 0)).toContain('limit=1')
    expect(resolveConversationUrl(undefined, 'abc', 999999)).toContain('limit=5000')
  })
})

describe('session bridge url resolvers', () => {
  it('normalizes snapshot/history urls and trims trailing slash', () => {
    expect(resolveSnapshotUrl('http://127.0.0.1:8000/')).toBe('http://127.0.0.1:8000/api/session-bridge/snapshot')
    expect(resolveHistoryUrl('http://127.0.0.1:8000/', 999)).toBe('http://127.0.0.1:8000/api/session-bridge/history?limit=200')
  })

  it('converts http server url to websocket bridge url when no override', () => {
    expect(resolveBridgeWsUrl('http://localhost:8000')).toBe('ws://localhost:8000/api/session-bridge/ws')
    expect(resolveBridgeWsUrl('https://example.com')).toBe('wss://example.com/api/session-bridge/ws')
  })

  it('prefers explicit bridge override', () => {
    expect(resolveBridgeWsUrl('http://localhost:8000', 'ws://1.2.3.4/ws')).toBe('ws://1.2.3.4/ws')
  })

  it('resolves unified agent approval url', () => {
    expect(resolveAgentApprovalUrl('http://127.0.0.1:8000/')).toBe('http://127.0.0.1:8000/api/session-bridge/agent/chat/approval')
  })
})

describe('submitSessionBridgeApproval', () => {
  it('uses unified agent approval endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await submitSessionBridgeApproval('http://127.0.0.1:8000/', {
      pending_id: 'pending-1',
      decision: 'allow_once',
      agent_brand: 'claude',
    })

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/session-bridge/agent/chat/approval',
      expect.objectContaining({
        method: 'POST',
      }),
    )
  })
})

describe('createSessionBridgeSession', () => {
  it('uses unified agent new-session endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ session_id: 's1', agent_brand: 'codex' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await createSessionBridgeSession('http://127.0.0.1:8000/', {
      cwd: '/tmp/work',
      agent_brand: 'claude',
    })

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/session-bridge/agent/session/new',
      expect.objectContaining({
        method: 'POST',
      }),
    )
  })
})
