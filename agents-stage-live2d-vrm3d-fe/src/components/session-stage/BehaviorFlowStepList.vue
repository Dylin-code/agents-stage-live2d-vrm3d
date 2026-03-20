<template>
  <div class="bf-steps">
    <div
      v-for="(step, idx) in localSteps"
      :key="step.id"
      class="bf-step-card"
    >
      <div class="bf-step-header">
        <span class="bf-step-index">{{ idx + 1 }}.</span>
        <span class="bf-step-icon">{{ stepIcon(step.type) }}</span>
        <select v-model="step.type" class="bf-select bf-step-type-select" @change="onStepTypeChange(idx)">
          <option value="moveTo">moveTo</option>
          <option value="interact">interact</option>
          <option value="wait">wait</option>
          <option value="playMotion">playMotion</option>
          <option value="roam">roam</option>
        </select>
        <span v-if="step.label" class="bf-step-label">{{ step.label }}</span>
        <div class="bf-step-header-actions">
          <button v-if="idx > 0" type="button" class="bf-btn-icon" title="上移" @click="moveStep(idx, -1)">↑</button>
          <button v-if="idx < localSteps.length - 1" type="button" class="bf-btn-icon" title="下移" @click="moveStep(idx, 1)">↓</button>
          <button type="button" class="bf-btn-icon bf-btn-icon-danger" title="刪除" @click="removeStep(idx)">✕</button>
        </div>
      </div>

      <!-- moveTo 參數 -->
      <div v-if="step.type === 'moveTo'" class="bf-step-params">
        <div class="bf-step-param-row">
          <label class="bf-label">目標模式</label>
          <select v-model="getMoveTarget(step).mode" class="bf-select" @change="emitUpdate">
            <option value="interactionPoint">互動點</option>
            <option value="coordinate">座標</option>
            <option value="route">路線（多點）</option>
            <option value="random">隨機</option>
          </select>
        </div>
        <div v-if="getMoveTarget(step).mode === 'interactionPoint'" class="bf-step-param-row">
          <label class="bf-label">互動點</label>
          <select v-model="getMoveTarget(step).interactionPointId" class="bf-select" @change="emitUpdate">
            <option v-for="p in interactionPoints" :key="p.id" :value="p.id">{{ p.label }} ({{ p.action.type }})</option>
          </select>
        </div>
        <div v-if="getMoveTarget(step).mode === 'coordinate'" class="bf-step-param-row">
          <label class="bf-label">X</label>
          <input v-model.number="getCoordinate(step).x" type="number" step="0.1" class="bf-input bf-input-sm" @change="emitUpdate" />
          <label class="bf-label">Z</label>
          <input v-model.number="getCoordinate(step).z" type="number" step="0.1" class="bf-input bf-input-sm" @change="emitUpdate" />
          <button type="button" class="bf-btn-pick" @click="pickCoordinate(step)">🎯 選取</button>
        </div>
        <div v-if="getMoveTarget(step).mode === 'route'" class="bf-step-params-route">
          <div class="bf-route-waypoints">
            <div v-for="(wp, wi) in getWaypoints(step)" :key="wi" class="bf-route-wp">
              <span class="bf-route-wp-idx">{{ wi + 1 }}.</span>
              <span class="bf-route-wp-coord">({{ wp.x.toFixed(2) }}, {{ wp.z.toFixed(2) }})</span>
              <button type="button" class="bf-btn-icon bf-btn-icon-danger" @click="removeWaypoint(step, wi)">✕</button>
            </div>
            <div v-if="!getWaypoints(step).length" class="bf-route-empty">尚無路線點</div>
          </div>
          <div class="bf-route-actions">
            <button type="button" class="bf-btn-pick" @click="pickRoutePoints(step)">🎯 連續選取路線點</button>
            <button v-if="getWaypoints(step).length" type="button" class="bf-btn-icon bf-btn-icon-danger" @click="clearWaypoints(step)">清空</button>
          </div>
          <div class="bf-step-param-row">
            <label class="bf-label-check">
              <input type="checkbox" :checked="step.skipObstacles !== false" disabled />
              <span>忽略障礙物（路線模式預設開啟）</span>
            </label>
          </div>
        </div>
        <div v-if="getMoveTarget(step).mode === 'random'" class="bf-step-param-row">
          <label class="bf-label">範圍</label>
          <input v-model.number="getMoveTarget(step).randomRange" type="number" step="0.5" min="0.5" class="bf-input bf-input-sm" @change="emitUpdate" />
        </div>
      </div>

      <!-- interact 參數 -->
      <div v-if="step.type === 'interact'" class="bf-step-params">
        <div class="bf-step-param-row">
          <label class="bf-label">目標模式</label>
          <select v-model="getInteractTarget(step).mode" class="bf-select" @change="emitUpdate">
            <option value="nearest">最近可用</option>
            <option value="specific">指定</option>
            <option value="byType">按類型</option>
          </select>
        </div>
        <div v-if="getInteractTarget(step).mode === 'specific'" class="bf-step-param-row">
          <label class="bf-label">互動點</label>
          <select v-model="getInteractTarget(step).interactionPointId" class="bf-select" @change="emitUpdate">
            <option v-for="p in interactionPoints" :key="p.id" :value="p.id">{{ p.label }} ({{ p.action.type }})</option>
          </select>
        </div>
        <div v-if="getInteractTarget(step).mode !== 'specific'" class="bf-step-param-row">
          <label class="bf-label">類型篩選</label>
          <select v-model="getInteractTarget(step).pointType" class="bf-select" @change="emitUpdate">
            <option value="">任意</option>
            <option value="sit">sit</option>
            <option value="work">work</option>
            <option value="stand-idle">stand-idle</option>
          </select>
        </div>
        <div class="bf-step-param-divider">動畫覆寫（留空用互動點預設）</div>
        <div class="bf-step-param-row">
          <label class="bf-label">進入動畫</label>
          <select v-model="step.interactEnterVrma" class="bf-select" @change="emitUpdate">
            <option value="">預設</option>
            <option v-for="f in vrmaFiles" :key="'enter-' + f" :value="f">{{ f }}</option>
          </select>
        </div>
        <div class="bf-step-param-row">
          <label class="bf-label">持續動畫</label>
          <select v-model="step.interactLoopVrma" class="bf-select" @change="emitUpdate">
            <option value="">預設</option>
            <option v-for="f in vrmaFiles" :key="'loop-' + f" :value="f">{{ f }}</option>
          </select>
        </div>
        <div class="bf-step-param-row">
          <label class="bf-label">離開動畫</label>
          <select v-model="step.interactExitVrma" class="bf-select" @change="emitUpdate">
            <option value="">預設</option>
            <option v-for="f in vrmaFiles" :key="'exit-' + f" :value="f">{{ f }}</option>
          </select>
        </div>
        <div class="bf-step-param-divider">方向與前後位移</div>
        <div class="bf-step-param-row">
          <label class="bf-label">動作方向</label>
          <input
            type="range"
            min="-180"
            max="180"
            step="5"
            :value="step.interactRotationYOverride ?? 0"
            class="bf-range"
            @input="step.interactRotationYOverride = Number(($event.target as HTMLInputElement).value); emitUpdate()"
          />
          <span class="bf-range-value">{{ step.interactRotationYOverride != null ? step.interactRotationYOverride + '°' : '預設' }}</span>
          <button v-if="step.interactRotationYOverride != null" type="button" class="bf-btn-icon" title="重設為預設" @click="step.interactRotationYOverride = undefined; emitUpdate()">↺</button>
        </div>
        <div class="bf-step-param-row">
          <label class="bf-label">高度位移</label>
          <input
            type="range"
            min="-1"
            max="1"
            step="0.02"
            :value="step.interactOffsetY ?? 0"
            class="bf-range"
            @input="step.interactOffsetY = Number(($event.target as HTMLInputElement).value); emitUpdate()"
          />
          <span class="bf-range-value">{{ (step.interactOffsetY ?? 0).toFixed(2) }}</span>
          <button v-if="step.interactOffsetY" type="button" class="bf-btn-icon" title="重設為 0" @click="step.interactOffsetY = undefined; emitUpdate()">↺</button>
        </div>
        <div class="bf-step-param-row">
          <label class="bf-label">前後位移</label>
          <input
            type="range"
            min="-0.5"
            max="0.5"
            step="0.02"
            :value="step.interactOffsetZ ?? 0"
            class="bf-range"
            @input="step.interactOffsetZ = Number(($event.target as HTMLInputElement).value); emitUpdate()"
          />
          <span class="bf-range-value">{{ (step.interactOffsetZ ?? 0).toFixed(2) }}</span>
          <button v-if="step.interactOffsetZ" type="button" class="bf-btn-icon" title="重設為 0" @click="step.interactOffsetZ = undefined; emitUpdate()">↺</button>
        </div>
      </div>

      <!-- wait 參數 -->
      <div v-if="step.type === 'wait'" class="bf-step-params">
        <div class="bf-step-param-row">
          <label class="bf-label-check">
            <input type="checkbox" :checked="!!step.waitRandom" @change="toggleWaitRandom(step)" />
            <span>隨機區間</span>
          </label>
        </div>
        <div v-if="step.waitRandom" class="bf-step-param-row">
          <label class="bf-label">最小 (ms)</label>
          <input v-model.number="step.waitRandom.min" type="number" min="0" step="500" class="bf-input bf-input-sm" @change="emitUpdate" />
          <label class="bf-label">最大 (ms)</label>
          <input v-model.number="step.waitRandom.max" type="number" min="0" step="500" class="bf-input bf-input-sm" @change="emitUpdate" />
        </div>
        <div v-else class="bf-step-param-row">
          <label class="bf-label">時長 (ms)</label>
          <input v-model.number="step.waitDuration" type="number" min="0" step="500" class="bf-input bf-input-sm" @change="emitUpdate" />
        </div>
      </div>

      <!-- playMotion 參數 -->
      <div v-if="step.type === 'playMotion'" class="bf-step-params">
        <div class="bf-step-param-row">
          <label class="bf-label">動畫</label>
          <select v-model="step.motionFile" class="bf-select" @change="emitUpdate">
            <option v-for="f in vrmaFiles" :key="f" :value="f">{{ f }}</option>
          </select>
        </div>
        <div class="bf-step-param-row">
          <label class="bf-label">播放模式</label>
          <select v-model="step.motionLoop" class="bf-select" @change="emitUpdate">
            <option value="once">播一次</option>
            <option value="repeat">循環</option>
          </select>
        </div>
        <div v-if="step.motionLoop === 'repeat'" class="bf-step-param-row">
          <label class="bf-label">持續 (ms)</label>
          <input v-model.number="step.motionDuration" type="number" min="0" step="1000" class="bf-input bf-input-sm" placeholder="0=直到下一步" @change="emitUpdate" />
        </div>
        <div class="bf-step-param-divider">位置校正</div>
        <div class="bf-step-param-row">
          <label class="bf-label">X</label>
          <input v-model.number="getMotionOffset(step).x" type="number" step="0.01" class="bf-input bf-input-sm" @change="emitUpdate" />
          <label class="bf-label">Y</label>
          <input v-model.number="getMotionOffset(step).y" type="number" step="0.01" class="bf-input bf-input-sm" @change="emitUpdate" />
          <label class="bf-label">Z</label>
          <input v-model.number="getMotionOffset(step).z" type="number" step="0.01" class="bf-input bf-input-sm" @change="emitUpdate" />
        </div>
        <div class="bf-step-param-divider">朝向</div>
        <div class="bf-step-param-row">
          <label class="bf-label">動作方向</label>
          <input
            type="range"
            min="-180"
            max="180"
            step="5"
            :value="step.motionRotationY ?? 0"
            class="bf-range"
            @input="step.motionRotationY = Number(($event.target as HTMLInputElement).value); emitUpdate()"
          />
          <span class="bf-range-value">{{ step.motionRotationY != null ? step.motionRotationY + '°' : '維持目前方向' }}</span>
          <button v-if="step.motionRotationY != null" type="button" class="bf-btn-icon" title="重設為目前方向" @click="step.motionRotationY = undefined; emitUpdate()">↺</button>
        </div>
      </div>

      <!-- roam 無參數 -->
      <div v-if="step.type === 'roam'" class="bf-step-params bf-step-params-empty">
        隨機漫步（無額外參數）
      </div>
    </div>

    <button type="button" class="bf-btn bf-btn-add-step" @click="addStep">+ 新增步驟</button>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import type { BehaviorFlowStep, FlowMoveTarget, FlowInteractTarget } from './vrmBehaviorFlowConfigUtils'
