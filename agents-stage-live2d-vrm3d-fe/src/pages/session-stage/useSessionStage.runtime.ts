import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import * as PIXI from 'pixi.js'
import { Live2DModel as Live2DModelCubism4, MotionPreloadStrategy } from 'pixi-live2d-display/cubism4'
import { Live2DModel as Live2DModelCubism2 } from 'pixi-live2d-display/cubism2'

import type { Conversation, SystemSettings } from '../../types/message'
import type { AvatarActor, SessionHistoryItem, SessionSnapshotItem, SessionState, SessionStateEvent } from '../../types/sessionState'
import {
  createAgentSession,
  fetchAgentBrands,
  fetchSessionBridgeHistory,
} from '../../utils/api/sessionBridge'
import {
  DEFAULT_AGENT_BRANDS,
  buildAgentBrandCatalog,
  getAgentBrandModels,
  type AgentBrandCatalogItem,
} from '../../utils/agentBrands'
import { selectDirectoryViaLocalBridge } from '../../utils/api/localBridge'
import { type Live2DModelItem } from '../../utils/api/live2dModels'
import { getContextPercentLabel as getContextPercentLabelFromContext } from '../../utils/session-stage/contextWindow'
import { stateText as getSessionStateText } from '../../utils/session-stage/stateText'
import {
  getSessionActivityEpoch,
  mergeHistorySessions,
  sortSessionsByActivityDesc,
  touchManualSummonTime,
} from '../../utils/sessionStageState'
import { deriveStableVrm3dVisibleSessionIds } from './vrmVisibleSessionOrder'
import { createSessionStageActorRuntime } from './sessionStageActorRuntime'
import { createSessionStageChatAgentUtils, type SessionAgentUiOptions } from './sessionStageChatAgentUtils'
import type { OpenSessionChatOptions } from './sessionStageChatAgentUtils'
import { getDefaultBridgeWsUrl, getDefaultServerUrl } from '../../utils/serverUrl'
import { buildDefaultSystemSettings } from './sessionStageDefaults'
import { createSessionStageModelCatalogUtils } from './sessionStageModelCatalogUtils'
import {
  normalizeMotionToken,
  type ModelMotionEntry,
} from './live2dMotionUtils'

Live2DModelCubism4.registerTicker(PIXI.Ticker)
Live2DModelCubism2.registerTicker(PIXI.Ticker)

export type SessionStageRenderer = 'live2d' | 'vrm3d'

interface UseSessionStageOptions {
  renderer?: SessionStageRenderer
}

