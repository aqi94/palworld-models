// Cooked shaders: recover the original material maths from the game's compiled shader bytecode.
//
//   reflect <regex>              print the public members of CUE4Parse types whose name matches (API discovery)
//   shader <material pkg>... | --from-raw [--force]
//                                raw/shaders/<material package path>/: resources.json (shader maps + uniform expression set: which
//                                parameter feeds which constant slot), <freq>_<i>.dxbc and .asm (D3DDisassemble) per shader.
//                                --from-raw: every material in raw/palworld*/*/pal.json chains whose base is not the PalLit
//                                family (the effect shaders the importer rebuilds), each instance and its base.
//
// Material shader maps are not inline in cooked packages: the material resource holds the map's ResourceHash, and the
// project shader archive (ShaderArchive-Pal-PCD3D_SM5) maps that hash to the shaders' compressed code.
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using CUE4Parse.Compression;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Objects.Core.Misc;
using CUE4Parse.UE4.Readers;
using CUE4Parse.UE4.Shaders;
using Newtonsoft.Json;

namespace PalExtract;

public static class Shaders
{
    const string ArchivePath = "Pal/Content/ShaderArchive-Pal-PCD3D_SM5-PCD3D_SM5.ushaderbytecode";

    public static void Reflect(string pattern)
    {
        var asm = typeof(DefaultFileProvider).Assembly;
        Type[] types;
        try { types = asm.GetTypes(); }
        catch (ReflectionTypeLoadException e) { types = e.Types.Where(t => t != null).ToArray()!; }
        var re = new Regex(pattern);
        foreach (var t in types.Where(t => re.IsMatch(t.Name)).OrderBy(t => t.FullName))
        {
            Console.WriteLine($"== {t.FullName} : {t.BaseType?.Name}");
            const BindingFlags F = BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
            foreach (var f in t.GetFields(F)) Console.WriteLine($"   field {(f.IsStatic ? "static " : "")}{f.FieldType.Name} {f.Name}");
            foreach (var p in t.GetProperties(F)) Console.WriteLine($"   prop  {p.PropertyType.Name} {p.Name}");
            foreach (var m in t.GetMethods(F).Where(m => !m.IsSpecialName))
                Console.WriteLine($"   meth  {(m.IsStatic ? "static " : "")}{m.ReturnType.Name} {m.Name}({string.Join(", ", m.GetParameters().Select(p => p.ParameterType.Name + " " + p.Name))})");
            foreach (var c in t.GetConstructors()) Console.WriteLine($"   ctor  ({string.Join(", ", c.GetParameters().Select(p => p.ParameterType.Name + " " + p.Name))})");
        }
    }

    public static void Dump(DefaultFileProvider provider, List<string> args)
    {
        var materials = args.Where(a => !a.StartsWith("--")).ToList();
        if (args.Contains("--from-raw")) materials.AddRange(EffectMaterialsFromRaw());
        provider.ReadShaderMaps = true;
        var archBytes = provider.SaveAsset(ArchivePath);
        var arch = new FShaderCodeArchive(new FByteArchive("shaderarchive", archBytes, provider.Versions));
        if (arch.SerializedShaders is not FSerializedShaderArchive lib)
            throw new Exception($"unexpected shader library {arch.SerializedShaders?.GetType().Name}");
        Console.WriteLine($"archive: {lib.ShaderMapHashes.Length} shader maps, {lib.ShaderEntries.Length} shaders, code arrays {arch.ShaderCode?.Length}");
        var mapIndex = new Dictionary<string, int>();
        for (int i = 0; i < lib.ShaderMapHashes.Length; i++) mapIndex[Hex(lib.ShaderMapHashes[i])] = i;

        foreach (var path in materials.Distinct())
        {
            var outDir = Path.Combine(Program.RawDir, "shaders", path.Replace('/', Path.DirectorySeparatorChar));
            if (!Program.Force && File.Exists(Path.Combine(outDir, "resources.json"))) continue;
            UMaterialInterface mat;
            try { mat = provider.LoadPackageObject<UMaterialInterface>(path); }
            catch (Exception e) { Console.WriteLine($"{path}: {e.Message}"); continue; }
            Directory.CreateDirectory(outDir);
            File.WriteAllText(Path.Combine(outDir, "resources.json"), JsonConvert.SerializeObject(mat.LoadedMaterialResources, Program.JsonSettings));
            Console.WriteLine($"{mat.Name}: {mat.LoadedMaterialResources?.Count ?? 0} material resources");
            int r = 0;
            foreach (var res in mat.LoadedMaterialResources ?? new())
            {
                var sm = res.LoadedShaderMap;
                if (sm?.ResourceHash is not FSHAHash h) { Console.WriteLine($"  [{r}] no shader map hash"); r++; continue; }
                var id = sm.ShaderMapId;
                var tag = $"r{r}_{id?.FeatureLevel}_{id?.QualityLevel}";
                if (!mapIndex.TryGetValue(Hex(h), out var mi)) { Console.WriteLine($"  [{tag}] map {Hex(h)} not in archive"); r++; continue; }
                var entry = lib.ShaderMapEntries[mi];
                Console.WriteLine($"  [{tag}] map {mi}: {entry.NumShaders} shaders");
                // every shader in the map, by its ResourceIndex; the content lists which type/vertex factory each one is
                var content = sm.Content as FMaterialShaderMapContent;
                var described = new List<(string vf, FShader s)>();
                if (content != null)
                {
                    foreach (var s in content.Shaders ?? Array.Empty<FShader>()) described.Add(("-", s));
                    foreach (var mm in content.OrderedMeshShaderMaps ?? Array.Empty<FMeshMaterialShaderMap>())
                        foreach (var s in mm.Shaders ?? Array.Empty<FShader>()) described.Add((mm.VertexFactoryTypeName.Hash.ToString("x16"), s));
                }
                var index = new List<object>();
                foreach (var (vf, s) in described)
                {
                    if (s.ResourceIndex < 0 || s.ResourceIndex >= entry.NumShaders) continue;
                    var si = (int)lib.ShaderIndices[entry.ShaderIndicesOffset + s.ResourceIndex];
                    var freq = s.Target.Frequency.ToString();
                    var name = $"{tag}_{freq}_{s.ResourceIndex}";
                    var code = ShaderCode(arch, lib, si);
                    var dxbc = code == null ? null : ExtractDxbc(code);
                    string? asmText = null;
                    if (dxbc != null)
                    {
                        File.WriteAllBytes(Path.Combine(outDir, name + ".dxbc"), dxbc);
                        if (freq.Contains("Pixel") || freq.Contains("Vertex"))
                        {
                            asmText = Disassemble(dxbc);
                            if (asmText != null) File.WriteAllText(Path.Combine(outDir, name + ".asm"), asmText);
                        }
                    }
                    index.Add(new { name, type = s.Type.Hash.ToString("x16"), vf, freq, s.ResourceIndex, s.NumInstructions, archiveShader = si,
                                    dxbc = dxbc?.Length, asm = asmText != null, s.ParameterMapInfo });
                }
                File.WriteAllText(Path.Combine(outDir, tag + "_index.json"), JsonConvert.SerializeObject(index, Program.JsonSettings));
                r++;
            }
        }
    }

