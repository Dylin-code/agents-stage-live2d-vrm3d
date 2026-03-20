<template>
  <div ref="stageContainerRef" class="vrm-stage-scene">
    <div class="vrm-stage-mask"></div>

    <!-- 行為流編輯面板 -->
    <BehaviorFlowEditorPanel
      :visible="behaviorFlowEditorVisible"
      :config-utils="behaviorFlowConfigUtils"
      :actors="actorList"
      :interaction-points="interactionPointList"
      :is-test-running="isTestRunning"
      :start-coordinate-pick="startCoordinatePick"
      @close="handleBehaviorFlowClose"
      @preview-update="onBehaviorFlowPreviewUpdate"
      @test-execute="onTestExecute"
      @test-stop="onTestStop"
    />

    <InteractionPointEditorPanel
      :visible="interactionEditorEnabled"
      :points="interactionPointList"
      :selected-point-id="selectedInteractionPointId"
      :draft-point="interactionDraftPoint"
      :has-changes="interactionDraftHasChanges"
      :transform-target="interactionTransformTarget"
      @close="setInteractionEditorEnabled(false)"
      @create="createInteractionPointDraft"
      @select="setSelectedInteractionPoint"
      @update-draft="updateInteractionPointDraft"
      @save-selected="saveSelectedInteractionPoint"
      @discard-selected="discardSelectedInteractionPoint"
      @delete-selected="deleteSelectedInteractionPoint"
      @set-transform-target="setInteractionTransformTarget"
    />

    <!-- 座標拾取覆蓋層 -->
    <div
      v-if="coordinatePickMode"
      class="vrm-coordinate-pick-overlay"
      @click="onSceneClick"
    >
      <div class="vrm-coordinate-pick-hint">
        點擊場景地面選取座標（按「完成」結束連續選取）
        <button type="button" class="vrm-coordinate-pick-cancel" @click.stop="cancelCoordinatePick">完成</button>
      </div>
    </div>

    <div v-if="loadingText" class="vrm-stage-loading">{{ loadingText }}</div>
  </div>
</template>

<script setup lang="ts">
import { toRef, ref, computed, onMounted, onUnmounted } from 'vue'

import type { SessionSnapshotItem, SessionState } from '../../types/sessionState'
import { useVrmStage } from './useVrmStage'
import BehaviorFlowEditorPanel from './BehaviorFlowEditorPanel.vue'
import InteractionPointEditorPanel from './InteractionPointEditorPanel.vue'
import type { BehaviorFlow } from './vrmBehaviorFlowConfigUtils'
import { VRM_INTERACTION_EDITOR_TOGGLE_EVENT } from './vrmInteractionPointEvents'

interface Props {
  visibleSessions: SessionSnapshotItem[]
  selectedChatSessionId: string
  stateText: (state: SessionState) => string
  openSessionChatBySessionId: (sessionId: string) => void
  summonSessionBySessionId: (sessionId: string) => void
}

const props = defineProps<Props>()
const stageContainerRef = ref<HTMLElement | null>(null)
const behaviorFlowEditorVisible = ref(false)

// 監聽齒輪面板發出的行為流開關事件
const BEHAVIOR_FLOW_TOGGLE_EVENT = 'vrm-stage:toggle-behavior-flow-editor'

function handleBehaviorFlowToggleEvent(): void {
  behaviorFlowEditorVisible.value = !behaviorFlowEditorVisible.value
}

function handleInteractionEditorToggleEvent(): void {
  setInteractionEditorEnabled()
}

onMounted(() => {
  window.addEventListener(BEHAVIOR_FLOW_TOGGLE_EVENT, handleBehaviorFlowToggleEvent)
  window.addEventListener(VRM_INTERACTION_EDITOR_TOGGLE_EVENT, handleInteractionEditorToggleEvent)
})

onUnmounted(() => {
  window.removeEventListener(BEHAVIOR_FLOW_TOGGLE_EVENT, handleBehaviorFlowToggleEvent)
  window.removeEventListener(VRM_INTERACTION_EDITOR_TOGGLE_EVENT, handleInteractionEditorToggleEvent)
})

function handleCharacterClick(sessionId: string): void {
  props.openSessionChatBySessionId(sessionId)
}

const {
  loadingText,
  behaviorFlowConfigUtils,
  testExecuteFlow,
  testStopFlow,
  isTestRunning,
  refreshBehaviorFlowPreview,
  getActorList,
  getInteractionPoints,
  pickGroundPoint,
  interactionEditorEnabled,
  selectedInteractionPointId,
  interactionDraftPoint,
  interactionTransformTarget,
  hasInteractionDraftChanges,
  setInteractionEditorEnabled,
  setSelectedInteractionPoint,
  createInteractionPointDraft,
  updateInteractionPointDraft,
  saveSelectedInteractionPoint,
  discardSelectedInteractionPoint,
  deleteSelectedInteractionPoint,
  setInteractionTransformTarget,
} = useVrmStage({
  containerRef: stageContainerRef,
  visibleSessions: toRef(props, 'visibleSessions'),
  selectedChatSessionId: toRef(props, 'selectedChatSessionId'),
  onCharacterClick: handleCharacterClick,
})

const actorList = computed(() => getActorList())
const interactionPointList = computed(() => getInteractionPoints())
const interactionDraftHasChanges = computed(() => hasInteractionDraftChanges())

