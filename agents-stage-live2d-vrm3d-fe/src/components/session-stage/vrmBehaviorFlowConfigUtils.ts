import type { SessionState } from '../../types/sessionState'
import type { BehaviorStepType } from './vrmBehaviorScheduler'

// ─── Flow Config Types ───

/** moveTo 步驟的目標模式 */
export interface FlowMoveTarget {
  mode: 'interactionPoint' | 'coordinate' | 'route' | 'random'
  interactionPointId?: string
  coordinate?: { x: number; z: number }
  /** route 模式：多點路線 */
  waypoints?: Array<{ x: number; z: number }>
  randomRange?: number
}

/** interact 步驟的目標模式 */
export interface FlowInteractTarget {
  mode: 'nearest' | 'specific' | 'byType'
  interactionPointId?: string
  pointType?: string
}

/** 單一步驟的配置 */
export interface BehaviorFlowStep {
  id: string
  type: BehaviorStepType
  label?: string

  // moveTo
  moveTarget?: FlowMoveTarget
  /** 移動時忽略障礙物碰撞（路線模式用） */
  skipObstacles?: boolean

  // interact
  interactTarget?: FlowInteractTarget
  /** 互動動畫覆寫（留空則用互動點預設動畫） */
  interactEnterVrma?: string
  interactLoopVrma?: string
  interactExitVrma?: string
  /** 互動方向覆寫（角度，-180~180；留空用互動點預設） */
  interactRotationYOverride?: number
  /** 互動高度位移校正（世界座標 Y 偏移） */
  interactOffsetY?: number
  /** 互動前後位移校正（世界座標 Z 偏移，不影響高度） */
  interactOffsetZ?: number

  // wait
  waitDuration?: number
  waitRandom?: { min: number; max: number }

  // playMotion
  motionFile?: string
  motionLoop?: 'once' | 'repeat'
  motionDuration?: number
  motionOffset?: { x: number; y: number; z: number }
  /** playMotion 朝向覆寫（角度，-180~180；留空維持目前方向） */
  motionRotationY?: number

  // roam: 無額外參數
}

/** 行為流觸發條件 */
export interface BehaviorFlowCondition {
  requireInteractionPoint?: string
  requireNoRoute?: boolean
  requireRoute?: boolean
}

/** 單一行為流 */
export interface BehaviorFlow {
  id: string
  name: string
  triggerStates: SessionState[]
  priority: number
  probability?: number
  condition?: BehaviorFlowCondition
  steps: BehaviorFlowStep[]
  onComplete: 'loop' | 'roam' | 'idle'
  interruptOnStateChange: boolean
  /** 指定角色 slot (0~3)，null 表示適用於所有角色 */
  actorSlot?: number | null
}

/** 每個角色 slot 的 state→flow 指派表 */
export interface ActorSlotAssignment {
  [state: string]: string | null  // SessionState → flowId or null (use default/fallback)
}

/** 完整配置 */
export interface BehaviorFlowConfig {
  version: number
  flows: BehaviorFlow[]
  /** 4 個角色 slot 的指派表（可選；若無則用 flow 上的 actorSlot 欄位過濾） */
  slotAssignments?: Record<number, ActorSlotAssignment>
}

// ─── Constants ───

const STORAGE_KEY = 'vrm-stage-behavior-flows-v3'
const CONFIG_VERSION = 3
export const MAX_ACTOR_SLOTS = 4

/** 可用的 VRMA 動畫檔案（動態載入，fallback 硬編碼） */
const FALLBACK_VRMA_FILES = [
  'Walking.vrma', 'Thinking.vrma', 'SittingDrinking.vrma', 'Sitting.vrma',
  'Jump.vrma', 'LookAround.vrma', 'Relax.vrma', 'Sleepy.vrma', 'Blush.vrma',
  'Angry.vrma', 'Clapping.vrma', 'Goodbye.vrma', 'Sad.vrma', 'Surprised.vrma',
]

let _vrmaFiles: string[] | null = null
let _vrmaFetchPromise: Promise<string[]> | null = null

