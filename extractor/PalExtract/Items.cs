// `items`: what every weapon blueprint looks like, for naming the weapon models after the game's items and finding NPC versions.
//
// raw/palworld_weapons/items.json: one entry per BP_* under Blueprint/Weapon (BackWeapon display copies and Bullet projectiles skipped):
//   {class, blueprint, parents: [class...], user: player|npc|otomo|ride, parts: [{component, mesh, item, parent, socket, visible, overrides}]}
// `class` is the blueprint name without BP_ (DT_ItemDataTable.ItemActorClass names it). A child blueprint inherits its parent's components
// and can replace a component's mesh or materials (BP_SkyBow: BP_CompoundBow's mesh with MI_PalProp_CompoundBow_Blue), so components are
// merged by SCS variable name from the root class down. `overrides` = OverrideMaterials per slot (null = the mesh's own material), each with
// its exported material chain. Meshes no other command exports (NPC canes, katanas, Sm_sword_01...) go to raw/palworld_weapons/<item>/.

using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports.Material;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace PalExtract;

public static class Items
{
    const string BpRoot = "Pal/Content/Pal/Blueprint/Weapon/";

    sealed class Comp
    {
        public string? Mesh, Parent, Socket;
        public bool Visible = true;
        public JToken? Loc, Rot, Scale; // relative to the parent component / socket (Unreal cm, FRotator degrees)
        public List<string?> Overrides = new();
    }

    static string StripIndex(string objectPath) => objectPath[..(objectPath.LastIndexOf('.') is var d && d > objectPath.LastIndexOf('/') ? d : objectPath.Length)];

    // one blueprint's own component templates (SCS nodes + inherited-component overrides), keyed by SCS variable name
    static (string? super, Dictionary<string, JObject> comps, Dictionary<string, (string? parent, string? socket)> scs) ReadBp(DefaultFileProvider provider, string path)
    {
        var exports = JArray.Parse(JsonConvert.SerializeObject(provider.LoadPackage(path).GetExports(), Program.JsonSettings));
        string? super = null;
        var comps = new Dictionary<string, JObject>();
        var scs = new Dictionary<string, (string?, string?)>();
        foreach (var e in exports.OfType<JObject>())
        {
            var type = (string?)e["Type"] ?? "";
            if (type == "BlueprintGeneratedClass" && e["SuperStruct"] is JObject so && (string?)so["ObjectPath"] is string sp)
            {
                var p = StripIndex(sp);
                if (p.StartsWith("Pal/Content/")) super = p;
            }
            else if (type == "SCS_Node" && e["Properties"] is JObject np && (string?)np["InternalVariableName"] is string v)
                scs[v] = ((string?)np["ParentComponentOrVariableName"], (string?)np["AttachToName"]);
            else if (type.EndsWith("Component") && ((string?)e["Name"])?.EndsWith("_GEN_VARIABLE") == true)
                comps[((string)e["Name"]!)[..^"_GEN_VARIABLE".Length]] = e["Properties"] as JObject ?? new JObject();
        }
        return (super, comps, scs);
    }

