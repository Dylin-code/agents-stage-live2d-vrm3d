<template>
  <div v-if="visible" class="ip-editor-panel">
    <div class="ip-editor-header">
      <span class="ip-editor-title">互動點編輯</span>
      <button type="button" class="ip-btn ip-btn-sm" @click="$emit('close')">關閉</button>
    </div>

    <div class="ip-editor-body">
      <div class="ip-editor-sidebar">
        <div class="ip-editor-sidebar-actions">
          <button type="button" class="ip-btn ip-btn-primary ip-btn-full" @click="$emit('create')">+ 新增互動點</button>
        </div>
        <div class="ip-point-list">
          <button
            v-for="point in pointList"
            :key="point.id"
            type="button"
            class="ip-point-item"
            :class="{ selected: selectedPointId === point.id, draft: point.isDraftOnly }"
            @click="$emit('select', point.id)"
          >
            <span class="ip-point-item-name">{{ point.label }}</span>
            <span class="ip-point-item-type">{{ point.action.type }}</span>
          </button>
        </div>
      </div>

      <div class="ip-editor-main">
        <template v-if="localDraft">
          <div class="ip-form-grid">
            <label class="ip-field">
              <span>名稱</span>
              <input v-model="localDraft.label" type="text" @input="emitDraft" />
            </label>
            <label class="ip-field">
              <span>類型</span>
              <select v-model="localDraft.action.type" @change="handleActionTypeChange">
                <option value="sit">sit</option>
                <option value="work">work</option>
                <option value="stand-idle">stand-idle</option>
              </select>
            </label>
            <label class="ip-field">
              <span>循環動畫</span>
              <input v-model="localDraft.action.loopVrma" type="text" @input="emitDraft" />
            </label>
            <label class="ip-field">
              <span>容量</span>
              <input v-model.number="localDraft.capacity" type="number" min="1" step="1" @input="emitDraft" />
            </label>
          </div>

          <div class="ip-section">
            <div class="ip-section-title">拖拉模式</div>
            <div class="ip-mode-row">
              <button type="button" class="ip-btn" :class="{ active: transformTarget === 'position' }" @click="$emit('set-transform-target', 'position')">拖主點</button>
              <button type="button" class="ip-btn" :class="{ active: transformTarget === 'approach' }" @click="$emit('set-transform-target', 'approach')">拖接近點</button>
              <button type="button" class="ip-btn" :class="{ active: transformTarget === 'rotate' }" @click="$emit('set-transform-target', 'rotate')">轉朝向</button>
            </div>
          </div>

          <div class="ip-section">
            <div class="ip-section-title">主點座標</div>
            <div class="ip-coord-grid">
              <label class="ip-field"><span>X</span><input v-model.number="localDraft.position.x" type="number" step="0.01" @input="emitDraft" /></label>
              <label class="ip-field"><span>Y</span><input v-model.number="localDraft.position.y" type="number" step="0.01" @input="emitDraft" /></label>
              <label class="ip-field"><span>Z</span><input v-model.number="localDraft.position.z" type="number" step="0.01" @input="emitDraft" /></label>
            </div>
          </div>

          <div class="ip-section">
            <div class="ip-section-title">接近點座標</div>
            <div class="ip-coord-grid">
              <label class="ip-field"><span>X</span><input v-model.number="localDraft.approachPosition.x" type="number" step="0.01" @input="emitDraft" /></label>
              <label class="ip-field"><span>Y</span><input v-model.number="localDraft.approachPosition.y" type="number" step="0.01" @input="emitDraft" /></label>
              <label class="ip-field"><span>Z</span><input v-model.number="localDraft.approachPosition.z" type="number" step="0.01" @input="emitDraft" /></label>
            </div>
          </div>

          <div class="ip-section">
            <div class="ip-section-title">朝向</div>
            <label class="ip-field">
              <span>Y 旋轉（弧度）</span>
              <input v-model.number="localDraft.approachRotationY" type="number" step="0.01" @input="emitDraft" />
            </label>
          </div>

          <div v-if="localDraft.action.type === 'sit'" class="ip-section">
            <div class="ip-section-title">座位偏移</div>
            <div class="ip-coord-grid">
              <label class="ip-field"><span>X</span><input v-model.number="localDraft.action.seatOffset!.x" type="number" step="0.01" @input="emitDraft" /></label>
              <label class="ip-field"><span>Y</span><input v-model.number="localDraft.action.seatOffset!.y" type="number" step="0.01" @input="emitDraft" /></label>
              <label class="ip-field"><span>Z</span><input v-model.number="localDraft.action.seatOffset!.z" type="number" step="0.01" @input="emitDraft" /></label>
            </div>
          </div>

          <div class="ip-actions">
            <button type="button" class="ip-btn ip-btn-primary" :disabled="!hasChanges" @click="$emit('save-selected')">儲存此點</button>
            <button type="button" class="ip-btn" :disabled="!hasChanges" @click="$emit('discard-selected')">取消變更</button>
            <button type="button" class="ip-btn ip-btn-danger" @click="$emit('delete-selected')">刪除此點</button>
          </div>
        </template>

        <div v-else class="ip-empty">先從左側選一個互動點，或新增一個。</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { InteractionPoint, InteractionPointData } from './vrmInteractionPointUtils'
