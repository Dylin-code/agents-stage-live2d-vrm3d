import * as THREE from 'three'
import type { InteractionPoint } from './vrmInteractionPointUtils'

/** 行為步驟類型 */
export type BehaviorStepType = 'moveTo' | 'interact' | 'wait' | 'playMotion' | 'roam'

/** 單一行為步驟 */
export interface BehaviorStep {
  type: BehaviorStepType
  target?: { x: number; z: number }
  interactionPointId?: string
  interactionPhase?: 'approach' | 'enter' | 'loop' | 'exit'
  duration?: number
  vrmaFile?: string
  motionLoop?: 'repeat' | 'once'
  motionOffset?: { x: number; y: number; z: number }
  motionRotationY?: number
  /** 移動時忽略障礙物碰撞 */
  skipObstacles?: boolean
  /** interact 動畫覆寫（優先於互動點預設動畫） */
  interactEnterVrma?: string
  interactLoopVrma?: string
  interactExitVrma?: string
  /** 互動方向覆寫（角度）；留空用互動點預設 */
  interactRotationYOverride?: number
  /** 互動高度校正（世界座標 Y 偏移） */
  interactOffsetY?: number
  /** 互動 Z 座標校正（世界座標偏移） */
  interactOffsetZ?: number
}

/** 每個角色的行為狀態 */
export interface ActorBehavior {
  steps: BehaviorStep[]
  currentIndex: number
  stepStartedAt: number
  interruptible: boolean
  onComplete?: 'loop' | 'roam' | 'idle'
  motionApplied?: boolean
  /** 卡點偵測：上次記錄的距離目標距離 */
  _lastDistToTarget?: number
  /** 卡點偵測：上次有明顯進展的時間 */
  _lastProgressAt?: number
}

/** Scheduler 需要的 actor 最小介面 */
export interface BehaviorActorShape {
  sessionId: string
  modelUrl: string
  targetPosition: THREE.Vector3
  roamTarget: THREE.Vector3
  roamSpeed: number
  root: THREE.Group
  behavior: ActorBehavior | null
  occupyingPointId: string | null
  routePointIndex: number
}