import { getAvailableVrmaFiles, fetchAvailableVrmaFiles, generateStepId } from './vrmBehaviorFlowConfigUtils'
import type { InteractionPoint } from './vrmInteractionPointUtils'

interface Props {
  steps: BehaviorFlowStep[]
  interactionPoints: InteractionPoint[]
  startCoordinatePick: () => Promise<{ x: number; z: number } | null>
}

const props = defineProps<Props>()

const emit = defineEmits<{
  update: [steps: BehaviorFlowStep[]]
}>()

const localSteps = ref<BehaviorFlowStep[]>(JSON.parse(JSON.stringify(props.steps)))
const vrmaFiles = ref<string[]>(getAvailableVrmaFiles())
const isPicking = ref(false)

onMounted(async () => {
  vrmaFiles.value = await fetchAvailableVrmaFiles()
})

watch(() => props.steps, (s) => {
  // 連續選取期間不覆蓋本地狀態，避免覆蓋剛加的 waypoint
  if (isPicking.value) return
  localSteps.value = JSON.parse(JSON.stringify(s))
}, { deep: true })

function emitUpdate(): void {
  emit('update', JSON.parse(JSON.stringify(localSteps.value)))
}

function stepIcon(type: string): string {
  switch (type) {
    case 'moveTo': return '🚶'
    case 'interact': return '🪑'
    case 'wait': return '⏸'
    case 'playMotion': return '🎬'
    case 'roam': return '🔀'
    default: return '?'
  }
}

