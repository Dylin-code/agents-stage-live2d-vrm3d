import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  CHAT_DRAFT_STORAGE_KEY,
  CHAT_DRAFT_TTL_MS,
  CHAT_UI_STATE_STORAGE_KEY,
  SESSION_AGENT_OPTIONS_STORAGE_KEY,
  type StorageLike,
  clearChatDraft,
  createDebouncedWriter,
  loadChatDraft,
  loadChatUiState,
  loadSessionAgentOptionsMap,
  pruneStaleChatDrafts,
  saveChatDraft,
  saveChatUiState,
  saveSessionAgentOptionsMap,
} from './uiStateCache'

function createMemoryStorage(): StorageLike & { dump(): Record<string, string> } {
  const data = new Map<string, string>()
  return {
    getItem: (key) => (data.has(key) ? data.get(key)! : null),
    setItem: (key, value) => {
      data.set(key, value)
    },
    removeItem: (key) => {
      data.delete(key)
    },
    dump: () => Object.fromEntries(data.entries()),
  }
}

describe('chat draft cache', () => {
  let storage: ReturnType<typeof createMemoryStorage>

  beforeEach(() => {
    storage = createMemoryStorage()
  })

  it('returns empty string when no draft saved', () => {
    expect(loadChatDraft('session-1', storage)).toBe('')
  })

  it('saves and reloads draft for the matching key', () => {
    saveChatDraft('session-1', '寫到一半的訊息', storage, 1_700_000_000_000)
    saveChatDraft('session-2', 'another draft', storage, 1_700_000_000_000)

    expect(loadChatDraft('session-1', storage)).toBe('寫到一半的訊息')
    expect(loadChatDraft('session-2', storage)).toBe('another draft')
  })

  it('removes draft entry when value becomes empty', () => {
    saveChatDraft('session-1', 'draft', storage)
    saveChatDraft('session-1', '', storage)

    expect(loadChatDraft('session-1', storage)).toBe('')
    expect(storage.dump()[CHAT_DRAFT_STORAGE_KEY]).toBeUndefined()
  })

  it('clearChatDraft removes only the targeted key', () => {
    saveChatDraft('session-1', 'a', storage)
    saveChatDraft('session-2', 'b', storage)
    clearChatDraft('session-1', storage)

    expect(loadChatDraft('session-1', storage)).toBe('')
    expect(loadChatDraft('session-2', storage)).toBe('b')
  })

  it('prunes drafts older than the TTL', () => {
    const now = 2_000_000_000_000
    saveChatDraft('fresh', 'still here', storage, now)
    saveChatDraft('stale', 'should expire', storage, now - CHAT_DRAFT_TTL_MS - 1)

    pruneStaleChatDrafts(CHAT_DRAFT_TTL_MS, storage, now)

    expect(loadChatDraft('fresh', storage)).toBe('still here')
    expect(loadChatDraft('stale', storage)).toBe('')
  })

  it('ignores corrupted JSON without throwing', () => {
    storage.setItem(CHAT_DRAFT_STORAGE_KEY, '{not json')
    expect(loadChatDraft('session-1', storage)).toBe('')
  })

  it('does no work when conversationKey is empty', () => {
    saveChatDraft('', 'should not save', storage)
    expect(storage.dump()[CHAT_DRAFT_STORAGE_KEY]).toBeUndefined()
  })
})

describe('chat ui state cache', () => {
  let storage: ReturnType<typeof createMemoryStorage>

  beforeEach(() => {
    storage = createMemoryStorage()
  })

  it('round-trips selectedChatSessionId and chatModalVisible', () => {
    saveChatUiState({ selectedChatSessionId: 's-1', chatModalVisible: true }, storage)
    expect(loadChatUiState(storage)).toEqual({
      selectedChatSessionId: 's-1',
      chatModalVisible: true,
    })
  })

  it('removes the storage key when both fields are empty', () => {
    saveChatUiState({ selectedChatSessionId: 's-1', chatModalVisible: true }, storage)
    saveChatUiState({}, storage)
    expect(storage.dump()[CHAT_UI_STATE_STORAGE_KEY]).toBeUndefined()
    expect(loadChatUiState(storage)).toEqual({})
  })

  it('drops fields with the wrong type', () => {
    storage.setItem(
      CHAT_UI_STATE_STORAGE_KEY,
      JSON.stringify({ selectedChatSessionId: 123, chatModalVisible: 'yes' }),
    )
    expect(loadChatUiState(storage)).toEqual({})
  })
})

describe('session agent options map', () => {
  let storage: ReturnType<typeof createMemoryStorage>

  beforeEach(() => {
    storage = createMemoryStorage()
  })

  it('saves and reloads the map keyed by session id', () => {
    saveSessionAgentOptionsMap(
      {
        's-1': { model: 'gpt-5', permission_mode: 'default' },
        's-2': { model: 'sonnet', plan_mode: true },
      },
      storage,
    )
    expect(loadSessionAgentOptionsMap(storage)).toEqual({
      's-1': { model: 'gpt-5', permission_mode: 'default' },
      's-2': { model: 'sonnet', plan_mode: true },
    })
  })

  it('removes storage key when given an empty map', () => {
    saveSessionAgentOptionsMap({ 's-1': { model: 'gpt-5' } }, storage)
    saveSessionAgentOptionsMap({}, storage)
    expect(storage.dump()[SESSION_AGENT_OPTIONS_STORAGE_KEY]).toBeUndefined()
  })

  it('rejects non-object payloads gracefully', () => {
    storage.setItem(SESSION_AGENT_OPTIONS_STORAGE_KEY, JSON.stringify(['oops']))
    expect(loadSessionAgentOptionsMap(storage)).toEqual({})
  })
})

describe('createDebouncedWriter', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('coalesces rapid calls into one delayed invocation', () => {
    const sink = vi.fn<(value: string) => void>()
    const writer = createDebouncedWriter(sink, 100)

    writer.schedule('a')
    writer.schedule('b')
    writer.schedule('c')

    expect(sink).not.toHaveBeenCalled()
    vi.advanceTimersByTime(100)
    expect(sink).toHaveBeenCalledTimes(1)
    expect(sink).toHaveBeenCalledWith('c')
  })

  it('flush runs the latest pending call synchronously', () => {
    const sink = vi.fn<(value: string) => void>()
    const writer = createDebouncedWriter(sink, 100)

    writer.schedule('first')
    writer.schedule('latest')
    writer.flush()

    expect(sink).toHaveBeenCalledTimes(1)
    expect(sink).toHaveBeenCalledWith('latest')

    vi.advanceTimersByTime(100)
    expect(sink).toHaveBeenCalledTimes(1)
  })

  it('cancel clears pending calls', () => {
    const sink = vi.fn()
    const writer = createDebouncedWriter(sink, 100)
    writer.schedule('pending')
    writer.cancel()
    vi.advanceTimersByTime(100)
    expect(sink).not.toHaveBeenCalled()
  })
})
