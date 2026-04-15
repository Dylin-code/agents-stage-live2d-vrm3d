import { describe, expect, it } from 'vitest'

import {
  DEFAULT_AGENT_BRANDS,
  buildAgentBrandCatalog,
  getAgentBrandDefaultPermissionMode,
  getAgentBrandModels,
  normalizeAgentBrandCatalog,
} from './agentBrands'

describe('normalizeAgentBrandCatalog', () => {
  it('keeps valid server brand metadata', () => {
    const result = normalizeAgentBrandCatalog([
      {
        brand: 'claude',
        display_name: 'Claude',
        badge_icon: '/brand/claude-badge.svg',
        models: ['claude-sonnet-4-6'],
      },
    ])

    expect(result).toEqual([
      {
        brand: 'claude',
        display_name: 'Claude',
        badge_icon: '/brand/claude-badge.svg',
        models: ['claude-sonnet-4-6'],
        default_permission_mode: 'default',
      },
    ])
  })

  it('drops invalid items and trims strings', () => {
    const result = normalizeAgentBrandCatalog([
      {
        brand: ' codex ',
        display_name: ' Codex ',
        badge_icon: ' /brand/codex-badge.svg ',
        models: [' gpt-5.3-codex ', '', 1],
      },
      null,
      {
        brand: '',
        display_name: 'Invalid',
        badge_icon: '',
        models: [],
      },
    ])

    expect(result).toEqual([
      {
        brand: 'codex',
        display_name: 'Codex',
        badge_icon: '/brand/codex-badge.svg',
        models: ['gpt-5.3-codex'],
        default_permission_mode: 'default',
      },
    ])
  })

  it('keeps brand-specific default permission mode from server metadata', () => {
    const result = normalizeAgentBrandCatalog([
      {
        brand: 'codex',
        display_name: 'Codex',
        badge_icon: '/brand/codex-badge.svg',
        models: ['gpt-5.4'],
        default_permission_mode: 'full',
      },
    ])

    expect(result[0]?.default_permission_mode).toBe('full')
  })
})

describe('buildAgentBrandCatalog', () => {
  it('prefers server catalog when present', () => {
    const catalog = buildAgentBrandCatalog([
      {
        brand: 'copilot',
        display_name: 'GitHub Copilot',
        badge_icon: '/brand/copilot-badge.svg',
        models: ['gpt-5'],
      },
    ])

    expect(catalog).toHaveLength(1)
    expect(catalog[0].brand).toBe('copilot')
  })

  it('falls back to built-in brand catalog when server response is empty', () => {
    expect(buildAgentBrandCatalog([])).toEqual(DEFAULT_AGENT_BRANDS)
    expect(buildAgentBrandCatalog(undefined)).toEqual(DEFAULT_AGENT_BRANDS)
  })
})

describe('getAgentBrandModels', () => {
  it('returns brand-specific models from catalog', () => {
    const catalog = buildAgentBrandCatalog([
      {
        brand: 'claude',
        display_name: 'Claude',
        badge_icon: '/brand/claude-badge.svg',
        models: ['claude-opus-4-6'],
      },
    ])

    expect(getAgentBrandModels(catalog, 'claude')).toEqual(['claude-opus-4-6'])
  })

  it('falls back to codex models when brand is missing', () => {
    expect(getAgentBrandModels(DEFAULT_AGENT_BRANDS, 'missing-brand')).toEqual(DEFAULT_AGENT_BRANDS[0].models)
  })
})

describe('getAgentBrandDefaultPermissionMode', () => {
  it('returns brand-specific default permission mode from catalog', () => {
    const catalog = buildAgentBrandCatalog([
      {
        brand: 'codex',
        display_name: 'Codex',
        badge_icon: '/brand/codex-badge.svg',
        models: ['gpt-5.4'],
        default_permission_mode: 'full',
      },
    ])

    expect(getAgentBrandDefaultPermissionMode(catalog, 'codex')).toBe('full')
  })

  it('falls back to default when brand metadata is missing', () => {
    expect(getAgentBrandDefaultPermissionMode(DEFAULT_AGENT_BRANDS, 'missing-brand')).toBe('default')
  })
})
