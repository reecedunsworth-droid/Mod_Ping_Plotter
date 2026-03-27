# Netra

Netra is a premium dark-mode desktop network diagnostics dashboard built with **Electron + React + Node.js**.

## Features

- Continuous ping monitoring with adjustable interval.
- Real-time latency chart with packet loss awareness.
- Smart health status detection (stable/warning/critical).
- On-demand traceroute and full diagnostics toolset.
- DNS lookup, TCP port testing, public IP/ISP detection.
- Speed test (download/upload/latency/jitter).
- Multi-target monitoring with target tabs.
- Session history storage and JSON/CSV export.
- Dual experience: **Simple Mode** and **Advanced Mode** with expandable sections.

## Run locally

```bash
npm install
npm run dev
```

`npm run dev` starts Vite and waits for `http://localhost:5173` before launching Electron.

## Architecture

- `electron/`: Main process + secure preload bridge.
- `src/components/`: Modular UI components.
- `src/hooks/`: Real-time monitor hook.
- `src/services/`: Persistence and app services.
- `src/utils/`: Health/tone logic utilities.
