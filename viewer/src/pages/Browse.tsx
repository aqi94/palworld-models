import { useEffect, useMemo, useRef, useState } from 'react';
import { loadGameIndex, modelAsset } from '../data';
import type { GameIndex } from '../types';
import { ELEMENT_COLORS, PAL_FILTERS, dexLabel, filterModel, type PalFilter } from '../data/pal';
import { Disclaimer } from '../ui/Disclaimer';
const CHUNK = 120;

/** One game's thumbnail grid. Tiles render in chunks so a 900-model game does not mount every <img> at once. */
export function Browse({ game }: { game: string }) {
  const [index, setIndex] = useState<GameIndex | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [kind, setKind] = useState<PalFilter>('all');
  const [shown, setShown] = useState(CHUNK);
  const sentinel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    loadGameIndex(game).then((i) => { setIndex(i); document.title = `${i.name} Model Viewer`; }).catch((e) => setErr(String(e instanceof Error ? e.message : e)));
  }, [game]);

  const models = useMemo(() => {
    const f = q.trim().toLowerCase();
    return (index?.models ?? []).filter((m) => filterModel(m, kind, f));
  }, [index, q, kind]);

  useEffect(() => setShown(CHUNK), [q, kind, game]);
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver((e) => { if (e[0].isIntersecting) setShown((n) => n + CHUNK); }, { rootMargin: '600px' });
    io.observe(el);
    return () => io.disconnect();
  }, [models]);

  return (
    <div className="browse">
      <header>
        <h1>{index?.name ?? game} Model Viewer</h1>
        <input className="text" placeholder="Search name, code name, #dex or element..." value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="tabs">
          {PAL_FILTERS.map(([k, label]) => (
            <button key={k} className={'btn small' + (kind === k ? ' primary' : '')} onClick={() => setKind(k)}>{label}</button>
          ))}
        </div>
      </header>
      {err && <div className="overlay err">{err}</div>}
      <div className="grid">
        {models.slice(0, shown).map((m) => (
          <a key={m.id} className="tile" href={`${import.meta.env.BASE_URL}${m.game}/${encodeURIComponent(m.id)}`}>
            <div className="thumb">
              {m.thumbnail ? (
                <img src={modelAsset(m.thumbnail)} alt={m.displayName} loading="lazy" decoding="async" />
              ) : (
                <span className="ph">{dexLabel(m) || m.kind}</span>
              )}
            </div>
            <div className="meta">
              <strong>{m.displayName}</strong>
              <span className="dim">{[dexLabel(m), m.codeName ?? m.id, `${m.clips} clips`].filter(Boolean).join(' · ')}</span>
              <span className="elements">{(m.elements ?? []).map((e) => <span key={e} className="element" style={{ background: ELEMENT_COLORS[e] ?? '#888' }}>{e}</span>)}</span>
            </div>
          </a>
        ))}
        {index && models.length === 0 && <div className="dim pad">No models match.</div>}
      </div>
      <div ref={sentinel} style={{ height: 1 }} />
      <Disclaimer />
    </div>
  );
}
