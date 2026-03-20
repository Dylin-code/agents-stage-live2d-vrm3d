<template>
  <div class="session-stage">
    <slot
      name="scene"
      :stageCanvas="stageCanvas"
      :visibleSessions="visibleSessions"
      :selectedChatSessionId="selectedChatSessionId"
      :openSessionChatBySessionId="openSessionChatBySessionId"
      :summonSessionBySessionId="handleSessionCardClick"
      :stateText="stateText"
    >
      <canvas v-if="isLive2DRenderer" ref="stageCanvas"></canvas>
    </slot>

    <div class="stage-header">
      <div class="status-chip" :class="connectionStatusClass">
        {{ connectionStatusText }}
      </div>
      <div class="status-chip neutral">Active: {{ activeCount }}/{{ MAX_SESSIONS }}</div>
      <div class="status-chip neutral">{{ globalRateLimitText }}</div>
      <button class="stage-view-switch" type="button" @click="switchView">
        {{ switchButtonText }}
      </button>
    </div>

    <div class="role-settings-panel" :class="{ collapsed: roleSettingsCollapsed }">
      <div class="role-settings-header">
        <div v-if="!roleSettingsCollapsed" class="role-settings-title">角色選單設定</div>
        <button class="role-settings-toggle" @click="toggleRoleSettingsPanel" aria-label="切換角色選單設定面板">
          ⚙
        </button>
      </div>
      <div v-if="!roleSettingsCollapsed">
        <template v-if="isLive2DRenderer">
          <div class="role-setting-item">
            <label>Session 角色池 (逗號分隔)</label>
            <input
              v-model="modelPathsInput"
              type="text"
              placeholder="assets/models/A/a.model3.json, assets/models/B/b.model3.json"
              @change="applyModelPathsInput"
            >
          </div>
          <div class="role-setting-item">
            <div class="role-setting-header">
              <label>固定目錄模型清單</label>
              <span class="models-root" v-if="modelsRootDir">{{ modelsRootDir }}</span>
            </div>
            <div class="models-status" v-if="modelsLoading">載入模型中...</div>
            <div class="models-status error" v-else-if="modelsError">{{ modelsError }}</div>
            <div class="role-model-grid" v-else>
              <div
                v-for="item in live2dModels"
                :key="item.id"
                class="role-model-card"
                :class="{ selected: isModelSelected(item.model_path) }"
                @click="toggleModel(item.model_path)"
              >
                <div class="role-model-preview">
                  <img v-if="item.preview_image" :src="toAssetUrl(item.preview_image)" :alt="item.name">
                  <div v-else class="role-model-preview-fallback">No Preview</div>
                </div>
                <div class="role-model-info">
                  <input
                    type="checkbox"
                    :checked="isModelSelected(item.model_path)"
                    @click.stop
                    @change="toggleModel(item.model_path)"
                  >
                  <div class="role-model-meta">
                    <div class="role-model-name">{{ item.name }}</div>
                    <div class="role-model-path">{{ item.model_path }}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>
        <div v-else>
          <div class="role-setting-note">
            3D 版角色來源跟隨 Session，仍可操作對話與切換視角。
          </div>
          <div class="role-setting-item">
            <label>全域地板偏移（3D）</label>
            <div class="role-setting-slider-row">
              <input
                :value="vrmGlobalGroundOffset"
                type="range"
                :min="VRM_GLOBAL_GROUND_OFFSET_MIN"
                :max="VRM_GLOBAL_GROUND_OFFSET_MAX"
                :step="VRM_GLOBAL_GROUND_OFFSET_STEP"
                aria-label="全域地板偏移"
                @input="handleVrmGroundOffsetInput"
              >
              <span class="role-setting-slider-value">{{ vrmGlobalGroundOffsetLabel }}</span>
            </div>
          </div>
          <div class="role-setting-item">
            <label>人物大小（3D）</label>
            <div class="role-setting-slider-row">
              <input
                :value="vrmActorScale"
                type="range"
                :min="VRM_ACTOR_SCALE_MIN"
                :max="VRM_ACTOR_SCALE_MAX"
                :step="VRM_ACTOR_SCALE_STEP"
                aria-label="人物大小"
                @input="handleVrmActorScaleInput"
              >
              <span class="role-setting-slider-value">{{ vrmActorScaleLabel }}</span>
            </div>
          </div>
          <div class="role-setting-item">
            <div class="role-setting-header">
              <label>固定角色槽位（3D）</label>
            </div>
            <div class="role-setting-note">
              每個位置固定使用指定 VRM 角色，初始化與補位都不再隨機抽取。
            </div>
            <div class="vrm-slot-config-list">
              <div v-for="(modelUrl, index) in vrmActorSlotConfig" :key="index" class="vrm-slot-config-row">
                <span class="vrm-slot-config-label">Slot {{ index }}</span>
                <select
                  :value="modelUrl"
                  class="vrm-slot-config-select"
                  @change="handleVrmActorSlotConfigChange(index, $event)"
                >
                  <option v-for="option in vrmActorSlotOptions" :key="option.modelUrl" :value="option.modelUrl">
                    {{ option.label }}
                  </option>
                </select>
              </div>
            </div>
          </div>
          <div class="role-setting-item">
            <button
              type="button"
              class="role-setting-behavior-flow-btn"
              @click="toggleBehaviorFlowEditor"
            >
              行為流設定
            </button>
          </div>
          <div class="role-setting-item">
            <button
              type="button"
              class="role-setting-behavior-flow-btn"
              @click="toggleInteractionEditor"
            >
              互動點編輯
            </button>
          </div>
          <div class="role-setting-item">
            <button
              type="button"
              class="role-setting-behavior-flow-btn"
              @click="reloadInteractionPoints"
            >
              重新讀取互動點
            </button>
          </div>
        </div>
      </div>
    </div>

    <aside
      ref="sessionSidebarRef"
      class="session-sidebar"
      :class="{ 'with-chat': chatModalVisible, 'focus-chat': focusChatMode }"
    >
      <div class="session-sidebar-header">
        <div class="session-sidebar-title">對話串</div>
        <button class="new-session-btn" type="button" @click="newSessionOpen = !newSessionOpen">
          {{ newSessionOpen ? '收起' : '新增' }}
        </button>
      </div>

      <div v-if="newSessionOpen" class="new-session-panel">
        <select
          v-model="newSessionCwdSelection"
          class="new-session-cwd-select"
          @change="onNewSessionCwdSelectionChange"
        >
          <option value="">選擇歷史目錄或新路徑</option>
          <option v-for="cwd in newSessionCwdOptions" :key="cwd" :value="cwd">{{ cwd }}</option>
          <option value="__pick_new__">+ 新路徑（選擇器）</option>
        </select>
        <input
          v-model.trim="newSessionForm.cwd"
          type="text"
          list="new-session-cwd-options"
          class="new-session-cwd-input"
          placeholder="選擇歷史目錄或輸入 /path/to/workspace"
        >
        <datalist id="new-session-cwd-options">
          <option v-for="cwd in newSessionCwdOptions" :key="cwd" :value="cwd"></option>
        </datalist>
        <div v-if="newSessionCwdOptions.length" class="new-session-cwd-hint">
          可直接選擇最近使用的工作目錄，也可手動輸入新目錄
        </div>
        <div class="new-session-controls">
          <select v-model="newSessionForm.agent_brand" class="new-session-brand-select" @change="onBrandChange">
            <option v-for="brand in agentBrandOptions" :key="brand.brand" :value="brand.brand">
              {{ brand.display_name }}
            </option>
          </select>
          <select v-model="newSessionForm.model">
            <option value="">Model: 預設</option>
            <option v-for="model in newSessionModelOptions" :key="model" :value="model">{{ model }}</option>
          </select>
          <select v-model="newSessionForm.reasoning_effort">
            <option value="">推理: 預設</option>
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </select>
          <select v-model="newSessionForm.permission_mode">
            <option value="default">執行模式: 預設 (自動接受編輯)</option>
            <option value="full">執行模式: 完整存取權</option>
          </select>
        </div>
        <label class="new-session-plan-toggle">
          <input type="checkbox" v-model="newSessionForm.plan_mode">
          計劃模式
        </label>
        <div class="new-session-actions">
          <button
            class="new-session-submit"
            type="button"
            :disabled="creatingNewSession || !newSessionForm.cwd"
            @click="createNewSession"
          >
            {{ creatingNewSession ? '建立中...' : '建立 Session' }}
          </button>
        </div>
      </div>

      <div class="session-groups">
        <div
          v-for="session in historySessions"
          :key="session.session_id"
          class="session-card"
          :class="[
            `state-${session.state.toLowerCase()}`,
            {
              active: isActorVisible(session.session_id),
              selected: selectedChatSessionId === session.session_id,
            },
          ]"
          @click="handleSidebarSessionCardClick(session)"
        >
          <div class="session-card-top">
            <img
              class="session-brand-icon"
              :src="`/brand/${session.agent_brand || 'codex'}-badge.svg`"
              :alt="session.agent_brand || 'codex'"
              draggable="false"
            />
            <span class="session-name">{{ session.display_name }}</span>
          </div>
          <div class="session-state">{{ stateText(session.state) }}</div>
          <div class="session-state-muted">
            {{ session.branch ? `Branch: ${session.branch}` : `Session: ${session.session_id.slice(0, 8)}` }}
          </div>
          <div class="session-cwd" :title="session.cwd || '未設定工作目錄'">{{ session.cwd || '未設定工作目錄' }}</div>
          <div class="session-time">{{ relativeTime(session.last_seen_at) }}</div>
        </div>
      </div>
    </aside>

    <div
      v-if="chatModalVisible"
      ref="chatDockRef"
      class="chat-dock"
    >
      <div class="chat-dock-header">
        <div class="chat-dock-title">{{ chatConversation.label || 'Session 對話' }}</div>
        <div class="chat-dock-path">{{ activeChatSessionCwd || '未設定工作目錄' }}</div>
        <button class="chat-dock-close" type="button" @click="closeSessionChat">關閉</button>
      </div>
      <div class="chat-dock-body">
        <ChatPage
          :conversation="chatConversation"
          :onNewMessage="handleChatConversationUpdate"
          :systemSettings="chatSystemSettings"
          :forceAgentSession="true"
          :agentSessionOptions="activeChatAgentOptions"
          :onAgentSessionOptionsChange="handleActiveSessionAgentOptionsChange"
          :onRequestRefreshBranches="refreshActiveSessionBranches"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ChatPage from '../chat.vue'
