import { beforeEach, describe, expect, it } from 'vitest'
import {
  VRM_ACTOR_SCALE_DEFAULT,
  VRM_ACTOR_SCALE_STORAGE_KEY,
  VRM_ACTOR_SCALE_MAX,
  VRM_ACTOR_SCALE_MIN,
  clampVrmActorScale,
  loadVrmActorScale,
  saveVrmActorScale,
} from './vrmActorScaleSettings'

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

describe('vrmActorScaleSettings', () => {
  beforeEach(() => {
    mockLocalStorage.clear()
  })

  it('loads default scale when storage is empty', () => {
    expect(loadVrmActorScale()).toBe(VRM_ACTOR_SCALE_DEFAULT)
  })

  it('clamps persisted scale into valid range', () => {
    mockLocalStorage.setItem(VRM_ACTOR_SCALE_STORAGE_KEY, String(VRM_ACTOR_SCALE_MAX + 1))
    expect(loadVrmActorScale()).toBe(VRM_ACTOR_SCALE_MAX)

    mockLocalStorage.setItem(VRM_ACTOR_SCALE_STORAGE_KEY, String(VRM_ACTOR_SCALE_MIN - 1))
    expect(loadVrmActorScale()).toBe(VRM_ACTOR_SCALE_MIN)
  })

  it('saves clamped values', () => {
    expect(saveVrmActorScale(VRM_ACTOR_SCALE_MAX + 1)).toBe(VRM_ACTOR_SCALE_MAX)
    expect(saveVrmActorScale(VRM_ACTOR_SCALE_MIN - 1)).toBe(VRM_ACTOR_SCALE_MIN)
    expect(clampVrmActorScale(1.95)).toBe(1.95)
  })
})
