// Shared human animation sets and NPC weapons, read from the blueprints (no per-model data).
//
// In game an NPC is DT_PalHumanParameter[CharacterID] -> BPClass row of DT_PalBPClass -> BP_NPC_<X>. Its parent chain ends in a
// data set (BP_NPC_StandardHumanDataSet / BP_NPC_FatHumanDataSet) whose components hold:
//   PalShooterComponent.DefaultWeaponAnimeAssetBPSoftClassMap   weapon type -> ShooterAnime BP (locomotion + aim/fire/reload for
//                                                               that weapon; `None` = unarmed). Mostly the player's own sets.
//   PalShooterComponent.OtherWeaponAnimeAssetBPSoftClassMap     weapon BP class -> ShooterAnime BP (pump-action shotgun)
//   *MontageMap / *BlendSpaceMap                                action / general montages (work, sit, sleep, hit...)
// The per-outfit anim layer (Imp/ABP_NPC_<Outfit>_Implimentation) only holds cloth physics. Every human sequence is authored on
// SK_PalHuman_Skeleton with retarget mode Animation on every bone, so one set plays unchanged on every human mesh.
// The weapon: DT_PalHumanParameter.Weapon (EPalWeaponType, used when WeaponEquip) -> the NPC's weapon generator WeaponIDMap ->
// item -> DT_ItemDataTable.ItemActorClass -> DT_ItemActorClassDataTable -> weapon BP -> its mesh components.
//
// `npcsets`  -> raw/palworld_humans/animsets.json {sets, meshes, weapons}
// `animpack` -> raw/palworld_animpacks/<SetId>/{pal.json, anims/} (skeleton ref pose, no mesh)

using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports.Animation;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using System.Text.RegularExpressions;

namespace PalExtract;

public static class NpcSets
{
    const string Content = "Pal/Content/";
    public const string HumanSkeleton = "Pal/Content/Pal/Model/Character/Skeleton/Human/SK_PalHuman_Skeleton";
    const string DefaultGenerator = "Pal/Content/Pal/Blueprint/Character/NPC/BP_NPCWeaponGenerator";
    static string SetsPath => Path.Combine(Program.RawDir, "palworld_humans", "animsets.json");

    // "/Game/Pal/X/BP_Y.BP_Y_C" or "Pal/Content/Pal/X/BP_Y.3" -> "Pal/Content/Pal/X/BP_Y"
    static string Pkg(string p)
    {
        if (p.StartsWith("/Game/")) p = Content + p["/Game/".Length..];
        var dot = p.LastIndexOf('.');
        return dot > p.LastIndexOf('/') ? p[..dot] : p;
    }

    static readonly Dictionary<string, JArray> cache = new();
    static JArray Exports(DefaultFileProvider provider, string pkg)
    {
        if (!cache.TryGetValue(pkg, out var a))
            cache[pkg] = a = JArray.Parse(JsonConvert.SerializeObject(provider.LoadPackage(pkg).GetExports(), Program.JsonSettings));
        return a;
    }

    // blueprint packages, child first, while the parent (SuperStruct) is a blueprint
    static List<string> Chain(DefaultFileProvider provider, string bp)
    {
        var res = new List<string>();
        for (string? cur = bp; cur != null && res.Count < 16 && provider.Files.ContainsKey(cur + ".uasset");)
        {
            res.Add(cur);
            var cls = Exports(provider, cur).FirstOrDefault(e => ((string?)e["Type"] ?? "").EndsWith("BlueprintGeneratedClass") && e["SuperStruct"] != null);
            var sup = (string?)cls?["SuperStruct"]?["ObjectPath"];
            cur = sup == null ? null : Pkg(sup);
        }
        return res;
    }

    static readonly Regex HardRef = new(@"""ObjectName"":\s*""(AnimSequence|AnimMontage|AnimComposite|BlendSpace\w*|AimOffsetBlendSpace\w*)'[^']*'"",\s*""ObjectPath"":\s*""([^""]+)""");
    static readonly Regex SoftRef = new(@"""AssetPathName"":\s*""(/Game/[^"".]+/(AS|AM|BS|AO)_[^"".]+)\.[^""]*""");

