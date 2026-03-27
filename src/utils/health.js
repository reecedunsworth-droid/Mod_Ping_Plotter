export function deriveHealth({ latency = 0, packetLoss = 0 }) {
  if (packetLoss >= 5 || latency >= 180) return { label: 'Packet Loss Detected', tone: 'critical' };
  if (packetLoss > 0 || latency >= 90) return { label: 'High Latency Detected', tone: 'warning' };
  return { label: 'Connection Stable', tone: 'healthy' };
}

export function toneToColor(tone) {
  if (tone === 'critical') return '#ef4444';
  if (tone === 'warning') return '#eab308';
  return '#22c55e';
}
