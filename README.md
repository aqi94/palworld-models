# palworld-models

An open-source pipeline and web viewer for Palworld models. It reads the game files from **your own Steam install** and turns
every Pal into a rigged glTF (`model.glb`) with all of its animations as named clips, plus a `manifest.json` the viewer reads.
Blender exports (FBX with every animation baked, OBJ+MTL, PNG textures) come from the same GLB.

Unofficial fan project, not affiliated with or endorsed by Pocketpair, Inc. Palworld and its assets are © Pocketpair, Inc.

## What is not included

No game data of any kind: no models, textures, animations, renders, thumbnails, mappings (`.usmap`) or data tables. Everything
under `raw/` and `out/` is generated on your machine from your installed copy and is git-ignored.

## How it works

```
Pal-Windows.pak ──► extractor/PalExtract (C#, CUE4Parse) ──► raw/   (JSON + binary buffers + PNG, Unreal space)
                    pipeline/importers/palworld (Python) ──► out/palworld/<Code>/model.glb + manifest.json
                    blender/export_formats.py            ──► out/blender/palworld/<Code>/  (FBX, OBJ, PNG)
                    viewer/ (Vite + React + three.js)    ──► reads out/ directly
```

## Running it against your install

Requirements: Windows, Palworld (Steam), [.NET 10 SDK](https://dotnet.microsoft.com/download), Python 3.12+, Node 20+,
Blender 4.2+ (only for FBX/OBJ export and thumbnails).

1. **Mappings.** Palworld's paks use unversioned properties, so the extractor needs a `.usmap` for your game build.
   Generate one with [UE4SS](https://github.com/UE4SS-RE/RE-UE4SS) (`DumpUSMAP`, Ctrl+Numpad6 by default) and put it at
   `raw/mappings/<anything>.usmap`. Remove UE4SS again afterwards if you play online.
2. **Extract** (reads the pak in place, never modifies it):
   ```
   cd extractor/PalExtract
   dotnet build -c Release
   cd ../../raw          # (create it first)
   dotnet ../extractor/PalExtract/bin/Release/net10.0/PalExtract.dll tables
   dotnet ../extractor/PalExtract/bin/Release/net10.0/PalExtract.dll pal --all-pals      # or: pal SheepBall Kitsunebi
   ```
   Use `--game <Paks folder>` if Palworld is not in the default Steam location. Oodle is downloaded next to the extractor.
3. **Convert:**
   ```
   python -m venv .venv && .venv\Scripts\pip install numpy pillow pygltflib pytest
   .venv\Scripts\python -m pipeline convert --all
   .venv\Scripts\python -m pipeline thumbs            # optional, needs Blender
   .venv\Scripts\python -m pipeline export --id SheepBall   # FBX / OBJ / PNG
   ```
4. **View:** `cd viewer && npm install && npm run dev`, then open http://localhost:5180.

Tests: `.venv\Scripts\python -m pytest -q` (synthetic data, no game files needed).

See `CLAUDE.md` for the format, coordinate conventions and the rules the importer follows.
