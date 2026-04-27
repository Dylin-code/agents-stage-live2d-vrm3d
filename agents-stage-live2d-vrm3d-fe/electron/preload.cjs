const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('desktopWidget', {
  close: () => ipcRenderer.send('desktop-widget:close'),
  reload: () => ipcRenderer.send('desktop-widget:reload'),
})
