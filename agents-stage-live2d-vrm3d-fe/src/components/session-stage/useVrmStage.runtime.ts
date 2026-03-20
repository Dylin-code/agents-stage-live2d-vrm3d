import { onMounted, onUnmounted, ref, watch, type Ref } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { TransformControls } from 'three/examples/jsm/controls/TransformControls.js'
import { CSS2DObject, CSS2DRenderer } from 'three/examples/jsm/renderers/CSS2DRenderer.js'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm'
import { VRMAnimationLoaderPlugin, createVRMAnimationClip, type VRMAnimation } from '@pixiv/three-vrm-animation'

import type { SessionRuntimeContext, SessionSnapshotItem, SessionState } from '../../types/sessionState'
import { getContextPercentLabel as getContextPercentLabelFromContext } from '../../utils/session-stage/contextWindow'
import { stateText as getSessionStateText } from '../../utils/session-stage/stateText'
import { createVrmActorMovementUtils } from './vrmActorMovementUtils'
import { findOldestActor } from './vrmActorSelectionUtils'
import { createVrmBehaviorScheduler, type ActorBehavior } from './vrmBehaviorScheduler'
import { decideBehaviorSteps, getBehaviorFlowConfigUtils } from './vrmBehaviorDecisionUtils'
import type { BehaviorFlow } from './vrmBehaviorFlowConfigUtils'
import { resolveFlowSteps } from './vrmBehaviorDecisionUtils'
import { createVrmHeadLabelUtils } from './vrmHeadLabelUtils'
import { createVrmInteractionHandlers } from './vrmInteractionHandlers'
import { createVrmInteractionPointEditorUtils } from './vrmInteractionPointEditorUtils'
import { createVrmInteractionPointUtils, type InteractionPointData } from './vrmInteractionPointUtils'
import {
  VRM_INTERACTION_POINTS_RELOAD_EVENT,
  buildInteractionPointsStorageKey,
} from './vrmInteractionPointEvents'
import { createVrmRouteCameraUtils } from './vrmRouteCameraUtils'
import { createVrmSceneSetupUtils } from './vrmSceneSetupUtils'
import {
  VRM_ACTOR_SCALE_DEFAULT,
  VRM_ACTOR_SCALE_EVENT,
  loadVrmActorScale,
  saveVrmActorScale,
} from './vrmActorScaleSettings'
import {
  VRM_GLOBAL_GROUND_OFFSET_EVENT,
  loadVrmGlobalGroundOffset,
  saveVrmGlobalGroundOffset,
} from './vrmGroundOffsetSettings'
import {
  DEFAULT_VRM_ACTOR_SLOT_OPTIONS,
  VRM_ACTOR_SLOT_CONFIG_EVENT,
  loadVrmActorSlotConfig,
  normalizeVrmActorSlotConfig,
} from './vrmActorSlotSettings'

interface UseVrmStageOptions {
  containerRef: Ref<HTMLElement | null>
  visibleSessions: Ref<SessionSnapshotItem[]>
  selectedChatSessionId: Ref<string>
  onCharacterClick: (sessionId: string) => void
}

interface VrmActor {
  sessionId: string
  stageSlot: number
  displayName: string
  state: SessionState
  context: SessionRuntimeContext | null
  modelUrl: string
  mountedOrder: number
  vrm: any
  mixer: THREE.AnimationMixer
  root: THREE.Group
  targetPosition: THREE.Vector3
  currentAction: THREE.AnimationAction | null
  currentMotionFile: string
  modelBaseLocalPosition: THREE.Vector3
  hipsBone: THREE.Object3D | null
  hipsBaseLocalPosition: THREE.Vector3 | null
  seatIndex: number
  roamTarget: THREE.Vector3
  roamSpeed: number
  agentBrand: string
  labelRoot: HTMLDivElement | null
  labelTitleEl: HTMLDivElement | null
  labelStateEl: HTMLDivElement | null
  labelContextEl: HTMLDivElement | null
  labelBrandEl: HTMLDivElement | null
  labelAnchor: THREE.Object3D | null
  labelObject: CSS2DObject | null
  jumpFinishHandler: ((event: any) => void) | null
  jumpLocked: boolean
  jumpStartedAt: number
  jumpDuration: number
  jumpResumeBehavior: ActorBehavior | null
  routePointIndex: number
  groundOffsetY: number
  behavior: ActorBehavior | null
  occupyingPointId: string | null
}

interface StageObstacle {
  minX: number
  maxX: number
  minZ: number
  maxZ: number
}

type InteractionTransformTarget = 'position' | 'approach' | 'rotate'

const MAX_ACTORS = 4
const DEFAULT_VRM_URL = '/vrm3d/AliciaSolid.vrm'
const SPECIAL_VRM_URL = '/vrm3d/ふらすこ式風きりたん_VRM_1_0_1.vrm'
const HATSUNE_MIKU_VRM_URL = '/vrm3d/HatsuneMikuNT.vrm'
const AVATAR_L_VRM_URL = '/vrm3d/avatar_L.vrm'
const STAGE_FIXED_VRM_URLS = [DEFAULT_VRM_URL, SPECIAL_VRM_URL, HATSUNE_MIKU_VRM_URL, AVATAR_L_VRM_URL]
const VRMA_BASE_PATH = '/vrm3d/vrm-viewer-main/VRMA/'
const STAGE_SCENE_URL = '/vrm3d/scenes/mirrors_edge_apartment.glb'
const TARGET_STAGE_MAX_SIZE = 14
const ROAM_MOVE_VRMA_FILE = 'Walking.vrma'
const ROAM_PAUSE_VRMA_FILES = ['LookAround.vrma', 'Relax.vrma', 'Sleepy.vrma', 'Blush.vrma'] as const
const JUMP_VRMA_FILE = 'Jump.vrma'
const ROAM_MOVE_ANIMATION_TIME_SCALE = 0.55
const JUMP_ANIMATION_TIME_SCALE = 1
const JUMP_POST_HOLD_MS = 800
const JUMP_VISUAL_HEIGHT = 0.28
const ROAM_SPEED_MIN = 0.2
const ROAM_SPEED_MAX = 0.8
const ROAM_STOP_MIN = 2
const ROAM_STOP_MAX = 5
const ROAM_REACH_DISTANCE = 0.16
const ROAM_TURN_DAMPING = 7
const MAX_DEVICE_PIXEL_RATIO = 1.25
const TARGET_FPS = 30
const FRAME_INTERVAL = 1 / TARGET_FPS
const SHADOW_MAP_SIZE = 1024
const ACTOR_COLLISION_RADIUS = 0.16
const OBSTACLE_COLLISION_PADDING = 0.01
const CENTER_BLOCK_X_RATIO = 0.32
const CENTER_BLOCK_Z_RATIO = 0.28
const ROUTE_STORAGE_KEY = 'vrm-stage-custom-route-v1'
const ROUTE_POINT_REACH_DISTANCE = 0.22
const CAMERA_VIEW_STORAGE_KEY = 'vrm-stage-camera-view-v1'
const CAMERA_VIEW_SAVE_DEBOUNCE_MS = 120
const INTERACTION_POINTS_STORAGE_KEY = buildInteractionPointsStorageKey(STAGE_SCENE_URL)
const VRM_ACTOR_SLOT_OPTIONS = DEFAULT_VRM_ACTOR_SLOT_OPTIONS

export function resolveActorTargetY(
  inputY: number,
  actorGroundOffset: number,
  globalGroundOffset: number,
  preserveInputY = false,
): number {
  return preserveInputY ? inputY : actorGroundOffset + globalGroundOffset
}

