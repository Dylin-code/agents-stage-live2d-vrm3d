export type SessionState = 'IDLE' | 'THINKING' | 'TOOLING' | 'RESPONDING' | 'WAITING'
export type AgentBrand = string

export interface SessionRuntimeContext {
  [key: string]: unknown
  model?: string
  effort?: string
  permission_mode?: string
  approval_policy?: string
  sandbox_mode?: string
  plan_mode?: boolean
  plan_mode_fallback?: boolean
  total_tokens?: number
  model_context_window?: number
  primary_rate_remaining_percent?: number
  secondary_rate_remaining_percent?: number
}

export interface SessionStateMeta {
  originator?: string
  cwd?: string
  cwd_basename?: string
  last_event_type?: string
  branch?: string
  context?: SessionRuntimeContext
  inactive?: boolean
}

export interface SessionStateEvent {
  version: '1'
  event: 'session_state'
  session_id: string
  display_name?: string
  state: SessionState
  ts: string
  source: string
  agent_brand?: AgentBrand
  has_real_user_input?: boolean
  meta: SessionStateMeta
}

export interface SessionSnapshotItem {
  session_id: string
  display_name: string
  state: SessionState
  last_seen_at: string
  originator?: string
  cwd?: string
  cwd_basename?: string
  branch?: string
  last_event_type?: string
  agent_brand?: AgentBrand
  has_real_user_input?: boolean
  context?: SessionRuntimeContext
  active?: boolean
  inactive?: boolean
  pending_inactive?: boolean
  summary?: string
  manual_summoned_at?: string
}

export interface SessionSnapshotResponse {
  version: '1'
  generated_at: string
  sessions: SessionSnapshotItem[]
}

export interface SessionHistoryItem {
  session_id: string
  display_name: string
  state: SessionState
  last_seen_at: string
  active: boolean
  originator?: string
  cwd?: string
  cwd_basename?: string
  branch?: string
  last_event_type?: string
  agent_brand?: AgentBrand
  has_real_user_input?: boolean
  context?: SessionRuntimeContext
}

export interface SessionHistoryResponse {
  version: '1'
  generated_at: string
  sessions: SessionHistoryItem[]
}

export interface SessionConversationMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
}

export interface SessionConversationResponse {
  version: '1'
  generated_at: string
  session_id: string
  messages: SessionConversationMessage[]
}

export interface AvatarActor {
  session_id: string
  state: SessionState
  seat_index: number
  display_name: string
  last_seen_at: string
  model: any
  model_path: string
  phase: 'entering' | 'active' | 'exiting'
  target_x: number
  target_y: number
  target_scale: number
  exit_x: number
  last_motion: string
  agent_brand?: AgentBrand
  status_bubble?: any
  status_text?: any
  status_context_text?: any
  summary_label?: any
  summary_text?: any
  close_button?: any
  brand_badge?: any
}
