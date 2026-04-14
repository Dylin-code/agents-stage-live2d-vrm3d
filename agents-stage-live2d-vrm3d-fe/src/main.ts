// Polyfill TouchEvent for desktop browsers (Firefox etc.) where it is not defined.
// PixiJS InteractionManager uses `instanceof TouchEvent` internally and throws if missing.
if (typeof globalThis.TouchEvent === 'undefined') {
  (globalThis as any).TouchEvent = class TouchEvent extends UIEvent {}
}

import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './style.css'  
import ElementPlus from 'element-plus'
import { bootstrapStageConfigSync, startStageConfigAutoSync } from './utils/stageConfigSync'
import { setupMobileViewportVars } from './utils/mobileViewport'

async function bootstrap(): Promise<void> {
  try {
    await bootstrapStageConfigSync(window.localStorage)
  } catch (error) {
    console.error('Failed to bootstrap stage config sync', error)
  }

  startStageConfigAutoSync(window.localStorage, window)
  setupMobileViewportVars(window, document.documentElement)

  const app = createApp(App)
  app.use(router)
  app.use(ElementPlus)
  app.mount('#app')
}

void bootstrap()
