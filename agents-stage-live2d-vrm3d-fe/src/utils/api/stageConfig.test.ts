import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchStageConfig, resolveStageConfigUrl, saveStageConfig } from './stageConfig'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('stageConfig api', () => {
  it('resolves stage config url', () => {
    expect(resolveStageConfigUrl('http://127.0.0.1:8000/')).toBe('http://127.0.0.1:8000/api/stage-config')
  })

  it('fetches stage config via GET', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ schemaVersion: 1, source: 'test', exportedAt: '', entries: {} }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await fetchStageConfig('http://127.0.0.1:8000/')

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/stage-config',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('saves stage config via PUT', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ schemaVersion: 1, source: 'test', exportedAt: '', entries: {} }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await saveStageConfig({
      schemaVersion: 1,
      source: 'test',
      exportedAt: '',
      entries: {},
    }, 'http://127.0.0.1:8000/')

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/stage-config',
      expect.objectContaining({ method: 'PUT' }),
    )
  })
})
