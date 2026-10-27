// Humans (NPCs, tower/field bosses, the customisable player) and every Model/Weapon mesh. Same output layout as a Pal
// (see Pals.cs); only the mesh enumeration and the animation lookup differ.
//
// raw/palworld_humans/<Id>/{pal.json, lod0.bin, anims/}         NPC_<Gender>_<Type>NN[_vNN], Boss_<X>
// raw/palworld_humans/Player_Female/                            default body + anims for the player skeleton
// raw/palworld_humans/Player_Female/parts/<Part>_<Name>/        every Head/Hair/Eye/HeadEquip/Outfit (no anims) + parts.json
// raw/palworld_weapons/<Class>_<NNN>/                           Model/Weapon/<Class>/S[KM]_Weapon_<Class>_<NNN>

using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Objects.UObject;
using Newtonsoft.Json;

namespace PalExtract;

public static class Humans
{
    const string Root = "Pal/Content/Pal/";
    const string NpcRoot = Root + "Model/Character/NPC/";
    const string PlayerRoot = Root + "Model/Character/Player/";
    const string WeaponRoot = Root + "Model/Weapon/";
    // NPCs/bosses get only the NPC folders (the player set is ~700 clips, ~340 MB per character); the player gets its own Female set.
    static readonly string NpcAnim = Root + "Animation/Character/NPC/";
    // boss intro cutscenes (Demo/BossBattle<X>Start/Animation) animate the boss on its own skeleton, cloth bones included
    static readonly string DemoAnim = Root + "Demo/BossBattle";
    static readonly string PlayerAnim = Root + "Animation/Character/Player/Female/";

    static bool IsSkip(string name) => name.Contains("Skeleton", StringComparison.OrdinalIgnoreCase)
        || name.Contains("Physics", StringComparison.OrdinalIgnoreCase);

    // id -> mesh package path, for NPCs and bosses. Hair/Head/Outfit sub-meshes of a boss (inside SK_ folders) are not characters.
    static SortedDictionary<string, string> NpcMeshes(DefaultFileProvider provider)
    {
        var map = new SortedDictionary<string, string>(StringComparer.Ordinal);
        foreach (var k in provider.Files.Keys.OrderBy(k => k, StringComparer.Ordinal))
        {
            if (!k.StartsWith(NpcRoot, StringComparison.OrdinalIgnoreCase) || !k.EndsWith(".uasset")) continue;
            var p = k[NpcRoot.Length..].Split('/');
            var file = p[^1][..^".uasset".Length];
            if (!file.StartsWith("SK_NPC_", StringComparison.OrdinalIgnoreCase) || IsSkip(file)) continue;
            // NPC/SK_NPC_X/SK_NPC_X_v02 (2 deep) or NPC/Boss/<X>Boss/SK_NPC_<X>Boss001 (3 deep); anything deeper is a sub-part folder
            if (!(p.Length == 2 || (p.Length == 3 && p[0].Equals("Boss", StringComparison.OrdinalIgnoreCase)))) continue;
            if (file.Contains("Hair0") || file.Contains("Head0") || file.Contains("_Outfit_")) continue;
            var id = p.Length == 3 ? "Boss_" + file["SK_NPC_".Length..] : file["SK_".Length..];
            map[id] = k[..^".uasset".Length];
        }
        // Zoe (GrassBoss) has no SK_NPC_GrassBoss001: her whole body is the "_ALL" outfit mesh (what Grizzbolt's tower blueprint carries)
        const string grass = NpcRoot + "Boss/GrassBoss/SK_NPC_Outfit_GrassBoss001/SK_NPC_Outfit_GrassBoss001_ALL";
        if (provider.Files.ContainsKey(grass + ".uasset")) map["Boss_GrassBoss001"] = grass;
        return map;
    }

    // AS_ package -> skeleton package path (loaded once; each human then filters by its own skeleton)
    static Dictionary<string, string?>? animSkeletons;
    static IEnumerable<string> AnimsFor(DefaultFileProvider provider, string? skelPath, bool player, string? key = null)
    {
        if (animSkeletons == null)
        {
            animSkeletons = new Dictionary<string, string?>();
            foreach (var k in provider.Files.Keys.OrderBy(k => k, StringComparer.Ordinal))
            {
                if (!k.EndsWith(".uasset") || !(k.StartsWith(NpcAnim, StringComparison.OrdinalIgnoreCase) || k.StartsWith(DemoAnim, StringComparison.OrdinalIgnoreCase) || k.StartsWith(PlayerAnim, StringComparison.OrdinalIgnoreCase))) continue;
                if (!Path.GetFileName(k).StartsWith("AS_", StringComparison.OrdinalIgnoreCase)) continue;
                var pkg = k[..^".uasset".Length];
                try
                {
                    var seq = provider.LoadPackageObject<UAnimSequence>(pkg);
                    var s = seq.Skeleton.ResolvedObject?.Load<USkeleton>();
                    animSkeletons[pkg] = s == null ? null : Pals.PkgPath(s);
                }
                catch { animSkeletons[pkg] = null; }
            }
            Console.WriteLine($"indexed {animSkeletons.Count} human animation sequences");
        }
        var root = player ? PlayerAnim : NpcAnim;
        return animSkeletons.Where(kv => kv.Value != null && kv.Value == skelPath && kv.Key.StartsWith(root, StringComparison.OrdinalIgnoreCase))
            // a boss's intro cutscene is authored on its own skeleton (SK_NPC_<X>Boss001_Skeleton, same bone names as its mesh): take those too
            .Concat(key == null ? Enumerable.Empty<KeyValuePair<string, string?>>() : animSkeletons.Where(kv => kv.Key.StartsWith(DemoAnim, StringComparison.OrdinalIgnoreCase) && kv.Value != null && kv.Value.Contains($"/SK_NPC_{key}", StringComparison.OrdinalIgnoreCase)))
            .Select(kv => kv.Key).ToList();
    }

