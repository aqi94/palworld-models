// Per-Pal export. Everything stays in Unreal's own conventions (left-handed, Z up, centimetres,
// quaternions x,y,z,w as stored by the engine); the Python importer does the glTF conversion.
//
// raw/palworld/<Code>/pal.json           index of everything below
// raw/palworld/<Code>/lod0.bin           vertex/index buffers (layout in pal.json "lods[].buffers")
// raw/palworld/<Code>/anims/<AS>.bin     per-frame local bone transforms (layout in pal.json "anims")
// raw/materials/<package path>.json      material instance chain (shared between Pals)
// raw/textures/<package path>.png        decoded textures (shared between Pals)

using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine.Curves;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse_Conversion.Animations;
using CUE4Parse_Conversion.Dto;
using CUE4Parse_Conversion.Options;
using CUE4Parse_Conversion.Textures;
using Newtonsoft.Json;

namespace PalExtract;

public static class Pals
{
    const string ModelRoot = "Pal/Content/Pal/Model/Character/Monster/";
    const string AnimRoot = "Pal/Content/Pal/Animation/Character/Monster/";
    const int MaxInfluences = 8;

    public static void Export(DefaultFileProvider provider, List<string> args)
    {
        var codes = args.Contains("--all-pals") ? AllCodes(provider) : args.Where(a => !a.StartsWith("--")).ToList();
        int ok = 0, fail = 0, skip = 0;
        foreach (var code in codes)
        {
            var outDir = Path.Combine(Program.RawDir, "palworld", code);
            if (!Program.Force && File.Exists(Path.Combine(outDir, "pal.json"))) { skip++; continue; }
            try
            {
                var t0 = DateTime.Now;
                var info = ExportOne(provider, code, outDir);
                Console.WriteLine($"{code,-32} {info.anims,4} anims {info.textures,3} tex {(DateTime.Now - t0).TotalSeconds,6:F1}s");
                ok++;
            }
            catch (Exception e)
            {
                Console.WriteLine($"{code,-32} FAILED: {e.GetType().Name}: {e.Message}");
                Directory.CreateDirectory(outDir);
                File.WriteAllText(Path.Combine(outDir, "error.txt"), e.ToString());
                fail++;
            }
        }
        Console.WriteLine($"done: {ok} ok, {fail} failed, {skip} skipped");
    }

    // `weapon`: held items. For every Pal whose blueprint names a UniqueRidePalWeaponClass (on its PalRideMarkerComponent), export
    // the weapon's skeletal meshes to raw/palworld_weapons/<name>/ (same layout as a Pal, no anims) and record the link in
    // raw/palworld_weapons/held.json: {pal, weapon, blueprint, mesh, parts: [{part, item, mesh}]}. Weapons can have several mesh
    // components (the dual grenade launcher has SkeletalMeshL/R); `item` names the exported model of each part.
    public static void ExportWeapons(DefaultFileProvider provider, List<string> args)
    {
        var codes = args.Where(a => !a.StartsWith("--")).ToList();
        if (codes.Count == 0) codes = AllCodes(provider);
        var held = new List<object>();
        var done = new HashSet<string>();
        foreach (var code in codes)
        {
            try
            {
                foreach (var e in EmbeddedWeapons(provider, code))
                {
                    var outDirE = Path.Combine(Program.RawDir, "palworld_weapons", e.item);
                    if (done.Add(e.item) && (Program.Force || !File.Exists(Path.Combine(outDirE, "pal.json"))))
                        ExportOne(provider, e.item, outDirE, e.mesh, false);
                    held.Add(new { pal = code, weapon = e.item, blueprint = e.blueprint, mesh = e.mesh, parts = new[] { e.part } });
                    Console.WriteLine($"{code,-32} {e.item} (component {e.part.part} on {e.part.socket})");
                }
                var bp = FindWeaponBlueprint(provider, code);
                if (bp == null) continue;
                var meshes = WeaponMeshes(provider, bp);
                if (meshes.Count == 0) { Console.WriteLine($"{code,-32} {bp}: no mesh"); continue; }
                var name = bp.Split('/').Last().Replace("BP_UniqueRideWeapon_", "");
                var items = new Dictionary<string, string>(); // mesh path -> item name; the first mesh keeps the weapon's name
                var parts = new List<object>();
                foreach (var (part, mesh, loc, rot, scale, mounted) in meshes)
                {
                    if (!items.TryGetValue(mesh, out var item))
                    {
                        item = items.Count == 0 ? name : $"{name}_{items.Count}";
                        items[mesh] = item;
                        var outDir = Path.Combine(Program.RawDir, "palworld_weapons", item);
                        if (done.Add(item) && (Program.Force || !File.Exists(Path.Combine(outDir, "pal.json"))))
                            ExportOne(provider, item, outDir, mesh, false);
                    }
                    parts.Add(new { part, item, mesh, loc, rot, scale, mounted }); // mounted: a static prop around the Pal (missile pods, prism towers), not held
                }
                held.Add(new { pal = code, weapon = name, blueprint = bp, mesh = meshes[0].mesh, parts });
                Console.WriteLine($"{code,-32} {name} [{string.Join(", ", meshes.Select(m => m.part))}]");
            }
            catch (Exception e) { Console.WriteLine($"{code,-32} FAILED: {e.GetType().Name}: {e.Message}"); }
        }
        Directory.CreateDirectory(Path.Combine(Program.RawDir, "palworld_weapons"));
        File.WriteAllText(Path.Combine(Program.RawDir, "palworld_weapons", "held.json"), JsonConvert.SerializeObject(held, Program.JsonSettings));
    }

    // BP_<Code> (or BP_<Code>_Normal, or the base Pal's) -> PalRideMarkerComponent.UniqueRidePalWeaponClass package path
    static string? FindWeaponBlueprint(DefaultFileProvider provider, string code)
    {
        var parts = code.Split('_');
        for (int n = parts.Length; n >= 1; n--)
        {
            var c = string.Join('_', parts.Take(n));
            foreach (var bpName in new[] { $"BP_{c}", $"BP_{c}_Normal" })
            {
                var path = $"Pal/Content/Pal/Blueprint/Character/Monster/PalActorBP/{c}/{bpName}";
                if (!provider.Files.ContainsKey(path + ".uasset")) continue;
                var json = JsonConvert.SerializeObject(provider.LoadPackage(path).GetExports(), Program.JsonSettings);
                var m = System.Text.RegularExpressions.Regex.Match(json, @"""UniqueRidePalWeaponClass"":\s*\{[^}]*?""ObjectPath"":\s*""([^""]+?)\.\d+""");
                if (m.Success) return m.Groups[1].Value;
            }
        }
        return null;
    }

