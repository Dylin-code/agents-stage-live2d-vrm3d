export function applyMacWidgetWindowBehavior(window) {
  if (process.platform !== 'darwin') return
  window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
  window.setAlwaysOnTop(true, 'floating')
  window.setFullScreenable(false)
}