export function useSessionStage(options: UseSessionStageOptions = {}) {
const STORAGE_KEY = 'live2d-viewer-settings'
const STORAGE_KEY_CONVERSATIONS = 'live2d-viewer-conversations'
const MAX_SESSIONS = 4
const MAX_VRM3D_SESSIONS = 4
const HISTORY_DISPLAY_LIMIT = 20
const HISTORY_FETCH_LIMIT = 100
const CONVERSATION_LIMIT = 1000
const MAX_RECONNECT_DELAY_MS = 10000
const DESKTOP_LAYOUT_BREAKPOINT = 920
const SIDEBAR_FALLBACK_WIDTH = 340
const CHAT_DOCK_FALLBACK_HEIGHT = 440
const CHAT_DOCK_FALLBACK_WIDTH = 620
const STAGE_LEFT_PADDING = 20
const STAGE_TOP_PADDING = 96
const STAGE_BOTTOM_PADDING = 32
const BASE_STAGE_MODEL_SCALE = 0.28
const BUBBLE_RESERVED_HEIGHT = 62
const BUBBLE_TOP_MARGIN = 10
const DOUBLE_CLICK_THRESHOLD_MS = 280
const CONVERSATION_SYNC_DEBOUNCE_MS = 180
const rendererMode: SessionStageRenderer = options.renderer || 'live2d'
const isLive2DRenderer = rendererMode === 'live2d'

const stageCanvas = ref<HTMLCanvasElement | null>(null)
const connectionStatus = ref<'connecting' | 'connected' | 'disconnected'>('connecting')
const sessionStore = reactive<Record<string, SessionSnapshotItem>>({})
const globalPrimaryRateRemaining = ref<number | null>(null)
const globalSecondaryRateRemaining = ref<number | null>(null)

const actors = new Map<string, AvatarActor>()
const seatAssignments = new Map<string, number>()
const seatReservations = new Map<string, number>()
const sessionModelAssignments = new Map<string, string>()
const switchingActors = new Set<string>()
const lastPrimaryClickAtBySession = new Map<string, number>()
const conversationSyncTimers = new Map<string, number>()
const conversationSyncRunning = new Set<string>()
const conversationSyncQueued = new Set<string>()

let app: PIXI.Application | null = null
let ws: WebSocket | null = null
let reconnectTimer: number | null = null
let reconnectAttempt = 0
let disposed = false
let lastVisibilitySyncMs = 0

let serverUrl = getDefaultServerUrl()
let bridgeUrl = getDefaultBridgeWsUrl()
let modelPaths: string[] = ['assets/models/Senko_Normals/senko.model3.json']
let modelScale = 0.28
const modelMotionsByPath = new Map<string, ModelMotionEntry[]>()
const modelSemanticMotionsByPath = new Map<string, Record<string, string[]>>()
const modelPathsInput = ref('')
const chatSystemSettings = ref<SystemSettings>(buildDefaultSystemSettings())
const chatModalVisible = ref(false)
const sessionSidebarRef = ref<HTMLElement | null>(null)
const chatDockRef = ref<HTMLElement | null>(null)
const selectedChatSessionId = ref('')
const sessionSidebarWidth = ref<number>(SIDEBAR_FALLBACK_WIDTH)
const chatDockHeight = ref<number>(CHAT_DOCK_FALLBACK_HEIGHT)
const chatDockWidth = ref<number>(CHAT_DOCK_FALLBACK_WIDTH)
const sessionAgentOptionsBySession = reactive<Record<string, SessionAgentUiOptions>>({})
const agentBrandCatalog = ref<AgentBrandCatalogItem[]>(DEFAULT_AGENT_BRANDS)
const newSessionOpen = ref(false)
const creatingNewSession = ref(false)
const newSessionCwdSelection = ref('')
const newSessionForm = reactive({
  cwd: '',
  model: '',
  reasoning_effort: '',
  permission_mode: 'default',
  plan_mode: false,
  agent_brand: 'codex' as string,
})
const chatConversation = ref<Conversation>({
  key: '',
  label: '新對話',
  messages: [],
  createdAt: 0,
  updatedAt: 0,
  group: undefined,
})

const live2dModels = ref<Live2DModelItem[]>([])
const modelsRootDir = ref<string>('')
const modelsLoading = ref<boolean>(false)
const modelsError = ref<string>('')
const roleSettingsCollapsed = ref(true)

let layoutResizeObserver: ResizeObserver | null = null
let actorRuntime: ReturnType<typeof createSessionStageActorRuntime> | null = null
let openSessionChatProxy: (sessionId: string, options?: OpenSessionChatOptions) => void = () => {}

const bubbleColor: Record<SessionState, number> = {
  IDLE: 0x6f8298,
  THINKING: 0xcaa35b,
  TOOLING: 0x6e9bd6,
  RESPONDING: 0x4aa99a,
  WAITING: 0xc57d7d,
}

const BUBBLE_BOX_HEIGHT = 30
const BUBBLE_TAIL_HEIGHT = 10
const BUBBLE_HEAD_CLEARANCE = 8
const conversationSyncEventTypes = new Set([
  'user_message',
  'agent_message',
  'message',
  'function_call_output',
  'custom_tool_call',
  'error',
  'task_complete',
])

function sessionActivityEpoch(session: SessionSnapshotItem): number {
  return getSessionActivityEpoch(session)
}

const activitySortedCandidateSessions = computed(() => {
  return Object.values(sessionStore)
    .filter((x) => !x.inactive)
    .filter((session) => !isInternalWarmupSession(session))
    .sort((a, b) => sessionActivityEpoch(b) - sessionActivityEpoch(a))
})

const vrm3dVisibleSessionIds = ref<string[]>([])

const visibleSessions = computed(() => {
  const candidates = activitySortedCandidateSessions.value
  if (!isLive2DRenderer) {
    const candidateMap = new Map(candidates.map((session) => [session.session_id, session]))
    return vrm3dVisibleSessionIds.value
      .map((sessionId) => candidateMap.get(sessionId))
      .filter((session): session is SessionSnapshotItem => !!session)
  }
  const withLastFallback = candidates.length > 0 ? candidates : candidates.slice(0, 1)
  if (focusChatMode.value && selectedChatSessionId.value) {
    return withLastFallback
      .filter((item) => item.session_id === selectedChatSessionId.value)
      .slice(0, 1)
  }
  return withLastFallback.slice(0, MAX_SESSIONS)
})

function isInternalWarmupSession(session: SessionSnapshotItem): boolean {
  const name = (session.display_name || '').trim().toLowerCase()
  if (!name) return false
  if (name.startsWith('# agents.md instructions for ')) return true
  if (name.startsWith('warning: apply_patch was requested via')) return true
  if (name === 'tool loaded.' || name === 'tool loaded') return true
  const isDefaultSessionName = /^session-[0-9a-f]{8}$/i.test((session.display_name || '').trim())
  if (isDefaultSessionName && !session.has_real_user_input) return true
  return false
}

const historySessions = computed(() => {
  return sortSessionsByActivityDesc(
    Object.values(sessionStore)
      .filter((session) => !isInternalWarmupSession(session)),
  ).slice(0, HISTORY_DISPLAY_LIMIT)
})

const newSessionCwdOptions = computed(() => {
  const seen = new Set<string>()
  const options: string[] = []
  for (const session of historySessions.value) {
    const cwd = (session.cwd || '').trim()
    if (!cwd || seen.has(cwd)) continue
    seen.add(cwd)
    options.push(cwd)
  }
  return options
})

const agentBrandOptions = computed(() => agentBrandCatalog.value)

const newSessionModelOptions = computed(() => {
  return getAgentBrandModels(agentBrandCatalog.value, newSessionForm.agent_brand)
})

const focusChatMode = computed(() => {
  return chatModalVisible.value && !!selectedChatSessionId.value
})

const activeChatAgentOptions = computed<SessionAgentUiOptions>(() => {
  const sessionId = selectedChatSessionId.value
  if (!sessionId) return {}
  return sessionAgentOptionsBySession[sessionId] || {}
})

const activeChatSessionCwd = computed(() => {
  const sessionId = selectedChatSessionId.value
  if (!sessionId) return ''
  const options = sessionAgentOptionsBySession[sessionId]
  if (options?.cwd_override) {
    return options.cwd_override
  }
  return options?.cwd || sessionStore[sessionId]?.cwd || ''
})

const activeCount = computed(() => visibleSessions.value.length)

const connectionStatusText = computed(() => {
  if (connectionStatus.value === 'connected') return 'Bridge Connected'
  if (connectionStatus.value === 'connecting') return 'Bridge Connecting'
  return 'Bridge Disconnected'
})

const connectionStatusClass = computed(() => {
  if (connectionStatus.value === 'connected') return 'connected'
  if (connectionStatus.value === 'connecting') return 'connecting'
  return 'disconnected'
})

const globalRateLimitText = computed(() => {
  const primary = globalPrimaryRateRemaining.value
  const secondary = globalSecondaryRateRemaining.value
  const primaryText = primary === null ? '--%' : `${Math.round(primary)}%`
  const secondaryText = secondary === null ? '--%' : `${Math.round(secondary)}%`
  return `Agent剩餘狀態: ${primaryText} / ${secondaryText}`
})

function stateText(state: SessionState): string {
  return getSessionStateText(state)
}

function parseRemainingPercent(value: unknown): number | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'number' && Number.isFinite(value)) {
    return Math.min(100, Math.max(0, value))
  }
  if (typeof value === 'string') {
    const text = value.trim().replace('%', '')
    if (!text) return null
    const parsed = Number.parseFloat(text)
    if (!Number.isFinite(parsed)) return null
    return Math.min(100, Math.max(0, parsed))
  }
  return null
}

