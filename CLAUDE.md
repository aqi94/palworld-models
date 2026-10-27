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

## Conventions / rules (no per-model fixes; for any errors, assume its root cause potentially affects other models too)
- Extractor output stays in Unreal space: left-handed, Z up, cm, mesh faces +Y, quats xyzw as the engine stores them
  (verified: anim frame 0 matches the bind pose without conjugation).
- Unreal → glTF = swap Y/Z, ×0.01 (a reflection = the handedness change). Quaternion (v,w) → (−Mv, w). Unreal front faces
  are clockwise; after the reflection they are glTF CCW, so indices are kept. Tested in `pipeline/tests/test_palworld.py`.
- Animation translations are authored at the *mesh* bone lengths (the USkeleton ref pose can be larger, Lamball 1.25×);
  every surveyed bone uses retarget mode `Animation` (use as-is). Other modes are implemented like the engine.
- fps = (frames − 1) / SequenceLength (CUE4Parse's FramesPerSecond is frames / length). Additive sequences (`AdditiveAnimType` is read from the asset's own properties: CUE4Parse's field stays `AAT_None`; `Minigun_Aim_*` are
  `AAT_RotationOffsetMeshSpace`) are baked onto frame 0 of their `<prefix>_Idle` (`bake_additive`: mesh-space rotation delta, translation added, base scale kept); with no such base they are skipped.
- Loop flags are not in UE 5.1 sequences; derived from names (`LOOP_RE`). Categories: idle / move / battle / work / other.
- Materials (PalLit family): `Base Texture` → base colour; `Normal Map` (BC5) → normal, green flipped (DirectX → glTF);
  `MetallicRoughnessOcclusionSpecularTexture` R metal / G rough / B AO, flagged sRGB so linearised, repacked to glTF ORM.
  `Emissive Texture` → emissive. Blend modes from BasePropertyOverrides (1 Masked, 2 Translucent). Materials without an
  albedo (jewels, unknown effects) become half-transparent tinted "effect" materials.
