<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  applyPersonaPreset,
  fetchPersona,
  resetPersona,
  updatePersona,
  type PersonaConfig,
  type PersonaPresetSummary,
} from '../../../utils/api/masterAgent'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'changed', persona: PersonaConfig): void
}>()

const persona = ref<PersonaConfig | null>(null)
const presets = ref<PersonaPresetSummary[]>([])
const selectedPresetId = ref('')
const loading = ref(false)
const saving = ref(false)
const error = ref('')

// Free-text editing buffers for list fields so users type commas as
// separators without us mutating the array on every keystroke.
const personalityDraft = ref('')
const boundariesDraft = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const snapshot = await fetchPersona()
    persona.value = snapshot.persona ?? defaultPersona()
    presets.value = snapshot.presets
    syncDraftsFromPersona()
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    error.value = `載入失敗: ${msg}`
  } finally {
    loading.value = false
  }
}

function defaultPersona(): PersonaConfig {
  return {
    enabled: true,
    display_name: '導演',
    summary: '',
    personality: [],
    speaking_style: '',
    catchphrase: '',
    boundaries: [],
  }
}

function syncDraftsFromPersona() {
  if (!persona.value) return
  personalityDraft.value = persona.value.personality.join('、')
  boundariesDraft.value = persona.value.boundaries.join('\n')
}

function splitPersonality(raw: string): string[] {
  return raw
    .split(/[、,，;；\n]/)
    .map((p) => p.trim())
    .filter(Boolean)
}

function splitBoundaries(raw: string): string[] {
  return raw
    .split(/\n/)
    .map((p) => p.trim())
    .filter(Boolean)
}

async function onSave() {
  if (!persona.value || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const payload: PersonaConfig = {
      ...persona.value,
      personality: splitPersonality(personalityDraft.value),
      boundaries: splitBoundaries(boundariesDraft.value),
    }
    const saved = await updatePersona(payload)
    persona.value = saved
    syncDraftsFromPersona()
    emit('changed', saved)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    error.value = `儲存失敗: ${msg}`
  } finally {
    saving.value = false
  }
}

async function onReset() {
  saving.value = true
  error.value = ''
  try {
    const saved = await resetPersona()
    persona.value = saved
    syncDraftsFromPersona()
    selectedPresetId.value = ''
    emit('changed', saved)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    error.value = `重置失敗: ${msg}`
  } finally {
    saving.value = false
  }
}

async function onApplyPreset() {
  if (!selectedPresetId.value || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const saved = await applyPersonaPreset(selectedPresetId.value)
    persona.value = saved
    syncDraftsFromPersona()
    emit('changed', saved)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    error.value = `套用預設失敗: ${msg}`
  } finally {
    saving.value = false
  }
}

function onClose() {
  emit('close')
}

const isPureToolMode = computed(() => !persona.value?.enabled)

onMounted(() => {
  void load()
})

watch(
  () => props.open,
  (next) => {
    if (next) void load()
  },
)
</script>

