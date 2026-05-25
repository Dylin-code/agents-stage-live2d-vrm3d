/**
 * Composable that owns all client-side state for the TUI bridge feature.
 *
 * Kept separate from the chat-session ``useSessionStage`` runtime so the
 * tmux-backed sessions never leak into the existing history/store logic.
 * ``SessionStageBase`` simply mounts this and binds it to the template.
 */
import { reactive, ref } from 'vue'
import { message } from 'ant-design-vue'

import {
  type TuiSession,
  createTuiSession,
  fetchTuiBridgeConfig,
  killTuiSession,
  listTuiSessions,
} from '../../utils/api/tuiBridge'

export interface TuiBridgeInstance {
  id: number
  index: number
  sessionId: string
  initialLabel: string
}

export interface UseTuiBridgeOptions {
  /** Pre-fill the new-session form's cwd from the chat-session form. */
  defaultCwd?: () => string
}

const REFRESH_DEBOUNCE_MS = 250

export function useTuiBridge(options: UseTuiBridgeOptions = {}) {
  // -- Feature gating --------------------------------------------------
  const enabled = ref(false)
  const hasTmux = ref(false)
  const maxSessions = ref(8)
  const initialized = ref(false)
  const loading = ref(false)

  // -- Sessions list ---------------------------------------------------
  const sessions = ref<TuiSession[]>([])

  // -- Floating windows ------------------------------------------------
  const instances = ref<TuiBridgeInstance[]>([])
  let nextInstanceId = 0
  let nextInstanceIndex = 0

  // -- New-session form ------------------------------------------------
  const newSessionOpen = ref(false)
  const newSessionForm = reactive({
    label: '',
    cwd: '',
    command: '',
  })
  const creating = ref(false)

  // -- cwd picker (dropdown + native directory browser) ----------------
  const cwdSelection = ref('')
  const directoryBrowserVisible = ref(false)

  function syncCwdSelection(recentList: string[]): void {
    const cwd = newSessionForm.cwd.trim()
    cwdSelection.value = cwd && recentList.includes(cwd) ? cwd : ''
  }

  function applyCwdSelection(): void {
    const next = cwdSelection.value
    if (next) newSessionForm.cwd = next
  }

  function applyBrowsedCwd(path: string): void {
    newSessionForm.cwd = String(path || '').trim()
  }

  function openDirectoryBrowser(): void {
    directoryBrowserVisible.value = true
  }

  // -- Initialization --------------------------------------------------
  async function initialize(): Promise<void> {
    try {
      const cfg = await fetchTuiBridgeConfig()
      enabled.value = cfg.enabled
      hasTmux.value = cfg.has_tmux
      maxSessions.value = cfg.max_sessions
      initialized.value = true
      if (enabled.value && hasTmux.value) {
        await refreshSessions()
      }
    } catch (err) {
      enabled.value = false
      hasTmux.value = false
      initialized.value = true
      // Quiet — feature is opt-in; missing endpoint just means it's not deployed.
      console.debug('[tui-bridge] config probe failed', err)
    }
  }

  // -- List refresh with light debouncing ------------------------------
  let refreshPending: number | null = null
  async function refreshSessions(): Promise<void> {
    if (!enabled.value || !hasTmux.value) {
      sessions.value = []
      return
    }
    loading.value = true
    try {
      sessions.value = await listTuiSessions()
    } catch (err) {
      console.warn('[tui-bridge] list failed', err)
    } finally {
      loading.value = false
    }
  }

  function scheduleRefresh(): void {
    if (refreshPending !== null) window.clearTimeout(refreshPending)
    refreshPending = window.setTimeout(() => {
      refreshPending = null
      void refreshSessions()
    }, REFRESH_DEBOUNCE_MS)
  }

  // -- Mutations -------------------------------------------------------
  function resetForm(): void {
    newSessionForm.label = ''
    newSessionForm.cwd = ''
    newSessionForm.command = ''
    cwdSelection.value = ''
  }

  function openNewSessionPanel(): void {
    newSessionOpen.value = !newSessionOpen.value
    if (newSessionOpen.value && !newSessionForm.cwd) {
      const fallback = options.defaultCwd?.() || ''
      if (fallback) newSessionForm.cwd = fallback
    }
  }

  async function createSession(): Promise<void> {
    if (creating.value) return
    if (!enabled.value || !hasTmux.value) {
      message.warning('TUI bridge 尚未啟用或伺服器未安裝 tmux')
      return
    }
    if (!newSessionForm.cwd.trim()) {
      message.warning('請填入工作目錄 (cwd)')
      return
    }
    creating.value = true
    try {
      const created = await createTuiSession({
        label: newSessionForm.label.trim(),
        cwd: newSessionForm.cwd.trim(),
        command: newSessionForm.command.trim(),
      })
      message.success(`已建立 TUI session：${created.session_id}`)
      sessions.value = [created, ...sessions.value.filter((s) => s.session_id !== created.session_id)]
      newSessionOpen.value = false
      resetForm()
      openSession(created.session_id, created.label)
      scheduleRefresh()
    } catch (err) {
      message.error(`建立 TUI session 失敗：${(err as Error).message}`)
    } finally {
      creating.value = false
    }
  }

  function openSession(sessionId: string, initialLabel = ''): void {
    if (!sessionId) return
    if (instances.value.some((inst) => inst.sessionId === sessionId)) {
      // Already attached — bring it to attention. Browser z-index handles overlay,
      // but at minimum we won't spawn a duplicate WS for the same session.
      return
    }
    if (instances.value.length >= maxSessions.value) {
      message.warning(`已開啟 ${instances.value.length} 個 TUI 視窗，超過上限 ${maxSessions.value}`)
      return
    }
    nextInstanceId += 1
    nextInstanceIndex += 1
    instances.value.push({
      id: nextInstanceId,
      index: nextInstanceIndex,
      sessionId,
      initialLabel,
    })
  }

  function closeInstance(instanceId: number): void {
    instances.value = instances.value.filter((inst) => inst.id !== instanceId)
  }

  async function killSession(sessionId: string): Promise<void> {
    try {
      await killTuiSession(sessionId)
      sessions.value = sessions.value.filter((s) => s.session_id !== sessionId)
      instances.value = instances.value.filter((inst) => inst.sessionId !== sessionId)
      message.success(`已終止 TUI session ${sessionId.slice(0, 12)}`)
    } catch (err) {
      message.error(`終止失敗：${(err as Error).message}`)
    }
  }

  function handleInstanceTerminated(sessionId: string): void {
    // The window itself called kill; just drop it from the list.
    sessions.value = sessions.value.filter((s) => s.session_id !== sessionId)
    instances.value = instances.value.filter((inst) => inst.sessionId !== sessionId)
  }

  return {
    // state
    enabled,
    hasTmux,
    maxSessions,
    initialized,
    loading,
    sessions,
    instances,
    newSessionOpen,
    newSessionForm,
    creating,
    cwdSelection,
    directoryBrowserVisible,
    // actions
    initialize,
    refreshSessions,
    scheduleRefresh,
    openNewSessionPanel,
    resetForm,
    createSession,
    openSession,
    closeInstance,
    killSession,
    handleInstanceTerminated,
    applyCwdSelection,
    applyBrowsedCwd,
    openDirectoryBrowser,
    syncCwdSelection,
  }
}

export type UseTuiBridgeReturn = ReturnType<typeof useTuiBridge>
