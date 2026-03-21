import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

export const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'SessionStage',
    component: () => import('../pages/SessionStage.vue')
  },
  {
    path: '/session-stage',
    name: 'SessionStageLegacy',
    component: () => import('../pages/SessionStage.vue')
  },
  {
    path: '/session-stage-3d',
    name: 'SessionStage3D',
    component: () => import('../pages/SessionStage3D.vue')
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('../pages/Login.vue')
  }
]

/**
 * Check authentication by calling /api/auth/me.
 * - Returns true if allowed to proceed.
 * - Returns '/login' if authentication is required but missing.
 * - In local mode /api/auth/me doesn't exist, so fetch throws -> allow through.
 */
export async function checkAuth(targetPath: string): Promise<true | string> {
  if (targetPath === '/login') return true
  try {
    const res = await fetch('/api/auth/me')
    if (res.ok) return true
    if (res.status === 401) return '/login'
    // Other errors (e.g. 404 in local mode) -> allow through
    return true
  } catch {
    // Network error or /api/auth/me not found (local mode) -> allow through
    return true
  }
}

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach(async (to) => {
  return await checkAuth(to.path)
})

export default router
