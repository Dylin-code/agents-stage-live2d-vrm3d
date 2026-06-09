import { describe, expect, it } from 'vitest'

import {
  POWER_SAVE_MODE_STORAGE_KEY,
  loadPowerSaveMode,
  savePowerSaveMode,
} from './powerSaveModeSettings'

class MemoryStorage {
  private readonly storage = new Map<string, string>()

  getItem(key: string): string | null {
    return this.storage.get(key) ?? null
  }

  setItem(key: string, value: string): void {
    this.storage.set(key, value)
  }
}

describe('powerSaveModeSettings', () => {
  it('defaults to disabled when storage has no value', () => {
    expect(loadPowerSaveMode(new MemoryStorage())).toBe(false)
  })

  it('persists enabled and disabled states as booleans', () => {
    const storage = new MemoryStorage()

    expect(savePowerSaveMode(true, storage)).toBe(true)
    expect(storage.getItem(POWER_SAVE_MODE_STORAGE_KEY)).toBe('true')
    expect(loadPowerSaveMode(storage)).toBe(true)

    expect(savePowerSaveMode(false, storage)).toBe(false)
    expect(storage.getItem(POWER_SAVE_MODE_STORAGE_KEY)).toBe('false')
    expect(loadPowerSaveMode(storage)).toBe(false)
  })
})
