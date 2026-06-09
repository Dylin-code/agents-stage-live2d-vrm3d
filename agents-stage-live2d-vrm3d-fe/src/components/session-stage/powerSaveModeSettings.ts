export const POWER_SAVE_MODE_STORAGE_KEY = 'session-stage-power-save-mode-v1'
export const POWER_SAVE_MODE_EVENT = 'session-stage:power-save-mode-change'

export interface PowerSaveModeEventDetail {
  enabled: boolean
}

interface PowerSaveModeStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

function getBrowserStorage(): PowerSaveModeStorage | null {
  if (typeof window === 'undefined') return null
  return window.localStorage
}

export function loadPowerSaveMode(storage: PowerSaveModeStorage | null = getBrowserStorage()): boolean {
  if (!storage) return false
  try {
    return storage.getItem(POWER_SAVE_MODE_STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

export function savePowerSaveMode(
  enabled: boolean,
  storage: PowerSaveModeStorage | null = getBrowserStorage(),
): boolean {
  if (!storage) return enabled
  try {
    storage.setItem(POWER_SAVE_MODE_STORAGE_KEY, enabled ? 'true' : 'false')
  } catch {
    // Storage can fail in private browsing or constrained test environments.
  }
  return enabled
}
