// Translation retargeting, read from the raw asset data.
//
// USkeleton.BoneTree[i].TranslationRetargetingMode: CUE4Parse's typed FBoneNode field stays at its default (Animation) for this build
// because the usmap has EBoneTranslationRetargetingMode without its values; the serialized property holds the number
// (EBoneTranslationRetargetingMode::1). SK_PalHuman_Skeleton: root/pelvis 2 (AnimationScaled), weapon_l/r 0 (Animation), every other
// bone 1 (Skeleton: the target mesh's own offset; the animation's translation is ignored).
// AnimationScaled scales by target length / source length, the source being the sequence's retarget source pose
// (UAnimSequence.RetargetSource -> USkeleton.AnimRetargetSources[name].ReferencePose, or RetargetSourceAsset -> that mesh's ref pose),
// else the skeleton's reference pose.
//
// `retarget` patches every raw/palworld*/<code>/pal.json in place (no re-export): skeletonBones[].retarget, anims[].retargetSource and
// retargetSources {source: {bone: t}} (only the bones whose mode reads a source).

using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace PalExtract;

public static class Retarget
{
    static readonly string[] ModeNames = { "Animation", "Skeleton", "AnimationScaled", "AnimationRelative", "OrientAndScale" };

    public record SkelInfo(Dictionary<string, string> modes, Dictionary<string, Dictionary<string, float[]>> sources);
    static readonly Dictionary<string, SkelInfo> cache = new();

    static string ModeName(string? raw)
    {
        var v = raw == null ? "0" : raw[(raw.LastIndexOf(':') + 1)..];
        return int.TryParse(v, out var i) && i >= 0 && i < ModeNames.Length ? ModeNames[i] : v;
    }

    static float[] T(JToken? t) => t == null ? new float[3] : new[] { (float)(t["X"] ?? 0), (float)(t["Y"] ?? 0), (float)(t["Z"] ?? 0) };

    /// bone name -> mode, and retarget source name -> (bone -> translation) for the bones whose mode reads a source
    public static SkelInfo For(USkeleton skel)
    {
        var key = Pals.PkgPath(skel);
        if (cache.TryGetValue(key, out var hit)) return hit;
        var j = JObject.Parse(JsonConvert.SerializeObject(skel, Program.JsonSettings));
        var names = skel.ReferenceSkeleton.FinalRefBoneInfo.Select(b => b.Name.Text).ToList();
        var tree = j["Properties"]?["BoneTree"] as JArray;
        var modes = new Dictionary<string, string>();
        for (int i = 0; i < names.Count; i++) modes[names[i]] = ModeName((string?)tree?.ElementAtOrDefault(i)?["TranslationRetargetingMode"]);
        var scaled = names.Select((n, i) => (n, i)).Where(x => modes[x.n] is "AnimationScaled" or "AnimationRelative").ToList();
        var sources = new Dictionary<string, Dictionary<string, float[]>>();
        if (scaled.Count > 0 && j["AnimRetargetSources"] is JObject srcs)
            foreach (var s in srcs.Properties())
                if (s.Value["ReferencePose"] is JArray pose)
                    sources[s.Name] = scaled.Where(x => x.i < pose.Count).ToDictionary(x => x.n, x => T(pose[x.i]?["Translation"]));
        return cache[key] = new SkelInfo(modes, sources);
    }

    /// the sequence's retarget source: an AnimRetargetSources name, or "asset:<mesh package>" (that mesh's ref pose); null = skeleton ref pose
    public static string? SourceOf(UAnimSequence seq)
    {
        string? Prop(string name) => seq.Properties.FirstOrDefault(p => p.Name.Text == name)?.Tag?.GenericValue?.ToString();
        var asset = Prop("RetargetSourceAsset");
        if (!string.IsNullOrEmpty(asset) && asset != "None" && !asset.EndsWith("None"))
        {
            var path = asset.Split('\'').FirstOrDefault(x => x.Contains('/')) ?? asset;
            path = path.Trim();
            if (path.StartsWith("/Game/")) path = "Pal/Content/" + path["/Game/".Length..];
            var dot = path.LastIndexOf('.');
            if (dot > path.LastIndexOf('/')) path = path[..dot];
            return "asset:" + path;
        }
        var name = Prop("RetargetSource");
        return string.IsNullOrEmpty(name) || name == "None" ? null : name;
    }

    static readonly Dictionary<string, Dictionary<string, float[]>> assetPoses = new();
    static Dictionary<string, float[]> AssetPose(DefaultFileProvider provider, string mesh, IEnumerable<string> bones)
    {
        if (!assetPoses.TryGetValue(mesh, out var pose))
        {
            pose = new Dictionary<string, float[]>();
            try
            {
                var m = provider.LoadPackage(mesh).GetExports().OfType<USkeletalMesh>().First();
                var rs = m.ReferenceSkeleton;
                for (int i = 0; i < rs.FinalRefBoneInfo.Length; i++)
                {
                    var t = rs.FinalRefBonePose[i].Translation;
                    pose[rs.FinalRefBoneInfo[i].Name.Text] = new[] { t.X, t.Y, t.Z };
                }
            }
            catch (Exception e) { Console.WriteLine($"  retarget source {mesh}: {e.Message}"); }
            assetPoses[mesh] = pose;
        }
        var want = bones.ToHashSet();
        return pose.Where(kv => want.Contains(kv.Key)).ToDictionary(kv => kv.Key, kv => kv.Value);
    }