const VRMA_MANIFEST_URL = '/vrm3d/vrm-viewer-main/VRMA/manifest.json'

/** 取得可用的 VRMA 檔案清單（從 manifest.json 動態載入） */
export async function fetchAvailableVrmaFiles(): Promise<string[]> {
  if (_vrmaFiles) return _vrmaFiles
  if (_vrmaFetchPromise) return _vrmaFetchPromise
  _vrmaFetchPromise = fetch(VRMA_MANIFEST_URL)
    .then((res) => res.ok ? res.json() : FALLBACK_VRMA_FILES)
    .then((list: string[]) => {
      _vrmaFiles = Array.isArray(list) && list.length > 0 ? list.sort() : FALLBACK_VRMA_FILES
      return _vrmaFiles
    })
    .catch(() => {
      _vrmaFiles = FALLBACK_VRMA_FILES
      return _vrmaFiles
    })
  return _vrmaFetchPromise
}

/** 同步取得（若尚未載入則回傳 fallback） */
export function getAvailableVrmaFiles(): string[] {
  return _vrmaFiles ?? FALLBACK_VRMA_FILES
}

/** 相容舊的 export 名稱 */
export const AVAILABLE_VRMA_FILES = FALLBACK_VRMA_FILES

/** 所有 SessionState */
export const ALL_SESSION_STATES: SessionState[] = ['IDLE', 'THINKING', 'TOOLING', 'RESPONDING', 'WAITING']

/** 角色 slot 標籤 */
export const SLOT_LABELS = ['角色 1', '角色 2', '角色 3', '角色 4']

export function normalizeTriggerStates(triggerStates: unknown): SessionState[] {
  if (!Array.isArray(triggerStates)) return []
  const normalized = triggerStates.filter((state): state is SessionState => {
    return ALL_SESSION_STATES.includes(state as SessionState)
  })
  return Array.from(new Set(normalized))
}

export function getFlowTriggerStates(flow: Pick<BehaviorFlow, 'triggerStates'>): SessionState[] {
  return normalizeTriggerStates(flow.triggerStates)
}

// ─── Default Config ───

let _cachedStepId = 0
function genStepId(): string {
  return `fs-${Date.now().toString(36)}-${(++_cachedStepId).toString(36)}`
}

export function generateStepId(): string {
  return genStepId()
}

export function generateFlowId(): string {
  return `flow-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`
}

