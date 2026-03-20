export type ChatRequestMode = 'chat' | 'agentic' | 'agent_session'

export interface ChatRequestMessage {
  role: string
  content: string
}

export interface AgentImageInput {
  name: string
  data_url: string
}

export interface AgentSessionPayloadOptions {
  images?: AgentImageInput[]
  model?: string
  reasoning_effort?: string
  permission_mode?: string
  plan_mode?: boolean
  cwd_override?: string
  git_branch?: string
  agent_brand?: string
}

export type CodexImageInput = AgentImageInput
export type CodexSessionPayloadOptions = AgentSessionPayloadOptions

export interface BuildChatRequestPayloadInput {
  model: string
  messages: ChatRequestMessage[]
  agents: unknown[]
  chatId: string
  isResume: boolean
  webSearch: boolean
  rag: boolean
  agentOptions?: AgentSessionPayloadOptions
  codexOptions?: CodexSessionPayloadOptions
}

export function resolveChatRequestMode(
  forceAgentSession: boolean,
  forceAgentic: boolean,
  isAgent: boolean,
): ChatRequestMode {
  if (forceAgentSession) {
    return 'agent_session'
  }
  if (forceAgentic || isAgent) {
    return 'agentic'
  }
  return 'chat'
}

export function resolveChatRequestPath(mode: ChatRequestMode): string {
  if (mode === 'agent_session') {
    // Use unified agent endpoint for multi-brand support.
    return '/api/session-bridge/agent/chat'
  }
  if (mode === 'agentic') {
    return '/api/agentic/chat'
  }
  return '/api/chat'
}

export function buildChatRequestPayload(mode: ChatRequestMode, payload: BuildChatRequestPayloadInput): Record<string, unknown> {
  if (mode === 'agent_session') {
    const latest = payload.messages[payload.messages.length - 1]
    const options = payload.agentOptions || payload.codexOptions || {}
    const result: Record<string, unknown> = {
      session_id: payload.chatId,
      message: latest?.content || '',
    }
    if (options.images && options.images.length > 0) {
      result.images = options.images
    }
    if (options.model) result.model = options.model
    if (options.reasoning_effort) result.reasoning_effort = options.reasoning_effort
    if (options.permission_mode) result.permission_mode = options.permission_mode
    if (typeof options.plan_mode === 'boolean') result.plan_mode = options.plan_mode
    if (options.cwd_override) result.cwd_override = options.cwd_override
    if (options.git_branch) result.git_branch = options.git_branch
    if (options.agent_brand) result.agent_brand = options.agent_brand
    return result
  }
  return {
    model: payload.model,
    messages: payload.messages,
    agents: payload.agents,
    chat_id: payload.chatId,
    is_resume: payload.isResume,
    web_search: payload.webSearch,
    rag: payload.rag,
  }
}
