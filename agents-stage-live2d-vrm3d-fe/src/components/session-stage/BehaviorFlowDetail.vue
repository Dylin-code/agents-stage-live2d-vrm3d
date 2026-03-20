<template>
  <div class="bf-detail">
    <!-- 流程基本設定 -->
    <div class="bf-detail-section">
      <div class="bf-detail-header">
        <input v-model="local.name" class="bf-detail-name-input" @change="emitUpdate" />
        <div class="bf-detail-actions">
          <button type="button" class="bf-btn bf-btn-sm" @click="$emit('duplicate', local.id)">複製</button>
          <button type="button" class="bf-btn bf-btn-sm bf-btn-danger" @click="confirmDelete">刪除</button>
        </div>
      </div>

      <div class="bf-detail-row">
        <label class="bf-label">觸發狀態</label>
        <div class="bf-checkbox-group">
          <label v-for="s in allStates" :key="s" class="bf-label-check">
            <input v-model="local.triggerStates" type="checkbox" :value="s" @change="onTriggerStatesChange" />
            <span>{{ s }}</span>
          </label>
        </div>
      </div>

      <div class="bf-detail-row">
        <label class="bf-label">指定角色</label>
        <select v-model="localActorSlot" class="bf-select" @change="onActorSlotChange">
          <option :value="-1">全部角色</option>
          <option v-for="i in 4" :key="i - 1" :value="i - 1">{{ slotLabels[i - 1] }} (Slot {{ i - 1 }})</option>
        </select>
      </div>

      <div class="bf-detail-row">
        <label class="bf-label">優先順序</label>
        <input v-model.number="local.priority" type="number" min="0" max="100" class="bf-input bf-input-sm" @change="emitUpdate" />
      </div>

      <div class="bf-detail-row">
        <label class="bf-label">執行機率</label>
        <input v-model.number="local.probability" type="number" min="0" max="1" step="0.1" class="bf-input bf-input-sm" @change="emitUpdate" />
      </div>

      <div class="bf-detail-row">
        <label class="bf-label">完成後</label>
        <select v-model="local.onComplete" class="bf-select" @change="emitUpdate">
          <option value="loop">loop（循環）</option>
          <option value="roam">roam（漫步）</option>
          <option value="idle">idle（閒置）</option>
        </select>
      </div>

      <div class="bf-detail-row">
        <label class="bf-label-check">
          <input v-model="local.interruptOnStateChange" type="checkbox" @change="emitUpdate" />
          <span>State 變化時打斷</span>
        </label>
      </div>

      <!-- 條件 -->
      <div class="bf-detail-conditions">
        <div class="bf-section-title">條件</div>
        <label class="bf-label-check">
          <input v-model="condRequireNoRoute" type="checkbox" @change="onConditionChange" />
          <span>需要無路線</span>
        </label>
        <label class="bf-label-check">
          <input v-model="condRequireRoute" type="checkbox" @change="onConditionChange" />
          <span>需要有路線</span>
        </label>
        <div class="bf-detail-row">
          <label class="bf-label-check">
            <input v-model="condRequirePoint" type="checkbox" @change="onConditionChange" />
            <span>需要互動點類型</span>
          </label>
          <select v-if="condRequirePoint" v-model="condPointType" class="bf-select bf-select-sm" @change="onConditionChange">
            <option value="sit">sit</option>
            <option value="work">work</option>
            <option value="stand-idle">stand-idle</option>
          </select>
        </div>
      </div>
    </div>

    <!-- 步驟列表 -->
    <div class="bf-detail-section">
      <div class="bf-section-title">步驟</div>
      <BehaviorFlowStepList
        :steps="local.steps"
        :interaction-points="interactionPoints"
        :start-coordinate-pick="startCoordinatePick"
        @update="onStepsUpdate"
      />
    </div>

    <!-- 測試區 -->
    <div class="bf-detail-section bf-test-section">
      <div class="bf-section-title">測試</div>
      <div class="bf-test-row">
        <label class="bf-label">角色</label>
        <select v-model="testActorId" class="bf-select">
          <option v-for="a in actors" :key="a.sessionId" :value="a.sessionId">
            [S{{ a.slot }}] {{ a.displayName }} ({{ a.state }})
          </option>
        </select>
      </div>
      <div class="bf-test-actions">
        <button
          type="button"
          class="bf-btn bf-btn-primary"
          :disabled="!testActorId || testRunning"
          @click="onTestExecute"
        >
          ▶ 執行此流程
        </button>
        <button
          type="button"
          class="bf-btn"
          :disabled="!testActorId || !testRunning"
          @click="onTestStop"
        >
          ⏹ 停止
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import type { BehaviorFlow, BehaviorFlowStep } from './vrmBehaviorFlowConfigUtils'
import { ALL_SESSION_STATES, SLOT_LABELS } from './vrmBehaviorFlowConfigUtils'
import type { InteractionPoint } from './vrmInteractionPointUtils'
import BehaviorFlowStepList from './BehaviorFlowStepList.vue'

interface ActorInfo {
  sessionId: string
  displayName: string
  state: string
  slot: number
}

interface Props {
  flow: BehaviorFlow
  actors: ActorInfo[]
  interactionPoints: InteractionPoint[]
  isTestRunning: (sessionId: string) => boolean
  startCoordinatePick: () => Promise<{ x: number; z: number } | null>
}

const props = defineProps<Props>()

const emit = defineEmits<{
  update: [flow: BehaviorFlow]
  delete: [flowId: string]
  duplicate: [flowId: string]
  testExecute: [sessionId: string]
  testStop: [sessionId: string]
}>()