import { useSessionStage, type SessionStageRenderer } from '../../pages/session-stage/useSessionStage'
import type { SessionSnapshotItem } from '../../types/sessionState'
import {
  VRM_ACTOR_SCALE_DEFAULT,
  VRM_ACTOR_SCALE_EVENT,
  VRM_ACTOR_SCALE_MAX,
  VRM_ACTOR_SCALE_MIN,
  VRM_ACTOR_SCALE_STEP,
  loadVrmActorScale,
  saveVrmActorScale,
} from './vrmActorScaleSettings'
import {
  VRM_GLOBAL_GROUND_OFFSET_EVENT,
  VRM_GLOBAL_GROUND_OFFSET_MAX,
  VRM_GLOBAL_GROUND_OFFSET_MIN,
  VRM_GLOBAL_GROUND_OFFSET_STEP,
  loadVrmGlobalGroundOffset,
  saveVrmGlobalGroundOffset,
} from './vrmGroundOffsetSettings'
import {
  DEFAULT_VRM_ACTOR_SLOT_OPTIONS,
  VRM_ACTOR_SLOT_CONFIG_EVENT,
  loadVrmActorSlotConfig,
  saveVrmActorSlotConfig,
} from './vrmActorSlotSettings'
import {
  VRM_INTERACTION_EDITOR_TOGGLE_EVENT,
  VRM_INTERACTION_POINTS_RELOAD_EVENT,
} from './vrmInteractionPointEvents'