    public static void Patch(DefaultFileProvider provider, List<string> args)
    {
        var subs = new[] { "palworld", "palworld_humans", "palworld_animpacks" };
        if (args.Any(a => subs.Contains(a))) subs = subs.Where(args.Contains).ToArray(); // `retarget palworld_animpacks`: only those folders
        var only = args.Where(a => !a.StartsWith("--") && !subs.Contains(a) && a is not ("palworld" or "palworld_humans" or "palworld_animpacks")).ToHashSet(); // and/or model ids
        var hist = new SortedDictionary<string, Dictionary<string, int>>();
        int files = 0, anims = 0;
        foreach (var sub in subs)
        {
            var root = Path.Combine(Program.RawDir, sub);
            if (!Directory.Exists(root)) continue;
            foreach (var dir in Directory.GetDirectories(root).OrderBy(d => d))
            {
                var pj = Path.Combine(dir, "pal.json");
                if (!File.Exists(pj) || (only.Count > 0 && !only.Contains(Path.GetFileName(dir)))) continue;
                try
                {
                    var info = JObject.Parse(File.ReadAllText(pj));
                    var skelPath = (string?)info["skeleton"];
                    if (skelPath == null) continue;
                    var skel = provider.LoadPackage(skelPath).GetExports().OfType<USkeleton>().First();
                    var si = For(skel);
                    if (!hist.ContainsKey(skelPath))
                        hist[skelPath] = si.modes.Values.GroupBy(m => m).ToDictionary(g => g.Key, g => g.Count());
                    if (info["skeletonBones"] is JArray sb)
                        foreach (var b in sb) if ((string?)b["name"] is string n && si.modes.TryGetValue(n, out var mode)) b["retarget"] = mode;
                    // retarget sources: only skeletons with bones that read one
                    var scaledBones = si.modes.Where(kv => kv.Value is "AnimationScaled" or "AnimationRelative").Select(kv => kv.Key).ToList();
                    var used = new Dictionary<string, Dictionary<string, float[]>>();
                    if (scaledBones.Count > 0 && info["anims"] is JArray an)
                        foreach (var a in an)
                        {
                            var name = (string?)a["name"];
                            if (name == null || a["error"] != null) continue;
                            var pkg = FindSequence(provider, name, skelPath);
                            if (pkg == null) continue;
                            string? src = null;
                            ((JObject)a).Remove("retargetSourcePose");
                            try
                            {
                                var seq = provider.LoadPackageObject<UAnimSequence>(pkg);
                                // UE 5.1 bakes the retarget source mesh's pose into the sequence (RetargetSourceAssetReferencePose, skeleton
                                // bone order as of saving; the skeleton has grown since, bones are only appended). It wins over the name.
                                var cached = JObject.Parse(JsonConvert.SerializeObject(seq, Program.JsonSettings))["Properties"]?["RetargetSourceAssetReferencePose"] as JArray;
                                if (cached != null && cached.Count > 0)
                                {
                                    var pose = new JObject();
                                    foreach (var bn in scaledBones)
                                        if (skel.ReferenceSkeleton.FinalNameToIndexMap.TryGetValue(bn, out var bi) && bi < cached.Count)
                                            pose[bn] = new JArray(T(cached[bi]?["Translation"]));
                                    a["retargetSourcePose"] = pose;
                                    anims++;
                                    ((JObject)a).Remove("retargetSource");
                                    continue;
                                }
                                src = SourceOf(seq);
                            }
                            catch { }
                            if (src == null) { ((JObject)a).Remove("retargetSource"); continue; }
                            a["retargetSource"] = src;
                            anims++;
                            if (!used.ContainsKey(src))
                                used[src] = src.StartsWith("asset:") ? AssetPose(provider, src["asset:".Length..], scaledBones)
                                    : si.sources.TryGetValue(src, out var p) ? p : new Dictionary<string, float[]>();
                        }
                    info["retargetSources"] = JObject.FromObject(used);
                    File.WriteAllText(pj, JsonConvert.SerializeObject(info, Program.JsonSettings));
                    files++;
                }
                catch (Exception e) { Console.WriteLine($"{sub}/{Path.GetFileName(dir)}: {e.GetType().Name}: {e.Message}"); }
            }
        }
        Console.WriteLine($"patched {files} pal.json, {anims} sequences with a retarget source");
        foreach (var (s, h) in hist) Console.WriteLine($"  {s.Split('/').Last(),-44} {string.Join(", ", h.OrderByDescending(k => k.Value).Select(k => $"{k.Key} {k.Value}"))}");
    }

    // sequence asset name -> package path (indexed once over the animation / demo folders)
    static Dictionary<string, List<string>>? seqIndex;
    static string? FindSequence(DefaultFileProvider provider, string name, string skelPath)
    {
        if (seqIndex == null)
        {
            seqIndex = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
            foreach (var k in provider.Files.Keys)
            {
                if (!k.EndsWith(".uasset") || !Path.GetFileName(k).StartsWith("AS_", StringComparison.OrdinalIgnoreCase)) continue;
                var n = Path.GetFileNameWithoutExtension(k);
                if (!seqIndex.TryGetValue(n, out var l)) seqIndex[n] = l = new List<string>();
                l.Add(k[..^".uasset".Length]);
            }
        }
        return seqIndex.TryGetValue(name, out var list) ? list[0] : null;
    }
}
