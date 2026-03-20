import { describe, expect, it } from 'vitest'
import { deriveStableVrm3dVisibleSessionIds } from './vrmVisibleSessionOrder'

describe('useSessionStage.runtime', () => {
  it('keeps current 3d slot order stable when visible sessions are unchanged', () => {
    expect(deriveStableVrm3dVisibleSessionIds(
      ['session-1', 'session-2', 'session-3'],
      [
        { session_id: 'session-2' },
        { session_id: 'session-1' },
        { session_id: 'session-3' },
      ],
      4,
    )).toEqual(['session-1', 'session-2', 'session-3'])
  })

  it('allows a new session into the visible set without reshuffling survivors', () => {
    expect(deriveStableVrm3dVisibleSessionIds(
      ['session-1', 'session-2', 'session-3', 'session-4'],
      [
        { session_id: 'session-9' },
        { session_id: 'session-2' },
        { session_id: 'session-1' },
        { session_id: 'session-4' },
      ],
      4,
    )).toEqual(['session-1', 'session-2', 'session-4', 'session-9'])
  })
})