<template>
  <div v-if="props.open" class="persona-overlay" role="dialog" aria-label="角色設定">
    <div class="persona-backdrop" @click="onClose" />
    <div class="persona-panel">
      <header>
        <span class="title">🎭 角色設定</span>
        <button type="button" class="close" aria-label="關閉" @click="onClose">✕</button>
      </header>

      <section v-if="loading" class="loading">載入中…</section>
      <section v-else-if="error" class="banner error">{{ error }}</section>

      <section v-if="persona && !loading" class="form">
        <div class="field row toggle-row">
          <label class="toggle">
            <input v-model="persona.enabled" type="checkbox" />
            <span>啟用角色（關閉 = 純工具模式）</span>
          </label>
        </div>

        <div v-if="isPureToolMode" class="notice">
          已關閉角色。系統只會用中性、工具化的口吻回應，所有人設欄位都不會注入 LLM。
        </div>

        <fieldset :disabled="isPureToolMode">
          <div class="field">
            <label>角色名稱 <span class="hint">(LLM 自稱、TG 顯示用)</span></label>
            <input v-model="persona.display_name" type="text" maxlength="80" placeholder="導演" />
          </div>

          <div class="field">
            <label>套用預設</label>
            <div class="preset-row">
              <select v-model="selectedPresetId">
                <option value="">— 選一個預設 —</option>
                <option v-for="p in presets" :key="p.id" :value="p.id">
                  {{ p.display_name }} — {{ p.summary || (p.enabled ? '' : '純工具') }}
                </option>
              </select>
              <button
                type="button"
                class="ghost"
                :disabled="!selectedPresetId || saving"
                @click="onApplyPreset"
              >
                套用
              </button>
            </div>
          </div>

          <div class="field">
            <label>角色簡介 <span class="hint">(一句話描述)</span></label>
            <textarea v-model="persona.summary" rows="2" maxlength="600" placeholder="這座 agent 舞台的導演,把使用者的構想拆成鏡頭..." />
          </div>

          <div class="field">
            <label>性格 <span class="hint">(逗號 / 頓號分隔)</span></label>
            <input v-model="personalityDraft" type="text" placeholder="沉穩、有條理、鏡頭感" />
          </div>

          <div class="field">
            <label>說話風格 <span class="hint">(自由描述，越具體越好)</span></label>
            <textarea
              v-model="persona.speaking_style"
              rows="4"
              maxlength="1200"
              placeholder="以導演視角說話：把任務當成一場戲，用「下一個鏡頭」這類用語…"
            />
          </div>

          <div class="field">
            <label>口頭禪 / 開場語 <span class="hint">(選填，自然帶入即可)</span></label>
            <input v-model="persona.catchphrase" type="text" maxlength="200" placeholder="場記開始──" />
          </div>

          <div class="field">
            <label>界線 <span class="hint">(每行一條，務必遵守)</span></label>
            <textarea
              v-model="boundariesDraft"
              rows="3"
              placeholder="不假裝親自寫程式碼&#10;不打破第四面牆"
            />
          </div>
        </fieldset>

        <div class="actions">
          <button type="button" class="ghost" :disabled="saving" @click="onReset">
            重置為預設（導演）
          </button>
          <button type="button" class="primary" :disabled="saving" @click="onSave">
            {{ saving ? '儲存中…' : '儲存' }}
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.persona-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
}
.persona-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(2px);
}
.persona-panel {
  position: relative;
  width: min(520px, 94vw);
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
.loading {
  padding: 30px 0;
  text-align: center;
  color: #b0bec5;
  font-size: 13px;
}
.banner.error {
  padding: 8px 10px;
  background: rgba(244, 67, 54, 0.18);
  color: #ffcdd2;
  border-radius: 6px;
  font-size: 12px;
}
.notice {
  padding: 8px 10px;
  background: rgba(255, 193, 7, 0.12);
  border: 1px solid rgba(255, 193, 7, 0.3);
  border-radius: 6px;
  font-size: 12px;
  color: #ffe082;
}
.form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}
fieldset {
  display: flex;
  flex-direction: column;
  gap: 12px;
  border: none;
  padding: 0;
  margin: 0;
  min-width: 0;
}
fieldset:disabled {
  opacity: 0.5;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.field.row {
  flex-direction: row;
  align-items: center;
}
.field label {
  font-size: 12px;
  color: #cfd8dc;
}
.field label .hint {
  color: #90a4ae;
  font-weight: normal;
  margin-left: 4px;
}
.field input[type='text'],
.field textarea,
.field select {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  box-sizing: border-box;
  padding: 6px 10px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.05);
  color: #f5f7fa;
  font-size: 13px;
  font-family: inherit;
}
.field textarea {
  resize: vertical;
  word-break: break-word;
}
.field input[type='text']:focus,
.field textarea:focus,
.field select:focus {
  outline: none;
  border-color: rgba(100, 181, 246, 0.6);
}
/* Native <option> dropdown can't be reliably styled, but most engines
   honor option-level background/color — without this the dropdown
   list pops white-on-white because the page sets light text globally. */
.field select option {
  background: #131c2a;
  color: #f5f7fa;
}
.preset-row {
  display: flex;
  gap: 8px;
  min-width: 0;
}
.preset-row select {
  flex: 1 1 auto;
  min-width: 0;
}
.preset-row button {
  flex: 0 0 auto;
}
.toggle-row .toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  cursor: pointer;
}
.actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}
.primary {
  padding: 8px 18px;
  background: #1976d2;
  color: #fff;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
}
.ghost {
  padding: 8px 14px;
  background: rgba(255, 255, 255, 0.08);
  color: #f5f7fa;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
}
.primary:disabled,
.ghost:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