interface Props {
  rendererMode?: SessionStageRenderer
  switchToPath: string
  switchButtonText: string
}

const props = withDefaults(defineProps<Props>(), {
  rendererMode: 'live2d',
})

const router = useRouter()
const route = useRoute()
const vrmGlobalGroundOffset = ref(0)
const vrmActorScale = ref(VRM_ACTOR_SCALE_DEFAULT)
const vrmActorSlotOptions = DEFAULT_VRM_ACTOR_SLOT_OPTIONS
const vrmActorSlotConfig = ref<string[]>(loadVrmActorSlotConfig(vrmActorSlotOptions))

const vrmGlobalGroundOffsetLabel = computed(() => `${(vrmGlobalGroundOffset.value * 100).toFixed(1)} cm`)
const vrmActorScaleLabel = computed(() => `${Math.round(vrmActorScale.value * 100)}%`)

function applyVrmGlobalGroundOffset(value: number): void {
  const next = saveVrmGlobalGroundOffset(value)
  vrmGlobalGroundOffset.value = next
  window.dispatchEvent(new CustomEvent(VRM_GLOBAL_GROUND_OFFSET_EVENT, {
    detail: { value: next },
  }))
}

function handleVrmGroundOffsetInput(event: Event): void {
  const target = event.target as HTMLInputElement | null
  if (!target) return
  const value = Number.parseFloat(target.value)
  if (!Number.isFinite(value)) return
  applyVrmGlobalGroundOffset(value)
}