export function useVrmStage(options: UseVrmStageOptions) {
  const loadingText = ref('初始化 3D 舞台...')
  const routeEditorEnabled = ref(false)
  const activeRouteModelIndex = ref(0)
  const routeEditorHintText = ref('')
  const globalActorScale = ref(VRM_ACTOR_SCALE_DEFAULT)
  const globalGroundOffset = ref(0)
  const interactionEditorEnabled = ref(false)
  const selectedInteractionPointId = ref<string | null>(null)
  const interactionDraftPoint = ref<InteractionPointData | null>(null)
  const interactionDraftSnapshot = ref<string | null>(null)
  const interactionTransformTarget = ref<InteractionTransformTarget>('position')
  const actorSlotModelConfig = ref<string[]>(loadVrmActorSlotConfig(VRM_ACTOR_SLOT_OPTIONS))

  let scene: THREE.Scene | null = null
  let camera: THREE.PerspectiveCamera | null = null
  let renderer: THREE.WebGLRenderer | null = null
  let labelRenderer: CSS2DRenderer | null = null
  let controls: OrbitControls | null = null
  let loader: GLTFLoader | null = null
  let interactionTransformControls: TransformControls | null = null
  let interactionTransformProxy: THREE.Object3D | null = null
  let interactionDraftPreviewTimer: number | null = null
  let animationFrameId: number | null = null
  let cameraViewSaveTimer: number | null = null
  let frameAccumulator = 0
  let destroyed = false
  let stageSceneRoot: THREE.Object3D | null = null
  let stageSeatCenter: THREE.Vector3 | null = null
  let stageSeatSpread = { x: 1.1, z: 0.7 }
  let stageCameraTarget: THREE.Vector3 | null = null
  let stageCameraPosition: THREE.Vector3 | null = null
  let stageWorldCenter: THREE.Vector3 | null = null
  let stageWorldSize: THREE.Vector3 | null = null
  const sceneLights: THREE.Light[] = []
  const sceneLightTargets: THREE.Object3D[] = []
  const stageObstacles: StageObstacle[] = []
  const routePointsByModel = new Map<string, Array<{ x: number; z: number }>>()
  let routeLine: THREE.Line | null = null
  const routeMarkers: THREE.Mesh[] = []

  const clock = new THREE.Clock()
  const raycaster = new THREE.Raycaster()
  const pointer = new THREE.Vector2()
  const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0)
  const rayHitPoint = new THREE.Vector3()
  const roamDirection = new THREE.Vector3()
  const roamCandidatePosition = new THREE.Vector3()
  const roamTargetQuaternion = new THREE.Quaternion()
  const roamTargetEuler = new THREE.Euler()

  const actors: VrmActor[] = []
  /** 響應式版本號 — 每次 actors 增刪時遞增，讓 Vue computed 可追蹤 */
  const actorsVersion = ref(0)
  const actorSeatIndexes = new Map<string, number>()
  const vrmaAnimationCache = new Map<string, VRMAnimation>()
  let actorMountSequence = 0

  function stateText(state: SessionState): string {
    const normalized = String(state || 'IDLE').toUpperCase() as SessionState
    return getSessionStateText(normalized) || normalized
  }

  function setGlobalGroundOffset(value: number): void {
    globalGroundOffset.value = saveVrmGlobalGroundOffset(value)
  }

  function setGlobalActorScale(value: number): void {
    globalActorScale.value = saveVrmActorScale(value)
  }

  function handleGlobalGroundOffsetEvent(event: Event): void {
    const customEvent = event as CustomEvent<{ value?: unknown }>
    const next = Number(customEvent.detail?.value)
    if (!Number.isFinite(next)) return
    globalGroundOffset.value = saveVrmGlobalGroundOffset(next)
  }

  function handleGlobalActorScaleEvent(event: Event): void {
    const customEvent = event as CustomEvent<{ value?: unknown }>
    const next = Number(customEvent.detail?.value)
    if (!Number.isFinite(next)) return
    globalActorScale.value = saveVrmActorScale(next)
  }

  function handleActorSlotConfigEvent(event: Event): void {
    const customEvent = event as CustomEvent<{ value?: unknown }>
    actorSlotModelConfig.value = normalizeVrmActorSlotConfig(customEvent.detail?.value, VRM_ACTOR_SLOT_OPTIONS)
    void syncActorsToConfiguredModels()
  }

  function getConfiguredModelUrlForSlot(slot: number): string {
    return actorSlotModelConfig.value[slot]
      || VRM_ACTOR_SLOT_OPTIONS[slot]?.modelUrl
      || VRM_ACTOR_SLOT_OPTIONS[0]?.modelUrl
      || STAGE_FIXED_VRM_URLS[slot]
      || STAGE_FIXED_VRM_URLS[0]
      || ''
  }

  function computeGroundOffsetY(vrm: any): number {
    const box = new THREE.Box3().setFromObject(vrm.scene)
    const minY = box.min.y
    if (!Number.isFinite(minY)) return 0
    return -minY
  }

  function setActorTargetPosition(
    actor: VrmActor,
    position: THREE.Vector3,
    syncRoot = false,
    preserveInputY = false,
  ): void {
    actor.targetPosition.copy(position)
    actor.targetPosition.y = resolveActorTargetY(
      actor.targetPosition.y,
      actor.groundOffsetY,
      globalGroundOffset.value,
      preserveInputY,
    )
    if (syncRoot) {
      actor.root.position.copy(actor.targetPosition)
    }
  }

  function setActorRoamTarget(actor: VrmActor, position: THREE.Vector3): void {
    actor.roamTarget.copy(position)
    actor.roamTarget.y = actor.groundOffsetY + globalGroundOffset.value
  }

  function applyActorScale(actor: VrmActor, scale: number): void {
    actor.vrm.scene.scale.setScalar(scale)
    actor.groundOffsetY = computeGroundOffsetY(actor.vrm)
    actor.targetPosition.y = actor.groundOffsetY + globalGroundOffset.value
    actor.roamTarget.y = actor.groundOffsetY + globalGroundOffset.value
    actor.root.position.y = actor.targetPosition.y
  }

  const {
    getSeatPositions,
    randomRange,
    isWalkablePosition,
    resolveToWalkablePosition,
    pickRoamTarget,
    lockActorRootMotion,
    getJumpLift,
    faceActorTowardDirection,
  } = createVrmActorMovementUtils({
    getStageSeatCenter: () => stageSeatCenter,
    getStageWorldCenter: () => stageWorldCenter,
    getStageWorldSize: () => stageWorldSize,
    getStageObstacles: () => stageObstacles,
    getActors: () => actors,
    actorSeatIndexes,
    getSelectedSessionId: () => options.selectedChatSessionId.value,
    getCenterBlockRatios: () => ({ xRatio: CENTER_BLOCK_X_RATIO, zRatio: CENTER_BLOCK_Z_RATIO }),
    actorCollisionRadius: ACTOR_COLLISION_RADIUS,
    roamTurnDamping: ROAM_TURN_DAMPING,
    jumpVisualHeight: JUMP_VISUAL_HEIGHT,
    roamTargetQuaternion,
    roamTargetEuler,
    getApproachWhitelist: () => interactionPointUtils.getInteractionPoints().map((p) => p.approachPosition),
  })

  function setLoadingText(text: string): void {
    loadingText.value = text
  }

  function hasCustomRoute(modelUrl?: string): boolean {
    if (modelUrl) {
      return getRoutePointsByModel(modelUrl).length >= 2
    }
    for (const routePoints of routePointsByModel.values()) {
      if (routePoints.length >= 2) return true
    }
    return false
  }

  const {
    saveCameraViewToStorage,
    scheduleSaveCameraView,
    loadCameraViewFromStorage,
    getActiveRouteModelUrl,
    getRoutePointsByModel,
    getActiveRoutePointCount,
    getModelRouteLabel,
    updateRouteEditorHintText,
    saveRouteToStorage,
    loadRouteFromStorage,
    clearRouteVisuals,
    refreshRouteVisuals,
    getActorRoutePoint,
    addRoutePointFromPointer,
    removeLastRoutePoint,
    clearRoutePoints,
    applyRouteToAllActors,
    toggleRouteEditor,
  } = createVrmRouteCameraUtils({
    routeEditorHintText,
    activeRouteModelIndex,
    routeEditorEnabled,
    routePointsByModel: routePointsByModel as unknown as Map<string, { x: number; z: number }[]>,
    stageFixedVrmUrls: STAGE_FIXED_VRM_URLS,
    routeStorageKey: ROUTE_STORAGE_KEY,
    cameraViewStorageKey: CAMERA_VIEW_STORAGE_KEY,
    cameraViewSaveDebounceMs: CAMERA_VIEW_SAVE_DEBOUNCE_MS,
    getCamera: () => camera,
    getControlsTarget: () => controls?.target || null,
    getScene: () => scene,
    getRenderer: () => renderer,
    getRouteLine: () => routeLine,
    setRouteLine: (line) => {
      routeLine = line
    },
    getRouteMarkers: () => routeMarkers,
    pointer,
    raycaster,
    groundPlane,
    rayHitPoint,
    resolveToWalkablePosition: (desired) => resolveToWalkablePosition(desired),
    assignRouteIndexForActor: (actor) => assignRouteIndexForActor(actor as VrmActor),
    getActors: () => actors as Array<{ modelUrl: string; routePointIndex: number; roamTarget: THREE.Vector3 }>,
    applyAllActorBehaviors,
    setLoadingText,
    hasCustomRoute,
    getCameraViewSaveTimer: () => cameraViewSaveTimer,
    setCameraViewSaveTimer: (timer) => {
      cameraViewSaveTimer = timer
    },
  })

  const { rebuildStageObstacles, loadStageScene, setupLights } = createVrmSceneSetupUtils({
    getScene: () => scene,
    getLoader: () => loader,
    stageSceneUrl: STAGE_SCENE_URL,
    targetStageMaxSize: TARGET_STAGE_MAX_SIZE,
    obstacleCollisionPadding: OBSTACLE_COLLISION_PADDING,
    shadowMapSize: SHADOW_MAP_SIZE,
    getStageSceneRoot: () => stageSceneRoot,
    setStageSceneRoot: (value) => {
      stageSceneRoot = value
    },
    setStageSeatCenter: (value) => {
      stageSeatCenter = value
    },
    setStageSeatSpread: (value) => {
      stageSeatSpread = value
    },
    setStageCameraTarget: (value) => {
      stageCameraTarget = value
    },
    setStageCameraPosition: (value) => {
      stageCameraPosition = value
    },
    setStageWorldCenter: (value) => {
      stageWorldCenter = value
    },
    setStageWorldSize: (value) => {
      stageWorldSize = value
    },
    getStageWorldSize: () => stageWorldSize,
    getStageWorldCenter: () => stageWorldCenter,
    stageObstacles,
    sceneLights,
    sceneLightTargets,
  })

  const interactionPointUtils = createVrmInteractionPointUtils({
    storageKey: INTERACTION_POINTS_STORAGE_KEY,
    getStageWorldCenter: () => stageWorldCenter,
    getStageWorldSize: () => stageWorldSize,
  })

  /** 取得角色的 slot index (0~3) */
  function getActorSlot(actor: VrmActor): number {
    return actor.stageSlot
  }

  /** 取得目前場景中可用的互動點類型（有空位的） */
  function getAvailableInteractionPointTypes(): string[] {
    const types = new Set<string>()
    for (const point of interactionPointUtils.getInteractionPoints()) {
      if (point.occupiedBy.length < point.capacity) {
        types.add(point.action.type)
      }
    }
    return Array.from(types)
  }

  const behaviorScheduler = createVrmBehaviorScheduler({
    pickRoamTarget: (pos, actor) => pickRoamTarget(pos, actor as any),
    resolveToWalkablePosition: (pos, actor) => resolveToWalkablePosition(pos, actor as any),
    isWalkablePosition: (pos, actor) => isWalkablePosition(pos, actor as any),
    faceActorTowardDirection: (actor, dir, delta) => faceActorTowardDirection(actor as any, dir, delta),
    playActorMotion: (actor, file, opts) => playActorMotion(actor as VrmActor, file, opts),
    getPointById: (id) => interactionPointUtils.getPointById(id),
    occupyPoint: (pid, sid) => interactionPointUtils.occupyPoint(pid, sid),
    releasePoint: (pid, sid) => interactionPointUtils.releasePoint(pid, sid),
    setActorTargetPosition: (actor, pos, syncRoot, preserveInputY) =>
      setActorTargetPosition(actor as VrmActor, pos, syncRoot, preserveInputY),
    setActorRoamTarget: (actor, pos) => setActorRoamTarget(actor as VrmActor, pos),
    getRoutePointsByModel,
    getActorRoutePoint,
    roamSpeedRange: { min: ROAM_SPEED_MIN, max: ROAM_SPEED_MAX },
    roamStopRange: { min: ROAM_STOP_MIN, max: ROAM_STOP_MAX },
    moveReachDistance: ROUTE_POINT_REACH_DISTANCE,
    roamMoveVrma: ROAM_MOVE_VRMA_FILE,
    roamPauseVrmaFiles: ROAM_PAUSE_VRMA_FILES,
  })

  const { updateActorHeadLabel, updateAllHeadLabels, setupActorHeadLabel, cleanupActorHeadLabel } = createVrmHeadLabelUtils({
    getActors: () => actors as Array<{
      sessionId: string
      displayName: string
      state: string
      agentBrand?: string
      vrm: any
      labelRoot: HTMLDivElement | null
      labelTitleEl: HTMLDivElement | null
      labelStateEl: HTMLDivElement | null
      labelContextEl: HTMLDivElement | null
      labelBrandEl: HTMLDivElement | null
      labelAnchor: THREE.Object3D | null
      labelObject: CSS2DObject | null
    }>,
    getSelectedSessionId: () => options.selectedChatSessionId.value,
    stateText,
    getContextPercentLabel: (actor) => getContextPercentLabel(actor as VrmActor),
    onLabelClick: (sessionId) => options.onCharacterClick(sessionId),
  })

  const interactionPointEditor = createVrmInteractionPointEditorUtils({
    getScene: () => scene,
    getInteractionPoints: () => interactionPointUtils.getInteractionPoints(),
    getSelectedPointId: () => selectedInteractionPointId.value,
    getDraftPoint: () => interactionDraftPoint.value,
  })

  function roundCoord(value: number): number {
    return Math.round(value * 100) / 100
  }

  function cloneInteractionPointData(point: InteractionPointData): InteractionPointData {
    return {
      id: point.id,
      label: point.label,
      position: {
        x: roundCoord(point.position.x),
        y: roundCoord(point.position.y ?? 0),
        z: roundCoord(point.position.z),
      },
      approachPosition: {
        x: roundCoord(point.approachPosition.x),
        y: roundCoord(point.approachPosition.y ?? 0),
        z: roundCoord(point.approachPosition.z),
      },
      approachRotationY: point.approachRotationY,
      action: {
        ...point.action,
        seatOffset: point.action.seatOffset ? { ...point.action.seatOffset } : undefined,
      },
      capacity: point.capacity,
    }
  }

  function hasInteractionDraftChanges(): boolean {
    if (!interactionDraftPoint.value) return false
    if (interactionDraftSnapshot.value === null) return true
    return JSON.stringify(interactionDraftPoint.value) !== interactionDraftSnapshot.value
  }

  function cancelInteractionDraftPreview(): void {
    if (interactionDraftPreviewTimer !== null) {
      window.clearTimeout(interactionDraftPreviewTimer)
      interactionDraftPreviewTimer = null
    }
  }

  function restoreInteractionDraftPreview(): void {
    cancelInteractionDraftPreview()
    const pointId = selectedInteractionPointId.value
    if (!pointId) return
    if (interactionDraftSnapshot.value === null) {
      interactionPointUtils.removePoint(pointId)
    } else {
      const original = JSON.parse(interactionDraftSnapshot.value) as InteractionPointData
      interactionPointUtils.addManualPoint(cloneInteractionPointData(original))
    }
    refreshInteractionEditorState()
    reapplyBehaviorsAfterInteractionPointChange()
  }

  function scheduleInteractionDraftPreview(): void {
    cancelInteractionDraftPreview()
    if (!interactionDraftPoint.value) return
    interactionDraftPreviewTimer = window.setTimeout(() => {
      interactionDraftPreviewTimer = null
      if (!interactionDraftPoint.value) return
      interactionPointUtils.addManualPoint(cloneInteractionPointData(interactionDraftPoint.value))
      refreshInteractionEditorState()
      reapplyBehaviorsAfterInteractionPointChange()
    }, 120)
  }

  function ensureInteractionTransformControls(): void {
    if (interactionTransformControls || !scene || !camera || !renderer || !controls) return

    interactionTransformProxy = new THREE.Object3D()
    interactionTransformProxy.visible = false
    scene.add(interactionTransformProxy)

    const transformControls = new TransformControls(camera, renderer.domElement)
    transformControls.setSize(0.75)
    transformControls.addEventListener('dragging-changed', (event: any) => {
      if (!controls) return
      controls.enabled = !event.value
    })
    transformControls.addEventListener('objectChange', () => {
      if (!interactionTransformProxy || !interactionDraftPoint.value) return
      const next = cloneInteractionPointData(interactionDraftPoint.value)
      if (interactionTransformTarget.value === 'position') {
        next.position.x = roundCoord(interactionTransformProxy.position.x)
        next.position.y = roundCoord(interactionTransformProxy.position.y)
        next.position.z = roundCoord(interactionTransformProxy.position.z)
      } else if (interactionTransformTarget.value === 'approach') {
        next.approachPosition.x = roundCoord(interactionTransformProxy.position.x)
        next.approachPosition.y = roundCoord(interactionTransformProxy.position.y)
        next.approachPosition.z = roundCoord(interactionTransformProxy.position.z)
      } else {
        next.approachRotationY = interactionTransformProxy.rotation.y
      }
      interactionDraftPoint.value = next
      interactionPointEditor.refreshVisuals()
    })
    scene.add(transformControls)
    interactionTransformControls = transformControls
  }

  function syncInteractionTransformControls(): void {
    ensureInteractionTransformControls()
    if (!interactionTransformControls || !interactionTransformProxy) return
    if (!interactionEditorEnabled.value || !interactionDraftPoint.value) {
      interactionTransformControls.detach()
      interactionTransformControls.visible = false
      return
    }

    const draft = interactionDraftPoint.value
    if (interactionTransformTarget.value === 'rotate') {
      interactionTransformControls.setMode('rotate')
      interactionTransformControls.showX = false
      interactionTransformControls.showY = true
      interactionTransformControls.showZ = false
      interactionTransformProxy.position.set(
        draft.approachPosition.x,
        draft.approachPosition.y ?? 0,
        draft.approachPosition.z,
      )
      interactionTransformProxy.rotation.set(0, draft.approachRotationY, 0)
    } else {
      interactionTransformControls.setMode('translate')
      interactionTransformControls.showX = true
      interactionTransformControls.showY = true
      interactionTransformControls.showZ = true
      const target = interactionTransformTarget.value === 'approach' ? draft.approachPosition : draft.position
      interactionTransformProxy.position.set(target.x, target.y ?? 0, target.z)
      interactionTransformProxy.rotation.set(0, 0, 0)
    }

    interactionTransformControls.visible = true
    interactionTransformControls.attach(interactionTransformProxy)
  }

  function refreshInteractionEditorState(): void {
    if (interactionEditorEnabled.value) {
      interactionPointEditor.refreshVisuals()
    } else {
      interactionPointEditor.clearVisuals()
    }
    syncInteractionTransformControls()
  }

  function reapplyBehaviorsAfterInteractionPointChange(): void {
    for (const actor of actors) {
      behaviorScheduler.cleanupBehavior(actor)
    }
    void applyAllActorBehaviors(true)
  }

  function setInteractionEditorEnabled(next?: boolean): boolean {
    const target = typeof next === 'boolean' ? next : !interactionEditorEnabled.value
    if (target === interactionEditorEnabled.value) {
      refreshInteractionEditorState()
      return true
    }

    if (!target && hasInteractionDraftChanges()) {
      const shouldDiscard = window.confirm('目前互動點有未儲存變更，是否關閉編輯並捨棄？')
      if (!shouldDiscard) return false
      restoreInteractionDraftPreview()
    }

    interactionEditorEnabled.value = target
    if (!target) {
      cancelInteractionDraftPreview()
      selectedInteractionPointId.value = null
      interactionDraftPoint.value = null
      interactionDraftSnapshot.value = null
      interactionTransformTarget.value = 'position'
    }

    refreshInteractionEditorState()
    if (target) {
      setLoadingText(`互動點編輯模式：可從列表逐點編輯，或點場景標記選取（${interactionPointUtils.getInteractionPoints().length} 點）`)
    } else {
      setLoadingText(actors.length ? '' : '等待 Session 資料...')
    }
    return true
  }

  function setSelectedInteractionPoint(pointId: string | null): boolean {
    if (pointId === null) {
      selectedInteractionPointId.value = null
      interactionDraftPoint.value = null
      interactionDraftSnapshot.value = null
      refreshInteractionEditorState()
      return true
    }

    if (selectedInteractionPointId.value !== pointId && hasInteractionDraftChanges()) {
      const shouldDiscard = window.confirm('目前互動點有未儲存變更，是否捨棄後切換？')
      if (!shouldDiscard) return false
      restoreInteractionDraftPreview()
    }

    const point = interactionPointUtils.getPointById(pointId)
    if (!point) return false
    selectedInteractionPointId.value = pointId
    interactionDraftPoint.value = cloneInteractionPointData(point)
    interactionDraftSnapshot.value = JSON.stringify(interactionDraftPoint.value)
    interactionTransformTarget.value = 'position'
    refreshInteractionEditorState()
    return true
  }

  function setInteractionTransformTarget(target: InteractionTransformTarget): void {
    interactionTransformTarget.value = target
    refreshInteractionEditorState()
  }

  function createInteractionPointDraft(): void {
    if (hasInteractionDraftChanges()) {
      const shouldDiscard = window.confirm('目前互動點有未儲存變更，是否捨棄後新增？')
      if (!shouldDiscard) return
      restoreInteractionDraftPreview()
    }

    const center = stageWorldCenter || stageSeatCenter || new THREE.Vector3(0, 0, 0)
    const id = `manual-${Date.now()}`
    const draft: InteractionPointData = {
      id,
      label: `互動點-${interactionPointUtils.getInteractionPoints().length + 1}`,
      position: { x: roundCoord(center.x), y: 0, z: roundCoord(center.z) },
      approachPosition: { x: roundCoord(center.x), y: 0, z: roundCoord(center.z + 0.4) },
      approachRotationY: Math.PI,
      action: {
        type: 'sit',
        loopVrma: 'SittingDrinking.vrma',
        seatOffset: { x: 0, y: 0, z: 0 },
      },
      capacity: 1,
    }
    selectedInteractionPointId.value = id
    interactionDraftPoint.value = draft
    interactionDraftSnapshot.value = null
    interactionTransformTarget.value = 'position'
    refreshInteractionEditorState()
    setLoadingText(`已建立新互動點草稿：${draft.label}`)
  }

  function updateInteractionPointDraft(data: InteractionPointData): void {
    interactionDraftPoint.value = cloneInteractionPointData(data)
    refreshInteractionEditorState()
    scheduleInteractionDraftPreview()
  }

  function saveSelectedInteractionPoint(): boolean {
    if (!interactionDraftPoint.value) return false
    cancelInteractionDraftPreview()
    interactionPointUtils.addManualPoint(cloneInteractionPointData(interactionDraftPoint.value))
    interactionPointUtils.saveToStorage()
    interactionDraftSnapshot.value = JSON.stringify(interactionDraftPoint.value)
    refreshInteractionEditorState()
    reapplyBehaviorsAfterInteractionPointChange()
    setLoadingText(`已儲存互動點：${interactionDraftPoint.value.label}`)
    return true
  }

  function discardSelectedInteractionPoint(): void {
    if (!interactionDraftPoint.value) return
    restoreInteractionDraftPreview()
    if (interactionDraftSnapshot.value === null) {
      selectedInteractionPointId.value = null
      interactionDraftPoint.value = null
      interactionDraftSnapshot.value = null
      refreshInteractionEditorState()
      return
    }
    const point = selectedInteractionPointId.value
      ? interactionPointUtils.getPointById(selectedInteractionPointId.value)
      : null
    interactionDraftPoint.value = point ? cloneInteractionPointData(point) : null
    interactionDraftSnapshot.value = interactionDraftPoint.value
      ? JSON.stringify(interactionDraftPoint.value)
      : null
    refreshInteractionEditorState()
  }

  function deleteSelectedInteractionPoint(): void {
    const pointId = selectedInteractionPointId.value
    if (!pointId) return
    cancelInteractionDraftPreview()
    interactionPointUtils.removePoint(pointId)
    interactionPointUtils.saveToStorage()
    selectedInteractionPointId.value = null
    interactionDraftPoint.value = null
    interactionDraftSnapshot.value = null
    refreshInteractionEditorState()
    reapplyBehaviorsAfterInteractionPointChange()
    setLoadingText(`已刪除互動點（剩餘 ${interactionPointUtils.getInteractionPoints().length} 點）`)
  }

  async function reloadInteractionPointsFromScene(options: {
    persist?: boolean
    reapplyBehaviors?: boolean
  } = {}): Promise<void> {
    const { persist = true, reapplyBehaviors = true } = options
    if (!stageSceneRoot) {
      setLoadingText('目前場景尚未完成載入，無法重新讀取互動點')
      return
    }

    selectedInteractionPointId.value = null
    cancelInteractionDraftPreview()
    interactionDraftPoint.value = null
    interactionDraftSnapshot.value = null
    interactionPointUtils.clearAll()
    interactionPointUtils.extractFromScene(stageSceneRoot)
    refreshInteractionEditorState()

    if (persist) {
      interactionPointUtils.saveToStorage()
    }

    if (reapplyBehaviors) {
      reapplyBehaviorsAfterInteractionPointChange()
    }

    const count = interactionPointUtils.getInteractionPoints().length
    setLoadingText(count > 0 ? `已重新讀取 ${count} 個互動點` : '目前場景未辨識到可用互動點')
  }

  function handleInteractionPointsReloadEvent(): void {
    void reloadInteractionPointsFromScene()
  }

  function pickInteractionPointByRay(clientX: number, clientY: number): string | null {
    if (!renderer || !camera) return null
    const rect = renderer.domElement.getBoundingClientRect()
    const x = ((clientX - rect.left) / rect.width) * 2 - 1
    const y = -((clientY - rect.top) / rect.height) * 2 + 1
    pointer.set(x, y)
    raycaster.setFromCamera(pointer, camera)
    return interactionPointEditor.pickPointByRay(raycaster)
  }

  function pickGroundPointForEditor(clientX: number, clientY: number): THREE.Vector3 | null {
    if (!renderer || !camera) return null
    const rect = renderer.domElement.getBoundingClientRect()
    const x = ((clientX - rect.left) / rect.width) * 2 - 1
    const y = -((clientY - rect.top) / rect.height) * 2 + 1
    pointer.set(x, y)
    raycaster.setFromCamera(pointer, camera)
    const point = raycaster.ray.intersectPlane(groundPlane, rayHitPoint)
    return point ? point.clone() : null
  }

  const { onPointerDown, onKeyDown, onResize, handleSidebarSessionClick } = createVrmInteractionHandlers({
    interactionEditorEnabled,
    toggleInteractionEditor: setInteractionEditorEnabled,
    getRenderer: () => renderer,
    getCamera: () => camera,
    getLabelRenderer: () => labelRenderer,
    getContainer: () => options.containerRef.value,
    maxDevicePixelRatio: MAX_DEVICE_PIXEL_RATIO,
    optionsOnCharacterClick: options.onCharacterClick,
    pickActorByRay: (x, y) => pickActorByRay(x, y),
    setLoadingText,
    ensureSessionOnStage,
    updateAllHeadLabels,
    getActorsLength: () => actors.length,
    pickInteractionPointByRay,
    getInteractionPointById: (pointId) => interactionPointUtils.getPointById(pointId),
    selectInteractionPoint: (pointId) => setSelectedInteractionPoint(pointId),
    refreshInteractionVisuals: () => {
      refreshInteractionEditorState()
    },
    getInteractionPointsCount: () => interactionPointUtils.getInteractionPoints().length,
  })

  function assignRouteIndexForActor(actor: VrmActor): void {
    const routePoints = getRoutePointsByModel(actor.modelUrl)
    if (routePoints.length < 2) {
      actor.routePointIndex = 0
      return
    }
    actor.routePointIndex = 0
    setActorRoamTarget(actor, getActorRoutePoint(actor, actor.routePointIndex))
  }

  function findActorBySessionId(sessionId: string): VrmActor | null {
    return actors.find((item) => item.sessionId === sessionId) || null
  }

  function findActorByStageSlot(stageSlot: number): VrmActor | null {
    return actors.find((item) => item.stageSlot === stageSlot) || null
  }

  function assignSeatToActor(actor: VrmActor, setInitialPosition = false): void {
    const seats = getSeatPositions()
    let seatIndex = actor.stageSlot
    if (!Number.isInteger(seatIndex) || seatIndex < 0 || seatIndex >= seats.length) {
      seatIndex = Math.min(Math.max(actor.stageSlot, 0), Math.max(seats.length - 1, 0))
    }
    actorSeatIndexes.set(actor.sessionId, seatIndex)
    actor.seatIndex = seatIndex
    setActorTargetPosition(actor, resolveToWalkablePosition(seats[seatIndex], actor), setInitialPosition)
  }

  function getContextPercentLabel(actor: VrmActor): string {
    return getContextPercentLabelFromContext(actor.context)
  }

  async function loadVrmaAnimation(fileName: string): Promise<VRMAnimation> {
    const cached = vrmaAnimationCache.get(fileName)
    if (cached) return cached
    if (!loader) {
      throw new Error('GLTF loader not ready')
    }
    const url = `${VRMA_BASE_PATH}${fileName}`
    const gltf = await new Promise<any>((resolve, reject) => {
      loader?.load(url, resolve, undefined, reject)
    })
    const vrmAnimation = gltf.userData.vrmAnimations?.[0]
    if (!vrmAnimation) {
      throw new Error(`${fileName} 缺少 VRMAnimation`)
    }
    vrmaAnimationCache.set(fileName, vrmAnimation)
    return vrmAnimation
  }

  function clearJumpFinishHandler(actor: VrmActor): void {
    if (!actor.jumpFinishHandler) return
    actor.mixer.removeEventListener('finished', actor.jumpFinishHandler)
    actor.jumpFinishHandler = null
  }

  async function playActorMotion(
    actor: VrmActor,
    fileName: string,
    options: { force?: boolean; loop?: 'repeat' | 'once' } = {},
  ): Promise<THREE.AnimationAction | null> {
    const force = !!options.force
    const loopMode = options.loop || 'repeat'
    if (actor.jumpLocked && fileName !== JUMP_VRMA_FILE) {
      return actor.currentAction
    }
    if (!force && actor.currentMotionFile === fileName && loopMode === 'repeat') return actor.currentAction
    const vrmAnimation = await loadVrmaAnimation(fileName)
    const clip = createVRMAnimationClip(vrmAnimation, actor.vrm)
    if (!clip) {
      throw new Error(`${fileName} 轉換 AnimationClip 失敗`)
    }

    if (actor.currentAction) {
      actor.currentAction.fadeOut(0.15)
    }
    const action = actor.mixer.clipAction(clip)
    action.reset()
    if (loopMode === 'once') {
      action.setLoop(THREE.LoopOnce, 1)
      action.clampWhenFinished = true
    } else {
      action.setLoop(THREE.LoopRepeat, Infinity)
      action.clampWhenFinished = false
    }
    if (fileName === ROAM_MOVE_VRMA_FILE) {
      action.timeScale = ROAM_MOVE_ANIMATION_TIME_SCALE
    } else if (fileName === JUMP_VRMA_FILE) {
      action.timeScale = JUMP_ANIMATION_TIME_SCALE
    } else {
      action.timeScale = 1
    }
    action.fadeIn(0.2)
    action.play()

    actor.currentAction = action
    actor.currentMotionFile = fileName
    return action
  }

  function buildActorBehavior(
    actor: VrmActor,
    now: number,
    previousState?: SessionState,
  ): { behavior: ActorBehavior | null; interruptCurrent: boolean } {
    const decision = decideBehaviorSteps({
      actorState: actor.state,
      previousState,
      hasCustomRoute: false,
      isInteracting: behaviorScheduler.isInteracting(actor),
      findPointById: (pointId) => interactionPointUtils.getPointById(pointId),
      findNearestAvailablePoint: (pos, type) =>
        interactionPointUtils.findNearestAvailablePoint(pos, type),
      actorPosition: { x: actor.targetPosition.x, z: actor.targetPosition.z },
      getAvailablePointTypes: () => getAvailableInteractionPointTypes(),
      actorSlot: getActorSlot(actor),
    })

    return {
      interruptCurrent: decision.interruptCurrent,
      behavior: decision.steps.length > 0
        ? {
            steps: decision.steps,
            currentIndex: 0,
            stepStartedAt: now,
            interruptible: true,
            onComplete: decision.onComplete,
            motionApplied: false,
          }
        : null,
    }
  }

  async function applyActorBehavior(actor: VrmActor, forceMotion = false): Promise<void> {
    setActorTargetPosition(actor, actor.root.position)
    actor.behavior = buildActorBehavior(actor, clock.elapsedTime).behavior
  }

  function cloneBehaviorForJumpResume(
    behavior: ActorBehavior | null,
    now: number,
  ): ActorBehavior | null {
    if (!behavior) return null
    return {
      ...behavior,
      steps: behavior.steps.map((step) => ({
        ...step,
        target: step.target ? { ...step.target } : undefined,
        motionOffset: step.motionOffset ? { ...step.motionOffset } : undefined,
      })),
      stepStartedAt: now,
      motionApplied: false,
    }
  }

  async function triggerActorJump(actor: VrmActor): Promise<void> {
    clearJumpFinishHandler(actor)
    actor.jumpResumeBehavior = cloneBehaviorForJumpResume(actor.behavior, clock.elapsedTime)
    actor.behavior = null
    actor.jumpLocked = true
    let action: THREE.AnimationAction | null = null
    try {
      action = await playActorMotion(actor, JUMP_VRMA_FILE, { force: true, loop: 'once' })
    } catch (error) {
      actor.jumpLocked = false
      throw error
    }
    if (!action) {
      actor.jumpLocked = false
      return
    }
    const clipDuration = action.getClip()?.duration || 0.6
    actor.jumpStartedAt = clock.elapsedTime
    actor.jumpDuration = Math.max(0.45, clipDuration / Math.max(0.01, action.timeScale || 1))

    const onFinished = (event: any) => {
      if (event?.action !== action) return
      actor.mixer.removeEventListener('finished', onFinished)
      actor.jumpFinishHandler = null
      window.setTimeout(() => {
        actor.jumpLocked = false
        actor.jumpStartedAt = 0
        actor.jumpDuration = 0
        const resumeBehavior = cloneBehaviorForJumpResume(actor.jumpResumeBehavior, clock.elapsedTime)
        actor.jumpResumeBehavior = null
        if (resumeBehavior) {
          actor.behavior = resumeBehavior
          return
        }
        void applyActorBehavior(actor, true).catch((error) => {
          console.warn('Jump 後恢復動作失敗:', error)
        })
      }, JUMP_POST_HOLD_MS)
    }
    actor.jumpFinishHandler = onFinished
    actor.mixer.addEventListener('finished', onFinished)
  }

  async function applyAllActorBehaviors(forceMotion = false): Promise<void> {
    await Promise.all(actors.map((actor) => applyActorBehavior(actor, forceMotion)))
  }

  async function refreshBehaviorFlowPreview(): Promise<void> {
    behaviorFlowTestMode.clear()
    for (const actor of actors) {
      behaviorScheduler.cleanupBehavior(actor)
    }
    await applyAllActorBehaviors(true)
  }

  function updateRoamingActor(actor: VrmActor, now: number, delta: number): void {
    // 如果有 behavior 佇列，委託給 scheduler
    if (actor.behavior) {
      behaviorScheduler.updateBehavior(actor, now, delta)
      return
    }

    if (behaviorFlowTestMode.get(actor.sessionId) === true) {
      behaviorFlowTestMode.delete(actor.sessionId)
    }

    actor.behavior = buildActorBehavior(actor, now).behavior
  }

  async function loadVrmInstance(modelUrl: string): Promise<any> {
    if (!loader) {
      throw new Error('GLTF loader not ready')
    }
    const gltf = await new Promise<any>((resolve, reject) => {
      loader?.load(modelUrl, resolve, undefined, reject)
    })
    const vrm = gltf.userData.vrm
    if (!vrm) {
      throw new Error('未取得 VRM 物件')
    }
    VRMUtils.rotateVRM0(vrm)
    VRMUtils.removeUnnecessaryVertices(vrm.scene)
    VRMUtils.combineSkeletons(vrm.scene)
    return vrm
  }

  async function createActor(session: SessionSnapshotItem, preferredModelUrl?: string, stageSlot = 0): Promise<VrmActor> {
    const modelUrl = preferredModelUrl || getConfiguredModelUrlForSlot(stageSlot)
    const vrm = await loadVrmInstance(modelUrl)
    vrm.scene.scale.setScalar(globalActorScale.value)
    vrm.scene.traverse((node: THREE.Object3D) => {
      const mesh = node as THREE.Mesh
      if (!mesh.isMesh) return
      mesh.castShadow = false
      mesh.receiveShadow = false
    })
    const mixer = new THREE.AnimationMixer(vrm.scene)
    const groundOffsetY = computeGroundOffsetY(vrm)

    const root = new THREE.Group()
    root.name = `session-${session.session_id}`
    root.userData.sessionId = session.session_id
    root.add(vrm.scene)
    scene?.add(root)

    const actor: VrmActor = {
      sessionId: session.session_id,
      stageSlot,
      displayName: session.display_name,
      state: session.state,
      context: session.context || null,
      agentBrand: session.agent_brand || 'codex',
      modelUrl,
      mountedOrder: ++actorMountSequence,
      vrm,
      mixer,
      root,
      targetPosition: new THREE.Vector3(),
      currentAction: null,
      currentMotionFile: '',
      modelBaseLocalPosition: vrm.scene.position.clone(),
      hipsBone: null,
      hipsBaseLocalPosition: null,
      seatIndex: -1,
      roamTarget: new THREE.Vector3(),
      roamSpeed: randomRange(ROAM_SPEED_MIN, ROAM_SPEED_MAX),
      routePointIndex: 0,
      labelRoot: null,
      labelTitleEl: null,
      labelStateEl: null,
      labelContextEl: null,
      labelBrandEl: null,
      labelAnchor: null,
      labelObject: null,
      jumpFinishHandler: null,
      jumpLocked: false,
      jumpStartedAt: 0,
      jumpDuration: 0,
      jumpResumeBehavior: null,
      groundOffsetY,
      behavior: null,
      occupyingPointId: null,
    }

    const hipsBone = vrm.humanoid?.getNormalizedBoneNode?.('hips') || null
    actor.hipsBone = hipsBone
    actor.hipsBaseLocalPosition = hipsBone ? hipsBone.position.clone() : null

    setupActorHeadLabel(actor)
    assignRouteIndexForActor(actor)
    assignSeatToActor(actor, true)
    actors.push(actor)
    actorsVersion.value++
    await applyActorBehavior(actor, true)
    return actor
  }

  async function removeActor(actor: VrmActor): Promise<void> {
    const idx = actors.findIndex((item) => item.sessionId === actor.sessionId)
    if (idx >= 0) {
      actors.splice(idx, 1)
      actorsVersion.value++
    }
    actorSeatIndexes.delete(actor.sessionId)
    behaviorScheduler.cleanupBehavior(actor)
    interactionPointUtils.releaseAllBySession(actor.sessionId)
    if (actor.currentAction) {
      actor.currentAction.stop()
    }
    clearJumpFinishHandler(actor)
    cleanupActorHeadLabel(actor)
    actor.jumpLocked = false
    actor.jumpStartedAt = 0
    actor.jumpDuration = 0
    actor.jumpResumeBehavior = null
    scene?.remove(actor.root)
    actor.vrm.dispose?.()
  }

  async function replaceOldestActorWithSession(session: SessionSnapshotItem, triggerJump = false): Promise<void> {
    const oldest = findOldestActor(actors)
    if (!oldest) {
      const created = await createActor(session, undefined, 0)
      if (triggerJump) {
        await triggerActorJump(created)
      }
      return
    }
    const preservedStageSlot = oldest.stageSlot
    await removeActor(oldest)
    const created = await createActor(session, undefined, preservedStageSlot)
    if (triggerJump) {
      await triggerActorJump(created)
    }
  }

  async function ensureSessionOnStage(session: SessionSnapshotItem, triggerJump = false): Promise<void> {
    const existing = findActorBySessionId(session.session_id)
    if (existing) {
      const previousState = existing.state
      existing.displayName = session.display_name
      existing.state = session.state
      existing.context = session.context || null
      existing.agentBrand = session.agent_brand || existing.agentBrand || 'codex'
      updateActorHeadLabel(existing)

      // state 變化時觸發行為決策
      if (previousState !== session.state) {
        const next = buildActorBehavior(existing, clock.elapsedTime, previousState)

        if (next.interruptCurrent) {
          await behaviorScheduler.interruptBehavior(existing, 'state-change')
        }

        existing.behavior = next.behavior
      }

      if (triggerJump) {
        await triggerActorJump(existing)
      }
      return
    }
    if (actors.length < MAX_ACTORS) {
      const nextSlot = actors.length
      const created = await createActor(session, undefined, nextSlot)
      if (triggerJump) {
        await triggerActorJump(created)
      }
      return
    }
    await replaceOldestActorWithSession(session, triggerJump)
  }

  async function syncActorsToConfiguredModels(): Promise<void> {
    const actorsInSlotOrder = [...actors].sort((a, b) => a.stageSlot - b.stageSlot)
    for (const actor of actorsInSlotOrder) {
      const targetModelUrl = getConfiguredModelUrlForSlot(actor.stageSlot)
      if (!targetModelUrl || actor.modelUrl === targetModelUrl) continue
      const session = options.visibleSessions.value.find((item) => item.session_id === actor.sessionId)
      if (!session) continue
      await removeActor(actor)
      await createActor(session, targetModelUrl, actor.stageSlot)
    }
    updateAllHeadLabels()
  }

  async function syncActorsWithSessions(): Promise<void> {
    const targetSessions = options.visibleSessions.value.slice(0, MAX_ACTORS)
    const targetIdSet = new Set(targetSessions.map((item) => item.session_id))

    for (let slot = 0; slot < MAX_ACTORS; slot++) {
      const targetSession = targetSessions[slot]
      const actorAtSlot = findActorByStageSlot(slot)

      if (!targetSession) {
        if (actorAtSlot) {
          await removeActor(actorAtSlot)
        }
        continue
      }

      if (actorAtSlot && actorAtSlot.sessionId === targetSession.session_id) {
        await ensureSessionOnStage(targetSession, false)
        continue
      }

      const existingActor = findActorBySessionId(targetSession.session_id)
      if (existingActor) {
        if (actorAtSlot && actorAtSlot !== existingActor) {
          await removeActor(actorAtSlot)
        }
        existingActor.stageSlot = slot
        assignSeatToActor(existingActor, true)
        const targetModelUrl = getConfiguredModelUrlForSlot(slot)
        if (existingActor.modelUrl !== targetModelUrl) {
          await removeActor(existingActor)
          await createActor(targetSession, targetModelUrl, slot)
        } else {
          await ensureSessionOnStage(targetSession, false)
        }
        continue
      }

      if (actorAtSlot) {
        await removeActor(actorAtSlot)
      }
      await createActor(targetSession, undefined, slot)
    }

    const strayActors = actors.filter((actor) => {
      return actor.stageSlot >= targetSessions.length || !targetIdSet.has(actor.sessionId)
    })
    for (const actor of strayActors) {
      if (actors.includes(actor)) {
        await removeActor(actor)
      }
    }

    updateAllHeadLabels()
    setLoadingText(actors.length ? '' : '等待 Session 資料...')
  }

  function pickActorByRay(clientX: number, clientY: number): VrmActor | null {
    if (!renderer || !camera || !actors.length) return null
    const rect = renderer.domElement.getBoundingClientRect()
    const x = ((clientX - rect.left) / rect.width) * 2 - 1
    const y = -((clientY - rect.top) / rect.height) * 2 + 1
    pointer.set(x, y)
    raycaster.setFromCamera(pointer, camera)
    const intersections = raycaster.intersectObjects(actors.map((actor) => actor.root), true)
    if (!intersections.length) return null
    for (const hit of intersections) {
      let target: THREE.Object3D | null = hit.object
      while (target) {
        const actor = actors.find((item) => item.root === target)
        if (actor) return actor
        target = target.parent
      }
    }
    return null
  }

  async function init(): Promise<void> {
    const container = options.containerRef.value
    if (!container) return

    scene = new THREE.Scene()
    scene.background = new THREE.Color(0xc7d4e8)

    camera = new THREE.PerspectiveCamera(30, container.clientWidth / container.clientHeight, 0.1, 30)
    camera.position.set(0, 1.5, 5.2)

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, MAX_DEVICE_PIXEL_RATIO))
    renderer.setSize(container.clientWidth, container.clientHeight)
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.05
    renderer.shadowMap.enabled = true
    renderer.shadowMap.type = THREE.PCFSoftShadowMap
    renderer.shadowMap.autoUpdate = false
    container.appendChild(renderer.domElement)

    labelRenderer = new CSS2DRenderer()
    labelRenderer.setSize(container.clientWidth, container.clientHeight)
    labelRenderer.domElement.style.position = 'absolute'
    labelRenderer.domElement.style.inset = '0'
    labelRenderer.domElement.style.pointerEvents = 'none'
    container.appendChild(labelRenderer.domElement)

    controls = new OrbitControls(camera, renderer.domElement)
    controls.target.set(0, 1.15, 0)
    controls.update()

    loader = new GLTFLoader()
    loader.register((parser) => new VRMLoaderPlugin(parser))
    loader.register((parser) => new VRMAnimationLoaderPlugin(parser))

    await loadStageScene()
    setupLights()
    const hasSavedInteractionPoints = interactionPointUtils.loadFromStorage()
    if (!hasSavedInteractionPoints) {
      await reloadInteractionPointsFromScene({ persist: true, reapplyBehaviors: false })
    }
    renderer.shadowMap.needsUpdate = true
    if (camera && controls) {
      const storedCameraView = loadCameraViewFromStorage()
      if (storedCameraView) {
        camera.position.set(
          storedCameraView.position.x,
          storedCameraView.position.y,
          storedCameraView.position.z,
        )
        controls.target.set(
          storedCameraView.target.x,
          storedCameraView.target.y,
          storedCameraView.target.z,
        )
      } else if (stageCameraTarget && stageCameraPosition) {
        camera.position.copy(stageCameraPosition)
        controls.target.copy(stageCameraTarget)
      }
      controls.update()
    }
    controls.addEventListener('change', scheduleSaveCameraView)

    renderer.domElement.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('resize', onResize)
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('session-stage:sidebar-session-click', handleSidebarSessionClick as EventListener)

    await syncActorsWithSessions()

    const tick = () => {
      if (destroyed) return
      animationFrameId = window.requestAnimationFrame(tick)
      const delta = clock.getDelta()
      frameAccumulator += delta
      if (frameAccumulator < FRAME_INTERVAL) {
        return
      }
      const stepDelta = frameAccumulator
      frameAccumulator = 0
      const now = clock.elapsedTime
      for (const actor of actors) {
        actor.vrm.update(stepDelta)
        actor.mixer.update(stepDelta)
        lockActorRootMotion(actor)
        const jumpLift = getJumpLift(actor, now)
        updateRoamingActor(actor, now, stepDelta)
        actor.root.position.lerp(actor.targetPosition, 0.08)
        actor.root.position.y = actor.targetPosition.y + jumpLift
      }
      controls?.update()
      if (scene && camera) {
        renderer?.render(scene, camera)
        labelRenderer?.render(scene, camera)
      }
    }
    tick()
  }

  function destroy(): void {
    destroyed = true
    if (animationFrameId !== null) {
      window.cancelAnimationFrame(animationFrameId)
      animationFrameId = null
    }
    window.removeEventListener('resize', onResize)
    window.removeEventListener('keydown', onKeyDown)
    window.removeEventListener('session-stage:sidebar-session-click', handleSidebarSessionClick as EventListener)
    renderer?.domElement.removeEventListener('pointerdown', onPointerDown)
    controls?.removeEventListener('change', scheduleSaveCameraView)
    if (cameraViewSaveTimer !== null) {
      window.clearTimeout(cameraViewSaveTimer)
      cameraViewSaveTimer = null
    }
    cancelInteractionDraftPreview()
    saveCameraViewToStorage()

    while (actors.length) {
      const actor = actors.pop()
      if (!actor) continue
      behaviorScheduler.cleanupBehavior(actor)
      actor.currentAction?.stop()
      clearJumpFinishHandler(actor)
      cleanupActorHeadLabel(actor)
      scene?.remove(actor.root)
      actor.vrm.dispose?.()
    }
    if (stageSceneRoot) {
      scene?.remove(stageSceneRoot)
      stageSceneRoot.traverse((node) => {
        const mesh = node as THREE.Mesh
        if (mesh.geometry) {
          mesh.geometry.dispose()
        }
        if (!mesh.material) {
          return
        }
        if (Array.isArray(mesh.material)) {
          mesh.material.forEach((material) => material.dispose())
        } else {
          mesh.material.dispose()
        }
      })
      stageSceneRoot = null
    }
    clearRouteVisuals()
    interactionPointEditor.clearVisuals()
    interactionPointUtils.clearAll()
    if (interactionTransformControls) {
      interactionTransformControls.detach()
      interactionTransformControls.dispose()
      scene?.remove(interactionTransformControls)
      interactionTransformControls = null
    }
    if (interactionTransformProxy) {
      scene?.remove(interactionTransformProxy)
      interactionTransformProxy = null
    }
    if (controls) {
      controls.enabled = true
    }
    sceneLights.length = 0
    sceneLightTargets.length = 0
    stageObstacles.length = 0
    actorSeatIndexes.clear()
    vrmaAnimationCache.clear()
    stageSeatCenter = null
    stageSeatSpread = { x: 1.1, z: 0.7 }
    stageCameraTarget = null
    stageCameraPosition = null
    stageWorldCenter = null
    stageWorldSize = null

    controls?.dispose()
    renderer?.dispose()

    const container = options.containerRef.value
    if (container) {
      if (renderer?.domElement && container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement)
      }
      if (labelRenderer?.domElement && container.contains(labelRenderer.domElement)) {
        container.removeChild(labelRenderer.domElement)
      }
    }

    scene = null
    camera = null
    renderer = null
    labelRenderer = null
    controls = null
    loader = null
  }

  globalActorScale.value = loadVrmActorScale()
  globalGroundOffset.value = loadVrmGlobalGroundOffset()

  watch(
    () => options.selectedChatSessionId.value,
    () => {
      updateAllHeadLabels()
    },
  )

  watch(
    () => options.visibleSessions.value,
    () => {
      void syncActorsWithSessions()
    },
    { deep: true },
  )

  watch(
    globalGroundOffset,
    (value) => {
      saveVrmGlobalGroundOffset(value)
      for (const actor of actors) {
        actor.targetPosition.y = actor.groundOffsetY + value
        actor.roamTarget.y = actor.groundOffsetY + value
      }
    },
  )

  watch(
    globalActorScale,
    (value) => {
      saveVrmActorScale(value)
      for (const actor of actors) {
        applyActorScale(actor, value)
      }
    },
  )

  watch(
    interactionTransformTarget,
    () => {
      syncInteractionTransformControls()
    },
  )

  // ─── 行為流測試執行器 ───

  const behaviorFlowTestMode = new Map<string, boolean>()

  function testExecuteFlow(actorSessionId: string, flow: BehaviorFlow): boolean {
    const actor = findActorBySessionId(actorSessionId)
    if (!actor) return false

    const ctx = {
      actorState: actor.state,
      previousState: undefined,
      hasCustomRoute: false,
      isInteracting: behaviorScheduler.isInteracting(actor),
      findPointById: (pointId: string) => interactionPointUtils.getPointById(pointId),
      findNearestAvailablePoint: (pos: { x: number; z: number }, type?: string) =>
        interactionPointUtils.findNearestAvailablePoint(pos, type),
      actorPosition: { x: actor.targetPosition.x, z: actor.targetPosition.z },
      getAvailablePointTypes: () => getAvailableInteractionPointTypes(),
      actorSlot: getActorSlot(actor),
    }

    const resolved = resolveFlowSteps(flow.steps, ctx)
    if (!resolved) return false

    // 打斷當前行為
    void behaviorScheduler.interruptBehavior(actor, 'test-execute')

    behaviorFlowTestMode.set(actorSessionId, true)
    actor.behavior = {
      steps: resolved.steps,
      currentIndex: 0,
      stepStartedAt: clock.elapsedTime,
      interruptible: true,
      onComplete: flow.onComplete,
      motionApplied: false,
    }
    return true
  }

  function testStopFlow(actorSessionId: string): void {
    const actor = findActorBySessionId(actorSessionId)
    if (!actor) return
    behaviorFlowTestMode.delete(actorSessionId)
    void behaviorScheduler.interruptBehavior(actor, 'test-stop')
    actor.behavior = buildActorBehavior(actor, clock.elapsedTime).behavior
  }

  function isTestRunning(actorSessionId: string): boolean {
    return behaviorFlowTestMode.get(actorSessionId) === true
  }

  function getActorList(): Array<{ sessionId: string; displayName: string; state: string; slot: number }> {
    // 讀取 actorsVersion 讓 Vue computed 追蹤 actors 增刪
    void actorsVersion.value
    return actors.map((a, idx) => ({
      sessionId: a.sessionId,
      displayName: a.displayName,
      state: a.state,
      slot: a.stageSlot,
    }))
    .sort((a, b) => a.slot - b.slot)
  }

  function getActorCurrentStep(actorSessionId: string): string | null {
    const actor = findActorBySessionId(actorSessionId)
    if (!actor) return null
    return behaviorScheduler.getCurrentStepType(actor)
  }

  onMounted(() => {
    window.addEventListener(VRM_INTERACTION_POINTS_RELOAD_EVENT, handleInteractionPointsReloadEvent as EventListener)
    window.addEventListener(VRM_ACTOR_SCALE_EVENT, handleGlobalActorScaleEvent as EventListener)
    window.addEventListener(VRM_GLOBAL_GROUND_OFFSET_EVENT, handleGlobalGroundOffsetEvent as EventListener)
    window.addEventListener(VRM_ACTOR_SLOT_CONFIG_EVENT, handleActorSlotConfigEvent as EventListener)
    void init().catch((error) => {
      console.error(error)
      setLoadingText(`3D 舞台初始化失敗: ${String((error as Error)?.message || error)}`)
    })
  })

  onUnmounted(() => {
    window.removeEventListener(VRM_INTERACTION_POINTS_RELOAD_EVENT, handleInteractionPointsReloadEvent as EventListener)
    window.removeEventListener(VRM_ACTOR_SCALE_EVENT, handleGlobalActorScaleEvent as EventListener)
    window.removeEventListener(VRM_GLOBAL_GROUND_OFFSET_EVENT, handleGlobalGroundOffsetEvent as EventListener)
    window.removeEventListener(VRM_ACTOR_SLOT_CONFIG_EVENT, handleActorSlotConfigEvent as EventListener)
    destroy()
  })

  return {
    loadingText,
    globalActorScale,
    setGlobalActorScale,
    globalGroundOffset,
    setGlobalGroundOffset,
    // 行為流設定面板 API
    behaviorFlowConfigUtils: getBehaviorFlowConfigUtils(),
    testExecuteFlow,
    testStopFlow,
    isTestRunning,
    refreshBehaviorFlowPreview,
    getActorList,
    getActorCurrentStep,
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
    getInteractionPoints: () => interactionPointUtils.getInteractionPoints(),
    pickGroundPoint: pickGroundPointForEditor,
  }
}
