# palworld-models

Pipeline + web viewer for Palworld models, sibling of `../pokemon_model_ripping` (same IR, GLB writer, manifest, Blender
scripts and viewer; only the importer side is new). Nothing downstream of the importer knows about Unreal.

## Layout
- `extractor/PalExtract/` — .NET 10 console app on CUE4Parse (NuGet `CUE4Parse`, `CUE4Parse-Conversion` 1.2.2.202610).
  `list`, `probe <pkg>`, `json <pkg> <out>`, `tables`, `pal <Code>... | --all-pals [--force]`. Reads
  `C:\Program Files (x86)\Steam\steamapps\common\Palworld\Pal\Content\Paks` (`--game`), writes `raw/` (`--raw`).
- `raw/` (git-ignored) — `mappings/*.usmap`, `tables/*.json`, `palworld/<Code>/{pal.json, lod0.bin, anims/*.bin}`,
  `materials/<pkg path>.json`, `textures/<pkg path>.{png,json}`, `listing.txt`.
- `pipeline/importers/palworld/` — `raw.py` (reader), `convert.py` (space conversion), `importer.py` (→ `Model`).
- `pipeline/catalog/palworld.py` — refs, names (DT_PalNameText_Common en), Paldeck no. (`ZukanIndex` + suffix), elements.
- `pipeline/common/` — copied unchanged from the Pokémon repo (IR, `write_glb`, manifest).
- `viewer/` — copied from the Pokémon repo; Palworld bits in `src/data/pal.ts`, `src/ui/Disclaimer.tsx`. Dev port 5180.
- `out/` (git-ignored) — `palworld/<Code>/{model.glb, manifest.json, thumb.webp}`, indexes, `blender/`.

## Game facts (build 25246127, Sep 2026)
- UE 5.1, one classic pak `Pal-Windows.pak` (36.6 GiB), pak v11, unencrypted (zero AES key), Oodle. CUE4Parse `EGame.GAME_Palworld`.
- Unversioned properties: a `.usmap` is required. Ours came from UE4SS experimental (v3.0.1-1181) `DumpUSMAP()` run
  from a one-off Lua mod 30 s after launch; UE4SS was then removed from the game folder. Regenerate after game updates.
- Pals: `Pal/Content/Pal/Model/Character/Monster/<Code>/SK_<Code>` (+ MI_/T_), animations in
  `Pal/Content/Pal/Animation/Character/Monster/<Code>/AS_*` (AM_ montages wrap them). Element forms (`Kitsunebi_Ice`)
  and skins (`PinkCat_Skin001`) have their own SK; their AS_ live in the base Pal's folder → the extractor searches every
  code prefix and keeps sequences whose Skeleton equals the mesh's.
- Code names are internal (SheepBall = Lamball, Kitsunebi = Foxparks). Names: `PAL_NAME_<Code>`.

## Conventions / rules (no per-model fixes)
- Extractor output stays in Unreal space: left-handed, Z up, cm, mesh faces +Y, quats xyzw as the engine stores them
  (verified: anim frame 0 matches the bind pose without conjugation).
- Unreal → glTF = swap Y/Z, ×0.01 (a reflection = the handedness change). Quaternion (v,w) → (−Mv, w). Unreal front faces
  are clockwise; after the reflection they are glTF CCW, so indices are kept. Tested in `pipeline/tests/test_palworld.py`.
- Animation translations are authored at the *mesh* bone lengths (the USkeleton ref pose can be larger, Lamball 1.25×);
  every surveyed bone uses retarget mode `Animation` (use as-is). Other modes are implemented like the engine.
