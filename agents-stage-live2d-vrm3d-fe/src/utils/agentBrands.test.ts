import { describe, expect, it } from 'vitest'

import {
  DEFAULT_AGENT_BRANDS,
  buildAgentBrandCatalog,
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
      },
    ])
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
