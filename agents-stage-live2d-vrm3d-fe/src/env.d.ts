/// <reference types="vite/client" />

interface Window {
  desktopWidget?: {
    close: () => void
    reload: () => void
  }
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
} 
