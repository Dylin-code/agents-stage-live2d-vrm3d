import { type Ref } from 'vue'

import type { SystemSettings } from '../../types/message'
import type { Live2DModelItem } from '../../utils/api/live2dModels'
import { fetchLive2DModels } from '../../utils/api/live2dModels'
import { resolveBridgeWsUrl } from '../../utils/api/sessionBridge'
import { normalizeModelPaths, type ModelMotionEntry } from './live2dMotionUtils'

export function createSessionStageModelCatalogUtils(args: {
  storageKey: string
  chatSystemSettings: Ref<SystemSettings>
  modelPathsInput: Ref<string>
  modelsRootDir: Ref<string>
  modelsLoading: Ref<boolean>
  modelsError: Ref<string>
  live2dModels: Ref<Live2DModelItem[]>
  modelMotionsByPath: Map<string, ModelMotionEntry[]>
  modelSemanticMotionsByPath: Map<string, Record<string, string[]>>
  sessionModelAssignments: Map<string, string>
  getServerUrl: () => string
  setServerUrl: (value: string) => void
  setBridgeUrl: (value: string) => void
  getModelPaths: () => string[]
  setModelPaths: (value: string[]) => void
  getModelScale: () => number
  setModelScale: (value: number) => void
  buildDefaultSystemSettings: () => SystemSettings
}) {
  const {
    storageKey,
    chatSystemSettings,
    modelPathsInput,
    modelsRootDir,
    modelsLoading,
    modelsError,
    live2dModels,
    modelMotionsByPath,
    modelSemanticMotionsByPath,
    sessionModelAssignments,
    getServerUrl,
    setServerUrl,
    setBridgeUrl,
    getModelPaths,
    setModelPaths,
    setModelScale,
    buildDefaultSystemSettings,
  } = args

  function saveSessionModelPathsToStorage(paths: string[]): void {
    const raw = localStorage.getItem(storageKey)
    const parsed = raw ? JSON.parse(raw) : {}
    const systemSettings = parsed.systemSettings || {}
    const sessionStage = systemSettings.sessionStage || {}
    sessionStage.modelPaths = paths.join(', ')
    systemSettings.sessionStage = sessionStage
    parsed.systemSettings = systemSettings
    chatSystemSettings.value = {
      ...chatSystemSettings.value,
      sessionStage: {
        ...chatSystemSettings.value.sessionStage,
        modelPaths: sessionStage.modelPaths,
      },
    }
    localStorage.setItem(storageKey, JSON.stringify(parsed))
  }

  function loadSettings(): void {
    const fallback = buildDefaultSystemSettings()
    chatSystemSettings.value = fallback
    const raw = localStorage.getItem(storageKey)
    if (!raw) {
      modelPathsInput.value = getModelPaths().join(', ')
      return
    }
    try {
      const parsed = JSON.parse(raw)
      const settings = parsed?.systemSettings || {}
      chatSystemSettings.value = {
        ...fallback,
        ...settings,
        sessionStage: { ...fallback.sessionStage, ...(settings.sessionStage || {}) },
        assistantSettings: { ...fallback.assistantSettings, ...(settings.assistantSettings || {}) },
        live2DSettings: { ...fallback.live2DSettings, ...(settings.live2DSettings || {}) },
      }
      if (settings.serverUrl) {
        setServerUrl(settings.serverUrl)
      }
      let modelPaths = getModelPaths()
      if (settings.live2DSettings?.modelPath) {
        modelPaths = normalizeModelPaths([settings.live2DSettings.modelPath], modelPaths)
      }
      modelPaths = normalizeModelPaths(settings.sessionStage?.modelPaths, modelPaths)
      setModelPaths(modelPaths)
      modelPathsInput.value = modelPaths.join(', ')
      if (typeof settings.live2DSettings?.scale === 'number') {
        setModelScale(Math.max(0.1, Math.min(1, settings.live2DSettings.scale * 0.55)))
      }
      setBridgeUrl(resolveBridgeWsUrl(getServerUrl(), settings.sessionStage?.bridgeUrl))
    } catch (error) {
      console.error('Failed to parse settings for session stage', error)
      modelPathsInput.value = getModelPaths().join(', ')
    }
  }

  async function loadLive2DModelCatalog(): Promise<void> {
    modelsLoading.value = true
    modelsError.value = ''
    try {
      const resp = await fetchLive2DModels(getServerUrl())
      live2dModels.value = resp.models || []
      modelSemanticMotionsByPath.clear()
      for (const item of live2dModels.value) {
        const mapping = item.motion_semantic_map
        if (mapping && typeof mapping === 'object') {
          modelSemanticMotionsByPath.set(item.model_path, mapping)
        }
      }
      modelsRootDir.value = resp.root_dir || ''
      if (resp.error) modelsError.value = resp.error
    } catch (error) {
      console.error(error)
      modelsError.value = '讀取模型清單失敗，請確認後端已啟動。'
      live2dModels.value = []
      modelSemanticMotionsByPath.clear()
    } finally {
      modelsLoading.value = false
    }
  }

  async function loadModelMotionsForPath(modelPath: string): Promise<void> {
    if (modelMotionsByPath.has(modelPath)) return
    try {
      const resp = await fetch(modelPath)
      const data = await resp.json()
      const motions = data?.FileReferences?.Motions || {}
      const parsed: ModelMotionEntry[] = []
      for (const [group, rawItems] of Object.entries(motions)) {
        if (!Array.isArray(rawItems)) continue
        rawItems.forEach((item, index) => {
          const filePath = typeof item?.File === 'string' ? item.File : ''
          const fileName = filePath
            .split('/')
            .pop()
            ?.replace(/\.motion3\.json$/i, '')
            ?.replace(/\.mtn$/i, '') || ''
          const motionName = fileName || String(group || '')
          parsed.push({
            group: String(group || ''),
            index,
            name: motionName,
            key: `${String(group || '')}#${index}`,
          })
        })
      }
      modelMotionsByPath.set(modelPath, parsed)
    } catch (error) {
      console.error('Failed to load model motions', modelPath, error)
      modelMotionsByPath.set(modelPath, [])
    }
  }

  function applyModelPaths(nextPaths: string[]): void {
    const normalized = normalizeModelPaths(nextPaths, getModelPaths())
    setModelPaths(normalized)
    modelPathsInput.value = normalized.join(', ')
    saveSessionModelPathsToStorage(normalized)
    for (const path of normalized) {
      loadModelMotionsForPath(path).catch((error) => {
        console.error('Failed to preload motions for model path', path, error)
      })
    }
    for (const [sessionId, assignedPath] of sessionModelAssignments.entries()) {
      if (!normalized.includes(assignedPath)) {
        sessionModelAssignments.delete(sessionId)
      }
    }
  }

  function applyModelPathsInput(): void {
    const next = normalizeModelPaths(modelPathsInput.value, getModelPaths())
    applyModelPaths(next)
  }

  function isModelSelected(modelPath: string): boolean {
    return getModelPaths().includes(modelPath)
  }

  function toggleModel(modelPath: string): void {
    const current = getModelPaths()
    if (current.includes(modelPath)) {
      const next = current.filter((x) => x !== modelPath)
      if (next.length === 0) return
      applyModelPaths(next)
      return
    }
    applyModelPaths([...current, modelPath])
  }

  function toAssetUrl(value: string): string {
    if (!value) return ''
    if (value.startsWith('http://') || value.startsWith('https://') || value.startsWith('/')) {
      return value
    }
    return `/${value}`
  }

  return {
    loadSettings,
    loadLive2DModelCatalog,
    loadModelMotionsForPath,
    applyModelPathsInput,
    isModelSelected,
    toggleModel,
    toAssetUrl,
  }
}
