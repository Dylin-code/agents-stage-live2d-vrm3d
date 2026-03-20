export const VRM_GLOBAL_GROUND_OFFSET_STORAGE_KEY = 'vrm-stage-global-ground-offset-v1'
export const VRM_GLOBAL_GROUND_OFFSET_EVENT = 'session-stage:vrm-ground-offset-change'
export const VRM_GLOBAL_GROUND_OFFSET_MIN = -0.3
export const VRM_GLOBAL_GROUND_OFFSET_MAX = 0.3
export const VRM_GLOBAL_GROUND_OFFSET_STEP = 0.005

export function clampVrmGlobalGroundOffset(value: number): number {
  return Math.max(VRM_GLOBAL_GROUND_OFFSET_MIN, Math.min(VRM_GLOBAL_GROUND_OFFSET_MAX, value))
}

export function loadVrmGlobalGroundOffset(): number {
  try {
    const raw = window.localStorage.getItem(VRM_GLOBAL_GROUND_OFFSET_STORAGE_KEY)
    if (!raw) return 0
    const parsed = Number(raw)
    if (!Number.isFinite(parsed)) return 0
    return clampVrmGlobalGroundOffset(parsed)
  } catch {
    return 0
  }
}

export function saveVrmGlobalGroundOffset(value: number): number {
  const clamped = clampVrmGlobalGroundOffset(value)
  try {
    window.localStorage.setItem(VRM_GLOBAL_GROUND_OFFSET_STORAGE_KEY, String(clamped))
  } catch {
    // ignore storage failures
  }
  return clamped
}
