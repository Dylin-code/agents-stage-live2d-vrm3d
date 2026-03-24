import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  FRONTEND_CONFIG_SETTINGS_STORAGE_KEY,
  type FrontendConfigBackupPayload,
  type StorageLike,
} from './frontendConfigBackup'
import { bootstrapStageConfigSync, computeStageConfigFingerprint, startStageConfigAutoSync } from './stageConfigSync'

vi.mock('./api/stageConfig', () => ({
  fetchStageConfig: vi.fn(),
  saveStageConfig: vi.fn(),
}))

import { fetchStageConfig, saveStageConfig } from './api/stageConfig'

class MemoryStorage implements StorageLike {
  private readonly storage = new Map<string, string>()

  get length(): number {
    return this.storage.size
  }

  getItem(key: string): string | null {
    return this.storage.get(key) ?? null
  }

  setItem(key: string, value: string): void {
    this.storage.set(key, value)
  }

  removeItem(key: string): void {
    this.storage.delete(key)
  }

  key(index: number): string | null {
    return [...this.storage.keys()][index] ?? null
  }
}

class BrowserStub {
  private readonly listeners = new Map<string, Set<EventListenerOrEventListenerObject>>()
  private readonly intervals = new Map<number, () => void>()
  private sequence = 0

  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    const bucket = this.listeners.get(type) || new Set<EventListenerOrEventListenerObject>()
    bucket.add(listener)
    this.listeners.set(type, bucket)
  }

  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    this.listeners.get(type)?.delete(listener)
  }

  setInterval(handler: TimerHandler): number {
    const id = ++this.sequence
    this.intervals.set(id, handler as () => void)
    return id
  }

  clearInterval(id: number): void {
    this.intervals.delete(id)
  }

  tickAll(): void {
    for (const handler of this.intervals.values()) {
      handler()
    }
  }
}

function buildRemotePayload(entries: Record<string, string>): FrontendConfigBackupPayload {
  return {
    schemaVersion: 1,
    source: 'remote',
    exportedAt: '2026-03-24T00:00:00.000Z',
    entries,
  }
}

describe('stageConfigSync', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('hydrates local storage from remote when local config is empty', async () => {
    const storage = new MemoryStorage()
    vi.mocked(fetchStageConfig).mockResolvedValueOnce(buildRemotePayload({
      [FRONTEND_CONFIG_SETTINGS_STORAGE_KEY]: '{"systemSettings":{"serverUrl":"http://remote"}}',
    }))

    const result = await bootstrapStageConfigSync(storage)

    expect(result.source).toBe('remote')
    expect(storage.getItem(FRONTEND_CONFIG_SETTINGS_STORAGE_KEY)).toBe(
      '{"systemSettings":{"serverUrl":"http://remote"}}',
    )
  })

  it('pushes local storage to remote when local config changed after last sync', async () => {
    const storage = new MemoryStorage()
    storage.setItem(FRONTEND_CONFIG_SETTINGS_STORAGE_KEY, '{"systemSettings":{"serverUrl":"http://local"}}')
    storage.setItem('stage-config-sync-meta-v1', JSON.stringify({
      syncedFingerprint: computeStageConfigFingerprint(buildRemotePayload({
        [FRONTEND_CONFIG_SETTINGS_STORAGE_KEY]: '{"systemSettings":{"serverUrl":"http://old"}}',
      })),
    }))
    vi.mocked(fetchStageConfig).mockResolvedValueOnce(buildRemotePayload({
      [FRONTEND_CONFIG_SETTINGS_STORAGE_KEY]: '{"systemSettings":{"serverUrl":"http://remote"}}',
    }))

    const result = await bootstrapStageConfigSync(storage)

    expect(result.source).toBe('local')
    expect(saveStageConfig).toHaveBeenCalledTimes(1)
  })

  it('auto syncs when managed storage changes', async () => {
    const storage = new MemoryStorage()
    const browser = new BrowserStub()
    vi.mocked(saveStageConfig).mockResolvedValue(buildRemotePayload({}))

    const stop = startStageConfigAutoSync(storage, browser)
    storage.setItem(FRONTEND_CONFIG_SETTINGS_STORAGE_KEY, '{"systemSettings":{"serverUrl":"http://changed"}}')
    browser.tickAll()
    await Promise.resolve()
    await Promise.resolve()

    expect(saveStageConfig).toHaveBeenCalledTimes(1)
    stop()
  })
})