- fps = (frames − 1) / SequenceLength (CUE4Parse's FramesPerSecond is frames / length). Additive sequences are skipped.
- Loop flags are not in UE 5.1 sequences; derived from names (`LOOP_RE`). Categories: idle / move / battle / work / other.
- Materials (PalLit family): `Base Texture` → base colour; `Normal Map` (BC5) → normal, green flipped (DirectX → glTF);
  `MetallicRoughnessOcclusionSpecularTexture` R metal / G rough / B AO, flagged sRGB so linearised, repacked to glTF ORM.
  `Emissive Texture` → emissive. Blend modes from BasePropertyOverrides (1 Masked, 2 Translucent). Materials without an
  albedo (fire `M_Pal_Fire`, jewels) become half-transparent tinted "effect" materials.
- Material scalars `Roughness Mul/Add`, `Metallic Mul/Add` are baked into the ORM texture (cache key includes them); `Specular`
  (UE 0.5 = glTF default) is written as `KHR_materials_specular` (specularFactor = Specular/0.5).
- Held items: `PalExtract weapon [<Code>...]` finds them in two places, both in `BP_<Code>` (variants fall back to the base Pal), and writes
  `raw/palworld_weapons/<Item>/` + `held.json` (`parts`, each with the component's `loc/rot/scale`):
  (1) `PalRideMarkerComponent.UniqueRidePalWeaponClass` -> weapon BP's mesh components (dual launcher = `SkeletalMeshL/R`, hammer has its
  own offset/yaw/x5 scale); (2) SkeletalMesh/StaticMesh components in the Pal's own BP attached to a `*Weapon*` socket (Tanzee rifle, Bad Catgirl
  shotgun, Carbunclo handgun (static mesh, `ExportStatic`), Beegarde/Elizabee spears; socket = bone + transform from the skeleton).
  Offset = socket * component transform (`attach_offset` in `pipeline/catalog/palworld.py`), overridable per item in `held_offsets.json`
  (`<item>@<bone>` or `<item>`; only the Minigun is fitted, by `tools/fit_held_item.py`). Static ride props (missile pods, prism towers:
  `mounted`) have no hand attach and are skipped. Not held weapons: BP_EggLauncher/PenguinLauncher/Flamethrower/Katana use the Pal's own mesh
  (the player holds the Pal); skill props (Robinquill bow, Wing Golem cutter) only exist inside skill BPs. `PalExtract props|refs` survey tools.
  Weapons are `kind: object` models `Weapon_<Item>`; manifest `heldItem`/`heldItems` (+`scale`); props show a weapon only in clips matching
  `HELD_CLIP_RE` (Aim/Minigun/Fire_/Shoot) unless the component is visible by default (`always`, the spears). Viewer: `setHeldItem` + `setExtraHeld`.
  Weapon materials: PalLit instances via `build_material`, Albedo/Normal/RMA instances, or bare UMaterials by texture suffix (`build_weapon_material`).
- Vertex colours are shader masks: never exported as COLOR_0. Tangents are not exported (viewer derives them).
- Eyes/mouths are atlases (Lamball eye: 2×4 cells); the mesh UVs sit in the neutral cell. Expressions are set by gameplay
  code, not anim curves (AS_ have no curves). TODO: offer the cells as a variant group via KHR_texture_transform.

## Commands
```
dotnet raw/../extractor/PalExtract/bin/Release/net10.0/PalExtract.dll pal SheepBall      (run from raw/)
.venv/Scripts/python.exe -m pipeline convert --all [--resume]
.venv/Scripts/python.exe -m pipeline thumbs | export --id X | index | relabel
.venv/Scripts/python.exe -m pytest -q
cd viewer && npm run dev   (port 5180; 5173 is the Pokémon viewer)
```

## Gotchas
- Git Bash rewrites leading `/` args into Windows paths (`/palworld` → `C:/Program Files/Git/palworld`) — pass paths
  without the slash to node scripts.
- Viewer "Game" shading is the Pokémon toon model; Palworld defaults to PBR + studio reflection env (metals were black).
- Hosting: same pattern as the Pokémon repo (`tools/deploy_pages.py`: app → gh-pages, GLBs → Hugging Face). Ask first.
