import { useMemo, useState } from 'react';
import Sidebar from './components/Sidebar';
import TopBar from './components/TopBar';
import StatusCard from './components/StatusCard';
import LatencyChart from './components/LatencyChart';
import ExpandablePanel from './components/ExpandablePanel';
import TracerouteView from './components/TracerouteView';
import { useMonitor } from './hooks/useMonitor';
import { exportHistory, loadHistory } from './services/storage';

const api = window.netraApi;

export default function App() {
  const [activePage, setActivePage] = useState('Dashboard');
  const [input, setInput] = useState('1.1.1.1');
  const [targets, setTargets] = useState(['1.1.1.1']);
  const [selectedTarget, setSelectedTarget] = useState('1.1.1.1');
  const [intervalMs, setIntervalMs] = useState(1000);
  const [running, setRunning] = useState(true);
  const [advanced, setAdvanced] = useState(false);
  const [expanded, setExpanded] = useState({ traceroute: true, tools: true });
  const [traceroute, setTraceroute] = useState([]);
  const [dnsResult, setDnsResult] = useState(null);
  const [portResult, setPortResult] = useState(null);
  const [publicIp, setPublicIp] = useState(null);
  const [speed, setSpeed] = useState(null);

  const monitor = useMonitor(targets, intervalMs, running);
  const selected = monitor[selectedTarget] || { latest: {}, series: [], health: { label: 'Awaiting data', tone: 'warning' }, jitter: 0 };

  const addTarget = () => {
    const value = input.trim();
    if (!value || targets.includes(value)) return;
    setTargets((prev) => [...prev, value]);
    setSelectedTarget(value);
  };

  const removeTarget = (target) => {
    setTargets((prev) => prev.filter((t) => t !== target));
    if (selectedTarget === target && targets.length > 1) {
      const fallback = targets.find((t) => t !== target);
      setSelectedTarget(fallback);
    }
  };

  const issueRecommendation = useMemo(() => {
    if (selected.health.tone === 'critical') return 'Issue detected. Run traceroute to isolate unstable hop.';
    if (selected.health.tone === 'warning') return 'Performance drift detected. Monitor trend or run traceroute.';
    return 'Connection is healthy.';
  }, [selected.health.tone]);

  const runTraceroute = async () => {
    try {
      const hops = await api.traceroute(selectedTarget);
      setTraceroute(hops);
    } catch (error) {
      setTraceroute([
        {
          hop: 1,
          host: 'Traceroute failed',
          latency: null,
          raw: error.message
        }
      ]);
    }
  };

  const runDiagnostics = async () => {
    const [dns, ip, speedResult, port] = await Promise.allSettled([
      api.dnsLookup(selectedTarget),
      api.publicIp(),
      api.speedTest(),
      api.testPort(selectedTarget, 443)
    ]);
    setDnsResult(dns.status === 'fulfilled' ? dns.value : { error: dns.reason?.message ?? 'DNS lookup failed' });
    setPublicIp(ip.status === 'fulfilled' ? ip.value : { error: ip.reason?.message ?? 'Public IP lookup failed' });
    setSpeed(speedResult.status === 'fulfilled' ? speedResult.value : { ok: false, error: speedResult.reason?.message ?? 'Speed test failed' });
    setPortResult(port.status === 'fulfilled' ? port.value : { error: port.reason?.message ?? 'Port test failed' });
  };

  const downloadHistory = (format) => {
    const payload = exportHistory(format);
    const blob = new Blob([payload], { type: format === 'csv' ? 'text/csv' : 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `netra-history.${format}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex h-screen bg-slate-950 text-slate-100">
      <Sidebar active={activePage} onChange={setActivePage} />
      <main className="flex-1 overflow-auto p-5">
        <TopBar
          input={input}
          setInput={setInput}
          addTarget={addTarget}
          targets={targets}
          removeTarget={removeTarget}
          intervalMs={intervalMs}
          setIntervalMs={setIntervalMs}
          running={running}
          setRunning={setRunning}
        />

        <div className="mb-4 flex flex-wrap items-center gap-2">
          {targets.map((target) => (
            <button
              key={target}
              onClick={() => setSelectedTarget(target)}
              className={`rounded-lg px-3 py-1.5 text-sm ${selectedTarget === target ? 'bg-cyan-500/20 text-cyan-300' : 'bg-slate-800 text-slate-300'}`}
            >
              {target}
            </button>
          ))}
          <button onClick={() => setAdvanced((v) => !v)} className="ml-auto rounded-lg bg-slate-800 px-3 py-1.5 text-sm hover:bg-slate-700">
            {advanced ? 'Simple Mode' : 'Advanced Mode'}
          </button>
        </div>

        <section className="grid gap-4 lg:grid-cols-4">
          <StatusCard title="Health" value={selected.health.label} tone={selected.health.tone} subtext={issueRecommendation} />
          <StatusCard title="Latency" value={`${selected.latest.latency ?? '—'} ms`} tone={selected.health.tone} />
          <StatusCard title="Packet Loss" value={`${selected.latest.packetLoss ?? 0}%`} tone={selected.health.tone} />
          <StatusCard title="Jitter" value={`${selected.jitter ?? 0} ms`} tone={selected.health.tone} />
        </section>

        <div className="mt-4 grid gap-4 xl:grid-cols-3">
          <div className="xl:col-span-2">
            <LatencyChart data={selected.series} />
          </div>
          <div className="panel">
            <h3 className="mb-2 text-lg font-semibold">Smart Actions</h3>
            <p className="mb-3 text-sm text-slate-300">{issueRecommendation}</p>
            <button onClick={runTraceroute} className="w-full rounded-lg bg-indigo-600 px-3 py-2 hover:bg-indigo-500">
              Run Traceroute
            </button>
            <button onClick={runDiagnostics} className="mt-2 w-full rounded-lg bg-cyan-600 px-3 py-2 hover:bg-cyan-500">
              Run Full Toolset
            </button>
          </div>
        </div>

        {advanced ? (
          <div className="mt-4 space-y-4">
            <ExpandablePanel title="Traceroute" open={expanded.traceroute} onToggle={() => setExpanded((e) => ({ ...e, traceroute: !e.traceroute }))}>
              <TracerouteView hops={traceroute} />
            </ExpandablePanel>

            <ExpandablePanel title="Tools Output" open={expanded.tools} onToggle={() => setExpanded((e) => ({ ...e, tools: !e.tools }))}>
              <div className="grid gap-3 md:grid-cols-2">
                <pre className="rounded-lg bg-slate-950 p-3 text-xs text-slate-300">{JSON.stringify(dnsResult, null, 2) || 'Run diagnostics to load DNS lookup'}</pre>
                <pre className="rounded-lg bg-slate-950 p-3 text-xs text-slate-300">{JSON.stringify(speed, null, 2) || 'Speed test output pending'}</pre>
                <pre className="rounded-lg bg-slate-950 p-3 text-xs text-slate-300">{JSON.stringify(portResult, null, 2) || 'Port tester output pending'}</pre>
                <pre className="rounded-lg bg-slate-950 p-3 text-xs text-slate-300">{JSON.stringify(publicIp, null, 2) || 'Public IP output pending'}</pre>
              </div>
            </ExpandablePanel>
          </div>
        ) : null}

        {activePage === 'History' ? (
          <section className="panel mt-4">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-lg font-semibold">Session History</h3>
              <div className="space-x-2">
                <button onClick={() => downloadHistory('json')} className="rounded-md bg-slate-800 px-3 py-1.5 text-sm hover:bg-slate-700">
                  Export JSON
                </button>
                <button onClick={() => downloadHistory('csv')} className="rounded-md bg-slate-800 px-3 py-1.5 text-sm hover:bg-slate-700">
                  Export CSV
                </button>
              </div>
            </div>
            <div className="max-h-80 overflow-auto rounded-lg border border-slate-800">
              <table className="w-full text-left text-sm">
                <thead className="sticky top-0 bg-slate-900 text-slate-300">
                  <tr>
                    <th className="p-2">Timestamp</th>
                    <th className="p-2">Target</th>
                    <th className="p-2">Latency</th>
                    <th className="p-2">Loss</th>
                    <th className="p-2">Jitter</th>
                  </tr>
                </thead>
                <tbody>
                  {loadHistory().map((row, i) => (
                    <tr key={`${row.timestamp}-${i}`} className="border-t border-slate-800">
                      <td className="p-2">{new Date(row.timestamp).toLocaleString()}</td>
                      <td className="p-2">{row.target}</td>
                      <td className="p-2">{row.latency}</td>
                      <td className="p-2">{row.packetLoss}%</td>
                      <td className="p-2">{row.jitter}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}
      </main>
    </div>
  );
}
