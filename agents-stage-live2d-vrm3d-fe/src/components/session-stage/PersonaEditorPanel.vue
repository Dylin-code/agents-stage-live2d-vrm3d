<template>
  <div class="persona-editor">
    <div class="persona-editor-sidebar">
      <button type="button" class="persona-editor-add" @click="handleAddPersona">新增角色個性</button>
      <div v-if="drafts.length === 0" class="persona-editor-empty">尚未建立角色個性</div>
      <button
        v-for="item in drafts"
        :key="item.id"
        type="button"
        class="persona-editor-item"
        :class="{ selected: item.id === selectedPersonaId }"
        @click="selectedPersonaId = item.id"
      >
        {{ item.name || '未命名個性' }}
      </button>
    </div>

    <div class="persona-editor-body">
      <template v-if="selectedPersona">
        <div class="persona-editor-actions">
          <button type="button" class="danger" @click="handleDeletePersona(selectedPersona.id)">刪除</button>
        </div>
        <div class="persona-editor-field">
          <label>名稱</label>
          <input v-model.trim="selectedPersona.name" type="text" placeholder="例如：毒舌女僕 / 冷靜 PM / 熱血教練">
        </div>
        <div class="persona-editor-field">
          <label>內容</label>
          <textarea
            v-model="selectedPersona.content"
            rows="14"
            placeholder="可輸入角色背景、說話風格、回覆準則、禁忌事項等任意 prompt"
          />
        </div>
      </template>
      <div v-else class="persona-editor-empty">請先新增或選擇一個角色個性</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { CharacterPersona } from '../../types/message'
import { createCharacterPersonaId, sanitizeCharacterPersonas } from '../../utils/personas'

const props = defineProps<{
  personas: CharacterPersona[]
}>()

const emit = defineEmits<{
  (event: 'update:personas', value: CharacterPersona[]): void
}>()

const drafts = ref<CharacterPersona[]>([])
const selectedPersonaId = ref('')

const selectedPersona = computed(() => {
  return drafts.value.find((item) => item.id === selectedPersonaId.value)
})

function syncDrafts(personas: CharacterPersona[]): void {
  drafts.value = sanitizeCharacterPersonas(personas).map((item) => ({ ...item }))
  if (!drafts.value.some((item) => item.id === selectedPersonaId.value)) {
    selectedPersonaId.value = drafts.value[0]?.id || ''
  }
}

function emitDrafts(): void {
  emit('update:personas', drafts.value.map((item) => ({
    ...item,
    name: item.name.trim() || '未命名個性',
  })))
}

function handleAddPersona(): void {
  const persona: CharacterPersona = {
    id: createCharacterPersonaId(),
    name: '未命名個性',
    content: '',
  }
  drafts.value = [...drafts.value, persona]
  selectedPersonaId.value = persona.id
}

function handleDeletePersona(personaId: string): void {
  drafts.value = drafts.value.filter((item) => item.id !== personaId)
  if (selectedPersonaId.value === personaId) {
    selectedPersonaId.value = drafts.value[0]?.id || ''
  }
}

watch(
  () => props.personas,
  (personas) => {
    syncDrafts(personas)
  },
  { deep: true, immediate: true },
)

watch(drafts, emitDrafts, { deep: true })
</script>

<style scoped>
.persona-editor {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 16px;
  min-height: 420px;
}

.persona-editor-sidebar,
.persona-editor-body {
  border: 1px solid rgba(148, 171, 204, 0.24);
  border-radius: 14px;
  background: rgba(10, 19, 34, 0.62);
  padding: 14px;
}

.persona-editor-sidebar {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.persona-editor-add,
.persona-editor-item,
.persona-editor-actions button {
  border: 1px solid rgba(170, 201, 240, 0.28);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.08);
  color: #eef5ff;
  padding: 10px 12px;
  text-align: left;
  cursor: pointer;
}

.persona-editor-item.selected {
  background: rgba(82, 143, 255, 0.3);
  border-color: rgba(136, 180, 255, 0.58);
}

.persona-editor-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.persona-editor-actions {
  display: flex;
  justify-content: flex-end;
}

.persona-editor-actions .danger {
  border-color: rgba(255, 155, 155, 0.42);
  background: rgba(176, 56, 56, 0.32);
}

.persona-editor-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.persona-editor-field label,
.persona-editor-empty {
  color: rgba(230, 239, 255, 0.82);
  font-size: 13px;
}

.persona-editor-field input,
.persona-editor-field textarea {
  width: 100%;
  border: 1px solid rgba(170, 201, 240, 0.24);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.08);
  color: #f4f8ff;
  padding: 10px 12px;
}

.persona-editor-field textarea {
  resize: vertical;
  min-height: 240px;
}

@media (max-width: 840px) {
  .persona-editor {
    grid-template-columns: 1fr;
  }
}
</style>