function getDefaultConfig(): BehaviorFlowConfig {
  return {
    version: CONFIG_VERSION,
    flows: [
      {
        id: 'default-idle-roam',
        name: '閒置漫步',
        triggerStates: ['IDLE'],
        priority: 10,
        actorSlot: null,
        condition: { requireNoRoute: true },
        steps: [{ id: genStepId(), type: 'roam' }],
        onComplete: 'roam',
        interruptOnStateChange: true,
      },
      {
        id: 'default-idle-sit',
        name: '閒置坐下',
        triggerStates: ['IDLE'],
        priority: 10,
        actorSlot: null,
        condition: { requireNoRoute: true, requireInteractionPoint: 'sit' },
        steps: [
          {
            id: genStepId(),
            type: 'interact',
            interactTarget: { mode: 'nearest', pointType: 'sit' },
          },
        ],
        onComplete: 'roam',
        interruptOnStateChange: true,
      },
      {
        id: 'default-thinking-sit',
        name: '思考時找椅子坐',
        triggerStates: ['THINKING'],
        priority: 1,
        actorSlot: null,
        condition: { requireNoRoute: true, requireInteractionPoint: 'sit' },
        steps: [
          {
            id: genStepId(),
            type: 'interact',
            interactTarget: { mode: 'nearest', pointType: 'sit' },
          },
        ],
        onComplete: 'roam',
        interruptOnStateChange: true,
      },
      {
        id: 'default-thinking-stand',
        name: '思考時站著（無椅子）',
        triggerStates: ['THINKING'],
        priority: 2,
        actorSlot: null,
        condition: { requireNoRoute: true },
        steps: [
          {
            id: genStepId(),
            type: 'playMotion',
            motionFile: 'Thinking.vrma',
            motionLoop: 'repeat',
          },
        ],
        onComplete: 'roam',
        interruptOnStateChange: true,
      },
      {
        id: 'default-tooling-work',
        name: '工具時找桌子',
        triggerStates: ['TOOLING'],
        priority: 1,
        actorSlot: null,
        condition: { requireNoRoute: true, requireInteractionPoint: 'work' },
        steps: [
          {
            id: genStepId(),
            type: 'interact',
            interactTarget: { mode: 'nearest', pointType: 'work' },
          },
        ],
        onComplete: 'roam',
        interruptOnStateChange: true,
      },
      {
        id: 'default-tooling-stand',
        name: '工具時站著（無桌子）',
        triggerStates: ['TOOLING'],
        priority: 2,
        actorSlot: null,
        condition: { requireNoRoute: true },
        steps: [
          {
            id: genStepId(),
            type: 'playMotion',
            motionFile: 'Thinking.vrma',
            motionLoop: 'repeat',
          },
        ],
        onComplete: 'roam',
        interruptOnStateChange: true,
      },
      {
        id: 'default-responding-roam',
        name: '回應時站起來漫步',
        triggerStates: ['RESPONDING'],
        priority: 1,
        actorSlot: null,
        condition: { requireNoRoute: true },
        steps: [{ id: genStepId(), type: 'roam' }],
        onComplete: 'roam',
        interruptOnStateChange: true,
      },
      {
        id: 'default-waiting-sit',
        name: '等待時坐下',
        triggerStates: ['WAITING'],
        priority: 1,
        actorSlot: null,
        condition: { requireNoRoute: true, requireInteractionPoint: 'sit' },
        steps: [
          {
            id: genStepId(),
            type: 'interact',
            interactTarget: { mode: 'nearest', pointType: 'sit' },
          },
        ],
        onComplete: 'roam',
        interruptOnStateChange: true,
      },
      {
        id: 'default-waiting-roam',
        name: '等待時漫步（無椅子）',
        triggerStates: ['WAITING'],
        priority: 2,
        actorSlot: null,
        condition: { requireNoRoute: true },
        steps: [{ id: genStepId(), type: 'roam' }],
        onComplete: 'roam',
        interruptOnStateChange: true,
      },
    ],
    slotAssignments: {},
  }
}

// ─── Validation ───

const VALID_STEP_TYPES: BehaviorStepType[] = ['moveTo', 'interact', 'wait', 'playMotion', 'roam']
const VALID_ON_COMPLETE = ['loop', 'roam', 'idle']

function validateConfig(config: unknown): { valid: boolean; errors: string[] } {
  const errors: string[] = []
  if (!config || typeof config !== 'object') {
    return { valid: false, errors: ['配置不是有效物件'] }
  }

  const cfg = config as Record<string, unknown>
  if (typeof cfg.version !== 'number') {
    errors.push('缺少 version 欄位')
  }
  if (!Array.isArray(cfg.flows)) {
    return { valid: false, errors: [...errors, 'flows 不是陣列'] }
  }

  for (let i = 0; i < cfg.flows.length; i++) {
    const flow = cfg.flows[i] as Record<string, unknown>
    const prefix = `flows[${i}]`

    if (!flow.id || typeof flow.id !== 'string') errors.push(`${prefix}: 缺少 id`)
    if (!flow.name || typeof flow.name !== 'string') errors.push(`${prefix}: 缺少 name`)
    const triggerStates = normalizeTriggerStates(flow.triggerStates)
    if (triggerStates.length === 0) {
      errors.push(`${prefix}: triggerStates 至少需要一個有效狀態`)
    }
    if (typeof flow.priority !== 'number') errors.push(`${prefix}: 缺少 priority`)
    if (!VALID_ON_COMPLETE.includes(flow.onComplete as string)) {
      errors.push(`${prefix}: onComplete 無效 (${String(flow.onComplete)})`)
    }
    if (!Array.isArray(flow.steps)) {
      errors.push(`${prefix}: steps 不是陣列`)
      continue
    }
    if (flow.steps.length === 0) {
      errors.push(`${prefix}: steps 為空`)
    }

    for (let j = 0; j < flow.steps.length; j++) {
      const step = flow.steps[j] as Record<string, unknown>
      const sp = `${prefix}.steps[${j}]`
      if (!VALID_STEP_TYPES.includes(step.type as BehaviorStepType)) {
        errors.push(`${sp}: type 無效 (${String(step.type)})`)
      }
    }
  }

  return { valid: errors.length === 0, errors }
}

