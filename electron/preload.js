const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('netraApi', {
  ping: (target) => ipcRenderer.invoke('diag:ping', { target }),
  traceroute: (target) => ipcRenderer.invoke('diag:traceroute', { target }),
  dnsLookup: (target) => ipcRenderer.invoke('diag:dnsLookup', { target }),
  testPort: (target, port, timeout) => ipcRenderer.invoke('diag:testPort', { target, port, timeout }),
  publicIp: () => ipcRenderer.invoke('diag:publicIp'),
  speedTest: () => ipcRenderer.invoke('diag:speedTest')
});
