import * as THREE from 'three'
import type { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

export interface StageObstacle {
  minX: number
  maxX: number
  minZ: number
  maxZ: number
}

export function createVrmSceneSetupUtils(args: {
  getScene: () => THREE.Scene | null
  getLoader: () => GLTFLoader | null
  stageSceneUrl: string
  targetStageMaxSize: number
  obstacleCollisionPadding: number
  shadowMapSize: number
  getStageSceneRoot: () => THREE.Object3D | null
  setStageSceneRoot: (value: THREE.Object3D | null) => void
  setStageSeatCenter: (value: THREE.Vector3 | null) => void
  setStageSeatSpread: (value: { x: number; z: number }) => void
  setStageCameraTarget: (value: THREE.Vector3 | null) => void
  setStageCameraPosition: (value: THREE.Vector3 | null) => void
  setStageWorldCenter: (value: THREE.Vector3 | null) => void
  setStageWorldSize: (value: THREE.Vector3 | null) => void
  getStageWorldSize: () => THREE.Vector3 | null
  getStageWorldCenter: () => THREE.Vector3 | null
  stageObstacles: StageObstacle[]
  sceneLights: THREE.Light[]
  sceneLightTargets: THREE.Object3D[]
}) {
  const {
    getScene,
    getLoader,
    stageSceneUrl,
    targetStageMaxSize,
    obstacleCollisionPadding,
    shadowMapSize,
    getStageSceneRoot,
    setStageSceneRoot,
    setStageSeatCenter,
    setStageSeatSpread,
    setStageCameraTarget,
    setStageCameraPosition,
    setStageWorldCenter,
    setStageWorldSize,
    getStageWorldSize,
    getStageWorldCenter,
    stageObstacles,
    sceneLights,
    sceneLightTargets,
  } = args

  function rebuildStageObstacles(): void {
    stageObstacles.length = 0
    const stageSceneRoot = getStageSceneRoot()
    if (!stageSceneRoot) return

    const stageSize = getStageWorldSize() || new THREE.Vector3(12, 6, 12)
    const box = new THREE.Box3()
    const size = new THREE.Vector3()

    stageSceneRoot.updateMatrixWorld(true)
    stageSceneRoot.traverse((node: THREE.Object3D) => {
      const mesh = node as THREE.Mesh
      if (!mesh.isMesh) return
      box.setFromObject(mesh)
      if (!Number.isFinite(box.min.x) || !Number.isFinite(box.max.x)) return
      box.getSize(size)
      if (size.x < 0.08 || size.z < 0.08 || size.y < 0.2) return
      const footprint = size.x * size.z
      if (footprint < 0.45) return
      if (size.y < 0.35 && box.max.y < 0.38) return
      if (box.min.y > 1.8) return
      if (size.x > stageSize.x * 0.75 && size.z > stageSize.z * 0.75) return
      stageObstacles.push({
        minX: box.min.x - obstacleCollisionPadding,
        maxX: box.max.x + obstacleCollisionPadding,
        minZ: box.min.z - obstacleCollisionPadding,
        maxZ: box.max.z + obstacleCollisionPadding,
      })
    })
  }

  async function loadStageScene(): Promise<void> {
    const scene = getScene()
    const loader = getLoader()
    if (!scene || !loader) return

    const gltf = await new Promise<any>((resolve, reject) => {
      loader.load(stageSceneUrl, resolve, undefined, reject)
    })
    const stageSceneRoot = gltf.scene || null
    setStageSceneRoot(stageSceneRoot)
    if (!stageSceneRoot) return

    const rawBounds = new THREE.Box3().setFromObject(stageSceneRoot)
    const rawSize = rawBounds.getSize(new THREE.Vector3())
    const maxRawSize = Math.max(rawSize.x, rawSize.y, rawSize.z)
    const normalizeScale = maxRawSize > 0 ? targetStageMaxSize / maxRawSize : 1
    stageSceneRoot.scale.setScalar(normalizeScale)
    stageSceneRoot.updateMatrixWorld(true)

    const normalizedBounds = new THREE.Box3().setFromObject(stageSceneRoot)
    const normalizedSize = normalizedBounds.getSize(new THREE.Vector3())
    const normalizedCenter = normalizedBounds.getCenter(new THREE.Vector3())
    stageSceneRoot.position.set(-normalizedCenter.x, -normalizedBounds.min.y, -normalizedCenter.z)
    stageSceneRoot.updateMatrixWorld(true)

    const placedBounds = new THREE.Box3().setFromObject(stageSceneRoot)
    const placedSize = placedBounds.getSize(new THREE.Vector3())
    const placedCenter = placedBounds.getCenter(new THREE.Vector3())

    setStageSeatCenter(new THREE.Vector3(placedCenter.x, 0, placedCenter.z + placedSize.z * 0.18))
    setStageSeatSpread({
      x: Math.max(0.55, Math.min(1.5, placedSize.x * 0.12)),
      z: Math.max(0.4, Math.min(1.1, placedSize.z * 0.08)),
    })
    setStageCameraTarget(new THREE.Vector3(placedCenter.x, placedSize.y * 0.33, placedCenter.z))
    setStageCameraPosition(new THREE.Vector3(placedCenter.x, placedSize.y * 0.55, placedCenter.z + placedSize.z * 0.95))
    setStageWorldCenter(placedCenter.clone())
    setStageWorldSize(placedSize.clone())

    stageSceneRoot.traverse((node: THREE.Object3D) => {
      const mesh = node as THREE.Mesh
      if (!mesh.isMesh) return
      mesh.castShadow = true
      mesh.receiveShadow = true
      const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
      for (const material of materials) {
        if (!material) continue
        const anyMaterial = material as any
        if (anyMaterial.map) {
          anyMaterial.map.colorSpace = THREE.SRGBColorSpace
        }
        anyMaterial.needsUpdate = true
      }
    })

    stageSceneRoot.name = 'classroom-stage'
    scene.add(stageSceneRoot)
    rebuildStageObstacles()
  }

  function setupLights(): void {
    const scene = getScene()
    if (!scene) return
    for (const light of sceneLights) {
      scene.remove(light)
    }
    for (const target of sceneLightTargets) {
      scene.remove(target)
    }
    sceneLights.length = 0
    sceneLightTargets.length = 0

    const center = getStageWorldCenter() || new THREE.Vector3(0, 1.2, 0)
    const size = getStageWorldSize() || new THREE.Vector3(12, 6, 12)
    const span = Math.max(size.x, size.y, size.z)
    const ambientLight = new THREE.AmbientLight(0xf6f0e3, 0.2)
    const hemiLight = new THREE.HemisphereLight(0xcfe3ff, 0x8f6f4d, 0.22)
    const sunLight = new THREE.DirectionalLight(0xffedc9, 2.25)
    sunLight.position.set(center.x - size.x * 0.65, center.y + size.y * 1.2, center.z - size.z * 0.95)
    sunLight.target.position.set(center.x + size.x * 0.24, center.y + size.y * 0.2, center.z + size.z * 0.18)
    sunLight.castShadow = true
    sunLight.shadow.mapSize.set(shadowMapSize, shadowMapSize)
    sunLight.shadow.bias = -0.00035
    sunLight.shadow.normalBias = 0.02
    sunLight.shadow.camera.near = 0.5
    sunLight.shadow.camera.far = span * 4
    sunLight.shadow.camera.left = -span * 1.3
    sunLight.shadow.camera.right = span * 1.3
    sunLight.shadow.camera.top = span * 1.3
    sunLight.shadow.camera.bottom = -span * 1.3
    const fillLight = new THREE.DirectionalLight(0xbfd8ff, 0.35)
    fillLight.position.set(center.x + size.x * 0.72, center.y + size.y * 0.72, center.z + size.z * 0.84)
    fillLight.target.position.set(center.x, center.y + size.y * 0.16, center.z)
    scene.add(ambientLight, hemiLight, sunLight, sunLight.target, fillLight, fillLight.target)
    sceneLights.push(ambientLight, hemiLight, sunLight, fillLight)
    sceneLightTargets.push(sunLight.target, fillLight.target)
  }

  return {
    rebuildStageObstacles,
    loadStageScene,
    setupLights,
  }
}
