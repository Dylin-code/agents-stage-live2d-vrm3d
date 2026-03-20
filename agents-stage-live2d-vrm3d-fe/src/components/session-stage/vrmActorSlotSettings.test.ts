import { beforeEach, describe, expect, it } from 'vitest'
import {
  DEFAULT_VRM_ACTOR_SLOT_OPTIONS,
  VRM_ACTOR_SLOT_CONFIG_STORAGE_KEY,
  buildDefaultVrmActorSlotConfig,
  loadVrmActorSlotConfig,
  normalizeVrmActorSlotConfig,
  saveVrmActorSlotConfig,
} from './vrmActorSlotSettings'

const storage = new Map<string, string>()
const mockLocalStorage = {
  getItem(key: string): string | null {
    return storage.has(key) ? storage.get(key) || null : null
  },
  setItem(key: string, value: string): void {
    storage.set(key, String(value))
  },
  removeItem(key: string): void {
    storage.delete(key)
  },
  clear(): void {
    storage.clear()
  },
}

Object.defineProperty(globalThis, 'localStorage', {
  value: mockLocalStorage,
  configurable: true,
})

describe('vrmActorSlotSettings', () => {
  beforeEach(() => {
    mockLocalStorage.clear()
  })

  it('loads default slot config when storage is empty', () => {
    expect(loadVrmActorSlotConfig()).toEqual(buildDefaultVrmActorSlotConfig())
  })

  it('normalizes invalid slot config entries with defaults', () => {
    expect(normalizeVrmActorSlotConfig([
      DEFAULT_VRM_ACTOR_SLOT_OPTIONS[3]?.modelUrl,
      '/invalid/model.vrm',
    ])).toEqual([
      DEFAULT_VRM_ACTOR_SLOT_OPTIONS[3]?.modelUrl,
      DEFAULT_VRM_ACTOR_SLOT_OPTIONS[1]?.modelUrl,
      DEFAULT_VRM_ACTOR_SLOT_OPTIONS[2]?.modelUrl,
      DEFAULT_VRM_ACTOR_SLOT_OPTIONS[3]?.modelUrl,
    ])
  })

  it('saves normalized config to storage', () => {
    const saved = saveVrmActorSlotConfig([
      DEFAULT_VRM_ACTOR_SLOT_OPTIONS[1]?.modelUrl || '',
      DEFAULT_VRM_ACTOR_SLOT_OPTIONS[0]?.modelUrl || '',
      DEFAULT_VRM_ACTOR_SLOT_OPTIONS[3]?.modelUrl || '',
      DEFAULT_VRM_ACTOR_SLOT_OPTIONS[2]?.modelUrl || '',
    ])

    expect(saved).toEqual([
      DEFAULT_VRM_ACTOR_SLOT_OPTIONS[1]?.modelUrl,
      DEFAULT_VRM_ACTOR_SLOT_OPTIONS[0]?.modelUrl,
      DEFAULT_VRM_ACTOR_SLOT_OPTIONS[3]?.modelUrl,
      DEFAULT_VRM_ACTOR_SLOT_OPTIONS[2]?.modelUrl,
    ])
    expect(storage.get(VRM_ACTOR_SLOT_CONFIG_STORAGE_KEY)).toBe(JSON.stringify(saved))
  })
})