    // `refs <folder prefix> <name>...`: which packages under the prefix mention any of the names (package name map lookup, no export parsing).
    public static void FindRefs(DefaultFileProvider provider, List<string> args)
    {
        var prefix = args[0]; var names = args.Skip(1).ToList();
        foreach (var f in provider.Files.Keys.Where(k => k.StartsWith(prefix) && k.EndsWith(".uasset")).OrderBy(k => k))
        {
            try
            {
                var pkg = provider.LoadPackage(f[..^".uasset".Length]);
                var hit = pkg.NameMap.Select(n => n.Name).Where(n => names.Any(x => n.Contains(x, StringComparison.OrdinalIgnoreCase))).Distinct().ToList();
                if (hit.Count > 0) Console.WriteLine($"{f}	{string.Join(" ", hit)}");
            }
            catch { }
        }
    }

    // `props`: survey of every extra mesh component (skeletal or static, not the body) in the Pals' blueprints, with the socket it is
    // attached to. Prints one line per component; used to find held / worn items.
    public static void ScanProps(DefaultFileProvider provider)
    {
        foreach (var f in provider.Files.Keys.Where(k => k.StartsWith("Pal/Content/Pal/Blueprint/Character/Monster/PalActorBP/") && k.EndsWith(".uasset")
                     && Path.GetFileName(k).StartsWith("BP_")).OrderBy(k => k))
        {
            var path = f[..^".uasset".Length];
            try
            {
                var rawJson = JsonConvert.SerializeObject(provider.LoadPackage(path).GetExports(), Program.JsonSettings);
                foreach (System.Text.RegularExpressions.Match m in System.Text.RegularExpressions.Regex.Matches(rawJson, @"Pal/Blueprint/(?:Weapon|Character/Palmi)/(?!Bullet|Explosion)[A-Za-z0-9_/]*?(BP_[A-Za-z0-9_]+)\.\d+").DistinctBy(m => m.Value))
                    Console.WriteLine($"{Path.GetFileName(path)}	REF	{m.Groups[1].Value}");
                var exports = Newtonsoft.Json.Linq.JArray.Parse(rawJson);
                var attach = new Dictionary<string, string>();
                foreach (var e in exports)
                    if ((string?)e["Type"] == "SCS_Node" && (string?)e["Properties"]?["InternalVariableName"] is string v)
                        attach[v] = $"{(string?)e["Properties"]?["AttachToName"]}<{(string?)e["Properties"]?["ParentComponentOrVariableName"]}";
                foreach (var e in exports)
                {
                    var type = (string?)e["Type"];
                    if (type is not ("SkeletalMeshComponent" or "StaticMeshComponent" or "PalSkeletalMeshComponent")) continue;
                    var mesh = (string?)e["Properties"]?["SkeletalMesh"]?["ObjectPath"] ?? (string?)e["Properties"]?["StaticMesh"]?["ObjectPath"];
                    var comp = ((string?)e["Name"] ?? "").Replace("_GEN_VARIABLE", "");
                    if (mesh == null || comp == "CharacterMesh0") continue;
                    var vis = (string?)e["Properties"]?["bVisible"] ?? "";
                    Console.WriteLine($"{Path.GetFileName(path)}	{type}	{comp}	{attach.GetValueOrDefault(comp, "-")}	{mesh}	visible={vis}	scale={e["Properties"]?["RelativeScale3D"]?.ToString(Formatting.None)}");
                }
            }
            catch (Exception ex) { Console.WriteLine($"{Path.GetFileName(path)}	FAILED {ex.Message}"); }
        }
    }

    // Weapons that are part of the Pal's own blueprint: a SkeletalMeshComponent holding a Model/Weapon mesh, attached to a skeleton
    // socket (Tanzee's rifle: SkeletalMesh -> SK_Weapon_AssaultRifle_004 on Socket_Weapon_R). The attach transform is engine data:
    // socket (bone + relative transform, from the skeleton) then the component's own relative transform.
    static List<(string item, string mesh, string blueprint, dynamic part)> EmbeddedWeapons(DefaultFileProvider provider, string code)
    {
        var res = new List<(string, string, string, dynamic)>();
        var tokens = code.Split('_');
        for (int n = tokens.Length; n >= 1 && res.Count == 0; n--)
        {
            var c = string.Join('_', tokens.Take(n));
            var path = $"Pal/Content/Pal/Blueprint/Character/Monster/PalActorBP/{c}/BP_{c}";
            if (!provider.Files.ContainsKey(path + ".uasset")) continue;
            var exports = Newtonsoft.Json.Linq.JArray.Parse(JsonConvert.SerializeObject(provider.LoadPackage(path).GetExports(), Program.JsonSettings));
            var attach = new Dictionary<string, string>();
            foreach (var e in exports)
                if ((string?)e["Type"] == "SCS_Node" && (string?)e["Properties"]?["InternalVariableName"] is string v && (string?)e["Properties"]?["AttachToName"] is string at)
                    attach[v] = at;
            foreach (var e in exports)
            {
                if ((string?)e["Type"] is not ("SkeletalMeshComponent" or "StaticMeshComponent")) continue;
                var meshPath = (string?)e["Properties"]?["SkeletalMesh"]?["ObjectPath"] ?? (string?)e["Properties"]?["StaticMesh"]?["ObjectPath"];
                var comp = ((string?)e["Name"] ?? "").Replace("_GEN_VARIABLE", "");
                if (meshPath == null || comp == "CharacterMesh0" || !attach.TryGetValue(comp, out var socket) || !socket.Contains("Weapon")) continue;
                meshPath = meshPath[..meshPath.LastIndexOf('.')];
                var item = MeshItemName(meshPath);
                var (bone, sl, sr) = FindSocket(provider, code, socket);
                var props = e["Properties"]!;
                res.Add((item, meshPath, path, new { part = comp, item, mesh = meshPath, socket, bone,
                    visible = (string?)props["bVisible"] != "False", socketLoc = sl, socketRot = sr, loc = V3(props["RelativeLocation"], 0), rot = Rot(props["RelativeRotation"]), scale = V3(props["RelativeScale3D"], 1) }));
            }
        }
        return res;
    }

