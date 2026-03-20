import { beforeEach, describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { createVrmInteractionPointUtils } from './vrmInteractionPointUtils'

const storage = new Map<string, string>()

Object.defineProperty(globalThis, 'localStorage', {
  value: {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => {
      storage.set(key, value)
    },
    removeItem: (key: string) => {
      storage.delete(key)
    },
  },
  configurable: true,
})

describe('vrmInteractionPointUtils', () => {
  beforeEach(() => {
    storage.clear()
  })

  it('persists manual point y coordinates when saving and loading', () => {
    const storageKey = 'vrm-interaction-point-utils-test'
    const createUtils = () => createVrmInteractionPointUtils({
      storageKey,
      getStageWorldCenter: () => new THREE.Vector3(0, 0, 0),
      getStageWorldSize: () => new THREE.Vector3(10, 5, 10),
    })

    const utils = createUtils()
    utils.addManualPoint({
      id: 'point-1',
      label: '測試點',
      position: { x: 1.25, y: 0.8, z: -0.5 },
      approachPosition: { x: 1.1, y: 0.4, z: -0.2 },
      approachRotationY: Math.PI / 2,
      action: {
        type: 'sit',
        loopVrma: 'SittingDrinking.vrma',
      },
      capacity: 1,
    })
    utils.saveToStorage()

    const reloaded = createUtils()
    expect(reloaded.loadFromStorage()).toBe(true)
    expect(reloaded.getInteractionPoints()).toHaveLength(1)
    expect(reloaded.getPointById('point-1')?.position).toEqual({ x: 1.25, y: 0.8, z: -0.5 })
    expect(reloaded.getPointById('point-1')?.approachPosition).toEqual({ x: 1.1, y: 0.4, z: -0.2 })
  })
})
