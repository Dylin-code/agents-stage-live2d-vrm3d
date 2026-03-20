import { describe, expect, it } from 'vitest'
import {
  groupSessionsByCwd,
  getSessionActivityEpoch,
  isSessionVisibleOnStage,
  mergeHistorySessions,
  sortSessionsByActivityDesc,
  touchManualSummonTime,
} from './sessionStageState'

const base = '2026-02-27T10:00:00.000Z'

describe('mergeHistorySessions', () => {
  it('merges updates, keeps latest timestamp, sorts desc, and limits 20', () => {
    const existing = Array.from({ length: 20 }, (_, idx) => ({
      session_id: `s-${idx}`,
      display_name: `session-${idx}`,
      state: 'IDLE' as const,
      last_seen_at: `2026-02-27T09:${String(idx).padStart(2, '0')}:00.000Z`,
      active: idx % 2 === 0,
    }))
    const incoming = [
      {
        session_id: 's-3',
        display_name: 'session-3-new',
        state: 'RESPONDING' as const,
        last_seen_at: '2026-02-27T10:59:00.000Z',
        active: true,
      },
      {
        session_id: 's-new',
        display_name: 'session-new',
        state: 'THINKING' as const,
        last_seen_at: '2026-02-27T10:58:00.000Z',
        active: true,
      },
    ]

    const merged = mergeHistorySessions(existing, incoming, 20)
    expect(merged).toHaveLength(20)
    expect(merged[0].session_id).toBe('s-3')
    expect(merged[0].display_name).toBe('session-3-new')
    expect(merged[1].session_id).toBe('s-new')
  })

  it('keeps latest metadata fields while preserving newer activity timestamp', () => {
    const existing = [
      {
        session_id: 's-ctx',
        display_name: 'old',
        state: 'RESPONDING' as const,
        last_seen_at: '2026-02-27T11:00:00.000Z',
        active: true,
        cwd: '/repo/a',
        branch: 'main',
        manual_summoned_at: '2026-02-27T10:59:00.000Z',
      },
    ]
    const incoming = [
      {
        session_id: 's-ctx',
        display_name: 'new title',
        state: 'IDLE' as const,
        last_seen_at: '2026-02-27T10:00:00.000Z',
        active: false,
        cwd: '/repo/b',
        branch: 'feature-x',
      },
    ]
    const merged = mergeHistorySessions(existing, incoming, 20)
    expect(merged).toHaveLength(1)
    expect(merged[0].last_seen_at).toBe('2026-02-27T11:00:00.000Z')
    expect(merged[0].state).toBe('RESPONDING')
    expect(merged[0].display_name).toBe('old')
    expect(merged[0].cwd).toBe('/repo/b')
    expect(merged[0].branch).toBe('feature-x')
    expect(merged[0].manual_summoned_at).toBe('2026-02-27T10:59:00.000Z')
  })

  it('preserves real user input flag across history merges', () => {
    const existing = [
      {
        session_id: 's-real',
        display_name: 'session-12345678',
        state: 'RESPONDING' as const,
        last_seen_at: '2026-02-27T11:00:00.000Z',
        active: true,
        has_real_user_input: true,
      },
    ]
    const incoming = [
      {
        session_id: 's-real',
        display_name: 'session-12345678',
        state: 'IDLE' as const,
        last_seen_at: '2026-02-27T10:00:00.000Z',
        active: false,
        has_real_user_input: false,
      },
    ]

    const merged = mergeHistorySessions(existing, incoming, 20)
    expect(merged).toHaveLength(1)
    expect(merged[0].has_real_user_input).toBe(true)
  })
})

describe('isSessionVisibleOnStage', () => {
  it('uses manual summon time to keep role visible within leave window', () => {
    const manualSummonedAt = '2026-02-27T10:05:00.000Z'
    const now = Date.parse('2026-02-27T10:12:00.000Z')
    expect(isSessionVisibleOnStage(base, manualSummonedAt, 10, now)).toBe(true)
  })

  it('hides role when both activity and summon are older than leave window', () => {
    const manualSummonedAt = '2026-02-27T09:10:00.000Z'
    const now = Date.parse('2026-02-27T10:12:00.000Z')
    expect(isSessionVisibleOnStage(base, manualSummonedAt, 10, now)).toBe(false)
  })
})

describe('touchManualSummonTime', () => {
  it('updates manual summon timestamp with provided time', () => {
    const updated = touchManualSummonTime(
      {
        session_id: 's-1',
        display_name: 'session-1',
        state: 'IDLE',
        last_seen_at: base,
        active: false,
      },
      '2026-02-27T10:30:00.000Z',
    )
    expect(updated.manual_summoned_at).toBe('2026-02-27T10:30:00.000Z')
  })
})

describe('sortSessionsByActivityDesc', () => {
  it('sorts by latest visible activity across last_seen and manual summon time', () => {
    const sessions = sortSessionsByActivityDesc([
      {
        session_id: 'codex-older',
        display_name: 'codex older',
        state: 'IDLE',
        last_seen_at: '2026-02-27T11:00:00.000Z',
        manual_summoned_at: '2026-02-27T11:04:00.000Z',
        active: true,
      },
      {
        session_id: 'claude-newer',
        display_name: 'claude newer',
        state: 'RESPONDING',
        last_seen_at: '2026-02-27T11:03:00.000Z',
        active: true,
      },
      {
        session_id: 'codex-newest',
        display_name: 'codex newest',
        state: 'THINKING',
        last_seen_at: '2026-02-27T11:02:00.000Z',
        manual_summoned_at: '2026-02-27T11:05:00.000Z',
        active: true,
      },
    ])

    expect(sessions.map((session) => session.session_id)).toEqual([
      'codex-newest',
      'codex-older',
      'claude-newer',
    ])
    expect(getSessionActivityEpoch(sessions[0])).toBe(Date.parse('2026-02-27T11:05:00.000Z'))
  })
})

describe('groupSessionsByCwd', () => {
  it('groups sessions by cwd basename and keeps latest group first', () => {
    const groups = groupSessionsByCwd([
      {
        session_id: 's-1',
        display_name: 'a',
        state: 'IDLE',
        last_seen_at: '2026-02-27T11:00:00.000Z',
        active: true,
        cwd: '/repo/live2d-assistant',
        cwd_basename: 'live2d-assistant',
      },
      {
        session_id: 's-2',
        display_name: 'b',
        state: 'IDLE',
        last_seen_at: '2026-02-27T10:59:00.000Z',
        active: true,
        cwd: '/repo/live2d-assistant',
        cwd_basename: 'live2d-assistant',
      },
      {
        session_id: 's-3',
        display_name: 'c',
        state: 'RESPONDING',
        last_seen_at: '2026-02-27T11:05:00.000Z',
        active: true,
        cwd: '/repo/sporty-ai-playbook',
        cwd_basename: 'sporty-ai-playbook',
      },
    ])
    expect(groups).toHaveLength(2)
    expect(groups[0].cwd_basename).toBe('sporty-ai-playbook')
    expect(groups[0].sessions).toHaveLength(1)
    expect(groups[1].cwd_basename).toBe('live2d-assistant')
    expect(groups[1].sessions).toHaveLength(2)
  })
})
