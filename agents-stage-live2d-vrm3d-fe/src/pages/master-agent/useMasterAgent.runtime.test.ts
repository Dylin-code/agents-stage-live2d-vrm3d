import { afterEach, describe, expect, it, vi } from 'vitest'
import * as masterAgentApi from '../../utils/api/masterAgent'
import {
  applyMasterEvent,
  applyWsEnvelope,
  buildMasterAgentRuntime,
  emptyRuntimeState,
  hydrateStateFromSnapshot,
  type WebSocketLike,
} from './useMasterAgent.runtime'
import type { MasterRuntimeState } from './masterAgentTypes'

describe('applyMasterEvent', () => {
  it('accumulates thinking deltas', () => {
    let state = emptyRuntimeState()
    state = applyMasterEvent(state, {
      type: 'master_thinking_delta',
      content: { text: 'hello ', hop: 0 },
    })
    state = applyMasterEvent(state, {
      type: 'master_thinking_delta',
      content: { text: 'world', hop: 0 },
    })
    expect(state.thinkingDraft).toBe('hello world')
  })

  it('appends a system turn on tool_call_begin and clears draft', () => {
    let state = emptyRuntimeState()
    state = applyMasterEvent(state, {
      type: 'master_thinking_delta',
      content: { text: 'thinking…' },
    })
    state = applyMasterEvent(state, {
      type: 'tool_call_begin',
      content: { id: 't1', name: 'codex_new_session', arguments: { cwd: '/tmp' } },
    })
    expect(state.thinkingDraft).toBe('')
    expect(state.turns).toHaveLength(1)
    expect(state.turns[0].toolCalls?.[0].name).toBe('codex_new_session')
  })

  it('records subtask placeholders when tool_call_end carries subtask_id', () => {
    let state = emptyRuntimeState()
    state = applyMasterEvent(state, {
      type: 'tool_call_end',
      content: {
        id: 't2',
        name: 'codex_send_prompt',
        ok: true,
        output_text: 'dispatched',
        data: { subtask_id: 'sub-1', agent_brand: 'codex', session_id: 'sess-1' },
      },
    })
    expect(state.subtasks['sub-1']).toBeDefined()
    expect(state.subtasks['sub-1'].agent_brand).toBe('codex')
    expect(state.subtasks['sub-1'].session_id).toBe('sess-1')
  })

  it('marks isStreaming false on final_text and appends assistant turn', () => {
    let state = { ...emptyRuntimeState(), isStreaming: true }
    state = applyMasterEvent(state, {
      type: 'final_text',
      content: { text: 'all done' },
    })
    expect(state.isStreaming).toBe(false)
    const last = state.turns[state.turns.length - 1]
    expect(last.role).toBe('assistant')
    expect(last.text).toBe('all done')
  })

  it('records errors and stops streaming', () => {
    let state = { ...emptyRuntimeState(), isStreaming: true }
    state = applyMasterEvent(state, { type: 'error', content: 'oops' })
    expect(state.isStreaming).toBe(false)
    expect(state.lastError).toBe('oops')
  })
})

describe('applyWsEnvelope', () => {
  it('inserts a subtask into state.subtasks when the conversation matches', () => {
    const state = { ...emptyRuntimeState(), conversationId: 'c-1' }
    const next = applyWsEnvelope(state, {
      event: 'subtask',
      type: 'created',
      conversation_id: 'c-1',
      subtask: {
        id: 'sub-1', conversation_id: 'c-1', agent_brand: 'claude',
        session_id: 's', prompt: 'p', cwd: '/', status: 'pending',
        created_at: 0, updated_at: 0, final_text: '', last_event_type: '',
        error: '',
      },
    })
    expect(next.subtasks['sub-1']).toBeDefined()
  })

  it('ignores envelopes for a different conversation', () => {
    const state = { ...emptyRuntimeState(), conversationId: 'c-1' }
    const next = applyWsEnvelope(state, {
      event: 'subtask',
      type: 'created',
      conversation_id: 'c-other',
      subtask: {
        id: 'sub-other', conversation_id: 'c-other', agent_brand: 'claude',
        session_id: 's', prompt: 'p', cwd: '/', status: 'pending',
        created_at: 0, updated_at: 0, final_text: '', last_event_type: '',
        error: '',
      },
    })
    expect(next).toBe(state)
  })

  it('ignores non-subtask envelopes for now', () => {
    const state = { ...emptyRuntimeState(), conversationId: 'c-1' }
    const next = applyWsEnvelope(state, {
      event: 'conversation',
      type: 'created',
      conversation_id: 'c-1',
    })
    expect(next).toBe(state)
  })
})