    // `bossride`: tower bosses fight riding a Pal. The tower Pal's blueprint (BP_<Pal>_GYM) holds a BP_PalBossNPCMarker component
    // attached to the skeleton socket BossRideMarker; the component's SkeletalMesh is the boss, its relative transform is the seat.
    // The Pal's own CharacterMesh0 scale/offset (taken from the GYM, else the _BOSS, else the base blueprint) scales the whole rig.
    // -> raw/palworld_humans/rides.json
    public static void ExportBossRides(DefaultFileProvider provider)
    {
        const string bpRoot = Root + "Blueprint/Character/Monster/PalActorBP/";
        var rides = new List<object>();
        foreach (var f in provider.Files.Keys.Where(k => k.StartsWith(bpRoot) && k.EndsWith(".uasset") && Path.GetFileName(k).StartsWith("BP_") && k.Contains("_gym", StringComparison.OrdinalIgnoreCase)).OrderBy(k => k))
        {
            var path = f[..^".uasset".Length];
            var code = f[bpRoot.Length..].Split('/')[0];
            try
            {
                var exports = Newtonsoft.Json.Linq.JArray.Parse(JsonConvert.SerializeObject(provider.LoadPackage(path).GetExports(), Program.JsonSettings));
                var marker = exports.FirstOrDefault(e => (string?)e["Type"] == "BP_PalBossNPCMarker_C" && e["Properties"]?["SkeletalMesh"] != null);
                var meshPath = (string?)marker?["Properties"]?["SkeletalMesh"]?["ObjectPath"];
                if (marker == null || meshPath == null || !meshPath.Contains("/Model/Character/NPC/Boss/")) continue;
                meshPath = meshPath[..meshPath.LastIndexOf('.')];
                // the SCS node that instances the marker names the socket (or bone) it hangs from and the component it is a child of
                var node = exports.FirstOrDefault(e => (string?)e["Type"] == "SCS_Node" && ((string?)e["Properties"]?["ComponentClass"]?["ObjectName"] ?? "").Contains("BP_PalBossNPCMarker"));
                var socket = (string?)node?["Properties"]?["AttachToName"] ?? "BossRideMarker";
                var (bone, sl, sr) = Pals.FindSocket(provider, code, socket);
                if (node != null) Console.WriteLine($"    attach {socket} (parent {(string?)node["Properties"]?["ParentComponentOrVariableName"]})");
                double[]? scale = null, loc = null;
                foreach (var b in new[] { path, path.Replace("_GYM", "_BOSS").Replace("_Gym", "_BOSS"), $"{bpRoot}{code}/BP_{code}" })
                {
                    if (!provider.Files.ContainsKey(b + ".uasset")) continue;
                    var body = Newtonsoft.Json.Linq.JArray.Parse(JsonConvert.SerializeObject(provider.LoadPackage(b).GetExports(), Program.JsonSettings))
                        .FirstOrDefault(e => ((string?)e["Name"])?.StartsWith("CharacterMesh0") == true && e["Properties"]?["RelativeScale3D"] != null);
                    if (body == null) continue;
                    scale = Pals.V3(body["Properties"]!["RelativeScale3D"], 1);
                    loc = Pals.V3(body["Properties"]!["RelativeLocation"], 0);
                    break;
                }
                var p = marker["Properties"]!;
                var bossId = "Boss_" + meshPath.Split('/').Last().Replace("SK_NPC_", "");
                if (meshPath.EndsWith("Outfit_GrassBoss001_ALL")) bossId = "Boss_GrassBoss001";
                var held = BossHeldItems(provider, exports, node, bossId);
                var pairs = BossClipPairs(provider, marker["Properties"]!, path, code, bossId);
                // bAbsoluteRotation / bAbsoluteScale: the seat's rotation and scale are world values (the actor's frame), only its location follows the bone
                rides.Add(new { pal = code, blueprint = path.Split('/').Last(), boss = bossId, bossMesh = meshPath, socket, bone, socketLoc = sl, socketRot = sr,
                    absRot = (string?)p["bAbsoluteRotation"] == "True", absScale = (string?)p["bAbsoluteScale"] == "True",
                    loc = Pals.V3(p["RelativeLocation"], 0), rot = Pals.Rot(p["RelativeRotation"]), scale = Pals.V3(p["RelativeScale3D"], 1), palScale = scale, palLoc = loc, held, pairs });
                Console.WriteLine($"{code,-22} {Path.GetFileName(path),-34} -> {bossId} on {bone}{(held.Count > 0 ? " holding " + string.Join(", ", held.Select(h => ((dynamic)h).item)) : "")}");
            }
            catch (Exception e) { Console.WriteLine($"{path}: FAILED {e.Message}"); }
        }
        Directory.CreateDirectory(Path.Combine(Program.RawDir, "palworld_humans"));
        File.WriteAllText(Path.Combine(Program.RawDir, "palworld_humans", "rides.json"), JsonConvert.SerializeObject(rides, Program.JsonSettings));
    }