    internal static double[] V3(Newtonsoft.Json.Linq.JToken? t, double d) => t == null ? new[] { d, d, d } : new[] { (double)(t["X"] ?? d), (double)(t["Y"] ?? d), (double)(t["Z"] ?? d) };
    internal static double[] Rot(Newtonsoft.Json.Linq.JToken? t) => t == null ? new double[3] : new[] { (double)(t["Pitch"] ?? 0), (double)(t["Yaw"] ?? 0), (double)(t["Roll"] ?? 0) };

    // SK_Weapon_Shotgun_004 -> Shotgun_004, SM_Weapon_Handgun_002 -> Handgun_002, SK_QueenBee_spear -> QueenBee_spear
    static string MeshItemName(string meshPath) => System.Text.RegularExpressions.Regex.Replace(meshPath.Split('/').Last(), @"^(S[KM]_)(Weapon_)?", "");

    // socket name -> (bone, relative location, relative rotation [pitch,yaw,roll]) from the Pal's skeleton; a socket that is not
    // found is taken to be a bone name
    internal static (string bone, double[] loc, double[] rot) FindSocket(DefaultFileProvider provider, string code, string socket)
        => FindSocketIn(provider, Path.Combine(Program.RawDir, "palworld", code, "pal.json"), socket);

    // same, for the skeleton named in an exported model's pal.json (a human's: raw/palworld_humans/<id>/pal.json)
    internal static (string bone, double[] loc, double[] rot) FindSocketIn(DefaultFileProvider provider, string info, string socket)
    {
        if (File.Exists(info))
        {
            var skel = (string?)Newtonsoft.Json.Linq.JObject.Parse(File.ReadAllText(info))["skeleton"];
            if (skel != null)
            {
                var pkg = skel.Contains('.') ? skel[..skel.LastIndexOf('.')] : skel;
                foreach (var e in Newtonsoft.Json.Linq.JArray.Parse(JsonConvert.SerializeObject(provider.LoadPackage(pkg).GetExports(), Program.JsonSettings)))
                {
                    if ((string?)e["Type"] != "SkeletalMeshSocket" || (string?)e["Properties"]?["SocketName"] != socket) continue;
                    var p = e["Properties"]!;
                    var l = p["RelativeLocation"]; var r = p["RelativeRotation"];
                    return ((string?)p["BoneName"] ?? socket,
                        l == null ? new double[3] : new[] { (double)(l["X"] ?? 0), (double)(l["Y"] ?? 0), (double)(l["Z"] ?? 0) },
                        r == null ? new double[3] : new[] { (double)(r["Pitch"] ?? 0), (double)(r["Yaw"] ?? 0), (double)(r["Roll"] ?? 0) });
                }
            }
        }
        return (socket, new double[3], new double[3]);
    }

    // every mesh component template of the weapon blueprint with a mesh: component name, mesh package path and the component's own
    // relative transform (the weapon actor attaches at the hand, its meshes sit at this offset: the hammer is 110 cm out, yawed 180, x5)
    static List<(string part, string mesh, double[] loc, double[] rot, double[] scale, bool mounted)> WeaponMeshes(DefaultFileProvider provider, string bp)
    {
        var json = JsonConvert.SerializeObject(provider.LoadPackage(bp).GetExports(), Program.JsonSettings);
        var res = new List<(string, string, double[], double[], double[], bool)>();
        foreach (var e in Newtonsoft.Json.Linq.JArray.Parse(json))
        {
            if ((string?)e["Type"] is not ("SkeletalMeshComponent" or "StaticMeshComponent")) continue;
            var path = (string?)e["Properties"]?["SkeletalMesh"]?["ObjectPath"] ?? (string?)e["Properties"]?["StaticMesh"]?["ObjectPath"];
            if (path == null) continue;
            var part = ((string?)e["Name"] ?? "").Replace("_GEN_VARIABLE", "");
            var p = e["Properties"]!;
            res.Add((part, path[..path.LastIndexOf('.')], V3(p["RelativeLocation"], 0), Rot(p["RelativeRotation"]), V3(p["RelativeScale3D"], 1), (string?)e["Type"] == "StaticMeshComponent"));
        }
        return res.OrderBy(r => r.Item1, StringComparer.Ordinal).ToList();
    }

    // code -> mesh package path. Enumerated by mesh, not by folder: variants keep their SK_ in the base Pal's folder
    // (Monster/LazyDragon/SK_LazyDragon_Electric). A mesh whose name differs from its folder is only a Pal if it is a row
    // in the Pal tables (otherwise it is a weapon/arm/ring part).
    static Dictionary<string, string> MeshMap(DefaultFileProvider provider)
    {
        var map = new SortedDictionary<string, string>(StringComparer.Ordinal);
        var skipped = new List<string>();
        var tableText = new Lazy<string>(() => string.Concat(new[] { "DT_PalMonsterParameter", "DT_PalBPClass", "DT_SkinDataTable" }
            .Select(t => Path.Combine(Program.RawDir, "tables", t + ".json")).Where(File.Exists).Select(File.ReadAllText)));
        foreach (var k in provider.Files.Keys.OrderBy(k => k, StringComparer.Ordinal))
        {
            if (!k.StartsWith(ModelRoot, StringComparison.OrdinalIgnoreCase) || !k.EndsWith(".uasset")) continue;
            var p = k[ModelRoot.Length..].Split('/');
            if (p.Length != 2 || !p[1].StartsWith("SK_", StringComparison.OrdinalIgnoreCase)) continue;
            var code = p[1][3..^".uasset".Length];
            if (!code.Equals(p[0], StringComparison.OrdinalIgnoreCase) && !tableText.Value.Contains($"\"{code}\""))
            {
                skipped.Add($"{p[0]}/{p[1]}");
                continue;
            }
            // folder-named mesh wins over a same-named variant mesh elsewhere
            if (!map.ContainsKey(code) || code.Equals(p[0], StringComparison.OrdinalIgnoreCase))
                map[code] = k[..^".uasset".Length];
        }
        foreach (var s in skipped) Console.WriteLine($"skipped non-Pal mesh: {s}");
        return new Dictionary<string, string>(map);
    }

