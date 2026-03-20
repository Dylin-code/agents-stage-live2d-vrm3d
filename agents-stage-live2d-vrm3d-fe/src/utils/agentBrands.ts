export interface AgentBrandCatalogItem {
  brand: string
  display_name: string
  badge_icon: string
  models: string[]
}

export const DEFAULT_AGENT_BRANDS: AgentBrandCatalogItem[] = [
  {
    brand: 'codex',
    display_name: 'Codex',
    badge_icon: '/brand/codex-badge.svg',
    models: ['gpt-5.3-codex', 'gpt-5.4', 'gpt-5.2-codex', 'gpt-5.1-codex-max', 'gpt-5.2'],
  },
  {
    brand: 'claude',
    display_name: 'Claude',
    badge_icon: '/brand/claude-badge.svg',
    models: ['claude-sonnet-4-6', 'claude-opus-4-6', 'claude-haiku-4-5-20251001', 'sonnet', 'opus', 'haiku'],
  },
]

export function normalizeAgentBrandCatalog(input: unknown): AgentBrandCatalogItem[] {
  if (!Array.isArray(input)) return []

  return input
    .map((item) => {
      if (!item || typeof item !== 'object') return null
      const record = item as Record<string, unknown>
      const brand = String(record.brand || '').trim().toLowerCase()
      const displayName = String(record.display_name || '').trim()
      const badgeIcon = String(record.badge_icon || '').trim()
      const models = Array.isArray(record.models)
        ? record.models
            .filter((model): model is string => typeof model === 'string')
            .map((model) => model.trim())
            .filter(Boolean)
        : []
      if (!brand || !displayName || !badgeIcon || models.length === 0) return null
      return {
        brand,
        display_name: displayName,
        badge_icon: badgeIcon,
        models,
      }
    })
    .filter((item): item is AgentBrandCatalogItem => !!item)
}

export function buildAgentBrandCatalog(input: unknown): AgentBrandCatalogItem[] {
  const normalized = normalizeAgentBrandCatalog(input)
  return normalized.length > 0 ? normalized : DEFAULT_AGENT_BRANDS
}

export function getAgentBrandModels(catalog: AgentBrandCatalogItem[], brand?: string): string[] {
  const normalized = String(brand || '').trim().toLowerCase()
  const target = catalog.find((item) => item.brand === normalized)
  if (target) return target.models
  return catalog[0]?.models || []
}