    // Which Pal clips play while the boss plays each of its clips (the boss's ride clips are authored against them; Sorajima's VortexRage
    // spins with Shaolong's). All from the game's tables, by the waza / state they serve:
    //   waza:  marker WazaMontage[W] (boss montage)  <->  Pal StaticCharacterParameterComponent.WazaActionDeclarationMap[W] (action BP):
    //          its "Montage List" (montage + PlayRate, e.g. VortexRage Start / Loop / End x1.45); an action without one is driven by the
    //          Pal's anim BP through its FlagName (<Code>_<Stem>) -> the Pal's AS_<Code>_<Stem>[_Start|_Loop|_End].
    //   state: marker Anim List (Idle / Encount / Commonwaza): Commonwaza <-> GeneralMontageMap GeneralAttackReady/Process/After_Far
    //          (+ their Loop montages); UniqueWazaIn / UniqueWaza / UniqueWazaOut <-> Start / Loop / End of the GYM learnset's one unique
    //          waza not in WazaMontage (Snow TackleLaser, Desert Flarestorm); the others by the boss clip's stem (Encount, Idle), as do
    //          wazas missing from the declaration map.
    // A montage expands to its AnimSequence segments; montage names ending in Loop (or with a looping section) are loop fillers.
    // -> [{boss: AS name, source, pal: [{anim: AS name, rate, loop}]}]
    static List<object> BossClipPairs(DefaultFileProvider provider, Newtonsoft.Json.Linq.JToken markerProps, string bpPath, string code, string bossId)
    {
        Newtonsoft.Json.Linq.JArray Exports(string pkg) => Newtonsoft.Json.Linq.JArray.Parse(JsonConvert.SerializeObject(provider.LoadPackage(pkg).GetExports(), Program.JsonSettings));
        static string Pkg(string objectPath) // "Pal/Content/.../AM_X.0" or "/Game/Pal/.../BP_X.BP_X_C" -> package path
        {
            var p = objectPath.StartsWith("/Game/") ? "Pal/Content/" + objectPath["/Game/".Length..] : objectPath;
            return p[..p.LastIndexOf('.')];
        }
        static string AsName(string? objectName) => (objectName ?? "").Split('\'').ElementAtOrDefault(1) ?? "";
        List<string> MontageAnims(string montagePkg) => MontageSegments(montagePkg).Select(s => s.anim).ToList();
        // a montage's AnimSequence segments with the time range each plays (SnowImpact_01_DownLoop holds frame 0 of SnowImpact_01: 0..0.005 s)
        List<(string anim, double from, double to)> MontageSegments(string montagePkg)
        {
            var m = Exports(montagePkg).FirstOrDefault(e => (string?)e["Type"] == "AnimMontage");
            var segs = m?["Properties"]?["SlotAnimTracks"]?.FirstOrDefault()?["AnimTrack"]?["AnimSegments"] as Newtonsoft.Json.Linq.JArray;
            return segs?.Select(s => (AsName((string?)s["AnimReference"]?["ObjectName"]), (double?)s["AnimStartTime"] ?? 0, (double?)s["AnimEndTime"] ?? -1))
                .Where(s => s.Item1 != "").ToList() ?? new List<(string, double, double)>();
        }
        // the Pal's tables: the GYM blueprint's component overrides the base Pal's, map by map
        var tables = new Dictionary<string, Newtonsoft.Json.Linq.JArray>();
        foreach (var bp in new[] { bpPath, $"Pal/Content/Pal/Blueprint/Character/Monster/PalActorBP/{code}/BP_{code}" })
        {
            if (!provider.Files.ContainsKey(bp + ".uasset")) continue;
            var comp = Exports(bp).FirstOrDefault(e => ((string?)e["Name"] ?? "").StartsWith("StaticCharacterParameterComponent"));
            foreach (var k in new[] { "WazaActionDeclarationMap", "GeneralMontageMap", "ActionMontageMap" })
                if (!tables.ContainsKey(k) && comp?["Properties"]?[k] is Newtonsoft.Json.Linq.JArray a) tables[k] = a;
        }
        string? MapValue(string map, string key) => tables.GetValueOrDefault(map)?.FirstOrDefault(kv => (string?)kv["Key"] == key)?["Value"] is Newtonsoft.Json.Linq.JObject v
            ? (string?)v["ObjectPath"] ?? (string?)v["AssetPathName"] : null;
        var animRoot = $"Pal/Content/Pal/Animation/Character/Monster/{code}/";
        // the Pal's sequences by lower-cased name (an action named Flarestorm plays AS_Horus_FlareStorm_*)
        var palAnims = provider.Files.Keys.Where(k => k.StartsWith(animRoot, StringComparison.OrdinalIgnoreCase) && k.EndsWith(".uasset"))
            .Select(k => Path.GetFileNameWithoutExtension(k)).GroupBy(n => n.ToLowerInvariant()).ToDictionary(g => g.Key, g => g.First());
        string? PalAnim(string asName) => palAnims.GetValueOrDefault(asName.ToLowerInvariant());
        // the Pal's clip(s) for a stem; boss clip names carry suffixes (Idle_SnowBoss, Idle_Boss), so shorter stems are tried in turn
        List<object> ByStem(string stem)
        {
            var tokens = stem.Split('_');
            for (int n = tokens.Length; n >= 1; n--)
            {
                var whole = $"AS_{code}_{string.Join('_', tokens.Take(n))}";
                if (PalAnim(whole) is string one) return new List<object> { new { anim = one, rate = 1.0, loop = false } };
                var parts = new[] { "Start", "Loop", "End" }.Select(p => PalAnim($"{whole}_{p}")).OfType<string>()
                    .Select(x => (object)new { anim = x, rate = 1.0, loop = x.EndsWith("_Loop", StringComparison.OrdinalIgnoreCase) }).ToList();
                if (parts.Count > 0) return parts;
            }
            return new List<object>();
        }
        List<object> Montages(IEnumerable<(string path, double rate)> list) => list.SelectMany(m =>
        {
            var name = m.path.Split('/').Last();
            var loop = name.EndsWith("Loop", StringComparison.OrdinalIgnoreCase);
            return MontageSegments(m.path).Select(a => (object)new { anim = a.anim, rate = m.rate, loop, from = Math.Round(a.from, 4), to = Math.Round(a.to, 4) });
        }).ToList();
        string Stem(string bossAs) => System.Text.RegularExpressions.Regex.Replace(bossAs, @"^AS_[A-Za-z]+Boss\d*_(Ride_)?", "");
        // an action blueprint's Pal clips: its Montage List; else its FlagName (BlueSkyDragon_Tossin, TackleLaser); else its class name
        // (BP_Action_Unique_Horus_Flarestorm -> Flarestorm)
        (List<object> anims, string? wazaId) ActionAnims(string actionPath)
        {
            var props = Exports(Pkg(actionPath)).FirstOrDefault(e => ((string?)e["Name"] ?? "").StartsWith("Default__"))?["Properties"];
            var wid = ((string?)props?["WazaID"])?.Split("::").Last();
            // "Montage List" (most), or several phase lists in declaration order (SnowImpact: UpMontage, DownMontage, LandingMontage,
            // ChargeMontage; TrampleSlash: Montages); entries are {Montage, PlayRate} or bare montage refs; a montage listed twice plays once
            var list = new List<(string path, double rate)>();
            foreach (var prop in (props as Newtonsoft.Json.Linq.JObject)?.Properties() ?? Enumerable.Empty<Newtonsoft.Json.Linq.JProperty>())
            {
                if (!prop.Name.Contains("Montage", StringComparison.OrdinalIgnoreCase) || prop.Value is not Newtonsoft.Json.Linq.JArray arr) continue;
                foreach (var x in arr.OfType<Newtonsoft.Json.Linq.JObject>())
                {
                    var mref = x["Montage"] as Newtonsoft.Json.Linq.JObject ?? x;
                    if ((string?)mref["ObjectPath"] is not string op || !((string?)mref["ObjectName"] ?? "").StartsWith("AnimMontage")) continue;
                    var path = Pkg(op);
                    if (list.All(l => l.path != path)) list.Add((path, (double?)x["PlayRate"] ?? 1.0));
                }
            }
            if (list.Count > 0) return (Montages(list), wid);
            if ((string?)props?["FlagName"] is string flag && ByStem(flag.StartsWith(code + "_") ? flag[(code.Length + 1)..] : flag) is { Count: > 0 } byFlag)
                return (byFlag, wid);
            var cls = Pkg(actionPath).Split('/').Last();
            var stem = System.Text.RegularExpressions.Regex.Replace(cls, $@"^BP_(Unique)?Action_?(Unique_?)?({code}_)?", "");
            return (ByStem(stem), wid);
        }
        // the GYM Pal's learnset (DT_WazaMasterLevel rows GYM_<Code>NNN): the wazas it can use in the tower fight
        var learnset = new HashSet<string>();
        try
        {
            var dt = Exports("Pal/Content/Pal/DataTable/Waza/DT_WazaMasterLevel").FirstOrDefault(e => e["Rows"] != null)?["Rows"] as Newtonsoft.Json.Linq.JObject;
            foreach (var row in dt?.Properties() ?? Enumerable.Empty<Newtonsoft.Json.Linq.JProperty>())
                if (System.Text.RegularExpressions.Regex.IsMatch(row.Name, $@"^GYM_{code}\d+$")) learnset.Add(((string?)row.Value["WazaID"] ?? "").Split("::").Last());
        }
        catch (Exception e) { Console.WriteLine($"    learnset: {e.Message}"); }
        // the marker's generic unique-skill states (UniqueWazaIn / UniqueWaza / UniqueWazaOut) serve the one unique waza of the GYM learnset
        // that has no montage of its own in WazaMontage (Snow: TackleLaser; Desert: Flarestorm). Phase by phase: In <-> Start, UniqueWaza <-> Loop,
        // Out <-> End. Several candidates (or none) -> not paired.
        var wazaMontageKeys = ((markerProps["WazaMontage"] as Newtonsoft.Json.Linq.JArray) ?? new Newtonsoft.Json.Linq.JArray()).Select(kv => (string?)kv["Key"]).ToHashSet();
        var uniqueCandidates = (tables.GetValueOrDefault("WazaActionDeclarationMap") ?? new Newtonsoft.Json.Linq.JArray())
            .Where(kv => ((string?)kv["Key"] ?? "").StartsWith("EPalWazaID::Unique_") && !wazaMontageKeys.Contains((string?)kv["Key"]))
            .Select(kv => (string?)(kv["Value"] as Newtonsoft.Json.Linq.JObject)?["AssetPathName"]).OfType<string>().Distinct()
            .Select(ActionAnims).Where(a => a.wazaId != null && learnset.Contains(a.wazaId) && a.anims.Count > 0).ToList();
        List<object>? UniquePhase(string state)
        {
            if (uniqueCandidates.Count != 1) return null;
            var segs = uniqueCandidates[0].anims;
            bool IsLoop(object o) => (bool)o.GetType().GetProperty("loop")!.GetValue(o)!;
            var fixedSegs = segs.Where(s => !IsLoop(s)).ToList();
            var loops = segs.Where(IsLoop).ToList();
            return state switch
            {
                "UniqueWazaIn" when fixedSegs.Count > 0 => new List<object> { fixedSegs[0] },
                "UniqueWazaOut" when fixedSegs.Count > 1 => new List<object> { fixedSegs[^1] },
                "UniqueWaza" when loops.Count > 0 => new List<object> { loops[0] },
                _ => null,
            };
        }

        var pairs = new List<object>();
        var seen = new HashSet<string>();
        void Add(string bossAs, string source, List<object> pal)
        {
            if (bossAs == "" || pal.Count == 0 || !seen.Add(bossAs)) return;
            pairs.Add(new { boss = bossAs, source, pal });
        }
        // wazas
        // states first, Idle leading: a marker that reuses the ride idle for another state (Sakurajima's Commonwaza) keeps it an idle
        var states = ((markerProps["Anim List"] as Newtonsoft.Json.Linq.JObject)?.Properties() ?? Enumerable.Empty<Newtonsoft.Json.Linq.JProperty>())
            .OrderBy(p => p.Name.StartsWith("Idle_") ? 0 : 1).ToList();
        foreach (var p in states)
        {
            var state = p.Name.Split('_')[0];
            var bossAs = AsName((string?)(p.Value as Newtonsoft.Json.Linq.JObject)?["ObjectName"]);
            if (bossAs == "") continue;
            var stem = Stem(bossAs);
            List<object> pal;
            if (state.Equals("Commonwaza", StringComparison.OrdinalIgnoreCase) && !stem.Contains("Idle"))
                pal = Montages(new[] { "GeneralAttackReady_Far", "GeneralAttackReadyLoop_Far", "GeneralAttackProcess_Far", "GeneralAttackProcessLoop_Far", "GeneralAttackAfter_Far" }
                    .Select(k => MapValue("GeneralMontageMap", "EPalGeneralMontageType::" + k)).Where(v => v != null).Select(v => (Pkg(v!), 1.0)));
            else if (state.StartsWith("UniqueWaza") && !stem.Contains("Idle") && UniquePhase(state) is { } phase) pal = phase;
            else pal = ByStem(stem);
            Add(bossAs, "state:" + state, pal);
        }
        foreach (var kv in (markerProps["WazaMontage"] as Newtonsoft.Json.Linq.JArray) ?? new Newtonsoft.Json.Linq.JArray())
        {
            var waza = ((string?)kv["Key"] ?? "").Split("::").Last();
            var bm = (string?)(kv["Value"] as Newtonsoft.Json.Linq.JObject)?["ObjectPath"];
            if (bm == null) continue;
            foreach (var bossAs in MontageAnims(Pkg(bm)))
            {
                var action = MapValue("WazaActionDeclarationMap", "EPalWazaID::" + waza);
                var pal = action != null ? ActionAnims(action).anims : new List<object>();
                if (pal.Count == 0) pal = ByStem(Stem(bossAs)); // e.g. Unique_BlueSkyDragon_GYM_Act: not declared on the Pal; same stem (RisingWaterLevel)
                Add(bossAs, "waza:" + waza, pal);
            }
        }
        // the boss's own ride clips the marker does not list (Grass: AS_GrassBoss_Ride_Spa / _SleepStart / _Waza...) pair by stem
        var info = Path.Combine(Program.RawDir, "palworld_humans", bossId, "pal.json");
        if (File.Exists(info))
        {
            var owner = System.Text.RegularExpressions.Regex.Replace(bossId.Replace("Boss_", ""), @"\d+$", "");
            foreach (var a in (Newtonsoft.Json.Linq.JObject.Parse(File.ReadAllText(info))["anims"] as Newtonsoft.Json.Linq.JArray) ?? new Newtonsoft.Json.Linq.JArray())
            {
                var name = (string?)a["name"] ?? "";
                if (name.StartsWith($"AS_{owner}_Ride_", StringComparison.Ordinal) && !seen.Contains(name)) Add(name, "ride clip", ByStem(Stem(name)));
            }
        }
        return pairs;
    }