    static Dictionary<string, string>? meshMap;
    static Dictionary<string, string> CachedMeshMap(DefaultFileProvider provider) => meshMap ??= MeshMap(provider);

    static List<string> AllCodes(DefaultFileProvider provider) => CachedMeshMap(provider).Keys.ToList();

    // `pal <Code>`: the folder-named mesh if present, else the mesh with that name in any Monster folder
    static string? FindMesh(DefaultFileProvider provider, string code)
    {
        var direct = $"{ModelRoot}{code}/SK_{code}";
        if (provider.Files.ContainsKey(direct + ".uasset")) return direct;
        return CachedMeshMap(provider).GetValueOrDefault(code);
    }

    internal static (int anims, int textures) ExportOne(DefaultFileProvider provider, string code, string outDir, string? meshOverride = null, bool withAnims = true, Func<string?, IEnumerable<string>>? animSource = null)
    {
        Directory.CreateDirectory(outDir);
        var meshPath = meshOverride ?? FindMesh(provider, code) ?? $"{ModelRoot}{code}/SK_{code}";
        // the export name can differ from the file name (SK_GrassMinotaur_Ice): take the package's skeletal mesh
        var smesh = provider.LoadPackage(meshPath).GetExports().OfType<UStaticMesh>().FirstOrDefault();
        if (smesh != null && !provider.LoadPackage(meshPath).GetExports().OfType<USkeletalMesh>().Any()) return ExportStatic(provider, outDir, meshPath, smesh);
        var mesh = provider.LoadPackage(meshPath).GetExports().OfType<USkeletalMesh>().First();
        var skeletonIdx = mesh.GetOrDefault<FPackageIndex>("Skeleton");
        var skeleton = skeletonIdx?.Load<USkeleton>();

        using var dto = new SkeletalMeshDto(mesh, EMeshQuality.Highest, ENaniteMeshFormat.NoNanite, false);
        var bones = dto.Bones.Select(b => new
        {
            name = b.Name,
            parent = b.ParentIndex,
            t = V(b.Transform.Translation),
            r = Q(b.Transform.Rotation),
            s = V(b.Transform.Scale3D),
        }).ToList();

        // Materials: slot -> material instance; export the whole parent chain + textures.
        var textures = new HashSet<string>();
        var materials = new List<object>();
        foreach (var m in dto.Materials)
        {
            var obj = m.Material?.Load<UMaterialInterface>();
            materials.Add(new { slot = m.SlotName, path = obj == null ? null : PkgPath(obj), chain = obj == null ? new List<string>() : ExportMaterialChain(obj, textures) });
        }

        // Material-only skins (<Code>_SkinDark/ with MI_ but no SK_): alternate materials for this mesh.
        var materialSkins = new Dictionary<string, List<object>>();
        foreach (var folder in MaterialSkinFolders(provider, code))
        {
            var list = new List<object>();
            foreach (var k in provider.Files.Keys.Where(k => k.StartsWith($"{ModelRoot}{folder}/", StringComparison.OrdinalIgnoreCase)
                         && Path.GetFileName(k).StartsWith("MI_", StringComparison.OrdinalIgnoreCase) && k.EndsWith(".uasset")).OrderBy(k => k))
            {
                var obj = provider.LoadPackage(k[..^".uasset".Length]).GetExports().OfType<UMaterialInterface>().FirstOrDefault();
                if (obj != null) list.Add(new { name = obj.Name, path = PkgPath(obj), chain = ExportMaterialChain(obj, textures) });
            }
            if (list.Count > 0) materialSkins[folder] = list;
        }

        // Aura shells: an unused MI_ in the Pal's own folder whose instance sets `OutlineWidth` (Enchanted Sword's cyan glow; the BP adds it
        // at runtime), plus the emissive-over-time curve next to it.
        var auras = new List<object>();
        var usedSlots = materials.Select(x => (string?)x.GetType().GetProperty("slot")?.GetValue(x)).ToHashSet();
        object? auraCurve = null;
        foreach (var k in provider.Files.Keys.Where(k => k.StartsWith($"{ModelRoot}{code}/", StringComparison.OrdinalIgnoreCase) && k.EndsWith(".uasset")).OrderBy(k => k))
        {
            var name = Path.GetFileNameWithoutExtension(k);
            try
            {
                if (name.StartsWith("MI_", StringComparison.OrdinalIgnoreCase) && !usedSlots.Contains(name))
                {
                    var obj = provider.LoadPackage(k[..^".uasset".Length]).GetExports().OfType<UMaterialInstanceConstant>().FirstOrDefault();
                    if (obj != null && obj.ScalarParameterValues.Any(p => p.ParameterInfo.Name.Text == "OutlineWidth"))
                        auras.Add(new { name = obj.Name, path = PkgPath(obj), chain = ExportMaterialChain(obj, textures) });
                }
                else if (name.Contains("Emissive", StringComparison.OrdinalIgnoreCase) && name.EndsWith("Curve", StringComparison.OrdinalIgnoreCase))
                {
                    var curve = provider.LoadPackage(k[..^".uasset".Length]).GetExports().OfType<UCurveFloat>().FirstOrDefault();
                    if (curve != null) auraCurve = curve.GetOrDefault<FRichCurve>("FloatCurve")!.Keys.Select(c => new { t = c.Time, v = c.Value }).ToList();
                }
            }
            catch (Exception e) { Console.Error.WriteLine($"aura scan {name}: {e.Message}"); }
        }

        // LOD0 only (the viewer shows LOD0; engines rebuild their own LODs).
        var lods = new List<object>();
        var lod = dto.LODs[0];
        lods.Add(WriteLod(lod, Path.Combine(outDir, "lod0.bin")));

        // Animations: AnimSequences in this Pal's animation folder (and the base Pal's, for variants)
        // whose Skeleton is this mesh's skeleton.
        var animDir = Path.Combine(outDir, "anims");
        Directory.CreateDirectory(animDir);
        var anims = new List<object>();
        var skelPath = skeleton == null ? null : PkgPath(skeleton);
        var animPaths = !withAnims ? Enumerable.Empty<string>() : animSource != null ? animSource(skelPath) : AnimCandidates(provider, code);
        foreach (var asPath in animPaths)
        {
            try
            {
                var seq = provider.LoadPackageObject<UAnimSequence>(asPath);
                var seqSkel = seq.Skeleton.ResolvedObject?.Load<USkeleton>();
                if (animSource == null && (seqSkel == null || skelPath == null || PkgPath(seqSkel) != skelPath)) continue; // a custom source (humans) already chose its sequences
                anims.Add(WriteAnim(seq, animDir));
            }
            catch (Exception e)
            {
                anims.Add(new { name = Path.GetFileName(asPath), error = e.Message });
            }
        }

        // Weapons use plain UMaterials (textures sit inside the expression graph, which is not exported): take the T_* textures under the mesh folder.
        var folderTextures = new List<string>();
        if (!withAnims && meshPath.Contains("/Model/Weapon/"))
        {
            // the mesh's folder tree: the sub-folder is Textures/, Texture/ or Material/ depending on the weapon
            var texDir = meshPath[..meshPath.LastIndexOf('/')] + "/";
            foreach (var k in provider.Files.Keys.Where(k => k.StartsWith(texDir, StringComparison.OrdinalIgnoreCase) && k.EndsWith(".uasset")
                         && k[(k.LastIndexOf('/') + 1)..].StartsWith("T_", StringComparison.OrdinalIgnoreCase)).OrderBy(k => k))
            {
                var tp = k[..^".uasset".Length];
                folderTextures.Add(tp);
                textures.Add(tp);
            }
        }

        foreach (var t in textures) ExportTexture(provider, t);

        var info = new
        {
            code,
            mesh = meshPath,
            skeleton = skelPath,
            bones,
            materialSkins,
            auras,
            auraCurve,
            // USkeleton reference pose + per-bone translation retargeting mode (animations are authored against this).
            skeletonBones = skeleton == null ? null : skeleton.ReferenceSkeleton.FinalRefBoneInfo.Select((bi, i) => new
            {
                name = bi.Name.Text,
                parent = bi.ParentIndex,
                t = V(skeleton.ReferenceSkeleton.FinalRefBonePose[i].Translation),
                r = Q(skeleton.ReferenceSkeleton.FinalRefBonePose[i].Rotation),
                s = V(skeleton.ReferenceSkeleton.FinalRefBonePose[i].Scale3D),
                retarget = i < skeleton.BoneTree.Length ? skeleton.BoneTree[i].ToString() : "",
            }).ToList(),
            materials,
            lods,
            anims,
            textures = textures.OrderBy(x => x).ToList(),
            folderTextures,
            morphTargets = dto.MorphTargets?.Length ?? 0,
            morphs = WriteMorphs(mesh, Path.Combine(outDir, "morphs.bin")),
            sockets = (mesh.GetOrDefault<FPackageIndex[]>("Sockets") ?? Array.Empty<FPackageIndex>())
                .Select(s => s.Load<USkeletalMeshSocket>()).Where(s => s != null)
                .Select(s => new { name = s!.SocketName.Text, bone = s.BoneName.Text, t = V(s.RelativeLocation), r = R(s.RelativeRotation), s = V(s.RelativeScale) }).ToList(),
        };
        File.WriteAllText(Path.Combine(outDir, "pal.json"), JsonConvert.SerializeObject(info, Program.JsonSettings));
        return (anims.Count, textures.Count);
    }

