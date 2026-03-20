import { type Ref } from 'vue'
import * as THREE from 'three'

interface RoutePoint {
  x: number
  z: number
}

export interface StoredCameraView {
  position: { x: number; y: number; z: number }
  target: { x: number; y: number; z: number }
}

export function createVrmRouteCameraUtils(args: {
  routeEditorHintText: Ref<string>
  activeRouteModelIndex: Ref<number>
  routeEditorEnabled: Ref<boolean>
  routePointsByModel: Map<string, RoutePoint[]>
  stageFixedVrmUrls: string[]
  routeStorageKey: string
  cameraViewStorageKey: string
  cameraViewSaveDebounceMs: number
  getCamera: () => THREE.PerspectiveCamera | null
  getControlsTarget: () => THREE.Vector3 | null
  getScene: () => THREE.Scene | null
  getRenderer: () => THREE.WebGLRenderer | null
  getRouteLine: () => THREE.Line | null
  setRouteLine: (line: THREE.Line | null) => void
  getRouteMarkers: () => THREE.Mesh[]
  pointer: THREE.Vector2
  raycaster: THREE.Raycaster
  groundPlane: THREE.Plane
  rayHitPoint: THREE.Vector3
  resolveToWalkablePosition: (desired: THREE.Vector3) => THREE.Vector3
  assignRouteIndexForActor: (actor: { modelUrl: string; routePointIndex: number; roamTarget: THREE.Vector3 }) => void
  getActors: () => Array<{ modelUrl: string; routePointIndex: number; roamTarget: THREE.Vector3 }>
  applyAllActorBehaviors: (forceMotion?: boolean) => Promise<void>
  setLoadingText: (text: string) => void
  hasCustomRoute: (modelUrl?: string) => boolean
  getCameraViewSaveTimer: () => number | null
  setCameraViewSaveTimer: (timer: number | null) => void
}) {
  const {
    routeEditorHintText,
    activeRouteModelIndex,
    routeEditorEnabled,
    routePointsByModel,
    stageFixedVrmUrls,
    routeStorageKey,
    cameraViewStorageKey,
    cameraViewSaveDebounceMs,
    getCamera,
    getControlsTarget,
    getScene,
    getRenderer,
    getRouteLine,
    setRouteLine,
    getRouteMarkers,
    pointer,
    raycaster,
    groundPlane,
    rayHitPoint,
    resolveToWalkablePosition,
    assignRouteIndexForActor,
    getActors,
    applyAllActorBehaviors,
    setLoadingText,
    hasCustomRoute,
    getCameraViewSaveTimer,
    setCameraViewSaveTimer,
  } = args

  function getRouteModelUrlByIndex(index: number): string {
    const normalized = ((index % stageFixedVrmUrls.length) + stageFixedVrmUrls.length) % stageFixedVrmUrls.length
    return stageFixedVrmUrls[normalized] || stageFixedVrmUrls[0] || ''
  }

  function getActiveRouteModelUrl(): string {
    return getRouteModelUrlByIndex(activeRouteModelIndex.value)
  }

  function getRoutePointsByModel(modelUrl: string): RoutePoint[] {
    return routePointsByModel.get(modelUrl) || []
  }

  function getModelRouteLabel(modelUrl: string): string {
    const fileName = modelUrl.split('/').pop() || modelUrl
    return fileName.replace(/\.vrm$/i, '')
  }

  function getActiveRoutePointCount(): number {
    return getRoutePointsByModel(getActiveRouteModelUrl()).length
  }

  function updateRouteEditorHintText(): void {
    const modelUrl = getActiveRouteModelUrl()
    const count = getRoutePointsByModel(modelUrl).length
    routeEditorHintText.value = `快捷鍵: R 編輯/退出, 1-4 切路線, 點地面加點, Backspace 復原, C 清空, Enter 儲存, Esc 退出 | 目前路線: ${getModelRouteLabel(modelUrl)} (${count} 點)`
  }

  function saveCameraViewToStorage(): void {
    const camera = getCamera()
    const target = getControlsTarget()
    if (!camera || !target) return
    const payload: StoredCameraView = {
      position: { x: camera.position.x, y: camera.position.y, z: camera.position.z },
      target: { x: target.x, y: target.y, z: target.z },
    }
    try {
      localStorage.setItem(cameraViewStorageKey, JSON.stringify(payload))
    } catch (error) {
      console.warn('儲存鏡頭視角失敗:', error)
    }
  }

  function scheduleSaveCameraView(): void {
    const existing = getCameraViewSaveTimer()
    if (existing !== null) {
      window.clearTimeout(existing)
      setCameraViewSaveTimer(null)
    }
    const timer = window.setTimeout(() => {
      setCameraViewSaveTimer(null)
      saveCameraViewToStorage()
    }, cameraViewSaveDebounceMs)
    setCameraViewSaveTimer(timer)
  }

  function loadCameraViewFromStorage(): StoredCameraView | null {
    try {
      const raw = localStorage.getItem(cameraViewStorageKey)
      if (!raw) return null
      const parsed = JSON.parse(raw) as StoredCameraView
      const p = parsed?.position
      const t = parsed?.target
      const values = [p?.x, p?.y, p?.z, t?.x, t?.y, t?.z].map((v) => Number(v))
      if (values.some((v) => !Number.isFinite(v))) return null
      return {
        position: { x: values[0], y: values[1], z: values[2] },
        target: { x: values[3], y: values[4], z: values[5] },
      }
    } catch (error) {
      console.warn('讀取鏡頭視角失敗:', error)
      return null
    }
  }

  function saveRouteToStorage(): void {
    try {
      const payload: Record<string, RoutePoint[]> = {}
      for (const modelUrl of stageFixedVrmUrls) {
        payload[modelUrl] = getRoutePointsByModel(modelUrl)
      }
      localStorage.setItem(routeStorageKey, JSON.stringify({ routes_by_model: payload }))
    } catch (error) {
      console.warn('儲存自訂路線失敗:', error)
    }
  }

  function loadRouteFromStorage(): void {
    routePointsByModel.clear()
    for (const modelUrl of stageFixedVrmUrls) {
      routePointsByModel.set(modelUrl, [])
    }
    try {
      const raw = localStorage.getItem(routeStorageKey)
      if (!raw) {
        updateRouteEditorHintText()
        return
      }
      const parsed = JSON.parse(raw) as {
        points?: Array<{ x?: unknown; z?: unknown }>
        routes_by_model?: Record<string, Array<{ x?: unknown; z?: unknown }>>
      }
      const legacyPoints = Array.isArray(parsed?.points) ? parsed.points : null
      if (legacyPoints) {
        const normalized: RoutePoint[] = []
        for (const point of legacyPoints) {
          const x = Number(point?.x)
          const z = Number(point?.z)
          if (!Number.isFinite(x) || !Number.isFinite(z)) continue
          normalized.push({ x, z })
        }
        for (const modelUrl of stageFixedVrmUrls) {
          routePointsByModel.set(modelUrl, normalized.map((p) => ({ ...p })))
        }
      }
      const routesByModel = parsed?.routes_by_model || {}
      for (const modelUrl of stageFixedVrmUrls) {
        const list = Array.isArray(routesByModel[modelUrl]) ? routesByModel[modelUrl] : []
        const normalized: RoutePoint[] = []
        for (const point of list) {
          const x = Number(point?.x)
          const z = Number(point?.z)
          if (!Number.isFinite(x) || !Number.isFinite(z)) continue
          normalized.push({ x, z })
        }
        routePointsByModel.set(modelUrl, normalized)
      }
    } catch (error) {
      console.warn('讀取自訂路線失敗:', error)
    }
    updateRouteEditorHintText()
  }

  function clearRouteVisuals(): void {
    const scene = getScene()
    if (!scene) return
    const routeLine = getRouteLine()
    if (routeLine) {
      scene.remove(routeLine)
      routeLine.geometry.dispose()
      ;(routeLine.material as THREE.Material).dispose()
      setRouteLine(null)
    }
    const routeMarkers = getRouteMarkers()
    while (routeMarkers.length) {
      const marker = routeMarkers.pop()
      if (!marker) continue
      scene.remove(marker)
      marker.geometry.dispose()
      ;(marker.material as THREE.Material).dispose()
    }
  }

  function refreshRouteVisuals(): void {
    const scene = getScene()
    if (!scene) return
    clearRouteVisuals()
    const routePoints = getRoutePointsByModel(getActiveRouteModelUrl())
    if (routePoints.length === 0) return

    const routeMarkers = getRouteMarkers()
    const markerGeometry = new THREE.SphereGeometry(0.055, 12, 10)
    routePoints.forEach((point, idx) => {
      const markerMaterial = new THREE.MeshBasicMaterial({ color: idx === 0 ? 0x51e2c2 : 0xffd36e })
      const marker = new THREE.Mesh(markerGeometry.clone(), markerMaterial)
      marker.position.set(point.x, 0.03, point.z)
      scene.add(marker)
      routeMarkers.push(marker)
    })

    if (routePoints.length >= 2) {
      const points3d = routePoints.map((point) => new THREE.Vector3(point.x, 0.03, point.z))
      const lineGeometry = new THREE.BufferGeometry().setFromPoints(points3d)
      const lineMaterial = new THREE.LineBasicMaterial({ color: 0x7ec5ff })
      const routeLine = new THREE.Line(lineGeometry, lineMaterial)
      scene.add(routeLine)
      setRouteLine(routeLine)
    }
  }

  function pickGroundPoint(clientX: number, clientY: number): THREE.Vector3 | null {
    const renderer = getRenderer()
    const camera = getCamera()
    if (!renderer || !camera) return null
    const rect = renderer.domElement.getBoundingClientRect()
    const x = ((clientX - rect.left) / rect.width) * 2 - 1
    const y = -((clientY - rect.top) / rect.height) * 2 + 1
    pointer.set(x, y)
    raycaster.setFromCamera(pointer, camera)
    const point = raycaster.ray.intersectPlane(groundPlane, rayHitPoint)
    if (!point) return null
    return point.clone()
  }

  function getActorRoutePoint(actor: { modelUrl: string }, index: number): THREE.Vector3 {
    const routePoints = getRoutePointsByModel(actor.modelUrl)
    const point = routePoints[index]
    return new THREE.Vector3(point.x, 0, point.z)
  }

  function addRoutePointFromPointer(clientX: number, clientY: number): void {
    const point = pickGroundPoint(clientX, clientY)
    if (!point) return
    const routePoint = resolveToWalkablePosition(new THREE.Vector3(point.x, 0, point.z))
    const routePoints = getRoutePointsByModel(getActiveRouteModelUrl())
    routePoints.push({ x: routePoint.x, z: routePoint.z })
    refreshRouteVisuals()
    updateRouteEditorHintText()
    setLoadingText(`路線編輯中：${getModelRouteLabel(getActiveRouteModelUrl())} 已新增 ${routePoints.length} 個錨點`)
  }

  function removeLastRoutePoint(): void {
    const routePoints = getRoutePointsByModel(getActiveRouteModelUrl())
    if (!routePoints.length) return
    routePoints.pop()
    refreshRouteVisuals()
    updateRouteEditorHintText()
  }

  function clearRoutePoints(): void {
    const routePoints = getRoutePointsByModel(getActiveRouteModelUrl())
    routePoints.length = 0
    refreshRouteVisuals()
    updateRouteEditorHintText()
  }

  function applyRouteToAllActors(): void {
    for (const actor of getActors()) {
      assignRouteIndexForActor(actor)
    }
    void applyAllActorBehaviors(true)
  }

  function toggleRouteEditor(next?: boolean): void {
    routeEditorEnabled.value = typeof next === 'boolean' ? next : !routeEditorEnabled.value
    updateRouteEditorHintText()
    if (routeEditorEnabled.value) {
      setLoadingText(`路線編輯模式：${getModelRouteLabel(getActiveRouteModelUrl())}（目前 ${getActiveRoutePointCount()} 點）`)
    } else {
      setLoadingText(getActors().length ? '' : '等待 Session 資料...')
    }
  }

  return {
    saveCameraViewToStorage,
    scheduleSaveCameraView,
    loadCameraViewFromStorage,
    getActiveRouteModelUrl,
    getRoutePointsByModel,
    getActiveRoutePointCount,
    getModelRouteLabel,
    updateRouteEditorHintText,
    saveRouteToStorage,
    loadRouteFromStorage,
    clearRouteVisuals,
    refreshRouteVisuals,
    getActorRoutePoint,
    addRoutePointFromPointer,
    removeLastRoutePoint,
    clearRoutePoints,
    applyRouteToAllActors,
    toggleRouteEditor,
  }
}
