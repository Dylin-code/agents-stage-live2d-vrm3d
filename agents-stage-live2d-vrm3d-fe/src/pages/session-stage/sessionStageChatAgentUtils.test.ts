import { describe, expect, it } from 'vitest'
import { ref } from 'vue'
import type { Conversation, SystemSettings } from '../../types/message'
import type { SessionSnapshotItem } from '../../types/sessionState'
import { createSessionStageChatAgentUtils } from './sessionStageChatAgentUtils'

Object.defineProperty(globalThis, 'fetch', {
  value: async () => ({
    ok: true,
    json: async () => ({ messages: [], branches: [], current: '' }),
  }),
  configurable: true,
})

const storage = new Map<string, string>()
Object.defineProperty(globalThis, 'localStorage', {
  value: {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => {
      storage.set(key, String(value))
    },
    removeItem: (key: string) => {
      storage.delete(key)
    },
    clear: () => {
      storage.clear()
    },
  },
  configurable: true,
})

function createSession(sessionId: string, displayName: string): SessionSnapshotItem {
  return {
    session_id: sessionId,
    display_name: displayName,
    state: 'IDLE',
    last_seen_at: new Date().toISOString(),
    active: true,
    inactive: false,
    summary: displayName,
    context: {},
  }
}

describe('sessionStageChatAgentUtils', () => {
  it('does not override the current chat when switching is not forced', () => {
    const sessionStore: Record<string, SessionSnapshotItem> = {
      s1: createSession('s1', 'Session 1'),
      s2: createSession('s2', 'Session 2'),
    }
    const selectedChatSessionId = ref('s2')
    const chatModalVisible = ref(true)
    const chatConversation = ref<Conversation>({
      key: 's2',
      label: 'Session 2',
      messages: [],
      createdAt: 1,
      updatedAt: 1,
      group: undefined,
    })
    const chatSystemSettings = ref<SystemSettings>({} as SystemSettings)
    const timers = new Map<string, number>()

    const utils = createSessionStageChatAgentUtils({
      storageKeyConversations: 'test-conversations',
      conversationLimit: 20,
      conversationSyncDebounceMs: 0,
      serverUrl: () => '',
      sessionStore,
      sessionAgentOptionsBySession: {},
      selectedChatSessionId,
      chatModalVisible,
      chatConversation,
      chatSystemSettings,
      conversationSyncTimers: timers,
      conversationSyncRunning: new Set<string>(),
      conversationSyncQueued: new Set<string>(),
      ensureSessionVisible: () => {},
      syncActorsWithVisibility: () => {},
      getBrandModels: () => [],
    })

    utils.openSessionChat('s1')

    expect(selectedChatSessionId.value).toBe('s2')
    expect(chatConversation.value.key).toBe('s2')
  })

  it('switches chat when the caller forces it', () => {
    const sessionStore: Record<string, SessionSnapshotItem> = {
      s1: createSession('s1', 'Session 1'),
      s2: createSession('s2', 'Session 2'),
    }
    const selectedChatSessionId = ref('s2')
    const chatModalVisible = ref(true)
    const chatConversation = ref<Conversation>({
      key: 's2',
      label: 'Session 2',
      messages: [],
      createdAt: 1,
      updatedAt: 1,
      group: undefined,
    })
    const chatSystemSettings = ref<SystemSettings>({} as SystemSettings)
    const timers = new Map<string, number>()

    const utils = createSessionStageChatAgentUtils({
      storageKeyConversations: 'test-conversations-2',
      conversationLimit: 20,
      conversationSyncDebounceMs: 0,
      serverUrl: () => '',
      sessionStore,
      sessionAgentOptionsBySession: {},
      selectedChatSessionId,
      chatModalVisible,
      chatConversation,
      chatSystemSettings,
      conversationSyncTimers: timers,
      conversationSyncRunning: new Set<string>(),
      conversationSyncQueued: new Set<string>(),
      ensureSessionVisible: () => {},
      syncActorsWithVisibility: () => {},
      getBrandModels: () => [],
    })

    utils.openSessionChat('s1', { forceSwitch: true })

    expect(selectedChatSessionId.value).toBe('s1')
    expect(chatConversation.value.key).toBe('s1')
  })
})
