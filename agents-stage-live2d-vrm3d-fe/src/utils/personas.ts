import type { CharacterPersona } from '../types/message'

export function createCharacterPersonaId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `persona-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}

export function sanitizeCharacterPersona(raw: unknown): CharacterPersona | null {
  if (!raw || typeof raw !== 'object') return null
  const candidate = raw as Partial<CharacterPersona>
  const id = String(candidate.id || '').trim() || createCharacterPersonaId()
  const name = String(candidate.name || '').trim()
  const content = String(candidate.content || '')
  if (!name && !content.trim()) return null
  return {
    id,
    name: name || '未命名個性',
    content,
  }
}

export function sanitizeCharacterPersonas(raw: unknown): CharacterPersona[] {
  if (!Array.isArray(raw)) return []
  const seen = new Set<string>()
  const personas: CharacterPersona[] = []
  for (const item of raw) {
    const sanitized = sanitizeCharacterPersona(item)
    if (!sanitized || seen.has(sanitized.id)) continue
    seen.add(sanitized.id)
    personas.push(sanitized)
  }
  return personas
}

export function findCharacterPersonaById(
  personas: CharacterPersona[],
  personaId: string | undefined,
): CharacterPersona | undefined {
  const normalizedId = String(personaId || '').trim()
  if (!normalizedId) return undefined
  return personas.find((item) => item.id === normalizedId)
}

export function resolveSelectedCharacterPersona(
  personas: CharacterPersona[],
  personaId: string | undefined,
  fallbackName?: string,
  fallbackContent?: string,
): CharacterPersona | undefined {
  const matched = findCharacterPersonaById(personas, personaId)
  if (matched) return matched
  const normalizedId = String(personaId || '').trim()
  const normalizedName = String(fallbackName || '').trim()
  const normalizedContent = String(fallbackContent || '')
  if (!normalizedId || (!normalizedName && !normalizedContent.trim())) return undefined
  return {
    id: normalizedId,
    name: normalizedName || '已刪除的個性',
    content: normalizedContent,
  }
}
