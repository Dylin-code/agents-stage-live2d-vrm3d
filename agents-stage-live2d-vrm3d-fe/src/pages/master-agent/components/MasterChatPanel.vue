<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { MasterChatTurn } from '../masterAgentTypes'
import { parseChatCommand } from '../chatCommands'
import { renderMarkdown } from '../markdown'

const props = withDefaults(
  defineProps<{
    turns: MasterChatTurn[]
    thinkingDraft: string
    isStreaming: boolean
    displayName?: string
  }>(),
  { displayName: '導演' },
)

const placeholder = computed(
  () => `跟 ${props.displayName || '導演'} 對話… (Enter 送出, Shift+Enter 換行;  #new 開新對話;  #full 允許全權限)`,
)
const emit = defineEmits<{
  (e: 'send', message: string, permitFullAccess: boolean): void
  (e: 'abort'): void
  (e: 'new-conversation', firstMessage: string, permitFullAccess: boolean): void
}>()

const message = ref('')
const scroller = ref<HTMLDivElement | null>(null)

function submit() {
  const text = message.value.trim()
  if (!text) return
  // Slash-style command shortcuts.
  //   #new      → start a fresh conversation; remainder becomes first msg
  //   #full     → unlock permission_mode=full for this turn
  // Both can compose.
  const parsed = parseChatCommand(text)
  if (parsed.command === 'new') {
    emit('new-conversation', parsed.remainder, parsed.permitFullAccess)
    message.value = ''
    return
  }
  emit('send', parsed.remainder, parsed.permitFullAccess)
  message.value = ''
}

function onKeyDown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    submit()
  }
}

const visibleTurns = computed(() => props.turns)

// system turns are tool-call traces — keep them monospace + plain text
// so the JSON args stay readable. user / assistant get full markdown.
function turnHtml(turn: MasterChatTurn): string {
  return renderMarkdown(turn.text)
}

watch(
  () => [props.turns.length, props.thinkingDraft],
  async () => {
    await nextTick()
    if (scroller.value) {
      scroller.value.scrollTop = scroller.value.scrollHeight
    }
  },
)
</script>

