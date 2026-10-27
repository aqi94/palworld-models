// PalExtract: reads Palworld's Pal-Windows.pak with CUE4Parse and writes simple, documented files
// (JSON + raw little-endian binary + PNG) for the Python importer. The pak is only read.
//
//   PalExtract list   [--filter <substr>]            -> raw/listing.txt (all package paths)
//   PalExtract probe  <package path>                  -> prints exports as JSON
//   PalExtract json   <package path> <out.json>       -> writes exports as JSON
//   PalExtract tables                                 -> raw/tables/*.json (Pal data tables, English names)
//   PalExtract pal    <code name>... | --all-pals     -> raw/palworld/<CodeName>/
//   PalExtract weapon [<code name>...]                -> raw/palworld_weapons/<Weapon>/ + held.json (pal -> weapon)
//   PalExtract human  <id>... | --all-humans          -> raw/palworld_humans/<Id>/ (NPCs, bosses, Player_<Gender> + parts/)
//   PalExtract weapons-all [<id>...]                  -> raw/palworld_weapons/<Class>_<NNN>/ for every Model/Weapon mesh
//
// Options: --game <Pal dir> (default: Steam install), --raw <dir> (default: ../../raw relative to repo),
//          --usmap <file> (optional mappings), --force (re-export existing).

using CUE4Parse.Compression;
using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Versions;
using Newtonsoft.Json;

namespace PalExtract;

public static class Program
{
    public static string GameDir = @"C:\Program Files (x86)\Steam\steamapps\common\Palworld\Pal\Content\Paks";
    public static string RawDir = "";
    public static string? Usmap;
    public static bool Force;

    public static int Main(string[] argv)
    {
        var args = new List<string>(argv);
        string? Opt(string name)
        {
            var i = args.IndexOf(name);
            if (i < 0 || i + 1 >= args.Count) return null;
            var v = args[i + 1];
            args.RemoveRange(i, 2);
            return v;
        }
        GameDir = Opt("--game") ?? GameDir;
        RawDir = Opt("--raw") ?? Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "raw"));
        Usmap = Opt("--usmap") ?? FindUsmap(RawDir);
        Force = args.Remove("--force");
        if (args.Count == 0) { Console.Error.WriteLine("usage: PalExtract list|probe|json|tables|pal ..."); return 2; }

        if (args[0] == "reflect") { Shaders.Reflect(args[1]); return 0; } // API discovery, no game needed

        using var provider = Open();
        var cmd = args[0];
        var rest = args.Skip(1).ToList();
        switch (cmd)
        {
            case "list": Commands.List(provider, rest); break;
            case "probe": Commands.Probe(provider, rest[0]); break;
            case "shader": Shaders.Dump(provider, rest); break;
            case "json": Commands.Json(provider, rest[0], rest[1]); break;
            case "tables": Tables.Export(provider); break;
            case "pal": Pals.Export(provider, rest); break;
            case "refs": Pals.FindRefs(provider, rest); break;
            case "props": Pals.ScanProps(provider); break;
            case "weapon": Pals.ExportWeapons(provider, rest); break;
            case "human": Humans.Export(provider, rest); break;
            case "physics": Humans.ExportPhysics(provider, rest); break;
            case "bossride": Humans.ExportBossRides(provider); break;
            case "weapons-all": Humans.ExportAllWeapons(provider, rest); break;
            default: Console.Error.WriteLine($"unknown command {cmd}"); return 2;
        }
        return 0;
    }

    static string? FindUsmap(string raw)
    {
        var dir = Path.Combine(raw, "mappings");
        if (!Directory.Exists(dir)) return null;
        return Directory.GetFiles(dir, "*.usmap").OrderByDescending(File.GetLastWriteTimeUtc).FirstOrDefault();
    }

    static DefaultFileProvider Open()
    {
        // Oodle lives next to the extractor binary, never in the game folder.
        var oodle = Path.Combine(AppContext.BaseDirectory, OodleHelper.OODLE_NAME_CURRENT);
        if (!File.Exists(oodle)) OodleHelper.DownloadOodleDll(ref oodle!);
        OodleHelper.Initialize(oodle);

        var provider = new DefaultFileProvider(GameDir, SearchOption.TopDirectoryOnly, true, new VersionContainer(EGame.GAME_Palworld));
        provider.Initialize();
        provider.SubmitKey(new CUE4Parse.UE4.Objects.Core.Misc.FGuid(), new CUE4Parse.Encryption.Aes.FAesKey(new byte[32]));
        provider.PostMount();
        if (Usmap != null)
        {
            provider.MappingsContainer = new FileUsmapTypeMappingsProvider(Usmap, StringComparer.OrdinalIgnoreCase);
            Console.Error.WriteLine($"mappings: {Usmap}");
        }
        else Console.Error.WriteLine("mappings: none");
        Console.Error.WriteLine($"mounted {provider.MountedVfs.Count} archives, {provider.Files.Count} files");
        return provider;
    }

    public static readonly JsonSerializerSettings JsonSettings = new() { Formatting = Formatting.Indented, ReferenceLoopHandling = ReferenceLoopHandling.Ignore };
}
