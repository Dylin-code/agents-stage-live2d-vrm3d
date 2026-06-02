export interface AgentBrandCatalogItem {
  brand: string
  display_name: string
  badge_icon: string
  models: string[]
  default_permission_mode?: string
}

export const CODEX_AGENT_MODELS = [
  'gpt-5.5',
  'gpt-5.5-pro',
  'gpt-5.4',
  'gpt-5.4-mini',
  'gpt-5.4-nano',
  'gpt-5.3-codex',
  'gpt-5.2-codex',
  'gpt-5.1-codex-max',
  'gpt-5.2',
]

export const CLAUDE_AGENT_MODELS = [
  'claude-opus-4-7',
  'claude-sonnet-4-6',
  'claude-opus-4-6',
  'claude-haiku-4-5-20251001',
  'sonnet',
  'opus',
  'haiku',
]

export const OPENCODE_AGENT_MODELS = [
  'opencode/deepseek-v4-flash-free',
  'opencode/minimax-m3-free',
  'opencode/mimo-v2.5-free',
  'opencode/gpt-5.5',
  'opencode/gpt-5.4',
  'opencode/gpt-5.4-mini',
  'opencode/gpt-5.4-nano',
  'opencode/gpt-5.3-codex',
  'opencode/big-pickle',
  'ollama-cloud/deepseek-v4-flash',
  'ollama-cloud/gemini-3-flash-preview',
  'ollama-cloud/claude-sonnet-4-6',
  'ollama-cloud/claude-opus-4-7',
]

export const DEFAULT_AGENT_BRANDS: AgentBrandCatalogItem[] = [
  {
    brand: 'codex',
    display_name: 'Codex',
    badge_icon: '/brand/codex-badge.svg',
    models: CODEX_AGENT_MODELS,
    default_permission_mode: 'default',
  },
  {
    brand: 'claude',
    display_name: 'Claude',
    badge_icon: '/brand/claude-badge.svg',
    models: CLAUDE_AGENT_MODELS,
    default_permission_mode: 'default',
  },
  {
    brand: 'opencode',
    display_name: 'OpenCode',
    badge_icon: '/brand/opencode-badge.svg',
    models: OPENCODE_AGENT_MODELS,
    default_permission_mode: 'default',
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
      const defaultPermissionMode = String(record.default_permission_mode || '').trim().toLowerCase() || 'default'
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
        default_permission_mode: defaultPermissionMode,
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

export function getAgentBrandDefaultPermissionMode(catalog: AgentBrandCatalogItem[], brand?: string): string {
  const normalized = String(brand || '').trim().toLowerCase()
  const target = catalog.find((item) => item.brand === normalized)
  return String(target?.default_permission_mode || 'default')
}
