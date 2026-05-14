<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  fetchTelegramStatus,
  issueTelegramBindingCode,
  type TelegramBindingCode,
  type TelegramStatus,
} from '../../../utils/api/masterAgent'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const status = ref<TelegramStatus | null>(null)
const statusError = ref('')
const issuedCode = ref<TelegramBindingCode | null>(null)
const issuing = ref(false)
const issueError = ref('')
const now = ref(Date.now() / 1000)

let pollTimer: ReturnType<typeof setInterval> | null = null
let tickTimer: ReturnType<typeof setInterval> | null = null

async function refreshStatus() {
  statusError.value = ''
  try {
    status.value = await fetchTelegramStatus()
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    statusError.value = `無法取得狀態: ${msg}`
  }
}

async function onIssue() {
  if (issuing.value) return
  issuing.value = true
  issueError.value = ''
  try {
    issuedCode.value = await issueTelegramBindingCode()
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    issueError.value = msg
  } finally {
    issuing.value = false
  }
}

function onClose() {
  emit('close')
}

async function copyCode() {
  if (!issuedCode.value) return
  try {
    await navigator.clipboard.writeText(issuedCode.value.code)
  } catch {
    // Clipboard may be blocked (file://, insecure context). Silent fail
    // — the user can read the code on screen.
  }
}

const remainingSeconds = computed(() => {
  if (!issuedCode.value) return 0
  return Math.max(0, Math.floor(issuedCode.value.expires_at - now.value))
})

const remainingDisplay = computed(() => {
  const s = remainingSeconds.value
  if (s <= 0) return '已過期'
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${r.toString().padStart(2, '0')}`
})

const botLink = computed(() => {
  const name = status.value?.bot_username || issuedCode.value?.bot_username || ''
  return name ? `https://t.me/${name}` : ''
})

const bindCommand = computed(() => (issuedCode.value ? `/bind ${issuedCode.value.code}` : ''))

onMounted(() => {
  void refreshStatus()
  // Refresh status every 5s while the panel is mounted so the binding
  // count reflects fresh /bind activity from TG.
  pollTimer = setInterval(refreshStatus, 5000)
  tickTimer = setInterval(() => {
    now.value = Date.now() / 1000
  }, 1000)
})

onBeforeUnmount(() => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  if (tickTimer) {
    clearInterval(tickTimer)
    tickTimer = null
  }
})
</script>

<template>
  <div v-if="props.open" class="tg-overlay" role="dialog" aria-label="綁定 Telegram">
    <div class="tg-backdrop" @click="onClose" />
    <div class="tg-panel">
      <header>
        <span class="title">綁定 Telegram</span>
        <button type="button" class="close" aria-label="關閉" @click="onClose">✕</button>
      </header>

      <section v-if="statusError" class="banner error">{{ statusError }}</section>

      <section v-if="status" class="status">
        <div class="row">
          <span class="label">後端 Bot</span>
          <span class="value">
            <span :class="['dot', status.enabled ? 'on' : 'off']" />
            {{ status.enabled ? (status.running ? '已啟用' : '已設定 (未執行)') : '未設定' }}
          </span>
        </div>
        <div class="row">
          <span class="label">已綁定 chat 數</span>
          <span class="value">{{ status.binding_count }}</span>
        </div>
        <div v-if="status.bot_username" class="row">
          <span class="label">Bot 帳號</span>
          <span class="value">
            <a :href="botLink" target="_blank" rel="noreferrer">@{{ status.bot_username }}</a>
          </span>
        </div>
      </section>

      <section v-if="status && !status.enabled" class="notice">
        後端尚未設定 <code>TELEGRAM_BOT_TOKEN</code>。<br />
        在
        <code>agents-stage-live2d-vrm3d-server/.env</code>
        加上 token，重啟伺服器後即可使用。
      </section>

      <section v-if="status && status.enabled" class="issue">
        <button
          v-if="!issuedCode || remainingSeconds <= 0"
          type="button"
          class="primary"
          :disabled="issuing"
          @click="onIssue"
        >
          {{ issuing ? '產生中…' : (issuedCode ? '重新產生綁定碼' : '產生綁定碼') }}
        </button>
        <div v-if="issueError" class="banner error">{{ issueError }}</div>
        <div v-if="issuedCode && remainingSeconds > 0" class="code-box">
          <div class="code">{{ issuedCode.code }}</div>
          <div class="meta">
            <span>剩餘 {{ remainingDisplay }}</span>
            <button type="button" class="ghost" @click="copyCode">複製</button>
          </div>
          <ol class="steps">
            <li>
              開啟
              <a v-if="botLink" :href="botLink" target="_blank" rel="noreferrer">
                @{{ status.bot_username || issuedCode.bot_username }}
              </a>
              <span v-else>你的 Telegram Bot 私訊</span>
            </li>
            <li>傳送 <code>{{ bindCommand }}</code></li>
            <li>看到「✅ 已綁定！」就可以直接傳訊息派工</li>
          </ol>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.tg-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
}
.tg-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(2px);
}
.tg-panel {
  position: relative;
  width: min(420px, 92vw);
  max-height: 90vh;
  overflow-y: auto;
  background: #131c2a;
  color: #f5f7fa;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.5);
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
header .title {
  font-size: 15px;
  font-weight: 600;
}
header .close {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.08);
  color: #f5f7fa;
  cursor: pointer;
}
.status {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}
.status .row {
  display: flex;
  justify-content: space-between;
}
.status .label {
  color: #b0bec5;
}
.status a {
  color: #64b5f6;
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
}
.dot.on {
  background: #4caf50;
}
.dot.off {
  background: #9e9e9e;
}
.notice {
  background: rgba(255, 193, 7, 0.12);
  border: 1px solid rgba(255, 193, 7, 0.35);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.5;
  color: #ffe082;
}
.notice code {
  background: rgba(0, 0, 0, 0.3);
  padding: 1px 5px;
  border-radius: 3px;
}
.issue {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.primary {
  align-self: flex-start;
  padding: 8px 16px;
  background: #1976d2;
  color: #fff;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
}
.primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.ghost {
  padding: 4px 10px;
  background: rgba(255, 255, 255, 0.08);
  color: #f5f7fa;
  border: none;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
}
.banner.error {
  padding: 8px 10px;
  background: rgba(244, 67, 54, 0.18);
  color: #ffcdd2;
  border-radius: 6px;
  font-size: 12px;
}
.code-box {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.code-box .code {
  font-family: 'Menlo', 'Consolas', monospace;
  font-size: 28px;
  letter-spacing: 6px;
  text-align: center;
  color: #fff;
}
.code-box .meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: #b0bec5;
}
.steps {
  margin: 0;
  padding-left: 20px;
  font-size: 12px;
  line-height: 1.6;
  color: #cfd8dc;
}
.steps code {
  background: rgba(0, 0, 0, 0.3);
  padding: 1px 6px;
  border-radius: 3px;
}
.steps a {
  color: #64b5f6;
}
</style>
