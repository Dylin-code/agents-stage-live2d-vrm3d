/**
 * Master-agent page runtime: state + event handlers.
 *
 * Split from the .vue file so the reducer-like logic (applyMasterEvent)
 * can be unit-tested without spinning up Vue. The Vue layer holds reactive
 * state (refs) and just forwards events to applyMasterEvent.
 */

import type {
  MasterAgentWsEnvelope,
  MasterEvent,
  SubTaskRecord,
} from '../../utils/api/masterAgent'
import {
  abortMasterAgent,
  consumeMasterEventStream,
  createMasterAgentConversation,
  fetchMasterAgentSnapshot,
  fetchMasterAgentSubtasks,
  resolveMasterAgentWsUrl,
  submitMasterAgentChat,
} from '../../utils/api/masterAgent'
import type { MasterChatTurn, MasterRuntimeState } from './masterAgentTypes'
import { emptyRuntimeState } from './masterAgentTypes'

const CONVERSATION_STORAGE_KEY = 'agents-stage:master-agent:conversation_id'

interface PersistedConversation {
  load(): string | null
  save(id: string): void
  clear(): void
}

function browserStorage(): PersistedConversation {
  // Wrap in try/catch — Safari private-mode + tests w/o jsdom localStorage
  // both throw on access. Failing fast is worse than degrading to in-memory.
  return {
    load(): string | null {
      try {
        return globalThis.localStorage?.getItem(CONVERSATION_STORAGE_KEY) ?? null
      } catch {
        return null
      }
    },
    save(id: string): void {
      try {
        globalThis.localStorage?.setItem(CONVERSATION_STORAGE_KEY, id)
      } catch {
        // ignore
      }
    },
    clear(): void {
      try {
        globalThis.localStorage?.removeItem(CONVERSATION_STORAGE_KEY)
      } catch {
        // ignore
      }
    },
  }
}

function genTurnId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

export function applyMasterEvent(state: MasterRuntimeState, event: MasterEvent): MasterRuntimeState {
  const type = event.type
  const content = event.content as Record<string, unknown> | undefined
  if (type === 'master_thinking_delta') {
    const text = typeof content?.text === 'string' ? content.text : ''
    return { ...state, thinkingDraft: state.thinkingDraft + text }
  }
  if (type === 'tool_call_begin') {
    const name = String(content?.name || '')
    const argsRaw = content?.arguments
    const args: Record<string, unknown> =
      argsRaw && typeof argsRaw === 'object' ? (argsRaw as Record<string, unknown>) : {}
    const draft = state.thinkingDraft.trim()
    const newTurns = draft
      ? [
          ...state.turns,
          {
            id: genTurnId(),
            role: 'assistant' as const,
            text: draft,
            toolCalls: [{ id: String(content?.id || ''), name, arguments: args }],
            createdAt: Date.now(),
          },
        ]
      : [
          ...state.turns,
          {
            id: genTurnId(),
            role: 'system' as const,
            text: `→ ${name}(${JSON.stringify(args)})`,
            createdAt: Date.now(),
          },
        ]
    return { ...state, turns: newTurns, thinkingDraft: '' }
  }
  if (type === 'tool_call_end') {
    const name = String(content?.name || '')
    const ok = !!content?.ok
    const outputText = String(content?.output_text || '')
    const data = (content?.data as Record<string, unknown>) || {}
    const turns = [
      ...state.turns,
      {
        id: genTurnId(),
        role: 'system' as const,
        text: `← ${name}: ${ok ? outputText : `(error) ${content?.error || ''}`}`,
        createdAt: Date.now(),
      },
    ]
    const subtasks = { ...state.subtasks }
    const subtaskId = data?.subtask_id
    if (typeof subtaskId === 'string' && subtaskId) {
      subtasks[subtaskId] = {
        ...(subtasks[subtaskId] || makePlaceholderSubtask(subtaskId, data)),
        last_event_type: name,
        updated_at: Date.now() / 1000,
      }
    }
    return { ...state, turns, subtasks }
  }
  if (type === 'final_text') {
    const text = typeof content?.text === 'string' ? content.text : ''
    return {
      ...state,
      isStreaming: false,
      thinkingDraft: '',
      turns: [
        ...state.turns,
        { id: genTurnId(), role: 'assistant', text, createdAt: Date.now() },
      ],
    }
  }
  if (type === 'hop_limit_reached') {
    const text = typeof event.content === 'string' ? event.content : 'hop limit reached'
    return {
      ...state,
      isStreaming: false,
      thinkingDraft: '',
      lastError: text,
      turns: [
        ...state.turns,
        { id: genTurnId(), role: 'system', text: `(${text})`, createdAt: Date.now() },
      ],
    }
  }
  if (type === 'error') {
    const text = typeof event.content === 'string' ? event.content : 'unknown error'
    return {
      ...state,
      isStreaming: false,
      thinkingDraft: '',
      lastError: text,
      turns: [
        ...state.turns,
        { id: genTurnId(), role: 'system', text: `error: ${text}`, createdAt: Date.now() },
      ],
    }
  }
  return state
}