function getMoveTarget(step: BehaviorFlowStep): FlowMoveTarget {
  if (!step.moveTarget) {
    step.moveTarget = { mode: 'random', randomRange: 2.0 }
  }
  return step.moveTarget
}

function getCoordinate(step: BehaviorFlowStep): { x: number; z: number } {
  const mt = getMoveTarget(step)
  if (!mt.coordinate) {
    mt.coordinate = { x: 0, z: 0 }
  }
  return mt.coordinate
}

function getInteractTarget(step: BehaviorFlowStep): FlowInteractTarget {
  if (!step.interactTarget) {
    step.interactTarget = { mode: 'nearest', pointType: 'sit' }
  }
  return step.interactTarget
}

function getMotionOffset(step: BehaviorFlowStep): { x: number; y: number; z: number } {
  if (!step.motionOffset) {
    step.motionOffset = { x: 0, y: 0, z: 0 }
  }
  return step.motionOffset
}

function toggleWaitRandom(step: BehaviorFlowStep): void {
  if (step.waitRandom) {
    step.waitDuration = step.waitRandom.min
    step.waitRandom = undefined
  } else {
    step.waitRandom = { min: 2000, max: 5000 }
    step.waitDuration = undefined
  }
  emitUpdate()
}

function onStepTypeChange(idx: number): void {
  const step = localSteps.value[idx]
  // 清除舊參數，保留 id 和 type
  const cleaned: BehaviorFlowStep = { id: step.id, type: step.type }
  switch (step.type) {
    case 'moveTo':
      cleaned.moveTarget = { mode: 'random', randomRange: 2.0 }
      break
    case 'interact':
      cleaned.interactTarget = { mode: 'nearest', pointType: 'sit' }
      break
    case 'wait':
      cleaned.waitDuration = 3000
      break
    case 'playMotion':
      cleaned.motionFile = 'Thinking.vrma'
      cleaned.motionLoop = 'once'
      break
    case 'roam':
      break
  }
  localSteps.value[idx] = cleaned
  emitUpdate()
}

