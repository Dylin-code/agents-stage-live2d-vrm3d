import type { FrontendConfigBackupPayload } from '../frontendConfigBackup'
import { getDefaultServerUrl } from '../serverUrl'

const DEFAULT_SERVER_URL = getDefaultServerUrl()
const STAGE_CONFIG_PATH = '/api/stage-config'

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '')
}

export function resolveStageConfigUrl(serverUrl?: string): string {
  const base = trimTrailingSlash(serverUrl || DEFAULT_SERVER_URL)
  return `${base}${STAGE_CONFIG_PATH}`
}

export async function fetchStageConfig(serverUrl?: string): Promise<FrontendConfigBackupPayload> {
  const response = await fetch(resolveStageConfigUrl(serverUrl), {
    method: 'GET',
  })
  if (!response.ok) {
    throw new Error(`failed to fetch stage config: ${response.status}`)
  }
  return (await response.json()) as FrontendConfigBackupPayload
}

export async function saveStageConfig(
  payload: FrontendConfigBackupPayload,
  serverUrl?: string,
): Promise<FrontendConfigBackupPayload> {
  const response = await fetch(resolveStageConfigUrl(serverUrl), {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    throw new Error(`failed to save stage config: ${response.status}`)
  }
  return (await response.json()) as FrontendConfigBackupPayload
}