    // A static mesh (a held prop modelled as a StaticMeshComponent) in the skeletal layout: one root bone, every vertex weighted to it.
    static (int anims, int textures) ExportStatic(DefaultFileProvider provider, string outDir, string meshPath, UStaticMesh mesh)
    {
        using var dto = new StaticMeshDto(mesh);
        var textures = new HashSet<string>();
        var materials = dto.Materials.Select(m =>
        {
            var obj = m.Material?.Load<UMaterialInterface>();
            return (object)new { slot = m.SlotName, path = obj == null ? null : PkgPath(obj), chain = obj == null ? new List<string>() : ExportMaterialChain(obj, textures) };
        }).ToList();
        var lod = dto.LODs[0];
        var verts = lod.Vertices;
        int n = verts.Length;
        var buffers = new List<object>();
        var path = Path.Combine(outDir, "lod0.bin");
        using (var fs = File.Create(path))
        using (var w = new BinaryWriter(fs))
        {
            void Begin(string name, string dtype, int[] shape) => buffers.Add(new { name, dtype, shape, offset = fs.Position });
            Begin("position", "f4", new[] { n, 3 });
            foreach (var v in verts) { w.Write(v.Position.X); w.Write(v.Position.Y); w.Write(v.Position.Z); }
            Begin("normal", "f4", new[] { n, 4 });
            foreach (var v in verts) { w.Write(v.Normal.X); w.Write(v.Normal.Y); w.Write(v.Normal.Z); w.Write(v.Normal.W); }
            Begin("tangent", "f4", new[] { n, 4 });
            foreach (var v in verts) { w.Write(v.Tangent.X); w.Write(v.Tangent.Y); w.Write(v.Tangent.Z); w.Write(v.Tangent.W); }
            Begin("uv0", "f4", new[] { n, 2 });
            foreach (var v in verts) { w.Write(v.Uv.U); w.Write(v.Uv.V); }
            Begin("joints", "u2", new[] { n, MaxInfluences });
            for (int i = 0; i < n * MaxInfluences; i++) w.Write((ushort)0);
            Begin("weights", "f4", new[] { n, MaxInfluences });
            for (int i = 0; i < n; i++) for (int k = 0; k < MaxInfluences; k++) w.Write(k == 0 ? 1f : 0f);
            Begin("indices", "u4", new[] { lod.Indices.Length });
            foreach (var i in lod.Indices) w.Write(i);
        }
        var lods = new List<object> { new
        {
            vertexCount = n,
            sections = lod.Sections.Select(s => new { material = s.MaterialIndex, firstIndex = s.FirstIndex, numFaces = s.NumFaces }).ToList(),
            colorSets = new List<string>(), twoSided = false, verticesOverInfluenceLimit = 0, file = "lod0.bin", buffers,
        } };
        var folderTextures = new List<string>();
        var texDir = meshPath[..meshPath.LastIndexOf('/')] + "/";
        foreach (var k in provider.Files.Keys.Where(k => k.StartsWith(texDir, StringComparison.OrdinalIgnoreCase) && k.EndsWith(".uasset")
                     && k[(k.LastIndexOf('/') + 1)..].StartsWith("T_", StringComparison.OrdinalIgnoreCase)).OrderBy(k => k))
        {
            folderTextures.Add(k[..^".uasset".Length]);
            textures.Add(k[..^".uasset".Length]);
        }
        foreach (var t in textures) ExportTexture(provider, t);
        Directory.CreateDirectory(Path.Combine(outDir, "anims"));
        var root = new[] { new { name = "root", parent = -1, t = new double[3], r = new double[] { 0, 0, 0, 1 }, s = new double[] { 1, 1, 1 } } };
        var info = new { code = Path.GetFileName(outDir), mesh = meshPath, skeleton = (string?)null, bones = root, materialSkins = new Dictionary<string, List<object>>(),
            skeletonBones = (object?)null, materials, lods, anims = new List<object>(), textures = textures.OrderBy(x => x).ToList(), folderTextures,
            morphTargets = 0, sockets = new List<object>() };
        File.WriteAllText(Path.Combine(outDir, "pal.json"), JsonConvert.SerializeObject(info, Program.JsonSettings));
        return (0, textures.Count);
    }