    // anim assets referenced anywhere in a JSON fragment: (is sequence, package)
    static IEnumerable<(bool seq, string pkg)> AnimRefs(string json)
    {
        foreach (Match m in HardRef.Matches(json)) yield return (m.Groups[1].Value == "AnimSequence", Pkg(m.Groups[2].Value));
        foreach (Match m in SoftRef.Matches(json)) yield return (m.Groups[2].Value == "AS", Pkg(m.Groups[1].Value));
    }

    // every AnimSequence reachable from the fragment: montages, composites and blend spaces are followed to their sequences
    static void Closure(DefaultFileProvider provider, string json, SortedSet<string> seqs, HashSet<string> seen)
    {
        foreach (var (isSeq, pkg) in AnimRefs(json))
        {
            if (!seen.Add(pkg)) continue;
            if (isSeq) { seqs.Add(pkg); continue; }
            if (!provider.Files.ContainsKey(pkg + ".uasset")) continue;
            try { Closure(provider, Exports(provider, pkg).ToString(Formatting.None), seqs, seen); } catch { }
        }
    }

    // only the sequences authored on the shared human skeleton (montages can also drive weapon / prop skeletons)
    static readonly Dictionary<string, string?> seqSkeleton = new();
    static bool OnHumanSkeleton(DefaultFileProvider provider, string pkg)
    {
        if (!seqSkeleton.TryGetValue(pkg, out var s))
        {
            try { s = provider.LoadPackageObject<UAnimSequence>(pkg).Skeleton.ResolvedObject?.Load<USkeleton>() is { } sk ? Pals.PkgPath(sk) : null; }
            catch { s = null; }
            seqSkeleton[pkg] = s;
        }
        return s == HumanSkeleton;
    }

    static Dictionary<string, JObject> Rows(string table)
    {
        var res = new Dictionary<string, JObject>(StringComparer.OrdinalIgnoreCase);
        foreach (var name in new[] { table, table + "_Common" })
        {
            var p = Path.Combine(Program.RawDir, "tables", name + ".json");
            if (!File.Exists(p)) continue;
            foreach (var e in JArray.Parse(File.ReadAllText(p)))
                if (e["Rows"] is JObject rows)
                    foreach (var kv in rows.Properties()) res.TryAdd(kv.Name, (JObject)kv.Value);
        }
        return res;
    }

    // EPalWeaponType::4 -> AssaultRifle (enum names from the mappings)
    static Dictionary<int, string>? weaponEnum;
    static string? WeaponName(DefaultFileProvider provider, string? raw)
    {
        if (raw == null) return null;
        var v = raw[(raw.LastIndexOf(':') + 1)..];
        if (!int.TryParse(v, out var i)) return v;
        if (weaponEnum == null)
        {
            weaponEnum = WeaponEnumFromData(provider);
            var enums = provider.MappingsForGame?.Enums;
            if (enums != null && enums.TryGetValue("EPalWeaponType", out var e))
                foreach (var kv in e) weaponEnum[(int)kv.Key] = kv.Value.Contains("::") ? kv.Value[(kv.Value.LastIndexOf(':') + 1)..] : kv.Value;
            if (weaponEnum.Count == 0)
                Console.WriteLine($"WARNING: no EPalWeaponType names in the mappings; enums with Weapon: {string.Join(", ", (enums?.Keys ?? Enumerable.Empty<string>()).Where(k => k.Contains("Weapon")))} ({enums?.Count ?? 0} enums)");
        }
        return weaponEnum.GetValueOrDefault(i); // 0 and the gaps: no weapon
    }

