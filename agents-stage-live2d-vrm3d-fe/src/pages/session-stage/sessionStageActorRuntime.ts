import * as PIXI from 'pixi.js'

import type { AvatarActor, SessionHistoryItem, SessionSnapshotItem, SessionState, SessionStateEvent } from '../../types/sessionState'
import {
  findMotionByCandidates,
  getSemanticMotionCandidates,
  pickModelPathForSession,
  pickMotionForState,
  pickNextModelPath,
  pickRandomSupportedMotion,
  SUMMON_MOTION_CANDIDATES,
  type ModelMotionEntry,
} from './live2dMotionUtils'

/* ---------- Brand badge texture helper (cross-browser SVG→Canvas) ---------- */
const BADGE_PX = 36 // render at 2× for retina clarity
const _badgeTextureCache = new Map<string, PIXI.Texture>()

function getBrandBadgeTexture(brand: string): PIXI.Texture {
  const cached = _badgeTextureCache.get(brand)
  if (cached) return cached
  // Return a temporary 1×1 transparent texture; replace once the image loads.
  const placeholder = PIXI.Texture.EMPTY
  const img = new Image(BADGE_PX, BADGE_PX)
  img.onload = () => {
    const canvas = document.createElement('canvas')
    canvas.width = BADGE_PX
    canvas.height = BADGE_PX
    const ctx = canvas.getContext('2d')!
    ctx.drawImage(img, 0, 0, BADGE_PX, BADGE_PX)
    const tex = PIXI.Texture.from(canvas, { resolution: 2 })
    _badgeTextureCache.set(brand, tex)
    // Patch all existing sprites that still reference the placeholder.
    // PIXI Sprite.texture setter triggers re-render automatically.
  }
  img.crossOrigin = 'anonymous'
  img.src = `/brand/${brand}-badge.svg`
  return placeholder
}

interface SessionAgentUiOptions {
  model?: string
  reasoning_effort?: string
  permission_mode?: string
  plan_mode?: boolean
  cwd_override?: string
  git_branch?: string
  available_models?: string[]
  available_branches?: string[]
  cwd?: string
}

export function shouldReplayActorMotion(previousState: SessionState, nextState: SessionState): boolean {
  return previousState !== nextState
}

