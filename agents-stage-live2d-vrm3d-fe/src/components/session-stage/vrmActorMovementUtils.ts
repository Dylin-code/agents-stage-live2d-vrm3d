import * as THREE from 'three'

export interface VrmActorMovementShape {
  sessionId: string
  modelUrl: string
  root: THREE.Group
  targetPosition: THREE.Vector3
}

export interface StageObstacle {
  minX: number
  maxX: number
  minZ: number
  maxZ: number
}

export function createVrmActorMovementUtils(args: {
  getStageSeatCenter: () => THREE.Vector3 | null
  getStageWorldCenter: () => THREE.Vector3 | null
  getStageWorldSize: () => THREE.Vector3 | null
  getStageObstacles: () => StageObstacle[]
  getActors: () => VrmActorMovementShape[]
  actorSeatIndexes: Map<string, number>
  getSelectedSessionId: () => string
  getCenterBlockRatios: () => { xRatio: number; zRatio: number }
  actorCollisionRadius: number
  roamTurnDamping: number
  jumpVisualHeight: number
  roamTargetQuaternion: THREE.Quaternion
  roamTargetEuler: THREE.Euler
  getApproachWhitelist?: () => Array<{ x: number; z: number }>
}) {
  const {
    getStageSeatCenter,
    getStageWorldCenter,
    getStageWorldSize,
    getStageObstacles,
    getActors,
    actorSeatIndexes,
    getSelectedSessionId,
    getCenterBlockRatios,
    actorCollisionRadius,
    roamTurnDamping,
    jumpVisualHeight,
    roamTargetQuaternion,
    roamTargetEuler,
    getApproachWhitelist,
  } = args

  function getSeatPositions(): THREE.Vector3[] {
    const stageWorldCenter = getStageWorldCenter()
    const stageWorldSize = getStageWorldSize()
    if (stageWorldCenter && stageWorldSize) {
      const center = stageWorldCenter
      const size = stageWorldSize
      const xSpan = Math.max(1.3, Math.min(2.6, size.x * 0.25))
      const zFront = Math.max(1.0, Math.min(2.4, size.z * 0.34))
      const zBack = Math.max(0.8, Math.min(1.9, size.z * 0.2))
      return [
        new THREE.Vector3(center.x - xSpan * 0.95, 0, center.z + zFront),
        new THREE.Vector3(center.x - xSpan * 0.35, 0, center.z + zBack),
        new THREE.Vector3(center.x + xSpan * 0.35, 0, center.z + zBack),
        new THREE.Vector3(center.x + xSpan * 0.95, 0, center.z + zFront),
      ]
    }
    return [
      new THREE.Vector3(-2.15, 0, 0.65),
      new THREE.Vector3(-1.05, 0, 0.15),
      new THREE.Vector3(0, 0, -0.08),
      new THREE.Vector3(1.05, 0, 0.15),
      new THREE.Vector3(2.15, 0, 0.65),
    ]
  }

  function getAvailableSeatIndexes(): number[] {
    const seats = getSeatPositions()
    const allIndexes = seats.map((_, idx) => idx)
    const used = new Set(actorSeatIndexes.values())
    return allIndexes.filter((idx) => !used.has(idx))
  }

  function pickRandom<T>(items: T[]): T | null {
    if (!items.length) return null
    return items[Math.floor(Math.random() * items.length)] || null
  }

  function randomRange(min: number, max: number): number {
    return min + Math.random() * (max - min)
  }

  function isFocusedActor(actor: { sessionId: string }): boolean {
    return getSelectedSessionId() === actor.sessionId
  }

  function getRoamBounds() {
    const stageSeatCenter = getStageSeatCenter()
    const stageWorldCenter = getStageWorldCenter()
    const stageWorldSize = getStageWorldSize()
    const center = stageSeatCenter || stageWorldCenter || new THREE.Vector3(0, 0, 0)
    const size = stageWorldSize || new THREE.Vector3(12, 6, 12)
    const xRange = Math.max(1.1, Math.min(2.8, size.x * 0.22))
    const zFront = Math.max(0.7, Math.min(2.2, size.z * 0.22))
    const zBack = Math.max(0.35, Math.min(1.1, size.z * 0.1))
    return { y: 0, minX: center.x - xRange, maxX: center.x + xRange, minZ: center.z - zBack, maxZ: center.z + zFront }
  }

  function isWithinRoamBounds(position: THREE.Vector3, margin = actorCollisionRadius): boolean {
    const bounds = getRoamBounds()
    return position.x >= bounds.minX + margin && position.x <= bounds.maxX - margin && position.z >= bounds.minZ + margin && position.z <= bounds.maxZ - margin
  }

  function collidesWithStageObstacle(position: THREE.Vector3): boolean {
    for (const obstacle of getStageObstacles()) {
      if (position.x >= obstacle.minX && position.x <= obstacle.maxX && position.z >= obstacle.minZ && position.z <= obstacle.maxZ) return true
    }
    return false
  }

  function collidesWithCenterBlock(position: THREE.Vector3): boolean {
    const stageWorldCenter = getStageWorldCenter()
    const stageWorldSize = getStageWorldSize()
    if (!stageWorldCenter || !stageWorldSize) return false
    const ratios = getCenterBlockRatios()
    const halfX = stageWorldSize.x * ratios.xRatio
    const halfZ = stageWorldSize.z * ratios.zRatio
    return (
      position.x >= stageWorldCenter.x - halfX
      && position.x <= stageWorldCenter.x + halfX
      && position.z >= stageWorldCenter.z - halfZ
      && position.z <= stageWorldCenter.z + halfZ
    )
  }

  function collidesWithOtherActors(position: THREE.Vector3, selfActor?: VrmActorMovementShape): boolean {
    const minDistance = actorCollisionRadius * 2
    const minDistanceSq = minDistance * minDistance
    for (const actor of getActors()) {
      if (selfActor && actor === selfActor) continue
      if (position.distanceToSquared(actor.targetPosition) < minDistanceSq) return true
    }
    return false
  }

  function isNearApproachWhitelist(position: THREE.Vector3): boolean {
    if (!getApproachWhitelist) return false
    const whitelist = getApproachWhitelist()
    const threshold = actorCollisionRadius * 3
    const thresholdSq = threshold * threshold
    for (const wp of whitelist) {
      const dx = position.x - wp.x
      const dz = position.z - wp.z
      if (dx * dx + dz * dz < thresholdSq) return true
    }
    return false
  }

  function isWalkablePosition(position: THREE.Vector3, selfActor?: VrmActorMovementShape): boolean {
    if (!isWithinRoamBounds(position)) return false
    if (collidesWithCenterBlock(position)) return false
    // 如果位置靠近互動點的 approach 位置，允許穿過障礙物（椅子前方可能在碰撞框內）
    if (collidesWithStageObstacle(position) && !isNearApproachWhitelist(position)) return false
    if (collidesWithOtherActors(position, selfActor)) return false
    return true
  }

  function resolveToWalkablePosition(desired: THREE.Vector3, selfActor?: VrmActorMovementShape): THREE.Vector3 {
    if (isWalkablePosition(desired, selfActor)) return desired.clone()
    const origin = desired.clone()
    const test = new THREE.Vector3()
    for (let radius = 0.1; radius <= 1.4; radius += 0.1) {
      for (let angle = 0; angle < Math.PI * 2; angle += Math.PI / 8) {
        test.set(origin.x + Math.cos(angle) * radius, origin.y, origin.z + Math.sin(angle) * radius)
        if (isWalkablePosition(test, selfActor)) return test.clone()
      }
    }
    return origin
  }

  function pickRoamTarget(currentPosition: THREE.Vector3, selfActor?: VrmActorMovementShape): THREE.Vector3 {
    const bounds = getRoamBounds()
    const target = new THREE.Vector3()
    let attempts = 0
    do {
      target.set(randomRange(bounds.minX, bounds.maxX), bounds.y, randomRange(bounds.minZ, bounds.maxZ))
      attempts += 1
    } while (attempts < 28 && (target.distanceToSquared(currentPosition) < 0.65 || !isWalkablePosition(target, selfActor)))
    if (!isWalkablePosition(target, selfActor)) return resolveToWalkablePosition(currentPosition, selfActor)
    return target
  }

  function lockActorRootMotion(actor: { vrm: any; modelBaseLocalPosition: THREE.Vector3; jumpLocked: boolean; hipsBone: THREE.Object3D | null; hipsBaseLocalPosition: THREE.Vector3 | null }): void {
    actor.vrm.scene.position.copy(actor.modelBaseLocalPosition)
    if (!actor.jumpLocked && actor.hipsBone && actor.hipsBaseLocalPosition) {
      actor.hipsBone.position.copy(actor.hipsBaseLocalPosition)
    }
  }

  function getJumpLift(actor: { jumpLocked: boolean; jumpDuration: number; jumpStartedAt: number }, now: number): number {
    if (!actor.jumpLocked || actor.jumpDuration <= 0) return 0
    const elapsed = Math.max(0, now - actor.jumpStartedAt)
    const progress = Math.max(0, Math.min(1, elapsed / actor.jumpDuration))
    return Math.sin(progress * Math.PI) * jumpVisualHeight
  }

  function faceActorTowardDirection(actor: { root: THREE.Group }, direction: THREE.Vector3, delta: number): void {
    if (direction.lengthSq() < 1e-6) return
    roamTargetEuler.set(0, Math.atan2(direction.x, direction.z), 0)
    roamTargetQuaternion.setFromEuler(roamTargetEuler)
    const turnLerp = 1 - Math.exp(-roamTurnDamping * Math.max(delta, 0.001))
    actor.root.quaternion.slerp(roamTargetQuaternion, Math.min(1, turnLerp))
  }

  return {
    getSeatPositions,
    getAvailableSeatIndexes,
    pickRandom,
    randomRange,
    isFocusedActor,
    isWalkablePosition,
    resolveToWalkablePosition,
    pickRoamTarget,
    lockActorRootMotion,
    getJumpLift,
    faceActorTowardDirection,
  }
}