    // The usmap carries EPalWeaponType without its values. The data pairs them: every weapon generator's WeaponIDMap is keyed by the
    // type's name, and the item it names resolves to a weapon blueprint whose class default stores WeaponType as the number.
    static Dictionary<int, string> WeaponEnumFromData(DefaultFileProvider provider)
    {
        var res = new Dictionary<int, string>();
        var items = Rows("DT_ItemDataTable");
        var actors = ActorClasses(provider);
        foreach (var gen in provider.Files.Keys.Where(k => k.StartsWith("Pal/Content/Pal/Blueprint/Character/NPC/BP_NPCWeaponGenerator") && k.EndsWith(".uasset")))
            foreach (var e in Exports(provider, gen[..^".uasset".Length]))
                foreach (var map in new[] { e["Properties"]?["WeaponIDMap"], e["Properties"]?["OtomoWeaponIDMap"] }.OfType<JArray>())
                    foreach (var it in map)
                    {
                        var name = ((string?)it["Key"] ?? "").Split("::").Last();
                        var item = (string?)it["Value"];
                        if (item == null || name.Length == 0) continue;
                        var actor = items.TryGetValue(item, out var ir) ? (string?)ir["ItemActorClass"] ?? item : item;
                        if (!actors.TryGetValue(actor, out var bp)) continue;
                        foreach (var b in Chain(provider, bp))
                        {
                            var m = Regex.Match(Exports(provider, b).ToString(Formatting.None), @"""WeaponType"":\s*""EPalWeaponType::(\d+)""");
                            if (!m.Success) continue;
                            var n = int.Parse(m.Groups[1].Value);
                            if (res.TryGetValue(n, out var old) && old != name) Console.WriteLine($"WARNING: EPalWeaponType {n} is both {old} and {name}");
                            else res[n] = name;
                            break;
                        }
                    }
        Console.WriteLine($"EPalWeaponType from data: {string.Join(", ", res.OrderBy(k => k.Key).Select(k => $"{k.Key}={k.Value}"))}");
        return res;
    }

    static Dictionary<string, string>? actorClasses;
    static Dictionary<string, string> ActorClasses(DefaultFileProvider provider)
    {
        if (actorClasses != null) return actorClasses;
        actorClasses = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        try
        {
            foreach (var e in Exports(provider, "Pal/Content/PalEditor/DataTable/DT_ItemActorClassDataTable"))
                if (e["Rows"] is JObject rows)
                    foreach (var kv in rows.Properties())
                        if ((string?)kv.Value["ClassName"] is string cn && (string?)kv.Value["actorClass"]?["AssetPathName"] is string ap && ap != "None") actorClasses[cn] = Pkg(ap);
        }
        catch (Exception e) { Console.WriteLine($"DT_ItemActorClassDataTable: {e.Message}"); }
        return actorClasses;
    }

    record SetInfo(string id, string source, string kind, string? weaponType, SortedSet<string> anims, string? idle = null);

    // the stance a ShooterAnime BP stands in: IdlePoseMap[Stand] of the first blueprint in its chain that sets it
    static string? StandIdle(DefaultFileProvider provider, string bp)
    {
        foreach (var b in Chain(provider, bp))
            foreach (var e in Exports(provider, b))
                if ((e["Properties"]?["IdlePoseMap"] ?? e["Properties"]?["AnimeInfo"]?["IdlePoseMap"]) is JArray map)
                    foreach (var it in map)
                        if (((string?)it["Key"] ?? "").EndsWith("::Stand") && (string?)it["Value"]?["ObjectPath"] is string op) return Pkg(op);
        return null;
    }

