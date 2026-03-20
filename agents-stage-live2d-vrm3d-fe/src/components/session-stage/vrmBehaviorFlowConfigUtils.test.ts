import { beforeEach, describe, expect, it } from 'vitest'
import { createVrmBehaviorFlowConfigUtils, type BehaviorFlowConfig } from './vrmBehaviorFlowConfigUtils'

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

describe('vrmBehaviorFlowConfigUtils', () => {
  beforeEach(() => {
    storage.clear()
  })

  it('applies preview config without persisting it', () => {
    const utils = createVrmBehaviorFlowConfigUtils()
    const base = utils.getPersistedConfig()
    const preview: BehaviorFlowConfig = {
      ...base,
      flows: [
        {
          id: 'preview-flow',
          name: '預覽流程',
          triggerStates: ['IDLE'],
          priority: 1,
          probability: 1,
          condition: {},
          steps: [{ id: 'step-1', type: 'roam' }],
          onComplete: 'roam',
          interruptOnStateChange: true,
          actorSlot: null,
        },
      ],
    }

    utils.applyPreviewConfig(preview)

    expect(utils.getConfig().flows[0]?.id).toBe('preview-flow')
    expect(utils.getPersistedConfig().flows[0]?.id).not.toBe('preview-flow')
    expect(storage.size).toBe(0)
  })

  it('migrates legacy single triggerState into triggerStates', () => {
    storage.set('vrm-stage-behavior-flows-v2', JSON.stringify({
      version: 2,
      flows: [
        {
          id: 'legacy-flow',
          name: '舊流程',
          triggerState: 'WAITING',
          priority: 3,
          condition: {},
          steps: [{ id: 'step-1', type: 'roam' }],
          onComplete: 'roam',
          interruptOnStateChange: true,
        },
      ],
      slotAssignments: {},
    }))

    const utils = createVrmBehaviorFlowConfigUtils()
    const config = utils.getPersistedConfig()

    expect(config.version).toBe(3)
    expect(config.flows[0]?.triggerStates).toEqual(['WAITING'])
    expect(storage.has('vrm-stage-behavior-flows-v2')).toBe(false)
    expect(storage.has('vrm-stage-behavior-flows-v3')).toBe(true)
  })

  it('imports legacy config json by migrating triggerState to triggerStates', () => {
    const utils = createVrmBehaviorFlowConfigUtils()

    const result = utils.importConfig(JSON.stringify({
      version: 2,
      flows: [
        {
          id: 'imported-legacy-flow',
          name: '匯入舊流程',
          triggerState: 'RESPONDING',
          priority: 2,
          condition: {},
          steps: [{ id: 'step-1', type: 'roam' }],
          onComplete: 'roam',
          interruptOnStateChange: true,
        },
      ],
      slotAssignments: {},
    }))

    expect(result.success).toBe(true)
    expect(utils.getPersistedConfig().flows[0]?.triggerStates).toEqual(['RESPONDING'])
  })

  it('discards preview config back to persisted config', () => {
    const utils = createVrmBehaviorFlowConfigUtils()
    const base = utils.getPersistedConfig()

    utils.applyPreviewConfig({
      ...base,
      flows: [],
    })
    utils.discardPreviewConfig()

    expect(utils.getConfig().flows.length).toBe(base.flows.length)
  })
})
