using CUE4Parse.FileProvider;
using Newtonsoft.Json;

namespace PalExtract;

public static class Commands
{
    public static void List(DefaultFileProvider provider, List<string> args)
    {
        var filter = args.Count > 0 ? args[0] : null;
        Directory.CreateDirectory(Program.RawDir);
        var path = Path.Combine(Program.RawDir, filter == null ? "listing.txt" : "listing_filtered.txt");
        var keys = provider.Files.Keys.Where(k => filter == null || k.Contains(filter, StringComparison.OrdinalIgnoreCase)).OrderBy(k => k, StringComparer.Ordinal).ToList();
        File.WriteAllLines(path, keys);
        Console.WriteLine($"{keys.Count} paths -> {path}");
    }

    public static void Probe(DefaultFileProvider provider, string path)
    {
        var pkg = provider.LoadPackage(path);
        foreach (var e in pkg.GetExports())
            Console.WriteLine($"{e.ExportType} {e.Name}");
        Console.WriteLine(JsonConvert.SerializeObject(pkg.GetExports(), Program.JsonSettings));
    }

    public static void Json(DefaultFileProvider provider, string path, string outPath)
    {
        var pkg = provider.LoadPackage(path);
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outPath))!);
        File.WriteAllText(outPath, JsonConvert.SerializeObject(pkg.GetExports(), Program.JsonSettings));
        Console.WriteLine($"-> {outPath}");
    }
}