// ─── 座標拾取模式 ───
const coordinatePickMode = ref(false)
let coordinatePickResolve: ((coord: { x: number; z: number } | null) => void) | null = null

function startCoordinatePick(): Promise<{ x: number; z: number } | null> {
  coordinatePickMode.value = true
  return new Promise((resolve) => {
    coordinatePickResolve = resolve
  })
}

function onSceneClick(event: MouseEvent): void {
  if (!coordinatePickMode.value) return
  const point = pickGroundPoint(event.clientX, event.clientY)
  coordinatePickMode.value = false
  if (coordinatePickResolve) {
    coordinatePickResolve(point ? { x: Math.round(point.x * 100) / 100, z: Math.round(point.z * 100) / 100 } : null)
    coordinatePickResolve = null
  }
}

function cancelCoordinatePick(): void {
  coordinatePickMode.value = false
  if (coordinatePickResolve) {
    coordinatePickResolve(null)
    coordinatePickResolve = null
  }
}

function onTestExecute(sessionId: string, flow: BehaviorFlow): void {
  testExecuteFlow(sessionId, flow)
}

function onTestStop(sessionId: string): void {
  testStopFlow(sessionId)
}

function handleBehaviorFlowClose(): void {
  behaviorFlowEditorVisible.value = false
}

function onBehaviorFlowPreviewUpdate(): void {
  void refreshBehaviorFlowPreview()
}
</script>

<style scoped>
.vrm-stage-scene {
  position: absolute;
  inset: 0;
  overflow: hidden;
}

.vrm-stage-mask {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 2;
  background:
    radial-gradient(circle at 54% 72%, rgba(255, 239, 205, 0.16) 0%, rgba(255, 239, 205, 0) 46%),
    linear-gradient(180deg, rgba(120, 150, 196, 0.12) 0%, rgba(15, 24, 36, 0.24) 100%);
}

.vrm-stage-loading {
  position: absolute;
  left: 50%;
  bottom: 16px;
  transform: translateX(-50%);
  z-index: 3;
  border-radius: 999px;
  border: 1px solid rgba(182, 212, 248, 0.38);
  background: rgba(6, 18, 33, 0.8);
  color: #e9f4ff;
  font-size: 12px;
  padding: 6px 10px;
  pointer-events: none;
}

.vrm-coordinate-pick-overlay {
  position: absolute;
  inset: 0;
  z-index: 20;
  cursor: crosshair;
}

.vrm-coordinate-pick-hint {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-radius: 10px;
  border: 1px solid rgba(255, 220, 124, 0.65);
  background: rgba(49, 33, 8, 0.85);
  color: #ffe8a0;
  font-size: 14px;
  font-weight: 700;
  pointer-events: auto;
}

.vrm-coordinate-pick-cancel {
  border: 1px solid rgba(255, 200, 100, 0.4);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.1);
  color: #ffe8a0;
  font-size: 12px;
  font-weight: 600;
  padding: 4px 10px;
  cursor: pointer;
}

.vrm-coordinate-pick-cancel:hover {
  background: rgba(255, 255, 255, 0.2);
}

:deep(canvas) {
  position: absolute;
  inset: 0;
}

:deep(.session-head-label) {
  pointer-events: none;
  min-width: 84px;
  max-width: 210px;
  border-radius: 12px;
  border: 1px solid rgba(186, 217, 255, 0.62);
  background: rgba(8, 18, 30, 0.86);
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.34);
  color: #f3f8ff;
  padding: 5px 8px;
  text-align: center;
  transform: translate(-50%, calc(-100% - 8px));
  transform-origin: center bottom;
}

:deep(.session-head-label::after) {
  content: '';
  position: absolute;
  left: 50%;
  bottom: -8px;
  width: 12px;
  height: 12px;
  transform: translateX(-50%) rotate(45deg);
  border-right: 1px solid rgba(186, 217, 255, 0.62);
  border-bottom: 1px solid rgba(186, 217, 255, 0.62);
  background: rgba(8, 18, 30, 0.86);
}

:deep(.session-head-label .title) {
  font-size: 12px;
  font-weight: 700;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

:deep(.session-head-label .state) {
  margin-top: 4px;
  font-size: 11px;
  opacity: 0.9;
}

:deep(.session-head-label .context) {
  margin-top: 3px;
  font-size: 11px;
  font-weight: 700;
  color: #fff1c7;
  opacity: 0.95;
}

:deep(.session-head-label.selected) {
  box-shadow: 0 0 0 1px rgba(131, 225, 255, 0.5), 0 12px 28px rgba(0, 0, 0, 0.34);
  min-width: 134px;
  padding: 7px 9px;
}

:deep(.session-head-label:not(.selected) .state),
:deep(.session-head-label:not(.selected) .context) {
  display: none;
}

:deep(.session-head-label:not(.selected)::after) {
  display: none;
}

:deep(.session-head-label.state-idle) { border-color: rgba(190, 205, 219, 0.74); }
:deep(.session-head-label.state-thinking) { border-color: rgba(255, 218, 136, 0.84); }
:deep(.session-head-label.state-tooling) { border-color: rgba(168, 199, 255, 0.84); }
:deep(.session-head-label.state-responding) { border-color: rgba(136, 237, 227, 0.88); }
:deep(.session-head-label.state-waiting) { border-color: rgba(255, 153, 153, 0.9); }
</style>