function handleVrmGroundOffsetEvent(event: Event): void {
  const customEvent = event as CustomEvent<{ value?: unknown }>
  const next = Number(customEvent.detail?.value)
  if (!Number.isFinite(next)) return
  vrmGlobalGroundOffset.value = next
}

function applyVrmActorScale(value: number): void {
  const next = saveVrmActorScale(value)
  vrmActorScale.value = next
  window.dispatchEvent(new CustomEvent(VRM_ACTOR_SCALE_EVENT, {
    detail: { value: next },
  }))
}

function handleVrmActorScaleInput(event: Event): void {
  const target = event.target as HTMLInputElement | null
  if (!target) return
  const value = Number.parseFloat(target.value)
  if (!Number.isFinite(value)) return
  applyVrmActorScale(value)
}

function handleVrmActorScaleEvent(event: Event): void {
  const customEvent = event as CustomEvent<{ value?: unknown }>
  const next = Number(customEvent.detail?.value)
  if (!Number.isFinite(next)) return
  vrmActorScale.value = next
}

function applyVrmActorSlotConfig(nextConfig: string[]): void {
  const next = saveVrmActorSlotConfig(nextConfig, vrmActorSlotOptions)
  vrmActorSlotConfig.value = next
  window.dispatchEvent(new CustomEvent(VRM_ACTOR_SLOT_CONFIG_EVENT, {
    detail: { value: next },
  }))
}

function handleVrmActorSlotConfigChange(index: number, event: Event): void {
  const target = event.target as HTMLSelectElement | null
  if (!target) return
  const next = [...vrmActorSlotConfig.value]
  next[index] = target.value
  applyVrmActorSlotConfig(next)
}

function handleVrmActorSlotConfigEvent(event: Event): void {
  const customEvent = event as CustomEvent<{ value?: unknown }>
  vrmActorSlotConfig.value = saveVrmActorSlotConfig(
    Array.isArray(customEvent.detail?.value) ? customEvent.detail?.value as string[] : vrmActorSlotConfig.value,
    vrmActorSlotOptions,
  )
}

function toggleBehaviorFlowEditor(): void {
  roleSettingsCollapsed.value = true
  window.dispatchEvent(new CustomEvent('vrm-stage:toggle-behavior-flow-editor'))
}

function toggleInteractionEditor(): void {
  roleSettingsCollapsed.value = true
  window.dispatchEvent(new CustomEvent(VRM_INTERACTION_EDITOR_TOGGLE_EVENT))
}

function reloadInteractionPoints(): void {
  roleSettingsCollapsed.value = true
  window.dispatchEvent(new CustomEvent(VRM_INTERACTION_POINTS_RELOAD_EVENT))
}