    public static void Export(DefaultFileProvider provider, List<string> args)
    {
        var outRoot = Path.Combine(Program.RawDir, "palworld_weapons");
        Directory.CreateDirectory(outRoot);
        var bpCache = new Dictionary<string, (string? super, Dictionary<string, JObject> comps, Dictionary<string, (string? parent, string? socket)> scs)>();
        (string? super, Dictionary<string, JObject> comps, Dictionary<string, (string? parent, string? socket)> scs) Bp(string p)
        {
            if (!bpCache.TryGetValue(p, out var r)) bpCache[p] = r = ReadBp(provider, p);
            return r;
        }
        var textures = new HashSet<string>();
        var chains = new Dictionary<string, List<string>>();
        List<string> Chain(string mat)
        {
            if (chains.TryGetValue(mat, out var c)) return c;
            var obj = provider.LoadPackage(mat).GetExports().OfType<UMaterialInterface>().FirstOrDefault();
            return chains[mat] = obj == null ? new List<string>() : Pals.ExportMaterialChain(obj, textures);
        }
        var exported = new HashSet<string>(Directory.Exists(outRoot)
            ? Directory.GetDirectories(outRoot).Where(d => File.Exists(Path.Combine(d, "pal.json"))).Select(Path.GetFileName)!
            : Array.Empty<string>(), StringComparer.OrdinalIgnoreCase);
        var done = new HashSet<string>(StringComparer.OrdinalIgnoreCase); // exported (or checked) in this run
        // DT_ItemDataTable.ItemActorClass -> blueprint (the name is not always BP_<class>: NormalPistol = BP_HandGun)
        var classesByBp = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
        var iac = JToken.Parse(JsonConvert.SerializeObject(provider.LoadPackage("Pal/Content/PalEditor/DataTable/DT_ItemActorClassDataTable").GetExports(), Program.JsonSettings));
        foreach (var row in ((iac is JArray ia ? ia[0] : iac)["Rows"] as JObject ?? new JObject()).Properties())
        {
            var ap = ((string?)row.Value["actorClass"]?["AssetPathName"] ?? "").Split('.')[0];
            if (!ap.StartsWith("/Game/") || (string?)row.Value["ClassName"] is not string cn) continue;
            var bp = "Pal/Content/" + ap["/Game/".Length..];
            (classesByBp.TryGetValue(bp, out var l) ? l : classesByBp[bp] = new List<string>()).Add(cn);
        }
        var entries = new List<object>();
        foreach (var f in provider.Files.Keys.Where(k => k.StartsWith(BpRoot, StringComparison.OrdinalIgnoreCase) && k.EndsWith(".uasset")
                     && Path.GetFileName(k).StartsWith("BP_", StringComparison.OrdinalIgnoreCase)
                     && !k.Contains("/BackWeapon/", StringComparison.OrdinalIgnoreCase) && !k.Contains("/Bullet/", StringComparison.OrdinalIgnoreCase)).OrderBy(k => k, StringComparer.Ordinal))
        {
            var path = f[..^".uasset".Length];
            var cls = Path.GetFileName(path)[3..];
            try
            {
                // leaf -> root, then apply root first so children win
                var lineage = new List<string>();
                for (string? p = path; p != null && lineage.Count < 16 && provider.Files.ContainsKey(p + ".uasset"); p = Bp(p).super) lineage.Add(p);
                var merged = new Dictionary<string, Comp>();
                foreach (var p in Enumerable.Reverse(lineage))
                {
                    var (_, comps, scs) = Bp(p);
                    foreach (var (name, (parent, socket)) in scs)
                    {
                        var c = merged.TryGetValue(name, out var x) ? x : merged[name] = new Comp();
                        c.Parent = parent ?? c.Parent;
                        c.Socket = socket ?? c.Socket;
                    }
                    foreach (var (name, props) in comps)
                    {
                        var c = merged.TryGetValue(name, out var x) ? x : merged[name] = new Comp();
                        // a child can clear the mesh (SkeletalMesh: null) as well as replace it
                        foreach (var key in new[] { "SkeletalMesh", "StaticMesh" })
                            if (props.TryGetValue(key, out var mt))
                                c.Mesh = mt is JObject mo2 && (string?)mo2["ObjectPath"] is string mesh ? StripIndex(mesh) : null;
                        if ((string?)props["bVisible"] is string vis) c.Visible = vis != "False";
                        c.Loc = props["RelativeLocation"] ?? c.Loc;
                        c.Rot = props["RelativeRotation"] ?? c.Rot;
                        c.Scale = props["RelativeScale3D"] ?? c.Scale;
                        if (props["OverrideMaterials"] is JArray om)
                            for (int i = 0; i < om.Count; i++)
                            {
                                while (c.Overrides.Count <= i) c.Overrides.Add(null);
                                if (om[i] is JObject mo && (string?)mo["ObjectPath"] is string mp) c.Overrides[i] = StripIndex(mp);
                            }
                    }
                }
                var user = path.Contains("/NPCWeapon/Otomo/", StringComparison.OrdinalIgnoreCase) || cls.EndsWith("_Otomo") ? "otomo"
                    : path.Contains("/NPCWeapon/", StringComparison.OrdinalIgnoreCase) || cls.Contains("_NPC") ? "npc"
                    : path.Contains("/UniqueRidePalWeapon/", StringComparison.OrdinalIgnoreCase) ? "ride" : "player";
                var itemClasses = classesByBp.GetValueOrDefault(path) ?? new List<string>();
                // only what someone carries becomes a model: blueprints an item row names, or an NPC's weapon. Not the Pal a launcher
                // fires (Model/Character/Monster: the Pal models already) or effect meshes.
                var carried = itemClasses.Count > 0 || user is "npc" or "otomo";
                var parts = new List<object>();
                foreach (var (name, c) in merged)
                {
                    if (c.Mesh == null || !c.Mesh.StartsWith("Pal/Content/")) continue;
                    var item = Pals.MeshItemName(c.Mesh);
                    var exportable = carried && !c.Mesh.Contains("/Model/Character/Monster/") && !c.Mesh.Contains("/Effect/");
                    if (exportable && done.Add(item) && (Program.Force || !exported.Contains(item)))
                    {
                        try
                        {
                            Pals.ExportOne(provider, item, Path.Combine(outRoot, item), c.Mesh, false);
                            exported.Add(item);
                            Console.WriteLine($"  exported {item} ({c.Mesh})");
                        }
                        catch (Exception e) { Console.WriteLine($"  {item}: export FAILED {e.GetType().Name}: {e.Message}"); continue; }
                    }
                    parts.Add(new
                    {
                        component = name, mesh = c.Mesh, item = exported.Contains(item) ? item : null, parent = c.Parent, socket = c.Socket, visible = c.Visible,
                        loc = Pals.V3(c.Loc, 0), rot = Pals.Rot(c.Rot), scale = Pals.V3(c.Scale, 1),
                        overrides = c.Overrides.Select(o => o == null ? null : (object)new { path = o, chain = Chain(o) }).ToList(),
                    });
                }
                if (parts.Count == 0) continue;
                entries.Add(new { @class = cls, blueprint = path, itemClasses, parents = lineage.Skip(1).Select(p => Path.GetFileName(p)[3..]).ToList(), user, parts });
                Console.WriteLine($"{cls,-40} {user,-6} {string.Join(", ", parts.Select(p => ((dynamic)p).item))}");
            }
            catch (Exception e) { Console.WriteLine($"{cls,-40} FAILED {e.GetType().Name}: {e.Message}"); }
        }
        foreach (var t in textures) Pals.ExportTexture(provider, t);
        File.WriteAllText(Path.Combine(outRoot, "items.json"), JsonConvert.SerializeObject(entries, Program.JsonSettings));
        Console.WriteLine($"-> palworld_weapons/items.json ({entries.Count} blueprints)");
    }
}