function applyGlobalRateLimitFromContext(context: Record<string, unknown> | undefined): void {
  if (!context) return
  const primary = parseRemainingPercent(context.primary_rate_remaining_percent)
  const secondary = parseRemainingPercent(context.secondary_rate_remaining_percent)
  if (primary !== null) {
    globalPrimaryRateRemaining.value = primary
  }
  if (secondary !== null) {
    globalSecondaryRateRemaining.value = secondary
  }
}

function refreshGlobalRateLimitFromStore(): void {
  const sessions = Object.values(sessionStore)
    .sort((a, b) => new Date(b.last_seen_at).getTime() - new Date(a.last_seen_at).getTime())
  for (const session of sessions) {
    const context = session.context as Record<string, unknown> | undefined
    if (!context) continue
    const primary = parseRemainingPercent(context.primary_rate_remaining_percent)
    const secondary = parseRemainingPercent(context.secondary_rate_remaining_percent)
    if (primary === null && secondary === null) continue
    if (primary !== null) {
      globalPrimaryRateRemaining.value = primary
    }
    if (secondary !== null) {
      globalSecondaryRateRemaining.value = secondary
    }
    return
  }
}

function getContextPercentLabel(actor: AvatarActor): string {
  const session = sessionStore[actor.session_id]
  return getContextPercentLabelFromContext(session?.context)
}

function drawBubbleBackground(
  graphics: PIXI.Graphics,
  width: number,
  height: number,
  borderColor: number,
): void {
  const radius = 12
  const tailWidth = 16
  const tailHeight = 10
  const tailHalf = tailWidth / 2
  const tailBaseY = height
  const tailTipY = height + tailHeight

  graphics.clear()
  graphics.lineStyle(1.5, borderColor, 0.95)
  graphics.beginFill(0x0b1a2d, 0.84)
  graphics.drawRoundedRect(-width / 2, 0, width, height, radius)
  graphics.moveTo(-tailHalf, tailBaseY)
  graphics.lineTo(0, tailTipY)
  graphics.lineTo(tailHalf, tailBaseY)
  graphics.closePath()
  graphics.endFill()
}

