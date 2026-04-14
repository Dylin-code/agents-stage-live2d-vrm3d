export interface WindowViewportLike {
  innerHeight: number
  innerWidth: number
  addEventListener: (type: string, listener: EventListenerOrEventListenerObject) => void
  removeEventListener: (type: string, listener: EventListenerOrEventListenerObject) => void
  visualViewport?: {
    height: number
    width: number
    addEventListener: (type: string, listener: EventListenerOrEventListenerObject) => void
    removeEventListener: (type: string, listener: EventListenerOrEventListenerObject) => void
  } | null
}

export interface CssVariableTarget {
  style: {
    setProperty: (name: string, value: string) => void
  }
}

export interface MobileViewportMetrics {
  height: number
  width: number
}

export function measureMobileViewport(win: WindowViewportLike): MobileViewportMetrics {
  const viewport = win.visualViewport
  const height = Math.max(1, Math.round(viewport?.height ?? win.innerHeight))
  const width = Math.max(1, Math.round(viewport?.width ?? win.innerWidth))
  return { height, width }
}

export function applyMobileViewportCssVars(target: CssVariableTarget, metrics: MobileViewportMetrics): void {
  target.style.setProperty('--app-viewport-height', `${metrics.height}px`)
  target.style.setProperty('--app-viewport-width', `${metrics.width}px`)
}

export function setupMobileViewportVars(
  win: WindowViewportLike,
  target: CssVariableTarget,
): () => void {
  const sync = () => {
    applyMobileViewportCssVars(target, measureMobileViewport(win))
  }

  sync()
  win.addEventListener('resize', sync)
  win.addEventListener('orientationchange', sync)
  win.visualViewport?.addEventListener('resize', sync)
  win.visualViewport?.addEventListener('scroll', sync)

  return () => {
    win.removeEventListener('resize', sync)
    win.removeEventListener('orientationchange', sync)
    win.visualViewport?.removeEventListener('resize', sync)
    win.visualViewport?.removeEventListener('scroll', sync)
  }
}
