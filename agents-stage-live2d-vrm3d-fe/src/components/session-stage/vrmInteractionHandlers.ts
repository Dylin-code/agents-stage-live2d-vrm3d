import type { Ref } from 'vue'
import type { SessionSnapshotItem } from '../../types/sessionState'

export function createVrmInteractionHandlers(args: {
  interactionEditorEnabled: Ref<boolean>
  toggleInteractionEditor: (next?: boolean) => boolean
  getRenderer: () => { setPixelRatio: (ratio: number) => void; setSize: (w: number, h: number) => void } | null
  getCamera: () => { aspect: number; updateProjectionMatrix: () => void } | null
  getLabelRenderer: () => { setSize: (w: number, h: number) => void } | null
  getContainer: () => HTMLElement | null
  maxDevicePixelRatio: number
  optionsOnCharacterClick: (sessionId: string) => void
  pickActorByRay: (x: number, y: number) => { sessionId: string } | null
  setLoadingText: (text: string) => void
  ensureSessionOnStage: (session: SessionSnapshotItem, triggerJump?: boolean) => Promise<void>
  updateAllHeadLabels: () => void
  getActorsLength: () => number
  pickInteractionPointByRay: (clientX: number, clientY: number) => string | null
  getInteractionPointById: (pointId: string) => { label: string; action?: { type?: string } } | null
  selectInteractionPoint: (pointId: string) => boolean
  refreshInteractionVisuals: () => void
  getInteractionPointsCount: () => number
}) {
  const {
    interactionEditorEnabled,
    toggleInteractionEditor,
    getRenderer,
    getCamera,
    getLabelRenderer,
    getContainer,
    maxDevicePixelRatio,
    optionsOnCharacterClick,
    pickActorByRay,
    setLoadingText,
    ensureSessionOnStage,
    updateAllHeadLabels,
    getActorsLength,
    pickInteractionPointByRay,
    getInteractionPointById,
    selectInteractionPoint,
    refreshInteractionVisuals,
    getInteractionPointsCount,
  } = args

  function handleInteractionEditorToggle(next?: boolean): void {
    const didToggle = toggleInteractionEditor(next)
    if (!didToggle) return
    if (interactionEditorEnabled.value) {
      refreshInteractionVisuals()
      setLoadingText(`互動點編輯模式：可從列表逐點編輯，或點場景標記選取（${getInteractionPointsCount()} 點）`)
    } else {
      refreshInteractionVisuals()
      setLoadingText(getActorsLength() ? '' : '等待 Session 資料...')
    }
  }

  function onPointerDown(event: PointerEvent): void {
    if (interactionEditorEnabled.value) {
      const pointId = pickInteractionPointByRay(event.clientX, event.clientY)
      if (pointId) {
        const didSelect = selectInteractionPoint(pointId)
        if (!didSelect) return
        const point = getInteractionPointById(pointId)
        setLoadingText(`已選取互動點：${point?.label || pointId}（類型：${point?.action?.type || '?'}）`)
      }
      return
    }

    const actor = pickActorByRay(event.clientX, event.clientY)
    if (!actor) return
    optionsOnCharacterClick(actor.sessionId)
  }

  function onKeyDown(event: KeyboardEvent): void {
    const target = event.target as HTMLElement | null
    if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return
    const key = event.key.toLowerCase()

    if (key === 'i') {
      event.preventDefault()
      handleInteractionEditorToggle()
      return
    }

    // 互動點編輯模式
    if (interactionEditorEnabled.value) {
      handleInteractionEditorKey(event, key)
      return
    }
  }

  function handleInteractionEditorKey(event: KeyboardEvent, key: string): void {
    if (key === 'escape') {
      event.preventDefault()
      handleInteractionEditorToggle(false)
    }
  }

  function onResize(): void {
    const renderer = getRenderer()
    const camera = getCamera()
    const labelRenderer = getLabelRenderer()
    const container = getContainer()
    if (!renderer || !camera || !labelRenderer || !container) return
    const { clientWidth, clientHeight } = container
    camera.aspect = clientWidth / clientHeight
    camera.updateProjectionMatrix()
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, maxDevicePixelRatio))
    renderer.setSize(clientWidth, clientHeight)
    labelRenderer.setSize(clientWidth, clientHeight)
  }

  async function handleSidebarSessionClick(event: Event): Promise<void> {
    const customEvent = event as CustomEvent<{ session?: SessionSnapshotItem }>
    const session = customEvent.detail?.session
    if (!session || !session.session_id) return
    try {
      await ensureSessionOnStage(session, true)
      updateAllHeadLabels()
      setLoadingText(getActorsLength() ? '' : '等待 Session 資料...')
    } catch (error) {
      console.warn('處理 sidebar session 點擊失敗:', error)
    }
  }

  return { onPointerDown, onKeyDown, onResize, handleSidebarSessionClick }
}
