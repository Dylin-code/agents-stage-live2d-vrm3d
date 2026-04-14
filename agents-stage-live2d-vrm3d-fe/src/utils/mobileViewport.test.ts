import { describe, expect, it, vi } from 'vitest'

import {
  applyMobileViewportCssVars,
  measureMobileViewport,
  setupMobileViewportVars,
  type CssVariableTarget,
  type WindowViewportLike,
} from './mobileViewport'

function createTarget() {
  const values = new Map<string, string>()
  const target: CssVariableTarget = {
    style: {
      setProperty: (name: string, value: string) => {
        values.set(name, value)
      },
    },
  }
  return { target, values }
}

function createWindowStub(): WindowViewportLike {
  const listeners = new Map<string, Set<EventListenerOrEventListenerObject>>()
  const viewportListeners = new Map<string, Set<EventListenerOrEventListenerObject>>()

  return {
    innerHeight: 844,
    innerWidth: 390,
    visualViewport: {
      height: 712,
      width: 390,
      addEventListener: (type, listener) => {
        const bucket = viewportListeners.get(type) || new Set<EventListenerOrEventListenerObject>()
        bucket.add(listener)
        viewportListeners.set(type, bucket)
      },
      removeEventListener: (type, listener) => {
        viewportListeners.get(type)?.delete(listener)
      },
    },
    addEventListener: (type, listener) => {
      const bucket = listeners.get(type) || new Set<EventListenerOrEventListenerObject>()
      bucket.add(listener)
      listeners.set(type, bucket)
    },
    removeEventListener: (type, listener) => {
      listeners.get(type)?.delete(listener)
    },
  }
}

describe('mobileViewport', () => {
  it('prefers visual viewport dimensions when available', () => {
    const win = createWindowStub()
    expect(measureMobileViewport(win)).toEqual({ height: 712, width: 390 })
  })

  it('writes viewport css variables in pixels', () => {
    const { target, values } = createTarget()
    applyMobileViewportCssVars(target, { height: 700, width: 360 })

    expect(values.get('--app-viewport-height')).toBe('700px')
    expect(values.get('--app-viewport-width')).toBe('360px')
  })

  it('syncs css variables immediately and on viewport resize', () => {
    const win = createWindowStub()
    const viewportResizeSpy = vi.spyOn(win.visualViewport!, 'addEventListener')
    const { target, values } = createTarget()

    const cleanup = setupMobileViewportVars(win, target)
    expect(values.get('--app-viewport-height')).toBe('712px')
    expect(viewportResizeSpy).toHaveBeenCalledWith('resize', expect.any(Function))

    win.visualViewport!.height = 680
    const resizeHandler = viewportResizeSpy.mock.calls.find(([type]) => type === 'resize')?.[1]
    if (typeof resizeHandler === 'function') {
      resizeHandler(new Event('resize'))
    } else if (resizeHandler && 'handleEvent' in resizeHandler) {
      resizeHandler.handleEvent(new Event('resize'))
    }

    expect(values.get('--app-viewport-height')).toBe('680px')
    cleanup()
  })
})
