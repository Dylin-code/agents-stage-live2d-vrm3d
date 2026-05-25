<template>
  <div
    ref="containerRef"
    class="tui-bridge-container"
    :style="containerStyle"
  >
    <div
      class="tui-bridge-header"
      @pointerdown="onDragStart"
    >
      <span class="tui-bridge-title" :title="titleTooltip">
        TUI · {{ label || sessionId.slice(0, 12) }}
      </span>
      <div class="tui-bridge-header-actions">
        <select
          class="tui-bridge-theme-select"
          :value="currentThemeName"
          title="切換主題"
          @change="onThemeChange"
          @pointerdown.stop
        >
          <option v-for="name in themeNames" :key="name" :value="name">{{ name }}</option>
        </select>
        <button
          class="tui-bridge-btn"
          :class="{ active: keyToolbarVisible }"
          title="切換軟鍵盤工具列（Esc / Tab / ↑↓ / Ctrl）"
          @click="toggleKeyToolbar"
          @pointerdown.stop
        >⌨</button>
        <button
          v-if="keyToolbarVisible"
          class="tui-bridge-btn"
          :class="{ active: keyToolbarExpanded }"
          title="展開更多按鍵（Home/End/PgUp/PgDn/Ctrl+L/Ctrl+Z 等）"
          @click="toggleKeyToolbarExpanded"
          @pointerdown.stop
        >⋯</button>
        <button
          class="tui-bridge-btn"
          title="重新 attach（不影響 session 內 TUI）"
          @click="reconnect"
        >↻</button>
        <button
          class="tui-bridge-btn danger"
          title="終止 tmux session（送出 SIGKILL 並關閉內部 TUI）"
          @click="onTerminate"
        >🗑</button>
        <button
          class="tui-bridge-btn"
          title="關閉視窗（保留 session 於後端）"
          @click="close"
        >✕</button>
      </div>
    </div>
    <div ref="terminalRef" class="tui-bridge-body"></div>
    <TuiKeyToolbar
      v-if="keyToolbarVisible"
      :expanded="keyToolbarExpanded"
      @send="onToolbarSend"
    />
    <div class="tui-bridge-resize-handle" @pointerdown="onResizeStart"></div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import { resolveTuiBridgeWsUrl, killTuiSession } from '../../utils/api/tuiBridge'
import { themeNames, getTheme, loadThemeName, saveThemeName } from '../web-terminal/terminalThemes'
import TuiKeyToolbar from './TuiKeyToolbar.vue'

const POSITION_OFFSET = 30
const MOBILE_BREAKPOINT = 640
const DEFAULT_WIDTH = 760
const DEFAULT_HEIGHT = 480
const MIN_WIDTH = 280
const MIN_HEIGHT = 200
const EDGE_PADDING = 8

const props = defineProps<{
  sessionId: string
  instanceIndex: number
  initialLabel?: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'terminated', sessionId: string): void
}>()

const containerRef = ref<HTMLDivElement>()
const terminalRef = ref<HTMLDivElement>()

const label = ref<string>(props.initialLabel || '')
const cwd = ref<string>('')
const command = ref<string>('')

const titleTooltip = computed(() => {
  const parts: string[] = [`session_id: ${props.sessionId}`]
  if (cwd.value) parts.push(`cwd: ${cwd.value}`)
  if (command.value) parts.push(`command: ${command.value}`)
  return parts.join('\n')
})

function getViewport() {
  return { w: window.innerWidth, h: window.innerHeight }
}

function computeInitialLayout(index: number) {
  const { w, h } = getViewport()
  const isMobile = w < MOBILE_BREAKPOINT
  if (isMobile) {
    return {
      width: Math.max(MIN_WIDTH, w - EDGE_PADDING * 2),
      height: Math.max(MIN_HEIGHT, Math.min(h - EDGE_PADDING * 2, Math.round(h * 0.7))),
      x: EDGE_PADDING,
      y: EDGE_PADDING,
    }
  }
  const targetW = Math.min(DEFAULT_WIDTH, w - EDGE_PADDING * 2)
  const targetH = Math.min(DEFAULT_HEIGHT, h - EDGE_PADDING * 2)
  const offset = (index - 1) * POSITION_OFFSET
  const maxX = Math.max(EDGE_PADDING, w - targetW - EDGE_PADDING)
  const maxY = Math.max(EDGE_PADDING, h - targetH - EDGE_PADDING)
  return {
    width: targetW,
    height: targetH,
    x: Math.min(120 + offset, maxX),
    y: Math.min(120 + offset, maxY),
  }
}

const initial = computeInitialLayout(props.instanceIndex)
const posX = ref(initial.x)
const posY = ref(initial.y)
const width = ref(initial.width)
const height = ref(initial.height)

function clampToViewport() {
  const { w, h } = getViewport()
  const maxW = Math.max(MIN_WIDTH, w - EDGE_PADDING * 2)
  const maxH = Math.max(MIN_HEIGHT, h - EDGE_PADDING * 2)
  width.value = Math.min(width.value, maxW)
  height.value = Math.min(height.value, maxH)
  posX.value = Math.max(0, Math.min(posX.value, w - width.value))
  posY.value = Math.max(0, Math.min(posY.value, h - height.value))
}

