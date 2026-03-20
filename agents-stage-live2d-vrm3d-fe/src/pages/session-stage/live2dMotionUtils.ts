import type { AvatarActor, SessionSnapshotItem, SessionState } from '../../types/sessionState'

export interface ModelMotionEntry {
  group: string
  index: number
  name: string
  key: string
}

const STATE_MOTION_CANDIDATES: Record<SessionState, string[]> = {
  IDLE: ['idle', 'main_1', 'home'],
  THINKING: ['main_2', 'main_1', 'touch_head', 'taphead', 'tap', 'idle'],
  TOOLING: ['mission', 'touch_body', 'touch_special', 'effect', 'tap', 'idle'],
  RESPONDING: ['main_3', 'main_2', 'main_1', 'home', 'taphead', 'idle'],
  WAITING: ['mail', 'home', 'login', 'tick_5', 'idle'],
}

const EVENT_MOTION_CANDIDATES: Record<string, string[]> = {
  session_meta: ['login', 'home', 'idle'],
  turn_context: ['home', 'idle'],
  user_message: ['home', 'mail', 'idle'],
  agent_reasoning: ['main_2', 'main_1', 'touch_head', 'taphead', 'tap', 'idle'],
  reasoning: ['main_2', 'main_1', 'touch_head', 'taphead', 'tap', 'idle'],
  function_call: ['mission', 'touch_body', 'touch_special', 'effect', 'tap', 'idle'],
  custom_tool_call: ['mission', 'touch_body', 'touch_special', 'effect', 'tap', 'idle'],
  function_call_output: ['mission', 'main_2', 'idle'],
  agent_message: ['main_3', 'main_2', 'main_1', 'home', 'taphead', 'idle'],
  message: ['main_3', 'main_2', 'main_1', 'home', 'taphead', 'idle'],
  error: ['mail', 'idle'],
  task_complete: ['complete', 'mission_complete', 'wedding', 'idle'],
}

const EVENT_SEMANTIC_SLOTS: Record<string, string> = {
  session_meta: 'IDLE',
  turn_context: 'IDLE',
  user_message: 'WAITING',
  agent_reasoning: 'THINKING',
  reasoning: 'THINKING',
  function_call: 'TOOLING',
  custom_tool_call: 'TOOLING',
  function_call_output: 'TOOLING',
  agent_message: 'RESPONDING',
  message: 'RESPONDING',
  error: 'ERROR',
  task_complete: 'COMPLETE',
}

export const SUMMON_MOTION_CANDIDATES = ['wave', 'greet', 'hello', 'main_4', 'touch_special', 'touch_body', 'tap', 'idle']

export function normalizeModelPaths(input: unknown, fallback: string[]): string[] {
  const value = input ?? fallback
  const raw =
    typeof value === 'string'
      ? value.split(',')
      : Array.isArray(value)
      ? value
      : fallback
  const cleaned = raw
    .map((x) => String(x ?? '').trim())
    .filter((x) => x.length > 0)
  return cleaned.length > 0 ? Array.from(new Set(cleaned)) : fallback
}

