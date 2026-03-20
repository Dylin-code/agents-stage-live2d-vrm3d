import { describe, expect, it } from 'vitest'
import { buildChatRequestPayload, resolveChatRequestMode, resolveChatRequestPath } from './chatRequestPath'

describe('resolveChatRequestPath', () => {
  it('forces agent session path when forceAgentSession is true', () => {
    const mode = resolveChatRequestMode(true, false, false)
    expect(mode).toBe('agent_session')
    expect(resolveChatRequestPath(mode)).toBe('/api/session-bridge/agent/chat')
  })

  it('forces agentic path when forceAgentic is true', () => {
    const mode = resolveChatRequestMode(false, true, false)
    expect(mode).toBe('agentic')
    expect(resolveChatRequestPath(mode)).toBe('/api/agentic/chat')
  })

  it('follows agent toggle when force flags are false', () => {
    const agentMode = resolveChatRequestMode(false, false, true)
    expect(resolveChatRequestPath(agentMode)).toBe('/api/agentic/chat')

    const chatMode = resolveChatRequestMode(false, false, false)
    expect(resolveChatRequestPath(chatMode)).toBe('/api/chat')
  })
})

describe('buildChatRequestPayload', () => {
  it('builds agent session payload with latest user message', () => {
    const payload = buildChatRequestPayload('agent_session', {
      model: 'qwen2.5',
      messages: [
        { role: 'assistant', content: 'a' },
        { role: 'user', content: 'latest' },
      ],
      chatId: 'session-1',
      agents: [],
      isResume: false,
      webSearch: false,
      rag: false,
    })
    expect(payload).toEqual({
      session_id: 'session-1',
      message: 'latest',
    })
  })

  it('includes agent runtime overrides and images when provided', () => {
    const payload = buildChatRequestPayload('agent_session', {
      model: 'qwen2.5',
      messages: [{ role: 'user', content: 'continue' }],
      chatId: 'session-2',
      agents: [],
      isResume: false,
      webSearch: false,
      rag: false,
      agentOptions: {
        images: [{ name: 'a.png', data_url: 'data:image/png;base64,abc=' }],
        model: 'gpt-5-codex',
        reasoning_effort: 'high',
        permission_mode: 'default',
        plan_mode: true,
        cwd_override: '/repo',
        git_branch: 'feature/a',
        agent_brand: 'claude',
      },
    })
    expect(payload).toEqual({
      session_id: 'session-2',
      message: 'continue',
      images: [{ name: 'a.png', data_url: 'data:image/png;base64,abc=' }],
      model: 'gpt-5-codex',
      reasoning_effort: 'high',
      permission_mode: 'default',
      plan_mode: true,
      cwd_override: '/repo',
      git_branch: 'feature/a',
      agent_brand: 'claude',
    })
  })

  it('keeps original payload for default mode', () => {
    const messages = [{ role: 'user', content: 'hello' }]
    const payload = buildChatRequestPayload('chat', {
      model: 'qwen2.5',
      messages,
      chatId: 'chat-1',
      agents: [],
      isResume: false,
      webSearch: true,
      rag: true,
    })
    expect(payload).toEqual({
      model: 'qwen2.5',
      messages,
      agents: [],
      chat_id: 'chat-1',
      is_resume: false,
      web_search: true,
      rag: true,
    })
  })
})