- Base-material parameter defaults: instances store only overrides; the extractor writes the base UMaterial's
  `CachedExpressionData` defaults as `Defaults {scalars, vectors, textures}` (RuntimeEntries[0/1/3] zipped with the value
  arrays) and exports default-only textures (Daedream's `T_DreamDemon_Extra`). `resolve_params` fills them in last;
  `prm["overridden"]` = names some instance sets.
- Cooked shaders are the source of truth for effect materials: `PalExtract shader --from-raw` dumps, for every non-PalLit
  material (and instance with its own shader map), raw/shaders/<pkg path>/{resources.json, *.dxbc, *.asm} from
  ShaderArchive-Pal-PCD3D_SM5 (D3DDisassemble). `pipeline/importers/palworld/shaders.py` decodes the uniform preshaders
  (cb2[N] -> parameter expression; `tools/preshaders.py <dir>` prints the map). Instances can be different static-switch
  variants (switch values are not cooked): `compiled_params` = parameters the instance's own compiled shader reads, which
  selects the variant. Transcribe formulas from the base-pass pixel shader (largest SF_Pixel), never fit them.
- `M_Pal_Fire` (35 Pals, `fire_shade`): m = MainTex(uv*tiling + frac(T*speed) + NoiseTex(..)*DistortionScale*0.1),
  v = uv.y; colour = 3-stop ramp of smoothstep(rgbMin, rgbMax, v*(m+rootColor)); alpha = smoothstep(clamp(alpha,0,1-edge),
  sat(alpha+edge), m*v^2*rootAlpha*(1+m)); VS pushes along the normal by OffsetScale cm. Fresnel rim only in the variant whose
  shader reads FresnelColor (7 Pals). `M_Pal_Manticore_Fire` (Blazehowl, Azurmane, `vfx2_shade`): centred tiling, layers
  squared, colour = lerp(C01, C02, smoothstep(RGBMin, RGBMax, a1 s1 + a2 s2)); alpha by variant (range / additive / opaque for
  Azurmane's Masked override) + U/V masks + |N.V|^FresnelScale. `M_Pal_Dream` (7) -> MainTex gradient, unlit, `extras.dream`
  shimmer (not yet decompiled). Colours are divided by eye adaptation in game, so FIRE_EXPOSURE = 1; `filmic` (per-channel
  ACES 75/25 matrixed ACES) is applied after x alpha, as Unreal tone-maps after translucency (viewer: premultiplied blending;
  GLB bake raises alpha, `bake_premultiplied`). Python shade functions and viewer/src/scene/effects.ts must stay in step.
- Material scalars `Roughness Mul/Add`, `Metallic Mul/Add` are baked into the ORM texture (cache key includes them); `Specular`
  (UE 0.5 = glTF default) is written as `KHR_materials_specular` (specularFactor = Specular/0.5).
- Held items: `PalExtract weapon [<Code>...]` finds them in two places, both in `BP_<Code>` (variants fall back to the base Pal), and writes
  `raw/palworld_weapons/<Item>/` + `held.json` (`parts`, each with the component's `loc/rot/scale`):
  (1) `PalRideMarkerComponent.UniqueRidePalWeaponClass` -> weapon BP's mesh components (dual launcher = `SkeletalMeshL/R`, hammer has its
  own offset/yaw/x5 scale); (2) SkeletalMesh/StaticMesh components in the Pal's own BP attached to a `*Weapon*` socket (Tanzee rifle, Bad Catgirl
  shotgun, Carbunclo handgun (static mesh, `ExportStatic`), Beegarde/Elizabee spears; socket = bone + transform from the skeleton).
  Offset = socket * component transform (`attach_offset` in `pipeline/catalog/palworld.py`), overridable per item in `held_offsets.json`
  (`<item>@<bone>` or `<item>`; currently empty: the Minigun was once fitted to the hand line with `tools/fit_held_item.py`, which turned it 33° off the Pal's forward; the engine attach is identity on `weapon_r`). Static ride props (missile pods, prism towers:
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
dotnet ../extractor/PalExtract/bin/Release/net10.0/PalExtract.dll shader --from-raw   (cooked effect shaders -> raw/shaders/)
.venv/Scripts/python.exe -m pipeline convert --all [--resume]
.venv/Scripts/python.exe -m pipeline thumbs | export --id X | index | relabel
.venv/Scripts/python.exe -m pytest -q
cd viewer && npm run dev   (port 5180; 5173 is the Pokémon viewer)
```

## Gotchas
- Git Bash rewrites leading `/` args into Windows paths (`/palworld` → `C:/Program Files/Git/palworld`) — pass paths
  without the slash to node scripts.
- Overlay layers sit ~1 mm above the surface (Astralym's star decals). The engine uses reverse-Z float depth; the viewer has
  24-bit depth, so `fitNearPlane` (ViewerScene, called from `scene.onBeforeRender`) pushes the near plane out to the model's
  padded bounding sphere on every render. Set the requested near through `setNear`, never by writing `camera.near`.
- Viewer "Game" shading is the Pokémon toon model; Palworld defaults to PBR + studio reflection env (metals were black).
- Hosting: same pattern as the Pokémon repo (`tools/deploy_pages.py`: app → gh-pages, GLBs → Hugging Face). Ask first.

## Humans, weapons, mounts, cloth (tower-boss canary)
- `PalExtract human <Boss_XBoss001>... | --all-humans` -> `raw/palworld_humans/<Id>/` (NPC/boss meshes under `Model/Character/NPC/**`; they share
  `SK_PalHuman_Skeleton`, so the extractor finds every character's clips: the importer keeps only `AS_<Owner>_*`, clip names keep the owner, ride clips are
  category `ride`). `weapons-all` -> every `Model/Weapon` mesh as `Weapon_<Class>_<NNN>`. `bossride` -> `raw/palworld_humans/rides.json`.
- Tower bosses ride Pals: `BP_<Pal>_GYM` holds a `BP_PalBossNPCMarker` (boss mesh + offset). Catalog links them: Pal
  `extras.bossRide`, boss `extras.mount` (same seat data); viewer Rider section on the Pal (`RideSection.tsx`, `ViewerScene.setRider`) and
  Mount section on the boss (`MountSection.tsx`, `setMount`: boss stays fixed, the Pal gets the inverse seat each frame). Zoe's body is the `_ALL` outfit mesh.
  Seat rules learned the hard way (all read from data, no per-boss fixes):
  1. The marker's socket is the SCS node's `AttachToName` (BossRideMarker, `head`, `moon`, `spine_03`), not always BossRideMarker.
  2. `bAbsoluteRotation` (Electric, Horus, Viking): the rotation is the actor's frame (Pal mesh sits at yaw -90), the bone's orientation is
     ignored, only `position` follows the bone -> `rotationSpace: "model"` (`absolute_seat_rotation`); `bAbsoluteScale` = boss is never scaled by the Pal.
  3. Viking/Sorajima/World Tree ride clips carry the seat in `root` (3-8 m, authored at the Pal's tower scale): importer sets boss
     `extras.seatInClip` (`ride_seat_in_clip`, root > 0.5 m), the boss goes at the Pal's model origin, tower size forced on, marker offset unused.
  A mount is always shown at its `palScale` (tower size): no toggle on the boss page, and selecting a rider on the Pal page forces it on.
  `poseMount` inverts bone * seat with the bone in the Pal's *unscaled* frame (the seat carries 1/palScale); measuring the bone with the Pal's scale
  applied cancelled the tower size (Forest/Snow/Grass/Sakurajima showed the Pal at ×1). Rider and mount go through `applyShading` with their own parser.
  Buried seats (`ViewerScene.seatLift`): Snow's BlackGriffon `BossRideMarker` track copies `spine_03`'s position, so the data seats Victor's feet
  ~1.2 m inside the torso (nothing in BP_PalBossNPCMarker / ABP_RidingBoss moves the boss). The intro cutscene (`AS_SnowBossStartDemo_*`, also
  extracted for Pals) ends with him standing on the back, i.e. the same seat lifted straight up. Rule: for a marker-bone seat (not seatInClip),
  if the rider's pelvis is inside the mount (ray parity, 3 of 5 rays) and >0.1 m below its surface, lift the rider along its up until both feet
  leave the body. Measured: Desert pelvis 1 cm deep (seated, untouched), Victor 22 cm -> lift 1.16 m; all others 0. Cutscene ends are not a
  general seat source (Forest's puts Lily in front of Lyleen's face).
  Pal clips offered under a rider are filtered by `mount_ok` -> clip `mountOk:false` (sleep/spa/work/death...). Unverified against the game: the
  Sakurajima (`moon`) and Sorajima seats look right but have no reference; `BP_<Pal>_GYM` animation differences are not checked.
  Clip pairs (`Humans.BossClipPairs` -> rides.json `pairs` -> `clip_pairs` -> mount/bossRide `pairs`; viewer `clipPairs.ts`): which Pal clips play
  while the boss plays each clip. Unique Active Skills: marker `WazaMontage[W]` (boss montage) <-> Pal `WazaActionDeclarationMap[W]` action BP ->
  its montage arrays (`Montage List`, or UpMontage/DownMontage/LandingMontage..., `Montages`) -> segments with their time range, else FlagName /
  class-name stem (Flarestorm -> FlareStorm_*, case-insensitive). Marker `Anim List` states: Idle/Encount by stem (shorter stems tried: Idle_SnowBoss
  -> Idle), Commonwaza <-> GeneralMontageMap Ready/Process/After_Far, UniqueWazaIn/UniqueWaza/UniqueWazaOut <-> Start/Loop/End of the one unique
  waza in the GYM learnset (DT_WazaMasterLevel `GYM_<Code>NNN`) not in WazaMontage (Snow TackleLaser, Desert FlareStorm). Then the boss's own
  `_Ride_<X>` clips by stem (Grass Ride_Spa <-> Spa; paired clips are offered under a rider even when mountOk is false).
  Timing (fitted on seat-in-clip bosses, boss pelvis vs the Pal's seat bone): one-shots at rate 1 back to back (the montage PlayRate, e.g. End
  x1.45, is runtime only); loops fill the leftover in whole cycles, in order (World Tree Supernova 40.17 = 2 + 26 x 1.33 + 3.5; < 1 cycle = hold).
  Viewer: boss page Mount dropdown "Auto" poses the Pal's sequence at the boss's time each frame (`driveMountPair`); Pal page Auto poses the boss
  clip of the pair containing the Pal's clip (`syncRiderClip`). Cutscene-only root flattening: `DEMO_CLIP_RE` (gameplay clips keep root: a
  seat-in-clip boss's root is its seat). Known gaps: Viking SnowImpact (the tiger's leap is actor motion in code, not in its clips), Electric's
  GYM_Act (its action names no montages; ThunderDescent/ThunderWire unmatched), Grass Ride_Waza* (no Pal clip by that stem).
  Boss held items: mesh components that are SCS children of the marker node in the GYM BP, on the boss's own socket (Lily's spear
  `SK_NPC_ForestBoss001_weapon` on Socket_Weapon_R) -> rides.json `held` -> boss `heldItems` (`_link_boss_rides`). `WeaponEquip` is False for every
  boss in DT_PalHumanParameter, so the `Weapon` type there is not a held item. Riders and mounts hold their own `heldItems` too
  (`attachOwnHeld`, own glTF parser, same clip gating). `^Aim` / `Minigun` clips are category battle, so they are mount-compatible.
- Human facial expressions are morph targets on the head/brow meshes (`eye_close`, `eye_smile`, `eye_anger`, `eye_pain`, `eye_trouble`, `mouth_open`, `unique`; 2-7 per boss).
  UE 5.1 cooks them as quantized batches in `FStaticLODModel.MorphTargetVertexInfoBuffers`: delta = (raw + batch.PositionMin) * PositionPrecision
  (CUE4Parse already adds IndexMin to the vertex index). `WriteMorphs` -> `morphs.bin` + pal.json `morphs`; importer `_morph_prim` -> glTF targets +
  mesh `targetNames`; viewer `ExpressionSection` = one weight slider per target (`ViewerScene.setMorph`). Like the Pals' eye cells they are set by
  gameplay code, not clips, so there are no presets. Pals with morph meshes would get them through the same path (not surveyed).
- Human eyes are layered (White, Cornea, Pupil, Highlight): `TextureCache.eye` flattens them. Weapon materials on other masters: `_classify_map`.
- Cloth/hair/belts are the KawaiiPhysics plugin (anim-layer nodes, `PalExtract physics` -> `physics.json`, manifest `extras.physics`, solver in
  `viewer/src/scene/springBones.ts`). Only Grass Boss has own nodes; the other bosses use a shared layer rooted at a bone they lack, so they hang rigid, as in the game.
