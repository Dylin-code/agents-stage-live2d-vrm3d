import type { BehaviorStep } from './vrmBehaviorScheduler'
import type { InteractionPoint } from './vrmInteractionPointUtils'
import type { SessionState } from '../../types/sessionState'
import type {
  BehaviorFlow,
  BehaviorFlowStep,
  BehaviorFlowConfigContext,
} from './vrmBehaviorFlowConfigUtils'
import { createVrmBehaviorFlowConfigUtils } from './vrmBehaviorFlowConfigUtils'

export interface BehaviorDecisionContext {
  actorState: SessionState
  previousState?: SessionState
  hasCustomRoute: boolean
  isInteracting: boolean
  findPointById?: (pointId: string) => InteractionPoint | null
  findNearestAvailablePoint: (
    position: { x: number; z: number },
    type?: string,
  ) => InteractionPoint | null
  actorPosition: { x: number; z: number }
  /** 取得所有可用互動點類型（供配置條件檢查） */
  getAvailablePointTypes?: () => string[]
  /** 角色的 slot index (0~3)，用於 per-actor 行為流配置 */
  actorSlot?: number
}

export interface BehaviorDecisionResult {
  steps: BehaviorStep[]
  interruptCurrent: boolean
  onComplete: 'loop' | 'roam' | 'idle'
  useInteractionPoint?: string
}

// ─── Config-based flow resolution singleton ───

const flowConfigUtils = createVrmBehaviorFlowConfigUtils()

/** 暴露給外部（UI 面板）使用的配置工具 */
export function getBehaviorFlowConfigUtils() {
  return flowConfigUtils
}

// ─── State change interrupt logic ───

function shouldInterrupt(
  previous: SessionState | undefined,
  current: SessionState,
  isInteracting: boolean,
): boolean {
  if (!previous || previous === current) return false
  if (current === 'RESPONDING' && isInteracting) return true
  if (isInteracting && (current === 'IDLE' || current === 'RESPONDING')) return true
  if (!isInteracting && (current === 'THINKING' || current === 'TOOLING' || current === 'WAITING')) return true
  return false
}

// ─── Config-driven decision ───

/**
 * 根據 session state 決定角色行為。
 * 僅使用 BehaviorFlowConfig；無匹配流程時不執行任何動作。
 */
export function decideBehaviorSteps(ctx: BehaviorDecisionContext): BehaviorDecisionResult {
  const { actorState, previousState, isInteracting } = ctx
  const interrupt = shouldInterrupt(previousState, actorState, isInteracting)

  // 嘗試從配置中決策
  const configResult = tryConfigBasedDecision(ctx, interrupt)
  if (configResult) return configResult

  return { steps: [], interruptCurrent: interrupt, onComplete: 'idle' }
}

function tryConfigBasedDecision(
  ctx: BehaviorDecisionContext,
  interrupt: boolean,
): BehaviorDecisionResult | null {
  const availablePointTypes = ctx.getAvailablePointTypes?.() ?? collectAvailablePointTypes(ctx)

  const configCtx: BehaviorFlowConfigContext = {
    hasRoute: ctx.hasCustomRoute,
    availablePointTypes,
    actorSlot: ctx.actorSlot,
  }

  const flows = flowConfigUtils.getFlowsForState(ctx.actorState, configCtx)
  if (flows.length === 0) return null

  for (const flow of flows) {
    // 機率過濾
    if (flow.probability !== undefined && flow.probability < 1.0 && Math.random() > flow.probability) {
      continue
    }

    const resolved = resolveFlowSteps(flow.steps, ctx)
    if (!resolved) continue

    return {
      steps: resolved.steps,
      interruptCurrent: flow.interruptOnStateChange ? interrupt : false,
      onComplete: flow.onComplete,
      useInteractionPoint: resolved.useInteractionPoint,
    }
  }

  return null
}

/** 從 context 推斷可用的互動點類型 */
function collectAvailablePointTypes(ctx: BehaviorDecisionContext): string[] {
  const types: string[] = []
  for (const type of ['sit', 'work', 'stand-idle']) {
    if (ctx.findNearestAvailablePoint(ctx.actorPosition, type)) {
      types.push(type)
    }
  }
  return types
}

// ─── Flow Step Resolution ───

interface ResolvedFlowResult {
  steps: BehaviorStep[]
  useInteractionPoint?: string
}

