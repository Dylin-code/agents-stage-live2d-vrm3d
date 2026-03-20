export const VRM_INTERACTION_POINTS_STORAGE_KEY_PREFIX = 'vrm-stage-interaction-points-v1'
export const VRM_INTERACTION_POINTS_RELOAD_EVENT = 'session-stage:vrm-interaction-points-reload'
export const VRM_INTERACTION_EDITOR_TOGGLE_EVENT = 'session-stage:vrm-interaction-editor-toggle'

export function buildInteractionPointsStorageKey(sceneUrl: string): string {
  return `${VRM_INTERACTION_POINTS_STORAGE_KEY_PREFIX}:${encodeURIComponent(sceneUrl)}`
}
