import { describe, expect, it } from 'vitest'
import { applyDraftActionType, normalizeSeatOffset } from './vrmInteractionPointDraftUtils'

describe('vrmInteractionPointDraftUtils', () => {
  it('creates a default seat offset for sit points', () => {
    expect(normalizeSeatOffset('sit')).toEqual({ x: 0, y: 0, z: 0 })
  })

  it('clears seat offset for non-sit points', () => {
    expect(normalizeSeatOffset('work', { x: 1, y: 2, z: 3 })).toBeUndefined()
  })

  it('applies action type changes without mutating other draft fields', () => {
    const draft = {
      id: 'point-1',
      label: '點位',
      position: { x: 1, y: 0, z: 2 },
      approachPosition: { x: 1, y: 0, z: 3 },
      approachRotationY: 0,
      action: {
        type: 'work',
        loopVrma: 'Thinking.vrma',
      },
      capacity: 1,
    }

    const sitDraft = applyDraftActionType(draft, 'sit')
    expect(sitDraft.action.type).toBe('sit')
    expect(sitDraft.action.seatOffset).toEqual({ x: 0, y: 0, z: 0 })
    expect(sitDraft.label).toBe(draft.label)

    const workDraft = applyDraftActionType(sitDraft, 'work')
    expect(workDraft.action.type).toBe('work')
    expect(workDraft.action.seatOffset).toBeUndefined()
  })
})