function makePlaceholderSubtask(subtaskId: string, data: Record<string, unknown>): SubTaskRecord {
  return {
    id: subtaskId,
    conversation_id: '',
    agent_brand: String(data?.agent_brand || ''),
    session_id: String(data?.session_id || ''),
    prompt: '',
    cwd: '',
    status: 'running',
    created_at: Date.now() / 1000,
    updated_at: Date.now() / 1000,
    final_text: '',
    last_event_type: '',
    error: '',
  }
}

/**
 * Rebuild turns + subtasks from a server-side conversation snapshot so
 * the UI can resume mid-conversation across page reloads.
 *
 * Server stores raw shapes (user=string, assistant={text, tool_calls},
 * tool={tool_use_id, name, content}). We map them back to the same
 * MasterChatTurn shapes that ``applyMasterEvent`` would have produced
 * live, plus pull subtasks straight from the snapshot's subtasks list.
 */
export function hydrateStateFromSnapshot(
  conversationId: string,
  snapshot: {
    conversation: { id: string; messages: Array<{ role: string; content: unknown }> }
    subtasks: SubTaskRecord[]
  },
): MasterRuntimeState {
  const turns: MasterChatTurn[] = []
  for (const entry of snapshot.conversation.messages ?? []) {
    const role = entry.role
    const content = entry.content as unknown
    if (role === 'user') {
      turns.push({
        id: genTurnId(),
        role: 'user',
        text: typeof content === 'string' ? content : JSON.stringify(content),
        createdAt: Date.now(),
      })
      continue
    }
    if (role === 'assistant' && content && typeof content === 'object') {
      const obj = content as Record<string, unknown>
      const text = typeof obj.text === 'string' ? obj.text : ''
      const toolCallsRaw = Array.isArray(obj.tool_calls) ? obj.tool_calls : []
      const toolCalls = toolCallsRaw
        .filter((c): c is Record<string, unknown> => !!c && typeof c === 'object')
        .map((c) => ({
          id: String(c.id || ''),
          name: String(c.name || ''),
          arguments: (c.arguments as Record<string, unknown>) || {},
        }))
      if (text || toolCalls.length === 0) {
        turns.push({
          id: genTurnId(),
          role: 'assistant',
          text: text || '(no reply)',
          toolCalls: toolCalls.length ? toolCalls : undefined,
          createdAt: Date.now(),
        })
      }
      for (const call of toolCalls) {
        turns.push({
          id: genTurnId(),
          role: 'system',
          text: `→ ${call.name}(${JSON.stringify(call.arguments)})`,
          createdAt: Date.now(),
        })
      }
      continue
    }
    if (role === 'tool' && content && typeof content === 'object') {
      const obj = content as Record<string, unknown>
      const name = String(obj.name || '')
      const raw = obj.content
      // tool content is stringified JSON from the orchestrator. Try to decode.
      let outputText = ''
      if (typeof raw === 'string') {
        try {
          const parsed = JSON.parse(raw) as Record<string, unknown>
          outputText = String(parsed.output_text || parsed.error || raw)
        } catch {
          outputText = raw
        }
      }
      turns.push({
        id: genTurnId(),
        role: 'system',
        text: `← ${name}: ${outputText}`,
        createdAt: Date.now(),
      })
    }
  }
  const subtasks: Record<string, SubTaskRecord> = {}
  for (const item of snapshot.subtasks ?? []) {
    subtasks[item.id] = item
  }
  return {
    conversationId,
    isStreaming: false,
    turns,
    thinkingDraft: '',
    subtasks,
    lastError: '',
  }
}

