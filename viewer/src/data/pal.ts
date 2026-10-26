import type { IndexModel } from '../types';

/** Element colours (approximate in-game UI hues) for the small element chips. */
export const ELEMENT_COLORS: Record<string, string> = {
  Neutral: '#b9a99a', Fire: '#e8613c', Water: '#3d8fe0', Grass: '#5fb547', Electric: '#e8c12f',
  Ice: '#79d0e8', Ground: '#a7743f', Dark: '#7c4f9c', Dragon: '#b04dc0',
};

/** Paldeck label: #029, #029B. Empty for models without a Paldeck number. */
export function dexLabel(m: Pick<IndexModel, 'dex' | 'dexSuffix'>): string {
  return m.dex != null ? '#' + String(m.dex).padStart(3, '0') + (m.dexSuffix ?? '') : '';
}

export type PalFilter = 'all' | 'pals' | 'skins';
export const PAL_FILTERS: [PalFilter, string][] = [['all', 'All'], ['pals', 'Pals'], ['skins', 'Skins']];

export function filterModel(m: IndexModel, filter: PalFilter, query: string): boolean {
  if (filter === 'pals' && m.skin) return false;
  if (filter === 'skins' && !m.skin) return false;
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay = [m.displayName, m.id, m.codeName ?? '', dexLabel(m), String(m.dex ?? ''), ...(m.elements ?? [])].join(' ').toLowerCase();
  return q.split(/\s+/).every((w) => hay.includes(w.replace(/^#0*/, '#').startsWith('#') ? w.replace(/^#/, '') : w));
}
