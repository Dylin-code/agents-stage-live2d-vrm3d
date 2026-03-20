import { describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import { createVrmBehaviorScheduler } from './vrmBehaviorScheduler'
import type { InteractionPoint } from './vrmInteractionPointUtils'

function createPoint(): InteractionPoint {
  return {
    id: 'chair-1',
    label: 'chair-1',
    position: { x: 1, z: 2 },
    approachPosition: { x: 0, z: 0 },
    approachRotationY: 0,
    action: {
      type: 'sit',
      loopVrma: 'SittingDrinking.vrma',
      seatOffset: { x: 0, y: -0.5, z: 0.1 },
    },
    capacity: 1,
    occupiedBy: [],
  }
}

describe('vrmBehaviorScheduler', () => {
  it('applies interaction y offset on enter and preserves input y', () => {
    const point = createPoint()
    const setActorTargetPosition = vi.fn()

    const scheduler = createVrmBehaviorScheduler({
      pickRoamTarget: () => new THREE.Vector3(),
      resolveToWalkablePosition: (desired) => desired.clone(),
      isWalkablePosition: () => true,
      faceActorTowardDirection: () => {},
      playActorMotion: async () => null,
      getPointById: () => point,
      occupyPoint: () => true,
      releasePoint: () => {},
      setActorTargetPosition,
      setActorRoamTarget: () => {},
      getRoutePointsByModel: () => [],
      getActorRoutePoint: () => new THREE.Vector3(),
      roamSpeedRange: { min: 0.2, max: 0.8 },
      roamStopRange: { min: 2, max: 5 },
      moveReachDistance: 0.22,
      roamMoveVrma: 'Walking.vrma',
      roamPauseVrmaFiles: ['Relax.vrma'],
    })

    const actor = {
      sessionId: 'session-1',
      modelUrl: 'model.vrm',
      targetPosition: new THREE.Vector3(0, 1.2, 0),
      roamTarget: new THREE.Vector3(),
      roamSpeed: 0.4,
      root: new THREE.Group(),
      behavior: null,
      occupyingPointId: null,
      routePointIndex: 0,
    }

    scheduler.assignInteractionBehavior(actor, point.id, 0)
    scheduler.updateBehavior(actor, 0, 0)
    actor.behavior!.steps[1]!.interactOffsetY = -0.25
    scheduler.updateBehavior(actor, 0, 0)

    const lastCall = setActorTargetPosition.mock.calls.at(-1)
    expect(lastCall?.[0]).toBe(actor)
    expect((lastCall?.[1] as THREE.Vector3).x).toBeCloseTo(1)
    expect((lastCall?.[1] as THREE.Vector3).y).toBeCloseTo(0.45)
    expect((lastCall?.[1] as THREE.Vector3).z).toBeCloseTo(2.1)
    expect(lastCall?.[2]).toBe(false)
    expect(lastCall?.[3]).toBe(true)
  })

  it('applies playMotion offsets once when the motion starts', () => {
    const setActorTargetPosition = vi.fn()
    const playActorMotion = vi.fn(async () => null)

    const scheduler = createVrmBehaviorScheduler({
      pickRoamTarget: () => new THREE.Vector3(),
      resolveToWalkablePosition: (desired) => desired.clone(),
      isWalkablePosition: () => true,
      faceActorTowardDirection: () => {},
      playActorMotion,
      getPointById: () => null,
      occupyPoint: () => true,
      releasePoint: () => {},
      setActorTargetPosition,
      setActorRoamTarget: () => {},
      getRoutePointsByModel: () => [],
      getActorRoutePoint: () => new THREE.Vector3(),
      roamSpeedRange: { min: 0.2, max: 0.8 },
      roamStopRange: { min: 2, max: 5 },
      moveReachDistance: 0.22,
      roamMoveVrma: 'Walking.vrma',
      roamPauseVrmaFiles: ['Relax.vrma'],
    })

    const actor = {
      sessionId: 'session-2',
      modelUrl: 'model.vrm',
      targetPosition: new THREE.Vector3(1, 1.5, 2),
      roamTarget: new THREE.Vector3(),
      roamSpeed: 0.4,
      root: new THREE.Group(),
      behavior: {
        steps: [
          {
            type: 'playMotion' as const,
            vrmaFile: 'Thinking.vrma',
            motionLoop: 'repeat' as const,
            motionOffset: { x: 0.25, y: -0.4, z: 0.1 },
            motionRotationY: 90,
          },
        ],
        currentIndex: 0,
        stepStartedAt: 0,
        interruptible: true,
        onComplete: 'idle' as const,
        motionApplied: false,
      },
      occupyingPointId: null,
      routePointIndex: 0,
    }

    scheduler.updateBehavior(actor, 0, 0)
    scheduler.updateBehavior(actor, 1, 0)

    expect(setActorTargetPosition).toHaveBeenCalledTimes(1)
    const firstCall = setActorTargetPosition.mock.calls[0]
    expect(firstCall?.[0]).toBe(actor)
    expect((firstCall?.[1] as THREE.Vector3).x).toBeCloseTo(1.25)
    expect((firstCall?.[1] as THREE.Vector3).y).toBeCloseTo(1.1)
    expect((firstCall?.[1] as THREE.Vector3).z).toBeCloseTo(2.1)
    expect(firstCall?.[2]).toBe(false)
    expect(firstCall?.[3]).toBe(true)
    const facingDirection = new THREE.Vector3(0, 0, 1).applyQuaternion(actor.root.quaternion)
    expect(facingDirection.x).toBeCloseTo(1, 5)
    expect(facingDirection.z).toBeCloseTo(0, 5)
    expect(playActorMotion).toHaveBeenCalledTimes(1)
    expect(playActorMotion).toHaveBeenCalledWith(
      actor,
      'Thinking.vrma',
      { loop: 'repeat' },
    )
  })
})