export interface MasterAgentRuntime {
  state: MasterRuntimeState
  ensureConversation(serverUrl?: string, defaultCwd?: string): Promise<string>
  sendMessage(
    message: string,
    opts?: { serverUrl?: string; defaultCwd?: string; permitFullAccess?: boolean },
  ): Promise<void>
  refreshSubtasks(serverUrl?: string): Promise<void>
  abort(serverUrl?: string): Promise<void>
  startNewConversation(serverUrl?: string, defaultCwd?: string): Promise<string>
  /** Subscribe to the broadcast WS; reconcile state.subtasks on every
   *  envelope tagged with the current conversation_id. Returns a
   *  disposer (call it to disconnect). */
  connectWs(serverUrl?: string): () => void
}

export interface BuildMasterAgentRuntimeOptions {
  storage?: PersistedConversation
  /** Optional factory for tests — swap the real WebSocket for a fake. */
  websocketFactory?: (url: string) => WebSocketLike
}

export interface WebSocketLike {
  onopen: ((ev: Event) => void) | null
  onmessage: ((ev: MessageEvent) => void) | null
  onclose: ((ev: CloseEvent) => void) | null
  onerror: ((ev: Event) => void) | null
  close(): void
}

/** Apply a single WS envelope to the runtime state. Exposed for tests. */
export function applyWsEnvelope(
  state: MasterRuntimeState,
  envelope: MasterAgentWsEnvelope,
): MasterRuntimeState {
  // Only react to events tagged with our conversation_id; other tabs'
  // conversations also flow through the same broadcast channel.
  if (
    envelope.conversation_id &&
    state.conversationId &&
    envelope.conversation_id !== state.conversationId
  ) {
    return state
  }
  if (envelope.event === 'subtask' && envelope.subtask) {
    const record = envelope.subtask
    return {
      ...state,
      subtasks: { ...state.subtasks, [record.id]: record },
    }
  }
  return state
}

/**
 * Build a runtime instance bound to a reactive setter. The setter is
 * called whenever the state needs to be replaced; the Vue layer wraps
 * a ref into this signature.
 *
 * ``storage`` defaults to ``window.localStorage`` so the same
 * conversation_id is reused across page reloads. Pass a custom impl in
 * tests / environments without localStorage.
 */
