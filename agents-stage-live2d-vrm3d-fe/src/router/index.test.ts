import { describe, expect, it } from 'vitest'
import { routes } from './index'

describe('router routes', () => {
  it('uses session-stage as default home route', () => {
    const home = routes.find((item) => item.path === '/')
    expect(home?.name).toBe('SessionStage')
  })

  it('keeps legacy /session-stage route for compatibility', () => {
    const stage = routes.find((item) => item.path === '/session-stage')
    expect(stage).toBeTruthy()
  })

  it('registers 3d session stage route', () => {
    const stage3d = routes.find((item) => item.path === '/session-stage-3d')
    expect(stage3d?.name).toBe('SessionStage3D')
  })

  it('removes deprecated routes', () => {
    expect(routes.some((item) => item.path === '/chat')).toBe(false)
    expect(routes.some((item) => item.path === '/medieval-village')).toBe(false)
  })
})
