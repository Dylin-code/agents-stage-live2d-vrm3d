import { type Ref } from 'vue'
import { message } from 'ant-design-vue'

import type { Conversation, Message, SystemSettings } from '../../types/message'
import type { SessionSnapshotItem } from '../../types/sessionState'
import {
  fetchSessionBridgeBranches,
  fetchSessionBridgeConversation,
  switchSessionBridgeBranch,
} from '../../utils/api/sessionBridge'
import { DEFAULT_AGENT_BRANDS, getAgentBrandModels } from '../../utils/agentBrands'

export interface SessionAgentUiOptions {
  model?: string
  reasoning_effort?: string
  permission_mode?: string
  plan_mode?: boolean
  cwd_override?: string
  git_branch?: string
  available_models?: string[]
  available_branches?: string[]
  cwd?: string
  agent_brand?: string
}

export interface OpenSessionChatOptions {
  forceSwitch?: boolean
}

export function brandDefaultModels(brand?: string): string[] {
  return getAgentBrandModels(DEFAULT_AGENT_BRANDS, brand)
}

export function createSessionStageChatAgentUtils(args: {
  storageKeyConversations: string
  conversationLimit: number
  conversationSyncDebounceMs: number
  serverUrl: () => string
  sessionStore: Record<string, SessionSnapshotItem>
  sessionAgentOptionsBySession: Record<string, SessionAgentUiOptions>
  selectedChatSessionId: Ref<string>
  chatModalVisible: Ref<boolean>
  chatConversation: Ref<Conversation>
  chatSystemSettings: Ref<SystemSettings>
  conversationSyncTimers: Map<string, number>
  conversationSyncRunning: Set<string>
  conversationSyncQueued: Set<string>
  ensureSessionVisible: (sessionId: string) => void
  syncActorsWithVisibility: () => void
  getBrandModels: (brand?: string) => string[]
}) {
  const {
    storageKeyConversations,
    conversationLimit,
    conversationSyncDebounceMs,
    serverUrl,
    sessionStore,
    sessionAgentOptionsBySession,
    selectedChatSessionId,
    chatModalVisible,
    chatConversation,
    chatSystemSettings,
    conversationSyncTimers,
    conversationSyncRunning,
    conversationSyncQueued,
    ensureSessionVisible,
    syncActorsWithVisibility,
    getBrandModels,
  } = args

  function readConversationsFromStorage(): Conversation[] {
    const raw = localStorage.getItem(storageKeyConversations)
    if (!raw) return []
    try {
      const parsed = JSON.parse(raw)
      return Array.isArray(parsed?.conversationItems) ? parsed.conversationItems : []
    } catch {
      return []
    }
  }

  function saveConversationsToStorage(items: Conversation[]): void {
    localStorage.setItem(storageKeyConversations, JSON.stringify({ conversationItems: items }))
  }

  function buildSessionAgentOptions(session?: SessionSnapshotItem): SessionAgentUiOptions {
    const context = session?.context || {}
    const brand = (session?.agent_brand || 'codex').toLowerCase()
    const permissionMode = String(context.permission_mode || '').trim()
      || (String(context.sandbox_mode || '').trim() === 'danger-full-access' ? 'full' : 'default')
    return {
      model: context.model || '',
      reasoning_effort: context.effort || '',
      permission_mode: permissionMode,
      plan_mode: context.plan_mode === true,
      cwd_override: '',
      git_branch: session?.branch || '',
      available_models: getBrandModels(brand),
      available_branches: [],
      cwd: session?.cwd || '',
      agent_brand: brand,
    }
  }

  function ensureSessionAgentOptions(sessionId: string): SessionAgentUiOptions {
    const session = sessionStore[sessionId]
    const existing = sessionAgentOptionsBySession[sessionId]
    if (existing) return existing
    const created = buildSessionAgentOptions(session)
    sessionAgentOptionsBySession[sessionId] = created
    return created
  }

  function syncSessionAgentOptionsFromSnapshot(session: SessionSnapshotItem): void {
    const current = sessionAgentOptionsBySession[session.session_id]
    const context = session.context || {}
    if (!current) {
      sessionAgentOptionsBySession[session.session_id] = buildSessionAgentOptions(session)
      return
    }
    current.model = current.model || context.model || ''
    current.reasoning_effort = current.reasoning_effort || context.effort || ''
    current.permission_mode = current.permission_mode
      || String(context.permission_mode || '')
      || (String(context.sandbox_mode || '').trim() === 'danger-full-access' ? 'full' : 'default')
    if (current.plan_mode === undefined || current.plan_mode === null) {
      current.plan_mode = context.plan_mode === true
    }
    if (session.cwd) current.cwd = session.cwd
    if (!current.git_branch && session.branch) current.git_branch = session.branch
  }

  async function refreshSessionBranches(sessionId: string): Promise<void> {
    const session = sessionStore[sessionId]
    if (!session) return
    try {
      const result = await fetchSessionBridgeBranches(serverUrl(), sessionId, session.cwd)
      const branches = Array.isArray(result.branches) ? result.branches.map((x) => String(x)) : []
      const current = String(result.current || '')
      const options = ensureSessionAgentOptions(sessionId)
      options.available_branches = branches
      if (!options.git_branch && current) options.git_branch = current
      if (current) session.branch = current
    } catch (error) {
      console.error('Failed to refresh branches', error)
    }
  }

  async function refreshActiveSessionBranches(): Promise<void> {
    if (!selectedChatSessionId.value) return
    await refreshSessionBranches(selectedChatSessionId.value)
  }

  async function switchSessionBranchIfNeeded(sessionId: string, nextBranch: string, previousBranch: string): Promise<void> {
    const branch = (nextBranch || '').trim()
    if (!branch || branch === (previousBranch || '').trim()) return
    try {
      const result = await switchSessionBridgeBranch(serverUrl(), { session_id: sessionId, branch })
      const current = String(result.current || branch)
      const options = ensureSessionAgentOptions(sessionId)
      options.git_branch = current
      const session = sessionStore[sessionId]
      if (session) session.branch = current
      message.success(`已切換分支：${current}`)
    } catch (error) {
      message.error(`切換分支失敗：${String((error as Error)?.message || error || 'unknown error')}`)
    }
  }

  async function handleActiveSessionAgentOptionsChange(options: SessionAgentUiOptions): Promise<void> {
    const sessionId = selectedChatSessionId.value
    if (!sessionId) return
    const current = ensureSessionAgentOptions(sessionId)
    const previousBranch = current.git_branch || ''
    sessionAgentOptionsBySession[sessionId] = { ...current, ...options }
    const session = sessionStore[sessionId]
    if (session) {
      if (options.cwd_override !== undefined) sessionAgentOptionsBySession[sessionId].cwd_override = options.cwd_override
      if (options.model !== undefined) session.context = { ...(session.context || {}), model: options.model || '' }
      if (options.reasoning_effort !== undefined) session.context = { ...(session.context || {}), effort: options.reasoning_effort || '' }
      if (options.permission_mode !== undefined) session.context = { ...(session.context || {}), permission_mode: options.permission_mode || 'default' }
      if (typeof options.plan_mode === 'boolean') session.context = { ...(session.context || {}), plan_mode: options.plan_mode }
    }
    await switchSessionBranchIfNeeded(sessionId, options.git_branch || '', previousBranch)
  }

  function buildConversationForSession(session: SessionSnapshotItem): Conversation {
    const now = Date.now()
    return {
      key: session.session_id,
      label: session.display_name || `session-${session.session_id.slice(0, 8)}`,
      messages: [],
      createdAt: now,
      updatedAt: now,
      group: undefined,
    }
  }

  function toConversationMessage(item: { role: string; content: string; timestamp: string }, index: number): Message {
    const ts = new Date(item.timestamp).getTime()
    const normalizedTimestamp = Number.isNaN(ts) ? item.timestamp : new Date(ts).toLocaleString()
    return {
      id: index + 1,
      role: item.role === 'user' ? 'user' : 'assistant',
      content: item.content,
      timestamp: normalizedTimestamp,
      loading: false,
    }
  }

  async function hydrateConversationFromBridge(sessionId: string): Promise<void> {
    try {
      const response = await fetchSessionBridgeConversation(serverUrl(), sessionId, conversationLimit)
      if (!Array.isArray(response.messages) || response.messages.length === 0) return
      const mappedMessages = response.messages.map((item, idx) => toConversationMessage(item, idx))
      const conversations = readConversationsFromStorage()
      const idx = conversations.findIndex((item) => item.key === sessionId)
      const current = idx >= 0 ? conversations[idx] : chatConversation.value
      const session = sessionStore[sessionId]
      const mergedConversation: Conversation = {
        ...current,
        key: sessionId,
        label: session?.display_name || current.label || `session-${sessionId.slice(0, 8)}`,
        messages: mappedMessages,
        updatedAt: Date.now(),
        createdAt: current.createdAt || Date.now(),
      }
      if (idx >= 0) conversations[idx] = mergedConversation
      else conversations.push(mergedConversation)
      saveConversationsToStorage(conversations)
      if (chatConversation.value.key === sessionId) {
        chatConversation.value = mergedConversation
      }
    } catch (error) {
      console.error('Failed to fetch session conversation', error)
    }
  }

  function runConversationHydration(sessionId: string): void {
    if (conversationSyncRunning.has(sessionId)) {
      conversationSyncQueued.add(sessionId)
      return
    }
    conversationSyncRunning.add(sessionId)
    void hydrateConversationFromBridge(sessionId).finally(() => {
      conversationSyncRunning.delete(sessionId)
      if (conversationSyncQueued.delete(sessionId)) runConversationHydration(sessionId)
    })
  }

  function scheduleConversationHydration(sessionId: string, delayMs = conversationSyncDebounceMs): void {
    const existing = conversationSyncTimers.get(sessionId)
    if (existing !== undefined) {
      window.clearTimeout(existing)
      conversationSyncTimers.delete(sessionId)
    }
    const timer = window.setTimeout(() => {
      conversationSyncTimers.delete(sessionId)
      runConversationHydration(sessionId)
    }, Math.max(0, delayMs))
    conversationSyncTimers.set(sessionId, timer)
  }

  function openSessionChat(sessionId: string, options: OpenSessionChatOptions = {}): void {
    const session = sessionStore[sessionId]
    if (!session) return
    const hasActiveOtherChat = (
      chatModalVisible.value
      && !!selectedChatSessionId.value
      && selectedChatSessionId.value !== sessionId
    )
    if (hasActiveOtherChat && options.forceSwitch !== true) {
      return
    }
    ensureSessionVisible(sessionId)
    selectedChatSessionId.value = sessionId
    ensureSessionAgentOptions(sessionId)
    syncSessionAgentOptionsFromSnapshot(session)
    const conversations = readConversationsFromStorage()
    const existing = conversations.find((item) => item.key === sessionId)
    const target = existing || buildConversationForSession(session)
    if (!existing) {
      conversations.push(target)
      saveConversationsToStorage(conversations)
    }
    chatConversation.value = target
    chatModalVisible.value = true
    syncActorsWithVisibility()
    scheduleConversationHydration(sessionId, 0)
    void refreshSessionBranches(sessionId)
  }

  function closeSessionChat(): void {
    chatModalVisible.value = false
    selectedChatSessionId.value = ''
    syncActorsWithVisibility()
  }

  function handleChatConversationUpdate(conversation: Conversation): void {
    chatConversation.value = conversation
    const conversations = readConversationsFromStorage()
    const idx = conversations.findIndex((item) => item.key === conversation.key)
    if (idx >= 0) conversations[idx] = conversation
    else conversations.push(conversation)
    saveConversationsToStorage(conversations)
  }

  return {
    buildSessionAgentOptions,
    ensureSessionAgentOptions,
    syncSessionAgentOptionsFromSnapshot,
    refreshSessionBranches,
    refreshActiveSessionBranches,
    handleActiveSessionAgentOptionsChange,
    openSessionChat,
    closeSessionChat,
    scheduleConversationHydration,
    handleChatConversationUpdate,
  }
}
