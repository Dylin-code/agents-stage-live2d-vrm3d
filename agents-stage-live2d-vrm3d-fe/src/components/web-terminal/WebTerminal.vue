<template>
  <div
    ref="containerRef"
    class="web-terminal-container"
    :style="containerStyle"
  >
    <div
      class="web-terminal-header"
      @pointerdown="onDragStart"
    >
      <span class="web-terminal-title">Terminal #{{ instanceIndex }}</span>
      <div class="web-terminal-header-actions">
        <select
          class="web-terminal-theme-select"
          :value="currentThemeName"
          title="切換主題"
          @change="onThemeChange"
          @pointerdown.stop
        >
          <option v-for="name in themeNames" :key="name" :value="name">{{ name }}</option>
        </select>
        <button class="web-terminal-btn" title="重新連線" @click="reconnect">↻</button>
        <button class="web-terminal-btn" title="關閉" @click="close">✕</button>
      </div>
    </div>
    <div ref="terminalRef" class="web-terminal-body"></div>
    <div class="web-terminal-resize-handle" @pointerdown="onResizeStart"></div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import { resolveTerminalWsUrl } from '../../utils/api/webTerminal'
import { themeNames, getTheme, loadThemeName, saveThemeName } from './terminalThemes'

const POSITION_OFFSET = 30
const MOBILE_BREAKPOINT = 640
const DEFAULT_WIDTH = 720
const DEFAULT_HEIGHT = 440
const MIN_WIDTH = 280
const MIN_HEIGHT = 200
const EDGE_PADDING = 8

const props = defineProps<{ instanceIndex: number; isWindows?: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const containerRef = ref<HTMLDivElement>()
const terminalRef = ref<HTMLDivElement>()

function getViewport() {
  return {
    w: window.innerWidth,
    h: window.innerHeight,
  }
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
    x: Math.min(80 + offset, maxX),
    y: Math.min(80 + offset, maxY),
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

let terminal: Terminal | null = null
let fitAddon: FitAddon | null = null
let ws: WebSocket | null = null
let resizeObserver: ResizeObserver | null = null

function applyTheme(name: string) {
  const theme = getTheme(name)
  currentThemeName.value = name
  saveThemeName(name)
  if (terminal) {
    terminal.options.theme = theme
  }
  if (containerRef.value) {
    containerRef.value.style.background = theme.background ?? '#1e1e2e'
  }
}

function onThemeChange(e: Event) {
  const name = (e.target as HTMLSelectElement).value
  applyTheme(name)
}

function createTerminal() {
  if (!terminalRef.value) return

  const theme = getTheme(currentThemeName.value)

  const opts: Record<string, unknown> = {
    cursorBlink: true,
    fontSize: 14,
    fontFamily: '"Cascadia Code", Menlo, Monaco, "Courier New", monospace',
    theme,
  }
  if (props.isWindows) {
    opts.windowsPty = { backend: 'conpty', buildNumber: 21376 }
  }
  terminal = new Terminal(opts)

  fitAddon = new FitAddon()
  terminal.loadAddon(fitAddon)
  terminal.loadAddon(new WebLinksAddon())
  terminal.open(terminalRef.value)
  fitAddon.fit()

  terminal.onData((data) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'input', data }))
    }
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
  const url = resolveTerminalWsUrl(cols, rows)
  ws = new WebSocket(url)

  ws.onopen = () => {
    terminal?.writeln('\x1b[32mTerminal connected.\x1b[0m')
    // Refit after browser completes layout, then force-sync size to PTY
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
      }
    } catch {
      // binary or non-json — write raw
      if (terminal) terminal.write(event.data)
    }
  }

  ws.onclose = () => {
    terminal?.writeln('\r\n\x1b[31mConnection closed.\x1b[0m')
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

function fitTerminal() {
  if (fitAddon && terminal && terminalRef.value) {
    fitAddon.fit()
  }
}

// ------------------------------------------------------------------
// Drag logic
// ------------------------------------------------------------------
let dragOffset = { x: 0, y: 0 }

function onDragStart(e: PointerEvent) {
  if ((e.target as HTMLElement).closest('.web-terminal-btn')) return
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

// ------------------------------------------------------------------
// Resize logic
// ------------------------------------------------------------------
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

// ------------------------------------------------------------------
// Lifecycle
// ------------------------------------------------------------------
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
  if (terminalRef.value) {
    resizeObserver.observe(terminalRef.value)
  }
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
.web-terminal-container {
  position: fixed;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
  border: 1px solid #45475a;
  background: #1e1e2e;
  max-width: 100vw;
  max-height: 100vh;
  box-sizing: border-box;
}

.web-terminal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 32px;
  padding: 0 10px;
  background: #313244;
  cursor: grab;
  user-select: none;
  flex-shrink: 0;
  touch-action: none;
  gap: 6px;
}

.web-terminal-header:active {
  cursor: grabbing;
}

.web-terminal-title {
  font-size: 12px;
  font-weight: 600;
  color: #cdd6f4;
  letter-spacing: 0.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex-shrink: 1;
  min-width: 0;
}

.web-terminal-header-actions {
  display: flex;
  gap: 4px;
  align-items: center;
  flex-shrink: 0;
}

.web-terminal-theme-select {
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

.web-terminal-theme-select:hover {
  border-color: #a6adc8;
}

.web-terminal-btn {
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

.web-terminal-btn:hover {
  background: #45475a;
  color: #cdd6f4;
}

.web-terminal-body {
  flex: 1;
  overflow: hidden;
}

.web-terminal-resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 28px;
  height: 28px;
  cursor: nwse-resize;
  touch-action: none;
  z-index: 2;
}

.web-terminal-resize-handle::after {
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
  .web-terminal-title {
    font-size: 11px;
  }
  .web-terminal-theme-select {
    max-width: 90px;
    font-size: 10px;
  }
  .web-terminal-resize-handle {
    width: 36px;
    height: 36px;
  }
}
</style>