describe('hydrateStateFromSnapshot', () => {
  it('rebuilds user/assistant/tool turns + subtasks from snapshot', () => {
    const state = hydrateStateFromSnapshot('conv-1', {
      conversation: {
        id: 'conv-1',
        messages: [
          { role: 'user', content: 'help me' },
          {
            role: 'assistant',
            content: {
              text: 'sure',
              tool_calls: [
                { id: 't1', name: 'codex_new_session', arguments: { cwd: '/tmp' } },
              ],
            },
          },
          {
            role: 'tool',
            content: {
              tool_use_id: 't1',
              name: 'codex_new_session',
              content: JSON.stringify({ ok: true, output_text: 'created' }),
            },
          },
        ],
      },
      subtasks: [
        {
          id: 'sub-1', conversation_id: 'conv-1', agent_brand: 'codex',
          session_id: 'sess-1', prompt: 'hi', cwd: '/tmp',
          status: 'done', created_at: 0, updated_at: 0,
          final_text: 'OK', last_event_type: '', error: '',
        },
      ],
    })
    expect(state.conversationId).toBe('conv-1')
    // user, assistant text, system (tool_call_begin echo), system (tool_call_end)
    expect(state.turns).toHaveLength(4)
    expect(state.turns[0].role).toBe('user')
    expect(state.turns[0].text).toBe('help me')
    expect(state.turns[1].role).toBe('assistant')
    expect(state.turns[1].text).toBe('sure')
    expect(state.turns[2].text).toContain('codex_new_session')
    expect(state.turns[3].text).toContain('created')
    expect(state.subtasks['sub-1'].status).toBe('done')
  })

  it('handles assistant with only tool_calls (no text)', () => {
    const state = hydrateStateFromSnapshot('c', {
      conversation: {
        id: 'c',
        messages: [
          {
            role: 'assistant',
            content: {
              text: '',
              tool_calls: [{ id: 'x', name: 'list_sessions', arguments: {} }],
            },
          },
        ],
      },
      subtasks: [],
    })
    // No assistant text turn (text empty + has tool_calls), just one system trace.
    expect(state.turns).toHaveLength(1)
    expect(state.turns[0].role).toBe('system')
    expect(state.turns[0].text).toContain('list_sessions')
  })
})


function memoryStorage() {
  let value: string | null = null
  return {
    load: () => value,
    save: (v: string) => { value = v },
    clear: () => { value = null },
    get raw() { return value },
  }
}