function switchView(): void {
  if (route.path === props.switchToPath) return
  void router.push(props.switchToPath)
}

function handleSidebarSessionCardClick(session: SessionSnapshotItem): void {
  if (isLive2DRenderer) {
    handleSessionCardClick(session.session_id)
    return
  }
  window.dispatchEvent(new CustomEvent('session-stage:sidebar-session-click', {
    detail: { session },
  }))
}

const {
  MAX_SESSIONS,
  isLive2DRenderer,
  stageCanvas,
  visibleSessions,
  connectionStatusClass,
  connectionStatusText,
  globalRateLimitText,
  activeCount,
  roleSettingsCollapsed,
  toggleRoleSettingsPanel,
  modelPathsInput,
  applyModelPathsInput,
  modelsRootDir,
  modelsLoading,
  modelsError,
  live2dModels,
  isModelSelected,
  toggleModel,
  toAssetUrl,
  sessionSidebarRef,
  chatModalVisible,
  focusChatMode,
  newSessionOpen,
  newSessionCwdSelection,
  onNewSessionCwdSelectionChange,
  newSessionCwdOptions,
  newSessionForm,
  agentBrandOptions,
  newSessionModelOptions,
  creatingNewSession,
  createNewSession,
  historySessions,
  isActorVisible,
  selectedChatSessionId,
  handleSessionCardClick,
  stateText,
  relativeTime,
  chatDockRef,
  chatConversation,
  activeChatSessionCwd,
  closeSessionChat,
  openSessionChatBySessionId,
  handleChatConversationUpdate,
  chatSystemSettings,
  activeChatAgentOptions,
  handleActiveSessionAgentOptionsChange,
  refreshActiveSessionBranches,
} = useSessionStage({ renderer: props.rendererMode })

function onBrandChange(): void {
  newSessionForm.model = ''
  newSessionForm.permission_mode = 'default'
  newSessionForm.plan_mode = false
}

onMounted(() => {
  vrmGlobalGroundOffset.value = loadVrmGlobalGroundOffset()
  vrmActorScale.value = loadVrmActorScale()
  vrmActorSlotConfig.value = loadVrmActorSlotConfig(vrmActorSlotOptions)
  window.addEventListener(VRM_GLOBAL_GROUND_OFFSET_EVENT, handleVrmGroundOffsetEvent as EventListener)
  window.addEventListener(VRM_ACTOR_SCALE_EVENT, handleVrmActorScaleEvent as EventListener)
  window.addEventListener(VRM_ACTOR_SLOT_CONFIG_EVENT, handleVrmActorSlotConfigEvent as EventListener)
})

onUnmounted(() => {
  window.removeEventListener(VRM_GLOBAL_GROUND_OFFSET_EVENT, handleVrmGroundOffsetEvent as EventListener)
  window.removeEventListener(VRM_ACTOR_SCALE_EVENT, handleVrmActorScaleEvent as EventListener)
  window.removeEventListener(VRM_ACTOR_SLOT_CONFIG_EVENT, handleVrmActorSlotConfigEvent as EventListener)
})
</script>