    static IEnumerable<string> MaterialSkinFolders(DefaultFileProvider provider, string code)
    {
        var prefix = $"{ModelRoot}{code}_";
        var folders = provider.Files.Keys
            .Where(k => k.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            .Select(k => k[ModelRoot.Length..].Split('/'))
            .Where(p => p.Length == 2 && p[0].Contains("_Skin", StringComparison.OrdinalIgnoreCase))
            .GroupBy(p => p[0]);
        foreach (var g in folders)
            if (!g.Any(p => p[1].StartsWith("SK_", StringComparison.OrdinalIgnoreCase)))
                yield return g.Key;
    }

    static IEnumerable<string> AnimCandidates(DefaultFileProvider provider, string code)
    {
        // "Kitsunebi_Ice" -> also "Kitsunebi"; "WeaselDragon_Fire_Skin002" -> also "WeaselDragon_Fire", "WeaselDragon"
        var folders = new List<string>();
        var parts = code.Split('_');
        for (int n = parts.Length; n >= 1; n--) folders.Add(string.Join('_', parts.Take(n)));
        var seen = new HashSet<string>();
        foreach (var f in folders)
        {
            var prefix = $"{AnimRoot}{f}/";
            foreach (var k in provider.Files.Keys)
            {
                if (!k.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) || !k.EndsWith(".uasset")) continue;
                var name = Path.GetFileNameWithoutExtension(k);
                if (!name.StartsWith("AS_", StringComparison.OrdinalIgnoreCase)) continue;
                if (seen.Add(name)) yield return k[..^".uasset".Length];
            }
        }
        // tower-boss intro cutscenes animate the mount too: Demo/BossBattle<X>Start/Animation/AS_<X>StartDemo_<Code> (the skeleton check filters later)
        foreach (var k in provider.Files.Keys)
        {
            if (!k.StartsWith(DemoRoot, StringComparison.OrdinalIgnoreCase) || !k.EndsWith(".uasset")) continue;
            var name = Path.GetFileNameWithoutExtension(k);
            if (name.StartsWith("AS_", StringComparison.OrdinalIgnoreCase) && folders.Any(f => name.EndsWith("_" + f, StringComparison.OrdinalIgnoreCase)) && seen.Add(name))
                yield return k[..^".uasset".Length];
        }
    }
    const string DemoRoot = "Pal/Content/Pal/Demo/";

    // LOD0 morph targets (blend shapes: the humans' eye_close/eye_smile/mouth_open...) as sparse position deltas in Unreal space (cm).
    // UE 5.1 cooks them as batches of quantized deltas: delta = (quantized + batch.PositionMin) * PositionPrecision; CUE4Parse already
    // adds IndexMin to the vertex index. The vertex index addresses the same LOD0 vertex order as lod0.bin.
    // morphs.bin = per morph: n x u32 index, then n x 3 f32; pal.json "morphs" = [{name, count, offset}].
    static List<object> WriteMorphs(USkeletalMesh mesh, string path)
    {
        var list = new List<object>();
        var buf = mesh.LODModels?.FirstOrDefault()?.MorphTargetVertexInfoBuffers;
        if (buf == null || buf.BatchesPerMorph == null || buf.BatchesPerMorph.Length == 0 || mesh.MorphTargets == null) return list;
        using var fs = File.Create(path);
        using var w = new BinaryWriter(fs);
        for (int mi = 0; mi < buf.BatchesPerMorph.Length && mi < mesh.MorphTargets.Length; mi++)
        {
            var idx = new List<uint>(); var d = new List<float>();
            for (uint b = 0; b < buf.BatchesPerMorph[mi]; b++)
            {
                var info = buf.MorphData[buf.BatchStartOffsetPerMorph[mi] + b];
                foreach (var q in info.QuantizedDelta)
                {
                    idx.Add(q.Index);
                    d.Add((q.Position.X + info.PositionMin.X) * buf.PositionPrecision);
                    d.Add((q.Position.Y + info.PositionMin.Y) * buf.PositionPrecision);
                    d.Add((q.Position.Z + info.PositionMin.Z) * buf.PositionPrecision);
                }
            }
            list.Add(new { name = mesh.MorphTargets[mi].Name, count = idx.Count, offset = fs.Position });
            foreach (var i in idx) w.Write(i);
            foreach (var f in d) w.Write(f);
        }
        return list;
    }

    static object WriteLod(MeshLodDto<SkinnedMeshVertex> lod, string path)
    {
        var verts = lod.Vertices;
        int n = verts.Length;
        var buffers = new List<object>();
        using var fs = File.Create(path);
        using var w = new BinaryWriter(fs);
        void Begin(string name, string dtype, int[] shape) => buffers.Add(new { name, dtype, shape, offset = fs.Position });