describe('buildMasterAgentRuntime — conversation persistence', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function makeHarness(initialState: MasterRuntimeState = emptyRuntimeState()) {
    const state = { current: initialState }
    return buildMasterAgentRuntime(
      () => state.current,
      (next) => { state.current = next },
      { storage: memoryStorage() },
    )
  }

  it('ensureConversation persists newly-created id', async () => {
    const storage = memoryStorage()
    const state = { current: emptyRuntimeState() }
    const runtime = buildMasterAgentRuntime(
      () => state.current,
      (next) => { state.current = next },
      { storage },
    )
    vi.spyOn(masterAgentApi, 'createMasterAgentConversation').mockResolvedValue({
      conversation_id: 'new-conv-id',
    })
    const result = await runtime.ensureConversation()
    expect(result).toBe('new-conv-id')
    expect(storage.raw).toBe('new-conv-id')
    expect(state.current.conversationId).toBe('new-conv-id')
  })

  it('ensureConversation rehydrates from storage when server still knows the id', async () => {
    const storage = memoryStorage()
    storage.save('persisted-id')
    const state = { current: emptyRuntimeState() }
    const runtime = buildMasterAgentRuntime(
      () => state.current,
      (next) => { state.current = next },
      { storage },
    )
    const snapshotSpy = vi
      .spyOn(masterAgentApi, 'fetchMasterAgentSnapshot')
      .mockResolvedValue({
        conversation: { id: 'persisted-id', messages: [
          { role: 'user', content: 'earlier' },
        ], created_at: 0, updated_at: 0 },
        subtasks: [],
      })
    const createSpy = vi.spyOn(masterAgentApi, 'createMasterAgentConversation')
    const result = await runtime.ensureConversation()
    expect(result).toBe('persisted-id')
    expect(snapshotSpy).toHaveBeenCalledOnce()
    expect(createSpy).not.toHaveBeenCalled()
    expect(state.current.turns[0].text).toBe('earlier')
  })

  it('ensureConversation falls back to create when stored id is gone (server forgot)', async () => {
    const storage = memoryStorage()
    storage.save('stale-id')
    const state = { current: emptyRuntimeState() }
    const runtime = buildMasterAgentRuntime(
      () => state.current,
      (next) => { state.current = next },
      { storage },
    )
    vi.spyOn(masterAgentApi, 'fetchMasterAgentSnapshot').mockRejectedValue(
      new Error('404'),
    )
    const createSpy = vi
      .spyOn(masterAgentApi, 'createMasterAgentConversation')
      .mockResolvedValue({ conversation_id: 'fresh-id' })
    const result = await runtime.ensureConversation()
    expect(result).toBe('fresh-id')
    expect(createSpy).toHaveBeenCalledOnce()
    expect(storage.raw).toBe('fresh-id')
  })

  it('connectWs reconciles subtask state from broadcast envelopes', async () => {
    const state = {
      current: { ...emptyRuntimeState(), conversationId: 'c-current' },
    }
    let socket: FakeSocket | null = null
    class FakeSocket implements WebSocketLike {
      onopen: ((ev: Event) => void) | null = null
      onmessage: ((ev: MessageEvent) => void) | null = null
      onclose: ((ev: CloseEvent) => void) | null = null
      onerror: ((ev: Event) => void) | null = null
      closed = false
      constructor() { socket = this }
      close() { this.closed = true }
    }
    const runtime = buildMasterAgentRuntime(
      () => state.current,
      (next) => { state.current = next },
      { websocketFactory: (_url: string) => new FakeSocket() },
    )
    const dispose = runtime.connectWs('http://example.test')
    // Simulate an envelope tagged with our conversation
    socket!.onmessage?.({
      data: JSON.stringify({
        event: 'subtask',
        type: 'status',
        conversation_id: 'c-current',
        subtask: {
          id: 'sub-1', conversation_id: 'c-current', agent_brand: 'codex',
          session_id: 's', prompt: 'p', cwd: '/', status: 'running',
          created_at: 0, updated_at: 0, final_text: '', last_event_type: '',
          error: '',
        },
      }),
    } as MessageEvent)
    expect(state.current.subtasks['sub-1'].status).toBe('running')
    // Envelope for a different conversation must be ignored
    socket!.onmessage?.({
      data: JSON.stringify({
        event: 'subtask',
        type: 'status',
        conversation_id: 'c-other',
        subtask: {
          id: 'sub-other', conversation_id: 'c-other', agent_brand: 'codex',
          session_id: 's2', prompt: 'p', cwd: '/', status: 'done',
          created_at: 0, updated_at: 0, final_text: 'X', last_event_type: '',
          error: '',
        },
      }),
    } as MessageEvent)
    expect(state.current.subtasks['sub-other']).toBeUndefined()
    dispose()
    expect(socket!.closed).toBe(true)
  })

  it('startNewConversation clears state and storage', async () => {
    const storage = memoryStorage()
    storage.save('old-id')
    const state = {
      current: {
        ...emptyRuntimeState(),
        conversationId: 'old-id',
        turns: [
          { id: 't1', role: 'user' as const, text: 'previous', createdAt: 0 },
        ],
      },
    }
    const runtime = buildMasterAgentRuntime(
      () => state.current,
      (next) => { state.current = next },
      { storage },
    )
    vi.spyOn(masterAgentApi, 'createMasterAgentConversation').mockResolvedValue({
      conversation_id: 'brand-new',
    })
    const result = await runtime.startNewConversation()
    expect(result).toBe('brand-new')
    expect(state.current.conversationId).toBe('brand-new')
    expect(state.current.turns).toHaveLength(0)
    expect(state.current.subtasks).toEqual({})
    expect(storage.raw).toBe('brand-new')
  })
})
