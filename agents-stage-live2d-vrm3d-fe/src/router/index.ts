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
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