export function resolveFlowSteps(
  flowSteps: BehaviorFlowStep[],
  ctx: BehaviorDecisionContext,
): ResolvedFlowResult | null {
  const resolved: BehaviorStep[] = []
  let useInteractionPoint: string | undefined

  for (const fs of flowSteps) {
    switch (fs.type) {
      case 'moveTo': {
        const targets = resolveMoveTargets(fs, ctx)
        if (!targets || targets.length === 0) return null
        const skip = fs.skipObstacles ?? (fs.moveTarget?.mode === 'route')
        for (const t of targets) {
          resolved.push({ type: 'moveTo', target: t, skipObstacles: skip || undefined })
        }
        break
      }

      case 'interact': {
        const point = resolveInteractTarget(fs, ctx)
        if (!point) return null
        useInteractionPoint = point.id
        // 動畫覆寫（留空則 scheduler 會用互動點預設動畫）
        const enterVrma = fs.interactEnterVrma || undefined
        const loopVrma = fs.interactLoopVrma || undefined
        const exitVrma = fs.interactExitVrma || undefined
        // 方向與 Z 校正覆寫
        const rotationYOverride = fs.interactRotationYOverride
        const offsetY = fs.interactOffsetY
        const offsetZ = fs.interactOffsetZ
        resolved.push(
          {
            type: 'interact',
            interactionPointId: point.id,
            interactionPhase: 'approach',
            target: { x: point.approachPosition.x, z: point.approachPosition.z },
            interactEnterVrma: enterVrma,
            interactLoopVrma: loopVrma,
            interactExitVrma: exitVrma,
            interactRotationYOverride: rotationYOverride,
            interactOffsetY: offsetY,
            interactOffsetZ: offsetZ,
          },
          {
            type: 'interact',
            interactionPointId: point.id,
            interactionPhase: 'enter',
            interactEnterVrma: enterVrma,
            interactLoopVrma: loopVrma,
            interactExitVrma: exitVrma,
            interactRotationYOverride: rotationYOverride,
            interactOffsetY: offsetY,
            interactOffsetZ: offsetZ,
          },
          {
            type: 'interact',
            interactionPointId: point.id,
            interactionPhase: 'loop',
            interactEnterVrma: enterVrma,
            interactLoopVrma: loopVrma,
            interactExitVrma: exitVrma,
          },
        )
        break
      }

      case 'wait': {
        const duration = fs.waitRandom
          ? randomBetween(fs.waitRandom.min, fs.waitRandom.max)
          : (fs.waitDuration ?? 3000)
        resolved.push({ type: 'wait', duration })
        break
      }

      case 'playMotion': {
        resolved.push({
          type: 'playMotion',
          vrmaFile: fs.motionFile,
          motionLoop: fs.motionLoop ?? 'once',
          duration: fs.motionDuration,
          motionOffset: fs.motionOffset ? { ...fs.motionOffset } : undefined,
          motionRotationY: fs.motionRotationY,
        })
        break
      }

      case 'roam': {
        resolved.push({ type: 'roam' })
        break
      }
    }
  }

  return resolved.length > 0 ? { steps: resolved, useInteractionPoint } : null
}

/** 解析 moveTo 目標，回傳一個或多個座標（route 模式回傳多個） */
function resolveMoveTargets(
  fs: BehaviorFlowStep,
  ctx: BehaviorDecisionContext,
): Array<{ x: number; z: number }> | null {
  const mt = fs.moveTarget
  if (!mt) return null

  switch (mt.mode) {
    case 'coordinate':
      return mt.coordinate ? [mt.coordinate] : null

    case 'route': {
      if (!mt.waypoints || mt.waypoints.length === 0) return null
      return mt.waypoints
    }

    case 'interactionPoint': {
      if (mt.interactionPointId) {
        const point = ctx.findPointById?.(mt.interactionPointId)
        if (point) return [{ x: point.approachPosition.x, z: point.approachPosition.z }]
      }
      return null
    }

    case 'random': {
      const range = mt.randomRange ?? 2.0
      return [{
        x: (Math.random() - 0.5) * range * 2,
        z: (Math.random() - 0.5) * range * 2,
      }]
    }

    default:
      return null
  }
}

function resolveInteractTarget(
  fs: BehaviorFlowStep,
  ctx: BehaviorDecisionContext,
): InteractionPoint | null {
  const it = fs.interactTarget
  if (!it) return ctx.findNearestAvailablePoint(ctx.actorPosition)

  switch (it.mode) {
    case 'nearest':
      return ctx.findNearestAvailablePoint(ctx.actorPosition, it.pointType)

    case 'specific': {
      if (!it.interactionPointId) return null
      const point = ctx.findPointById?.(it.interactionPointId)
      if (!point) return null
      if (it.pointType && point.action.type !== it.pointType) return null
      return point
    }

    case 'byType':
      return ctx.findNearestAvailablePoint(ctx.actorPosition, it.pointType)

    default:
      return ctx.findNearestAvailablePoint(ctx.actorPosition)
  }
}

function randomBetween(min: number, max: number): number {
  return min + Math.random() * (max - min)
}