<style scoped>
.session-stage {
  width: 100vw;
  height: 100vh;
  position: fixed;
  top: 0;
  left: 0;
  background:
    radial-gradient(circle at 18% 16%, rgba(148, 183, 255, 0.35), transparent 28%),
    radial-gradient(circle at 82% 14%, rgba(115, 236, 210, 0.24), transparent 26%),
    linear-gradient(165deg, #0f1e33 0%, #182e4f 54%, #20395e 100%);
  overflow: hidden;
}

canvas {
  width: 100%;
  height: 100%;
  display: block;
}

.stage-header {
  position: absolute;
  z-index: 10;
  top: 16px;
  left: 16px;
  display: flex;
  gap: 10px;
  align-items: center;
}

.status-chip {
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.2px;
  backdrop-filter: blur(8px);
  color: #f5f8ff;
}

.status-chip.connected {
  background: rgba(31, 150, 114, 0.66);
  border: 1px solid rgba(151, 255, 220, 0.35);
}

.status-chip.connecting {
  background: rgba(177, 127, 38, 0.56);
  border: 1px solid rgba(255, 227, 170, 0.32);
}

.status-chip.disconnected {
  background: rgba(154, 66, 66, 0.56);
  border: 1px solid rgba(255, 179, 179, 0.32);
}

.status-chip.neutral {
  background: rgba(255, 255, 255, 0.14);
  border: 1px solid rgba(255, 255, 255, 0.2);
}

.stage-view-switch {
  border-radius: 999px;
  border: 1px solid rgba(188, 216, 252, 0.45);
  background: rgba(12, 39, 68, 0.86);
  color: #ecf5ff;
  font-size: 12px;
  font-weight: 700;
  padding: 6px 10px;
  cursor: pointer;
}

.stage-view-switch:hover {
  background: rgba(17, 49, 82, 0.9);
}

.session-sidebar {
  position: absolute;
  z-index: 12;
  top: 16px;
  right: 16px;
  width: min(360px, calc(100vw - 24px));
  max-height: calc(100vh - 32px);
  padding: 12px;
  border-radius: 14px;
  background: rgba(7, 19, 34, 0.68);
  border: 1px solid rgba(196, 222, 255, 0.28);
  backdrop-filter: blur(10px);
  box-shadow: 0 14px 28px rgba(3, 9, 20, 0.38);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.session-sidebar.focus-chat {
  display: none;
}

.session-sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.session-sidebar-title {
  color: #f1f7ff;
  font-size: 15px;
  font-weight: 700;
}

.new-session-btn {
  border-radius: 999px;
  border: 1px solid rgba(180, 211, 247, 0.42);
  background: rgba(14, 38, 63, 0.8);
  color: #eaf3ff;
  font-size: 12px;
  font-weight: 600;
  padding: 4px 10px;
  cursor: pointer;
}

.new-session-btn:hover {
  background: rgba(20, 46, 76, 0.9);
}

.new-session-panel {
  border: 1px solid rgba(178, 209, 246, 0.28);
  background: rgba(6, 18, 33, 0.55);
  border-radius: 12px;
  padding: 10px;
  margin-bottom: 10px;
}

.new-session-cwd-input {
  margin-top: 6px;
  width: 100%;
  border: 1px solid rgba(182, 212, 248, 0.3);
  border-radius: 8px;
  background: rgba(7, 17, 30, 0.8);
  color: #ecf5ff;
  font-size: 12px;
  padding: 6px 8px;
}

.new-session-cwd-select {
  width: 100%;
  border: 1px solid rgba(182, 212, 248, 0.3);
  border-radius: 8px;
  background: rgba(7, 17, 30, 0.8);
  color: #ecf5ff;
  font-size: 12px;
  padding: 6px 8px;
}

.new-session-cwd-hint {
  margin-top: 4px;
  color: rgba(214, 230, 247, 0.7);
  font-size: 11px;
  line-height: 1.4;
}

.new-session-controls {
  margin-top: 8px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
}

.new-session-controls select {
  border: 1px solid rgba(182, 212, 248, 0.3);
  border-radius: 8px;
  background: rgba(7, 17, 30, 0.8);
  color: #ecf5ff;
  font-size: 12px;
  padding: 4px 6px;
}

.new-session-brand-select {
  grid-column: 1 / -1;
  font-weight: 600;
}

.new-session-plan-toggle {
  margin-top: 8px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #d9e9ff;
  font-size: 12px;
  font-weight: 600;
}

.new-session-actions {
  margin-top: 8px;
}

.new-session-submit {
  width: 100%;
  border: 1px solid rgba(178, 209, 246, 0.38);
  background: rgba(17, 60, 95, 0.86);
  color: #f3f9ff;
  border-radius: 10px;
  padding: 7px 10px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.new-session-submit:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.session-groups {
  flex: 1;
  overflow: auto;
  padding-right: 2px;
}

.session-card {
  margin-top: 6px;
  border-radius: 12px;
  padding: 10px 12px;
  color: #f8fbff;
  background: rgba(10, 20, 35, 0.62);
  border: 1px solid rgba(255, 255, 255, 0.16);
  backdrop-filter: blur(8px);
  box-shadow: 0 8px 18px rgba(0, 0, 0, 0.18);
  cursor: pointer;
  transition: transform 0.12s ease, border-color 0.12s ease;
}

.session-card:hover {
  transform: translateY(-1px);
}

.session-card.selected {
  border-color: rgba(126, 199, 255, 0.9);
  box-shadow: 0 0 0 1px rgba(128, 193, 255, 0.42), 0 8px 18px rgba(0, 0, 0, 0.18);
}

.session-card.active {
  border-color: rgba(141, 238, 182, 0.82);
}

.session-card.state-idle {
  border-color: rgba(190, 205, 219, 0.32);
}

.session-card.state-thinking {
  border-color: rgba(255, 218, 136, 0.52);
}

.session-card.state-tooling {
  border-color: rgba(168, 199, 255, 0.5);
}

.session-card.state-responding {
  border-color: rgba(136, 237, 227, 0.58);
}

.session-card.state-waiting {
  border-color: rgba(255, 153, 153, 0.58);
}

.session-card-top {
  display: flex;
  align-items: center;
  gap: 6px;
}

.session-brand-icon {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  flex-shrink: 0;
  object-fit: cover;
  display: block;
}

.session-name {
  font-weight: 700;
  font-size: 14px;
}

.session-state {
  margin-top: 3px;
  font-size: 12px;
  opacity: 0.92;
}

.session-state-muted {
  margin-top: 2px;
  font-size: 11px;
  opacity: 0.7;
}

.session-cwd {
  margin-top: 4px;
  color: rgba(198, 216, 238, 0.82);
  font-size: 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-time {
  margin-top: 2px;
  font-size: 11px;
  opacity: 0.68;
}

.role-settings-panel {
  position: absolute;
  z-index: 11;
  top: 60px;
  left: 16px;
  width: min(560px, calc(100vw - 32px));
  max-height: calc(100vh - 140px);
  overflow: auto;
  border-radius: 14px;
  padding: 12px;
  background: rgba(7, 19, 34, 0.72);
  border: 1px solid rgba(202, 223, 255, 0.28);
  backdrop-filter: blur(8px);
  box-shadow: 0 12px 28px rgba(3, 9, 20, 0.35);
}

.role-settings-panel.collapsed {
  width: 44px;
  height: 44px;
  padding: 6px;
  border-radius: 999px;
  overflow: hidden;
}

.role-settings-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.role-settings-panel.collapsed .role-settings-header {
  justify-content: center;
}

.role-settings-title {
  color: #f0f6ff;
  font-size: 14px;
  font-weight: 700;
}

.role-settings-toggle {
  width: 30px;
  height: 30px;
  border: 1px solid rgba(203, 223, 255, 0.3);
  background: rgba(255, 255, 255, 0.1);
  color: #e7f0ff;
  border-radius: 999px;
  font-size: 16px;
  line-height: 1;
  padding: 0;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.role-settings-toggle:hover {
  background: rgba(255, 255, 255, 0.16);
}

.role-setting-item {
  margin-top: 12px;
}

.role-setting-item label {
  display: block;
  color: #d9e8ff;
  font-size: 12px;
  margin-bottom: 6px;
  font-weight: 600;
}

.role-setting-item input[type='text'] {
  width: 100%;
  border: 1px solid rgba(202, 223, 255, 0.28);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.08);
  color: #f2f6ff;
  font-size: 13px;
  padding: 8px 10px;
}

.role-setting-item input[type='text']::placeholder {
  color: rgba(224, 238, 255, 0.65);
}

.role-setting-item input[type='range'] {
  width: 100%;
}

.role-setting-slider-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
}

.vrm-slot-config-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.vrm-slot-config-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.vrm-slot-config-label {
  width: 48px;
  flex-shrink: 0;
  font-size: 11px;
  color: rgba(230, 240, 255, 0.78);
}

.vrm-slot-config-select {
  flex: 1;
  border: 1px solid rgba(202, 223, 255, 0.28);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.08);
  color: #f2f6ff;
  font-size: 12px;
  padding: 6px 8px;
}

.role-setting-slider-value {
  font-size: 12px;
  font-weight: 700;
  color: #fff1c7;
  min-width: 68px;
  text-align: right;
}

.role-setting-header {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
}

.models-root {
  margin-left: 8px;
  color: #b6c8df;
  font-size: 11px;
  font-weight: 500;
}

.models-status {
  color: #d1e1f9;
  font-size: 12px;
}

.models-status.error {
  color: #ffb7b7;
}

.role-model-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
}

.role-model-card {
  border: 1px solid rgba(201, 223, 255, 0.26);
  border-radius: 10px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.08);
  cursor: pointer;
}

