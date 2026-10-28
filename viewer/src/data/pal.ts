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

/** Category / subcategory of a model (older indexes lack the fields: derive them from `kind`). */
export function categoryOf(m: IndexModel): string {
  return m.category ?? (m.kind === 'pal' ? 'pals' : m.kind === 'object' ? 'weapons' : 'other');
}

export function filterModel(m: IndexModel, category: string, sub: string, query: string): boolean {
  if (categoryOf(m) !== category) return false;
  if (sub !== 'all' && (m.subcategory ?? (m.kind === 'pal' ? (m.skin ? 'skins' : 'pals') : null)) !== sub) return false;
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay = [m.displayName, m.id, m.codeName ?? '', dexLabel(m), String(m.dex ?? ''), ...(m.elements ?? []), ...(m.aliases ?? [])].join(' ').toLowerCase();
  return q.split(/\s+/).every((w) => hay.includes(w.replace(/^#0*/, '#').startsWith('#') ? w.replace(/^#/, '') : w));
}