    // Items the boss holds while riding: mesh components whose SCS node is a child of the marker's (Lily's spear: PalSkeletalMesh
    // SK_NPC_ForestBoss001_weapon on Socket_Weapon_R). The socket is on the boss's skeleton. Each mesh is exported to
    // raw/palworld_weapons/<item>/ like the Pals' held weapons; the parts use the same fields as held.json.
    static List<object> BossHeldItems(DefaultFileProvider provider, Newtonsoft.Json.Linq.JArray exports, Newtonsoft.Json.Linq.JToken? markerNode, string bossId)
    {
        var res = new List<object>();
        var children = (markerNode?["Properties"]?["ChildNodes"] as Newtonsoft.Json.Linq.JArray)?
            .Select(c => ((string?)c["ObjectName"] ?? "").Split(':').Last().TrimEnd('\'').Split('.').Last()).ToHashSet() ?? new HashSet<string>();
        foreach (var n in exports.Where(e => (string?)e["Type"] == "SCS_Node" && children.Contains((string?)e["Name"] ?? "")))
        {
            var np = n["Properties"]!;
            var tmpl = ((string?)np["ComponentTemplate"]?["ObjectName"] ?? "").Split(':').Last().TrimEnd('\'');
            var comp = exports.FirstOrDefault(e => (string?)e["Name"] == tmpl);
            if (comp == null || (string?)comp["Type"] is not ("SkeletalMeshComponent" or "StaticMeshComponent" or "PalSkeletalMeshComponent")) continue;
            var props = comp["Properties"]!;
            var meshPath = (string?)props["SkeletalMesh"]?["ObjectPath"] ?? (string?)props["StaticMesh"]?["ObjectPath"];
            if (meshPath == null) continue;
            meshPath = meshPath[..meshPath.LastIndexOf('.')];
            var item = System.Text.RegularExpressions.Regex.Replace(meshPath.Split('/').Last(), @"^(S[KM]_)(Weapon_)?", "");
            var socket = (string?)np["AttachToName"] ?? "";
            var (bone, sl, sr) = Pals.FindSocketIn(provider, Path.Combine(Program.RawDir, "palworld_humans", bossId, "pal.json"), socket);
            var outDir = Path.Combine(Program.RawDir, "palworld_weapons", item);
            if (Program.Force || !File.Exists(Path.Combine(outDir, "pal.json"))) Pals.ExportOne(provider, item, outDir, meshPath, false);
            res.Add(new { part = (string?)np["InternalVariableName"] ?? tmpl, item, mesh = meshPath, socket, bone, visible = (string?)props["bVisible"] != "False",
                socketLoc = sl, socketRot = sr, loc = Pals.V3(props["RelativeLocation"], 0), rot = Pals.Rot(props["RelativeRotation"]), scale = Pals.V3(props["RelativeScale3D"], 1) });
        }
        return res;
    }

