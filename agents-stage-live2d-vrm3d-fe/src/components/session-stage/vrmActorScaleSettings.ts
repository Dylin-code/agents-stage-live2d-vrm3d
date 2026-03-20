export const VRM_ACTOR_SCALE_STORAGE_KEY = 'vrm-stage-actor-scale-v1'
export const VRM_ACTOR_SCALE_EVENT = 'session-stage:vrm-actor-scale-change'
export const VRM_ACTOR_SCALE_DEFAULT = 1.6
export const VRM_ACTOR_SCALE_MIN = 0.5
export const VRM_ACTOR_SCALE_MAX = 2.4
export const VRM_ACTOR_SCALE_STEP = 0.05

export function clampVrmActorScale(value: number): number {
  return Math.max(VRM_ACTOR_SCALE_MIN, Math.min(VRM_ACTOR_SCALE_MAX, value))
}

export function loadVrmActorScale(): number {
  try {
    const raw = window.localStorage.getItem(VRM_ACTOR_SCALE_STORAGE_KEY)
    if (!raw) return VRM_ACTOR_SCALE_DEFAULT
    const parsed = Number(raw)
    if (!Number.isFinite(parsed)) return VRM_ACTOR_SCALE_DEFAULT
    return clampVrmActorScale(parsed)
  } catch {
    return VRM_ACTOR_SCALE_DEFAULT
  }
}

export function saveVrmActorScale(value: number): number {
  const clamped = clampVrmActorScale(value)
  try {
    window.localStorage.setItem(VRM_ACTOR_SCALE_STORAGE_KEY, String(clamped))
  } catch {
    // ignore storage failures
  }
  return clamped
}
