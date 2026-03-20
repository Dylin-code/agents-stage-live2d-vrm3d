<template>
  <div v-if="visible" class="bf-editor-panel">
    <div class="bf-editor-header">
      <span class="bf-editor-title">行為流設定</span>
      <div class="bf-editor-header-actions">
        <button type="button" class="bf-btn bf-btn-sm bf-btn-close" @click="handleClose">✕</button>
      </div>
    </div>

    <div class="bf-editor-body">
      <div class="bf-editor-sidebar">
        <!-- 狀態篩選 -->
        <div class="bf-filter-section">
          <div class="bf-section-title">狀態篩選</div>
          <label class="bf-filter-item">
            <input v-model="filterAll" type="checkbox" @change="onFilterAllChange" />
            <span>全部</span>
          </label>
          <label v-for="s in allStates" :key="s" class="bf-filter-item">
            <input v-model="filterStates" type="checkbox" :value="s" />
            <span>{{ s }}</span>
          </label>
        </div>

        <!-- Slot 篩選 -->
        <div class="bf-filter-section">
          <div class="bf-section-title">角色篩選</div>
          <label class="bf-filter-item">
            <input v-model="filterSlotAll" type="checkbox" @change="onFilterSlotAllChange" />
            <span>全部</span>
          </label>
          <label v-for="i in 4" :key="i - 1" class="bf-filter-item">
            <input v-model="filterSlots" type="checkbox" :value="i - 1" />
            <span>Slot {{ i - 1 }}</span>
          </label>
        </div>

        <!-- 行為流列表 -->
        <div class="bf-flow-list-section">
          <div class="bf-section-title">行為流列表</div>
          <div class="bf-flow-list">
            <div
              v-for="flow in filteredFlows"
              :key="flow.id"
              class="bf-flow-item"
              :class="{ selected: selectedFlowId === flow.id }"
              @click="selectedFlowId = flow.id"
            >
              <div class="bf-flow-item-top">
                <span class="bf-flow-item-state">{{ formatTriggerStates(flow) }}</span>
                <span v-if="flow.actorSlot != null" class="bf-flow-item-slot">S{{ flow.actorSlot }}</span>
                <span v-else class="bf-flow-item-slot bf-flow-item-slot-all">ALL</span>
              </div>
              <span class="bf-flow-item-name">{{ flow.name }}</span>
            </div>
          </div>
          <button type="button" class="bf-btn bf-btn-full" @click="addNewFlow">+ 新增流程</button>
        </div>
      </div>

      <div class="bf-editor-main">
        <BehaviorFlowDetail
          v-if="selectedFlow"
          :flow="selectedFlow"
          :actors="actors"
          :interaction-points="interactionPoints"
          :is-test-running="isTestRunning"
          :start-coordinate-pick="startCoordinatePick"
          @update="onFlowUpdate"
          @delete="onFlowDelete"
          @duplicate="onFlowDuplicate"
          @test-execute="onTestExecute"
          @test-stop="onTestStop"
        />
        <div v-else class="bf-empty-hint">請從左側選擇一個行為流</div>

        <!-- 底部操作列 -->
        <div class="bf-editor-footer">
          <button type="button" class="bf-btn bf-btn-primary" @click="saveAll">儲存全部</button>
          <button type="button" class="bf-btn" @click="onImport">匯入</button>
          <button type="button" class="bf-btn" @click="onExport">匯出</button>
          <button type="button" class="bf-btn bf-btn-danger" @click="onReset">還原預設</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import type { BehaviorFlow, BehaviorFlowConfig } from './vrmBehaviorFlowConfigUtils'
import { ALL_SESSION_STATES, generateFlowId, generateStepId, getFlowTriggerStates } from './vrmBehaviorFlowConfigUtils'
import type { InteractionPoint } from './vrmInteractionPointUtils'
import BehaviorFlowDetail from './BehaviorFlowDetail.vue'

interface ActorInfo {
  sessionId: string
  displayName: string
  state: string
  slot: number
}

interface Props {
  visible: boolean
  configUtils: ReturnType<typeof import('./vrmBehaviorFlowConfigUtils').createVrmBehaviorFlowConfigUtils>
  actors: ActorInfo[]
  interactionPoints: InteractionPoint[]
  isTestRunning: (sessionId: string) => boolean
  startCoordinatePick: () => Promise<{ x: number; z: number } | null>
}

const props = defineProps<Props>()

const emit = defineEmits<{
  close: []
  previewUpdate: []
  testExecute: [sessionId: string, flow: BehaviorFlow]
  testStop: [sessionId: string]
}>()

