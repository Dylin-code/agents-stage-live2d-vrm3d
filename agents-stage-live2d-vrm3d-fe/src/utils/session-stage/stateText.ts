import type { SessionState } from '../../types/sessionState'

export const SESSION_STATE_LABEL: Record<SessionState, string> = {
  IDLE: '待命',
  THINKING: '思考中',
  TOOLING: '工具中',
  RESPONDING: '回答中',
  WAITING: '等待輸入',
}

export function stateText(state: SessionState): string {
  return SESSION_STATE_LABEL[state]
}