function addStep(): void {
  localSteps.value.push({
    id: generateStepId(),
    type: 'roam',
  })
  emitUpdate()
}

function removeStep(idx: number): void {
  localSteps.value.splice(idx, 1)
  emitUpdate()
}

async function pickCoordinate(step: BehaviorFlowStep): Promise<void> {
  isPicking.value = true
  const coord = await props.startCoordinatePick()
  isPicking.value = false
  if (coord) {
    const mt = getMoveTarget(step)
    if (!mt.coordinate) mt.coordinate = { x: 0, z: 0 }
    mt.coordinate.x = coord.x
    mt.coordinate.z = coord.z
    emitUpdate()
  }
}

function getWaypoints(step: BehaviorFlowStep): Array<{ x: number; z: number }> {
  const mt = getMoveTarget(step)
  if (!mt.waypoints) mt.waypoints = []
  return mt.waypoints
}

function removeWaypoint(step: BehaviorFlowStep, idx: number): void {
  const wps = getWaypoints(step)
  wps.splice(idx, 1)
  emitUpdate()
}

function clearWaypoints(step: BehaviorFlowStep): void {
  getMoveTarget(step).waypoints = []
  emitUpdate()
}

async function pickRoutePoints(step: BehaviorFlowStep): Promise<void> {
  const stepId = step.id
  isPicking.value = true
  while (true) {
    const coord = await props.startCoordinatePick()
    if (!coord) break
    const current = localSteps.value.find((s) => s.id === stepId)
    if (!current) break
    const mt = getMoveTarget(current)
    if (!mt.waypoints) mt.waypoints = []
    mt.waypoints.push(coord)
    // 不在迴圈中 emitUpdate，避免觸發 watch 覆蓋
  }
  isPicking.value = false
  emitUpdate()
}