    // `physics`: the cloth / hair / belt simulation of a human is the KawaiiPhysics plugin, configured by AnimNode_KawaiiPhysics nodes in
    // the character's anim layer (BP_NPC_<X>.AnimLayerClass -> Imp/ABP_NPC_<Outfit>_Implimentation). Each node roots a bone chain with
    // damping / stiffness / limit angle / gravity and its own capsule + sphere colliders (driving bone, offset, size).
    // -> raw/palworld_humans/<id>/physics.json {abp, nodes: [...]}; no file when the character's layer has no node.
    public static void ExportPhysics(DefaultFileProvider provider, List<string> args)
    {
        var ids = args.Contains("--all-humans")
            ? Directory.GetDirectories(Path.Combine(Program.RawDir, "palworld_humans")).Select(d => Path.GetFileName(d)!).Where(n => n.StartsWith("Boss_")).ToList()
            : args.Where(a => !a.StartsWith("--")).ToList();
        foreach (var id in ids)
        {
            try
            {
                var shortName = System.Text.RegularExpressions.Regex.Replace(id.Replace("Boss_", ""), @"\d+$", "");
                var bp = $"{Root}Blueprint/Character/NPC/Normal/BP_NPC_{shortName}";
                if (!provider.Files.ContainsKey(bp + ".uasset")) { Console.WriteLine($"{id,-26} no blueprint {bp}"); continue; }
                var json = JsonConvert.SerializeObject(provider.LoadPackage(bp).GetExports(), Program.JsonSettings);
                var m = System.Text.RegularExpressions.Regex.Match(json, @"""AnimLayerClass"":\s*\{[^}]*?""ObjectPath"":\s*""([^""]+?)\.\d+""");
                if (!m.Success) { Console.WriteLine($"{id,-26} no AnimLayerClass"); continue; }
                var abp = m.Groups[1].Value;
                var nodes = new List<object>();
                foreach (var e in Newtonsoft.Json.Linq.JArray.Parse(JsonConvert.SerializeObject(provider.LoadPackage(abp).GetExports(), Program.JsonSettings)))
                {
                    if (e["Properties"] is not Newtonsoft.Json.Linq.JObject props) continue;
                    foreach (var kv in props.Properties().Where(k => k.Name.StartsWith("AnimGraphNode_KawaiiPhysics") && k.Value is Newtonsoft.Json.Linq.JObject))
                    {
                        var n = (Newtonsoft.Json.Linq.JObject)kv.Value;
                        string? B(Newtonsoft.Json.Linq.JToken? t) => (string?)t?["BoneName"];
                        nodes.Add(new
                        {
                            node = kv.Name,
                            root = B(n["RootBone"]),
                            additionalRoots = (n["AdditionalRootBones"] as Newtonsoft.Json.Linq.JArray)?.Select(B).Where(x => x != null).ToList() ?? new List<string?>(),
                            exclude = (n["ExcludeBones"] as Newtonsoft.Json.Linq.JArray)?.Select(B).Where(x => x != null).ToList() ?? new List<string?>(),
                            settings = n["PhysicsSettings"],
                            gravity = n["Gravity"],
                            dummyBoneLength = (double?)n["DummyBoneLength"] ?? 0,
                            targetFrameRate = (int?)n["TargetFrameRate"] ?? 60,
                            capsules = (n["CapsuleLimits"] as Newtonsoft.Json.Linq.JArray)?.Where(c => (bool?)c["bEnable"] != false).Select(c => new
                                { bone = B(c["DrivingBone"]), radius = (double)c["Radius"]!, length = (double)c["Length"]!, loc = Pals.V3(c["OffsetLocation"], 0), rot = Pals.Rot(c["OffsetRotation"]) }).ToList(),
                            spheres = (n["SphericalLimits"] as Newtonsoft.Json.Linq.JArray)?.Where(c => (bool?)c["bEnable"] != false).Select(c => new
                                { bone = B(c["DrivingBone"]), radius = (double)c["Radius"]!, loc = Pals.V3(c["OffsetLocation"], 0), limitType = (string?)c["LimitType"] }).ToList(),
                        });
                    }
                }
                var path = Path.Combine(Program.RawDir, "palworld_humans", id, "physics.json");
                if (nodes.Count == 0) { if (File.Exists(path)) File.Delete(path); Console.WriteLine($"{id,-26} {abp.Split('/').Last()}: no KawaiiPhysics nodes"); continue; }
                File.WriteAllText(path, JsonConvert.SerializeObject(new { abp, nodes }, Program.JsonSettings));
                Console.WriteLine($"{id,-26} {abp.Split('/').Last()}: {nodes.Count} nodes");
            }
            catch (Exception e) { Console.WriteLine($"{id,-26} FAILED: {e.GetType().Name}: {e.Message}"); }
        }
    }

