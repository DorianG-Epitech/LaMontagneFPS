using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;
using UnityEngine.Rendering;

/// Browser build (GitHub Pages): WebGL2, gzip + decompression fallback (Pages sends no Content-Encoding),
/// custom template "LaMontagne" (full screen, tap-to-start, landscape hint), smaller textures, city data from
/// Resources/LaMontagne/city_web.bytes (Tools/pack_web.py) instead of the 139 MB StreamingAssets city.bin.
public static class LaMontagneWebBuild
{
    const string Out = "Builds/WebGL";

    static void LimitTextures()
    {
        int n = 0;
        foreach (var guid in AssetDatabase.FindAssets("t:Texture2D", new[] { "Assets/LaMontagne" }))
        {
            string p = AssetDatabase.GUIDToAssetPath(guid);
            var ti = AssetImporter.GetAtPath(p) as TextureImporter;
            if (ti == null) continue;
            int max = p.Contains("/Terrain/ortho") ? 2048 : 1024;
            if (p.EndsWith("terrain_mask.png")) max = 256;
            var ps = ti.GetPlatformTextureSettings("WebGL");
            if (ps.overridden && ps.maxTextureSize == max) continue;
            ps.overridden = true; ps.maxTextureSize = Mathf.Min(max, ti.maxTextureSize); ps.format = TextureImporterFormat.Automatic;
            ps.textureCompression = TextureImporterCompression.Compressed; ps.compressionQuality = 50;
            ti.SetPlatformTextureSettings(ps); ti.SaveAndReimport(); n++;
        }
        Debug.Log($"[WEB] WebGL texture overrides set on {n} textures");
    }

    public static void Build()
    {
        string city = "Assets/StreamingAssets/LaMontagne/city.bin";
        string park = Path.Combine(Path.GetTempPath(), "lm_city.bin.parked");
        bool moved = false;
        try
        {
            if (File.Exists(city))
            {
                File.Copy(city, park, true); File.Delete(city); if (File.Exists(city + ".meta")) File.Move(city + ".meta", park + ".meta");
                moved = true; AssetDatabase.Refresh();
            }
            if (!File.Exists("Assets/LaMontagne/Resources/LaMontagne/city_web.bytes") && !File.Exists("Assets/LaMontagne/Resources/LaMontagne/city_web_0.bytes")) throw new System.Exception("run Tools/pack_web.py first");
            LimitTextures();
            PlayerSettings.productName = "La Montagne FPS"; PlayerSettings.companyName = "Dorian Gomes";
            PlayerSettings.WebGL.template = "PROJECT:LaMontagne";
            PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Gzip;
            PlayerSettings.WebGL.decompressionFallback = true;
            PlayerSettings.WebGL.dataCaching = true;
            PlayerSettings.WebGL.exceptionSupport = WebGLExceptionSupport.ExplicitlyThrownExceptionsOnly;
            PlayerSettings.WebGL.nameFilesAsHashes = false;
            PlayerSettings.WebGL.linkerTarget = WebGLLinkerTarget.Wasm;
            PlayerSettings.WebGL.initialMemorySize = 512;
            PlayerSettings.WebGL.maximumMemorySize = 2048;
            PlayerSettings.WebGL.memoryGrowthMode = WebGLMemoryGrowthMode.Geometric;
            PlayerSettings.WebGL.showDiagnostics = false;
            PlayerSettings.SetGraphicsAPIs(BuildTarget.WebGL, new[] { GraphicsDeviceType.OpenGLES3 });
            PlayerSettings.SetManagedStrippingLevel(UnityEditor.Build.NamedBuildTarget.WebGL, ManagedStrippingLevel.Low);
            PlayerSettings.runInBackground = false;
            EditorUserBuildSettings.webGLBuildSubtarget = WebGLTextureSubtarget.ASTC;
            var opts = new BuildPlayerOptions
            {
                scenes = new[] { "Assets/Scenes/LaMontagne.unity" }, locationPathName = Out,
                target = BuildTarget.WebGL, options = BuildOptions.None
            };
            var rep = BuildPipeline.BuildPlayer(opts);
            Debug.Log($"[WEB] build result {rep.summary.result}, size {rep.summary.totalSize / 1048576f:F1} MB, errors {rep.summary.totalErrors}, time {rep.summary.totalTime}");
            if (rep.summary.result != BuildResult.Succeeded) EditorApplication.Exit(2);
        }
        finally
        {
            if (moved)
            {
                File.Copy(park, city, true); if (File.Exists(park + ".meta")) { if (File.Exists(city + ".meta")) File.Delete(city + ".meta"); File.Move(park + ".meta", city + ".meta"); }
                File.Delete(park); AssetDatabase.Refresh();
            }
        }
    }
}