// ─── Migration ───

/** 將舊版單一 triggerState 遷移為多選 triggerStates，並補上 actorSlot */
function normalizeLegacyFlow(flow: Record<string, unknown>): BehaviorFlow {
  const triggerStates = normalizeTriggerStates(flow.triggerStates)
  const legacyTriggerState = flow.triggerState
  const migratedTriggerStates = triggerStates.length > 0
    ? triggerStates
    : ALL_SESSION_STATES.includes(legacyTriggerState as SessionState)
      ? [legacyTriggerState as SessionState]
      : ['IDLE']

  return {
    ...(flow as unknown as BehaviorFlow),
    triggerStates: migratedTriggerStates,
    actorSlot: flow.actorSlot === undefined ? null : (flow.actorSlot as number | null),
  }
}

function migrateLegacyConfig(config: Record<string, unknown>): BehaviorFlowConfig {
  const flows = (config.flows as BehaviorFlow[]) || []
  const normalizedFlows = flows.map((flow) => normalizeLegacyFlow(flow as unknown as Record<string, unknown>))
  return {
    version: CONFIG_VERSION,
    flows: normalizedFlows,
    slotAssignments: {},
  }
}

// ─── Factory ───

export interface BehaviorFlowConfigContext {
  hasRoute: boolean
  availablePointTypes: string[]
  /** 當前角色的 slot index (0~3) */
  actorSlot?: number
}