function updateActorBubble(actor: AvatarActor): void {
  const bubble = actor.status_bubble as PIXI.Container | undefined
  const text = actor.status_text as PIXI.Text | undefined
  const contextText = actor.status_context_text as PIXI.Text | undefined
  if (!bubble || !text) {
    return
  }

  const label = `${stateText(actor.state)}`
  text.text = label
  text.style = new PIXI.TextStyle({
    fill: 0xf4f8ff,
    fontSize: 13,
    fontWeight: '700',
  })

  const paddingX = 14
  const bubbleWidth = Math.max(86, text.width + paddingX * 2)
  const bubbleHeight = 30
  const background = bubble.getChildByName('bubble-bg') as PIXI.Graphics | null
  if (background) {
    drawBubbleBackground(background, bubbleWidth, bubbleHeight, bubbleColor[actor.state])
  }
  text.x = 0
  text.y = bubbleHeight / 2

  if (contextText) {
    contextText.text = getContextPercentLabel(actor)
    contextText.style = new PIXI.TextStyle({
      fill: 0xfff5d8,
      fontSize: 12,
      fontWeight: '700',
      stroke: 0x0b1a2d,
      strokeThickness: 3,
    })
  }
}

function buildSessionSummary(displayName: string): string {
  return (displayName || '').trim() || 'Untitled Session'
}

function updateActorSummary(actor: AvatarActor): void {
  const label = actor.summary_label as PIXI.Container | undefined
  const text = actor.summary_text as PIXI.Text | undefined
  if (!label || !text) {
    return
  }

  const session = sessionStore[actor.session_id]
  const summary = session?.summary || buildSessionSummary(actor.display_name)
  text.text = summary
  text.style = new PIXI.TextStyle({
    fill: 0xeff6ff,
    fontSize: 11,
    fontWeight: '700',
    wordWrap: false,
  })
  text.x = 0
  text.y = 0
}

function createActorCloseButton(onClick: () => void): PIXI.Container {
  const button = new PIXI.Container()
  const bg = new PIXI.Graphics()
  const icon = new PIXI.Text('x', {
    fill: 0xf5f8ff,
    fontSize: 12,
    fontWeight: '700',
  })
  icon.anchor.set(0.5, 0.5)
  bg.lineStyle(1, 0xc5d2e7, 0.95)
  bg.beginFill(0x0b1a2d, 0.86)
  bg.drawCircle(0, 0, 10)
  bg.endFill()
  button.addChild(bg)
  button.addChild(icon)
  button.interactive = true
  ;(button as any).buttonMode = true
  button.on?.('pointertap', (event: unknown) => {
    ;(event as { stopPropagation?: () => void })?.stopPropagation?.()
    onClick()
  })
  button.on?.('click', () => onClick())
  button.on?.('tap', () => onClick())
  return button
}

