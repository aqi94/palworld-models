using CUE4Parse.FileProvider;
using Newtonsoft.Json;

namespace PalExtract;

// Data tables the catalog needs (names, Paldeck numbers, elements, skins) -> raw/tables/<name>.json
public static class Tables
{
    static readonly string[] Paths =
    {
        "Pal/Content/Pal/DataTable/Character/DT_PalMonsterParameter",
        "Pal/Content/Pal/DataTable/Character/DT_PalMonsterParameter_Common",
        "Pal/Content/Pal/DataTable/Character/DT_PalBPClass",
        "Pal/Content/Pal/DataTable/Character/DT_PalBPClass_Common",
        "Pal/Content/Pal/DataTable/Character/DT_PalCharacterIconDataTable",
        "Pal/Content/Pal/DataTable/Character/DT_PalCharacterIconDataTable_Common",
        "Pal/Content/Pal/DataTable/Skin/DT_SkinDataTable",
        "Pal/Content/Pal/DataTable/Skin/DT_SkinDataTable_Common",
        "Pal/Content/Pal/DataTable/Skin/DT_SkinAssetMapTable",
        "Pal/Content/Pal/DataTable/Skin/DT_SkinAssetMapTable_Common",
        "Pal/Content/L10N/en/Pal/DataTable/Text/DT_PalNameText_Common",
        "Pal/Content/L10N/en/Pal/DataTable/Text/DT_PalLongDescriptionText",
        "Pal/Content/L10N/en/Pal/DataTable/Text/DT_SkinNameText_Common",
        "Pal/Content/L10N/en/Pal/DataTable/Text/DT_SkinNameText",
    };

    public static void Export(DefaultFileProvider provider)
    {
        var dir = Path.Combine(Program.RawDir, "tables");
        Directory.CreateDirectory(dir);
        foreach (var p in Paths)
        {
            if (!provider.TryLoadPackage(p, out var pkg)) { Console.WriteLine($"missing {p}"); continue; }
            var name = Path.GetFileName(p) + (p.Contains("/L10N/en/") ? ".en" : "");
            File.WriteAllText(Path.Combine(dir, name + ".json"), JsonConvert.SerializeObject(pkg.GetExports(), Program.JsonSettings));
            Console.WriteLine($"-> tables/{name}.json");
        }
    }
}
