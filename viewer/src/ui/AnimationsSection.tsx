import { useEffect, useMemo, useRef, useState } from 'react';
import type { AnimPack, ClipInfo } from '../types';
import type { PlaybackState } from '../scene/animation';

const ORDER = ['idle', 'move', 'battle', 'ride', 'emote', 'work', 'other'];

interface Props {
  clips: ClipInfo[];
  state: PlaybackState;
  onSelect: (name: string) => void;
  /** humans: shared animation packs; clips with `pack` are listed under their pack (own packs open, other weapons folded) */
  packs?: AnimPack[];
}

function byCategory(list: ClipInfo[]) {
  const m = new Map<string, ClipInfo[]>();
  for (const c of list) {
    const k = ORDER.includes(c.category) ? c.category : 'other';
    if (!m.has(k)) m.set(k, []);
    m.get(k)!.push(c);
  }
  return ORDER.filter((k) => m.has(k)).map((k) => [k, m.get(k)!] as const);
}

/** Clip list (transport + timeline live in the bottom Timeline bar). */
export function AnimationsSection({ clips, state, onSelect, packs = [] }: Props) {
  const [q, setQ] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const activeRef = useRef<HTMLButtonElement>(null);
  const f = q.trim().toLowerCase();
  const filtered = useMemo(() => clips.filter((c) => !f || c.name.toLowerCase().includes(f)), [clips, f]);
  const own = useMemo(() => byCategory(filtered.filter((c) => !c.pack)), [filtered]);
  const sections = useMemo(() => packs.map((p) => ({ pack: p, list: filtered.filter((c) => c.pack === p.id) })).filter((s) => s.list.length), [packs, filtered]);
  const currentPack = clips.find((c) => c.name === state.clip)?.pack;

  useEffect(() => { activeRef.current?.scrollIntoView({ block: 'nearest' }); }, [state.clip]);

  const item = (c: ClipInfo) => (
    <button
      key={c.name}
      ref={c.name === state.clip ? activeRef : undefined}
      className={'anim-item' + (c.name === state.clip ? ' active' : '')}
      onClick={() => onSelect(c.name)}
      role="option"
      aria-selected={c.name === state.clip}
    >
      <span>{c.name}</span>
      <span className="dim">{c.loop ? '↻' : ''}</span>
    </button>
  );

  let otherHeader = false;
  return (
    <div className="picker">
      <input className="text" placeholder="Filter animations..." value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="anim-list" role="listbox">
        {own.map(([cat, list]) => (
          <div key={cat}>
            <div className="anim-cat">{cat} <span>({list.length})</span></div>
            {list.map(item)}
          </div>
        ))}
        {sections.map(({ pack, list }) => {
          // own packs start open; other weapons open on click, while filtering, or when they hold the playing clip
          const isOpen = open[pack.id] ?? (pack.own || !!f || pack.id === currentPack);
          const head = !pack.own && !otherHeader;
          if (head) otherHeader = true;
          return (
            <div key={pack.id}>
              {head && <div className="anim-cat anim-divider">other weapons</div>}
              <button className="anim-cat anim-pack" onClick={() => setOpen((o) => ({ ...o, [pack.id]: !isOpen }))} aria-expanded={isOpen}>
                <span style={{ flex: 1 }}>{isOpen ? '▾' : '▸'} {pack.label}</span>
                <span>({list.length})</span>
              </button>
              {isOpen && (pack.own ? byCategory(list).flatMap(([, l]) => l) : list).map(item)}
            </div>
          );
        })}
        {own.length === 0 && sections.length === 0 && <div className="dim pad">No matches</div>}
      </div>
    </div>
  );
}
