import { useEffect, useState } from 'react';
import { loadGames } from '../data';
import type { GameSummary } from '../types';

const KIND_LABEL: Record<string, string> = { pal: 'Pals', human: 'Humans', object: 'Objects' };

/** Landing page: one card per game (domain). Nothing but the tiny games.json is loaded here. */
export function Games() {
  const [games, setGames] = useState<Record<string, GameSummary> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    document.title = 'Palworld Model Viewer';
    loadGames().then((g) => {
      // a single game: go straight to its grid
      const keys = Object.keys(g);
      if (keys.length === 1) { window.location.replace(`${import.meta.env.BASE_URL}${keys[0]}`); return; }
      setGames(g);
    }).catch((e) => setErr(String(e instanceof Error ? e.message : e)));
  }, []);
  const last = (g: string) => { try { return localStorage.getItem('lastModel:' + g); } catch { return null; } };
  return (
    <div className="browse">
      <header><h1>Palworld Model Viewer</h1><span className="dim">Choose a game</span></header>
      {err && <div className="overlay err">{err}</div>}
      <div className="games">
        {Object.entries(games ?? {}).map(([g, s]) => (
          <div key={g} className="game-card">
            <a className="game-main" href={`${import.meta.env.BASE_URL}${g}`}>
              <strong>{s.name}</strong>
              <span className="dim">{s.count} models · {Object.entries(s.kinds).map(([k, n]) => `${n} ${KIND_LABEL[k] ?? k}`).join(', ')}</span>
            </a>
            {last(g) && <a className="btn small" href={`${import.meta.env.BASE_URL}${g}/${encodeURIComponent(last(g)!)}`}>Resume {last(g)}</a>}
          </div>
        ))}
      </div>
    </div>
  );
}
