export function pickModelUrlForNewActor(
  preferredModelUrl: string | undefined,
  actors: Array<{ modelUrl: string }>,
  stageFixedVrmUrls: string[],
): string {
  if (preferredModelUrl) return preferredModelUrl
  const used = new Set(actors.map((actor) => actor.modelUrl))
  for (const modelUrl of stageFixedVrmUrls) {
    if (!used.has(modelUrl)) return modelUrl
  }
  return stageFixedVrmUrls[actors.length % stageFixedVrmUrls.length] || stageFixedVrmUrls[0] || ''
}

export function findOldestActor<T extends { mountedOrder: number }>(actors: T[]): T | null {
  if (!actors.length) return null
  let oldest = actors[0] || null
  if (!oldest) return null
  for (const actor of actors) {
    if (actor.mountedOrder < oldest.mountedOrder) {
      oldest = actor
    }
  }
  return oldest
}
