<template>
  <div
    v-show="visible"
    ref="containerRef"
    class="web-terminal-container"
    :style="containerStyle"
  >
    <div
      class="web-terminal-header"
      @pointerdown="onDragStart"
    >
      <span class="web-terminal-title">Terminal</span>
      <div class="web-terminal-header-actions">
        <button class="web-terminal-btn" title="重新連線" @click="reconnect">↻</button>
        <button class="web-terminal-btn" title="關閉" @click="close">✕</button>
      </div>
    </div>
    <div ref="terminalRef" class="web-terminal-body"></div>
    <div class="web-terminal-resize-handle" @pointerdown="onResizeStart"></div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import { resolveTerminalWsUrl } from '../../utils/api/webTerminal'

const props = defineProps<{ visible: boolean }>()
const emit = defineEmits<{ (e: 'update:visible', value: boolean): void }>()

const containerRef = ref<HTMLDivElement>()
const terminalRef = ref<HTMLDivElement>()

// Position & size
const posX = ref(80)
const posY = ref(80)
const width = ref(720)
const height = ref(440)

const MIN_WIDTH = 360
const MIN_HEIGHT = 240

const containerStyle = computed(() => ({
  left: `${posX.value}px`,
  top: `${posY.value}px`,
  width: `${width.value}px`,
  height: `${height.value}px`,
}))

let terminal: Terminal | null = null
let fitAddon: FitAddon | null = null
let ws: WebSocket | null = null
let resizeObserver: ResizeObserver | null = null

function createTerminal() {
  if (!terminalRef.value) return

  terminal = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    fontFamily: '"Cascadia Code", Menlo, Monaco, "Courier New", monospace',
    theme: {
      background: '#1e1e2e',
      foreground: '#cdd6f4',
      cursor: '#f5e0dc',
      selectionBackground: '#585b7066',
      black: '#45475a',
      red: '#f38ba8',
      green: '#a6e3a1',
      yellow: '#f9e2af',
      blue: '#89b4fa',
      magenta: '#f5c2e7',
      cyan: '#94e2d5',
      white: '#bac2de',
      brightBlack: '#585b70',
      brightRed: '#f38ba8',
      brightGreen: '#a6e3a1',
      brightYellow: '#f9e2af',
      brightBlue: '#89b4fa',
      brightMagenta: '#f5c2e7',
      brightCyan: '#94e2d5',
      brightWhite: '#a6adc8',
    },
  })

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
  emit('update:visible', false)
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
  posX.value = Math.max(0, e.clientX - dragOffset.x)
  posY.value = Math.max(0, e.clientY - dragOffset.y)
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
  width.value = Math.max(MIN_WIDTH, resizeStart.w + (e.clientX - resizeStart.x))
  height.value = Math.max(MIN_HEIGHT, resizeStart.h + (e.clientY - resizeStart.y))
}

function onResizeEnd() {
  window.removeEventListener('pointermove', onResizeMove)
  window.removeEventListener('pointerup', onResizeEnd)
  fitTerminal()
}

// ------------------------------------------------------------------
// Lifecycle
// ------------------------------------------------------------------
watch(() => props.visible, async (show) => {
  if (show) {
    await nextTick()
    if (!terminal) {
      createTerminal()
      connectWs()
    } else {
      fitTerminal()
    }
    terminal?.focus()
  }
})

onMounted(() => {
  resizeObserver = new ResizeObserver(() => fitTerminal())
  if (terminalRef.value) {
    resizeObserver.observe(terminalRef.value)
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
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
}

.web-terminal-header:active {
  cursor: grabbing;
}

.web-terminal-title {
  font-size: 12px;
  font-weight: 600;
  color: #cdd6f4;
  letter-spacing: 0.5px;
}

.web-terminal-header-actions {
  display: flex;
  gap: 4px;
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
  padding: 4px;
  overflow: hidden;
}

.web-terminal-resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 16px;
  height: 16px;
  cursor: nwse-resize;
}

.web-terminal-resize-handle::after {
  content: '';
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 8px;
  height: 8px;
  border-right: 2px solid #585b70;
  border-bottom: 2px solid #585b70;
}
</style>
