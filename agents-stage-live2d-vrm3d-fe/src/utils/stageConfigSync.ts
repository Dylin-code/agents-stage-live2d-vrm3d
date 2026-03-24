import {
  applyFrontendConfigBackup,
  createFrontendConfigBackup,
  type FrontendConfigBackupPayload,
  type StorageLike,
} from './frontendConfigBackup'
import { fetchStageConfig, saveStageConfig } from './api/stageConfig'

const STAGE_CONFIG_SYNC_META_STORAGE_KEY = 'stage-config-sync-meta-v1'
const AUTO_SYNC_INTERVAL_MS = 2000

interface StageConfigSyncMeta {
  syncedFingerprint: string
}

interface BrowserLike {
  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void
  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void
  setInterval(handler: TimerHandler, timeout?: number): number
  clearInterval(id: number): void
}

export interface StageConfigSyncBootstrapResult {
  source: 'remote' | 'local' | 'none'
  fingerprint: string
}

function buildEmptySnapshot(): FrontendConfigBackupPayload {
  return {
    schemaVersion: 1,
    source: 'agents-stage-live2d-vrm3d-fe',
    exportedAt: '',
    entries: {},
  }
}

export function computeStageConfigFingerprint(payload: FrontendConfigBackupPayload): string {
  return JSON.stringify({
    schemaVersion: payload.schemaVersion,
    entries: payload.entries,
  })
}

function readSyncMeta(storage: StorageLike): StageConfigSyncMeta | null {
  const raw = storage.getItem(STAGE_CONFIG_SYNC_META_STORAGE_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<StageConfigSyncMeta>
    if (typeof parsed.syncedFingerprint !== 'string' || !parsed.syncedFingerprint) {
      return null
    }
    return { syncedFingerprint: parsed.syncedFingerprint }
  } catch {
    return null
  }
}

function writeSyncMeta(storage: StorageLike, fingerprint: string): void {
  storage.setItem(STAGE_CONFIG_SYNC_META_STORAGE_KEY, JSON.stringify({ syncedFingerprint: fingerprint }))
}

export async function bootstrapStageConfigSync(
  storage: StorageLike,
  serverUrl?: string,
): Promise<StageConfigSyncBootstrapResult> {
  const localSnapshot = createFrontendConfigBackup(storage)
  const localFingerprint = computeStageConfigFingerprint(localSnapshot)
  const localEntryCount = Object.keys(localSnapshot.entries).length
  const syncMeta = readSyncMeta(storage)

  let remoteSnapshot = buildEmptySnapshot()
  try {
    remoteSnapshot = await fetchStageConfig(serverUrl)
  } catch (error) {
    console.error('Failed to fetch stage config during bootstrap', error)
    if (localEntryCount > 0) {
      try {
        await saveStageConfig(localSnapshot, serverUrl)
        writeSyncMeta(storage, localFingerprint)
        return { source: 'local', fingerprint: localFingerprint }
      } catch (saveError) {
        console.error('Failed to save local stage config fallback during bootstrap', saveError)
      }
    }
    return { source: 'none', fingerprint: localFingerprint }
  }

  const remoteFingerprint = computeStageConfigFingerprint(remoteSnapshot)
  const remoteEntryCount = Object.keys(remoteSnapshot.entries).length

  if (localEntryCount === 0) {
    if (remoteEntryCount > 0) {
      applyFrontendConfigBackup(storage, remoteSnapshot)
      writeSyncMeta(storage, remoteFingerprint)
      return { source: 'remote', fingerprint: remoteFingerprint }
    }
    writeSyncMeta(storage, remoteFingerprint)
    return { source: 'none', fingerprint: remoteFingerprint }
  }

  if (syncMeta?.syncedFingerprint === localFingerprint && remoteFingerprint !== localFingerprint && remoteEntryCount > 0) {
    applyFrontendConfigBackup(storage, remoteSnapshot)
    writeSyncMeta(storage, remoteFingerprint)
    return { source: 'remote', fingerprint: remoteFingerprint }
  }

  try {
    await saveStageConfig(localSnapshot, serverUrl)
    writeSyncMeta(storage, localFingerprint)
  } catch (error) {
    console.error('Failed to push local stage config during bootstrap', error)
  }
  return { source: 'local', fingerprint: localFingerprint }
}

export function startStageConfigAutoSync(
  storage: StorageLike,
  browser: BrowserLike,
  serverUrl?: string,
): () => void {
  let stopped = false
  let running = false
  let queued = false
  let lastFingerprint = computeStageConfigFingerprint(createFrontendConfigBackup(storage))

  const syncNow = async (): Promise<void> => {
    if (stopped) return
    const snapshot = createFrontendConfigBackup(storage)
    const fingerprint = computeStageConfigFingerprint(snapshot)
    if (!queued && fingerprint === lastFingerprint) {
      return
    }
    if (running) {
      queued = true
      return
    }
    running = true
    queued = false
    try {
      await saveStageConfig(snapshot, serverUrl)
      lastFingerprint = fingerprint
      writeSyncMeta(storage, fingerprint)
    } catch (error) {
      console.error('Failed to sync stage config', error)
    } finally {
      running = false
      if (queued && !stopped) {
        queued = false
        void syncNow()
      }
    }
  }

  const handleVisibilityChange = (): void => {
    void syncNow()
  }

  const intervalId = browser.setInterval(() => {
    void syncNow()
  }, AUTO_SYNC_INTERVAL_MS)

  browser.addEventListener('beforeunload', handleVisibilityChange)
  browser.addEventListener('visibilitychange', handleVisibilityChange)

  return () => {
    stopped = true
    browser.clearInterval(intervalId)
    browser.removeEventListener('beforeunload', handleVisibilityChange)
    browser.removeEventListener('visibilitychange', handleVisibilityChange)
  }
}
