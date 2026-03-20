import { describe, expect, it } from 'vitest'
import { resolveActorTargetY } from './useVrmStage.runtime'

describe('useVrmStage.runtime', () => {
  it('defaults actor target y to ground offset plus global offset', () => {
    expect(resolveActorTargetY(99, 0.4, -0.1)).toBeCloseTo(0.3)
  })

  it('preserves the input y when requested for interaction offsets', () => {
    expect(resolveActorTargetY(-100, 0.4, 0.2, true)).toBe(-100)
  })
})
