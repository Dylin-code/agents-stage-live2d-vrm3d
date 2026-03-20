export interface VrmActorSlotOption {
  label: string
  modelUrl: string
}

export const VRM_ACTOR_SLOT_CONFIG_STORAGE_KEY = 'vrm-stage-actor-slot-config-v1'
export const VRM_ACTOR_SLOT_CONFIG_EVENT = 'session-stage:vrm-actor-slot-config-change'
export const VRM_ACTOR_SLOT_COUNT = 4

export const DEFAULT_VRM_ACTOR_SLOT_OPTIONS: VrmActorSlotOption[] = [
  { label: '角色 1 - Alicia Solid', modelUrl: '/vrm3d/AliciaSolid.vrm' },
  { label: '角色 2 - 風きりたん', modelUrl: '/vrm3d/ふらすこ式風きりたん_VRM_1_0_1.vrm' },
  { label: '角色 3 - Hatsune Miku NT', modelUrl: '/vrm3d/HatsuneMikuNT.vrm' },
  { label: '角色 4 - avatar_L', modelUrl: '/vrm3d/avatar_L.vrm' },
]

function normalizeOptions(options: VrmActorSlotOption[]): VrmActorSlotOption[] {
  if (options.length > 0) return options
  return [...DEFAULT_VRM_ACTOR_SLOT_OPTIONS]
}

export function buildDefaultVrmActorSlotConfig(options: VrmActorSlotOption[] = DEFAULT_VRM_ACTOR_SLOT_OPTIONS): string[] {
  const normalizedOptions = normalizeOptions(options)
  return Array.from({ length: VRM_ACTOR_SLOT_COUNT }, (_, index) => {
    return normalizedOptions[index]?.modelUrl || normalizedOptions[0]?.modelUrl || ''
  })
}

export function normalizeVrmActorSlotConfig(
  value: unknown,
  options: VrmActorSlotOption[] = DEFAULT_VRM_ACTOR_SLOT_OPTIONS,
): string[] {
  const normalizedOptions = normalizeOptions(options)
  const validModelUrls = new Set(normalizedOptions.map((item) => item.modelUrl))
  const defaults = buildDefaultVrmActorSlotConfig(normalizedOptions)
  if (!Array.isArray(value)) return defaults

  return Array.from({ length: VRM_ACTOR_SLOT_COUNT }, (_, index) => {
    const modelUrl = value[index]
    return typeof modelUrl === 'string' && validModelUrls.has(modelUrl)
      ? modelUrl
      : defaults[index] || normalizedOptions[0]?.modelUrl || ''
  })
}

export function loadVrmActorSlotConfig(
  options: VrmActorSlotOption[] = DEFAULT_VRM_ACTOR_SLOT_OPTIONS,
): string[] {
  try {
    const raw = window.localStorage.getItem(VRM_ACTOR_SLOT_CONFIG_STORAGE_KEY)
    if (!raw) return buildDefaultVrmActorSlotConfig(options)
    return normalizeVrmActorSlotConfig(JSON.parse(raw), options)
  } catch {
    return buildDefaultVrmActorSlotConfig(options)
  }
}

export function saveVrmActorSlotConfig(
  slotModelUrls: string[],
  options: VrmActorSlotOption[] = DEFAULT_VRM_ACTOR_SLOT_OPTIONS,
): string[] {
  const normalized = normalizeVrmActorSlotConfig(slotModelUrls, options)
  try {
    window.localStorage.setItem(VRM_ACTOR_SLOT_CONFIG_STORAGE_KEY, JSON.stringify(normalized))
  } catch {
    // ignore storage failures
  }
  return normalized
}
