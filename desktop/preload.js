// What the page may ask of the app: open a page in the system browser (where the reader is already signed in to
// OpenRouter, ChatGPT and the rest), save a file where the reader chooses, know that it runs inside the app and which
// version it is, follow the app's updates (main.js), which the page shows, and fill the screen.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktop', {
  openExternal: (url) => ipcRenderer.invoke('open-external', String(url)),
  saveFile: (name, data) => ipcRenderer.invoke('save-file', String(name), String(data)),
  chooseLibraryFolder: () => ipcRenderer.invoke('storage:choose-folder'),
  version: () => ipcRenderer.invoke('app:version'),
  updateState: () => ipcRenderer.invoke('update:state'),
  checkForUpdates: () => ipcRenderer.invoke('update:check'),
  downloadUpdate: () => ipcRenderer.invoke('update:download'),
  installUpdate: () => ipcRenderer.invoke('update:install'),
  dismissUpdate: () => ipcRenderer.invoke('update:dismiss'),
  onUpdate: (cb) => { ipcRenderer.on('update', (_e, s) => cb(s)); },
  fullScreen: (on) => ipcRenderer.invoke('window:fullscreen', on),
  onFullScreen: (cb) => { ipcRenderer.on('fullscreen', (_e, on) => cb(on)); },
});