function relativeTime(value: string): string {
  const ts = new Date(value).getTime()
  if (Number.isNaN(ts)) return '--'
  const diffSec = Math.max(0, Math.floor((Date.now() - ts) / 1000))
  if (diffSec < 5) return '剛剛'
  if (diffSec < 60) return `${diffSec}s 前`
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m 前`
  return `${Math.floor(diffSec / 3600)}h 前`
}

const {
  buildSessionAgentOptions,
  ensureSessionAgentOptions,
  syncSessionAgentOptionsFromSnapshot,
  refreshSessionBranches,
  refreshActiveSessionBranches,
  handleActiveSessionAgentOptionsChange,
  openSessionChat,
  closeSessionChat,
  scheduleConversationHydration,
  handleChatConversationUpdate,
} = createSessionStageChatAgentUtils({
  storageKeyConversations: STORAGE_KEY_CONVERSATIONS,
  conversationLimit: CONVERSATION_LIMIT,
  conversationSyncDebounceMs: CONVERSATION_SYNC_DEBOUNCE_MS,
  serverUrl: () => serverUrl,
  sessionStore,
  sessionAgentOptionsBySession,
  selectedChatSessionId,
  chatModalVisible,
  chatConversation,
  chatSystemSettings,
  conversationSyncTimers,
  conversationSyncRunning,
  conversationSyncQueued,
  ensureSessionVisible: (sessionId) => {
    if (!isLive2DRenderer) return
    actorRuntime?.ensureSessionVisible(sessionId)
  },
  syncActorsWithVisibility: () => {
    if (!isLive2DRenderer) return
    actorRuntime?.syncActorsWithVisibility()
  },
  getBrandModels: (brand) => getAgentBrandModels(agentBrandCatalog.value, brand),
})

const {
  loadSettings,
  loadLive2DModelCatalog,
  loadModelMotionsForPath,
  applyModelPathsInput,
  isModelSelected,
  toggleModel,
  toAssetUrl,
} = createSessionStageModelCatalogUtils({
  storageKey: STORAGE_KEY,
  chatSystemSettings,
  modelPathsInput,
  modelsRootDir,
  modelsLoading,
  modelsError,
  live2dModels,
  modelMotionsByPath,
  modelSemanticMotionsByPath,
  sessionModelAssignments,
  getServerUrl: () => serverUrl,
  setServerUrl: (value) => {
    serverUrl = value
  },
  setBridgeUrl: (value) => {
    bridgeUrl = value
  },
  getModelPaths: () => modelPaths,
  setModelPaths: (value) => {
    modelPaths = value
  },
  getModelScale: () => modelScale,
  setModelScale: (value) => {
    modelScale = value
  },
  buildDefaultSystemSettings,
})

function toggleRoleSettingsPanel(): void { roleSettingsCollapsed.value = !roleSettingsCollapsed.value }
function isActorVisible(sessionId: string): boolean {
  return !isLive2DRenderer ? visibleSessions.value.some((session) => session.session_id === sessionId) : !!actors.get(sessionId) && actors.get(sessionId)?.phase !== 'exiting'
}

function openSessionChatBySessionId(sessionId: string): void {
  openSessionChat(sessionId, { forceSwitch: true })
}

async function loadLive2DModelByPath(modelPath: string): Promise<any> {
  const isModel3 = modelPath.endsWith('.model3.json')
  const ModelClass = isModel3 ? Live2DModelCubism4 : Live2DModelCubism2
  const model = await ModelClass.from(modelPath, {
    motionPreload: MotionPreloadStrategy.IDLE,
    autoInteract: false,
    autoUpdate: true,
  })
  return model
}

actorRuntime = createSessionStageActorRuntime({
  maxSessions: MAX_SESSIONS,
  historyLimit: HISTORY_FETCH_LIMIT,
  stageTopPadding: STAGE_TOP_PADDING,
  stageBottomPadding: STAGE_BOTTOM_PADDING,
  stageLeftPadding: STAGE_LEFT_PADDING,
  desktopLayoutBreakpoint: DESKTOP_LAYOUT_BREAKPOINT,
  doubleClickThresholdMs: DOUBLE_CLICK_THRESHOLD_MS,
  bubbleBoxHeight: BUBBLE_BOX_HEIGHT,
  bubbleTailHeight: BUBBLE_TAIL_HEIGHT,
  bubbleHeadClearance: BUBBLE_HEAD_CLEARANCE,
  bubbleTopMargin: BUBBLE_TOP_MARGIN,
  bubbleReservedHeight: BUBBLE_RESERVED_HEIGHT,
  baseStageModelScale: BASE_STAGE_MODEL_SCALE,
  getModelScale: () => modelScale,
  getApp: () => app,
  isDisposed: () => disposed,
  getModelPaths: () => modelPaths,
  getVisibleSessions: () => visibleSessions.value,
  isFocusChatMode: () => focusChatMode.value,
  isChatModalVisible: () => chatModalVisible.value,
  getSelectedChatSessionId: () => selectedChatSessionId.value,
  setSelectedChatSessionId: (value) => { selectedChatSessionId.value = value },
  setChatModalVisible: (value) => { chatModalVisible.value = value },
  getSessionSidebarWidth: () => sessionSidebarWidth.value,
  getChatDockWidth: () => chatDockWidth.value,
  getChatDockHeight: () => chatDockHeight.value,
  sessionStore,
  agentOptionsBySession: sessionAgentOptionsBySession,
  actors,
  seatAssignments,
  seatReservations,
  sessionModelAssignments,
  switchingActors,
  lastPrimaryClickAtBySession,
  modelMotionsByPath,
  modelSemanticMotionsByPath,
  mergeHistorySessions,
  touchManualSummonTime,
  syncSessionAgentOptionsFromSnapshot,
  applyGlobalRateLimitFromContext,
  refreshGlobalRateLimitFromStore,
  buildSessionSummary,
  updateActorBubble,
  updateActorSummary,
  createActorCloseButton,
  openSessionChat: (sessionId) => { openSessionChatProxy(sessionId, { forceSwitch: true }) },
  loadModelMotionsForPath,
  loadLive2DModelByPath,
})
openSessionChatProxy = openSessionChat

const {
  upsertHistorySessions,
  syncActorsWithVisibility,
  ensureSessionVisible,
  summonSession,
  dismissSessionImmediately,
  handleSessionCardClick,
  beginActorExit,
  updateActorLayout,
  renderTick,
} = actorRuntime!


function applySessionState(event: SessionStateEvent): void {
  upsertHistorySessions([
    {
      session_id: event.session_id,
      display_name: (event.display_name || '').trim() || `session-${event.session_id.slice(0, 8)}`,
      state: event.state,
      last_seen_at: event.ts,
      active: !event.meta?.inactive,
      originator: event.meta?.originator,
      cwd: event.meta?.cwd,
      cwd_basename: event.meta?.cwd_basename,
      branch: event.meta?.branch,
      last_event_type: event.meta?.last_event_type,
      context: event.meta?.context,
      agent_brand: event.agent_brand,
      has_real_user_input: !!event.has_real_user_input,
    },
  ])
  const session = sessionStore[event.session_id]
  if (!session) return
  applyGlobalRateLimitFromContext(event.meta?.context as Record<string, unknown> | undefined)
  session.summary = buildSessionSummary(session.display_name)
  session.active = !event.meta?.inactive
  if (event.meta?.inactive) {
    const isChatting = chatModalVisible.value && selectedChatSessionId.value === event.session_id
    if (isChatting) {
      session.pending_inactive = true
    } else {
      session.inactive = true
      session.pending_inactive = false
      beginActorExit(event.session_id)
      syncActorsWithVisibility()
      return
    }
  } else {
    session.inactive = false
    session.pending_inactive = false
  }
  syncActorsWithVisibility()
  actorRuntime.updateActorState(event.session_id, event.state, event.ts, event.meta?.last_event_type)
  const eventType = normalizeMotionToken(event.meta?.last_event_type || '')
  if (
    chatModalVisible.value
    && selectedChatSessionId.value === event.session_id
    && conversationSyncEventTypes.has(eventType)
  ) {
    scheduleConversationHydration(event.session_id)
  }
  syncSessionAgentOptionsFromSnapshot(session)
  refreshGlobalRateLimitFromStore()
}

function syncHistory(items: SessionHistoryItem[]): void {
  upsertHistorySessions(items)
  for (const session of Object.values(sessionStore)) {
    session.summary = buildSessionSummary(session.display_name)
    if (session.active) {
      session.inactive = false
    }
    syncSessionAgentOptionsFromSnapshot(session)
  }
  syncActorsWithVisibility()
}

async function refreshHistory(): Promise<void> {
  const history = await fetchSessionBridgeHistory(serverUrl, HISTORY_FETCH_LIMIT)
  syncHistory(history.sessions)
}

function measureLayoutBounds(): void {
  if (sessionSidebarRef.value) {
    const rect = sessionSidebarRef.value.getBoundingClientRect()
    if (!focusChatMode.value && rect.width > 4) {
      sessionSidebarWidth.value = Math.max(220, Math.round(rect.width))
    } else {
      sessionSidebarWidth.value = 0
    }
  } else {
    sessionSidebarWidth.value = focusChatMode.value ? 0 : SIDEBAR_FALLBACK_WIDTH
  }
  if (chatModalVisible.value && chatDockRef.value) {
    const rect = chatDockRef.value.getBoundingClientRect()
    chatDockHeight.value = Math.max(280, Math.round(rect.height))
    chatDockWidth.value = Math.max(320, Math.round(rect.width))
  } else {
    chatDockHeight.value = CHAT_DOCK_FALLBACK_HEIGHT
    chatDockWidth.value = CHAT_DOCK_FALLBACK_WIDTH
  }
  updateActorLayout()
}

function setupLayoutObserver(): void {
  if (typeof ResizeObserver === 'undefined') {
    measureLayoutBounds()
    return
  }
  if (layoutResizeObserver) {
    layoutResizeObserver.disconnect()
  }
  layoutResizeObserver = new ResizeObserver(() => {
    measureLayoutBounds()
  })
  if (sessionSidebarRef.value) {
    layoutResizeObserver.observe(sessionSidebarRef.value)
  }
  if (chatDockRef.value) {
    layoutResizeObserver.observe(chatDockRef.value)
  }
  measureLayoutBounds()
}

function resetNewSessionForm(): void {
  newSessionCwdSelection.value = ''
  newSessionForm.cwd = ''
  newSessionForm.model = ''
  newSessionForm.reasoning_effort = ''
  newSessionForm.permission_mode = 'default'
  newSessionForm.plan_mode = false
  newSessionForm.agent_brand = 'codex'
}

function syncNewSessionCwdSelection(): void {
  const cwd = newSessionForm.cwd.trim()
  if (!cwd) {
    newSessionCwdSelection.value = ''
    return
  }
  newSessionCwdSelection.value = newSessionCwdOptions.value.includes(cwd) ? cwd : '__pick_new__'
}

async function pickDirectoryFromSystem(): Promise<string> {
  const win = window as Window & {
    electronAPI?: {
      selectDirectory?: () => Promise<string | null | undefined>
    }
    showDirectoryPicker?: () => Promise<unknown>
  }
  const pickerPath = await win.electronAPI?.selectDirectory?.()
  if (pickerPath && String(pickerPath).trim()) {
    return String(pickerPath).trim()
  }

  try {
    const bridgedPath = await selectDirectoryViaLocalBridge(serverUrl, {
      title: '選擇工作目錄',
      default_path: newSessionForm.cwd.trim() || undefined,
    })
    if (bridgedPath) {
      return bridgedPath
    }
  } catch (error) {
    console.warn('Local directory bridge unavailable, fallback to browser picker', error)
  }

  if (typeof win.showDirectoryPicker === 'function') {
    try {
      await win.showDirectoryPicker()
    } catch {
      // 使用者取消時不提示錯誤
    }
  }
  return ''
}

async function onNewSessionCwdSelectionChange(): Promise<void> {
  const selected = newSessionCwdSelection.value
  if (!selected) return
  if (selected !== '__pick_new__') {
    newSessionForm.cwd = selected
    return
  }
  const picked = await pickDirectoryFromSystem()
  if (!picked) {
    newSessionCwdSelection.value = ''
    message.warning('目前無法透過本地橋接取得絕對路徑，請確認後端服務可用，或手動輸入工作目錄')
    return
  }
  newSessionForm.cwd = picked
  syncNewSessionCwdSelection()
}

async function createNewSession(): Promise<void> {
  if (creatingNewSession.value) {
    return
  }
  const cwd = newSessionForm.cwd.trim()
  if (!cwd) {
    message.warning('請先輸入工作目錄')
    return
  }
  creatingNewSession.value = true
  try {
    const brand = newSessionForm.agent_brand || 'codex'
    // Use unified agent endpoint for brand-aware session creation.
    const result = await createAgentSession(serverUrl, {
      cwd,
      model: newSessionForm.model || undefined,
      reasoning_effort: newSessionForm.reasoning_effort || undefined,
      permission_mode: newSessionForm.permission_mode || 'default',
      plan_mode: newSessionForm.plan_mode,
      agent_brand: brand as any,
    })
    const sessionId = String(result.session_id || '').trim()
    if (!sessionId) {
      throw new Error('後端未回傳有效 session_id')
    }
    const now = new Date().toISOString()
    const session: SessionSnapshotItem = {
      session_id: sessionId,
      display_name: `session-${sessionId.slice(0, 8)}`,
      state: 'WAITING',
      last_seen_at: now,
      active: true,
      inactive: false,
      summary: `session-${sessionId.slice(0, 8)}`,
      cwd,
      cwd_basename: cwd.split('/').filter(Boolean).pop() || cwd,
      branch: String(result.branch || ''),
      agent_brand: (String(result.agent_brand || brand)) as any,
      context: {
        model: String(result.model || ''),
        effort: String(result.effort || ''),
        permission_mode: String(result.permission_mode || 'default'),
        approval_policy: String(result.approval_policy || ''),
        sandbox_mode: String(result.sandbox_mode || ''),
        plan_mode: !!result.plan_mode,
        plan_mode_fallback: !!result.plan_mode_fallback,
      },
    }
    sessionStore[sessionId] = session
    sessionAgentOptionsBySession[sessionId] = buildSessionAgentOptions(session)
    newSessionOpen.value = false
    resetNewSessionForm()
    summonSession(sessionId)
    openSessionChat(sessionId, { forceSwitch: true })
    await refreshSessionBranches(sessionId)
    message.success(`已建立新 session：${sessionId.slice(0, 8)}`)
    void refreshHistory()
  } catch (error) {
    message.error(`建立 session 失敗：${String((error as Error)?.message || error || 'unknown error')}`)
  } finally {
    creatingNewSession.value = false
  }
}

function clearReconnectTimer(): void { if (reconnectTimer !== null) { window.clearTimeout(reconnectTimer); reconnectTimer = null } }

function scheduleReconnect(): void {
  if (disposed) return
  clearReconnectTimer()
  const delay = Math.min(1000 * 2 ** reconnectAttempt, MAX_RECONNECT_DELAY_MS)
  reconnectAttempt += 1
  reconnectTimer = window.setTimeout(() => { connectBridge() }, delay)
}

function connectBridge(): void {
  clearReconnectTimer()
  if (ws) {
    try {
      ws.close()
    } catch {
      // ignore
    }
    ws = null
  }

  connectionStatus.value = 'connecting'
  ws = new WebSocket(bridgeUrl)

  ws.onopen = async () => {
    if (disposed) return
    connectionStatus.value = 'connected'
    reconnectAttempt = 0
    try {
      await refreshHistory()
    } catch (error) {
      console.error('Failed to refresh history on reconnect', error)
    }
  }

  ws.onmessage = (message) => {
    if (disposed) return
    try {
      const data = JSON.parse(message.data) as SessionStateEvent
      if (data.event === 'session_state') {
        applySessionState(data)
      }
    } catch (error) {
      console.error('Invalid bridge event', error)
    }
  }

  ws.onclose = () => {
    if (disposed) return
    connectionStatus.value = 'disconnected'
    scheduleReconnect()
  }

  ws.onerror = () => {
    if (disposed) return
    connectionStatus.value = 'disconnected'
    ws?.close()
  }
}

function onWindowResize(): void {
  if (app) app.renderer.resize(window.innerWidth, window.innerHeight)
  measureLayoutBounds()
  updateActorLayout()
}

function onCanvasContextMenu(event: MouseEvent): void {
  event.preventDefault()
}

watch(chatModalVisible, (visible) => {
  if (!visible) {
    for (const session of Object.values(sessionStore)) {
      if (session.pending_inactive) {
        session.inactive = true
        session.pending_inactive = false
        beginActorExit(session.session_id)
      }
    }
    syncActorsWithVisibility()
  }
  window.setTimeout(() => {
    setupLayoutObserver()
    measureLayoutBounds()
  }, 0)
})

watch(
  activitySortedCandidateSessions,
  (candidates) => {
    if (isLive2DRenderer) return
    vrm3dVisibleSessionIds.value = deriveStableVrm3dVisibleSessionIds(
      vrm3dVisibleSessionIds.value,
      candidates,
      MAX_VRM3D_SESSIONS,
    )
  },
  { deep: true, immediate: true },
)

watch(
  () => newSessionForm.cwd,
  () => {
    syncNewSessionCwdSelection()
  },
)

async function refreshAgentBrandCatalog(): Promise<void> {
  try {
    const response = await fetchAgentBrands(serverUrl)
    agentBrandCatalog.value = buildAgentBrandCatalog(response.brands)
  } catch (error) {
    console.warn('Failed to fetch agent brands, fallback to local defaults', error)
    agentBrandCatalog.value = DEFAULT_AGENT_BRANDS
  }
}

onMounted(async () => {
  loadSettings()
  await refreshAgentBrandCatalog()
  if (isLive2DRenderer) {
    await loadLive2DModelCatalog()
    await Promise.all(modelPaths.map((path) => loadModelMotionsForPath(path)))

    if (!stageCanvas.value) {
      console.error('Session stage canvas is missing')
      return
    }

    app = new PIXI.Application({
      view: stageCanvas.value,
      transparent: true,
      autoStart: true,
      width: window.innerWidth,
      height: window.innerHeight,
      backgroundAlpha: 0,
    })
    app.stage.sortableChildren = true
    app.ticker.add(renderTick)
    stageCanvas.value.addEventListener('contextmenu', onCanvasContextMenu)
  }

  try {
    await refreshHistory()
  } catch (error) {
    console.error('Failed to fetch initial history', error)
  }

  connectBridge()
  window.addEventListener('resize', onWindowResize)
  setupLayoutObserver()
  measureLayoutBounds()
})

onUnmounted(() => {
  disposed = true
  clearReconnectTimer()
  if (ws) {
    try {
      ws.close()
    } catch {
      // ignore
    }
    ws = null
  }
  window.removeEventListener('resize', onWindowResize)
  if (stageCanvas.value) {
    stageCanvas.value.removeEventListener('contextmenu', onCanvasContextMenu)
  }
  if (app) {
    app.destroy(true)
    app = null
  }
  if (layoutResizeObserver) {
    layoutResizeObserver.disconnect()
    layoutResizeObserver = null
  }
  actors.clear()
  seatAssignments.clear()
  seatReservations.clear()
  sessionModelAssignments.clear()
  for (const timer of conversationSyncTimers.values()) {
    window.clearTimeout(timer)
  }
  conversationSyncTimers.clear()
  conversationSyncRunning.clear()
  conversationSyncQueued.clear()
})

  return {
    MAX_SESSIONS,
    isLive2DRenderer,
    stageCanvas,
    visibleSessions,
    connectionStatusClass,
    connectionStatusText,
    globalRateLimitText,
    activeCount,
    roleSettingsCollapsed,
    toggleRoleSettingsPanel,
    modelPathsInput,
    applyModelPathsInput,
    modelsRootDir,
    modelsLoading,
    modelsError,
    live2dModels,
    isModelSelected,
    toggleModel,
    toAssetUrl,
    sessionSidebarRef,
    chatModalVisible,
    focusChatMode,
    newSessionOpen,
    newSessionCwdSelection,
    onNewSessionCwdSelectionChange,
    newSessionCwdOptions,
    newSessionForm,
    agentBrandOptions,
    newSessionModelOptions,
    creatingNewSession,
    createNewSession,
    historySessions,
    isActorVisible,
    selectedChatSessionId,
    handleSessionCardClick,
    stateText,
    relativeTime,
    chatDockRef,
    chatConversation,
    activeChatSessionCwd,
    closeSessionChat,
    openSessionChatBySessionId,
    handleChatConversationUpdate,
    chatSystemSettings,
    activeChatAgentOptions,
    handleActiveSessionAgentOptionsChange,
    refreshActiveSessionBranches,
  }
}