    public static void Export(DefaultFileProvider provider, List<string> args)
    {
        var npcs = NpcMeshes(provider);
        var ids = args.Contains("--all-humans")
            ? npcs.Keys.Concat(new[] { "Player_Female" }).ToList()
            : args.Where(a => !a.StartsWith("--")).ToList();
        int ok = 0, fail = 0, skip = 0;
        foreach (var id in ids)
        {
            var outDir = Path.Combine(Program.RawDir, "palworld_humans", id);
            if (!Program.Force && File.Exists(Path.Combine(outDir, "pal.json"))) { skip++; continue; }
            try
            {
                var t0 = DateTime.Now;
                (int anims, int textures) info;
                if (id == "Player_Female") info = ExportPlayer(provider, outDir);
                else
                {
                    if (!npcs.TryGetValue(id, out var mesh)) throw new Exception("no such NPC mesh");
                    info = Pals.ExportOne(provider, id, outDir, mesh, true, skel => AnimsFor(provider, skel, false, System.Text.RegularExpressions.Regex.Replace(id.Replace("Boss_", ""), @"\d+$", "")));
                }
                Console.WriteLine($"{id,-40} {info.anims,4} anims {info.textures,3} tex {(DateTime.Now - t0).TotalSeconds,6:F1}s");
                ok++;
            }
            catch (Exception e)
            {
                Console.WriteLine($"{id,-40} FAILED: {e.GetType().Name}: {e.Message}");
                Directory.CreateDirectory(outDir);
                File.WriteAllText(Path.Combine(outDir, "error.txt"), e.ToString());
                fail++;
            }
        }
        Console.WriteLine($"done: {ok} ok, {fail} failed, {skip} skipped");
    }

