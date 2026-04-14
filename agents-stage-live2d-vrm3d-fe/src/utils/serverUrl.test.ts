import { afterEach, describe, expect, it, vi } from 'vitest'

import { getDefaultBridgeWsUrl, getDefaultServerUrl } from './serverUrl'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('serverUrl defaults', () => {
  it('uses shared local defaults when window is unavailable', () => {
    expect(getDefaultServerUrl()).toBe('http://127.0.0.1:8000')
    expect(getDefaultBridgeWsUrl()).toBe('ws://127.0.0.1:8000/api/session-bridge/ws')
  })

  it('uses shared local defaults on localhost pages', () => {
    vi.stubGlobal('window', {
      location: {
        hostname: 'localhost',
        protocol: 'http:',
        host: 'localhost:5173',
      },
    })

    expect(getDefaultServerUrl()).toBe('http://127.0.0.1:8000')
    expect(getDefaultBridgeWsUrl()).toBe('ws://127.0.0.1:8000/api/session-bridge/ws')
  })

  it('falls back to same-origin urls in remote mode', () => {
    vi.stubGlobal('window', {
      location: {
        hostname: 'example.com',
        protocol: 'https:',
        host: 'example.com',
      },
    })

    expect(getDefaultServerUrl()).toBe('')
    expect(getDefaultBridgeWsUrl()).toBe('wss://example.com/api/session-bridge/ws')
  })
})
