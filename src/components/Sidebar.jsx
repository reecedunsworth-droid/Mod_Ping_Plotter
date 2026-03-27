const nav = ['Dashboard', 'Tools', 'History', 'Settings'];

export default function Sidebar({ active, onChange }) {
  return (
    <aside className="w-64 border-r border-slate-800 bg-slate-950/90 p-5">
      <div className="mb-10">
        <p className="text-xs uppercase tracking-[0.4em] text-cyan-400">Netra</p>
        <h1 className="text-3xl font-semibold tracking-tight text-slate-100">Netra</h1>
      </div>
      <nav className="space-y-2">
        {nav.map((item) => (
          <button
            key={item}
            onClick={() => onChange(item)}
            className={`w-full rounded-lg px-3 py-2 text-left transition ${
              active === item ? 'bg-cyan-500/20 text-cyan-300' : 'text-slate-300 hover:bg-slate-800/80'
            }`}
          >
            {item}
          </button>
        ))}
      </nav>
    </aside>
  );
}
