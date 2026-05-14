<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  fetchMasterAgentLlmInfo,
  fetchPersona,
  type MasterLlmInfo,
  type PersonaConfig,
} from '../../utils/api/masterAgent'
import MasterChatPanel from './components/MasterChatPanel.vue'
import PersonaPanel from './components/PersonaPanel.vue'
import SubTaskList from './components/SubTaskList.vue'
import TelegramBindingPanel from './components/TelegramBindingPanel.vue'
import { emptyRuntimeState, buildMasterAgentRuntime } from './useMasterAgent.runtime'

const router = useRouter()

const state = reactive(emptyRuntimeState())
const llmInfo = ref<MasterLlmInfo | null>(null)
const cwdHint = ref('')
const errorBanner = ref('')

function backToStage(): void {
  void router.push('/')
}

// Mobile-only drawer toggle: on a wide screen the subtask list is
// always visible alongside the chat; on phones we collapse it into
// a slide-up drawer toggled from the topbar.
const showMobileTasks = ref(false)
const showTelegramPanel = ref(false)
const showPersonaPanel = ref(false)
const persona = ref<PersonaConfig | null>(null)

const displayName = computed(() => persona.value?.display_name?.trim() || '導演')
const personaActive = computed(() => persona.value?.enabled !== false)

function onPersonaChanged(next: PersonaConfig) {
  persona.value = next
}

const subtaskCount = computed(() => Object.keys(state.subtasks).length)

const runtime = buildMasterAgentRuntime(
  () => state,
  (next) => Object.assign(state, next),
)

let disconnectWs: (() => void) | null = null

onMounted(async () => {
  try {
    llmInfo.value = await fetchMasterAgentLlmInfo()
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    errorBanner.value = `無法載入 LLM 資訊: ${msg}`
  }
  try {
    const snapshot = await fetchPersona()
    persona.value = snapshot.persona
  } catch {
    // Persona is non-critical for chat flow; leave it null and surface
    // a fallback display name. The page still works without it.
  }
  try {
    await runtime.ensureConversation(undefined, cwdHint.value || undefined)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    errorBanner.value = `建立對話失敗: ${msg}`
  }
  // Subscribe to the broadcast channel so subtask state changes from
  // other sources (or other tabs) show up immediately.
  disconnectWs = runtime.connectWs()
})

onBeforeUnmount(() => {
  disconnectWs?.()
  disconnectWs = null
})

async function onSend(message: string, permitFullAccess = false) {
  errorBanner.value = ''
  try {
    await runtime.sendMessage(message, {
      defaultCwd: cwdHint.value || undefined,
      permitFullAccess,
    })
    await runtime.refreshSubtasks()
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    errorBanner.value = msg
  }
}

async function onAbort() {
  await runtime.abort()
}

async function refreshSubtasks() {
  await runtime.refreshSubtasks()
}

