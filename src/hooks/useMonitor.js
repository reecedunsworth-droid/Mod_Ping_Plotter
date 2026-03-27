import { useEffect, useMemo, useRef, useState } from 'react';
import { deriveHealth } from '../utils/health';
import { saveSnapshot } from '../services/storage';

const api = window.netraApi;

export function useMonitor(targets, intervalMs, running) {
  const [state, setState] = useState({});
  const timerRef = useRef(null);

  const tick = async () => {
    const entries = await Promise.all(
      targets.map(async (target) => {
        try {
          const ping = await api.ping(target);
          const previous = state[target]?.series ?? [];
          const newPoint = {
            time: new Date().toLocaleTimeString(),
            latency: ping.latency ?? 0,
            packetLoss: ping.packetLoss ?? 0
          };
          const nextSeries = [...previous, newPoint].slice(-90);
          const jitter = nextSeries.length > 1 ? Math.abs(nextSeries.at(-1).latency - nextSeries.at(-2).latency) : 0;
          const health = deriveHealth({ latency: ping.latency, packetLoss: ping.packetLoss ?? 0 });

          const snapshot = {
            timestamp: new Date().toISOString(),
            target,
            latency: ping.latency,
            packetLoss: ping.packetLoss ?? 0,
            jitter
          };
          saveSnapshot(snapshot);

          return [
            target,
            {
              latest: ping,
              jitter,
              health,
              series: nextSeries,
              issueDetected: health.tone !== 'healthy'
            }
          ];
        } catch (error) {
          return [
            target,
            {
              latest: { latency: null, packetLoss: 100, raw: error.message },
              jitter: 0,
              health: { label: 'Packet Loss Detected', tone: 'critical' },
              series: state[target]?.series ?? []
            }
          ];
        }
      })
    );

    setState((prev) => ({ ...prev, ...Object.fromEntries(entries) }));
  };

  useEffect(() => {
    if (!running || !targets.length) return;
    tick();
    timerRef.current = setInterval(tick, intervalMs);
    return () => clearInterval(timerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, intervalMs, targets.join(',')]);

  return useMemo(() => state, [state]);
}
