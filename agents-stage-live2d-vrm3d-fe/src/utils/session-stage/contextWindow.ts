export const CONTEXT_PERCENT_ICON = '◔'
export const TOTAL_TOKENS_KEYS = ['total_tokens', 'totalTokens']
export const MODEL_CONTEXT_WINDOW_KEYS = ['model_context_window', 'modelContextWindow']

function toNumericValue(raw: unknown): number | null {
  if (raw === null || raw === undefined) return null
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw
  if (typeof raw === 'string') {
    const text = raw.trim()
    if (!text) return null
    const parsed = Number.parseFloat(text)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function pickFirstNumericValue(source: unknown, keys: string[]): number | null {
  if (!source || typeof source !== 'object') return null
  const dict = source as Record<string, unknown>
  for (const key of keys) {
    const value = toNumericValue(dict[key])
    if (value !== null) return value
  }
  return null
}

export function getContextUsagePercent(context: unknown): number | null {
  if (!context || typeof context !== 'object') return null
  const contextDict = context as Record<string, unknown>
  const lookupScopes: unknown[] = [
    contextDict,
    contextDict.usage,
    contextDict.context_usage,
    contextDict.token_usage,
    contextDict.window,
    contextDict.context_window,
  ]
  let totalTokens: number | null = null
  let modelContextWindow: number | null = null
  for (const scope of lookupScopes) {
    if (totalTokens === null) {
      totalTokens = pickFirstNumericValue(scope, TOTAL_TOKENS_KEYS)
    }
    if (modelContextWindow === null) {
      modelContextWindow = pickFirstNumericValue(scope, MODEL_CONTEXT_WINDOW_KEYS)
    }
    if (totalTokens !== null && modelContextWindow !== null) break
  }
  if (totalTokens === null || modelContextWindow === null || modelContextWindow <= 0) {
    return null
  }
  const computed = (totalTokens / modelContextWindow) * 100
  if (!Number.isFinite(computed)) return null
  return Math.min(100, Math.max(0, Math.round(computed)))
}

export function getContextPercentLabel(context: unknown): string {
  const percent = getContextUsagePercent(context)
  return percent === null ? `${CONTEXT_PERCENT_ICON} --%` : `${CONTEXT_PERCENT_ICON} ${percent}%`
}
