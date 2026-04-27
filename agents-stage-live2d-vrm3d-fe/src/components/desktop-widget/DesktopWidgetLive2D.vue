<template>
  <canvas ref="canvasRef" class="desktop-widget-live2d"></canvas>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import * as PIXI from 'pixi.js'
import { Live2DModel as Live2DModelCubism4 } from 'pixi-live2d-display/cubism4'
import { Live2DModel as Live2DModelCubism2 } from 'pixi-live2d-display/cubism2'
import type { SessionState } from '../../types/sessionState'

const props = withDefaults(defineProps<{
  state: SessionState
  modelPath?: string
}>(), {
  modelPath: 'assets/models/fn57_2203/normal/normal.model3.json',
})

interface MotionEntry {
  group: string
  index: number
  key: string
}

const STATE_MOTION_CANDIDATES: Record<SessionState, string[]> = {
  IDLE: ['idle', 'main_1', 'home'],
  THINKING: ['main_2', 'main_1', 'touch_head', 'taphead', 'tap', 'idle'],
  TOOLING: ['mission', 'touch_body', 'touch_special', 'effect', 'tap', 'idle'],
  RESPONDING: ['main_3', 'main_2', 'main_1', 'home', 'taphead', 'idle'],
  WAITING: ['mail', 'home', 'login', 'idle'],
}
const MIN_MODEL_SCALE = 0.06
const IDLE_MOTION_INTERVAL_MS = 10_000
const IDLE_MOTION_JITTER_MS = 2_500

const canvasRef = ref<HTMLCanvasElement | null>(null)
let app: PIXI.Application | null = null
let model: any | null = null
let disposed = false
let lastMotionKey = ''
let layoutWarmupTicks = 0
let idleMotionTimer: number | null = null
let nextIdleMotionAt = 0

Live2DModelCubism4.registerTicker(PIXI.Ticker)
Live2DModelCubism2.registerTicker(PIXI.Ticker)

function normalize(value: string): string {
  return value.trim().toLowerCase()
}

function collectMotions(currentModel: any): MotionEntry[] {
  const settings = currentModel?.internalModel?.settings
  const rawMotions = settings?.motions || settings?.json?.FileReferences?.Motions || {}
  const entries: MotionEntry[] = []
  for (const [group, motions] of Object.entries(rawMotions)) {
    if (!Array.isArray(motions)) continue
    motions.forEach((motion: any, index) => {
      const file = String(motion?.File || motion?.file || '')
      const name = file.split('/').pop()?.replace(/\.motion3?\.json$/i, '') || group
      entries.push({
        group,
        index,
        key: `${normalize(group)}:${normalize(name)}:${index}`,
      })
    })
  }
  return entries
}

function pickMotion(currentModel: any, state: SessionState): MotionEntry | null {
  const motions = collectMotions(currentModel)
  if (motions.length === 0) return null
  const candidates = STATE_MOTION_CANDIDATES[state]
  for (const candidate of candidates) {
    const target = normalize(candidate)
    const exact = motions.find((motion) => {
      const [group, name] = motion.key.split(':')
      return group === target || name === target
    })
    if (exact) return exact
    const fuzzy = motions.find((motion) => motion.key.includes(target))
    if (fuzzy) return fuzzy
  }
  return motions[0]
}

function pickRandomMotion(currentModel: any): MotionEntry | null {
  const motions = collectMotions(currentModel)
  if (motions.length === 0) return null
  const candidates = motions.filter((motion) => motion.key !== lastMotionKey)
  const pool = candidates.length > 0 ? candidates : motions
  const index = Math.floor(Math.random() * pool.length)
  return pool[index] || null
}

function layoutModel(): void {
  if (!app || !model) return
  const width = app.renderer.width
  const height = app.renderer.height
  const bounds = model.getLocalBounds()
  const naturalWidth = Math.max(1, bounds.width)
  const naturalHeight = Math.max(1, bounds.height)
  const horizontalPadding = 26
  const topPadding = 26
  const bottomPadding = 8
  const availableWidth = Math.max(1, width - horizontalPadding * 2)
  const availableHeight = Math.max(1, height - topPadding - bottomPadding)
  const scale = Math.max(MIN_MODEL_SCALE, Math.min(
    availableWidth / naturalWidth,
    availableHeight / naturalHeight,
  ))
  const scaledWidth = naturalWidth * scale
  const scaledHeight = naturalHeight * scale
  const targetLeft = (width - scaledWidth) / 2
  const targetTop = topPadding + Math.max(0, availableHeight - scaledHeight) * 0.42

  model.scale.set(scale)
  model.x = targetLeft - bounds.x * scale
  model.y = targetTop - bounds.y * scale
}