<template>
  <section class="chat-panel">
    <div ref="scroller" class="scroll">
      <div
        v-for="turn in visibleTurns"
        :key="turn.id"
        class="turn"
        :class="`turn-${turn.role}`"
      >
        <div class="role">{{ turn.role }}</div>
        <pre v-if="turn.role === 'system'" class="text">{{ turn.text }}</pre>
        <div v-else class="text markdown" v-html="turnHtml(turn)" />
      </div>
      <div v-if="thinkingDraft" class="turn turn-assistant draft">
        <div class="role">assistant (thinking…)</div>
        <pre class="text">{{ thinkingDraft }}</pre>
      </div>
    </div>
    <form class="composer" @submit.prevent="submit">
      <textarea
        v-model="message"
        class="input"
        :placeholder="placeholder"
        rows="3"
        @keydown="onKeyDown"
      />
      <div class="actions">
        <button type="submit" :disabled="isStreaming || !message.trim()">送出</button>
        <button v-if="isStreaming" type="button" class="abort" @click="emit('abort')">中止</button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.chat-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  color: inherit;
}
.scroll {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: transparent;
}
.turn {
  margin-bottom: 12px;
  padding: 10px 14px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.05);
  color: #f5f7fa;
  max-width: min(78ch, 100%);
  word-break: break-word;
}
.turn-user {
  background: rgba(25, 118, 210, 0.22);
  border-color: rgba(25, 118, 210, 0.35);
  margin-left: auto;
}
.turn-assistant {
  background: rgba(76, 175, 80, 0.18);
  border-color: rgba(76, 175, 80, 0.3);
}
.turn-system {
  background: rgba(255, 255, 255, 0.04);
  color: rgba(245, 247, 250, 0.65);
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, monospace;
}
.turn.draft {
  opacity: 0.85;
  border-left: 3px solid #ffb74d;
}
.role {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: rgba(245, 247, 250, 0.55);
  margin-bottom: 4px;
}
.text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  line-height: 1.55;
}
.text.markdown {
  white-space: normal;
}
.text.markdown :deep(p) {
  margin: 0 0 0.6em;
}
.text.markdown :deep(p:last-child) {
  margin-bottom: 0;
}
.text.markdown :deep(ul),
.text.markdown :deep(ol) {
  margin: 0.3em 0 0.6em;
  padding-left: 1.4em;
}
.text.markdown :deep(li) {
  margin: 0.15em 0;
}
.text.markdown :deep(a) {
  color: #82b1ff;
  text-decoration: underline;
}
.text.markdown :deep(code) {
  font-family: ui-monospace, SFMono-Regular, "JetBrains Mono", monospace;
  font-size: 0.9em;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.35);
  color: #ffd180;
}
.text.markdown :deep(pre) {
  margin: 0.4em 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.45);
  border: 1px solid rgba(255, 255, 255, 0.06);
  overflow-x: auto;
  font-size: 12.5px;
  line-height: 1.4;
}
.text.markdown :deep(pre code) {
  background: transparent;
  padding: 0;
  color: #d7e3f4;
}
.text.markdown :deep(blockquote) {
  margin: 0.4em 0;
  padding: 4px 12px;
  border-left: 3px solid rgba(255, 255, 255, 0.25);
  color: rgba(245, 247, 250, 0.78);
  background: rgba(255, 255, 255, 0.03);
}
.text.markdown :deep(table) {
  border-collapse: collapse;
  margin: 0.5em 0;
  font-size: 12.5px;
  width: auto;
  max-width: 100%;
  display: block;
  overflow-x: auto;
}
.text.markdown :deep(th),
.text.markdown :deep(td) {
  border: 1px solid rgba(255, 255, 255, 0.12);
  padding: 4px 10px;
  text-align: left;
  vertical-align: top;
}
.text.markdown :deep(th) {
  background: rgba(255, 255, 255, 0.06);
  font-weight: 600;
}
.text.markdown :deep(tr:nth-child(even) td) {
  background: rgba(255, 255, 255, 0.025);
}
.text.markdown :deep(h1),
.text.markdown :deep(h2),
.text.markdown :deep(h3),
.text.markdown :deep(h4) {
  margin: 0.5em 0 0.3em;
  font-weight: 700;
  line-height: 1.25;
}
.text.markdown :deep(h1) { font-size: 1.25em; }
.text.markdown :deep(h2) { font-size: 1.15em; }
.text.markdown :deep(h3) { font-size: 1.05em; }
.text.markdown :deep(h4) { font-size: 1em; }
.text.markdown :deep(hr) {
  border: none;
  border-top: 1px solid rgba(255, 255, 255, 0.12);
  margin: 0.6em 0;
}
.composer {
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  padding: 10px 12px;
  padding-bottom: max(10px, env(safe-area-inset-bottom, 0));
  background: rgba(15, 22, 32, 0.7);
  backdrop-filter: blur(6px);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.input {
  width: 100%;
  resize: vertical;
  font-family: inherit;
  padding: 10px 12px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.05);
  color: #f5f7fa;
  font-size: 14px;
  line-height: 1.5;
  min-height: 60px;
  box-sizing: border-box;
}
.input::placeholder {
  color: rgba(255, 255, 255, 0.45);
}
.input:focus {
  outline: none;
  border-color: rgba(25, 118, 210, 0.6);
  box-shadow: 0 0 0 2px rgba(25, 118, 210, 0.2);
}
.actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
button {
  padding: 8px 18px;
  border: none;
  border-radius: 8px;
  background: #1976d2;
  color: #fff;
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
}
button:disabled {
  background: rgba(255, 255, 255, 0.15);
  color: rgba(255, 255, 255, 0.4);
  cursor: not-allowed;
}
button.abort {
  background: #e53935;
}

@media (max-width: 768px) {
  .scroll {
    padding: 12px;
  }
  .turn {
    max-width: 100%;
    padding: 8px 12px;
  }
  .composer {
    padding: 8px 10px;
    padding-bottom: max(8px, env(safe-area-inset-bottom, 0));
  }
  .input {
    font-size: 16px; /* prevent iOS zoom on focus */
  }
  button {
    padding: 10px 16px;
    font-size: 15px;
  }
}
</style>
