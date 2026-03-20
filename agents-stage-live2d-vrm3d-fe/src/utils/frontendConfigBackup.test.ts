import { describe, expect, it } from 'vitest'

import {
  applyFrontendConfigBackup,
  createDefaultFrontendConfigBackup,
  createFrontendConfigBackup,
  FRONTEND_CONFIG_CAMERA_STORAGE_KEY,
  FRONTEND_CONFIG_CONVERSATIONS_STORAGE_KEY,
  FRONTEND_CONFIG_INTERACTION_POINTS_STORAGE_KEY_PREFIX,
  FRONTEND_CONFIG_SETTINGS_STORAGE_KEY,
  listManagedFrontendStorageKeys,
  parseFrontendConfigBackup,
} from './frontendConfigBackup'

class MemoryStorage {
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

describe('frontendConfigBackup', () => {
  it('collects only managed frontend storage keys', () => {
    const storage = new MemoryStorage()
    storage.setItem(FRONTEND_CONFIG_SETTINGS_STORAGE_KEY, '{"systemSettings":{}}')
    storage.setItem(FRONTEND_CONFIG_CONVERSATIONS_STORAGE_KEY, '{"conversationItems":[]}')
    storage.setItem(`${FRONTEND_CONFIG_INTERACTION_POINTS_STORAGE_KEY_PREFIX}scene.glb`, '{"points":[]}')
    storage.setItem('unrelated-key', 'ignore-me')

    expect(listManagedFrontendStorageKeys(storage)).toEqual([
      FRONTEND_CONFIG_CONVERSATIONS_STORAGE_KEY,
      FRONTEND_CONFIG_SETTINGS_STORAGE_KEY,
      `${FRONTEND_CONFIG_INTERACTION_POINTS_STORAGE_KEY_PREFIX}scene.glb`,
    ])

    expect(createFrontendConfigBackup(storage, '2026-03-20T00:00:00.000Z')).toEqual({
      schemaVersion: 1,
      source: 'agents-stage-live2d-vrm3d-fe',
      exportedAt: '2026-03-20T00:00:00.000Z',
      entries: {
        [FRONTEND_CONFIG_CONVERSATIONS_STORAGE_KEY]: '{"conversationItems":[]}',
        [FRONTEND_CONFIG_SETTINGS_STORAGE_KEY]: '{"systemSettings":{}}',
        [`${FRONTEND_CONFIG_INTERACTION_POINTS_STORAGE_KEY_PREFIX}scene.glb`]: '{"points":[]}',
      },
    })
  })

  it('replaces existing managed keys when applying backup', () => {
    const storage = new MemoryStorage()
    storage.setItem(FRONTEND_CONFIG_SETTINGS_STORAGE_KEY, '{"old":true}')
    storage.setItem(FRONTEND_CONFIG_CAMERA_STORAGE_KEY, '{"camera":"legacy"}')
    storage.setItem('unrelated-key', 'keep-me')

    applyFrontendConfigBackup(storage, {
      schemaVersion: 1,
      source: 'test',
      exportedAt: '2026-03-20T00:00:00.000Z',
      entries: {
        [FRONTEND_CONFIG_SETTINGS_STORAGE_KEY]: '{"systemSettings":{"serverUrl":"http://127.0.0.1:8000"}}',
      },
    })

    expect(storage.getItem(FRONTEND_CONFIG_SETTINGS_STORAGE_KEY)).toBe(
      '{"systemSettings":{"serverUrl":"http://127.0.0.1:8000"}}',
    )
    expect(storage.getItem(FRONTEND_CONFIG_CAMERA_STORAGE_KEY)).toBeNull()
    expect(storage.getItem('unrelated-key')).toBe('keep-me')
  })

  it('validates backup schema and default payload', () => {
    expect(() => parseFrontendConfigBackup('{"schemaVersion":99,"entries":{}}')).toThrow(
      'Unsupported config schema version: 99',
    )

    const backup = createDefaultFrontendConfigBackup()
    expect(backup.schemaVersion).toBe(1)
    expect(Object.keys(backup.entries)).toContain(FRONTEND_CONFIG_SETTINGS_STORAGE_KEY)
    expect(JSON.parse(backup.entries[FRONTEND_CONFIG_SETTINGS_STORAGE_KEY] || '{}')).toMatchObject({
      systemSettings: {
        serverUrl: 'http://127.0.0.1:8000',
      },
    })
  })
})
