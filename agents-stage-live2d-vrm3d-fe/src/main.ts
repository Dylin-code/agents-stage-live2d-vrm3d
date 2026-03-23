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

const app = createApp(App)
app.use(router)
app.use(ElementPlus)
app.mount('#app')