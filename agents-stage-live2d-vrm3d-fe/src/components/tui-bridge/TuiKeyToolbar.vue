<template>
  <div class="tui-key-toolbar" @pointerdown.stop>
    <button
      v-for="key in visibleKeys"
      :key="key.id"
      type="button"
      tabindex="-1"
      class="tui-key-btn"
      :class="{ pressed: pressedId === key.id }"
      :title="key.title"
      @mousedown.prevent
      @pointerdown.prevent="onPress(key)"
      @pointerup="onRelease"
      @pointerleave="onRelease"
      @pointercancel="onRelease"
    >{{ key.label }}</button>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

/**
 * Software keyboard for sending special keys / ANSI escape sequences that
 * mobile IMEs cannot synthesize through xterm.js's onData (e.g. Shift+Tab,
 * arrow keys, Ctrl+letter). Each key maps to the exact byte sequence the
 * PTY would receive from a hardware key press.
 */

interface KeyDef {
  id: string
  label: string
  title: string
  /** Raw bytes to send to the PTY. */
  bytes: string
  /** Excluded from the compact strip — only shown in the "more" tray. */
  extra?: boolean
}

const KEYS: KeyDef[] = [
  { id: 'esc',      label: 'Esc',  title: 'Escape',                bytes: '\x1b' },
  { id: 'tab',      label: 'Tab',  title: 'Tab',                   bytes: '\t' },
  { id: 'stab',     label: '⇧Tab', title: 'Shift+Tab (cycle mode)', bytes: '\x1b[Z' },
  { id: 'up',       label: '↑',    title: '方向鍵 上',              bytes: '\x1b[A' },
  { id: 'down',     label: '↓',    title: '方向鍵 下',              bytes: '\x1b[B' },
  { id: 'left',     label: '←',    title: '方向鍵 左',              bytes: '\x1b[D' },
  { id: 'right',    label: '→',    title: '方向鍵 右',              bytes: '\x1b[C' },
  { id: 'enter',    label: '⏎',    title: 'Enter',                 bytes: '\r' },
  { id: 'ctrl-c',   label: '⌃C',   title: 'Ctrl+C (中斷)',          bytes: '\x03' },
  { id: 'ctrl-d',   label: '⌃D',   title: 'Ctrl+D (EOF / 離開)',    bytes: '\x04' },
  { id: 'ctrl-l',   label: '⌃L',   title: 'Ctrl+L (清畫面)',        bytes: '\x0c', extra: true },
  { id: 'ctrl-z',   label: '⌃Z',   title: 'Ctrl+Z (suspend)',      bytes: '\x1a', extra: true },
  { id: 'home',     label: 'Home', title: 'Home',                  bytes: '\x1b[H', extra: true },
  { id: 'end',      label: 'End',  title: 'End',                   bytes: '\x1b[F', extra: true },
  { id: 'pgup',     label: 'PgUp', title: 'Page Up',               bytes: '\x1b[5~', extra: true },
  { id: 'pgdn',     label: 'PgDn', title: 'Page Down',             bytes: '\x1b[6~', extra: true },
  { id: 'space',    label: '␣',    title: 'Space',                 bytes: ' ', extra: true },
  { id: 'bksp',     label: '⌫',    title: 'Backspace',             bytes: '\x7f', extra: true },
]

const props = withDefaults(defineProps<{
  expanded?: boolean
}>(), {
  expanded: false,
})

const emit = defineEmits<{
  (e: 'send', bytes: string): void
}>()

const visibleKeys = computed(() => (props.expanded ? KEYS : KEYS.filter((k) => !k.extra)))

const pressedId = ref<string | null>(null)

function onPress(key: KeyDef) {
  pressedId.value = key.id
  emit('send', key.bytes)
}

function onRelease() {
  pressedId.value = null
}
</script>

<style scoped>
.tui-key-toolbar {
  display: flex;
  flex-wrap: nowrap;
  gap: 4px;
  padding: 6px 8px;
  background: #232639;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.18) transparent;
  /* Allow horizontal panning gesture without being eaten by the drag handler. */
  touch-action: pan-x;
  flex-shrink: 0;
}

.tui-key-toolbar::-webkit-scrollbar {
  height: 4px;
}

.tui-key-toolbar::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.16);
  border-radius: 2px;
}

.tui-key-btn {
  flex: 0 0 auto;
  min-width: 44px;
  height: 32px;
  padding: 0 10px;
  font-size: 13px;
  font-weight: 600;
  color: #d6ddf0;
  background: rgba(40, 50, 78, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 6px;
  cursor: pointer;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
  font-family: "Cascadia Code", Menlo, Monaco, "Courier New", monospace;
  transition: background 0.08s ease, transform 0.08s ease;
}

.tui-key-btn:hover {
  background: rgba(60, 75, 115, 0.95);
}

.tui-key-btn.pressed,
.tui-key-btn:active {
  background: rgba(95, 130, 200, 0.95);
  transform: scale(0.96);
}

@media (max-width: 640px) {
  .tui-key-btn {
    min-width: 48px;
    height: 36px;
    font-size: 14px;
  }
}
</style>
