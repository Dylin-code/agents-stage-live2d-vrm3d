import {
  buildDefaultVrmActorSlotConfig,
  VRM_ACTOR_SLOT_CONFIG_STORAGE_KEY,
} from '../components/session-stage/vrmActorSlotSettings'
import { VRM_ACTOR_SCALE_DEFAULT, VRM_ACTOR_SCALE_STORAGE_KEY } from '../components/session-stage/vrmActorScaleSettings'
import { VRM_GLOBAL_GROUND_OFFSET_STORAGE_KEY } from '../components/session-stage/vrmGroundOffsetSettings'
import { buildDefaultSystemSettings } from '../pages/session-stage/sessionStageDefaults'

export const FRONTEND_CONFIG_BACKUP_SCHEMA_VERSION = 1
export const FRONTEND_CONFIG_SETTINGS_STORAGE_KEY = 'live2d-viewer-settings'
export const FRONTEND_CONFIG_CONVERSATIONS_STORAGE_KEY = 'live2d-viewer-conversations'
export const FRONTEND_CONFIG_BEHAVIOR_FLOW_STORAGE_KEYS = [
  'vrm-stage-behavior-flows-v3',
  'vrm-stage-behavior-flows-v2',
  'vrm-stage-behavior-flows-v1',
] as const
export const FRONTEND_CONFIG_ROUTE_STORAGE_KEY = 'vrm-stage-custom-route-v1'
export const FRONTEND_CONFIG_CAMERA_STORAGE_KEY = 'vrm-stage-camera-view-v1'
export const FRONTEND_CONFIG_INTERACTION_POINTS_STORAGE_KEY_PREFIX = 'vrm-stage-interaction-points-v1:'

export interface FrontendConfigBackupPayload {
  schemaVersion: number
  source: string
  exportedAt: string
  entries: Record<string, string>
}

export interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
  key(index: number): string | null
  readonly length: number
}

const MANAGED_STORAGE_EXACT_KEYS = [
  FRONTEND_CONFIG_SETTINGS_STORAGE_KEY,
  FRONTEND_CONFIG_CONVERSATIONS_STORAGE_KEY,
  VRM_ACTOR_SCALE_STORAGE_KEY,
  VRM_GLOBAL_GROUND_OFFSET_STORAGE_KEY,
  VRM_ACTOR_SLOT_CONFIG_STORAGE_KEY,
  ...FRONTEND_CONFIG_BEHAVIOR_FLOW_STORAGE_KEYS,
  FRONTEND_CONFIG_ROUTE_STORAGE_KEY,
  FRONTEND_CONFIG_CAMERA_STORAGE_KEY,
] as const

const MANAGED_STORAGE_PREFIXES = [
  FRONTEND_CONFIG_INTERACTION_POINTS_STORAGE_KEY_PREFIX,
] as const

export function isManagedFrontendStorageKey(key: string): boolean {
  return MANAGED_STORAGE_EXACT_KEYS.includes(key as typeof MANAGED_STORAGE_EXACT_KEYS[number])
    || MANAGED_STORAGE_PREFIXES.some((prefix) => key.startsWith(prefix))
}

export function listManagedFrontendStorageKeys(storage: StorageLike): string[] {
  const keys: string[] = []
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index)
    if (!key || !isManagedFrontendStorageKey(key)) continue
    keys.push(key)
  }
  return keys.sort((a, b) => a.localeCompare(b))
}

export function createFrontendConfigBackup(
  storage: StorageLike,
  exportedAt = new Date().toISOString(),
): FrontendConfigBackupPayload {
  const entries: Record<string, string> = {}
  for (const key of listManagedFrontendStorageKeys(storage)) {
    const value = storage.getItem(key)
    if (value === null) continue
    entries[key] = value
  }
  return {
    schemaVersion: FRONTEND_CONFIG_BACKUP_SCHEMA_VERSION,
    source: 'agents-stage-live2d-vrm3d-fe',
    exportedAt,
    entries,
  }
}

export function parseFrontendConfigBackup(raw: string): FrontendConfigBackupPayload {
  const parsed = JSON.parse(raw) as Partial<FrontendConfigBackupPayload>
  if (parsed?.schemaVersion !== FRONTEND_CONFIG_BACKUP_SCHEMA_VERSION) {
    throw new Error(`Unsupported config schema version: ${String(parsed?.schemaVersion)}`)
  }
  if (!parsed.entries || typeof parsed.entries !== 'object' || Array.isArray(parsed.entries)) {
    throw new Error('Config backup entries must be an object')
  }

  const entries = Object.entries(parsed.entries).reduce<Record<string, string>>((result, [key, value]) => {
    if (typeof key !== 'string' || typeof value !== 'string') {
      throw new Error('Config backup entries must contain string key/value pairs')
    }
    result[key] = value
    return result
  }, {})

  return {
    schemaVersion: FRONTEND_CONFIG_BACKUP_SCHEMA_VERSION,
    source: typeof parsed.source === 'string' && parsed.source ? parsed.source : 'unknown',
    exportedAt: typeof parsed.exportedAt === 'string' && parsed.exportedAt ? parsed.exportedAt : '',
    entries,
  }
}

export function applyFrontendConfigBackup(
  storage: StorageLike,
  backup: FrontendConfigBackupPayload,
): FrontendConfigBackupPayload {
  const normalized = parseFrontendConfigBackup(JSON.stringify(backup))
  for (const key of listManagedFrontendStorageKeys(storage)) {
    storage.removeItem(key)
  }
  for (const [key, value] of Object.entries(normalized.entries)) {
    if (!isManagedFrontendStorageKey(key)) continue
    storage.setItem(key, value)
  }
  return normalized
}

export function createDefaultFrontendConfigBackup(): FrontendConfigBackupPayload {
  return {
    schemaVersion: FRONTEND_CONFIG_BACKUP_SCHEMA_VERSION,
    source: 'agents-stage-live2d-vrm3d-fe-default',
    exportedAt: 'default-config',
    entries: {
      [FRONTEND_CONFIG_SETTINGS_STORAGE_KEY]: JSON.stringify({
        systemSettings: buildDefaultSystemSettings(),
      }),
      [VRM_ACTOR_SCALE_STORAGE_KEY]: String(VRM_ACTOR_SCALE_DEFAULT),
      [VRM_GLOBAL_GROUND_OFFSET_STORAGE_KEY]: '0',
      [VRM_ACTOR_SLOT_CONFIG_STORAGE_KEY]: JSON.stringify(buildDefaultVrmActorSlotConfig()),
    },
  }
}
