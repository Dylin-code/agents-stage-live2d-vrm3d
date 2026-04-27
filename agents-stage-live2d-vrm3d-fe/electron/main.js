import { app, BrowserWindow, ipcMain } from 'electron'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { applyPlatformWidgetWindowBehavior, buildWidgetWindowOptions } from './platform.js'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

const DEFAULT_WIDGET_URL = 'http://127.0.0.1:5173/desktop-widget'

let widgetWindow = null

function resolveWidgetUrl() {
  return process.env.DESKTOP_WIDGET_URL || process.env.VITE_DEV_SERVER_URL || DEFAULT_WIDGET_URL
}

function createWidgetWindow() {
  const preloadPath = path.join(__dirname, 'preload.cjs')
  widgetWindow = new BrowserWindow(buildWidgetWindowOptions(preloadPath))

  applyPlatformWidgetWindowBehavior(widgetWindow)
  widgetWindow.loadURL(resolveWidgetUrl())
  widgetWindow.on('closed', () => {
    widgetWindow = null
  })
}

app.whenReady().then(() => {
  createWidgetWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWidgetWindow()
    }
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

ipcMain.on('desktop-widget:close', () => {
  widgetWindow?.close()
})

ipcMain.on('desktop-widget:reload', () => {
  widgetWindow?.reload()
})