    public static void Export(DefaultFileProvider provider, List<string> args)
    {
        var bpRows = Rows("DT_PalBPClass");
        var human = Rows("DT_PalHumanParameter");
        var names = Rows("DT_HumanNameText_Common.en");
        var palNames = new Dictionary<string, JObject>(StringComparer.OrdinalIgnoreCase);
        var pnp = Path.Combine(Program.RawDir, "tables", "DT_PalNameText_Common.en.json");
        if (File.Exists(pnp))
            foreach (var e in JArray.Parse(File.ReadAllText(pnp)))
                if (e["Rows"] is JObject rows) foreach (var kv in rows.Properties()) palNames[kv.Name] = (JObject)kv.Value;
        var sets = new Dictionary<string, SetInfo>();
        var setOfShooter = new Dictionary<string, List<string>>(); // ShooterAnime BP -> its set ids (own + parents)

        // a ShooterAnime BP and its blueprint parents each become a set (the parent's defaults are shared by every weapon)
        List<string> ShooterSets(string bp, string? weaponType)
        {
            if (setOfShooter.TryGetValue(bp, out var ids)) return ids;
            ids = new List<string>();
            foreach (var b in Chain(provider, bp))
            {
                var id = b.Split('/').Last().Replace("BP_", "");
                if (!sets.ContainsKey(id))
                {
                    var seqs = new SortedSet<string>();
                    // only this blueprint's own objects: the class default object and its subobjects
                    var own = new JArray(Exports(provider, b).Where(e => !((string?)e["Type"] ?? "").EndsWith("BlueprintGeneratedClass") && (string?)e["Type"] != "Function"));
                    Closure(provider, own.ToString(Formatting.None), seqs, new HashSet<string>());
                    seqs.RemoveWhere(s => !OnHumanSkeleton(provider, s));
                    var idle = StandIdle(provider, b);
                    if (idle != null && OnHumanSkeleton(provider, idle)) seqs.Add(idle); else idle = null;
                    sets[id] = new SetInfo(id, b, b == bp ? "weapon" : "base", b == bp ? weaponType : null, seqs, idle);
                }
                ids.Add(id);
            }
            setOfShooter[bp] = ids;
            return ids;
        }

        // CharacterID -> resolved blueprint data
        var meshes = new Dictionary<string, Dictionary<string, object?>>(StringComparer.OrdinalIgnoreCase);
        var meshChars = new Dictionary<string, List<(string cid, string? weapon, string? name, string bp)>>();
        var meshData = new Dictionary<string, (List<string> own, string actions, string generator, Dictionary<string, List<string>> shooters)>();
        var allShooters = new SortedDictionary<string, List<string>>();
        var shooterTargets = new HashSet<(string wt, string bp)>(); // weapon type -> set ids, over every data set
        foreach (var (cid, row) in human.OrderBy(k => k.Key, StringComparer.Ordinal))
        {
            var bpKey = (string?)row["BPClass"];
            if (bpKey == null || !bpRows.TryGetValue(bpKey, out var bpRow)) continue;
            var soft = (string?)bpRow["BPClass"]?["AssetPathName"];
            if (soft == null || !soft.Contains("/Blueprint/Character/NPC/")) continue;
            var bp = Pkg(soft);
            if (!provider.Files.ContainsKey(bp + ".uasset")) continue;
            try
            {
                var chain = Chain(provider, bp);
                string? mesh = null, generator = null;
                var shooter = new Dictionary<string, string>();       // weapon type -> ShooterAnime BP (child wins)
                var maps = new Dictionary<string, string>();          // "<map>|<key>" -> value JSON (child wins)
                var actionSource = chain.Last();
                // shooter keys the character's own blueprints set before its data set (Zoe's GrassBoss stance, the farmers' Female2 walk)
                var dsIndex = chain.FindIndex(b => b.EndsWith("DataSet"));
                if (dsIndex < 0) dsIndex = chain.Count - 1;
                var overrideKeys = new List<string>();
                foreach (var b in chain)
                {
                    var ex = Exports(provider, b);
                    foreach (var e in ex)
                    {
                        var props = e["Properties"] as JObject;
                        if (props == null) continue;
                        if (mesh == null && ((string?)e["Name"] ?? "").StartsWith("CharacterMesh0") && (string?)props["SkeletalMesh"]?["ObjectPath"] is string mp) mesh = Pkg(mp);
                        foreach (var kv in props.Properties())
                        {
                            if (kv.Value is not JArray arr) continue;
                            if (kv.Name is "DefaultWeaponAnimeAssetBPSoftClassMap" or "OtherWeaponAnimeAssetBPSoftClassMap")
                            {
                                foreach (var it in arr)
                                {
                                    var k = (string?)it["Key"]; var v = (string?)it["Value"]?["AssetPathName"];
                                    if (k == null || v == null || v == "None") continue;
                                    var wt = k.StartsWith("EPalWeaponType::") ? k["EPalWeaponType::".Length..] : k.Split('/').Last().Split('.').First().Replace("BP_", "");
                                    if (shooter.TryAdd(wt, Pkg(v)) && chain.IndexOf(b) < dsIndex) overrideKeys.Add(wt);
                                }
                            }
                            else if (Regex.IsMatch(kv.Name, "(Montage|BlendSpace)Map$"))
                            {
                                foreach (var it in arr)
                                {
                                    var key = kv.Name + "|" + it["Key"]?.ToString(Formatting.None);
                                    if (maps.TryAdd(key, it["Value"]?.ToString(Formatting.None) ?? "") && AnimRefs(it["Value"]?.ToString(Formatting.None) ?? "").Any() && b != chain.Last())
                                        if (chain.IndexOf(b) < chain.IndexOf(actionSource)) actionSource = b;
                                }
                            }
                        }
                    }
                    if (generator == null)
                    {
                        var g = Regex.Match(ex.ToString(Formatting.None), @"Blueprint/Character/NPC/(BP_NPCWeaponGenerator\w*)\.");
                        if (g.Success) generator = $"Pal/Content/Pal/Blueprint/Character/NPC/{g.Groups[1].Value}";
                    }
                }
                if (mesh == null) { Console.WriteLine($"{cid,-34} {bp.Split('/').Last()}: no mesh"); continue; }
                generator ??= DefaultGenerator;

                // the action set is named after the deepest blueprint that sets any of its montages (most NPCs: the data set)
                var actionsId = "Actions_" + actionSource.Split('/').Last().Replace("BP_", "");
                if (!sets.TryGetValue(actionsId, out var aset))
                {
                    var seqs = new SortedSet<string>();
                    Closure(provider, "[" + string.Join(",", maps.Values.Where(v => v.Length > 0)) + "]", seqs, new HashSet<string>());
                    seqs.RemoveWhere(s => !OnHumanSkeleton(provider, s));
                    sets[actionsId] = aset = new SetInfo(actionsId, actionSource, "actions", null, seqs);
                }
                var shooters = new Dictionary<string, List<string>>();
                foreach (var (wt, sbp) in shooter.OrderBy(k => k.Key, StringComparer.Ordinal))
                {
                    shooterTargets.Add((wt, sbp));
                    var ids = ShooterSets(sbp, wt);
                    shooters[wt] = ids;
                    if (!allShooters.ContainsKey(wt) || !allShooters[wt].SequenceEqual(ids)) allShooters.TryAdd(wt, ids);
                }
                var weapon = (bool?)row["WeaponEquip"] == true ? WeaponName(provider, (string?)row["Weapon"]) : null;
                var nameId = (string?)row["OverrideNameTextID"];
                var name = nameId != null && names.TryGetValue(nameId, out var n) ? (string?)n["TextData"]?["LocalizedString"] : null;
                // tower bosses are named in the Pal name table (PAL_NAME_ForestBoss = "Lily & Lyleen")
                if ((name == null || name == "-") && palNames.TryGetValue("PAL_NAME_" + cid, out var pn)) name = (string?)pn["TextData"]?["LocalizedString"];
                name = name?.Trim();
                if (!meshChars.TryGetValue(mesh, out var list)) meshChars[mesh] = list = new();
                list.Add((cid, weapon, name, bp));
                if (!meshData.ContainsKey(mesh)) meshData[mesh] = (overrideKeys, actionsId, generator, shooters);
            }
            catch (Exception e) { Console.WriteLine($"{cid,-34} FAILED: {e.GetType().Name}: {e.Message}"); }
        }

        // NPC weapon items: weapon type -> item (generator WeaponIDMap) -> actor class -> weapon BP -> mesh components
        var items = Rows("DT_ItemDataTable");
        var actorClasses = ActorClasses(provider);

        var weapons = new SortedDictionary<string, object>(); // "<generator>|<weapon type>" -> weapon info
        object? WeaponFor(string generator, string wt)
        {
            var key = generator.Split('/').Last() + "|" + wt;
            if (weapons.TryGetValue(key, out var w)) return w;
            string? item = null;
            foreach (var e in Exports(provider, generator))
                if (e["Properties"]?["WeaponIDMap"] is JArray map && item == null)
                    item = map.Where(it => ((string?)it["Key"] ?? "").EndsWith("::" + wt)).Select(it => (string?)it["Value"]).FirstOrDefault();
            if (item == null && generator != DefaultGenerator) return WeaponFor(DefaultGenerator, wt);
            if (item == null) return null;
            var actor = items.TryGetValue(item, out var ir) ? (string?)ir["ItemActorClass"] : item;
            if (actor == null || !actorClasses.TryGetValue(actor, out var wbp)) { Console.WriteLine($"weapon {wt}: item {item} has no actor class"); return null; }
            // mesh components over the weapon blueprint chain (child first, by component name)
            var parts = new List<object>();
            var seenComp = new HashSet<string>();
            foreach (var b in Chain(provider, wbp))
                foreach (var e in Exports(provider, b))
                {
                    if ((string?)e["Type"] is not ("SkeletalMeshComponent" or "StaticMeshComponent" or "PalSkeletalMeshComponent" or "PalStaticMeshComponent")) continue;
                    var p = e["Properties"]; if (p == null) continue;
                    var comp = ((string?)e["Name"] ?? "").Replace("_GEN_VARIABLE", "");
                    var mp = (string?)(p["SkeletalMesh"] as JObject)?["ObjectPath"] ?? (string?)(p["StaticMesh"] as JObject)?["ObjectPath"];
                    if (mp == null || !seenComp.Add(comp)) continue;
                    if ((string?)p["bVisible"] == "False" || (string?)p["bHiddenInGame"] == "True") continue;
                    var meshPath = Pkg(mp);
                    var itemName = Regex.Replace(meshPath.Split('/').Last(), @"^(S[KM]_)(Weapon_)?", "");
                    var outDir = Path.Combine(Program.RawDir, "palworld_weapons", itemName);
                    if (Program.Force || !File.Exists(Path.Combine(outDir, "pal.json")))
                    {
                        try { Pals.ExportOne(provider, itemName, outDir, meshPath, false); }
                        catch (Exception ex) { Console.WriteLine($"  weapon mesh {meshPath}: {ex.Message}"); continue; }
                    }
                    parts.Add(new { part = comp, item = itemName, mesh = meshPath, bone = "weapon_r", loc = Pals.V3(p["RelativeLocation"], 0), rot = Pals.Rot(p["RelativeRotation"]), scale = Pals.V3(p["RelativeScale3D"], 1) });
                }
            w = new { weaponType = wt, item, blueprint = wbp, parts };
            weapons[key] = w;
            Console.WriteLine($"weapon {key,-40} {item} -> {wbp.Split('/').Last()} [{string.Join(", ", parts.Select(x => ((dynamic)x).item))}]");
            return w;
        }

        foreach (var (mesh, chars) in meshChars.OrderBy(k => k.Key, StringComparer.Ordinal))
        {
            var d = meshData[mesh];
            // the weapon most of this mesh's characters equip (ties: alphabetical); null = unarmed
            var weapon = chars.Where(c => c.weapon != null).GroupBy(c => c.weapon!).OrderByDescending(g => g.Count()).ThenBy(g => g.Key, StringComparer.Ordinal).Select(g => g.Key).FirstOrDefault();
            var name = chars.Where(c => c.name != null && c.name != "-").GroupBy(c => c.name!).OrderByDescending(g => g.Count()).ThenBy(g => g.Key, StringComparer.Ordinal).Select(g => g.Key).FirstOrDefault();
            var own = new List<string> { d.actions };
            if (d.shooters.TryGetValue("None", out var none)) own.AddRange(none.AsEnumerable().Reverse());
            if (weapon != null && d.shooters.TryGetValue(weapon, out var ws)) own.AddRange(ws.AsEnumerable().Reverse());
            foreach (var k in d.own) if (d.shooters.TryGetValue(k, out var ov)) own.AddRange(ov.AsEnumerable().Reverse());
            own = own.Distinct().ToList();
            var weaponTypes = d.shooters.Keys.ToList();
            meshes[mesh] = new Dictionary<string, object?>
            {
                ["characterIds"] = chars.Select(c => c.cid).ToList(),
                ["blueprints"] = chars.Select(c => c.bp).Distinct().ToList(),
                ["name"] = name,
                ["weaponType"] = weapon,
                ["weaponTypes"] = chars.Where(c => c.weapon != null).Select(c => c.weapon).Distinct().ToList(),
                ["generator"] = d.generator,
                ["actions"] = d.actions,
                ["own"] = own,
                // shooter keys this mesh's blueprints override (their sets are the character's own stance)
                ["overrides"] = d.own,
                // every weapon type of this mesh's data set -> its set chain (base first)
                ["shooters"] = d.shooters.ToDictionary(k => k.Key, k => k.Value.AsEnumerable().Reverse().ToList()),
            };
            foreach (var wt in d.shooters.Keys) WeaponFor(d.generator, wt);
        }
        foreach (var wt in allShooters.Keys) WeaponFor(DefaultGenerator, wt);

        // a set a shooter map names directly is a weapon set, even when it is also another weapon's parent (RocketLauncher)
        foreach (var (wt, bp) in shooterTargets)
        {
            var id = bp.Split('/').Last().Replace("BP_", "");
            if (sets.TryGetValue(id, out var si) && si.kind != "weapon") sets[id] = si with { kind = "weapon", weaponType = wt };
        }
        // Catch-all sets for the remaining human sequences, so no human folder copies shared clips: NPC idles/emotes/attacks that
        // gameplay code plays (lean, pray, talk variants, melee combos; no data set names them) and the player's own extras (gliders,
        // petting, emotes, the other weapons). Boss folders stay with their boss.
        var inSets = sets.Values.SelectMany(x => x.anims).ToHashSet(StringComparer.OrdinalIgnoreCase);
        foreach (var (id, root) in new[] { ("NPC_Extra", "Pal/Content/Pal/Animation/Character/NPC/"), ("Player_Female_Extra", "Pal/Content/Pal/Animation/Character/Player/Female/") })
        {
            var seqs = new SortedSet<string>();
            foreach (var k in provider.Files.Keys.Where(k => k.StartsWith(root, StringComparison.OrdinalIgnoreCase) && k.EndsWith(".uasset")
                         && !k.Contains("/Boss/", StringComparison.OrdinalIgnoreCase) && Path.GetFileName(k).StartsWith("AS_", StringComparison.OrdinalIgnoreCase)))
            {
                var pkg = k[..^".uasset".Length];
                if (!inSets.Contains(pkg) && OnHumanSkeleton(provider, pkg)) seqs.Add(pkg);
            }
            sets[id] = new SetInfo(id, root, "extra", null, seqs);
        }
        var outSets = sets.Values.OrderBy(s => s.id, StringComparer.Ordinal).ToDictionary(s => s.id, s => (object)new { s.source, s.kind, s.weaponType, idle = s.idle?.Split('/').Last(), anims = s.anims.ToList() });
        // skeletons whose meshes may play these sets: the set skeleton and its CompatibleSkeletons (the engine plays an animation only on
        // a mesh of the same or a declared-compatible skeleton; the bosses' cutscene skeletons declare none)
        var compatible = new List<string> { HumanSkeleton };
        try
        {
            var sj = JObject.Parse(JsonConvert.SerializeObject(provider.LoadPackageObject<USkeleton>(HumanSkeleton), Program.JsonSettings));
            foreach (var c in (sj["Properties"]?["CompatibleSkeletons"] as JArray) ?? new JArray())
                if ((string?)c["AssetPathName"] is string ap && ap != "None") compatible.Add(Pkg(ap));
        }
        catch (Exception e) { Console.WriteLine($"CompatibleSkeletons: {e.Message}"); }
        var output = new
        {
            skeleton = HumanSkeleton,
            compatible = compatible.Distinct().ToList(),
            sets = outSets,
            // every weapon type over all data sets -> its set chain (base first), for "other weapons"
            shooters = allShooters.ToDictionary(k => k.Key, k => k.Value.AsEnumerable().Reverse().ToList()),
            meshes,
            weapons,
        };
        File.WriteAllText(SetsPath, JsonConvert.SerializeObject(output, Program.JsonSettings));
        Console.WriteLine($"{sets.Count} sets ({sets.Values.Sum(s => s.anims.Count)} sequences, {sets.Values.SelectMany(s => s.anims).Distinct().Count()} distinct), {meshes.Count} meshes, {weapons.Count} weapons -> {SetsPath}");
        foreach (var s in sets.Values.OrderBy(s => s.id)) Console.WriteLine($"  {s.id,-50} {s.kind,-8} {s.weaponType,-16} {s.anims.Count,4}");
        // NPC meshes no blueprint names: they still get the standard data set's sets in the catalog
        var all = Directory.Exists(Path.Combine(Program.RawDir, "palworld_humans"))
            ? Directory.GetDirectories(Path.Combine(Program.RawDir, "palworld_humans")).Select(Path.GetFileName).ToList() : new();
        foreach (var id in all)
        {
            var pj = Path.Combine(Program.RawDir, "palworld_humans", id!, "pal.json");
            if (!File.Exists(pj)) continue;
            var mesh = (string?)JObject.Parse(File.ReadAllText(pj))["mesh"];
            if (mesh != null && !meshes.ContainsKey(mesh)) Console.WriteLine($"  unmatched: {id} ({mesh})");
        }
    }

