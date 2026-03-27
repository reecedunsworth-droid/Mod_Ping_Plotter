import { Play, Plus, X } from 'lucide-react';

export default function TopBar({ input, setInput, addTarget, targets, removeTarget, intervalMs, setIntervalMs, running, setRunning }) {
  return (
    <div className="panel mb-4 flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
      <div className="flex flex-1 gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Enter IP or domain"
          className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-4 py-2 focus:border-cyan-500 focus:outline-none"
        />
        <button onClick={addTarget} className="inline-flex items-center gap-2 rounded-lg bg-cyan-600 px-4 py-2 font-medium hover:bg-cyan-500">
          <Plus size={16} /> Add
        </button>
        <button
          onClick={() => setRunning((r) => !r)}
          className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 font-medium hover:bg-emerald-500"
        >
          <Play size={16} /> {running ? 'Pause' : 'Run Test'}
        </button>
      </div>
      <div className="flex items-center gap-3 text-sm">
        <label className="text-slate-400">Interval (ms)</label>
        <input
          type="number"
          min="500"
          step="500"
          value={intervalMs}
          onChange={(e) => setIntervalMs(Number(e.target.value))}
          className="w-28 rounded-md border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>
      <div className="flex flex-wrap gap-2">
        {targets.map((target) => (
          <span key={target} className="inline-flex items-center gap-2 rounded-full bg-slate-800 px-3 py-1 text-sm">
            {target}
            <button onClick={() => removeTarget(target)} className="text-slate-400 hover:text-rose-400">
              <X size={14} />
            </button>
          </span>
        ))}
      </div>
    </div>
  );
}
