import * as THREE from 'three'

/** 互動點上可執行的動作定義 */
export interface InteractionAction {
  type: string
  enterVrma?: string
  loopVrma: string
  exitVrma?: string
  seatOffset?: { x: number; y: number; z: number }
}

/** 場景中一個可互動的點（持久化用，不含 runtime 狀態） */
export interface InteractionPointData {
  id: string
  label: string
  position: { x: number; y?: number; z: number }
  approachPosition: { x: number; y?: number; z: number }
  approachRotationY: number
  action: InteractionAction
  capacity: number
}

/** 場景中一個可互動的點（含 runtime 狀態） */
export interface InteractionPoint extends InteractionPointData {
  occupiedBy: string[]
}

interface DetectionRule {
  keywords: string[]
  type: string
  defaultAction: InteractionAction
  approachOffset: number
}

const DETECTION_RULES: DetectionRule[] = [
  {
    keywords: ['chair', 'seat', 'stool'],
    type: 'sit',
    defaultAction: {
      type: 'sit',
      loopVrma: 'SittingDrinking.vrma',
      seatOffset: { x: 0, y: 0, z: 0 },
    },
    approachOffset: 0.4,
  },
  {
    keywords: ['desk', 'table'],
    type: 'work',
    defaultAction: {
      type: 'work',
      loopVrma: 'Thinking.vrma',
    },
    approachOffset: 0.5,
  },
  {
    keywords: ['bench'],
    type: 'sit',
    defaultAction: {
      type: 'sit',
      loopVrma: 'SittingDrinking.vrma',
    },
    approachOffset: 0.4,
  },
]

const STORAGE_VERSION = 1

interface StoredInteractionPoints {
  version: number
  points: InteractionPointData[]
}