const filterStates = ref<string[]>([...ALL_SESSION_STATES])
const filterAll = ref(true)
const filterSlots = ref<number[]>([0, 1, 2, 3])
const filterSlotAll = ref(true)
const selectedFlowId = ref<string | null>(null)

// 配置狀態
const flows = ref<BehaviorFlow[]>([])
let previewTimer: number | null = null

const allStates = ALL_SESSION_STATES

onMounted(() => {
  loadFlows()
})

function loadFlows(): void {
  const config = props.configUtils.getPersistedConfig()
  flows.value = JSON.parse(JSON.stringify(config.flows))
  if (flows.value.length > 0 && !selectedFlowId.value) {
    selectedFlowId.value = flows.value[0].id
  }
}

watch(() => props.visible, (v, prev) => {
  if (v) {
    loadFlows()
    return
  }
  if (prev) {
    props.configUtils.discardPreviewConfig()
    emit('previewUpdate')
  }
})

const filteredFlows = computed(() => {
  return flows.value
    .filter((f) => getFlowTriggerStates(f).some((state) => filterStates.value.includes(state)))
    .filter((f) => {
      if (f.actorSlot == null) return true  // 通用流程始終顯示
      return filterSlots.value.includes(f.actorSlot)
    })
})

const selectedFlow = computed(() => {
  return flows.value.find((f) => f.id === selectedFlowId.value) ?? null
})

function onFilterAllChange(): void {
  if (filterAll.value) {
    filterStates.value = [...ALL_SESSION_STATES]
  } else {
    filterStates.value = []
  }
}

watch(filterStates, (val) => {
  filterAll.value = val.length === ALL_SESSION_STATES.length
}, { deep: true })

function onFilterSlotAllChange(): void {
  filterSlots.value = filterSlotAll.value ? [0, 1, 2, 3] : []
}

watch(filterSlots, (val) => {
  filterSlotAll.value = val.length === 4
}, { deep: true })

function addNewFlow(): void {
  const flow: BehaviorFlow = {
    id: generateFlowId(),
    name: '新行為流',
    triggerStates: ['IDLE'],
    priority: 10,
    probability: 1.0,
    condition: {},
    steps: [{ id: generateStepId(), type: 'roam' }],
    onComplete: 'roam',
    interruptOnStateChange: true,
  }
  flows.value.push(flow)
  selectedFlowId.value = flow.id
  schedulePreviewUpdate()
}

function onFlowUpdate(updated: BehaviorFlow): void {
  const idx = flows.value.findIndex((f) => f.id === updated.id)
  if (idx >= 0) {
    flows.value[idx] = updated
    schedulePreviewUpdate()
  }
}

function onFlowDelete(flowId: string): void {
  const idx = flows.value.findIndex((f) => f.id === flowId)
  if (idx >= 0) {
    flows.value.splice(idx, 1)
    if (selectedFlowId.value === flowId) {
      selectedFlowId.value = flows.value[0]?.id ?? null
    }
    schedulePreviewUpdate()
  }
}

function onFlowDuplicate(flowId: string): void {
  const source = flows.value.find((f) => f.id === flowId)
  if (!source) return
  const copy: BehaviorFlow = JSON.parse(JSON.stringify(source))
  copy.id = generateFlowId()
  copy.name = `${source.name} (副本)`
  copy.steps.forEach((s) => { s.id = generateStepId() })
  flows.value.push(copy)
  selectedFlowId.value = copy.id
  schedulePreviewUpdate()
}

function formatTriggerStates(flow: BehaviorFlow): string {
  return getFlowTriggerStates(flow).join(', ')
}

function onTestExecute(sessionId: string): void {
  if (!selectedFlow.value) return
  emit('testExecute', sessionId, selectedFlow.value)
}

function onTestStop(sessionId: string): void {
  emit('testStop', sessionId)
}

function saveAll(): void {
  const config = buildCurrentConfig()
  props.configUtils.saveConfig(config)
  emit('previewUpdate')
}

function buildCurrentConfig(): BehaviorFlowConfig {
  const persistedConfig = props.configUtils.getPersistedConfig()
  return {
    version: persistedConfig.version,
    flows: JSON.parse(JSON.stringify(flows.value)),
    slotAssignments: persistedConfig.slotAssignments ?? {},
  }
}

function schedulePreviewUpdate(): void {
  if (!props.visible) return
  if (previewTimer !== null) {
    window.clearTimeout(previewTimer)
  }
  previewTimer = window.setTimeout(() => {
    previewTimer = null
    props.configUtils.applyPreviewConfig(buildCurrentConfig())
    emit('previewUpdate')
  }, 120)
}