const containerStyle = computed(() => ({
  left: `${posX.value}px`,
  top: `${posY.value}px`,
  width: `${width.value}px`,
  height: `${height.value}px`,
}))

const currentThemeName = ref(loadThemeName())

function isMobileViewport(): boolean {
  return typeof window !== 'undefined' && window.innerWidth < MOBILE_BREAKPOINT
}

// On mobile the soft-keyboard cannot send Shift+Tab / arrows / Ctrl-letter,
// so the helper toolbar is on by default; desktop users can toggle it via
// the keyboard icon in the header.
const keyToolbarVisible = ref(isMobileViewport())
const keyToolbarExpanded = ref(false)

let terminal: Terminal | null = null
let fitAddon: FitAddon | null = null
let ws: WebSocket | null = null
let resizeObserver: ResizeObserver | null = null

function sendBytes(data: string): void {
  if (!data) return
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'input', data }))
  }
}

function onToolbarSend(bytes: string): void {
  sendBytes(bytes)
  terminal?.focus()
}

function toggleKeyToolbar(): void {
  keyToolbarVisible.value = !keyToolbarVisible.value
  // Allow xterm to recompute size after layout change.
  requestAnimationFrame(() => fitTerminal())
}

function toggleKeyToolbarExpanded(): void {
  keyToolbarExpanded.value = !keyToolbarExpanded.value
}

function applyTheme(name: string) {
  const theme = getTheme(name)
  currentThemeName.value = name
  saveThemeName(name)
  if (terminal) terminal.options.theme = theme
  if (containerRef.value) containerRef.value.style.background = theme.background ?? '#1e1e2e'
}

function onThemeChange(e: Event) {
  const name = (e.target as HTMLSelectElement).value
  applyTheme(name)
}

function createTerminal() {
  if (!terminalRef.value) return

  const theme = getTheme(currentThemeName.value)
  terminal = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    fontFamily: '"Cascadia Code", Menlo, Monaco, "Courier New", monospace',
    theme,
    scrollback: 5000,
  })
  fitAddon = new FitAddon()
  terminal.loadAddon(fitAddon)
  terminal.loadAddon(new WebLinksAddon())
  terminal.open(terminalRef.value)
  fitAddon.fit()

  terminal.onData((data) => {
    sendBytes(data)
  })

  terminal.onResize(({ cols, rows }) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'resize', cols, rows }))
    }
  })
}

function connectWs() {
  if (!terminal) return
  const cols = terminal.cols
  const rows = terminal.rows
  const url = resolveTuiBridgeWsUrl(props.sessionId, cols, rows)
  ws = new WebSocket(url)

  ws.onopen = () => {
    terminal?.writeln('\x1b[36mAttaching to tmux session...\x1b[0m')
    requestAnimationFrame(() => {
      fitTerminal()
      if (terminal && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols: terminal.cols, rows: terminal.rows }))
      }
    })
  }

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data)
      if (msg.type === 'output' && terminal) {
        terminal.write(msg.data)
      } else if (msg.type === 'session') {
        label.value = String(msg.label || label.value)
        cwd.value = String(msg.cwd || '')
        command.value = String(msg.command || '')
      } else if (msg.type === 'detached') {
        terminal?.writeln('\r\n\x1b[33mtmux attach exited — session remains on server.\x1b[0m')
      } else if (msg.type === 'error') {
        terminal?.writeln(`\r\n\x1b[31mServer error: ${msg.message ?? 'unknown'}\x1b[0m`)
      }
    } catch {
      if (terminal) terminal.write(event.data)
    }
  }

  ws.onclose = (event) => {
    if (event.code === 4404) {
      terminal?.writeln(`\r\n\x1b[31mSession not found on server (${props.sessionId}).\x1b[0m`)
    } else if (event.code === 4503) {
      terminal?.writeln('\r\n\x1b[31mServer reports tmux is unavailable.\x1b[0m')
    } else if (event.code === 4403) {
      terminal?.writeln('\r\n\x1b[31mTUI bridge is disabled on the server.\x1b[0m')
    } else {
      terminal?.writeln('\r\n\x1b[33mConnection closed.\x1b[0m')
    }
  }

  ws.onerror = () => {
    terminal?.writeln('\r\n\x1b[31mConnection error.\x1b[0m')
  }
}

function disconnectWs() {
  if (ws) {
    ws.onclose = null
    ws.close()
    ws = null
  }
}

function reconnect() {
  disconnectWs()
  terminal?.clear()
  connectWs()
}

function close() {
  emit('close')
}

async function onTerminate() {
  if (!window.confirm(`真的要終止 tmux session ${props.sessionId}?\n內部所有程序（含 TUI）會被殺掉。`)) return
  try {
    await killTuiSession(props.sessionId)
    emit('terminated', props.sessionId)
    emit('close')
  } catch (err) {
    terminal?.writeln(`\r\n\x1b[31mKill failed: ${(err as Error).message}\x1b[0m`)
  }
}

