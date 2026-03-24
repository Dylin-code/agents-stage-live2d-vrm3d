import { normalizeMotionToken } from './live2dMotionUtils'

const CONVERSATION_SYNC_EVENT_TYPES = new Set([
  'user_message',
  'agent_message',
  'assistant_message',
  'message',
  'function_call_output',
  'custom_tool_call',
  'agent_tool_call_begin',
  'agent_tool_call_finish',
  'request_user_input',
  'error',
  'task_complete',
])

export function shouldHydrateConversationForEvent(eventType: string): boolean {
  return CONVERSATION_SYNC_EVENT_TYPES.has(normalizeMotionToken(eventType || ''))
}
