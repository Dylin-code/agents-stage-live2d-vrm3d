import * as THREE from 'three'
import type { InteractionPoint, InteractionPointData } from './vrmInteractionPointUtils'

const MARKER_COLORS: Record<string, number> = {
  sit: 0x4a9eff,
  work: 0x4adf6a,
  'stand-idle': 0xaaaaaa,
}
const DEFAULT_MARKER_COLOR = 0xffaa44
const SELECTED_MARKER_COLOR = 0xff4488
const APPROACH_MARKER_COLOR = 0xffd36e
const MARKER_RADIUS = 0.07
const APPROACH_MARKER_RADIUS = 0.045
const ARROW_LENGTH = 0.25

export function createVrmInteractionPointEditorUtils(args: {
  getScene: () => THREE.Scene | null
  getInteractionPoints: () => InteractionPoint[]
  getSelectedPointId: () => string | null
  getDraftPoint?: () => InteractionPointData | null
}) {
  const { getScene, getInteractionPoints, getSelectedPointId, getDraftPoint } = args

  const markers: THREE.Mesh[] = []
  const approachMarkers: THREE.Mesh[] = []
  const lines: THREE.Line[] = []
  const arrows: THREE.ArrowHelper[] = []

  function clearVisuals(): void {
    const scene = getScene()
    if (!scene) return
    for (const m of markers) {
      scene.remove(m)
      m.geometry.dispose()
      ;(m.material as THREE.Material).dispose()
    }
    markers.length = 0
    for (const m of approachMarkers) {
      scene.remove(m)
      m.geometry.dispose()
      ;(m.material as THREE.Material).dispose()
    }
    approachMarkers.length = 0
    for (const l of lines) {
      scene.remove(l)
      l.geometry.dispose()
      ;(l.material as THREE.Material).dispose()
    }
    lines.length = 0
    for (const a of arrows) {
      scene.remove(a)
      a.dispose()
    }
    arrows.length = 0
  }

  function refreshVisuals(): void {
    const scene = getScene()
    if (!scene) return
    clearVisuals()

    const points = getInteractionPoints()
    const selectedId = getSelectedPointId()
    const markerGeo = new THREE.SphereGeometry(MARKER_RADIUS, 12, 10)
    const approachGeo = new THREE.SphereGeometry(APPROACH_MARKER_RADIUS, 10, 8)
    const draftPoint = getDraftPoint?.() ?? null

    function renderPoint(point: InteractionPoint | InteractionPointData, isSelected: boolean): void {
      const color = isSelected
        ? SELECTED_MARKER_COLOR
        : (MARKER_COLORS[point.action.type] ?? DEFAULT_MARKER_COLOR)

      // 互動點主標記
      const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.85 })
      const marker = new THREE.Mesh(markerGeo.clone(), mat)
      marker.position.set(point.position.x, point.position.y ?? 0.05, point.position.z)
      marker.userData.interactionPointId = point.id
      scene.add(marker)
      markers.push(marker)

      // 接近點標記
      const approachMat = new THREE.MeshBasicMaterial({ color: APPROACH_MARKER_COLOR, transparent: true, opacity: 0.75 })
      const approachMesh = new THREE.Mesh(approachGeo.clone(), approachMat)
      approachMesh.position.set(point.approachPosition.x, point.approachPosition.y ?? 0.03, point.approachPosition.z)
      scene.add(approachMesh)
      approachMarkers.push(approachMesh)

      // 連線
      const lineGeo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(point.position.x, point.position.y ?? 0.04, point.position.z),
        new THREE.Vector3(point.approachPosition.x, point.approachPosition.y ?? 0.04, point.approachPosition.z),
      ])
      const lineMat = new THREE.LineBasicMaterial({ color: 0x999999, transparent: true, opacity: 0.5 })
      const line = new THREE.Line(lineGeo, lineMat)
      scene.add(line)
      lines.push(line)

      // 方向箭頭（從 approach 點朝互動點方向）
      const dir = new THREE.Vector3(
        Math.sin(point.approachRotationY),
        0,
        Math.cos(point.approachRotationY),
      ).normalize()
      const origin = new THREE.Vector3(
        point.approachPosition.x,
        (point.approachPosition.y ?? 0.06) + 0.03,
        point.approachPosition.z,
      )
      const arrow = new THREE.ArrowHelper(dir, origin, ARROW_LENGTH, 0xff6644, 0.08, 0.05)
      scene.add(arrow)
      arrows.push(arrow)
    }

    for (const point of points) {
      const isSelected = point.id === selectedId
      const displayPoint = draftPoint && draftPoint.id === point.id ? draftPoint : point
      renderPoint(displayPoint, isSelected)
    }

    if (draftPoint && draftPoint.id === selectedId && !points.some((point) => point.id === draftPoint.id)) {
      renderPoint(draftPoint, true)
    }
  }

  function pickPointByRay(
    raycaster: THREE.Raycaster,
  ): string | null {
    if (!markers.length) return null
    const intersections = raycaster.intersectObjects(markers, false)
    if (!intersections.length) return null
    const hit = intersections[0]
    return (hit.object.userData.interactionPointId as string) || null
  }

  return {
    refreshVisuals,
    clearVisuals,
    pickPointByRay,
  }
}
