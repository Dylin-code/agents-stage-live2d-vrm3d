import { describe, expect, it } from 'vitest'

import { filterDirectoryEntries } from './directoryBrowserFilter'

describe('filterDirectoryEntries', () => {
  it('returns all entries when keyword is empty', () => {
    const entries = [
      { name: 'alpha', path: '/repo/alpha' },
      { name: 'beta', path: '/repo/beta' },
    ]

    expect(filterDirectoryEntries(entries, '')).toEqual(entries)
  })

  it('filters by directory name or path case-insensitively', () => {
    const entries = [
      { name: 'AgentTools', path: '/repo/AgentTools' },
      { name: 'Sandbox', path: '/repo/internal/sandbox' },
      { name: 'Docs', path: '/repo/docs' },
    ]

    expect(filterDirectoryEntries(entries, 'agent')).toEqual([
      { name: 'AgentTools', path: '/repo/AgentTools' },
    ])
    expect(filterDirectoryEntries(entries, 'INTERNAL')).toEqual([
      { name: 'Sandbox', path: '/repo/internal/sandbox' },
    ])
  })
})
