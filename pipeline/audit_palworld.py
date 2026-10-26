"""Unread-value ledger for Palworld: every material parameter seen across the extracted Pals, with how often it occurs
and whether the importer reads it. Writes docs/ledger/palworld.json.

    python -m pipeline.audit_palworld [--raw raw]
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict

from .importers.palworld.importer import BASE_PARAMS, EMISSIVE_PARAMS, MROS_PARAMS, NORMAL_PARAMS, resolve_params

READ_TEXTURES = set(BASE_PARAMS + NORMAL_PARAMS + MROS_PARAMS + EMISSIVE_PARAMS)
READ_OTHER = {"blend", "two_sided", "clip"}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="raw")
    ap.add_argument("--out", default=os.path.join("docs", "ledger", "palworld.json"))
    args = ap.parse_args(argv)
    root = os.path.join(args.raw, "palworld")
    tex, sca, vec = Counter(), Counter(), Counter()
    examples: dict[str, set] = defaultdict(set)
    bases = Counter()
    n_slots = 0
    for code in sorted(os.listdir(root)):
        pj = os.path.join(root, code, "pal.json")
        if not os.path.isfile(pj):
            continue
        with open(pj, encoding="utf-8") as f:
            info = json.load(f)
        for slot in info["materials"]:
            n_slots += 1
            prm = resolve_params(args.raw, slot.get("chain") or [])
            bases[(prm["base_material"] or "?").rsplit("/", 1)[-1]] += 1
            for k in prm["textures"]:
                tex[k] += 1
                if len(examples["t:" + k]) < 4:
                    examples["t:" + k].add(code)
            for k in prm["scalars"]:
                sca[k] += 1
            for k in prm["vectors"]:
                vec[k] += 1

    def rows(c: Counter, read: set, prefix: str):
        return [{"name": k, "slots": n, "read": k in read, **({"examples": sorted(examples[prefix + k])} if prefix == "t:" else {})}
                for k, n in c.most_common()]

    ledger = {
        "note": "Material parameters across all extracted Pals (resolved child-first along each instance chain). "
                "'read' = used by pipeline/importers/palworld/importer.py; everything else is not reproduced in the GLB.",
        "materialSlots": n_slots,
        "baseMaterials": [{"name": k, "slots": n} for k, n in bases.most_common()],
        "textureParams": rows(tex, READ_TEXTURES, "t:"),
        "scalarParams": rows(sca, set(), "s:"),
        "vectorParams": rows(vec, set(), "v:"),
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=1)
    unread = [r for r in ledger["textureParams"] if not r["read"]]
    print(f"{n_slots} material slots, {len(bases)} base materials, {len(tex)} texture params ({len(unread)} unread) -> {args.out}")
    for r in unread[:15]:
        print(f"  unread texture param {r['name']!r}: {r['slots']} slots, e.g. {', '.join(r['examples'])}")


if __name__ == "__main__":
    main()