function moveStep(idx: number, direction: -1 | 1): void {
  const target = idx + direction
  if (target < 0 || target >= localSteps.value.length) return
  const temp = localSteps.value[idx]
  localSteps.value[idx] = localSteps.value[target]
  localSteps.value[target] = temp
  emitUpdate()
}
</script>

<style scoped>
.bf-steps {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.bf-step-card {
  border: 1px solid rgba(182, 212, 248, 0.15);
  border-radius: 8px;
  background: rgba(8, 26, 45, 0.3);
  padding: 6px 8px;
}

.bf-step-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.bf-step-index {
  font-size: 10px;
  font-weight: 700;
  opacity: 0.5;
  min-width: 16px;
}

.bf-step-icon {
  font-size: 12px;
}

.bf-step-type-select {
  font-size: 11px;
  font-weight: 600;
}

.bf-step-label {
  font-size: 10px;
  opacity: 0.6;
  flex: 1;
}

.bf-step-header-actions {
  display: flex;
  gap: 2px;
  margin-left: auto;
}

.bf-btn-icon {
  border: none;
  background: none;
  color: #e9f4ff;
  font-size: 11px;
  padding: 2px 4px;
  cursor: pointer;
  opacity: 0.5;
  border-radius: 3px;
}

.bf-btn-icon:hover {
  opacity: 1;
  background: rgba(74, 158, 255, 0.15);
}

.bf-btn-icon-danger:hover {
  color: #ff9999;
  background: rgba(255, 100, 100, 0.15);
}

.bf-step-params {
  padding: 4px 0 2px 22px;
}

.bf-step-params-empty {
  font-size: 10px;
  opacity: 0.5;
  font-style: italic;
}

.bf-step-param-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
  flex-wrap: wrap;
}

.bf-label {
  font-size: 10px;
  opacity: 0.7;
  min-width: 55px;
  flex-shrink: 0;
}

.bf-label-check {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  cursor: pointer;
}

.bf-label-check input[type="checkbox"] {
  width: 12px;
  height: 12px;
  accent-color: #4a9eff;
}

.bf-select {
  border: 1px solid rgba(182, 212, 248, 0.2);
  border-radius: 4px;
  background: rgba(8, 26, 45, 0.5);
  color: #e9f4ff;
  font-size: 10px;
  padding: 2px 4px;
}

.bf-input {
  border: 1px solid rgba(182, 212, 248, 0.2);
  border-radius: 4px;
  background: rgba(8, 26, 45, 0.5);
  color: #e9f4ff;
  font-size: 10px;
  padding: 2px 4px;
}

.bf-input-sm {
  width: 55px;
}

.bf-step-params-route {
  padding: 4px 0 2px 22px;
}

.bf-route-waypoints {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 4px;
  max-height: 120px;
  overflow-y: auto;
}

.bf-route-wp {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  padding: 1px 0;
}

.bf-route-wp-idx {
  font-weight: 700;
  opacity: 0.5;
  min-width: 18px;
}

.bf-route-wp-coord {
  font-family: monospace;
  opacity: 0.8;
}

.bf-route-empty {
  font-size: 10px;
  opacity: 0.4;
  font-style: italic;
  padding: 2px 0;
}

.bf-route-actions {
  display: flex;
  gap: 6px;
  margin-bottom: 4px;
}

.bf-step-param-divider {
  font-size: 9px;
  opacity: 0.45;
  margin: 4px 0 2px;
  border-top: 1px solid rgba(182, 212, 248, 0.08);
  padding-top: 4px;
}

.bf-btn-pick {
  border: 1px solid rgba(255, 220, 124, 0.4);
  border-radius: 4px;
  background: rgba(255, 220, 124, 0.12);
  color: #ffe8a0;
  font-size: 10px;
  font-weight: 600;
  padding: 2px 6px;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s;
}

.bf-btn-pick:hover {
  background: rgba(255, 220, 124, 0.25);
}

.bf-range {
  flex: 1;
  min-width: 60px;
  max-width: 120px;
  height: 14px;
  accent-color: #4a9eff;
  cursor: pointer;
}

.bf-range-value {
  font-size: 10px;
  font-family: monospace;
  min-width: 32px;
  text-align: right;
  opacity: 0.8;
}

.bf-btn-add-step {
  border: 1px dashed rgba(182, 212, 248, 0.25);
  border-radius: 6px;
  background: transparent;
  color: #e9f4ff;
  font-size: 11px;
  padding: 6px;
  cursor: pointer;
  opacity: 0.6;
  transition: opacity 0.15s, border-color 0.15s;
}

.bf-btn-add-step:hover {
  opacity: 1;
  border-color: rgba(74, 158, 255, 0.5);
}
</style>
