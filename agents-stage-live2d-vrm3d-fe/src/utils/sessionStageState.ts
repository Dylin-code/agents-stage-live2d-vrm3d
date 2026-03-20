import type { SessionRuntimeContext, SessionState } from '../types/sessionState'

export interface SessionHistoryLike {
  session_id: string
  display_name: string
  state: SessionState
  last_seen_at: string
  active: boolean
  has_real_user_input?: boolean
  originator?: string
  cwd?: string
  cwd_basename?: string
  branch?: string
  last_event_type?: string
  context?: SessionRuntimeContext
  inactive?: boolean
  manual_summoned_at?: string
}

export interface SessionGroupLike<T extends SessionHistoryLike = SessionHistoryLike> {
  key: string
  cwd: string
  cwd_basename: string
  sessions: T[]
}

function toEpoch(value: string): number {
  const ts = Date.parse(value)
  return Number.isFinite(ts) ? ts : 0
}

function compareByLastSeenDesc(a: SessionHistoryLike, b: SessionHistoryLike): number {
  return toEpoch(b.last_seen_at) - toEpoch(a.last_seen_at)
}

export function getSessionActivityEpoch(session: SessionHistoryLike): number {
  return Math.max(toEpoch(session.last_seen_at), toEpoch(session.manual_summoned_at || ''))
}

export function sortSessionsByActivityDesc<T extends SessionHistoryLike>(sessions: T[]): T[] {
  return [...sessions].sort((a, b) => getSessionActivityEpoch(b) - getSessionActivityEpoch(a))
}

export function mergeHistorySessions(
  existing: SessionHistoryLike[],
  incoming: SessionHistoryLike[],
  limit = 20,
): SessionHistoryLike[] {
  const map = new Map<string, SessionHistoryLike>()
  for (const item of existing) {
    map.set(item.session_id, { ...item })
  }
  for (const item of incoming) {
    const current = map.get(item.session_id)
    if (!current) {
      map.set(item.session_id, { ...item })
      continue
    }
    const keep = toEpoch(current.last_seen_at) > toEpoch(item.last_seen_at)
      ? {
          ...current,
          ...item,
          last_seen_at: current.last_seen_at,
          state: current.state,
          active: current.active,
          has_real_user_input: current.has_real_user_input || item.has_real_user_input,
          display_name: current.display_name || item.display_name,
          manual_summoned_at: current.manual_summoned_at || item.manual_summoned_at,
        }
      : {
          ...current,
          ...item,
          has_real_user_input: current.has_real_user_input || item.has_real_user_input,
          manual_summoned_at: current.manual_summoned_at || item.manual_summoned_at,
        }
    map.set(item.session_id, keep)
  }
  return [...map.values()]
    .sort(compareByLastSeenDesc)
    .slice(0, Math.max(1, limit))
}

export function isSessionVisibleOnStage(
  lastSeenAt: string,
  manualSummonedAt: string | undefined,
  leaveAfterMinutes: number,
  nowMs = Date.now(),
): boolean {
  const ttlMs = Math.max(1, leaveAfterMinutes) * 60 * 1000
  const lastSeenMs = toEpoch(lastSeenAt)
  const summonMs = manualSummonedAt ? toEpoch(manualSummonedAt) : 0
  const lastActivity = Math.max(lastSeenMs, summonMs)
  if (!lastActivity) {
    return false
  }
  return nowMs - lastActivity < ttlMs
}

export function touchManualSummonTime(
  session: SessionHistoryLike,
  isoNow = new Date().toISOString(),
): SessionHistoryLike {
  return {
    ...session,
    manual_summoned_at: isoNow,
  }
}

export function groupSessionsByCwd<T extends SessionHistoryLike>(
  sessions: T[],
): SessionGroupLike<T>[] {
  const grouped = new Map<string, SessionGroupLike<T>>()
  for (const session of sessions) {
    const cwd = (session.cwd || '').trim()
    const cwdBasename = (session.cwd_basename || '').trim() || (cwd ? cwd.split('/').filter(Boolean).pop() || cwd : 'unknown')
    const key = cwd || `__unknown__${cwdBasename}`
    const bucket = grouped.get(key)
    if (bucket) {
      bucket.sessions.push(session)
      continue
    }
    grouped.set(key, {
      key,
      cwd,
      cwd_basename: cwdBasename,
      sessions: [session],
    })
  }
  return [...grouped.values()].sort((a, b) => {
    const aTs = toEpoch(a.sessions[0]?.last_seen_at || '')
    const bTs = toEpoch(b.sessions[0]?.last_seen_at || '')
    return bTs - aTs
  })
}
