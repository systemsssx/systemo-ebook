const { contextBridge, ipcRenderer } = require('electron');

// ★ 语言桥（内联，不要 require 别的文件）：Electron 27 的 preload 跑在沙箱里，
//   沙箱内 require 只能拿到 electron/events/timers/url，连 './xxx.js' 都会报
//   "module not found"，所以这里必须自带这段实现。
contextBridge.exposeInMainWorld('pageLang', {
    get: () => ipcRenderer.invoke('settings-get'),
    set: (lang) => ipcRenderer.invoke('settings-set', { language: lang }),
    onChange: (callback) => ipcRenderer.on('apply-language', (event, lang) => callback(lang))
});
