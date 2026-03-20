export function deriveStableVrm3dVisibleSessionIds(
  currentIds: string[],
  activitySortedSessions: Array<{ session_id: string }>,
  maxVisible: number,
): string[] {
  const desiredIds = activitySortedSessions
    .slice(0, Math.max(0, maxVisible))
    .map((session) => session.session_id)
  const desiredSet = new Set(desiredIds)
  const nextIds = currentIds.filter((sessionId) => desiredSet.has(sessionId))
  for (const sessionId of desiredIds) {
    if (!nextIds.includes(sessionId)) {
      nextIds.push(sessionId)
    }
  }
  return nextIds.slice(0, Math.max(0, maxVisible))
}