function hashSessionId(sessionId: string): number {
  let hash = 2166136261
  for (let idx = 0; idx < sessionId.length; idx += 1) {
    hash ^= sessionId.charCodeAt(idx)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

export function pickModelPathForSession(
  sessionId: string,
  args: {
    modelPaths: string[]
    sessionModelAssignments: Map<string, string>
    sessionStore: Record<string, SessionSnapshotItem>
    fallbackModelPath: string
  },
): string {
  const { modelPaths, sessionModelAssignments, sessionStore, fallbackModelPath } = args
  if (modelPaths.length === 0) {
    return fallbackModelPath
  }
  const existing = sessionModelAssignments.get(sessionId)
  if (existing && modelPaths.includes(existing)) {
    return existing
  }

  const usage = new Map<string, number>()
  for (const path of modelPaths) {
    usage.set(path, 0)
  }
  for (const [sid, path] of sessionModelAssignments.entries()) {
    if (!modelPaths.includes(path)) continue
    const item = sessionStore[sid]
    if (!item || item.inactive) continue
    usage.set(path, (usage.get(path) || 0) + 1)
  }

  const notUsed = modelPaths.filter((path) => (usage.get(path) || 0) === 0)
  const hash = hashSessionId(sessionId)
  let chosen = ''
  if (notUsed.length > 0) {
    chosen = notUsed[hash % notUsed.length]
  } else {
    const minUsage = Math.min(...Array.from(usage.values()))
    const candidates = modelPaths.filter((path) => (usage.get(path) || 0) === minUsage)
    chosen = candidates[hash % candidates.length]
  }
  sessionModelAssignments.set(sessionId, chosen)
  return chosen
}

export function normalizeMotionToken(value: string): string {
  return (value || '').trim().toLowerCase()
}

export function getSemanticMotionCandidates(modelPath: string, slot: string, semanticMapByPath: Map<string, Record<string, string[]>>): string[] {
  if (!slot) return []
  const mapping = semanticMapByPath.get(modelPath)
  if (!mapping) return []
  const values = mapping[slot]
  if (!Array.isArray(values)) return []
  return values
    .map((x) => String(x || '').trim())
    .filter((x) => x.length > 0)
}

export function findMotionByCandidates(
  modelMotions: ModelMotionEntry[],
  candidates: string[],
): ModelMotionEntry | null {
  if (modelMotions.length === 0) return null
  for (const candidate of candidates) {
    const target = normalizeMotionToken(candidate)
    if (!target) continue
    const exact = modelMotions.find((item) => {
      const motionName = normalizeMotionToken(item.name)
      const motionGroup = normalizeMotionToken(item.group)
      return motionName === target || motionGroup === target
    })
    if (exact) return exact
    const fuzzy = modelMotions.find((item) => normalizeMotionToken(item.name).startsWith(target))
    if (fuzzy) return fuzzy
  }
  return null
}

export function pickMotionForState(
  args: {
    state: SessionState
    actorModelPath: string
    lastEventType?: string
    modelMotionsByPath: Map<string, ModelMotionEntry[]>
    modelSemanticMotionsByPath: Map<string, Record<string, string[]>>
  },
): ModelMotionEntry | null {
  const { state, actorModelPath, lastEventType, modelMotionsByPath, modelSemanticMotionsByPath } = args
  const modelMotions = modelMotionsByPath.get(actorModelPath) || []
  const eventKey = normalizeMotionToken(lastEventType || '')
  const semanticEventSlot = EVENT_SEMANTIC_SLOTS[eventKey] || ''
  const semanticEventCandidates = getSemanticMotionCandidates(actorModelPath, semanticEventSlot, modelSemanticMotionsByPath)
  const semanticStateCandidates = getSemanticMotionCandidates(actorModelPath, state, modelSemanticMotionsByPath)
  const eventCandidates = EVENT_MOTION_CANDIDATES[eventKey] || []
  const stateCandidates = STATE_MOTION_CANDIDATES[state]
  const matched = findMotionByCandidates(modelMotions, [
    ...semanticEventCandidates,
    ...semanticStateCandidates,
    ...eventCandidates,
    ...stateCandidates,
  ])
  if (matched) return matched
  const idleFallback = findMotionByCandidates(modelMotions, ['idle', 'main_1', 'main'])
  return idleFallback || modelMotions[0] || null
}

export function pickRandomSupportedMotion(actor: AvatarActor, modelMotionsByPath: Map<string, ModelMotionEntry[]>): ModelMotionEntry | null {
  const modelMotions = modelMotionsByPath.get(actor.model_path) || []
  if (modelMotions.length === 0) return null
  const nonRepeating = modelMotions.filter((item) => item.key !== actor.last_motion)
  const pool = nonRepeating.length > 0 ? nonRepeating : modelMotions
  const randomIndex = Math.floor(Math.random() * pool.length)
  return pool[randomIndex] || null
}

export function pickNextModelPath(currentPath: string, modelPaths: string[]): string | null {
  if (modelPaths.length <= 1) return null
  const start = modelPaths.indexOf(currentPath)
  const startIndex = start >= 0 ? start : 0
  for (let step = 1; step <= modelPaths.length; step += 1) {
    const candidate = modelPaths[(startIndex + step) % modelPaths.length]
    if (candidate !== currentPath) return candidate
  }
  return null
}