    // blueprints whose CharacterMesh0 is this mesh (from animsets.json)
    public static List<string> BlueprintsFor(string? mesh)
    {
        if (mesh == null || !File.Exists(SetsPath)) return new();
        var m = JObject.Parse(File.ReadAllText(SetsPath))["meshes"]?[mesh];
        return (m?["blueprints"] as JArray)?.Select(x => (string)x!).ToList() ?? new();
    }

    // every sequence that belongs to a set (humans' own folders skip these)
    public static HashSet<string> PackedSequences()
    {
        var res = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        if (!File.Exists(SetsPath)) return res;
        foreach (var s in ((JObject)JObject.Parse(File.ReadAllText(SetsPath))["sets"]!).Properties())
            foreach (var a in (JArray)s.Value["anims"]!) res.Add((string)a!);
        return res;
    }

    // `animpack [<SetId>...]`: one skeleton-only "model" per set
    public static void ExportPacks(DefaultFileProvider provider, List<string> args)
    {
        var doc = JObject.Parse(File.ReadAllText(SetsPath));
        var only = args.Where(a => !a.StartsWith("--")).ToHashSet();
        var skeleton = provider.LoadPackageObject<USkeleton>(HumanSkeleton);
        var rs = skeleton.ReferenceSkeleton;
        var bones = rs.FinalRefBoneInfo.Select((bi, i) => new
        {
            name = bi.Name.Text, parent = bi.ParentIndex,
            t = new[] { rs.FinalRefBonePose[i].Translation.X, rs.FinalRefBonePose[i].Translation.Y, rs.FinalRefBonePose[i].Translation.Z },
            r = new[] { rs.FinalRefBonePose[i].Rotation.X, rs.FinalRefBonePose[i].Rotation.Y, rs.FinalRefBonePose[i].Rotation.Z, rs.FinalRefBonePose[i].Rotation.W },
            s = new[] { rs.FinalRefBonePose[i].Scale3D.X, rs.FinalRefBonePose[i].Scale3D.Y, rs.FinalRefBonePose[i].Scale3D.Z },
            retarget = Retarget.For(skeleton).modes.GetValueOrDefault(bi.Name.Text, ""),
        }).ToList();
        int ok = 0, skip = 0;
        foreach (var s in ((JObject)doc["sets"]!).Properties())
        {
            if (only.Count > 0 && !only.Contains(s.Name)) continue;
            var outDir = Path.Combine(Program.RawDir, "palworld_animpacks", s.Name);
            if (!Program.Force && File.Exists(Path.Combine(outDir, "pal.json"))) { skip++; continue; }
            var animDir = Path.Combine(outDir, "anims");
            Directory.CreateDirectory(animDir);
            var anims = new List<object>();
            foreach (var a in (JArray)s.Value["anims"]!)
            {
                try { anims.Add(Pals.WriteAnim(provider.LoadPackageObject<UAnimSequence>((string)a!), animDir)); }
                catch (Exception e) { anims.Add(new { name = ((string)a!).Split('/').Last(), error = e.Message }); }
            }
            var info = new
            {
                code = s.Name, mesh = (string?)null, skeleton = HumanSkeleton, bones, skeletonBones = bones,
                materials = new List<object>(), lods = new List<object>(), anims, textures = new List<string>(), sockets = new List<object>(),
                set = new { source = (string?)s.Value["source"], kind = (string?)s.Value["kind"], weaponType = (string?)s.Value["weaponType"], idle = (string?)s.Value["idle"] },
            };
            File.WriteAllText(Path.Combine(outDir, "pal.json"), JsonConvert.SerializeObject(info, Program.JsonSettings));
            Console.WriteLine($"{s.Name,-50} {anims.Count,4} anims");
            ok++;
        }
        Console.WriteLine($"done: {ok} packs, {skip} skipped");
    }
}