        Begin("position", "f4", new[] { n, 3 });
        foreach (var v in verts) { w.Write(v.Position.X); w.Write(v.Position.Y); w.Write(v.Position.Z); }
        Begin("normal", "f4", new[] { n, 4 }); // w = binormal sign
        foreach (var v in verts) { w.Write(v.Normal.X); w.Write(v.Normal.Y); w.Write(v.Normal.Z); w.Write(v.Normal.W); }
        Begin("tangent", "f4", new[] { n, 4 });
        foreach (var v in verts) { w.Write(v.Tangent.X); w.Write(v.Tangent.Y); w.Write(v.Tangent.Z); w.Write(v.Tangent.W); }
        Begin("uv0", "f4", new[] { n, 2 });
        foreach (var v in verts) { w.Write(v.Uv.U); w.Write(v.Uv.V); }
        if (lod.ExtraUvs != null)
            for (int u = 0; u < lod.ExtraUvs.Length; u++)
            {
                Begin($"uv{u + 1}", "f4", new[] { n, 2 });
                foreach (var uv in lod.ExtraUvs[u]) { w.Write(uv.U); w.Write(uv.V); }
            }
        if (lod.VertexColors != null)
            for (int c = 0; c < lod.VertexColors.Length; c++)
            {
                Begin($"color{c}", "u1", new[] { n, 4 }); // RGBA
                foreach (var col in lod.VertexColors[c].Colors) { w.Write(col.R); w.Write(col.G); w.Write(col.B); w.Write(col.A); }
            }
        Begin("joints", "u2", new[] { n, MaxInfluences });
        foreach (var v in verts)
            for (int i = 0; i < MaxInfluences; i++) w.Write(i < (v.Influences?.Length ?? 0) ? v.Influences![i].Bone : (ushort)0);
        Begin("weights", "f4", new[] { n, MaxInfluences });
        int over = 0;
        foreach (var v in verts)
        {
            if ((v.Influences?.Length ?? 0) > MaxInfluences) over++;
            for (int i = 0; i < MaxInfluences; i++) w.Write(i < (v.Influences?.Length ?? 0) ? v.Influences![i].Weight : 0f);
        }
        Begin("indices", "u4", new[] { lod.Indices.Length });
        foreach (var i in lod.Indices) w.Write(i);

        return new
        {
            vertexCount = n,
            sections = lod.Sections.Select(s => new { material = s.MaterialIndex, firstIndex = s.FirstIndex, numFaces = s.NumFaces }).ToList(),
            colorSets = lod.VertexColors?.Select(c => c.Name).ToList(),
            twoSided = lod.IsTwoSided,
            verticesOverInfluenceLimit = over,
            file = Path.GetFileName(path),
            buffers,
        };
    }

    // The sequence's own enum properties as the asset stores them: CUE4Parse's AdditiveAnimType field stays AAT_None for this build
    // (the usmap has no enum names, so the tag reads "EAdditiveAnimationType::2"). Returns the trailing name / number.
    static string RawEnum(UAnimSequence seq, string prop)
    {
        var v = seq.Properties.FirstOrDefault(p => p.Name.Text == prop)?.Tag?.GenericValue?.ToString() ?? "";
        return v.Substring(v.LastIndexOf(':') + 1);
    }

    // An object property's target asset name (AnimSequence'AS_X' / package path / ...), "" when unset.
    static string RawRef(UAnimSequence seq, string prop)
    {
        var v = seq.Properties.FirstOrDefault(p => p.Name.Text == prop)?.Tag?.GenericValue?.ToString() ?? "";
        var m = System.Text.RegularExpressions.Regex.Match(v, @"(AS_[\w]+)");
        return m.Success ? m.Groups[1].Value : v;
    }

    // EAdditiveAnimationType: 0 None, 1 LocalSpaceBase, 2 RotationOffsetMeshSpace
    static readonly string[] AdditiveNames = { "AAT_None", "AAT_LocalSpaceBase", "AAT_RotationOffsetMeshSpace" };

    static object WriteAnim(UAnimSequence seq, string dir)
    {
        var rawAdd = RawEnum(seq, "AdditiveAnimType");
        var addType = int.TryParse(rawAdd, out var ai) && ai >= 0 && ai < AdditiveNames.Length ? AdditiveNames[ai]
            : rawAdd.StartsWith("AAT_") ? rawAdd : seq.AdditiveAnimType.ToString();
        var set = AnimConverter.ConvertAnims(seq);
        var s = set.Sequences[0];
        var skel = set.Skeleton.ReferenceSkeleton;
        int frames = Math.Max(s.NumFrames, 1);
        var name = seq.Name;
        var file = Path.Combine(dir, name + ".bin");
        var tracks = new List<object>();
        using (var fs = File.Create(file))
        using (var w = new BinaryWriter(fs))
        {
            for (int b = 0; b < s.Tracks.Count && b < skel.FinalRefBoneInfo.Length; b++)
            {
                var tr = s.Tracks[b];
                if (!tr.HasKeys()) continue;
                tracks.Add(new { bone = skel.FinalRefBoneInfo[b].Name.Text, offset = fs.Position });
                // per frame: quat xyzw, pos xyz, scale xyz (10 floats)
                for (int f = 0; f < frames; f++)
                {
                    // channels without keys keep the skeleton reference pose
                    var rp = skel.FinalRefBonePose[b];
                    FQuat q = rp.Rotation; FVector p = rp.Translation; FVector sc = rp.Scale3D;
                    tr.GetBoneTransform(f, frames, ref q, ref p, ref sc);
                    w.Write(q.X); w.Write(q.Y); w.Write(q.Z); w.Write(q.W);
                    w.Write(p.X); w.Write(p.Y); w.Write(p.Z);
                    w.Write(sc.X); w.Write(sc.Y); w.Write(sc.Z);
                }
            }
        }
        return new
        {
            name,
            file = "anims/" + name + ".bin",
            frames,
            length = seq.SequenceLength,
            fps = s.FramesPerSecond,
            additive = addType != "AAT_None",
            additiveType = addType,
            // the base the deltas were authored against (an AnimFrame pose is the first frame of RefPoseSeq)
            refPoseType = RawEnum(seq, "RefPoseType"),
            refPoseSeq = RawRef(seq, "RefPoseSeq"),
            refFrame = seq.GetOrDefault("RefFrameIndex", 0),
            rateScale = seq.GetOrDefault("RateScale", 1f),
            loop = seq.GetOrDefault("bLoop", false),
            tracks,
        };
    }

