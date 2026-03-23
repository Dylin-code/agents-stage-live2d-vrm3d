import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { buildDialogFocusCameraView, resolveActorTargetY } from './useVrmStage.runtime'

describe('useVrmStage.runtime', () => {
  it('defaults actor target y to ground offset plus global offset', () => {
    expect(resolveActorTargetY(99, 0.4, -0.1)).toBeCloseTo(0.3)
  })

  it('preserves the input y when requested for interaction offsets', () => {
    expect(resolveActorTargetY(-100, 0.4, 0.2, true)).toBe(-100)
  })

  it('builds a focus camera view in front of the actor', () => {
    const target = new THREE.Vector3(1, 1.2, -0.5)
    const view = buildDialogFocusCameraView(target, new THREE.Vector3(0, 0, 2), 1.5, 0.25)

    expect(view.target).toEqual({ x: 1, y: 1.2, z: -0.5 })
    expect(view.position.x).toBeCloseTo(1)
    expect(view.position.y).toBeCloseTo(1.45)
    expect(view.position.z).toBeCloseTo(1)
  })

  it('falls back to forward z-axis when actor forward is degenerate', () => {
    const view = buildDialogFocusCameraView(new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 0))

    expect(view.position.x).toBeCloseTo(0)
    expect(view.position.y).toBeCloseTo(1.16)
    expect(view.position.z).toBeCloseTo(2.5)
  })
})
