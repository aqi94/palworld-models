"""Command line entry point.

  python -m pipeline list
  python -m pipeline convert --id SheepBall [--id ...] [--out out] [--no-anim]
  python -m pipeline convert --all [--limit N] [--resume]
  python -m pipeline index                      # rebuild out/index.json from manifests
  python -m pipeline relabel                    # refresh names/dex of converted manifests, reindex
  python -m pipeline export  --id SheepBall     # FBX/OBJ/PNG via Blender
  python -m pipeline thumbs                     # thumb.webp per model via Blender

Raw data comes from extractor/PalExtract (default ./raw, or --data / PALWORLD_RAW).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

from .catalog.palworld import ModelRef, PalworldCatalog
from .common.gltf_writer import write_glb
from .common.manifest import write_manifest


def _catalog(args) -> PalworldCatalog:
    if args.game != "palworld":
        sys.exit(f"game '{args.game}' has no importer (only palworld)")
    return PalworldCatalog(args.data)


KINDS = ["pal", "human", "object"]


def _refs_by_kind(cat, kind: str | None) -> list[ModelRef]:
    return cat.by_kind(kind)


def cmd_list(args) -> None:
    cat = _catalog(args)
    refs = _refs_by_kind(cat, args.kind)
    for r in refs:
        print(f"{r.kind:8s} {r.id:16s} {r.display_name}")
    print(f"{len(refs)} models", file=sys.stderr)


def convert_one(cat: PalworldCatalog, ref: ModelRef, out_root: str, with_anim: bool = True) -> dict:
    t0 = time.time()
    model = cat.import_ref(ref, with_anim)
    model.extras.update({k: v for k, v in ref.extras.items() if k != "folder"})
    model.extras["species"] = ref.species
    model.extras["form"] = ref.form
    model.source_paths = [ref.folder]
    out_dir = os.path.join(out_root, ref.game, ref.id)
    os.makedirs(out_dir, exist_ok=True)
    summary = write_glb(model, os.path.join(out_dir, "model.glb"))
    manifest = write_manifest(model, os.path.join(out_dir, "manifest.json"), summary)
    summary["seconds"] = round(time.time() - t0, 2)
    summary["clips"] = len(model.clips)
    return {"ok": True, "id": ref.id, "manifest": manifest, "summary": summary}


def cmd_convert(args) -> None:
    cat = _catalog(args)
    if args.all:
        refs = _refs_by_kind(cat, args.kind)
        if args.limit:
            refs = refs[: args.limit]
    else:
        refs = []
        for mid in args.id:
            r = cat.find(mid)
            if r is None:
                sys.exit(f"unknown model id {mid}")
            refs.append(r)
    log_path = os.path.join(args.out, "convert_log.jsonl")
    os.makedirs(args.out, exist_ok=True)
    done = set()
    if args.resume and os.path.isfile(log_path):
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if rec.get("ok"):
                        done.add(rec["id"])
                except json.JSONDecodeError:
                    pass
    ok = fail = 0
    with open(log_path, "a", encoding="utf-8") as log:
        for i, ref in enumerate(refs):
            if ref.id in done:
                continue
            try:
                res = convert_one(cat, ref, args.out, with_anim=not args.no_anim)
                s = res["summary"]
                print(f"[{i + 1}/{len(refs)}] {ref.id:16s} {ref.display_name:24s} {s['clips']:3d} clips {s['bytes'] / 1e6:6.1f} MB {s['seconds']:5.1f}s")
                log.write(json.dumps({"ok": True, "id": ref.id, "summary": s}) + "\n")
                ok += 1
            except Exception as e:  # noqa: BLE001
                print(f"[{i + 1}/{len(refs)}] {ref.id:16s} FAILED: {e}")
                log.write(json.dumps({"ok": False, "id": ref.id, "error": str(e), "trace": traceback.format_exc()}) + "\n")
                fail += 1
            log.flush()
    print(f"done: {ok} ok, {fail} failed")
    build_index(args.out)


CATEGORY_LABELS = {"pals": "Pals", "weapons": "Weapons", "humans": "Humans", "furniture": "Furniture", "other": "Other"}
SUB_LABELS = {"pals": "Pals", "skins": "Skins", "special": "Special", "held": "Held items"}


def categorize_model(kind: str, mid: str, extras: dict) -> tuple[str, str | None]:
    """(category, subcategory) of a model. Categories are the viewer's top-level tabs (new asset kinds add one here)."""
    if kind == "pal":
        return "pals", "skins" if extras.get("skin") else "pals" if extras.get("dex") else "special"
    if kind == "object":
        return ("weapons", "held") if mid.startswith("Weapon_") else ("other", None)
    return {"human": "humans", "furniture": "furniture"}.get(kind, "other"), None


