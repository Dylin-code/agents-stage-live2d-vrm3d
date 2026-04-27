function isMac() {
  return process.platform === 'darwin'
}

function isWindows() {
  return process.platform === 'win32'
}

export function buildWidgetWindowOptions(preloadPath) {
  const shared = {
    width: 360,
    height: 520,
    minWidth: 260,
    minHeight: 360,
    frame: false,
    resizable: true,
    alwaysOnTop: true,
    title: 'Agents Desktop Widget',
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  }

  if (isWindows()) {
    return {
      ...shared,
      transparent: false,
      hasShadow: true,
      backgroundColor: '#0f1722',
    }
  }

  return {
    ...shared,
    transparent: true,
    hasShadow: false,
    backgroundColor: '#00000000',
  }
}

export function applyPlatformWidgetWindowBehavior(window) {
  window.setMenuBarVisibility(false)

  if (isMac()) {
    window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
    window.setAlwaysOnTop(true, 'floating')
    window.setFullScreenable(false)
    return
  }

  if (isWindows()) {
    window.setAlwaysOnTop(true, 'pop-up-menu')
  }
}