function resizeRenderer(): void {
  if (!app || !canvasRef.value) return
  const rect = canvasRef.value.getBoundingClientRect()
  app.renderer.resize(Math.max(1, Math.floor(rect.width)), Math.max(1, Math.floor(rect.height)))
  layoutModel()
}

async function playStateMotion(state: SessionState): Promise<void> {
  if (!model) return
  const motion = pickMotion(model, state)
  if (!motion || motion.key === lastMotionKey) return
  lastMotionKey = motion.key
  try {
    if (typeof model.motion === 'function') {
      await model.motion(motion.group, motion.index)
      return
    }
    const manager = model.internalModel?.motionManager
    if (manager && typeof manager.startMotion === 'function') {
      manager.startMotion(motion.group, motion.index)
    }
  } catch (error) {
    console.warn('Failed to play desktop widget Live2D motion', error)
  } finally {
    scheduleNextIdleMotion()
  }
}

function scheduleNextIdleMotion(nowMs = Date.now()): void {
  const randomJitter = Math.floor(Math.random() * IDLE_MOTION_JITTER_MS)
  nextIdleMotionAt = nowMs + IDLE_MOTION_INTERVAL_MS + randomJitter
}

async function maybePlayIdleMotion(): Promise<void> {
  if (!model || disposed) return
  const now = Date.now()
  if (now < nextIdleMotionAt) return
  const motion = pickRandomMotion(model) || pickMotion(model, props.state)
  if (!motion) {
    scheduleNextIdleMotion(now)
    return
  }
  try {
    if (typeof model.motion === 'function') {
      await model.motion(motion.group, motion.index)
    } else {
      const manager = model.internalModel?.motionManager
      if (manager && typeof manager.startMotion === 'function') {
        manager.startMotion(motion.group, motion.index)
      }
    }
    lastMotionKey = motion.key
  } catch (error) {
    console.warn('Failed to play desktop widget idle motion', error)
  } finally {
    scheduleNextIdleMotion()
  }
}

async function loadModel(): Promise<void> {
  if (!app) return
  const ModelClass = props.modelPath.endsWith('.model3.json') ? Live2DModelCubism4 : Live2DModelCubism2
  const loaded = await ModelClass.from(props.modelPath)
  if (disposed || !app) {
    loaded.destroy()
    return
  }
  model = loaded
  model.zIndex = 1
  app.stage.addChild(model)
  layoutWarmupTicks = 12
  layoutModel()
  scheduleNextIdleMotion()
  void playStateMotion(props.state)
}

onMounted(() => {
  if (!canvasRef.value) return
  app = new PIXI.Application({
    view: canvasRef.value,
    transparent: true,
    autoStart: true,
    width: 360,
    height: 440,
    backgroundAlpha: 0,
  })
  app.stage.sortableChildren = true
  app.ticker.add(() => {
    if (layoutWarmupTicks <= 0) return
    layoutWarmupTicks -= 1
    layoutModel()
  })
  window.addEventListener('resize', resizeRenderer)
  resizeRenderer()
  idleMotionTimer = window.setInterval(() => {
    void maybePlayIdleMotion()
  }, 1000)
  void loadModel()
})

onUnmounted(() => {
  disposed = true
  window.removeEventListener('resize', resizeRenderer)
  if (idleMotionTimer !== null) {
    window.clearInterval(idleMotionTimer)
    idleMotionTimer = null
  }
  if (model) {
    model.destroy()
    model = null
  }
  if (app) {
    app.destroy(true)
    app = null
  }
})

watch(
  () => props.state,
  (state) => {
    void playStateMotion(state)
  },
)
</script>

<style scoped>
.desktop-widget-live2d {
  display: block;
  width: 100%;
  height: 100%;
}
</style>
