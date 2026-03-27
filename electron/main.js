const { app, BrowserWindow, ipcMain } = require('electron');
const { exec } = require('node:child_process');
const dns = require('node:dns').promises;
const https = require('node:https');
const net = require('node:net');
const path = require('node:path');
const speedTest = require('speedtest-net');

const isDev = !app.isPackaged;

function createWindow() {
  const win = new BrowserWindow({
    width: 1500,
    height: 920,
    minWidth: 1200,
    minHeight: 760,
    backgroundColor: '#020617',
    title: 'Netra',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true
    }
  });

  if (isDev) {
    win.loadURL('http://localhost:5173');
  } else {
    win.loadFile(path.join(__dirname, '../dist/index.html'));
  }

  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', (event, navigationUrl) => {
    const allowedOrigin = isDev ? 'http://localhost:5173' : 'file://';
    if (!navigationUrl.startsWith(allowedOrigin)) {
      event.preventDefault();
    }
  });
}

function runCommand(command, timeout = 20000) {
  return new Promise((resolve, reject) => {
    exec(command, { timeout }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(stderr || error.message));
        return;
      }
      resolve(stdout);
    });
  });
}

function parsePing(target, output) {
  const latency = output.match(/time[=<]([\d.]+)\s*ms/i) || output.match(/Average = ([\d.]+)ms/i);
  const loss = output.match(/(\d+(?:\.\d+)?)%\s*packet loss/i) || output.match(/Lost = \d+ \((\d+)% loss\)/i);
  return {
    target,
    latency: latency ? Number(latency[1]) : null,
    packetLoss: loss ? Number(loss[1]) : null,
    raw: output
  };
}

function parseTraceroute(output) {
  return output
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, i) => {
      const msValues = [...line.matchAll(/([\d.]+)\s*ms/g)].map((m) => Number(m[1]));
      const hostMatch = line.match(/^\s*\d+\s+([^\s]+)/);
      return {
        hop: i,
        host: hostMatch ? hostMatch[1] : line,
        latency: msValues.length ? Math.round(msValues.reduce((a, b) => a + b, 0) / msValues.length) : null,
        raw: line
      };
    })
    .filter((entry) => entry.hop > 0);
}

function getPublicIpInfo() {
  return new Promise((resolve, reject) => {
    https
      .get('https://ipapi.co/json/', (res) => {
        let data = '';
        res.on('data', (chunk) => {
          data += chunk;
        });
        res.on('end', () => {
          try {
            const payload = JSON.parse(data);
            resolve({
              ip: payload.ip,
              isp: payload.org,
              city: payload.city,
              region: payload.region,
              country: payload.country_name
            });
          } catch (error) {
            reject(error);
          }
        });
      })
      .on('error', reject);
  });
}

ipcMain.handle('diag:ping', async (_, { target }) => {
  const command = process.platform === 'win32' ? `ping -n 1 -w 1000 ${target}` : `ping -c 1 -W 1 ${target}`;
  const output = await runCommand(command, 5000);
  return parsePing(target, output);
});

ipcMain.handle('diag:traceroute', async (_, { target }) => {
  const command = process.platform === 'win32' ? `tracert -d ${target}` : `traceroute -n ${target}`;
  const output = await runCommand(command, 60000);
  return parseTraceroute(output);
});

ipcMain.handle('diag:dnsLookup', async (_, { target }) => {
  const [lookup, any] = await Promise.allSettled([dns.lookup(target), dns.resolveAny(target)]);
  return {
    lookup: lookup.status === 'fulfilled' ? lookup.value : null,
    records: any.status === 'fulfilled' ? any.value : [],
    error: lookup.status === 'rejected' && any.status === 'rejected' ? 'DNS lookup failed' : null
  };
});

ipcMain.handle('diag:testPort', async (_, { target, port, timeout = 2500 }) => {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    const started = Date.now();

    socket.setTimeout(timeout);
    socket
      .connect(port, target, () => {
        resolve({ open: true, latency: Date.now() - started, port });
        socket.destroy();
      })
      .on('timeout', () => {
        resolve({ open: false, reason: 'timeout', port });
        socket.destroy();
      })
      .on('error', (error) => {
        resolve({ open: false, reason: error.message, port });
      });
  });
});

ipcMain.handle('diag:publicIp', getPublicIpInfo);

ipcMain.handle('diag:speedTest', async () => {
  try {
    const result = await speedTest({ acceptLicense: true, acceptGdpr: true });
    return {
      ok: true,
      downloadMbps: Number((result.download.bandwidth * 8e-6).toFixed(2)),
      uploadMbps: Number((result.upload.bandwidth * 8e-6).toFixed(2)),
      latencyMs: Number(result.ping.latency.toFixed(2)),
      jitterMs: Number((result.ping.jitter || 0).toFixed(2)),
      server: result.server?.name
    };
  } catch (error) {
    return {
      ok: false,
      error:
        'Speed test unavailable in this network environment. Verify internet/proxy settings, or skip speed test for now.',
      details: error.message
    };
  }
});

app.whenReady().then(createWindow);
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
