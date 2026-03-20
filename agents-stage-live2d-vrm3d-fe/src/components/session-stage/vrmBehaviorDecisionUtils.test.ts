import { beforeEach, describe, expect, it } from 'vitest'
import { decideBehaviorSteps, getBehaviorFlowConfigUtils, resolveFlowSteps } from './vrmBehaviorDecisionUtils'
import type { InteractionPoint } from './vrmInteractionPointUtils'

const storage = new Map<string, string>()
const mockLocalStorage = {
  getItem(key: string): string | null {
    return storage.has(key) ? storage.get(key) || null : null
  },
  setItem(key: string, value: string): void {
    storage.set(key, String(value))
  },
  removeItem(key: string): void {
    storage.delete(key)
  },
  clear(): void {
    storage.clear()
  },
}

Object.defineProperty(globalThis, 'localStorage', {
  value: mockLocalStorage,
  configurable: true,
})

function createPoint(id: string, type: string, x: number, z: number): InteractionPoint {
  return {
    id,
    label: id,
    position: { x, z },
    approachPosition: { x: x + 0.1, z: z + 0.2 },
    approachRotationY: 0,
    action: {
      type,
      loopVrma: 'Relax.vrma',
    },
    capacity: 1,
    occupiedBy: [],
  }
}

describe('vrmBehaviorDecisionUtils', () => {
  const configUtils = getBehaviorFlowConfigUtils()

  beforeEach(() => {
    localStorage.clear()
    configUtils.resetToDefault()
  })

  it('does not fallback to legacy hardcoded behavior when config has no matching flow', () => {
    configUtils.saveConfig({
      version: 3,
      flows: [],
      slotAssignments: {},
    })

    const point = createPoint('chair-1', 'sit', 1, 2)
    const result = decideBehaviorSteps({
      actorState: 'THINKING',
      previousState: 'IDLE',
      hasCustomRoute: false,
      isInteracting: false,
      findPointById: (pointId) => (pointId === point.id ? point : null),
      findNearestAvailablePoint: () => point,
      actorPosition: { x: 0, z: 0 },
      getAvailablePointTypes: () => ['sit'],
      actorSlot: 0,
    })

    expect(result.steps).toEqual([])
    expect(result.onComplete).toBe('idle')
    expect(result.interruptCurrent).toBe(true)
  })

  it('resolves moveTo interactionPoint mode to the selected point', () => {
    const point = createPoint('desk-1', 'work', 4, 5)

    const resolved = resolveFlowSteps(
      [
        {
          id: 'step-1',
          type: 'moveTo',
          moveTarget: {
            mode: 'interactionPoint',
            interactionPointId: point.id,
          },
        },
      ],
      {
        actorState: 'TOOLING',
        hasCustomRoute: false,
        isInteracting: false,
        findPointById: (pointId) => (pointId === point.id ? point : null),
        findNearestAvailablePoint: () => null,
        actorPosition: { x: 0, z: 0 },
      },
    )

    expect(resolved?.steps).toEqual([
      {
        type: 'moveTo',
        target: {
          x: point.approachPosition.x,
          z: point.approachPosition.z,
        },
        skipObstacles: undefined,
      },
    ])
  })

  it('resolves interact specific mode to the selected interaction point', () => {
    const point = createPoint('chair-2', 'sit', -1, 3)

    const resolved = resolveFlowSteps(
      [
        {
          id: 'step-1',
          type: 'interact',
          interactTarget: {
            mode: 'specific',
            interactionPointId: point.id,
            pointType: 'sit',
          },
          interactRotationYOverride: 45,
          interactOffsetY: -0.3,
          interactOffsetZ: 0.2,
        },
      ],
      {
        actorState: 'IDLE',
        hasCustomRoute: false,
        isInteracting: false,
        findPointById: (pointId) => (pointId === point.id ? point : null),
        findNearestAvailablePoint: () => null,
        actorPosition: { x: 0, z: 0 },
      },
    )

    expect(resolved?.useInteractionPoint).toBe(point.id)
    expect(resolved?.steps.map((step) => step.type)).toEqual(['interact', 'interact', 'interact'])
    expect(resolved?.steps[0]).toMatchObject({
      interactionPointId: point.id,
      interactionPhase: 'approach',
      target: {
        x: point.approachPosition.x,
        z: point.approachPosition.z,
      },
      interactRotationYOverride: 45,
      interactOffsetY: -0.3,
      interactOffsetZ: 0.2,
    })
  })

  it('resolves playMotion offsets into scheduler steps', () => {
    const resolved = resolveFlowSteps(
      [
        {
          id: 'step-1',
          type: 'playMotion',
          motionFile: 'Thinking.vrma',
          motionLoop: 'repeat',
          motionDuration: 5000,
          motionOffset: { x: 0.3, y: -0.2, z: 0.15 },
          motionRotationY: 90,
        },
      ],
      {
        actorState: 'THINKING',
        hasCustomRoute: false,
        isInteracting: false,
        findNearestAvailablePoint: () => null,
        actorPosition: { x: 0, z: 0 },
      },
    )

    expect(resolved?.steps).toEqual([
      {
        type: 'playMotion',
        vrmaFile: 'Thinking.vrma',
        motionLoop: 'repeat',
        duration: 5000,
        motionOffset: { x: 0.3, y: -0.2, z: 0.15 },
        motionRotationY: 90,
      },
    ])
  })

  it('matches flow when current state is included in triggerStates', () => {
    configUtils.saveConfig({
      version: 3,
      flows: [
        {
          id: 'multi-trigger-flow',
          name: '多狀態流程',
          triggerStates: ['THINKING', 'WAITING'],
          priority: 1,
          probability: 1,
          condition: {},
          steps: [{ id: 'step-1', type: 'roam' }],
          onComplete: 'roam',
          interruptOnStateChange: true,
          actorSlot: null,
        },
      ],
      slotAssignments: {},
    })

    const result = decideBehaviorSteps({
      actorState: 'WAITING',
      previousState: 'IDLE',
      hasCustomRoute: false,
      isInteracting: false,
      findNearestAvailablePoint: () => null,
      actorPosition: { x: 0, z: 0 },
      getAvailablePointTypes: () => [],
      actorSlot: 0,
    })

    expect(result.steps).toEqual([{ type: 'roam' }])
    expect(result.onComplete).toBe('roam')
  })
})
