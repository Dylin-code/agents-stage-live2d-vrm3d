import * as THREE from 'three'
import { CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js'

type HeadLabelActor = {
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
}

// No longer used — brand icons are loaded directly via `/brand/{brand}-badge.svg`

export function createVrmHeadLabelUtils(args: {
  getActors: () => HeadLabelActor[]
  getSelectedSessionId: () => string
  stateText: (state: any) => string
  getContextPercentLabel: (actor: any) => string
  onLabelClick?: (sessionId: string) => void
}) {
  const { getActors, getSelectedSessionId, stateText, getContextPercentLabel, onLabelClick } = args

  function updateActorHeadLabel(actor: HeadLabelActor): void {
    if (!actor.labelRoot || !actor.labelTitleEl || !actor.labelStateEl || !actor.labelContextEl) return
    const stateClass = `state-${String(actor.state || 'IDLE').toLowerCase()}`
    const selectedClass = getSelectedSessionId() === actor.sessionId ? ' selected' : ''
    actor.labelRoot.className = `session-head-label ${stateClass}${selectedClass}`
    actor.labelTitleEl.textContent = actor.displayName || actor.sessionId.slice(0, 8)
    actor.labelStateEl.textContent = stateText(actor.state)
    actor.labelContextEl.textContent = getContextPercentLabel(actor)
    // Update brand badge icon src.
    if (actor.labelBrandEl) {
      const brand = actor.agentBrand || 'codex'
      const imgEl = actor.labelBrandEl.querySelector('img') as HTMLImageElement | null
      if (imgEl) imgEl.src = `/brand/${brand}-badge.svg`
    }
  }

  function updateAllHeadLabels(): void {
    for (const actor of getActors()) {
      updateActorHeadLabel(actor)
    }
  }

  function setupActorHeadLabel(actor: HeadLabelActor): void {
    const labelRoot = document.createElement('div')
    labelRoot.className = 'session-head-label state-idle'
    labelRoot.style.pointerEvents = 'auto'
    labelRoot.style.cursor = 'pointer'
    labelRoot.addEventListener('pointerdown', (event) => {
      event.stopPropagation()
      onLabelClick?.(actor.sessionId)
    })

    // Brand badge — circular icon above the title.
    const brand = actor.agentBrand || 'codex'
    const brandEl = document.createElement('div')
    brandEl.className = 'brand-badge'
    brandEl.style.cssText = `
      display:flex;
      justify-content:center;
      margin-bottom:3px;
      pointer-events:none;
    `
    const brandImg = document.createElement('img')
    brandImg.src = `/brand/${brand}-badge.svg`
    brandImg.alt = brand
    brandImg.style.cssText = `
      width:22px;
      height:22px;
      border-radius:50%;
      object-fit:cover;
      display:block;
    `
    brandEl.appendChild(brandImg)
    labelRoot.appendChild(brandEl)

    const titleEl = document.createElement('div')
    titleEl.className = 'title'
    titleEl.textContent = actor.displayName
    const stateEl = document.createElement('div')
    stateEl.className = 'state'
    stateEl.textContent = stateText(actor.state)
    const contextEl = document.createElement('div')
    contextEl.className = 'context'
    contextEl.textContent = getContextPercentLabel(actor)
    labelRoot.appendChild(titleEl)
    labelRoot.appendChild(stateEl)
    labelRoot.appendChild(contextEl)

    const headNode = actor.vrm.humanoid?.getNormalizedBoneNode?.('head') || null
    const neckNode = actor.vrm.humanoid?.getNormalizedBoneNode?.('neck') || null
    const chestNode = actor.vrm.humanoid?.getNormalizedBoneNode?.('chest') || null
    const headBone = headNode || neckNode || chestNode || actor.vrm.scene
    const headOffsetY = headNode ? 0.46 : neckNode ? 0.66 : chestNode ? 0.92 : 1.28
    const headAnchor = new THREE.Object3D()
    headAnchor.position.set(0, headOffsetY, 0)
    headBone.add(headAnchor)
    const label = new CSS2DObject(labelRoot)
    headAnchor.add(label)

    actor.labelRoot = labelRoot
    actor.labelTitleEl = titleEl
    actor.labelStateEl = stateEl
    actor.labelContextEl = contextEl
    actor.labelBrandEl = brandEl
    actor.labelAnchor = headAnchor
    actor.labelObject = label
    updateActorHeadLabel(actor)
  }

  function cleanupActorHeadLabel(actor: HeadLabelActor): void {
    if (actor.labelObject?.parent) actor.labelObject.parent.remove(actor.labelObject)
    if (actor.labelAnchor?.parent) actor.labelAnchor.parent.remove(actor.labelAnchor)
    if (actor.labelRoot?.parentElement) actor.labelRoot.parentElement.removeChild(actor.labelRoot)
    actor.labelRoot = null
    actor.labelTitleEl = null
    actor.labelStateEl = null
    actor.labelContextEl = null
    actor.labelBrandEl = null
    actor.labelAnchor = null
    actor.labelObject = null
  }

  return { updateActorHeadLabel, updateAllHeadLabels, setupActorHeadLabel, cleanupActorHeadLabel }
}