const allStates = ALL_SESSION_STATES
const slotLabels = SLOT_LABELS

// 本地編輯副本
const local = ref<BehaviorFlow>(JSON.parse(JSON.stringify(props.flow)))

// actorSlot: -1 表示全部角色，0~3 表示指定
const localActorSlot = ref<number>(local.value.actorSlot ?? -1)

watch(() => props.flow, (f) => {
  local.value = JSON.parse(JSON.stringify(f))
  localActorSlot.value = local.value.actorSlot ?? -1
  syncConditions()
}, { deep: true })

function onActorSlotChange(): void {
  local.value.actorSlot = localActorSlot.value === -1 ? null : localActorSlot.value
  emitUpdate()
}

function onTriggerStatesChange(): void {
  if (local.value.triggerStates.length === 0) {
    local.value.triggerStates = [allStates[0]]
  }
  emitUpdate()
}

// 條件狀態
const condRequireNoRoute = ref(false)
const condRequireRoute = ref(false)
const condRequirePoint = ref(false)
const condPointType = ref('sit')

function syncConditions(): void {
  const c = local.value.condition ?? {}
  condRequireNoRoute.value = !!c.requireNoRoute
  condRequireRoute.value = !!c.requireRoute
  condRequirePoint.value = !!c.requireInteractionPoint
  condPointType.value = c.requireInteractionPoint || 'sit'
}
syncConditions()

function onConditionChange(): void {
  local.value.condition = {
    ...(condRequireNoRoute.value ? { requireNoRoute: true } : {}),
    ...(condRequireRoute.value ? { requireRoute: true } : {}),
    ...(condRequirePoint.value ? { requireInteractionPoint: condPointType.value } : {}),
  }
  emitUpdate()
}

function emitUpdate(): void {
  emit('update', JSON.parse(JSON.stringify(local.value)))
}

function confirmDelete(): void {
  if (confirm(`確定刪除「${local.value.name}」？`)) {
    emit('delete', local.value.id)
  }
}

function onStepsUpdate(steps: BehaviorFlowStep[]): void {
  local.value.steps = steps
  emitUpdate()
}

// 測試
const testActorId = ref<string>('')

watch(() => props.actors, (a) => {
  if (a.length > 0 && !testActorId.value) {
    testActorId.value = a[0].sessionId
  }
}, { immediate: true })

const testRunning = computed(() => {
  if (!testActorId.value) return false
  return props.isTestRunning(testActorId.value)
})

function onTestExecute(): void {
  if (!testActorId.value) return
  emit('testExecute', testActorId.value)
}

function onTestStop(): void {
  if (!testActorId.value) return
  emit('testStop', testActorId.value)
}
</script>

<style scoped>
.bf-detail {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 12px;
  flex: 1;
}

.bf-detail-section {
  padding: 8px 0;
  border-bottom: 1px solid rgba(182, 212, 248, 0.1);
}

.bf-checkbox-group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
}

.bf-detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.bf-detail-name-input {
  flex: 1;
  border: 1px solid rgba(182, 212, 248, 0.25);
  border-radius: 6px;
  background: rgba(8, 26, 45, 0.5);
  color: #e9f4ff;
  font-size: 14px;
  font-weight: 700;
  padding: 4px 8px;
}

.bf-detail-name-input:focus {
  outline: none;
  border-color: rgba(74, 158, 255, 0.5);
}

.bf-detail-actions {
  display: flex;
  gap: 4px;
}

.bf-detail-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.bf-label {
  font-size: 11px;
  opacity: 0.8;
  min-width: 70px;
  flex-shrink: 0;
}

.bf-label-check {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  cursor: pointer;
  margin-bottom: 4px;
}

.bf-label-check input[type="checkbox"] {
  width: 13px;
  height: 13px;
  accent-color: #4a9eff;
}

.bf-select {
  border: 1px solid rgba(182, 212, 248, 0.25);
  border-radius: 5px;
  background: rgba(8, 26, 45, 0.5);
  color: #e9f4ff;
  font-size: 11px;
  padding: 3px 6px;
}

.bf-select-sm {
  margin-left: 4px;
}

.bf-input {
  border: 1px solid rgba(182, 212, 248, 0.25);
  border-radius: 5px;
  background: rgba(8, 26, 45, 0.5);
  color: #e9f4ff;
  font-size: 11px;
  padding: 3px 6px;
}

.bf-input-sm {
  width: 60px;
}

.bf-detail-conditions {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px solid rgba(182, 212, 248, 0.08);
}

.bf-section-title {
  font-size: 10px;
  font-weight: 700;
  opacity: 0.6;
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.bf-test-section {
  border-bottom: none;
}

.bf-test-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.bf-test-actions {
  display: flex;
  gap: 6px;
}

/* 共用按鈕樣式繼承自 panel */
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

.bf-btn:hover:not(:disabled) {
  border-color: rgba(74, 158, 255, 0.6);
  background: rgba(74, 158, 255, 0.15);
}

.bf-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.bf-btn-sm {
  padding: 3px 8px;
  font-size: 10px;
}

.bf-btn-primary {
  background: rgba(74, 158, 255, 0.25);
  border-color: rgba(74, 158, 255, 0.5);
}

.bf-btn-danger {
  border-color: rgba(255, 100, 100, 0.4);
  color: #ff9999;
}

.bf-btn-danger:hover:not(:disabled) {
  background: rgba(255, 100, 100, 0.15);
  border-color: rgba(255, 100, 100, 0.6);
}
</style>
