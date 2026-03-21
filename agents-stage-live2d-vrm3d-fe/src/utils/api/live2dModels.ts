export interface Live2DModelItem {
  id: string
  name: string
  model_path: string
  preview_image?: string | null
  motion_keys?: string[]
  motion_semantic_map?: Record<string, string[]>
}

export interface Live2DModelListResponse {
  root_dir: string
  models: Live2DModelItem[]
  error?: string
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '')
}

export async function fetchLive2DModels(serverUrl: string): Promise<Live2DModelListResponse> {
  const base = trimTrailingSlash(serverUrl || '')
  const resp = await fetch(`${base}/api/live2d/models`, { method: 'GET' })
  if (!resp.ok) {
    throw new Error(`failed to fetch live2d models: ${resp.status}`)
  }
  return (await resp.json()) as Live2DModelListResponse
}
