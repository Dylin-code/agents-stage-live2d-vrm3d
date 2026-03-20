import { describe, expect, it } from 'vitest'
import { shouldReplayActorMotion } from './sessionStageActorRuntime'

describe('sessionStageActorRuntime', () => {
  it('does not replay motion when the next state is the same', () => {
    expect(shouldReplayActorMotion('THINKING', 'THINKING')).toBe(false)
  })

  it('replays motion when the next state changes', () => {
    expect(shouldReplayActorMotion('IDLE', 'RESPONDING')).toBe(true)
  })
})
