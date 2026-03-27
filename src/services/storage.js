const KEY = 'netra-history-v1';

export function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '[]');
  } catch {
    return [];
  }
}

export function saveSnapshot(snapshot) {
  const history = loadHistory();
  history.unshift(snapshot);
  localStorage.setItem(KEY, JSON.stringify(history.slice(0, 100)));
}

export function exportHistory(format = 'json') {
  const history = loadHistory();
  if (format === 'csv') {
    const rows = ['timestamp,target,latency,packetLoss,jitter'];
    history.forEach((h) => rows.push(`${h.timestamp},${h.target},${h.latency},${h.packetLoss},${h.jitter}`));
    return rows.join('\n');
  }
  return JSON.stringify(history, null, 2);
}
