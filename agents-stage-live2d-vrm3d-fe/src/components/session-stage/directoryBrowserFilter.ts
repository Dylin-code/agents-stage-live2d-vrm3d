import type { SessionBridgeDirectoryEntry } from '../../utils/api/sessionBridge'

export function filterDirectoryEntries(
  entries: SessionBridgeDirectoryEntry[],
  keyword: string,
): SessionBridgeDirectoryEntry[] {
  const normalizedKeyword = keyword.trim().toLocaleLowerCase()
  if (!normalizedKeyword) {
    return entries
  }
  return entries.filter((entry) => {
    const name = String(entry.name || '').toLocaleLowerCase()
    const path = String(entry.path || '').toLocaleLowerCase()
    return name.includes(normalizedKeyword) || path.includes(normalizedKeyword)
  })
}