export function buildMasterAgentRuntime(
  getState: () => MasterRuntimeState,
  setState: (next: MasterRuntimeState) => void,
  options: BuildMasterAgentRuntimeOptions = {},
): MasterAgentRuntime {
  let abortCtl: AbortController | null = null
  const storage = options.storage ?? browserStorage()
  const wsFactory = options.websocketFactory ?? ((url: string) => new WebSocket(url) as unknown as WebSocketLike)

  async function ensureConversation(serverUrl?: string, defaultCwd?: string): Promise<string> {
    const current = getState()
    if (current.conversationId) return current.conversationId
    // First: try to rehydrate from a previously-saved conversation_id.
    const storedId = storage.load()
    if (storedId) {
      try {
        const snapshot = await fetchMasterAgentSnapshot(storedId, serverUrl)
        setState(hydrateStateFromSnapshot(storedId, snapshot))
        return storedId
      } catch {
        // Server forgot about it (restart / GC) — fall through to create.
        storage.clear()
      }
    }
    const { conversation_id } = await createMasterAgentConversation(serverUrl, defaultCwd)
    storage.save(conversation_id)
    setState({ ...getState(), conversationId: conversation_id })
    return conversation_id
  }

  async function startNewConversation(
    serverUrl?: string,
    defaultCwd?: string,
  ): Promise<string> {
    // Cancel any in-flight stream so we don't keep writing to the old
    // conversation after the user moved on.
    abortCtl?.abort()
    storage.clear()
    setState(emptyRuntimeState())
    const { conversation_id } = await createMasterAgentConversation(serverUrl, defaultCwd)
    storage.save(conversation_id)
    setState({ ...getState(), conversationId: conversation_id })
    return conversation_id
  }

  async function sendMessage(
    message: string,
    opts?: { serverUrl?: string; defaultCwd?: string; permitFullAccess?: boolean },
  ): Promise<void> {
    const trimmed = message.trim()
    if (!trimmed) return
    const conversationId = await ensureConversation(opts?.serverUrl, opts?.defaultCwd)
    abortCtl = new AbortController()
    const userTurnText = opts?.permitFullAccess ? `${trimmed} #full` : trimmed
    const userTurn: MasterChatTurn = {
      id: genTurnId(),
      role: 'user',
      text: userTurnText,
      createdAt: Date.now(),
    }
    setState({
      ...getState(),
      isStreaming: true,
      lastError: '',
      thinkingDraft: '',
      turns: [...getState().turns, userTurn],
    })
    try {
      const response = await submitMasterAgentChat(
        opts?.serverUrl,
        {
          conversation_id: conversationId,
          message: trimmed,
          default_cwd: opts?.defaultCwd,
          permit_full_access: opts?.permitFullAccess,
        },
        { signal: abortCtl.signal },
      )
      for await (const event of consumeMasterEventStream(response)) {
        setState(applyMasterEvent(getState(), event))
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setState({ ...getState(), isStreaming: false, lastError: msg })
    } finally {
      abortCtl = null
      setState({ ...getState(), isStreaming: false })
    }
  }

  async function refreshSubtasks(serverUrl?: string): Promise<void> {
    const current = getState()
    if (!current.conversationId) return
    const items = await fetchMasterAgentSubtasks(current.conversationId, serverUrl)
    const subtasks: Record<string, SubTaskRecord> = {}
    for (const item of items) subtasks[item.id] = item
    setState({ ...getState(), subtasks })
  }

  async function abort(serverUrl?: string): Promise<void> {
    abortCtl?.abort()
    const current = getState()
    if (current.conversationId) {
      try {
        await abortMasterAgent(current.conversationId, serverUrl)
      } catch {
        // best-effort
      }
    }
    setState({ ...getState(), isStreaming: false })
  }

  function connectWs(serverUrl?: string): () => void {
    let socket: WebSocketLike | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let disposed = false
    // Capped exponential backoff: 1s, 2s, 4s, ... up to 30s. Reset
    // back to 1s once the connection is healthy.
    let backoffMs = 1000
    const MAX_BACKOFF_MS = 30000

    const open = () => {
      if (disposed) return
      let nextSocket: WebSocketLike
      try {
        nextSocket = wsFactory(resolveMasterAgentWsUrl(serverUrl))
      } catch (err) {
        // ws constructor itself threw — schedule a retry.
        scheduleReconnect()
        return
      }
      socket = nextSocket
      nextSocket.onopen = () => {
        backoffMs = 1000
      }
      nextSocket.onmessage = (event: MessageEvent) => {
        let envelope: MasterAgentWsEnvelope
        try {
          envelope = JSON.parse(String(event.data)) as MasterAgentWsEnvelope
        } catch {
          return
        }
        setState(applyWsEnvelope(getState(), envelope))
      }
      nextSocket.onclose = () => {
        if (socket === nextSocket) socket = null
        scheduleReconnect()
      }
      nextSocket.onerror = () => {
        try {
          nextSocket.close()
        } catch {
          // ignore
        }
      }
    }
    const scheduleReconnect = () => {
      if (disposed) return
      const delay = backoffMs
      backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS)
      reconnectTimer = setTimeout(open, delay)
    }
    open()
    return () => {
      disposed = true
      if (reconnectTimer !== null) clearTimeout(reconnectTimer)
      try {
        socket?.close()
      } catch {
        // ignore
      }
    }
  }

  return {
    get state() {
      return getState()
    },
    ensureConversation,
    sendMessage,
    refreshSubtasks,
    abort,
    startNewConversation,
    connectWs,
  }
}

// Convenience re-export so tests can `import { emptyRuntimeState }` from runtime.
export { emptyRuntimeState }