async function onNewConversation(firstMessage = '', permitFullAccess = false) {
  errorBanner.value = ''
  try {
    await runtime.startNewConversation(undefined, cwdHint.value || undefined)
    showMobileTasks.value = false
    if (firstMessage) {
      // ``#new <text>`` form — once the fresh conversation is ready,
      // immediately send the remaining text as the first user prompt.
      await onSend(firstMessage, permitFullAccess)
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    errorBanner.value = `無法開新對話: ${msg}`
  }
}

function toggleMobileTasks() {
  showMobileTasks.value = !showMobileTasks.value
}
</script>

<template>
  <div class="master-agent-page">
    <div class="bg" aria-hidden="true" />
    <header class="topbar">
      <div class="topbar-row primary">
        <button
          class="back-button"
          type="button"
          aria-label="返回舞台"
          @click="backToStage"
        >
          ←
        </button>
        <button
          type="button"
          class="title-chip"
          :class="{ inactive: !personaActive }"
          :title="personaActive ? '點擊修改角色設定' : '純工具模式 — 點擊啟用角色'"
          @click="showPersonaPanel = true"
        >
          🎭 {{ displayName }}<span v-if="!personaActive" class="title-chip-tag">純工具</span>
        </button>
        <div class="meta">
          <span v-if="llmInfo" class="meta-item">{{ llmInfo.provider }} / {{ llmInfo.model }}</span>
          <span v-if="state.conversationId" class="meta-item conv">{{ state.conversationId.slice(0, 8) }}</span>
        </div>
        <button
          class="mobile-tasks-toggle"
          type="button"
          :aria-pressed="showMobileTasks"
          @click="toggleMobileTasks"
        >
          任務 ({{ subtaskCount }})
        </button>
      </div>
      <div class="topbar-row controls-row">
        <input
          v-model="cwdHint"
          class="cwd-input"
          type="text"
          placeholder="預設 cwd (可選)"
        />
        <div class="controls">
          <button type="button" class="ghost" @click="refreshSubtasks">重整任務</button>
          <button
            type="button"
            class="ghost"
            aria-label="綁定 Telegram"
            @click="showTelegramPanel = true"
          >
            綁定 Telegram
          </button>
          <button
            type="button"
            class="primary"
            :disabled="state.isStreaming"
            @click="onNewConversation"
          >
            開新對話
          </button>
        </div>
      </div>
    </header>
    <div v-if="errorBanner" class="banner">{{ errorBanner }}</div>
    <div class="layout" :class="{ 'mobile-show-tasks': showMobileTasks }">
      <MasterChatPanel
        class="chat-pane"
        :turns="state.turns"
        :thinking-draft="state.thinkingDraft"
        :is-streaming="state.isStreaming"
        :display-name="displayName"
        @send="onSend"
        @abort="onAbort"
        @new-conversation="onNewConversation"
      />
      <aside class="tasks-pane">
        <button
          class="mobile-tasks-close"
          type="button"
          aria-label="關閉任務列表"
          @click="showMobileTasks = false"
        >
          ✕
        </button>
        <SubTaskList :subtasks="state.subtasks" />
      </aside>
    </div>
    <TelegramBindingPanel :open="showTelegramPanel" @close="showTelegramPanel = false" />
    <PersonaPanel
      :open="showPersonaPanel"
      @close="showPersonaPanel = false"
      @changed="onPersonaChanged"
    />
  </div>
</template>

<style scoped>
/* Layout uses 100dvh + env(safe-area-inset-*) so the page sits inside
   the iOS notch / Android gesture bar without content being clipped. */
.master-agent-page {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: 100dvh;
  padding-top: env(safe-area-inset-top, 0);
  padding-bottom: env(safe-area-inset-bottom, 0);
  padding-left: env(safe-area-inset-left, 0);
  padding-right: env(safe-area-inset-right, 0);
  background: #0f1620;
  color: #f5f7fa;
  box-sizing: border-box;
  overflow: hidden;
}
.bg {
  position: absolute;
  inset: 0;
  z-index: 0;
  background:
    radial-gradient(60% 80% at 20% 10%, rgba(94, 53, 177, 0.35), transparent 60%),
    radial-gradient(50% 60% at 80% 0%, rgba(25, 118, 210, 0.32), transparent 60%),
    radial-gradient(70% 90% at 100% 100%, rgba(0, 137, 123, 0.25), transparent 65%),
    linear-gradient(160deg, #0a0f17 0%, #131c2a 50%, #0c121b 100%);
  pointer-events: none;
}
.topbar,
.banner,
.layout {
  position: relative;
  z-index: 1;
}
.topbar {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 16px;
  background: rgba(15, 22, 32, 0.85);
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(6px);
}
.topbar-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.title-chip {
  padding: 6px 14px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  background: rgba(100, 181, 246, 0.2);
  color: #e3f2fd;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  line-height: 1.2;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.title-chip.inactive {
  background: rgba(255, 255, 255, 0.06);
  color: #cfd8dc;
}
.title-chip:hover {
  background: rgba(100, 181, 246, 0.32);
}
.title-chip-tag {
  font-size: 11px;
  font-weight: 500;
  color: #b0bec5;
  padding: 1px 6px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 999px;
}
.back-button {
  width: 32px;
  height: 32px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.06);
  color: #f5f7fa;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.back-button:hover {
  background: rgba(255, 255, 255, 0.12);
}
.meta {
  display: flex;
  gap: 8px;
  font-size: 12px;
  color: #b0bec5;
  flex-wrap: wrap;
}
.meta-item {
  padding: 2px 8px;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 999px;
}
.meta .conv {
  font-family: monospace;
}
.mobile-tasks-toggle {
  display: none;
  margin-left: auto;
  padding: 6px 12px;
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.05);
  color: #f5f7fa;
  font-size: 12px;
  cursor: pointer;
}
.topbar-row.controls-row {
  gap: 8px;
}
.cwd-input {
  flex: 1 1 200px;
  min-width: 0;
  padding: 6px 10px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  color: #f5f7fa;
  font-size: 12px;
}
.cwd-input::placeholder {
  color: rgba(255, 255, 255, 0.45);
}
.controls {
  display: flex;
  gap: 6px;
}
.controls button {
  padding: 6px 14px;
  border: none;
  border-radius: 6px;
  color: #fff;
  cursor: pointer;
  font-size: 12px;
}
.controls button.ghost {
  background: rgba(255, 255, 255, 0.08);
}
.controls button.primary {
  background: #1976d2;
}
.controls button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.banner {
  padding: 8px 16px;
  background: rgba(244, 67, 54, 0.18);
  color: #ffcdd2;
  font-size: 13px;
  border-bottom: 1px solid rgba(244, 67, 54, 0.3);
}
.layout {
  flex: 1;
  display: flex;
  min-height: 0;
}
.chat-pane {
  flex: 1;
  min-width: 0;
}
.tasks-pane {
  position: relative;
  flex: 0 0 320px;
  min-width: 0;
  background: rgba(15, 22, 32, 0.65);
  border-left: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  flex-direction: column;
}
.tasks-pane :deep(.subtask-list) {
  background: transparent;
  border-left: none;
  color: #e1e4ed;
}
.tasks-pane :deep(.subtask-list h3) {
  color: #cfd8dc;
}
.tasks-pane :deep(.subtask-card) {
  background: rgba(255, 255, 255, 0.04);
  border-color: rgba(255, 255, 255, 0.08);
  color: #e1e4ed;
}
.tasks-pane :deep(.subtask-card .session),
.tasks-pane :deep(.subtask-card .cwd),
.tasks-pane :deep(.subtask-card .last-event) {
  color: rgba(255, 255, 255, 0.55);
}
.tasks-pane :deep(.subtask-card .final-text) {
  background: rgba(76, 175, 80, 0.12);
  color: #d7f0d8;
}
.tasks-pane :deep(.subtask-card .error) {
  background: rgba(244, 67, 54, 0.18);
  color: #ffd7d4;
}
.mobile-tasks-close {
  display: none;
}

/* ----- Mobile breakpoint -----
   Switch from side-by-side to a slide-in drawer for the subtask list. */
@media (max-width: 768px) {
  .topbar-row.controls-row {
    flex-wrap: wrap;
  }
  .cwd-input {
    flex-basis: 100%;
  }
  .mobile-tasks-toggle {
    display: inline-flex;
  }
  .tasks-pane {
    position: absolute;
    top: 0;
    bottom: 0;
    right: 0;
    width: min(86vw, 360px);
    flex: 0 0 auto;
    z-index: 2;
    transform: translateX(100%);
    transition: transform 0.18s ease-out;
    box-shadow: -8px 0 24px rgba(0, 0, 0, 0.4);
    padding-top: env(safe-area-inset-top, 0);
    padding-right: env(safe-area-inset-right, 0);
  }
  .layout.mobile-show-tasks .tasks-pane {
    transform: translateX(0);
  }
  .mobile-tasks-close {
    display: block;
    position: absolute;
    top: 8px;
    right: 8px;
    z-index: 1;
    width: 28px;
    height: 28px;
    border: none;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.1);
    color: #f5f7fa;
    font-size: 14px;
    cursor: pointer;
  }
}
</style>