def category_tree(models: list[dict]) -> list[dict]:
    tree: dict[str, dict] = {}
    for m in models:
        c = tree.setdefault(m["category"], {"id": m["category"], "label": CATEGORY_LABELS.get(m["category"], m["category"]), "count": 0, "subs": {}})
        c["count"] += 1
        if m.get("subcategory"):
            sc = c["subs"].setdefault(m["subcategory"], {"id": m["subcategory"], "label": SUB_LABELS.get(m["subcategory"], m["subcategory"]), "count": 0})
            sc["count"] += 1
    order = list(CATEGORY_LABELS)
    return [{**c, "subs": list(c["subs"].values())} for c in sorted(tree.values(), key=lambda c: order.index(c["id"]) if c["id"] in order else 99)]


def build_index(out_root: str) -> dict:
    models = []
    for game in sorted(os.listdir(out_root)):
        gdir = os.path.join(out_root, game)
        if not os.path.isdir(gdir):
            continue
        for mid in sorted(os.listdir(gdir)):
            mp = os.path.join(gdir, mid, "manifest.json")
            if not os.path.isfile(mp):
                continue
            with open(mp, encoding="utf-8") as f:
                m = json.load(f)
            cat, sub = categorize_model(m["kind"], m["id"], m.get("extras", {}))
            models.append(
                {
                    "game": m["game"],
                    "category": cat,
                    "subcategory": sub,
                    "id": m["id"],
                    "kind": m["kind"],
                    "displayName": m["displayName"],
                    "clips": m["stats"]["clips"],
                    "variantGroups": [g["name"] for g in m["variantGroups"]],
                    "path": f"{game}/{mid}/",
                    "dex": m.get("extras", {}).get("dex"),
                    "form": m.get("extras", {}).get("form"),
                    "item": m.get("extras", {}).get("item"),
                    "props": m.get("props"),
                    "thumbnail": f"{game}/{mid}/thumb.webp" if os.path.isfile(os.path.join(gdir, mid, "thumb.webp")) else None,
                    "dexSuffix": m.get("extras", {}).get("dexSuffix"),
                    "elements": m.get("extras", {}).get("elements"),
                    "species": m.get("extras", {}).get("species"),
                    "skin": m.get("extras", {}).get("skin"),
                    "codeName": m.get("extras", {}).get("codeName"),
                }
            )
    # Paldeck order (12B after 12); models without a number (skins of unnumbered Pals, bosses) keep id order.
    models.sort(key=lambda m: (m["game"], m["dex"] is None, m["dex"] or 0, m.get("dexSuffix") or "", m.get("skin") or "", m["id"]))
    names = {"palworld": "Palworld"}
    games = {g: names.get(g, g) for g in names if any(m["game"] == g for m in models)}
    index = {"indexVersion": 2, "games": games, "models": models}
    with open(os.path.join(out_root, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    # Per-game indexes + a tiny games list: the viewer loads only the game (domain) being viewed.
    summary = {}
    for g, name in games.items():
        gm = [m for m in models if m["game"] == g]
        with open(os.path.join(out_root, g, "index.json"), "w", encoding="utf-8") as f:
            json.dump({"indexVersion": 2, "game": g, "name": name, "categories": category_tree(gm), "models": gm}, f, ensure_ascii=False)
        kinds = {}
        for m in gm:
            kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
        summary[g] = {"name": name, "count": len(gm), "kinds": kinds}
    with open(os.path.join(out_root, "games.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False)
    return index


def cmd_relabel(args) -> None:
    """Rewrite displayName + extras.dex of already converted models from the catalog (no reconversion)."""
    cat = _catalog(args)
    changed = 0
    for ref in cat.all():
        mp = os.path.join(args.out, ref.game, ref.id, "manifest.json")
        if not os.path.isfile(mp):
            continue
        with open(mp, encoding="utf-8") as f:
            m = json.load(f)
        extras = m.setdefault("extras", {})
        if m.get("displayName") == ref.display_name and extras.get("dex") == ref.extras.get("dex"):
            continue
        print(f"{ref.id:16s} {m.get('displayName')!s:28s} -> {ref.display_name}")
        m["displayName"] = ref.display_name
        if "dex" in ref.extras:
            extras["dex"] = ref.extras["dex"]
        with open(mp, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
        changed += 1
    print(f"{changed} manifests relabelled")
    build_index(args.out)


def cmd_index(args) -> None:
    idx = build_index(args.out)
    print(f"{len(idx['models'])} models indexed")


def cmd_export(args) -> None:
    from .blender_runner import export_formats

    for mid in args.id:
        glb = os.path.join(args.out, args.game, mid, "model.glb")
        if not os.path.isfile(glb):
            sys.exit(f"{glb} not found; run convert first")
        out_dir = os.path.join(args.out, "blender", args.game, mid)
        if args.subset:  # a named subset goes to its own folder so the full export stays intact
            out_dir = os.path.join(out_dir, args.subset)
        res = export_formats(glb, out_dir, mid, clips=args.clips, rest_visible=args.rest_visible, parts=args.parts,
                             accessories=args.accessories, lod0=args.lod0)
        print(json.dumps(res, indent=1))


def cmd_thumbs(args) -> None:
    """Render thumb.webp for converted models (Blender headless, ~4 s each)."""
    import shutil
    import tempfile

    from PIL import Image

    from .blender_runner import inspect

    root = os.path.join(args.out, args.game)
    ids = args.id or sorted(d for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "model.glb")))
    tmp = tempfile.mkdtemp(prefix="thumbs_")
    n = 0
    for mid in ids:
        dst = os.path.join(root, mid, "thumb.webp")
        if os.path.isfile(dst) and not args.force:
            continue
        glb = os.path.join(root, mid, "model.glb")
        try:
            kind, pose = "", None
            try:
                with open(os.path.join(root, mid, "manifest.json"), encoding="utf-8") as f:
                    man = json.load(f)
                kind, pose = man.get("kind", ""), man.get("defaultClip")
            except (OSError, ValueError):
                pass
            r = inspect(glb, os.path.join(tmp, mid), pose_clip=pose)
            # maps are wide and flat: the 35 deg "top" shot shows the layout, the 12 deg front shot only the near edge
            src = os.path.join(tmp, mid, "preview_top.png" if kind == "map" else "preview_front.png")
            if not r.get("ok") or not os.path.isfile(src):
                print(f"{mid}: render failed: {r.get('error', '')[:200]}")
                continue
            im = Image.open(src).convert("RGB")
            im = im.resize((args.size, args.size), Image.LANCZOS)
            im.save(dst, "WEBP", quality=82)
            n += 1
            print(f"{mid}: thumb.webp")
        except Exception as e:  # noqa: BLE001
            print(f"{mid}: {e}")
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"{n} thumbnails written")
    build_index(args.out)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="pipeline")
    ap.add_argument("--game", default="palworld")
    ap.add_argument("--data", default=None, help="extractor output folder (default ./raw or PALWORLD_RAW)")
    ap.add_argument("--out", default="out")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list")
    p.add_argument("--kind", choices=KINDS)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("convert")
    p.add_argument("--id", action="append", default=[])
    p.add_argument("--all", action="store_true")
    p.add_argument("--kind", choices=KINDS)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--no-anim", action="store_true")
    p.set_defaults(fn=cmd_convert)

    p = sub.add_parser("index")
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("relabel", help="refresh displayName/dex of converted manifests from the catalog, then reindex")
    p.set_defaults(fn=cmd_relabel)

    p = sub.add_parser("export")
    p.add_argument("--id", action="append", required=True)
    p.add_argument("--clips", default=None, help="regex: export only the clips whose name matches (re.search)")
    p.add_argument("--rest-visible", action="store_true", help="drop meshes hidden at rest (other part options, colourways)")
    p.add_argument("--subset", default=None, help="write to out/blender/<game>/<id>/<subset>/ instead of the full export folder")
    p.add_argument("--parts", default=None,
                   help="modular models: 'group=option,...' (manifest partGroups) shown instead of the default options; "
                        "combine with --rest-visible to drop the other options")
    p.add_argument("--accessories", action="store_true", help="with --parts: also show the selected options' accessory meshes")
    p.add_argument("--lod0", action="store_true", help="drop LOD1+ meshes (node extras lod > 0), e.g. for engines that build their own LODs")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("thumbs")
    p.add_argument("--id", action="append", default=[])
    p.add_argument("--size", type=int, default=320)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_thumbs)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
