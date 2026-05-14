import type { MasterEvent, SubTaskRecord } from '../../utils/api/masterAgent'

export type { MasterEvent, SubTaskRecord }

export interface MasterChatTurn {
  id: string
  role: 'user' | 'assistant' | 'system'
  text: string
  toolCalls?: Array<{ id: string; name: string; arguments: Record<string, unknown> }>
  createdAt: number
}

export interface MasterRuntimeState {
  conversationId: string
  isStreaming: boolean
  turns: MasterChatTurn[]
  thinkingDraft: string
  subtasks: Record<string, SubTaskRecord>
  lastError: string
}

export function emptyRuntimeState(): MasterRuntimeState {
  return {
    conversationId: '',
    isStreaming: false,
    turns: [],
    thinkingDraft: '',
    subtasks: {},
    lastError: '',
  }
}