function onExport(): void {
  // 先儲存再匯出
  saveAll()
  const json = props.configUtils.exportConfig()
  const blob = new Blob([json], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `behavior-flows-${new Date().toISOString().slice(0, 10)}.json`
  a.click()
  URL.revokeObjectURL(url)
}

function onImport(): void {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.json'
  input.onchange = () => {
    const file = input.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      const result = props.configUtils.importConfig(reader.result as string)
      if (result.success) {
        loadFlows()
        emit('previewUpdate')
      } else {
        alert(`匯入失敗: ${result.error}`)
      }
    }
    reader.readAsText(file)
  }
  input.click()
}

function onReset(): void {
  if (!confirm('確定要還原為預設配置？所有自訂設定將被清除。')) return
  props.configUtils.resetToDefault()
  loadFlows()
  emit('previewUpdate')
}

function handleClose(): void {
  if (previewTimer !== null) {
    window.clearTimeout(previewTimer)
    previewTimer = null
  }
  props.configUtils.discardPreviewConfig()
  emit('previewUpdate')
  emit('close')
}
</script>

<style scoped>
.bf-editor-panel {
  position: absolute;
  left: 14px;
  top: 112px;
  bottom: 56px;
  width: min(680px, calc(100vw - 28px));
  z-index: 10;
  display: flex;
  flex-direction: column;
  border-radius: 12px;
  border: 1px solid rgba(182, 212, 248, 0.34);
  background: rgba(6, 18, 33, 0.92);
  color: #e9f4ff;
  font-size: 12px;
  overflow: hidden;
  backdrop-filter: blur(8px);
}

.bf-editor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid rgba(182, 212, 248, 0.18);
  flex-shrink: 0;
}

.bf-editor-title {
  font-weight: 700;
  font-size: 13px;
}

.bf-editor-header-actions {
  display: flex;
  gap: 6px;
}

.bf-editor-body {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.bf-editor-sidebar {
  width: 180px;
  flex-shrink: 0;
  border-right: 1px solid rgba(182, 212, 248, 0.18);
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.bf-editor-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.bf-filter-section,
.bf-flow-list-section {
  padding: 8px 10px;
}

.bf-section-title {
  font-size: 11px;
  font-weight: 700;
  opacity: 0.7;
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.bf-filter-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 0;
  cursor: pointer;
  font-size: 11px;
}

.bf-filter-item input[type="checkbox"] {
  width: 13px;
  height: 13px;
  accent-color: #4a9eff;
}

.bf-flow-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 8px;
}

.bf-flow-item {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 5px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}

.bf-flow-item:hover {
  background: rgba(74, 158, 255, 0.12);
}

.bf-flow-item.selected {
  background: rgba(74, 158, 255, 0.22);
  border: 1px solid rgba(74, 158, 255, 0.4);
}

.bf-flow-item-top {
  display: flex;
  align-items: center;
  gap: 4px;
}

.bf-flow-item-state {
  font-size: 9px;
  font-weight: 700;
  opacity: 0.6;
  text-transform: uppercase;
}

.bf-flow-item-slot {
  font-size: 8px;
  font-weight: 700;
  padding: 1px 4px;
  border-radius: 3px;
  background: rgba(74, 158, 255, 0.25);
  color: #8ac4ff;
}

.bf-flow-item-slot-all {
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.5);
}

.bf-flow-item-name {
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bf-empty-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  opacity: 0.5;
}

.bf-editor-footer {
  display: flex;
  gap: 6px;
  padding: 8px 12px;
  border-top: 1px solid rgba(182, 212, 248, 0.18);
  flex-shrink: 0;
}

/* ─── Shared button styles ─── */
.bf-btn {
  border: 1px solid rgba(182, 212, 248, 0.35);
  border-radius: 6px;
  background: rgba(8, 26, 45, 0.62);
  color: #e9f4ff;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  padding: 5px 10px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.bf-btn:hover {
  border-color: rgba(74, 158, 255, 0.6);
  background: rgba(74, 158, 255, 0.15);
}

.bf-btn-sm {
  padding: 3px 8px;
  font-size: 10px;
}

.bf-btn-full {
  width: 100%;
}

.bf-btn-primary {
  background: rgba(74, 158, 255, 0.25);
  border-color: rgba(74, 158, 255, 0.5);
}

.bf-btn-primary:hover {
  background: rgba(74, 158, 255, 0.35);
}

.bf-btn-danger {
  border-color: rgba(255, 100, 100, 0.4);
  color: #ff9999;
}

.bf-btn-danger:hover {
  background: rgba(255, 100, 100, 0.15);
  border-color: rgba(255, 100, 100, 0.6);
}

.bf-btn-close {
  border: none;
  background: none;
  font-size: 14px;
  opacity: 0.7;
  padding: 2px 6px;
}

.bf-btn-close:hover {
  opacity: 1;
}
</style>
