import { describe, expect, it } from 'vitest'
import {
  VRM_INTERACTION_EDITOR_TOGGLE_EVENT,
  VRM_INTERACTION_POINTS_RELOAD_EVENT,
  VRM_INTERACTION_POINTS_STORAGE_KEY_PREFIX,
  buildInteractionPointsStorageKey,
} from './vrmInteractionPointEvents'

describe('vrmInteractionPointEvents', () => {
  it('uses a scene-specific storage key', () => {
    const a = buildInteractionPointsStorageKey('/vrm3d/scenes/classroom_scene.glb')
    const b = buildInteractionPointsStorageKey('/vrm3d/scenes/mirrors_edge_apartment.glb')

    expect(a).toContain(VRM_INTERACTION_POINTS_STORAGE_KEY_PREFIX)
    expect(b).toContain(VRM_INTERACTION_POINTS_STORAGE_KEY_PREFIX)
    expect(a).not.toBe(b)
  })

  it('exports a stable reload event name', () => {
    expect(VRM_INTERACTION_POINTS_RELOAD_EVENT).toBe('session-stage:vrm-interaction-points-reload')
  })

  it('exports a stable editor toggle event name', () => {
    expect(VRM_INTERACTION_EDITOR_TOGGLE_EVENT).toBe('session-stage:vrm-interaction-editor-toggle')
  })
})
