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

  it('context model overrides cached model in syncSessionAgentOptionsFromSnapshot', () => {
    const sessionAgentOptionsBySession: Record<string, any> = {
      s1: { model: 'haiku', reasoning_effort: 'low', permission_mode: 'default' },
    }
    const sessionStore: Record<string, SessionSnapshotItem> = {
      s1: {
        ...createSession('s1', 'Session 1'),
        context: { model: 'opus', effort: 'high', permission_mode: 'full' },
      },
    }

    const utils = createSessionStageChatAgentUtils({
      storageKeyConversations: 'test-conversations-model',
      conversationLimit: 20,
      conversationSyncDebounceMs: 0,
      serverUrl: () => '',
      sessionStore,
      sessionAgentOptionsBySession,
      selectedChatSessionId: ref(''),
      chatModalVisible: ref(false),
      chatConversation: ref<Conversation>({
        key: '',
        label: '',
        messages: [],
        createdAt: 0,
        updatedAt: 0,
        group: undefined,
      }),
      chatSystemSettings: ref<SystemSettings>({} as SystemSettings),
      conversationSyncTimers: new Map<string, number>(),
      conversationSyncRunning: new Set<string>(),
      conversationSyncQueued: new Set<string>(),
      ensureSessionVisible: () => {},
      syncActorsWithVisibility: () => {},
      getBrandModels: () => [],
    })

    utils.syncSessionAgentOptionsFromSnapshot(sessionStore.s1)

    expect(sessionAgentOptionsBySession.s1.model).toBe('opus')
    expect(sessionAgentOptionsBySession.s1.reasoning_effort).toBe('high')
    expect(sessionAgentOptionsBySession.s1.permission_mode).toBe('full')
  })

  it('does not infer full permission from danger sandbox alone', () => {
    const sessionStore: Record<string, SessionSnapshotItem> = {
      s1: {
        ...createSession('s1', 'Session 1'),
        context: { sandbox_mode: 'danger-full-access' },
      },
    }

    const utils = createSessionStageChatAgentUtils({
      storageKeyConversations: 'test-conversations-danger-sandbox',
      conversationLimit: 20,
      conversationSyncDebounceMs: 0,
      serverUrl: () => '',
      sessionStore,
      sessionAgentOptionsBySession: {},
      selectedChatSessionId: ref(''),
      chatModalVisible: ref(false),
      chatConversation: ref<Conversation>({
        key: '',
        label: '',
        messages: [],
        createdAt: 0,
        updatedAt: 0,
        group: undefined,
      }),
      chatSystemSettings: ref<SystemSettings>({} as SystemSettings),
      conversationSyncTimers: new Map<string, number>(),
      conversationSyncRunning: new Set<string>(),
      conversationSyncQueued: new Set<string>(),
      ensureSessionVisible: () => {},
      syncActorsWithVisibility: () => {},
      getBrandModels: () => [],
    })

    expect(utils.buildSessionAgentOptions(sessionStore.s1).permission_mode).toBe('default')
  })

  it('hydrates persona fields from session context', () => {
    const sessionStore: Record<string, SessionSnapshotItem> = {
      s1: {
        ...createSession('s1', 'Session 1'),
        context: {
          persona_id: 'persona-1',
          persona_name: '冷靜 PM',
          persona_content: '請條理清楚地回覆。',
        },
      },
    }

    const utils = createSessionStageChatAgentUtils({
      storageKeyConversations: 'test-conversations-3',
      conversationLimit: 20,
      conversationSyncDebounceMs: 0,
      serverUrl: () => '',
      sessionStore,
      sessionAgentOptionsBySession: {},
      selectedChatSessionId: ref(''),
      chatModalVisible: ref(false),
      chatConversation: ref<Conversation>({
        key: '',
        label: '',
        messages: [],
        createdAt: 0,
        updatedAt: 0,
        group: undefined,
      }),
      chatSystemSettings: ref<SystemSettings>({} as SystemSettings),
      conversationSyncTimers: new Map<string, number>(),
      conversationSyncRunning: new Set<string>(),
      conversationSyncQueued: new Set<string>(),
      ensureSessionVisible: () => {},
      syncActorsWithVisibility: () => {},
      getBrandModels: () => [],
    })

    const options = utils.buildSessionAgentOptions(sessionStore.s1)
    expect(options.persona_id).toBe('persona-1')
    expect(options.persona_name).toBe('冷靜 PM')
    expect(options.persona_content).toBe('請條理清楚地回覆。')
  })
})
