export default function TracerouteView({ hops }) {
  return (
    <div className="panel">
      <h3 className="mb-4 text-lg font-semibold">Traceroute Hops</h3>
      <div className="space-y-2">
        {hops.map((hop, idx) => {
          const tone = hop.latency == null ? 'bg-slate-700' : hop.latency > 180 ? 'bg-rose-500' : hop.latency > 90 ? 'bg-amber-400' : 'bg-emerald-500';
          return (
            <div key={`${hop.host}-${idx}`} className="rounded-lg border border-slate-700 p-2">
              <div className="mb-1 flex justify-between text-sm">
                <span className="text-slate-300">
                  #{hop.hop} {hop.host}
                </span>
                <span className="text-slate-400">{hop.latency ?? '—'} ms</span>
              </div>
              <div className="h-2 rounded-full bg-slate-800">
                <div className={`h-2 rounded-full ${tone}`} style={{ width: `${Math.min((hop.latency ?? 0) / 3, 100)}%` }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
