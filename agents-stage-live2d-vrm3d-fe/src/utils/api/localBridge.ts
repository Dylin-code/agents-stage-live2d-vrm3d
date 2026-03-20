const DEFAULT_SERVER_URL = 'http://127.0.0.1:8000'
const LOCAL_SELECT_DIRECTORY_PATH = '/api/local/select-directory'

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '')
}

interface LocalSelectDirectoryPayload {
  title?: string
  default_path?: string
}

interface LocalSelectDirectoryResponse {
  path?: string
  canceled?: boolean
}

export async function selectDirectoryViaLocalBridge(
  serverUrl: string | undefined,
  payload: LocalSelectDirectoryPayload,
): Promise<string> {
  const base = trimTrailingSlash(serverUrl || DEFAULT_SERVER_URL)
  const response = await fetch(`${base}${LOCAL_SELECT_DIRECTORY_PATH}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload || {}),
  })
  if (!response.ok) {
    throw new Error(`failed to select directory by local bridge: ${response.status}`)
  }
  const data = (await response.json()) as LocalSelectDirectoryResponse
  if (data.canceled) {
    return ''
  }
  return String(data.path || '').trim()
}