export function createVrmInteractionPointUtils(args: {
  storageKey: string
  getStageWorldCenter: () => THREE.Vector3 | null
  getStageWorldSize: () => THREE.Vector3 | null
}) {
  const { storageKey, getStageWorldCenter, getStageWorldSize } = args

  const interactionPoints: InteractionPoint[] = []

  function getInteractionPoints(): InteractionPoint[] {
    return interactionPoints
  }

  function getPointById(id: string): InteractionPoint | null {
    return interactionPoints.find((p) => p.id === id) || null
  }

  function isPointAvailable(pointId: string): boolean {
    const point = getPointById(pointId)
    if (!point) return false
    return point.occupiedBy.length < point.capacity
  }

  function findAvailablePoint(type?: string): InteractionPoint | null {
    for (const point of interactionPoints) {
      if (type && point.action.type !== type) continue
      if (point.occupiedBy.length < point.capacity) return point
    }
    return null
  }

  function findNearestAvailablePoint(
    position: { x: number; z: number },
    type?: string,
  ): InteractionPoint | null {
    let best: InteractionPoint | null = null
    let bestDistSq = Infinity
    for (const point of interactionPoints) {
      if (type && point.action.type !== type) continue
      if (point.occupiedBy.length >= point.capacity) continue
      const dx = point.approachPosition.x - position.x
      const dz = point.approachPosition.z - position.z
      const distSq = dx * dx + dz * dz
      if (distSq < bestDistSq) {
        bestDistSq = distSq
        best = point
      }
    }
    return best
  }

  function occupyPoint(pointId: string, sessionId: string): boolean {
    const point = getPointById(pointId)
    if (!point) return false
    if (point.occupiedBy.includes(sessionId)) return true
    if (point.occupiedBy.length >= point.capacity) return false
    point.occupiedBy.push(sessionId)
    return true
  }

  function releasePoint(pointId: string, sessionId: string): void {
    const point = getPointById(pointId)
    if (!point) return
    const idx = point.occupiedBy.indexOf(sessionId)
    if (idx >= 0) point.occupiedBy.splice(idx, 1)
  }

  function releaseAllBySession(sessionId: string): void {
    for (const point of interactionPoints) {
      const idx = point.occupiedBy.indexOf(sessionId)
      if (idx >= 0) point.occupiedBy.splice(idx, 1)
    }
  }

  function addManualPoint(data: InteractionPointData): void {
    const existing = getPointById(data.id)
    if (existing) {
      Object.assign(existing, data)
      return
    }
    interactionPoints.push({ ...data, occupiedBy: [] })
  }

  function removePoint(pointId: string): void {
    const idx = interactionPoints.findIndex((p) => p.id === pointId)
    if (idx >= 0) interactionPoints.splice(idx, 1)
  }

  function clearAll(): void {
    interactionPoints.length = 0
  }

  function matchDetectionRule(name: string): DetectionRule | null {
    const lower = name.toLowerCase()
    for (const rule of DETECTION_RULES) {
      for (const keyword of rule.keywords) {
        if (lower.includes(keyword)) return rule
      }
    }
    return null
  }

  function computeApproachPosition(
    center: { x: number; y?: number; z: number },
    meshForward: THREE.Vector3,
    offset: number,
  ): { x: number; y?: number; z: number } {
    return {
      x: center.x + meshForward.x * offset,
      y: center.y ?? 0,
      z: center.z + meshForward.z * offset,
    }
  }

  /** 檢查是否已有太近的同類型互動點（去重用） */
  function hasTooClosePoint(pos: { x: number; z: number }, type: string, threshold = 0.3): boolean {
    for (const p of interactionPoints) {
      if (p.action.type !== type) continue
      const dx = p.position.x - pos.x
      const dz = p.position.z - pos.z
      if (dx * dx + dz * dz < threshold * threshold) return true
    }
    return false
  }

  function extractFromScene(sceneRoot: THREE.Object3D): void {
    const stageSize = getStageWorldSize() || new THREE.Vector3(12, 6, 12)
    const box = new THREE.Box3()
    const size = new THREE.Vector3()
    const center = new THREE.Vector3()
    let autoIndex = 0

    // 第一遍：收集「最具體」的候選節點（偏好有直接 mesh 子節點的群組 or mesh 自身）
    const candidates: Array<{ node: THREE.Object3D; rule: DetectionRule }> = []
    const skipNodes = new Set<THREE.Object3D>()

    sceneRoot.updateMatrixWorld(true)
    sceneRoot.traverse((node: THREE.Object3D) => {
      const rule = matchDetectionRule(node.name)
      if (!rule) return

      // 如果 node 有子節點也匹配同一規則，說明 node 是容器群組（如 STUDENT_CHAIRS），跳過
      let hasMatchingChild = false
      for (const child of node.children) {
        if (matchDetectionRule(child.name)) { hasMatchingChild = true; break }
      }
      if (hasMatchingChild) {
        skipNodes.add(node)
        return
      }

      // 如果已經被 skip 的祖先下面的節點，也跳過
      let ancestor: THREE.Object3D | null = node.parent
      while (ancestor) {
        if (candidates.some(c => c.node === ancestor && c.rule.type === rule.type)) return
        ancestor = ancestor.parent
      }

      candidates.push({ node, rule })
    })

    // 第二遍：從候選中生成互動點，去重
    for (const { node, rule } of candidates) {
      const mesh = node as THREE.Mesh
      if (mesh.isMesh) {
        box.setFromObject(mesh)
      } else {
        box.setFromObject(node)
      }
      if (!Number.isFinite(box.min.x) || !Number.isFinite(box.max.x)) continue
      box.getSize(size)
      box.getCenter(center)

      // 過濾過大的群組
      if (size.x > stageSize.x * 0.5 || size.z > stageSize.z * 0.5) continue

      // 去重：跳過位置太近的同類型互動點
      if (hasTooClosePoint({ x: center.x, z: center.z }, rule.type)) continue

      const id = `auto-${rule.type}-${autoIndex++}`
      const label = `${node.name || rule.type}-${autoIndex}`

      const forward = new THREE.Vector3(0, 0, 1)
      node.getWorldQuaternion(new THREE.Quaternion()).normalize()
      const approachPos = computeApproachPosition(
        { x: center.x, z: center.z },
        forward,
        rule.approachOffset,
      )

      const approachRotY = Math.atan2(-forward.x, -forward.z)

      interactionPoints.push({
        id,
        label,
        position: { x: center.x, y: center.y, z: center.z },
        approachPosition: approachPos,
        approachRotationY: approachRotY,
        action: { ...rule.defaultAction },
        capacity: 1,
        occupiedBy: [],
      })
    }
  }

  function toStoragePayload(): StoredInteractionPoints {
    return {
      version: STORAGE_VERSION,
      points: interactionPoints.map((p) => ({
        id: p.id,
        label: p.label,
        position: { ...p.position },
        approachPosition: { ...p.approachPosition },
        approachRotationY: p.approachRotationY,
        action: { ...p.action },
        capacity: p.capacity,
      })),
    }
  }

  function saveToStorage(): void {
    try {
      localStorage.setItem(storageKey, JSON.stringify(toStoragePayload()))
    } catch (error) {
      console.warn('儲存互動點失敗:', error)
    }
  }

  function loadFromStorage(): boolean {
    try {
      const raw = localStorage.getItem(storageKey)
      if (!raw) return false
      const parsed = JSON.parse(raw) as StoredInteractionPoints
      if (!parsed || parsed.version !== STORAGE_VERSION || !Array.isArray(parsed.points)) return false

      interactionPoints.length = 0
      for (const p of parsed.points) {
        if (!p.id || !p.position || !p.approachPosition || !p.action) continue
        const x = Number(p.position.x)
        const y = Number(p.position.y ?? 0)
        const z = Number(p.position.z)
        const ax = Number(p.approachPosition.x)
        const ay = Number(p.approachPosition.y ?? 0)
        const az = Number(p.approachPosition.z)
        if ([x, y, z, ax, ay, az].some((v) => !Number.isFinite(v))) continue
        interactionPoints.push({
          id: p.id,
          label: p.label || p.id,
          position: { x, y, z },
          approachPosition: { x: ax, y: ay, z: az },
          approachRotationY: Number(p.approachRotationY) || 0,
          action: {
            type: p.action.type || 'sit',
            enterVrma: p.action.enterVrma,
            loopVrma: p.action.loopVrma || 'SittingDrinking.vrma',
            exitVrma: p.action.exitVrma,
            seatOffset: p.action.seatOffset ? { ...p.action.seatOffset } : undefined,
          },
          capacity: Math.max(1, Number(p.capacity) || 1),
          occupiedBy: [],
        })
      }
      return interactionPoints.length > 0
    } catch (error) {
      console.warn('讀取互動點失敗:', error)
      return false
    }
  }

  return {
    getInteractionPoints,
    getPointById,
    isPointAvailable,
    findAvailablePoint,
    findNearestAvailablePoint,
    occupyPoint,
    releasePoint,
    releaseAllBySession,
    addManualPoint,
    removePoint,
    clearAll,
    extractFromScene,
    saveToStorage,
    loadFromStorage,
  }
}
