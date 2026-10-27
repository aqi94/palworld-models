import { useEffect, useMemo, useState } from 'react';
import type { CategoryInfo, GameIndex, IndexModel } from '../types';
import { categoryOf, filterModel } from '../data/pal';

/** Category tabs (from the index; derived for old indexes), sub-category chips, and the filtered model list. */
export function useModelFilter(index: GameIndex | null, query: string, currentId?: string) {
  const categories = useMemo<CategoryInfo[]>(() => {
    if (index?.categories) return index.categories;
    const by = new Map<string, CategoryInfo>();
    for (const m of index?.models ?? []) {
      const id = categoryOf(m);
      const c = by.get(id) ?? { id, label: id[0].toUpperCase() + id.slice(1), count: 0, subs: [] };
      c.count++;
      by.set(id, c);
    }
    return [...by.values()];
  }, [index]);
  const [category, setCategory] = useState('pals');
  const [sub, setSub] = useState('all');

  // Follow the opened model (deep link / prev-next into another category) so the list is never blank.
  const cur = index?.models.find((m) => m.id === currentId);
  const curCat = cur ? categoryOf(cur) : null;
  useEffect(() => { if (curCat && curCat !== category) { setCategory(curCat); setSub('all'); } }, [curCat]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (categories.length && !categories.some((c) => c.id === category)) setCategory(categories[0].id); }, [categories]); // eslint-disable-line react-hooks/exhaustive-deps

  const models = useMemo<IndexModel[]>(() => {
    const f = query.trim().toLowerCase();
    return (index?.models ?? []).filter((m) => filterModel(m, category, sub, f));
  }, [index, query, category, sub]);

  return { categories, category, sub, models, setCategory: (c: string) => { setCategory(c); setSub('all'); }, setSub };
}

export function CategoryFilter({ f }: { f: ReturnType<typeof useModelFilter> }) {
  const subs = f.categories.find((c) => c.id === f.category)?.subs ?? [];
  return (
    <>
      {f.categories.length > 1 && (
        <div className="row wrap" style={{ marginTop: 6 }}>
          {f.categories.map((c) => (
            <button key={c.id} className={'btn small' + (f.category === c.id ? ' primary' : '')} onClick={() => f.setCategory(c.id)}>{c.label}</button>
          ))}
        </div>
      )}
      {subs.length > 1 && (
        <div className="row wrap" style={{ marginTop: 6 }}>
          {[{ id: 'all', label: 'All' }, ...subs].map((s) => (
            <button key={s.id} className={'btn small' + (f.sub === s.id ? ' primary' : '')} onClick={() => f.setSub(s.id)}>{s.label}</button>
          ))}
        </div>
      )}
    </>
  );
}