export function createSessionStageActorRuntime(args: {
  maxSessions: number
  historyLimit: number
  stageTopPadding: number
  stageBottomPadding: number
  stageLeftPadding: number
  desktopLayoutBreakpoint: number
  doubleClickThresholdMs: number
  bubbleBoxHeight: number
  bubbleTailHeight: number
  bubbleHeadClearance: number
  bubbleTopMargin: number
  bubbleReservedHeight: number
  baseStageModelScale: number
  getModelScale: () => number
  getApp: () => PIXI.Application | null
  isDisposed: () => boolean
  getModelPaths: () => string[]
  getVisibleSessions: () => SessionSnapshotItem[]
  isFocusChatMode: () => boolean
  isChatModalVisible: () => boolean
  getSelectedChatSessionId: () => string
  setSelectedChatSessionId: (value: string) => void
  setChatModalVisible: (value: boolean) => void
  getSessionSidebarWidth: () => number
  getChatDockWidth: () => number
  getChatDockHeight: () => number
  sessionStore: Record<string, SessionSnapshotItem>
  agentOptionsBySession: Record<string, SessionAgentUiOptions>
  actors: Map<string, AvatarActor>
  seatAssignments: Map<string, number>
  seatReservations: Map<string, number>
  sessionModelAssignments: Map<string, string>
  switchingActors: Set<string>
  lastPrimaryClickAtBySession: Map<string, number>
  modelMotionsByPath: Map<string, ModelMotionEntry[]>
  modelSemanticMotionsByPath: Map<string, Record<string, string[]>>
  mergeHistorySessions: (existing: SessionHistoryItem[], items: SessionHistoryItem[], limit: number) => SessionHistoryItem[]
  touchManualSummonTime: (item: SessionHistoryItem, ts: string) => SessionHistoryItem
  syncSessionAgentOptionsFromSnapshot: (session: SessionSnapshotItem) => void
  applyGlobalRateLimitFromContext: (context: Record<string, unknown> | undefined) => void
  refreshGlobalRateLimitFromStore: () => void
  buildSessionSummary: (displayName: string) => string
  updateActorBubble: (actor: AvatarActor) => void
  updateActorSummary: (actor: AvatarActor) => void
  createActorCloseButton: (onClick: () => void) => PIXI.Container
  openSessionChat: (sessionId: string) => void
  scheduleConversationHydration: (sessionId: string) => void
  loadModelMotionsForPath: (modelPath: string) => Promise<void>
  loadLive2DModelByPath: (modelPath: string) => Promise<any>
}) {
  const {
    maxSessions,
    historyLimit,
    stageTopPadding,
    stageBottomPadding,
    stageLeftPadding,
    desktopLayoutBreakpoint,
    doubleClickThresholdMs,
    bubbleBoxHeight,
    bubbleTailHeight,
    bubbleHeadClearance,
    bubbleTopMargin,
    bubbleReservedHeight,
    baseStageModelScale,
    getModelScale,
    getApp,
    isDisposed,
    getModelPaths,
    getVisibleSessions,
    isFocusChatMode,
    isChatModalVisible,
    getSelectedChatSessionId,
    setSelectedChatSessionId,
    setChatModalVisible,
    getSessionSidebarWidth,
    getChatDockWidth,
    getChatDockHeight,
    sessionStore,
    agentOptionsBySession,
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
    openSessionChat,
    loadModelMotionsForPath,
    loadLive2DModelByPath,
  } = args

  let lastVisibilitySyncMs = 0

  function getLayoutMetrics() {
    const bounds = getStageBounds()
    const count = Math.max(1, Math.min(maxSessions, getVisibleSessions().length))
    const cols = Math.ceil(Math.sqrt(count))
    const rows = Math.ceil(count / cols)
    const rowCounts = Array.from({ length: rows }, (_, row) => {
      const rowStart = row * cols
      return Math.max(0, Math.min(cols, count - rowStart))
    })
    return { bounds, cols, rows, count, rowCounts }
  }

  function computeActorTargetScale(model: any, cell: { width: number; height: number }, activeCount: number): number {
    const prevScaleX = model.scale.x
    const prevScaleY = model.scale.y
    model.scale.set(1)
    const naturalWidth = Math.max(1, model.width)
    const naturalHeight = Math.max(1, model.height)
    model.scale.set(prevScaleX, prevScaleY)
    const densityFactor =
      activeCount <= 1 ? 0.84 : activeCount <= 2 ? 0.81 : activeCount <= 4 ? 0.78 : activeCount <= 6 ? 0.75 : 0.72
    const availableHeight = Math.max(96, cell.height - bubbleReservedHeight)
    const fitByHeight = (availableHeight * densityFactor) / naturalHeight
    const fitByWidth = (cell.width * (densityFactor - 0.04)) / naturalWidth
    const fitScale = Math.min(fitByHeight, fitByWidth)
    const aspect = naturalHeight / naturalWidth
    const aspectCorrection =
      aspect >= 2.2 ? 0.82 : aspect >= 1.8 ? 0.88 : aspect <= 0.95 ? 0.9 : aspect <= 1.2 ? 0.94 : 1
    const tuning = getModelScale() / baseStageModelScale
    return Math.max(0.05, Math.min(2.2, fitScale * tuning * aspectCorrection))
  }

  function reflowActiveActorSeats(): void {
    const orderedActiveIds = getVisibleSessions().map((session) => session.session_id).filter((id) => actors.has(id))
    orderedActiveIds.forEach((sessionId, index) => {
      const actor = actors.get(sessionId)
      if (!actor) return
      actor.seat_index = index
      seatAssignments.set(sessionId, index)
    })
  }

  function getSeatPosition(seatIndex: number): { x: number; y: number } {
    const app = getApp()
    if (!app) return { x: 0, y: 0 }
    const layout = getLayoutMetrics()
    const bounds = layout.bounds
    const rowCounts = layout.rowCounts
    const rows = layout.rows
    const cellHeight = bounds.height / rows
    let row = 0
    let indexInRow = seatIndex
    while (row < rowCounts.length && indexInRow >= rowCounts[row]) {
      indexInRow -= rowCounts[row]
      row += 1
    }
    if (row >= rowCounts.length) {
      row = rowCounts.length - 1
      indexInRow = Math.max(0, rowCounts[row] - 1)
    }
    const rowCount = Math.max(1, rowCounts[row] || 1)
    const cellWidth = bounds.width / Math.max(1, rowCount)
    const rowStartX = bounds.left + (bounds.width - cellWidth * rowCount) / 2
    const x = rowStartX + (indexInRow + 0.5) * cellWidth
    const rowDepthBias = rows <= 1 ? 0.9 : rows === 2 ? 0.84 : rows === 3 ? 0.79 : 0.75
    const y = bounds.top + (row + rowDepthBias) * cellHeight
    return { x, y }
  }

  function getSeatCellSize(): { width: number; height: number } {
    const app = getApp()
    if (!app) return { width: 0, height: 0 }
    const layout = getLayoutMetrics()
    const bounds = layout.bounds
    const maxRowCount = Math.max(1, ...layout.rowCounts)
    return { width: bounds.width / maxRowCount, height: bounds.height / layout.rows }
  }

  function getStageBounds(): { left: number; top: number; width: number; height: number } {
    const app = getApp()
    if (!app) return { left: 0, top: 0, width: 0, height: 0 }
    const fullWidth = app.renderer.width
    const fullHeight = app.renderer.height
    const isDesktop = window.innerWidth > desktopLayoutBreakpoint
    const showDesktopRightChat = isDesktop && isFocusChatMode()
    const sidebarReserved = isDesktop && !isFocusChatMode() ? Math.max(200, getSessionSidebarWidth() + 18) : 0
    const desiredChatReserved = showDesktopRightChat ? Math.max(380, getChatDockWidth() + 28) : 0
    const maxRightReserved = Math.max(0, fullWidth - stageLeftPadding - 220 - sidebarReserved)
    const chatRightReserved = Math.min(desiredChatReserved, maxRightReserved)
    const chatBottomReserved = isChatModalVisible() && !isDesktop
      ? Math.min(Math.max(260, getChatDockHeight() + 12), Math.floor(fullHeight * 0.58))
      : 0
    const left = stageLeftPadding
    const right = stageLeftPadding + sidebarReserved + chatRightReserved
    const top = stageTopPadding
    const width = Math.max(220, fullWidth - left - right)
    const bottom = stageBottomPadding + chatBottomReserved
    const height = Math.max(220, fullHeight - stageTopPadding - bottom)
    return { left, top, width, height }
  }

  function nextAvailableSeat(): number | null {
    const used = new Set<number>(seatAssignments.values())
    for (const reservation of seatReservations.values()) used.add(reservation)
    for (let idx = 0; idx < maxSessions; idx += 1) {
      if (!used.has(idx)) return idx
    }
    return null
  }

  function upsertHistorySessions(items: SessionHistoryItem[]): void {
    const existing = Object.values(sessionStore).map((item) => ({
      session_id: item.session_id,
      display_name: item.display_name,
      state: item.state,
      last_seen_at: item.last_seen_at,
      active: !!item.active,
      has_real_user_input: !!item.has_real_user_input,
      manual_summoned_at: item.manual_summoned_at,
    }))
    const merged = mergeHistorySessions(existing, items, historyLimit)
    const keepIds = new Set(merged.map((item) => item.session_id))
    for (const sessionId of Object.keys(sessionStore)) {
      if (!keepIds.has(sessionId)) {
        beginActorExit(sessionId)
        delete sessionStore[sessionId]
        delete agentOptionsBySession[sessionId]
        if (getSelectedChatSessionId() === sessionId) {
          setChatModalVisible(false)
          setSelectedChatSessionId('')
        }
      }
    }
    for (const item of merged) {
      const existingSession = sessionStore[item.session_id]
      const nextSession: SessionSnapshotItem = {
        ...existingSession,
        session_id: item.session_id,
        display_name: item.display_name,
        state: item.state,
        last_seen_at: item.last_seen_at,
        active: item.active,
        inactive: existingSession?.inactive && !item.active,
        has_real_user_input: !!item.has_real_user_input || !!existingSession?.has_real_user_input,
        summary: existingSession?.summary || buildSessionSummary(item.display_name),
        manual_summoned_at: item.manual_summoned_at || existingSession?.manual_summoned_at,
        originator: item.originator || existingSession?.originator,
        cwd: item.cwd || existingSession?.cwd,
        cwd_basename: item.cwd_basename || existingSession?.cwd_basename,
        branch: item.branch || existingSession?.branch,
        last_event_type: item.last_event_type || existingSession?.last_event_type,
        context: { ...(existingSession?.context || {}), ...(item.context || {}) },
        agent_brand: item.agent_brand || existingSession?.agent_brand,
      }
      sessionStore[item.session_id] = nextSession
      syncSessionAgentOptionsFromSnapshot(nextSession)
      applyGlobalRateLimitFromContext(nextSession.context as Record<string, unknown> | undefined)
      const actor = actors.get(item.session_id)
      if (actor) {
        updateActorBubble(actor)
        updateActorSummary(actor)
      }
    }
    refreshGlobalRateLimitFromStore()
  }

  function syncActorsWithVisibility(): void {
    const visibleSet = new Set(getVisibleSessions().map((item) => item.session_id))
    for (const [sessionId] of actors.entries()) {
      if (!visibleSet.has(sessionId)) beginActorExit(sessionId)
    }
    for (const session of getVisibleSessions()) {
      const existingActor = actors.get(session.session_id)
      if (existingActor && existingActor.phase === 'exiting') {
        existingActor.phase = 'active'
        existingActor.model.alpha = 1
        if (existingActor.status_bubble) existingActor.status_bubble.alpha = 1
        if (existingActor.status_context_text) existingActor.status_context_text.alpha = 1
        if (existingActor.brand_badge) existingActor.brand_badge.alpha = 1
        if (existingActor.summary_label) existingActor.summary_label.alpha = 1
        if (existingActor.close_button) existingActor.close_button.alpha = 1
        continue
      }
      if (!existingActor) {
        spawnActor(session).catch((error) => {
          console.error('Failed to spawn actor', error)
        })
      }
    }
    reflowActiveActorSeats()
    updateActorLayout()
  }

  function ensureSessionVisible(sessionId: string): void {
    const session = sessionStore[sessionId]
    if (!session) return
    const touched = touchManualSummonTime(
      {
        session_id: session.session_id,
        display_name: session.display_name,
        state: session.state,
        last_seen_at: session.last_seen_at,
        active: !!session.active,
        manual_summoned_at: session.manual_summoned_at,
      },
      new Date().toISOString(),
    )
    session.manual_summoned_at = touched.manual_summoned_at
    session.inactive = false
    syncActorsWithVisibility()
  }

  function summonSession(sessionId: string): void {
    ensureSessionVisible(sessionId)
    queueSummonGesture(sessionId)
  }

  function dismissSessionImmediately(sessionId: string): void {
    const session = sessionStore[sessionId]
    if (!session) return
    session.inactive = true
    session.active = false
    session.manual_summoned_at = undefined
    beginActorExit(sessionId, true)
    syncActorsWithVisibility()
  }

  function handleSessionCardClick(sessionId: string): void {
    summonSession(sessionId)
  }

  function playActorMotionEntry(actor: AvatarActor, motion: ModelMotionEntry): boolean {
    try {
      actor.model.motion(motion.group, motion.index)
      actor.last_motion = motion.key
      return true
    } catch (error) {
      try {
        actor.model.motion(motion.name || motion.group)
        actor.last_motion = motion.key
        return true
      } catch (fallbackError) {
        console.error('Failed to play motion', motion, error, fallbackError)
        return false
      }
    }
  }

  function playActorFallbackMotion(actor: AvatarActor): boolean {
    const candidates = ['idle', 'Idle', 'home', 'main_1', 'main_2', 'main_3', 'wait_1', 'wait_2']
    for (const name of candidates) {
      try {
        actor.model.motion(name)
        actor.last_motion = name
        return true
      } catch {
        // try next fallback
      }
    }
    return false
  }

  function playActorMotion(actor: AvatarActor, state: SessionState, lastEventType?: string): void {
    let motion = pickMotionForState({
      state,
      actorModelPath: actor.model_path,
      lastEventType,
      modelMotionsByPath,
      modelSemanticMotionsByPath,
    })
    if (!motion) motion = pickRandomSupportedMotion(actor, modelMotionsByPath)
    if (!motion) {
      playActorFallbackMotion(actor)
      return
    }
    if (actor.last_motion === motion.key) {
      const randomMotion = pickRandomSupportedMotion(actor, modelMotionsByPath)
      if (randomMotion) motion = randomMotion
    }
    const played = playActorMotionEntry(actor, motion)
    if (!played) playActorFallbackMotion(actor)
  }

  function playActorMotionByCandidates(actor: AvatarActor, candidates: string[]): boolean {
    const modelMotions = modelMotionsByPath.get(actor.model_path) || []
    const motion = findMotionByCandidates(modelMotions, candidates)
    if (!motion) return false
    const played = playActorMotionEntry(actor, motion)
    if (!played) console.error('Failed to play motion by candidates', motion)
    return played
  }

  function triggerActorRandomMotion(sessionId: string): void {
    const actor = actors.get(sessionId)
    if (!actor || actor.phase === 'exiting') return
    const randomMotion = pickRandomSupportedMotion(actor, modelMotionsByPath)
    if (randomMotion) {
      playActorMotionEntry(actor, randomMotion)
      return
    }
    playActorMotion(actor, actor.state)
  }

  function handleActorPrimaryInteraction(sessionId: string): void {
    const now = Date.now()
    const lastClickAt = lastPrimaryClickAtBySession.get(sessionId) || 0
    const isDoubleClick = now - lastClickAt <= doubleClickThresholdMs
    lastPrimaryClickAtBySession.set(sessionId, now)
    openSessionChat(sessionId)
    if (isDoubleClick) triggerActorRandomMotion(sessionId)
  }

  function queueSummonGesture(sessionId: string): void {
    const emitGestureStatus = (status: 'played' | 'missed'): void => {
      window.dispatchEvent(new CustomEvent('session-stage:summon-gesture', {
        detail: { session_id: sessionId, status },
      }))
    }
    let attempts = 0
    const maxAttempts = 5
    const timer = window.setInterval(() => {
      attempts += 1
      const actor = actors.get(sessionId)
      if (actor && actor.phase !== 'exiting') {
        const semanticSummonCandidates = getSemanticMotionCandidates(actor.model_path, 'SUMMON', modelSemanticMotionsByPath)
        playActorMotionByCandidates(actor, [...semanticSummonCandidates, ...SUMMON_MOTION_CANDIDATES])
        emitGestureStatus('played')
        window.clearInterval(timer)
        return
      }
      if (attempts >= maxAttempts) {
        emitGestureStatus('missed')
        window.clearInterval(timer)
      }
    }, 160)
  }

  function bindActorInteractions(model: any, sessionId: string): void {
    model.on?.('click', () => handleActorPrimaryInteraction(sessionId))
    model.on?.('tap', () => handleActorPrimaryInteraction(sessionId))
    model.on?.('rightclick', () => {
      switchActorModel(sessionId).catch((error) => {
        console.error('Failed to switch actor model', error)
      })
    })
    model.on?.('rightdown', () => {
      switchActorModel(sessionId).catch((error) => {
        console.error('Failed to switch actor model', error)
      })
    })
  }

  async function spawnActor(session: SessionSnapshotItem): Promise<void> {
    const app = getApp()
    if (!app || actors.has(session.session_id) || seatReservations.has(session.session_id)) return
    const seat = nextAvailableSeat()
    if (seat === null) return
    seatReservations.set(session.session_id, seat)
    try {
      const assignedModelPath = pickModelPathForSession(session.session_id, {
        modelPaths: getModelPaths(),
        sessionModelAssignments,
        sessionStore,
        fallbackModelPath: 'assets/models/Senko_Normals/senko.model3.json',
      })
      const modelPaths = getModelPaths()
      const loadCandidates = [assignedModelPath]
      if (modelPaths[0] && modelPaths[0] !== assignedModelPath) loadCandidates.push(modelPaths[0])
      let model: any | null = null
      let loadedModelPath = assignedModelPath
      for (const candidatePath of loadCandidates) {
        try {
          model = await loadLive2DModelByPath(candidatePath)
          loadedModelPath = candidatePath
          break
        } catch (error) {
          console.error('Failed to load model candidate', candidatePath, error)
        }
      }
      if (!model) throw new Error(`No available model for session ${session.session_id}`)
      const currentSession = sessionStore[session.session_id]
      const canStayVisible = !!currentSession && getVisibleSessions().some((item) => item.session_id === session.session_id)
      if (!getApp() || isDisposed() || actors.has(session.session_id) || !canStayVisible) {
        model.destroy()
        return
      }
      const liveApp = getApp()
      if (!liveApp) {
        model.destroy()
        return
      }
      const target = getSeatPosition(seat)
      const cell = getSeatCellSize()
      const startX = target.x < liveApp.renderer.width / 2 ? -220 : liveApp.renderer.width + 220
      model.anchor.set(0.5, 1.0)
      model.zIndex = 5
      const targetScale = computeActorTargetScale(model, cell, getVisibleSessions().length || 1)
      model.scale.set(targetScale)
      model.alpha = 0
      model.x = startX
      model.y = target.y
      model.interactive = true
      liveApp.stage.addChild(model)

      const bubble = new PIXI.Container()
      const bubbleBg = new PIXI.Graphics()
      bubbleBg.name = 'bubble-bg'
      const bubbleText = new PIXI.Text('', { fill: 0xf4f8ff, fontSize: 13, fontWeight: '700' })
      bubbleText.anchor.set(0.5, 0.5)
      bubble.addChild(bubbleBg)
      bubble.addChild(bubbleText)
      bubble.alpha = 0
      bubble.zIndex = 15
      liveApp.stage.addChild(bubble)

      const contextText = new PIXI.Text('', {
        fill: 0xfff5d8,
        fontSize: 12,
        fontWeight: '700',
        stroke: 0x0b1a2d,
        strokeThickness: 3,
      })
      contextText.anchor.set(0.5, 1)
      contextText.alpha = 0
      contextText.zIndex = 16
      liveApp.stage.addChild(contextText)

      // Brand badge sprite (SVG pre-rendered to canvas for cross-browser compat)
      const brandName = session.agent_brand || 'codex'
      const badgeTex = getBrandBadgeTexture(brandName)
      const brandBadge = new PIXI.Sprite(badgeTex)
      brandBadge.anchor.set(0.5, 0.5)
      brandBadge.width = 18
      brandBadge.height = 18
      brandBadge.alpha = 0
      brandBadge.zIndex = 16
      liveApp.stage.addChild(brandBadge)
      // If the texture was a placeholder, poll until the real one is cached.
      if (badgeTex === PIXI.Texture.EMPTY) {
        const pollId = setInterval(() => {
          const real = _badgeTextureCache.get(brandName)
          if (real && real !== PIXI.Texture.EMPTY) {
            brandBadge.texture = real
            brandBadge.width = 18
            brandBadge.height = 18
            clearInterval(pollId)
          }
        }, 50)
      }

      const summaryLabel = new PIXI.Container()
      const summaryBg = new PIXI.Graphics()
      summaryBg.name = 'summary-bg'
      const summaryText = new PIXI.Text('', { fill: 0xeff6ff, fontSize: 11, fontWeight: '700' })
      summaryText.anchor.set(0.5, 0.5)
      summaryLabel.addChild(summaryBg)
      summaryLabel.addChild(summaryText)
      summaryLabel.alpha = 0
      summaryLabel.zIndex = 12
      liveApp.stage.addChild(summaryLabel)

      const closeButton = createActorCloseButton(() => dismissSessionImmediately(session.session_id))
      closeButton.alpha = 0
      closeButton.zIndex = 18
      liveApp.stage.addChild(closeButton)

      const actor: AvatarActor = {
        session_id: session.session_id,
        display_name: session.display_name,
        state: session.state,
        seat_index: seat,
        last_seen_at: session.last_seen_at,
        model,
        model_path: loadedModelPath,
        phase: 'entering',
        target_x: target.x,
        target_y: target.y,
        target_scale: targetScale,
        exit_x: startX < target.x ? -280 : liveApp.renderer.width + 280,
        last_motion: '',
        agent_brand: session.agent_brand,
        status_bubble: bubble,
        status_text: bubbleText,
        status_context_text: contextText,
        summary_label: summaryLabel,
        summary_text: summaryText,
        close_button: closeButton,
        brand_badge: brandBadge,
      }
      actors.set(session.session_id, actor)
      sessionModelAssignments.set(session.session_id, loadedModelPath)
      seatAssignments.set(session.session_id, seat)
      await loadModelMotionsForPath(loadedModelPath)
      bindActorInteractions(model, session.session_id)
      playActorMotion(actor, session.state)
      updateActorBubble(actor)
      session.summary = buildSessionSummary(session.display_name)
      updateActorSummary(actor)
    } catch (error) {
      console.error('Failed to spawn actor', error)
    } finally {
      seatReservations.delete(session.session_id)
    }
  }

  async function switchActorModel(sessionId: string): Promise<void> {
    const app = getApp()
    if (!app) return
    if (switchingActors.has(sessionId)) return
    const actor = actors.get(sessionId)
    if (!actor || actor.phase === 'exiting') return
    const nextPath = pickNextModelPath(actor.model_path, getModelPaths())
    if (!nextPath || nextPath === actor.model_path) return
    switchingActors.add(sessionId)
    try {
      await loadModelMotionsForPath(nextPath)
      const nextModel = await loadLive2DModelByPath(nextPath)
      if (!actors.has(sessionId) || isDisposed() || !getApp()) {
        nextModel.destroy()
        return
      }
      const liveApp = getApp()
      if (!liveApp) {
        nextModel.destroy()
        return
      }
      const prevModel = actor.model
      nextModel.anchor.set(0.5, 1.0)
      nextModel.zIndex = 5
      const cell = getSeatCellSize()
      actor.target_scale = computeActorTargetScale(nextModel, cell, getVisibleSessions().length || 1)
      nextModel.scale.set(actor.target_scale)
      nextModel.alpha = prevModel.alpha
      nextModel.x = prevModel.x
      nextModel.y = prevModel.y
      nextModel.interactive = true
      liveApp.stage.addChild(nextModel)
      liveApp.stage.removeChild(prevModel)
      prevModel.destroy()
      actor.model = nextModel
      actor.model_path = nextPath
      actor.last_motion = ''
      sessionModelAssignments.set(sessionId, nextPath)
      bindActorInteractions(nextModel, sessionId)
      playActorMotion(actor, actor.state)
      updateActorSummary(actor)
    } catch (error) {
      console.error('Failed to switch model', sessionId, error)
    } finally {
      switchingActors.delete(sessionId)
    }
  }

  function beginActorExit(sessionId: string, immediate = false): void {
    const actor = actors.get(sessionId)
    if (!actor) {
      seatReservations.delete(sessionId)
      return
    }
    actor.phase = 'exiting'
    if (immediate) {
      actor.model.alpha = Math.min(actor.model.alpha, 0.01)
      if (actor.status_bubble) actor.status_bubble.alpha = actor.model.alpha
      if (actor.status_context_text) actor.status_context_text.alpha = actor.model.alpha
      if (actor.summary_label) actor.summary_label.alpha = actor.model.alpha
      if (actor.close_button) actor.close_button.alpha = actor.model.alpha
    }
  }

  function updateActorLayout(): void {
    const app = getApp()
    if (!app) return
    reflowActiveActorSeats()
    const cell = getSeatCellSize()
    const activeCount = getVisibleSessions().length || 1
    for (const actor of actors.values()) {
      const target = getSeatPosition(actor.seat_index)
      actor.target_x = target.x
      actor.target_y = target.y
      actor.target_scale = computeActorTargetScale(actor.model, cell, activeCount)
      const currentScale = Math.max(0.001, actor.model.scale.y || actor.model.scale.x || 1)
      const naturalHeight = Math.max(1, actor.model.height / currentScale)
      const minHeadTop = stageTopPadding + 10
      const predictedTop = actor.target_y - naturalHeight * actor.target_scale
      if (predictedTop < minHeadTop) actor.target_y += minHeadTop - predictedTop
      const maxFootY = app.renderer.height - stageBottomPadding
      actor.target_y = Math.min(actor.target_y, maxFootY)
      actor.exit_x = target.x < app.renderer.width / 2 ? -280 : app.renderer.width + 280
    }
  }

  function renderTick(delta: number): void {
    const app = getApp()
    if (!app) return
    const now = Date.now()
    if (now - lastVisibilitySyncMs >= 1000) {
      syncActorsWithVisibility()
      lastVisibilitySyncMs = now
    }
    let removedActor = false
    for (const [sessionId, actor] of actors.entries()) {
      if (actor.phase === 'entering') {
        actor.model.x += (actor.target_x - actor.model.x) * 0.14
        actor.model.y += (actor.target_y - actor.model.y) * 0.14
        actor.model.scale.x += (actor.target_scale - actor.model.scale.x) * 0.18
        actor.model.scale.y = actor.model.scale.x
        actor.model.alpha = Math.min(1, actor.model.alpha + 0.1 * delta)
        if (actor.status_bubble) actor.status_bubble.alpha = actor.model.alpha
        if (actor.status_context_text) actor.status_context_text.alpha = actor.model.alpha
        if (actor.brand_badge) actor.brand_badge.alpha = actor.model.alpha
        if (actor.summary_label) actor.summary_label.alpha = actor.model.alpha
        if (actor.close_button) actor.close_button.alpha = actor.model.alpha
        const arrived = Math.abs(actor.target_x - actor.model.x) < 1.5
        if (arrived && actor.model.alpha >= 0.98) actor.phase = 'active'
      } else if (actor.phase === 'active') {
        actor.model.x += (actor.target_x - actor.model.x) * 0.1
        actor.model.y += (actor.target_y - actor.model.y) * 0.1
        actor.model.scale.x += (actor.target_scale - actor.model.scale.x) * 0.14
        actor.model.scale.y = actor.model.scale.x
      } else {
        actor.model.x += (actor.exit_x - actor.model.x) * 0.15
        actor.model.alpha = Math.max(0, actor.model.alpha - 0.12 * delta)
        if (actor.status_bubble) actor.status_bubble.alpha = actor.model.alpha
        if (actor.status_context_text) actor.status_context_text.alpha = actor.model.alpha
        if (actor.brand_badge) actor.brand_badge.alpha = actor.model.alpha
        if (actor.summary_label) actor.summary_label.alpha = actor.model.alpha
        if (actor.close_button) actor.close_button.alpha = actor.model.alpha
        if (actor.model.alpha <= 0.02) {
          if (actor.status_bubble) {
            app.stage.removeChild(actor.status_bubble)
            actor.status_bubble.destroy({ children: true })
          }
          if (actor.status_context_text) {
            app.stage.removeChild(actor.status_context_text)
            actor.status_context_text.destroy()
          }
          if (actor.brand_badge) {
            app.stage.removeChild(actor.brand_badge)
            actor.brand_badge.destroy()
          }
          if (actor.summary_label) {
            app.stage.removeChild(actor.summary_label)
            actor.summary_label.destroy({ children: true })
          }
          if (actor.close_button) {
            app.stage.removeChild(actor.close_button)
            actor.close_button.destroy({ children: true })
          }
          app.stage.removeChild(actor.model)
          actor.model.destroy()
          actors.delete(sessionId)
          seatAssignments.delete(sessionId)
          removedActor = true
          continue
        }
      }
      let bounds: PIXI.Rectangle | null = null
      try {
        if (!actor.model || (actor.model as any).destroyed || !actor.model.parent) {
          throw new Error('actor model is detached')
        }
        bounds = actor.model.getBounds()
      } catch (error) {
        console.error('Failed to update actor layout bounds', sessionId, error)
        if (actor.status_bubble) {
          app.stage.removeChild(actor.status_bubble)
          actor.status_bubble.destroy({ children: true })
        }
        if (actor.status_context_text) {
          app.stage.removeChild(actor.status_context_text)
          actor.status_context_text.destroy()
        }
        if (actor.summary_label) {
          app.stage.removeChild(actor.summary_label)
          actor.summary_label.destroy({ children: true })
        }
        if (actor.close_button) {
          app.stage.removeChild(actor.close_button)
          actor.close_button.destroy({ children: true })
        }
        app.stage.removeChild(actor.model)
        actor.model.destroy()
        actors.delete(sessionId)
        seatAssignments.delete(sessionId)
        removedActor = true
        continue
      }
      if (actor.status_bubble) {
        actor.status_bubble.x = bounds.x + bounds.width / 2
        const requiredLift = Math.max(
          bubbleBoxHeight + bubbleTailHeight + bubbleHeadClearance,
          bounds.height * 0.06,
        )
        const desiredY = bounds.y - requiredLift
        actor.status_bubble.y = Math.max(stageTopPadding + bubbleTopMargin, desiredY)
      }
      if (actor.status_context_text && actor.status_bubble) {
        actor.status_context_text.x = actor.status_bubble.x
        actor.status_context_text.y = actor.status_bubble.y - 6
      }
      if (actor.brand_badge && actor.status_context_text) {
        const ctxText = actor.status_context_text as PIXI.Text
        actor.brand_badge.x = ctxText.x - ctxText.width / 2 - 12
        actor.brand_badge.y = ctxText.y - ctxText.height / 2
        actor.brand_badge.alpha = ctxText.alpha
      }
      if (actor.summary_label) {
        const text = actor.summary_text as PIXI.Text | undefined
        const bg = actor.summary_label.getChildByName('summary-bg') as PIXI.Graphics | null
        if (text && bg) {
          const maxTextWidth = 180
          if (text.width > maxTextWidth) {
            const content = text.text || ''
            let low = 0
            let high = content.length
            let best = '...'
            while (low <= high) {
              const mid = Math.floor((low + high) / 2)
              const candidate = `${content.slice(0, mid)}...`
              text.text = candidate
              if (text.width <= maxTextWidth) {
                best = candidate
                low = mid + 1
              } else {
                high = mid - 1
              }
            }
            text.text = best
          }
          const width = Math.max(84, Math.min(196, text.width + 16))
          const height = 22
          bg.clear()
          bg.lineStyle(1, 0xaac2df, 0.62)
          bg.beginFill(0x0a1a2c, 0.78)
          bg.drawRoundedRect(-width / 2, -height / 2, width, height, 8)
          bg.endFill()
        }
        actor.summary_label.x = bounds.x + bounds.width / 2
        actor.summary_label.y = bounds.y + bounds.height + 16
      }
      if (actor.close_button) {
        actor.close_button.x = bounds.x + bounds.width - 12
        actor.close_button.y = bounds.y + bounds.height - 14
      }
    }
    if (removedActor) syncActorsWithVisibility()
  }

  function syncHistory(items: SessionHistoryItem[]): void {
    upsertHistorySessions(items)
    for (const session of Object.values(sessionStore)) {
      session.summary = buildSessionSummary(session.display_name)
      if (session.active) session.inactive = false
      syncSessionAgentOptionsFromSnapshot(session)
    }
    syncActorsWithVisibility()
  }

  function updateActorState(sessionId: string, state: SessionState, lastSeenAt: string, lastEventType?: string): void {
    const actor = actors.get(sessionId)
    if (!actor) return
    const previousState = actor.state
    actor.display_name = sessionStore[sessionId]?.display_name || actor.display_name
    actor.state = state
    actor.last_seen_at = lastSeenAt
    if (shouldReplayActorMotion(previousState, state)) {
      playActorMotion(actor, state, lastEventType)
    }
    updateActorBubble(actor)
    updateActorSummary(actor)
  }

  return {
    upsertHistorySessions,
    syncActorsWithVisibility,
    ensureSessionVisible,
    summonSession,
    dismissSessionImmediately,
    handleSessionCardClick,
    beginActorExit,
    updateActorLayout,
    renderTick,
    syncHistory,
    updateActorState,
    shouldReplayActorMotion,
  }
}
