import { ChevronDown, ChevronUp } from 'lucide-react';

export default function ExpandablePanel({ title, open, onToggle, children }) {
  return (
    <section className="panel">
      <button onClick={onToggle} className="flex w-full items-center justify-between text-left">
        <h3 className="text-lg font-semibold">{title}</h3>
        {open ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
      </button>
      {open ? <div className="mt-4">{children}</div> : null}
    </section>
  );
}