.role-model-card.selected {
  border-color: rgba(132, 197, 255, 0.9);
  box-shadow: 0 0 0 2px rgba(128, 193, 255, 0.2);
}

.role-model-preview {
  height: 106px;
  background: rgba(8, 20, 35, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
}

.role-model-preview img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.role-model-preview-fallback {
  color: #9ab0ce;
  font-size: 12px;
}

.role-model-info {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 8px;
}

.role-model-meta {
  min-width: 0;
}

.role-model-name {
  color: #f0f6ff;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.2;
}

.role-model-path {
  margin-top: 4px;
  color: #bdd0ea;
  font-size: 10px;
  line-height: 1.25;
  word-break: break-all;
}

.role-setting-behavior-flow-btn {
  width: 100%;
  border: 1px solid rgba(255, 220, 124, 0.4);
  border-radius: 10px;
  background: rgba(49, 33, 8, 0.5);
  color: #ffe8a0;
  font-size: 12px;
  font-weight: 700;
  padding: 8px 10px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}

.role-setting-behavior-flow-btn:hover {
  background: rgba(49, 33, 8, 0.76);
  border-color: rgba(255, 220, 124, 0.65);
}

.role-setting-note {
  margin-top: 12px;
  color: rgba(205, 222, 244, 0.86);
  font-size: 12px;
  line-height: 1.5;
}

.chat-dock {
  position: absolute;
  z-index: 12;
  top: 84px;
  right: 16px;
  bottom: 16px;
  width: min(760px, calc(100vw - 420px));
  min-width: 380px;
  max-height: none;
  min-height: 380px;
  border-radius: 14px;
  border: 1px solid rgba(186, 216, 255, 0.28);
  background: rgba(9, 23, 41, 0.58);
  box-shadow: 0 14px 30px rgba(2, 10, 21, 0.44);
  backdrop-filter: blur(12px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-dock-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px solid rgba(192, 219, 255, 0.2);
}

.chat-dock-title {
  color: #eef6ff;
  font-size: 14px;
  font-weight: 700;
  white-space: nowrap;
  max-width: 34%;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat-dock-path {
  flex: 1;
  color: rgba(207, 224, 245, 0.86);
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat-dock-close {
  border-radius: 999px;
  border: 1px solid rgba(188, 216, 252, 0.35);
  background: rgba(17, 44, 72, 0.82);
  color: #ecf5ff;
  font-size: 12px;
  padding: 4px 10px;
  cursor: pointer;
}

.chat-dock-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

@media (max-width: 920px) {
  .stage-header {
    flex-wrap: wrap;
    max-width: calc(100vw - 24px);
  }

  .role-settings-panel {
    top: 96px;
    left: 12px;
    right: 12px;
    width: auto;
    max-height: 38vh;
  }

  .session-sidebar {
    top: auto;
    bottom: 12px;
    right: 12px;
    left: 12px;
    width: auto;
    max-height: 30vh;
    display: flex;
  }

  .session-sidebar.focus-chat {
    display: none;
  }

  .session-sidebar.with-chat {
    bottom: calc(52vh + 20px);
    max-height: 22vh;
  }

  .chat-dock {
    top: auto;
    width: auto;
    min-width: 0;
    min-height: 320px;
    left: 12px;
    right: 12px;
    bottom: 12px;
    max-height: 52vh;
  }
}
</style>