import { applyDraftActionType } from './vrmInteractionPointDraftUtils'

type TransformTarget = 'position' | 'approach' | 'rotate'

interface Props {
  visible: boolean
  points: InteractionPoint[]
  selectedPointId: string | null
  draftPoint: InteractionPointData | null
  hasChanges: boolean
  transformTarget: TransformTarget
}

const props = defineProps<Props>()

const emit = defineEmits<{
  close: []
  create: []
  select: [pointId: string]
  updateDraft: [draft: InteractionPointData]
  saveSelected: []
  discardSelected: []
  deleteSelected: []
  setTransformTarget: [target: TransformTarget]
}>()

const localDraft = ref<InteractionPointData | null>(null)

watch(() => props.draftPoint, (value) => {
  if (!value) {
    localDraft.value = null
    return
  }
  const cloned = JSON.parse(JSON.stringify(value)) as InteractionPointData
  localDraft.value = applyDraftActionType(cloned, cloned.action.type)
}, { immediate: true, deep: true })

const pointList = computed(() => {
  const points = props.points.map((point) => ({ ...point, isDraftOnly: false }))
  if (props.draftPoint && !points.some((point) => point.id === props.draftPoint?.id)) {
    points.unshift({ ...props.draftPoint, occupiedBy: [], isDraftOnly: true })
  }
  return points
})

function emitDraft(): void {
  if (!localDraft.value) return
  emit('updateDraft', JSON.parse(JSON.stringify(localDraft.value)))
}

function handleActionTypeChange(): void {
  if (!localDraft.value) return
  localDraft.value = applyDraftActionType(localDraft.value, localDraft.value.action.type)
  emitDraft()
}
</script>

<style scoped>
.ip-editor-panel {
  position: absolute;
  top: 112px;
  right: 14px;
  bottom: 56px;
  width: min(520px, calc(100vw - 28px));
  z-index: 13;
  display: flex;
  flex-direction: column;
  border-radius: 12px;
  border: 1px solid rgba(182, 212, 248, 0.34);
  background: rgba(6, 18, 33, 0.94);
  color: #e9f4ff;
  overflow: hidden;
  backdrop-filter: blur(8px);
}

.ip-editor-header,
.ip-actions,
.ip-editor-sidebar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ip-editor-header {
  justify-content: space-between;
  padding: 10px 12px;
  border-bottom: 1px solid rgba(182, 212, 248, 0.18);
}

.ip-editor-title,
.ip-section-title {
  font-weight: 700;
}

.ip-editor-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.ip-editor-sidebar {
  width: 180px;
  flex-shrink: 0;
  border-right: 1px solid rgba(182, 212, 248, 0.18);
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.ip-editor-sidebar-actions {
  padding: 10px;
}

.ip-point-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0 10px 10px;
  overflow-y: auto;
}

.ip-point-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 8px;
  border: 1px solid rgba(182, 212, 248, 0.16);
  border-radius: 8px;
  background: rgba(8, 26, 45, 0.3);
  color: inherit;
  cursor: pointer;
}

.ip-point-item.selected {
  border-color: rgba(74, 158, 255, 0.65);
  background: rgba(74, 158, 255, 0.18);
}

.ip-point-item.draft {
  border-style: dashed;
}

.ip-point-item-name {
  font-size: 12px;
  font-weight: 700;
}

.ip-point-item-type {
  font-size: 10px;
  opacity: 0.7;
}

.ip-editor-main {
  flex: 1;
  min-width: 0;
  padding: 12px;
  overflow-y: auto;
}

.ip-form-grid,
.ip-coord-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.ip-coord-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.ip-section {
  margin-top: 14px;
}

.ip-mode-row {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.ip-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 11px;
}

.ip-field input,
.ip-field select {
  border: 1px solid rgba(182, 212, 248, 0.2);
  border-radius: 6px;
  background: rgba(8, 26, 45, 0.45);
  color: #e9f4ff;
  font-size: 12px;
  padding: 6px 8px;
}

.ip-actions {
  margin-top: 16px;
  justify-content: flex-end;
}

.ip-btn {
  border: 1px solid rgba(182, 212, 248, 0.35);
  border-radius: 6px;
  background: rgba(8, 26, 45, 0.62);
  color: #e9f4ff;
  font-size: 11px;
  font-weight: 600;
  padding: 6px 10px;
  cursor: pointer;
}

.ip-btn.active,
.ip-btn-primary {
  border-color: rgba(74, 158, 255, 0.6);
  background: rgba(74, 158, 255, 0.24);
}

.ip-btn-danger {
  border-color: rgba(255, 100, 100, 0.4);
  color: #ffb0b0;
}

.ip-btn-full {
  width: 100%;
}

.ip-btn-sm {
  padding: 4px 8px;
}

.ip-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  opacity: 0.6;
}
</style>
