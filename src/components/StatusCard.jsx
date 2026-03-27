import { toneToColor } from '../utils/health';

export default function StatusCard({ title, value, tone = 'healthy', subtext }) {
  return (
    <div className="panel">
      <p className="text-sm text-slate-400">{title}</p>
      <p className="mt-2 text-2xl font-semibold" style={{ color: toneToColor(tone) }}>
        {value}
      </p>
      {subtext ? <p className="mt-1 text-xs text-slate-400">{subtext}</p> : null}
    </div>
  );
}