export function createVrmBehaviorFlowConfigUtils() {
  let currentConfig: BehaviorFlowConfig | null = null
  let previewConfig: BehaviorFlowConfig | null = null

  function loadConfig(): BehaviorFlowConfig | null {
    try {
      // 嘗試 v3
      let raw = localStorage.getItem(STORAGE_KEY)
      if (!raw) {
        for (const legacyKey of ['vrm-stage-behavior-flows-v2', 'vrm-stage-behavior-flows-v1']) {
          raw = localStorage.getItem(legacyKey)
          if (raw) {
            const parsed = JSON.parse(raw)
            if (parsed && Array.isArray(parsed.flows)) {
              const migrated = migrateLegacyConfig(parsed)
              currentConfig = migrated
              localStorage.setItem(STORAGE_KEY, JSON.stringify(migrated))
              localStorage.removeItem(legacyKey)
              return currentConfig
            }
          }
        }
        return null
      }
      const parsed = JSON.parse(raw)
      const result = validateConfig(parsed)
      if (!result.valid) {
        console.warn('行為流配置格式錯誤:', result.errors)
        return null
      }
      // 處理舊版本遷移
      if (parsed.version !== CONFIG_VERSION) {
        const migrated = migrateLegacyConfig(parsed)
        currentConfig = migrated
        localStorage.setItem(STORAGE_KEY, JSON.stringify(migrated))
        return currentConfig
      }
      currentConfig = parsed as BehaviorFlowConfig
      return currentConfig
    } catch (error) {
      console.warn('讀取行為流配置失敗:', error)
      return null
    }
  }

  function saveConfig(config: BehaviorFlowConfig): void {
    try {
      const result = validateConfig(config)
      if (!result.valid) {
        console.warn('儲存行為流配置格式錯誤:', result.errors)
        return
      }
      config.version = CONFIG_VERSION
      currentConfig = config
      previewConfig = null
      localStorage.setItem(STORAGE_KEY, JSON.stringify(config))
    } catch (error) {
      console.warn('儲存行為流配置失敗:', error)
    }
  }

  function getPersistedConfig(): BehaviorFlowConfig {
    if (currentConfig) return currentConfig
    const loaded = loadConfig()
    if (loaded) return loaded
    return getDefaultConfig()
  }

  function getConfig(): BehaviorFlowConfig {
    return previewConfig ?? getPersistedConfig()
  }

  function applyPreviewConfig(config: BehaviorFlowConfig): void {
    const result = validateConfig(config)
    if (!result.valid) {
      console.warn('預覽行為流配置格式錯誤:', result.errors)
      return
    }
    config.version = CONFIG_VERSION
    previewConfig = config
  }

  function discardPreviewConfig(): void {
    previewConfig = null
  }

  function resetToDefault(): void {
    currentConfig = null
    previewConfig = null
    localStorage.removeItem(STORAGE_KEY)
    localStorage.removeItem('vrm-stage-behavior-flows-v2')
    localStorage.removeItem('vrm-stage-behavior-flows-v1')
  }

  function exportConfig(): string {
    return JSON.stringify(getConfig(), null, 2)
  }

  function importConfig(json: string): { success: boolean; error?: string } {
    try {
      const parsed = JSON.parse(json) as Record<string, unknown>
      const configToImport = (
        parsed.version !== CONFIG_VERSION
        || (Array.isArray(parsed.flows) && parsed.flows.some((flow) => {
          return flow && typeof flow === 'object' && 'triggerState' in (flow as Record<string, unknown>)
        }))
      )
        ? migrateLegacyConfig(parsed)
        : parsed

      const result = validateConfig(configToImport)
      if (!result.valid) {
        return { success: false, error: result.errors.join('; ') }
      }
      saveConfig(configToImport as BehaviorFlowConfig)
      return { success: true }
    } catch (error) {
      return { success: false, error: String((error as Error)?.message || error) }
    }
  }

  /**
   * 取得指定 state 和 actorSlot 匹配的行為流。
   * 優先順序：
   * 1. slotAssignments 指派表中指定的 flowId
   * 2. flow.actorSlot === 當前 slot 的流程
   * 3. flow.actorSlot === null (通用) 的流程
   */
  function getFlowsForState(
    state: SessionState,
    ctx: BehaviorFlowConfigContext,
  ): BehaviorFlow[] {
    const config = getConfig()
    const slot = ctx.actorSlot

    // 1. 檢查 slotAssignments 指派表
    if (slot !== undefined && config.slotAssignments?.[slot]) {
      const assignment = config.slotAssignments[slot]
      const flowId = assignment[state]
      if (flowId) {
        const flow = config.flows.find((f) => f.id === flowId)
        if (flow && evaluateCondition(flow.condition, ctx)) {
          return [flow]
        }
      }
    }

    // 2. 按 actorSlot 和 priority 篩選
    return config.flows
      .filter((f) => getFlowTriggerStates(f).includes(state))
      .filter((f) => {
        // 精確 slot 匹配或通用（null）
        if (f.actorSlot !== null && f.actorSlot !== undefined && f.actorSlot !== slot) return false
        return true
      })
      .filter((f) => evaluateCondition(f.condition, ctx))
      .sort((a, b) => {
        // 精確 slot 匹配的排在通用前面
        const aSpecific = (a.actorSlot !== null && a.actorSlot !== undefined) ? 0 : 1
        const bSpecific = (b.actorSlot !== null && b.actorSlot !== undefined) ? 0 : 1
        if (aSpecific !== bSpecific) return aSpecific - bSpecific
        return a.priority - b.priority
      })
  }

  function evaluateCondition(
    condition: BehaviorFlowCondition | undefined,
    ctx: BehaviorFlowConfigContext,
  ): boolean {
    if (!condition) return true
    if (condition.requireRoute && !ctx.hasRoute) return false
    if (condition.requireNoRoute && ctx.hasRoute) return false
    if (condition.requireInteractionPoint) {
      if (!ctx.availablePointTypes.includes(condition.requireInteractionPoint)) return false
    }
    return true
  }

  return {
    loadConfig,
    saveConfig,
    getConfig,
    getPersistedConfig,
    applyPreviewConfig,
    discardPreviewConfig,
    getDefaultConfig,
    resetToDefault,
    validateConfig,
    exportConfig,
    importConfig,
    getFlowsForState,
  }
}