    // Material instance chain -> JSON files; collects every referenced texture package path.
    static List<string> ExportMaterialChain(UMaterialInterface mat, HashSet<string> textures)
    {
        var chain = new List<string>();
        UMaterialInterface? cur = mat;
        while (cur != null && chain.Count < 16)
        {
            var path = PkgPath(cur);
            chain.Add(path);
            var outPath = Path.Combine(Program.RawDir, "materials", path.Replace('/', Path.DirectorySeparatorChar) + ".json");
            if (cur is UMaterialInstance mi)
            {
                foreach (var tp in (mi as UMaterialInstanceConstant)?.TextureParameterValues ?? Array.Empty<FTextureParameterValue>())
                {
                    var tex = tp.ParameterValue?.Load<UTexture>();
                    if (tex != null) textures.Add(PkgPath(tex));
                }
                if (Program.Force || !File.Exists(outPath))
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(outPath)!);
                    File.WriteAllText(outPath, JsonConvert.SerializeObject(cur, Program.JsonSettings));
                }
                cur = mi.Parent?.Load<UMaterialInterface>();
            }
            else
            {
                // Base UMaterial: keep only its declared properties + parameter defaults (expressions are huge).
                var defaults = MaterialDefaults(cur);
                // engine / project stand-ins (DefaultBaseTexture, Engine Black...) mean "no map": not exported (importer.is_placeholder_texture)
        foreach (var t in defaults.textures.Values)
            if (!t.StartsWith("Engine/") && !System.Text.RegularExpressions.Regex.IsMatch(t[(t.LastIndexOf('/') + 1)..], "^(Default|T_Black|T_White|T_Default(Mask|Normal)|Black|White|Grey|Gray|FlatNormal)", System.Text.RegularExpressions.RegexOptions.IgnoreCase))
                textures.Add(t);
                if (Program.Force || !File.Exists(outPath) || !File.ReadAllText(outPath).Contains("\"Defaults\""))
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(outPath)!);
                    var props = cur.Properties.ToDictionary(p => p.Name.Text, p => p.Tag?.GenericValue);
                    File.WriteAllText(outPath, JsonConvert.SerializeObject(new { Type = cur.ExportType, Name = cur.Name, Properties = props, Defaults = new { defaults.scalars, defaults.vectors, defaults.textures } }, Program.JsonSettings));
                }
                cur = null;
            }
        }
        return chain;
    }

    // Parameter defaults of a base UMaterial, from its CachedExpressionData: RuntimeEntries[i].ParameterInfoSet names line up with
    // ScalarValues (i=0), VectorValues (1) and TextureValues (3). Instances only store overrides, so e.g. M_Pal_Dream's MainTex
    // (Daedream's gradient) exists only here. Texture soft paths "/Game/X.Y" become package paths "Pal/Content/X".
    internal static (Dictionary<string, float> scalars, Dictionary<string, Newtonsoft.Json.Linq.JToken> vectors, Dictionary<string, string> textures) MaterialDefaults(UObject mat)
    {
        var scalars = new Dictionary<string, float>();
        var vectors = new Dictionary<string, Newtonsoft.Json.Linq.JToken>();
        var tex = new Dictionary<string, string>();
        try
        {
            var j = Newtonsoft.Json.Linq.JToken.Parse(JsonConvert.SerializeObject(mat, Program.JsonSettings));
            var ced = (j is Newtonsoft.Json.Linq.JArray a ? a[0] : j)["CachedExpressionData"];
            if (ced == null) return (scalars, vectors, tex);
            List<string> Names(int i) => ced[i == 0 ? "RuntimeEntries" : $"RuntimeEntries[{i}]"]?["ParameterInfoSet"]?
                .Select(p => (string)p["Name"]!).ToList() ?? new List<string>();
            var sn = Names(0); var sv = ced["ScalarValues"]?.ToList() ?? new();
            for (int i = 0; i < Math.Min(sn.Count, sv.Count); i++) scalars.TryAdd(sn[i], (float)sv[i]);
            var vn = Names(1); var vv = ced["VectorValues"]?.ToList() ?? new();
            for (int i = 0; i < Math.Min(vn.Count, vv.Count); i++) vectors.TryAdd(vn[i], vv[i]);
            var tn = Names(3); var tv = ced["TextureValues"]?.ToList() ?? new();
            for (int i = 0; i < Math.Min(tn.Count, tv.Count); i++)
            {
                var p = ((string?)tv[i]["AssetPathName"] ?? "").Split('.')[0];
                if (p.StartsWith("/Game/")) tex.TryAdd(tn[i], "Pal/Content/" + p["/Game/".Length..]);
                else if (p.StartsWith("/Engine/")) tex.TryAdd(tn[i], "Engine/Content/" + p["/Engine/".Length..]);
            }
        }
        catch (Exception e) { Console.Error.WriteLine($"material defaults {mat.Name}: {e.Message}"); }
        return (scalars, vectors, tex);
    }

    static void ExportTexture(DefaultFileProvider provider, string path)
    {
        var outPath = Path.Combine(Program.RawDir, "textures", path.Replace('/', Path.DirectorySeparatorChar) + ".png");
        var meta = Path.ChangeExtension(outPath, ".json");
        if (!Program.Force && File.Exists(outPath)) return;
        try
        {
            var tex = provider.LoadPackageObject<UTexture>(path);
            var bmp = TextureDecoder.Decode(tex, ETexturePlatform.DesktopMobile);
            if (bmp == null) return;
            var bytes = TextureEncoder.Encode(bmp, ETextureFormat.Png, false, out _, 100);
            Directory.CreateDirectory(Path.GetDirectoryName(outPath)!);
            File.WriteAllBytes(outPath, bytes);
            File.WriteAllText(meta, JsonConvert.SerializeObject(new
            {
                width = bmp.Width,
                height = bmp.Height,
                format = tex.Format.ToString(),
                srgb = tex.SRGB,
                compression = tex.CompressionSettings.ToString(),
                addressX = tex.GetOrDefault("AddressX", TextureAddress.TA_Wrap).ToString(),
                addressY = tex.GetOrDefault("AddressY", TextureAddress.TA_Wrap).ToString(),
            }, Program.JsonSettings));
        }
        catch (Exception e)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(outPath)!);
            File.WriteAllText(Path.ChangeExtension(outPath, ".error.txt"), e.ToString());
        }
    }

    internal static string PkgPath(UObject o) => o.GetPathName().Split('.')[0];
    static float[] V(FVector v) => new[] { v.X, v.Y, v.Z };
    static float[] Q(FQuat q) => new[] { q.X, q.Y, q.Z, q.W };
    static float[] R(FRotator r) => new[] { r.Pitch, r.Yaw, r.Roll };
}
