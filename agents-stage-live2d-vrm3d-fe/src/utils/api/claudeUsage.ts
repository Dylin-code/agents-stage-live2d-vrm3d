import { getDefaultServerUrl } from '../serverUrl'

const DEFAULT_SERVER_URL = getDefaultServerUrl()
const CLAUDE_USAGE_PATH = '/api/session-bridge/claude-usage'

export interface ClaudeRateLimit {
  utilization: number
  remaining: number
  resets_at: string | null
}

export interface ClaudeExtraUsage {
  is_enabled: boolean
  monthly_limit: number | null
  used_credits: number | null
  utilization: number | null
}

export interface ClaudeUsageSummary {
  five_hour: ClaudeRateLimit | null
  seven_day: ClaudeRateLimit | null
  extra_usage: ClaudeExtraUsage | null
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '')
}

export async function fetchClaudeUsage(serverUrl?: string): Promise<ClaudeUsageSummary | null> {
  const base = trimTrailingSlash(serverUrl || DEFAULT_SERVER_URL)
  const url = `${base}${CLAUDE_USAGE_PATH}`
  try {
    const resp = await fetch(url, { signal: AbortSignal.timeout(10_000) })
    if (!resp.ok) return null
    return await resp.json() as ClaudeUsageSummary
  } catch {
    return null
  }
}