export function createVrmBehaviorScheduler(args: {
  pickRoamTarget: (currentPosition: THREE.Vector3, selfActor?: BehaviorActorShape) => THREE.Vector3
  resolveToWalkablePosition: (desired: THREE.Vector3, selfActor?: BehaviorActorShape) => THREE.Vector3
  isWalkablePosition: (position: THREE.Vector3, selfActor?: BehaviorActorShape) => boolean
  faceActorTowardDirection: (actor: BehaviorActorShape, direction: THREE.Vector3, delta: number) => void
  playActorMotion: (actor: BehaviorActorShape, fileName: string, options?: { force?: boolean; loop?: 'repeat' | 'once' }) => Promise<any>
  getPointById: (id: string) => InteractionPoint | null
  occupyPoint: (pointId: string, sessionId: string) => boolean
  releasePoint: (pointId: string, sessionId: string) => void
  setActorTargetPosition: (
    actor: BehaviorActorShape,
    position: THREE.Vector3,
    syncRoot?: boolean,
    preserveInputY?: boolean,
  ) => void
  setActorRoamTarget: (actor: BehaviorActorShape, position: THREE.Vector3) => void
  getRoutePointsByModel: (modelUrl: string) => Array<{ x: number; z: number }>
  getActorRoutePoint: (actor: BehaviorActorShape, index: number) => THREE.Vector3
  roamSpeedRange: { min: number; max: number }
  roamStopRange: { min: number; max: number }
  moveReachDistance: number
  roamMoveVrma: string
  roamPauseVrmaFiles: readonly string[]
}) {
  const {
    pickRoamTarget,
    resolveToWalkablePosition,
    isWalkablePosition,
    faceActorTowardDirection,
    playActorMotion,
    getPointById,
    occupyPoint,
    releasePoint,
    setActorTargetPosition,
    setActorRoamTarget,
    getRoutePointsByModel,
    getActorRoutePoint,
    roamSpeedRange,
    roamStopRange,
    moveReachDistance,
    roamMoveVrma,
    roamPauseVrmaFiles,
  } = args

  const _direction = new THREE.Vector3()
  const _candidate = new THREE.Vector3()
  const _roamTargetQuat = new THREE.Quaternion()
  const _roamTargetEuler = new THREE.Euler()

  function randomRange(min: number, max: number): number {
    return min + Math.random() * (max - min)
  }

  function pickRandomPauseVrma(): string {
    return roamPauseVrmaFiles[Math.floor(Math.random() * roamPauseVrmaFiles.length)] || roamPauseVrmaFiles[0]
  }

  // ─── Behavior assignment ───

  function assignRoamBehavior(actor: BehaviorActorShape, now: number): void {
    const target = resolveToWalkablePosition(pickRoamTarget(actor.targetPosition, actor), actor)
    const pauseDuration = randomRange(roamStopRange.min, roamStopRange.max) * 1000

    actor.roamSpeed = randomRange(roamSpeedRange.min, roamSpeedRange.max)
    actor.behavior = {
      steps: [
        { type: 'moveTo', target: { x: target.x, z: target.z } },
        { type: 'wait', duration: pauseDuration },
        { type: 'playMotion', vrmaFile: pickRandomPauseVrma(), motionLoop: 'repeat' },
      ],
      currentIndex: 0,
      stepStartedAt: now,
      interruptible: true,
      onComplete: 'roam',
      motionApplied: false,
    }
  }

  function assignRouteBehavior(actor: BehaviorActorShape, now: number): void {
    const routePoints = getRoutePointsByModel(actor.modelUrl)
    if (routePoints.length < 2) {
      assignRoamBehavior(actor, now)
      return
    }

    const steps: BehaviorStep[] = []
    for (let i = 0; i < routePoints.length; i++) {
      const point = routePoints[i]
      steps.push({ type: 'moveTo', target: { x: point.x, z: point.z } })
      steps.push({
        type: 'wait',
        duration: randomRange(roamStopRange.min, roamStopRange.max) * 1000,
      })
    }

    actor.routePointIndex = 0
    actor.roamSpeed = randomRange(roamSpeedRange.min, roamSpeedRange.max)
    actor.behavior = {
      steps,
      currentIndex: 0,
      stepStartedAt: now,
      interruptible: true,
      onComplete: 'loop',
      motionApplied: false,
    }
  }

  function assignInteractionBehavior(actor: BehaviorActorShape, pointId: string, now: number): boolean {
    const point = getPointById(pointId)
    if (!point) return false
    if (point.occupiedBy.length >= point.capacity && !point.occupiedBy.includes(actor.sessionId)) {
      return false
    }

    actor.roamSpeed = randomRange(roamSpeedRange.min, roamSpeedRange.max)
    actor.behavior = {
      steps: [
        {
          type: 'interact',
          interactionPointId: pointId,
          interactionPhase: 'approach',
          target: { x: point.approachPosition.x, z: point.approachPosition.z },
        },
        {
          type: 'interact',
          interactionPointId: pointId,
          interactionPhase: 'enter',
        },
        {
          type: 'interact',
          interactionPointId: pointId,
          interactionPhase: 'loop',
        },
      ],
      currentIndex: 0,
      stepStartedAt: now,
      interruptible: true,
      onComplete: 'roam',
      motionApplied: false,
    }
    return true
  }

  // ─── Step executors ───

  /** 卡點超時秒數：超過此時間沒有靠近目標則跳過 */
  const STUCK_TIMEOUT = 4.0
  /** 判定有進展的最小距離差 */
  const STUCK_PROGRESS_THRESHOLD = 0.05

  function executeMoveToStep(
    actor: BehaviorActorShape,
    step: BehaviorStep,
    now: number,
    delta: number,
  ): boolean {
    if (!step.target) return true
    const behavior = actor.behavior
    if (!behavior) return true

    const targetVec = new THREE.Vector3(step.target.x, actor.targetPosition.y, step.target.z)
    _direction.copy(targetVec).sub(actor.targetPosition)
    _direction.y = 0
    const distance = _direction.length()

    if (distance <= moveReachDistance) return true

    // ─── 卡點偵測 ───
    const lastDist = behavior._lastDistToTarget
    const lastProgressAt = behavior._lastProgressAt ?? now

    if (lastDist !== undefined && lastDist - distance > STUCK_PROGRESS_THRESHOLD) {
      // 有進展：更新記錄
      behavior._lastProgressAt = now
    } else if (lastDist === undefined) {
      // 首次記錄
      behavior._lastProgressAt = now
    }
    behavior._lastDistToTarget = distance

    // 超時：跳過此步驟
    if (now - (behavior._lastProgressAt ?? now) > STUCK_TIMEOUT) {
      behavior._lastDistToTarget = undefined
      behavior._lastProgressAt = undefined
      return true
    }

    // ─── 移動邏輯 ───
    const speed = actor.roamSpeed
    const stepDist = Math.min(distance, speed * Math.max(delta, 0.001))
    _direction.divideScalar(distance)
    _candidate.copy(actor.targetPosition).addScaledVector(_direction, stepDist)

    if (step.skipObstacles) {
      // 路線模式：直接移動，忽略碰撞
      setActorTargetPosition(actor, _candidate)
      faceActorTowardDirection(actor, _direction, delta)
    } else if (isWalkablePosition(_candidate, actor)) {
      setActorTargetPosition(actor, _candidate)
      faceActorTowardDirection(actor, _direction, delta)
    } else {
      // 嘗試繞路：左右兩側偏移嘗試
      let moved = false
      const perpX = -_direction.z
      const perpZ = _direction.x

      for (const sign of [1, -1]) {
        _candidate.set(
          actor.targetPosition.x + (_direction.x * 0.5 + perpX * sign * 0.7) * stepDist * 3,
          actor.targetPosition.y,
          actor.targetPosition.z + (_direction.z * 0.5 + perpZ * sign * 0.7) * stepDist * 3,
        )
        if (isWalkablePosition(_candidate, actor)) {
          setActorTargetPosition(actor, _candidate)
          const detourDir = _candidate.clone().sub(actor.targetPosition).normalize()
          faceActorTowardDirection(actor, detourDir, delta)
          moved = true
          break
        }
      }

      if (!moved) {
        const resolved = resolveToWalkablePosition(
          new THREE.Vector3(
            actor.targetPosition.x + _direction.x * stepDist,
            actor.targetPosition.y,
            actor.targetPosition.z + _direction.z * stepDist,
          ),
          actor,
        )
        setActorTargetPosition(actor, resolved)
        faceActorTowardDirection(actor, _direction, delta)
      }
    }

    void playActorMotion(actor, roamMoveVrma).catch(() => {})
    return false
  }

  function executeWaitStep(
    _actor: BehaviorActorShape,
    step: BehaviorStep,
    now: number,
    behavior: ActorBehavior,
  ): boolean {
    const elapsed = (now - behavior.stepStartedAt) * 1000
    return elapsed >= (step.duration || 0)
  }

  function executePlayMotionStep(
    actor: BehaviorActorShape,
    step: BehaviorStep,
    _now: number,
    behavior: ActorBehavior,
  ): boolean {
    if (!behavior.motionApplied && step.vrmaFile) {
      if (step.motionOffset) {
        const nextPosition = actor.targetPosition.clone().add(
          new THREE.Vector3(
            step.motionOffset.x,
            step.motionOffset.y,
            step.motionOffset.z,
          ),
        )
        setActorTargetPosition(actor, nextPosition, false, step.motionOffset.y !== 0)
      }
      if (step.motionRotationY !== undefined) {
        _roamTargetEuler.set(0, step.motionRotationY * Math.PI / 180, 0)
        _roamTargetQuat.setFromEuler(_roamTargetEuler)
        actor.root.quaternion.copy(_roamTargetQuat)
      }
      behavior.motionApplied = true
      void playActorMotion(actor, step.vrmaFile, {
        loop: step.motionLoop || 'repeat',
      }).catch(() => {})
    }

    if (step.motionLoop === 'once') {
      const elapsed = (_now - behavior.stepStartedAt) * 1000
      return elapsed >= (step.duration || 2000)
    }

    // repeat 模式：wait 由外部 duration 或打斷控制
    if (step.duration) {
      const elapsed = (_now - behavior.stepStartedAt) * 1000
      return elapsed >= step.duration
    }

    // 無 duration 的 repeat：完成後推進到下一步
    return false
  }

  function executeInteractStep(
    actor: BehaviorActorShape,
    step: BehaviorStep,
    now: number,
    delta: number,
    behavior: ActorBehavior,
  ): boolean {
    const pointId = step.interactionPointId
    if (!pointId) return true
    const point = getPointById(pointId)
    if (!point) return true

    switch (step.interactionPhase) {
      case 'approach': {
        const approachTarget = {
          x: point.approachPosition.x,
          z: point.approachPosition.z,
        }
        const reached = executeMoveToStep(actor, { ...step, target: approachTarget }, now, delta)
        if (reached) {
          // 到達 approach 點後轉向：優先使用覆寫方向，否則用互動點預設
          const rotY = step.interactRotationYOverride !== undefined
            ? step.interactRotationYOverride * Math.PI / 180
            : point.approachRotationY
          _roamTargetEuler.set(0, rotY, 0)
          _roamTargetQuat.setFromEuler(_roamTargetEuler)
          actor.root.quaternion.copy(_roamTargetQuat)
        }
        return reached
      }

      case 'enter': {
        const occupied = occupyPoint(pointId, actor.sessionId)
        if (!occupied) {
          // 搶佔失敗：清空行為佇列，回退到漫步
          actor.behavior = null
          return false
        }

        actor.occupyingPointId = pointId

        const yCorrection = step.interactOffsetY ?? 0
        const zCorrection = step.interactOffsetZ ?? 0
        if (point.action.seatOffset) {
          const offset = point.action.seatOffset
          const seatPos = new THREE.Vector3(
            point.position.x + offset.x,
            actor.targetPosition.y + offset.y + yCorrection,
            point.position.z + offset.z + zCorrection,
          )
          setActorTargetPosition(actor, seatPos, false, true)
        } else if (yCorrection !== 0 || zCorrection !== 0) {
          const correctedPos = actor.targetPosition.clone()
          correctedPos.y += yCorrection
          correctedPos.z += zCorrection
          setActorTargetPosition(actor, correctedPos, false, yCorrection !== 0)
        }

        // enter 時也套用方向覆寫（確保方向一致）
        if (step.interactRotationYOverride !== undefined) {
          _roamTargetEuler.set(0, step.interactRotationYOverride * Math.PI / 180, 0)
          _roamTargetQuat.setFromEuler(_roamTargetEuler)
          actor.root.quaternion.copy(_roamTargetQuat)
        }

        const enterVrma = step.interactEnterVrma || point.action.enterVrma
        if (enterVrma) {
          behavior.motionApplied = true
          void playActorMotion(actor, enterVrma, { loop: 'once' }).catch(() => {})
        }
        return true
      }

      case 'loop': {
        if (!behavior.motionApplied) {
          behavior.motionApplied = true
          const loopVrma = step.interactLoopVrma || point.action.loopVrma
          void playActorMotion(actor, loopVrma, { loop: 'repeat' }).catch(() => {})
        }
        // loop 永不自動完成，等待打斷
        return false
      }

      case 'exit': {
        if (!behavior.motionApplied) {
          behavior.motionApplied = true
          const exitVrma = step.interactExitVrma || point.action.exitVrma
          if (exitVrma) {
            void playActorMotion(actor, exitVrma, { loop: 'once' }).catch(() => {})
          }
        }

        // exit 動畫播放完後釋放
        const elapsed = (now - behavior.stepStartedAt) * 1000
        if (elapsed >= 800) {
          releasePoint(pointId, actor.sessionId)
          actor.occupyingPointId = null
          return true
        }
        return false
      }

      default:
        return true
    }
  }

  // ─── Main update ───

  function updateBehavior(actor: BehaviorActorShape, now: number, delta: number): void {
    const behavior = actor.behavior
    if (!behavior) return
    if (behavior.currentIndex >= behavior.steps.length) {
      onBehaviorComplete(actor, now)
      return
    }

    const step = behavior.steps[behavior.currentIndex]
    let completed = false

    switch (step.type) {
      case 'moveTo':
        completed = executeMoveToStep(actor, step, now, delta)
        break
      case 'wait':
        completed = executeWaitStep(actor, step, now, behavior)
        break
      case 'playMotion':
        completed = executePlayMotionStep(actor, step, now, behavior)
        break
      case 'interact':
        completed = executeInteractStep(actor, step, now, delta, behavior)
        break
      case 'roam':
        // roam 步驟：委託給 assignRoamBehavior 產生新佇列
        assignRoamBehavior(actor, now)
        return
    }

    if (completed) {
      behavior.currentIndex++
      behavior.stepStartedAt = now
      behavior.motionApplied = false
      behavior._lastDistToTarget = undefined
      behavior._lastProgressAt = undefined
    }
  }

  function onBehaviorComplete(actor: BehaviorActorShape, now: number): void {
    const behavior = actor.behavior
    if (!behavior) return

    switch (behavior.onComplete) {
      case 'loop': {
        // 重新循環（路線行走）
        behavior.currentIndex = 0
        behavior.stepStartedAt = now
        behavior.motionApplied = false
        break
      }
      case 'roam':
        assignRoamBehavior(actor, now)
        break
      case 'idle':
      default:
        actor.behavior = null
        break
    }
  }

  // ─── Interrupt ───

  async function interruptBehavior(actor: BehaviorActorShape, _reason: string): Promise<void> {
    const behavior = actor.behavior
    if (!behavior) return

    const currentStep = behavior.steps[behavior.currentIndex]

    // 如果正在互動中，需要執行 exit
    if (
      currentStep
      && currentStep.type === 'interact'
      && currentStep.interactionPointId
      && (currentStep.interactionPhase === 'loop' || currentStep.interactionPhase === 'enter')
    ) {
      const pointId = currentStep.interactionPointId
      behavior.steps = [
        {
          type: 'interact',
          interactionPointId: pointId,
          interactionPhase: 'exit',
        },
      ]
      behavior.currentIndex = 0
      behavior.stepStartedAt = 0 // 會在下一次 updateBehavior 時被 now 覆蓋
      behavior.motionApplied = false
      behavior.onComplete = 'idle'
      return
    }

    // 不在互動中，直接清空
    if (actor.occupyingPointId) {
      releasePoint(actor.occupyingPointId, actor.sessionId)
      actor.occupyingPointId = null
    }
    actor.behavior = null
  }

  function getCurrentStepType(actor: BehaviorActorShape): BehaviorStepType | null {
    const behavior = actor.behavior
    if (!behavior || behavior.currentIndex >= behavior.steps.length) return null
    return behavior.steps[behavior.currentIndex].type
  }

  function isInteracting(actor: BehaviorActorShape): boolean {
    const behavior = actor.behavior
    if (!behavior) return false
    const step = behavior.steps[behavior.currentIndex]
    return !!step && step.type === 'interact'
  }

  function cleanupBehavior(actor: BehaviorActorShape): void {
    if (actor.occupyingPointId) {
      releasePoint(actor.occupyingPointId, actor.sessionId)
      actor.occupyingPointId = null
    }
    actor.behavior = null
  }

  return {
    assignRoamBehavior,
    assignRouteBehavior,
    assignInteractionBehavior,
    updateBehavior,
    interruptBehavior,
    getCurrentStepType,
    isInteracting,
    cleanupBehavior,
  }
}