function fitTerminal() {
  if (fitAddon && terminal && terminalRef.value) fitAddon.fit()
}

// -- Drag ----------------------------------------------------------------
let dragOffset = { x: 0, y: 0 }

function onDragStart(e: PointerEvent) {
  if ((e.target as HTMLElement).closest('.tui-bridge-btn, .tui-bridge-theme-select')) return
  e.preventDefault()
  dragOffset = { x: e.clientX - posX.value, y: e.clientY - posY.value }
  window.addEventListener('pointermove', onDragMove)
  window.addEventListener('pointerup', onDragEnd)
}

function onDragMove(e: PointerEvent) {
  const { w, h } = getViewport()
  const maxX = Math.max(0, w - width.value)
  const maxY = Math.max(0, h - height.value)
  posX.value = Math.max(0, Math.min(e.clientX - dragOffset.x, maxX))
  posY.value = Math.max(0, Math.min(e.clientY - dragOffset.y, maxY))
}

function onDragEnd() {
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointerup', onDragEnd)
}

// -- Resize --------------------------------------------------------------
let resizeStart = { x: 0, y: 0, w: 0, h: 0 }

function onResizeStart(e: PointerEvent) {
  e.preventDefault()
  e.stopPropagation()
  resizeStart = { x: e.clientX, y: e.clientY, w: width.value, h: height.value }
  window.addEventListener('pointermove', onResizeMove)
  window.addEventListener('pointerup', onResizeEnd)
}

function onResizeMove(e: PointerEvent) {
  const { w, h } = getViewport()
  const maxW = Math.max(MIN_WIDTH, w - posX.value - EDGE_PADDING)
  const maxH = Math.max(MIN_HEIGHT, h - posY.value - EDGE_PADDING)
  width.value = Math.min(maxW, Math.max(MIN_WIDTH, resizeStart.w + (e.clientX - resizeStart.x)))
  height.value = Math.min(maxH, Math.max(MIN_HEIGHT, resizeStart.h + (e.clientY - resizeStart.y)))
}

function onResizeEnd() {
  window.removeEventListener('pointermove', onResizeMove)
  window.removeEventListener('pointerup', onResizeEnd)
  fitTerminal()
}

function onWindowResize() {
  clampToViewport()
  fitTerminal()
}

onMounted(async () => {
  await nextTick()
  createTerminal()
  connectWs()
  terminal?.focus()

  resizeObserver = new ResizeObserver(() => fitTerminal())
  if (terminalRef.value) resizeObserver.observe(terminalRef.value)
  window.addEventListener('resize', onWindowResize)
  window.addEventListener('orientationchange', onWindowResize)
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  window.removeEventListener('resize', onWindowResize)
  window.removeEventListener('orientationchange', onWindowResize)
  disconnectWs()
  terminal?.dispose()
  terminal = null
})
</script>

<style scoped>
.tui-bridge-container {
  position: fixed;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.55);
  border: 1px solid #4a5568;
  background: #1e1e2e;
  max-width: 100vw;
  max-height: 100vh;
  box-sizing: border-box;
}

.tui-bridge-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 32px;
  padding: 0 10px;
  background: #2a324a;
  cursor: grab;
  user-select: none;
  flex-shrink: 0;
  touch-action: none;
  gap: 6px;
}

.tui-bridge-header:active { cursor: grabbing; }

.tui-bridge-title {
  font-size: 12px;
  font-weight: 600;
  color: #d6ddf0;
  letter-spacing: 0.4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex-shrink: 1;
  min-width: 0;
}

.tui-bridge-header-actions {
  display: flex;
  gap: 4px;
  align-items: center;
  flex-shrink: 0;
}

.tui-bridge-theme-select {
  height: 22px;
  max-width: 150px;
  border: 1px solid #585b70;
  border-radius: 4px;
  background: #1e1e2e;
  color: #cdd6f4;
  font-size: 11px;
  padding: 0 4px;
  cursor: pointer;
  outline: none;
}

.tui-bridge-btn {
  background: none;
  border: none;
  color: #a6adc8;
  cursor: pointer;
  font-size: 14px;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  transition: background-color 0.15s, color 0.15s;
}

.tui-bridge-btn:hover { background: #45475a; color: #cdd6f4; }
.tui-bridge-btn.danger:hover { background: #5a2a2a; color: #fab387; }
.tui-bridge-btn.active { background: #4a6592; color: #d6ddf0; }

.tui-bridge-body {
  flex: 1;
  overflow: hidden;
}

.tui-bridge-resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 28px;
  height: 28px;
  cursor: nwse-resize;
  touch-action: none;
  z-index: 2;
}

.tui-bridge-resize-handle::after {
  content: '';
  position: absolute;
  right: 5px;
  bottom: 5px;
  width: 12px;
  height: 12px;
  border-right: 2px solid #585b70;
  border-bottom: 2px solid #585b70;
}

@media (max-width: 640px) {
  .tui-bridge-title { font-size: 11px; }
  .tui-bridge-theme-select { max-width: 90px; font-size: 10px; }
  .tui-bridge-resize-handle { width: 36px; height: 36px; }
}
</style>
