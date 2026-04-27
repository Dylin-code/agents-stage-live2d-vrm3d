import { app, BrowserWindow, ipcMain } from 'electron'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { applyMacWidgetWindowBehavior } from './platform.js'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

const DEFAULT_WIDGET_URL = 'http://127.0.0.1:5173/desktop-widget'

let widgetWindow = null

function resolveWidgetUrl() {
  return process.env.DESKTOP_WIDGET_URL || process.env.VITE_DEV_SERVER_URL || DEFAULT_WIDGET_URL
}

function createWidgetWindow() {
  widgetWindow = new BrowserWindow({
    width: 360,
    height: 520,
    minWidth: 300,
    minHeight: 420,
    transparent: true,
    frame: false,
    resizable: true,
    hasShadow: false,
    alwaysOnTop: true,
    backgroundColor: '#00000000',
    title: 'Agents Desktop Widget',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  })

  applyMacWidgetWindowBehavior(widgetWindow)
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