    // The player is modular. Body + anims go in the character folder; every other part is exported as its own
    // (animation-less) model in parts/ and listed in parts.json with its group, so the importer can build variants.
    // The male body has no SK_ in the pak (only MI_/T_), so only the female body is a default build.
    static (int anims, int textures) ExportPlayer(DefaultFileProvider provider, string outDir)
    {
        var body = PlayerRoot + "Body/Female/SK_Player_Female";
        var info = Pals.ExportOne(provider, "Player_Female", outDir, body, true, skel => AnimsFor(provider, skel, true));
        var parts = new List<object>();
        var partsDir = Path.Combine(outDir, "parts");
        foreach (var k in provider.Files.Keys.OrderBy(k => k, StringComparer.Ordinal))
        {
            if (!k.StartsWith(PlayerRoot, StringComparison.OrdinalIgnoreCase) || !k.EndsWith(".uasset")) continue;
            var p = k[PlayerRoot.Length..].Split('/');
            var file = p[^1][..^".uasset".Length];
            if (p.Length < 2 || p[0] == "Body" || !file.StartsWith("SK_", StringComparison.OrdinalIgnoreCase) || IsSkip(file)) continue;
            var name = file["SK_".Length..];
            var key = p[0] + "_" + name;
            try
            {
                var dir = Path.Combine(partsDir, key);
                if (Program.Force || !File.Exists(Path.Combine(dir, "pal.json")))
                    Pals.ExportOne(provider, key, dir, k[..^".uasset".Length], false);
                parts.Add(new { group = p[0], name, dir = key, mesh = k[..^".uasset".Length] });
            }
            catch (Exception e) { Console.WriteLine($"  part {key} FAILED: {e.GetType().Name}: {e.Message}"); }
        }
        File.WriteAllText(Path.Combine(outDir, "parts.json"), JsonConvert.SerializeObject(parts, Program.JsonSettings));
        return info;
    }