    // chain[0] and chain[-1] of every material slot whose base material is not M_PalLit* (PalLit maps cleanly to glTF)
    static IEnumerable<string> EffectMaterialsFromRaw()
    {
        var found = new SortedSet<string>(StringComparer.Ordinal);
        foreach (var dir in new[] { "palworld", "palworld_weapons", "palworld_humans" })
        {
            var root = Path.Combine(Program.RawDir, dir);
            if (!Directory.Exists(root)) continue;
            foreach (var f in Directory.GetFiles(root, "pal.json", SearchOption.AllDirectories))
            {
                var j = Newtonsoft.Json.Linq.JObject.Parse(File.ReadAllText(f));
                foreach (var m in j["materials"] ?? new Newtonsoft.Json.Linq.JArray())
                {
                    var chain = m["chain"]?.Select(c => (string)c!).ToList();
                    if (chain == null || chain.Count == 0 || Path.GetFileName(chain[^1]).StartsWith("M_PalLit")) continue;
                    found.Add(chain[0]);
                    found.Add(chain[^1]);
                }
            }
        }
        Console.WriteLine($"{found.Count} effect materials from raw/");
        return found;
    }

    static string Hex(FSHAHash h) => Convert.ToHexString(MemoryMarshal.AsBytes(MemoryMarshal.CreateReadOnlySpan(ref h, 1)));

    static byte[]? ShaderCode(FShaderCodeArchive arch, FSerializedShaderArchive lib, int si)
    {
        var e = lib.ShaderEntries[si];
        var raw = arch.ShaderCode != null && si < arch.ShaderCode.Length ? arch.ShaderCode[si] : null;
        if (raw == null) return null;
        if (raw.Length == e.UncompressedSize) return raw;
        return Compression.Decompress(raw, (int)e.UncompressedSize, CompressionMethod.Oodle, null!);
    }

    // The engine appends optional data after the bytecode; the DXBC container carries its own size (offset 24).
    static byte[]? ExtractDxbc(byte[] code)
    {
        for (int i = 0; i + 28 <= code.Length; i++)
        {
            if (code[i] != 'D' || code[i + 1] != 'X' || code[i + 2] != 'B' || code[i + 3] != 'C') continue;
            var size = BitConverter.ToInt32(code, i + 24);
            if (size > 0 && i + size <= code.Length) return code[i..(i + size)];
        }
        return null;
    }

    [ComImport, Guid("8BA5FB08-5195-40e2-AC58-0D989C3A0102"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface ID3DBlob
    {
        [PreserveSig] IntPtr GetBufferPointer();
        [PreserveSig] UIntPtr GetBufferSize();
    }

    [DllImport("d3dcompiler_47.dll", PreserveSig = true)]
    static extern int D3DDisassemble(byte[] src, UIntPtr size, uint flags, [MarshalAs(UnmanagedType.LPStr)] string? comments, out ID3DBlob blob);

    static string? Disassemble(byte[] dxbc)
    {
        try
        {
            if (D3DDisassemble(dxbc, (UIntPtr)dxbc.Length, 0, null, out var blob) != 0 || blob == null) return null;
            return Marshal.PtrToStringAnsi(blob.GetBufferPointer(), (int)blob.GetBufferSize()).TrimEnd('\0');
        }
        catch (Exception e) { Console.Error.WriteLine($"disassemble: {e.Message}"); return null; }
    }
}