    // `weapons-all`: every Model/Weapon SK_/SM_ mesh, named <Class>_<NNN>. Existing folders (the Pal-held ones keep their own names) are not touched.
    public static void ExportAllWeapons(DefaultFileProvider provider, List<string> args)
    {
        var only = args.Where(a => !a.StartsWith("--")).ToHashSet();
        int ok = 0, fail = 0, skip = 0;
        foreach (var k in provider.Files.Keys.OrderBy(k => k, StringComparer.Ordinal))
        {
            if (!k.StartsWith(WeaponRoot, StringComparison.OrdinalIgnoreCase) || !k.EndsWith(".uasset")) continue;
            var file = Path.GetFileNameWithoutExtension(k);
            if ((!file.StartsWith("SK_Weapon_", StringComparison.OrdinalIgnoreCase) && !file.StartsWith("SM_Weapon_", StringComparison.OrdinalIgnoreCase)) || IsSkip(file)) continue;
            var id = file["SK_Weapon_".Length..]; // <Class>_<NNN>[_suffix]
            if (only.Count > 0 && !only.Contains(id)) continue;
            var outDir = Path.Combine(Program.RawDir, "palworld_weapons", id);
            if (!Program.Force && File.Exists(Path.Combine(outDir, "pal.json"))) { skip++; continue; }
            try
            {
                var r = Pals.ExportOne(provider, id, outDir, k[..^".uasset".Length], false);
                Console.WriteLine($"{id,-40} {r.textures,3} tex");
                ok++;
            }
            catch (Exception e)
            {
                Console.WriteLine($"{id,-40} FAILED: {e.GetType().Name}: {e.Message}");
                Directory.CreateDirectory(outDir);
                File.WriteAllText(Path.Combine(outDir, "error.txt"), e.ToString());
                fail++;
            }
        }
        Console.WriteLine($"done: {ok} ok, {fail} failed, {skip} skipped");
    }
}
