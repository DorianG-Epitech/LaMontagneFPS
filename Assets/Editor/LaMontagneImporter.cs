using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

/// Re-runnable importer: LaMontagne > Build Scene.
/// Reads <project>/ImportData (written by Tools/build_city.py) and builds Assets/Scenes/LaMontagne.unity:
/// 2x2 real-scale terrains (IGN RGE ALTI + BD ORTHO), LiDAR-detected trees, city meshes (streamed from StreamingAssets),
/// materials, lighting/post, first-person player with hitscan gun and HUD.
public static class LaMontagneImporter
{
    const string Root = "Assets/LaMontagne";
    const string Gen = Root + "/Generated";
    const string Tex = Root + "/Textures";
    const string ScenePath = "Assets/Scenes/LaMontagne.unity";
    const float TileSize = 2048f, HScale = 64f;
    const int Res = 1025;

    static string Proj => Path.GetDirectoryName(Application.dataPath);
    static string Imp => Path.Combine(Proj, "ImportData");

    [MenuItem("LaMontagne/Build Scene (import data)")]
    public static void Build()
    {
        var t0 = System.DateTime.Now;
        foreach (var d in new[] { Gen, Gen + "/Materials", Gen + "/Terrain", Gen + "/Trees", "Assets/StreamingAssets/LaMontagne" })
            Directory.CreateDirectory(Path.Combine(Proj, d));
        File.Copy(Path.Combine(Imp, "city.bin"), Path.Combine(Proj, "Assets/StreamingAssets/LaMontagne/city.bin"), true);
        File.Copy(Path.Combine(Imp, "meta.json"), Path.Combine(Proj, "Assets/StreamingAssets/LaMontagne/meta.json"), true);
        for (int r = 0; r < 2; r++) for (int c = 0; c < 2; c++)
            File.Copy(Path.Combine(Imp, $"ortho_t{r}{c}.png"), Path.Combine(Proj, Gen, $"Terrain/ortho_t{r}{c}.png"), true);
        if (File.Exists(Path.Combine(Imp, "signs.png"))) File.Copy(Path.Combine(Imp, "signs.png"), Path.Combine(Proj, Tex, "signs.png"), true);
        {
            var mt = new Texture2D(4, 4, TextureFormat.RGBA32, false, true);
            var px = new Color32[16]; for (int k = 0; k < 16; k++) px[k] = new Color32(0, 255, 0, 0);
            mt.SetPixels32(px); File.WriteAllBytes(Path.Combine(Proj, Gen, "Terrain/terrain_mask.png"), mt.EncodeToPNG());
        }
        AssetDatabase.Refresh();
        SetupTextures();

        var meta = JsonUtility.FromJson<CityMeta>(File.ReadAllText(Path.Combine(Imp, "meta.json")));
        var mats = BuildCityMaterials(meta, out var names);
        var treeProtos = BuildTrees();

        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var terrains = BuildTerrain(treeProtos);
        SetupLighting();
        var cityGo = new GameObject("City");
        var loader = cityGo.AddComponent<CityLoader>();
        loader.materialNames = names; loader.materials = mats;
        loader.fallback = Mat("Fallback", "wall_plain", null, Color.magenta, 0.2f);
        BuildBounds();
        var sur = GameObject.CreatePrimitive(PrimitiveType.Plane); sur.name = "Surroundings (outside data area)";
        Object.DestroyImmediate(sur.GetComponent<Collider>());
        sur.transform.position = new Vector3(2048, 0.5f, 2048);   // below the Loire level (1.75 m) and the DEM minimum (1.65 m) sur.transform.localScale = new Vector3(3000, 1, 3000);
        sur.GetComponent<MeshRenderer>().sharedMaterial = Mat("Surroundings", null, null, new Color(0.30f, 0.36f, 0.24f), 0.0f);
        BuildPlayer(meta);
        EditorSceneManager.SaveScene(scene, ScenePath);
        EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(ScenePath, true) };
        ConfigureUrp();
        AssetDatabase.SaveAssets();
        Debug.Log($"[LAMONTAGNE] scene built in {(System.DateTime.Now - t0).TotalSeconds:F1}s -> {ScenePath}");
    }

    // ------------------------------------------------------------------ textures
    static void SetupTextures()
    {
        foreach (var guid in AssetDatabase.FindAssets("t:Texture2D", new[] { Tex, Gen + "/Terrain" }))
        {
            string p = AssetDatabase.GUIDToAssetPath(guid);
            var ti = AssetImporter.GetAtPath(p) as TextureImporter;
            if (ti == null) continue;
            bool normal = p.EndsWith("_n.png");
            bool ortho = p.Contains("/Terrain/");
            bool alpha = p.EndsWith("bullethole.png") || p.EndsWith("muzzleflash.png") || p.EndsWith("fence.png") || p.EndsWith("grass_blades.png");
            bool big = p.Contains("/fa_") || p.EndsWith("signs.png") || p.Contains("church_wall") || p.EndsWith("tuffeau.png");
            bool mask = p.EndsWith("terrain_mask.png");
            ti.textureType = normal ? TextureImporterType.NormalMap : TextureImporterType.Default;
            ti.mipmapEnabled = true;
            ti.anisoLevel = ortho ? 16 : 8;
            ti.wrapMode = ortho || (alpha && !p.EndsWith("fence.png")) || p.EndsWith("signs.png") ? TextureWrapMode.Clamp : TextureWrapMode.Repeat;
            ti.maxTextureSize = ortho ? 4096 : (big ? 2048 : 1024);
            ti.textureCompression = TextureImporterCompression.CompressedHQ;
            ti.alphaIsTransparency = alpha;
            ti.sRGBTexture = !normal && !mask;
            if (p.EndsWith("signs.png")) ti.textureCompression = TextureImporterCompression.Uncompressed;
            if (mask) { ti.textureCompression = TextureImporterCompression.Uncompressed; ti.mipmapEnabled = false; }
            ti.SaveAndReimport();
        }
    }
    static Texture2D T(string n) => n == null ? null : AssetDatabase.LoadAssetAtPath<Texture2D>($"{Tex}/{n}.png");

    // ------------------------------------------------------------------ materials
    static Material Mat(string name, string tex, string normal, Color col, float smooth, float metal = 0f, float bump = 1f)
    {
        string p = $"{Gen}/Materials/{name}.mat";
        var m = AssetDatabase.LoadAssetAtPath<Material>(p);
        if (m == null) { m = new Material(Shader.Find("Universal Render Pipeline/Lit")); AssetDatabase.CreateAsset(m, p); }
        m.shader = Shader.Find("Universal Render Pipeline/Lit");
        var t = T(tex);
        m.SetTexture("_BaseMap", t); m.SetTexture("_MainTex", t);
        m.SetColor("_BaseColor", col); m.SetColor("_Color", col);
        m.SetFloat("_Smoothness", smooth); m.SetFloat("_Metallic", metal);
        var n = T(normal);
        if (n) { m.SetTexture("_BumpMap", n); m.SetFloat("_BumpScale", bump); m.EnableKeyword("_NORMALMAP"); }
        else { m.SetTexture("_BumpMap", null); m.DisableKeyword("_NORMALMAP"); }
        m.enableInstancing = true;
        EditorUtility.SetDirty(m);
        return m;
    }

    static Material Transparent(string name, string tex, Color col, bool additive)
    {
        string p = $"{Gen}/Materials/{name}.mat";
        var m = AssetDatabase.LoadAssetAtPath<Material>(p);
        var sh = Shader.Find("Universal Render Pipeline/Unlit");
        if (m == null) { m = new Material(sh); AssetDatabase.CreateAsset(m, p); }
        m.shader = sh;
        m.SetTexture("_BaseMap", T(tex)); m.SetColor("_BaseColor", col);
        m.SetFloat("_Surface", 1); m.SetFloat("_Blend", additive ? 2 : 0);
        m.SetOverrideTag("RenderType", "Transparent");
        m.SetFloat("_SrcBlend", (float)(additive ? BlendMode.SrcAlpha : BlendMode.SrcAlpha));
        m.SetFloat("_DstBlend", (float)(additive ? BlendMode.One : BlendMode.OneMinusSrcAlpha));
        m.SetFloat("_SrcBlendAlpha", (float)BlendMode.One);
        m.SetFloat("_DstBlendAlpha", (float)(additive ? BlendMode.One : BlendMode.OneMinusSrcAlpha));
        m.SetFloat("_ZWrite", 0);
        m.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
        m.renderQueue = (int)RenderQueue.Transparent;
        EditorUtility.SetDirty(m);
        return m;
    }

    static Color Boost(Color c, float sat, float val)
    {
        Color.RGBToHSV(c, out var h, out var s, out var v);
        return Color.HSVToRGB(h, Mathf.Clamp01(s * sat), Mathf.Clamp01(v * val));
    }

    static Material[] BuildCityMaterials(CityMeta meta, out string[] names)
    {
        var pal = meta.palette_list.ToDictionary(p => p.name, p => new Color(p.r, p.g, p.b));
        var list = new List<(string, Material)>();
        foreach (var kv in pal)
        {
            string n = kv.Key; Color c = kv.Value;
            if (n.StartsWith("Render_"))
            {
                list.Add(("Facade_" + n, Mat("Facade_" + n, "facade_render", "facade_render_n", c, 0.18f)));
                list.Add(("Plain_" + n, Mat("Plain_" + n, "wall_plain", "wall_plain_n", c, 0.12f)));
            }
            else if (n.StartsWith("RoofTile")) list.Add((n, Mat(n, "roof_tile", "roof_tile_n", Boost(c, 1.45f, 1.12f), 0.22f)));
            else if (n.StartsWith("RoofSlate")) list.Add((n, Mat(n, "roof_slate", "roof_slate_n", Boost(c, 1.1f, 0.95f), 0.35f)));
            else if (n.StartsWith("RoofFlat")) list.Add((n, Mat(n, "roof_flat", "roof_flat_n", c, 0.1f)));
            else if (n.StartsWith("RoofMetal")) list.Add((n, Mat(n, "roof_metal", "roof_metal_n", c, 0.45f, 0.5f)));
        }
        list.Add(("RoofSlate_Church", Mat("RoofSlate_Church", "roof_slate", "roof_slate_n", new Color(0.30f, 0.33f, 0.37f), 0.4f)));
        list.Add(("Stone", Mat("Stone", "facade_stone", "facade_stone_n", Color.white, 0.15f)));
        list.Add(("ChurchStone", Mat("ChurchStone", "church_stone", "church_stone_n", new Color(0.95f, 0.93f, 0.88f), 0.12f)));
        list.Add(("Industrial_Grey", Mat("Industrial_Grey", "industrial", "industrial_n", new Color(0.62f, 0.64f, 0.66f), 0.35f, 0.3f)));
        list.Add(("Industrial_Blue", Mat("Industrial_Blue", "industrial", "industrial_n", new Color(0.45f, 0.55f, 0.66f), 0.35f, 0.3f)));
        list.Add(("Industrial_White", Mat("Industrial_White", "industrial", "industrial_n", new Color(0.90f, 0.90f, 0.88f), 0.35f, 0.3f)));
        list.Add(("Glass", Mat("Glass", "wall_plain", null, new Color(0.55f, 0.65f, 0.68f), 0.95f, 0.2f)));
        list.Add(("Road", Mat("Road", "asphalt", "asphalt_n", new Color(1.05f, 1.05f, 1.06f), 0.25f)));
        list.Add(("Sidewalk", Mat("Sidewalk", "sidewalk", "sidewalk_n", new Color(0.78f, 0.78f, 0.79f), 0.18f)));
        list.Add(("Curb", Mat("Curb", "kerb", "kerb_n", Color.white, 0.15f)));
        list.Add(("Concrete", Mat("Concrete", "concrete", "concrete_n", new Color(0.8f, 0.8f, 0.78f), 0.15f)));
        list.Add(("Path", Mat("Path", "path", "path_n", Color.white, 0.08f)));
        list.Add(("Gravel", Mat("Gravel", "gravel", "gravel_n", Color.white, 0.08f)));
        list.Add(("Ballast", Mat("Ballast", "ballast", "ballast_n", Color.white, 0.08f)));
        list.Add(("Rail", Mat("Rail", "rail", null, new Color(0.55f, 0.5f, 0.45f), 0.6f, 0.8f)));
        // ---- v2: facade atlases (colour baked in the texture), church, street details
        foreach (var st in new[] { "bourg_white_brick", "bourg_grey_tuffeau", "bourg_moellon", "bourg_cream", "pav_white_brown", "pav_white_roller", "pav_cream_blue", "pav_grey_white" })
            list.Add(("Fa_" + st, Mat("Fa_" + st, "fa_" + st, "fa_" + st + "_n", Color.white, 0.16f)));
        list.Add(("ChurchWall", Mat("ChurchWall", "church_wall", "church_wall_n", Color.white, 0.12f)));
        list.Add(("Granite", Mat("Granite", "granite", "granite_n", Color.white, 0.2f)));
        list.Add(("Tuffeau", Mat("Tuffeau", "tuffeau", "tuffeau_n", Color.white, 0.12f)));
        list.Add(("Marking", Mat("Marking", "marking", null, new Color(0.95f, 0.95f, 0.93f), 0.3f)));
        list.Add(("GardenWall", Mat("GardenWall", "garden_wall", "garden_wall_n", Color.white, 0.12f)));
        var fence = Mat("Fence", "fence", null, Color.white, 0.35f, 0.3f);
        fence.SetFloat("_AlphaClip", 1); fence.SetFloat("_Cutoff", 0.5f); fence.EnableKeyword("_ALPHATEST_ON");
        fence.SetFloat("_Cull", 0); fence.doubleSidedGI = true; fence.renderQueue = (int)RenderQueue.AlphaTest; fence.SetOverrideTag("RenderType", "TransparentCutout");
        list.Add(("Fence", fence));
        var hedge = Mat("Hedge", "hedge", "hedge_n", Color.white, 0.08f);
        hedge.SetFloat("_EnvironmentReflections", 0f); hedge.EnableKeyword("_ENVIRONMENTREFLECTIONS_OFF");
        list.Add(("Hedge", hedge));
        list.Add(("MetalGrey", Mat("MetalGrey", null, null, new Color(0.42f, 0.44f, 0.46f), 0.45f, 0.6f)));
        list.Add(("MetalDark", Mat("MetalDark", null, null, new Color(0.12f, 0.12f, 0.13f), 0.4f, 0.6f)));
        list.Add(("GreenMetal", Mat("GreenMetal", null, null, new Color(0.10f, 0.26f, 0.17f), 0.45f, 0.5f)));
        var lamp = Mat("LampHead", null, null, new Color(0.35f, 0.37f, 0.40f), 0.6f, 0.5f);
        list.Add(("LampHead", lamp));
        list.Add(("Wood", Mat("Wood", null, null, new Color(0.45f, 0.32f, 0.20f), 0.2f)));
        list.Add(("YellowPaint", Mat("YellowPaint", null, null, new Color(0.95f, 0.78f, 0.05f), 0.5f)));
        list.Add(("Signs", Mat("Signs", "signs", null, Color.white, 0.35f)));
        list.Add(("CarGlass", Mat("CarGlass", null, null, new Color(0.08f, 0.10f, 0.12f), 0.92f, 0.2f)));
        list.Add(("Tyre", Mat("Tyre", null, null, new Color(0.06f, 0.06f, 0.06f), 0.15f)));
        foreach (var (cn, cc) in new[] { ("White", new Color(0.88f, 0.88f, 0.87f)), ("Grey", new Color(0.33f, 0.34f, 0.36f)), ("Black", new Color(0.05f, 0.05f, 0.06f)),
                                          ("Blue", new Color(0.10f, 0.17f, 0.35f)), ("Red", new Color(0.55f, 0.06f, 0.06f)), ("Silver", new Color(0.62f, 0.63f, 0.65f)) })
            list.Add(("Car_" + cn, Mat("Car_" + cn, null, null, cc, 0.75f, 0.35f)));
        var water = Mat("Water", "water", "water_n", new Color(0.30f, 0.35f, 0.37f), 0.90f, 0f, 0.35f);
        water.SetTextureScale("_BumpMap", Vector2.one);
        list.Add(("Water", water));
        names = list.Select(x => x.Item1).ToArray();
        return list.Select(x => x.Item2).ToArray();
    }

    // ------------------------------------------------------------------ trees (procedural meshes, LODGroup prefabs)
    class MB { public List<Vector3> v = new List<Vector3>(); public List<Vector3> n = new List<Vector3>(); public List<Vector2> uv = new List<Vector2>(); public List<int>[] t = { new List<int>(), new List<int>() }; }

    static void Cylinder(MB m, float y0, float y1, float r0, float r1, int sides)
    {
        int b = m.v.Count;
        for (int i = 0; i <= sides; i++)
        {
            float a = i * Mathf.PI * 2 / sides; var d = new Vector3(Mathf.Cos(a), 0, Mathf.Sin(a));
            m.v.Add(d * r0 + Vector3.up * y0); m.n.Add(d); m.uv.Add(new Vector2(i / (float)sides, 0));
            m.v.Add(d * r1 + Vector3.up * y1); m.n.Add(d); m.uv.Add(new Vector2(i / (float)sides, (y1 - y0) / 2));
        }
        for (int i = 0; i < sides; i++)
        {
            int a = b + i * 2;
            m.t[0].AddRange(new[] { a, a + 1, a + 2, a + 2, a + 1, a + 3 });
        }
    }

    static void Blob(MB m, Vector3 c, Vector3 r, int subdiv, int seed)
    {
        // icosphere
        float p = (1 + Mathf.Sqrt(5)) / 2;
        var verts = new List<Vector3> { new(-1, p, 0), new(1, p, 0), new(-1, -p, 0), new(1, -p, 0), new(0, -1, p), new(0, 1, p), new(0, -1, -p), new(0, 1, -p), new(p, 0, -1), new(p, 0, 1), new(-p, 0, -1), new(-p, 0, 1) };
        for (int i = 0; i < verts.Count; i++) verts[i] = verts[i].normalized;
        var tris = new List<int> { 0, 11, 5, 0, 5, 1, 0, 1, 7, 0, 7, 10, 0, 10, 11, 1, 5, 9, 5, 11, 4, 11, 10, 2, 10, 7, 6, 7, 1, 8, 3, 9, 4, 3, 4, 2, 3, 2, 6, 3, 6, 8, 3, 8, 9, 4, 9, 5, 2, 4, 11, 6, 2, 10, 8, 6, 7, 9, 8, 1 };
        for (int s = 0; s < subdiv; s++)
        {
            var mid = new Dictionary<long, int>(); var nt = new List<int>();
            int Mid(int a, int b2)
            {
                long k = a < b2 ? ((long)a << 32) | (uint)b2 : ((long)b2 << 32) | (uint)a;
                if (mid.TryGetValue(k, out int idx)) return idx;
                verts.Add(((verts[a] + verts[b2]) * 0.5f).normalized); mid[k] = verts.Count - 1; return verts.Count - 1;
            }
            for (int i = 0; i < tris.Count; i += 3)
            {
                int a = tris[i], b2 = tris[i + 1], cc = tris[i + 2];
                int ab = Mid(a, b2), bc = Mid(b2, cc), ca = Mid(cc, a);
                nt.AddRange(new[] { a, ab, ca, b2, bc, ab, cc, ca, bc, ab, bc, ca });
            }
            tris = nt;
        }
        var rnd = new System.Random(seed);
        int b0 = m.v.Count;
        foreach (var d in verts)
        {
            float k = 1f + ((float)rnd.NextDouble() - 0.5f) * 0.28f;
            var pos = c + Vector3.Scale(d, r) * k;
            m.v.Add(pos); m.n.Add((Vector3.Scale(d, new Vector3(1 / r.x, 1 / r.y, 1 / r.z)).normalized + Vector3.up * 0.25f).normalized);
            m.uv.Add(new Vector2(Mathf.Atan2(d.z, d.x) / Mathf.PI * 2, d.y * 2));
        }
        // icosphere winding is CCW from outside (right-handed); Unity wants clockwise
        for (int i = 0; i < tris.Count; i += 3) m.t[1].AddRange(new[] { b0 + tris[i], b0 + tris[i + 2], b0 + tris[i + 1] });
    }

    static Mesh ToMesh(MB m, string name)
    {
        var mesh = new Mesh { name = name };
        mesh.SetVertices(m.v); mesh.SetNormals(m.n); mesh.SetUVs(0, m.uv);
        mesh.subMeshCount = 2; mesh.SetTriangles(m.t[0], 0); mesh.SetTriangles(m.t[1], 1);
        mesh.RecalculateBounds(); mesh.RecalculateTangents();
        return mesh;
    }

    struct Crown { public Vector3 c, r; public Crown(Vector3 c, Vector3 r) { this.c = c; this.r = r; } }

    static GameObject TreePrefab(string name, float trunkTop, float trunkR, Crown[] crowns, Material bark, Material leaves, bool collider)
    {
        var m0 = new MB(); var m1 = new MB();
        if (trunkTop > 0) { Cylinder(m0, -0.3f, trunkTop, trunkR, trunkR * 0.6f, 7); Cylinder(m1, -0.3f, trunkTop * 0.8f, trunkR, trunkR * 0.6f, 4); }
        int s = name.GetHashCode();
        foreach (var cr in crowns) Blob(m0, cr.c, cr.r, 1, s++);
        // LOD1: one blob enclosing the crowns
        var b = new Bounds(crowns[0].c, Vector3.zero);
        foreach (var cr in crowns) { b.Encapsulate(cr.c + cr.r); b.Encapsulate(cr.c - cr.r); }
        Blob(m1, b.center, b.extents * 0.92f, 0, s);
        string dir = Gen + "/Trees";
        var mesh0 = ToMesh(m0, name + "_LOD0"); var mesh1 = ToMesh(m1, name + "_LOD1");
        AssetDatabase.DeleteAsset($"{dir}/{name}_LOD0.asset"); AssetDatabase.DeleteAsset($"{dir}/{name}_LOD1.asset");
        AssetDatabase.CreateAsset(mesh0, $"{dir}/{name}_LOD0.asset"); AssetDatabase.CreateAsset(mesh1, $"{dir}/{name}_LOD1.asset");
        var root = new GameObject(name);
        var lods = new LOD[2];
        for (int i = 0; i < 2; i++)
        {
            var g = new GameObject("LOD" + i); g.transform.SetParent(root.transform, false);
            g.AddComponent<MeshFilter>().sharedMesh = i == 0 ? mesh0 : mesh1;
            var r = g.AddComponent<MeshRenderer>(); r.sharedMaterials = new[] { bark, leaves };
            lods[i] = new LOD(i == 0 ? 0.08f : 0.0025f, new Renderer[] { r });
        }
        var lg = root.AddComponent<LODGroup>(); lg.SetLODs(lods); lg.RecalculateBounds();
        if (collider)
        {
            var cap = root.AddComponent<CapsuleCollider>(); cap.radius = trunkR * 1.3f; cap.height = trunkTop + 0.3f; cap.center = new Vector3(0, trunkTop / 2, 0);
        }
        string path = $"{dir}/{name}.prefab";
        var prefab = PrefabUtility.SaveAsPrefabAsset(root, path);
        Object.DestroyImmediate(root);
        return prefab;
    }

    static GameObject[] BuildTrees()
    {
        var bark = Mat("Bark", "bark", null, new Color(0.75f, 0.72f, 0.68f), 0.05f);
        var la = Mat("Leaves_A", "leaves", null, new Color(0.80f, 0.86f, 0.74f), 0.12f);
        var lb = Mat("Leaves_B", "leaves", null, new Color(0.92f, 0.92f, 0.70f), 0.12f);
        var lc = Mat("Leaves_Conifer", "leaves", null, new Color(0.55f, 0.66f, 0.58f), 0.12f);
        var lp = Mat("Leaves_Poplar", "leaves", null, new Color(0.95f, 1.0f, 0.75f), 0.12f);
        var lh = Mat("Leaves_Bush", "leaves", null, new Color(0.85f, 0.92f, 0.72f), 0.1f);
        foreach (var vm in new[] { bark, la, lb, lc, lp, lh }) { vm.SetFloat("_EnvironmentReflections", 0f); vm.EnableKeyword("_ENVIRONMENTREFLECTIONS_OFF"); vm.SetFloat("_SpecularHighlights", 0f); vm.EnableKeyword("_SPECULARHIGHLIGHTS_OFF"); }
        return new[]
        {
            TreePrefab("Tree_BroadleafA", 4.2f, 0.22f, new[] { new Crown(new(0, 6.3f, 0), new(3.1f, 2.6f, 3.1f)), new Crown(new(1.3f, 7.2f, 0.6f), new(2.2f, 2.0f, 2.2f)), new Crown(new(-1.1f, 7.0f, -0.8f), new(2.3f, 2.1f, 2.3f)), new Crown(new(0.2f, 8.3f, -0.2f), new(1.8f, 1.6f, 1.8f)) }, bark, la, true),
            TreePrefab("Tree_BroadleafB", 3.4f, 0.26f, new[] { new Crown(new(0, 5.8f, 0), new(3.8f, 2.6f, 3.8f)), new Crown(new(1.8f, 6.6f, 1f), new(2.4f, 2f, 2.4f)), new Crown(new(-1.9f, 6.3f, 0.6f), new(2.4f, 2f, 2.4f)), new Crown(new(0, 7.8f, -1f), new(2.2f, 1.9f, 2.2f)) }, bark, lb, true),
            TreePrefab("Tree_Conifer", 5.5f, 0.2f, new[] { new Crown(new(0, 4.6f, 0), new(2.5f, 2.2f, 2.5f)), new Crown(new(0, 6.6f, 0), new(1.9f, 1.8f, 1.9f)), new Crown(new(0, 8.4f, 0), new(1.1f, 1.5f, 1.1f)) }, bark, lc, true),
            TreePrefab("Tree_Poplar", 2.0f, 0.2f, new[] { new Crown(new(0, 5.8f, 0), new(1.5f, 4.3f, 1.5f)) }, bark, lp, true),
            TreePrefab("Bush", 0f, 0.1f, new[] { new Crown(new(0, 0.9f, 0), new(1.3f, 0.95f, 1.3f)), new Crown(new(0.6f, 1.1f, 0.3f), new(0.8f, 0.7f, 0.8f)) }, bark, lh, false),
        };
    }

    // ------------------------------------------------------------------ terrain
    static Terrain[] BuildTerrain(GameObject[] protos)
    {
        var raw = File.ReadAllBytes(Path.Combine(Imp, "terrain_height.f32"));
        var H = new float[2049 * 2049];
        System.Buffer.BlockCopy(raw, 0, H, 0, raw.Length);
        // trees
        var tb = File.ReadAllBytes(Path.Combine(Imp, "trees.bin"));
        int ntree = System.BitConverter.ToInt32(tb, 4);
        var perTile = new List<TreeInstance>[4];
        for (int i = 0; i < 4; i++) perTile[i] = new List<TreeInstance>();
        float[] protoH = { 10f, 10f, 10f, 10f, 2f };
        var rnd = new System.Random(44101);
        for (int i = 0; i < ntree; i++)
        {
            int o = 8 + i * 20;
            float x = System.BitConverter.ToSingle(tb, o), y = System.BitConverter.ToSingle(tb, o + 4), z = System.BitConverter.ToSingle(tb, o + 8), h = System.BitConverter.ToSingle(tb, o + 12);
            int proto = System.BitConverter.ToInt32(tb, o + 16);
            int c = Mathf.Clamp((int)(x / TileSize), 0, 1), r = Mathf.Clamp((int)(z / TileSize), 0, 1);
            float hs = Mathf.Clamp(h / protoH[proto], 0.25f, 4.5f);
            perTile[r * 2 + c].Add(new TreeInstance
            {
                position = new Vector3((x - c * TileSize) / TileSize, y / HScale, (z - r * TileSize) / TileSize),
                prototypeIndex = proto, heightScale = hs, widthScale = hs * (0.85f + 0.3f * (float)rnd.NextDouble()),
                rotation = (float)(rnd.NextDouble() * Mathf.PI * 2), color = Color.white, lightmapColor = Color.white
            });
        }
        var terrainMat = new Material(Shader.Find("Universal Render Pipeline/Terrain/Lit"));
        AssetDatabase.DeleteAsset(Gen + "/Terrain/TerrainLit.mat");
        AssetDatabase.CreateAsset(terrainMat, Gen + "/Terrain/TerrainLit.mat");
        var terrains = new Terrain[4];
        var parent = new GameObject("Terrain (IGN RGE ALTI + BD ORTHO)");
        for (int r = 0; r < 2; r++)
            for (int c = 0; c < 2; c++)
            {
                var td = new TerrainData();
                td.heightmapResolution = Res;
                td.size = new Vector3(TileSize, HScale, TileSize);
                var h = new float[Res, Res];
                for (int y = 0; y < Res; y++)
                    for (int x = 0; x < Res; x++)
                        h[y, x] = H[(r * 1024 + y) * 2049 + c * 1024 + x] / HScale;
                td.SetHeights(0, 0, h);
                var layer = new TerrainLayer
                {
                    diffuseTexture = AssetDatabase.LoadAssetAtPath<Texture2D>($"{Gen}/Terrain/ortho_t{r}{c}.png"),
                    tileSize = new Vector2(TileSize, TileSize), tileOffset = Vector2.zero, smoothness = 0.0f, metallic = 0,
                    maskMapTexture = AssetDatabase.LoadAssetAtPath<Texture2D>($"{Gen}/Terrain/terrain_mask.png"),
                    maskMapRemapMin = Vector4.zero, maskMapRemapMax = new Vector4(0, 1, 0, 0),
                    diffuseRemapMin = Vector4.zero, diffuseRemapMax = new Vector4(1, 1, 1, 0)
                };
                string lp = $"{Gen}/Terrain/ortho_t{r}{c}.terrainlayer";
                AssetDatabase.DeleteAsset(lp); AssetDatabase.CreateAsset(layer, lp);
                td.alphamapResolution = 16;
                td.terrainLayers = new[] { layer };
                var am = new float[16, 16, 1];
                for (int i = 0; i < 16; i++) for (int j = 0; j < 16; j++) am[i, j, 0] = 1;
                td.SetAlphamaps(0, 0, am);
                td.treePrototypes = protos.Select(p => new TreePrototype { prefab = p }).ToArray();
                string gp = Path.Combine(Imp, $"grass_t{r}{c}.u8");
                if (File.Exists(gp))
                {
                    td.SetDetailResolution(2048, 64);
                    td.detailPrototypes = new[] { new DetailPrototype {
                        prototypeTexture = T("grass_blades"), renderMode = DetailRenderMode.GrassBillboard, usePrototypeMesh = false,
                        minWidth = 0.35f, maxWidth = 0.65f, minHeight = 0.18f, maxHeight = 0.42f, noiseSpread = 0.3f,
                        healthyColor = new Color(0.62f, 0.72f, 0.42f), dryColor = new Color(0.72f, 0.70f, 0.45f), useDensityScaling = true } };
                    var gb = File.ReadAllBytes(gp); var dl = new int[2048, 2048];
                    for (int y = 0; y < 2048; y++) for (int x = 0; x < 2048; x++) dl[y, x] = gb[y * 2048 + x];
                    td.SetDetailLayer(0, 0, 0, dl);
                }
                td.SetTreeInstances(perTile[r * 2 + c].ToArray(), true);
                string tp = $"{Gen}/Terrain/TerrainData_{r}{c}.asset";
                AssetDatabase.DeleteAsset(tp); AssetDatabase.CreateAsset(td, tp);
                var go = Terrain.CreateTerrainGameObject(td);
                go.name = $"Terrain_{r}{c}";
                go.transform.SetParent(parent.transform, false);
                go.transform.position = new Vector3(c * TileSize, 0, r * TileSize);
                var t = go.GetComponent<Terrain>();
                t.materialTemplate = terrainMat;
                t.heightmapPixelError = 2f;
                t.basemapDistance = 20000f;
                t.treeDistance = 4500f;
                t.treeBillboardDistance = 4500f;
                t.treeMaximumFullLODCount = 5000;
                t.detailObjectDistance = 70f; t.detailObjectDensity = 1f;
                t.drawInstanced = true;
                t.shadowCastingMode = ShadowCastingMode.TwoSided;
                t.allowAutoConnect = true;
                terrains[r * 2 + c] = t;
            }
        terrains[0].SetNeighbors(null, terrains[2], terrains[1], null);
        terrains[1].SetNeighbors(terrains[0], terrains[3], null, null);
        terrains[2].SetNeighbors(null, null, terrains[3], terrains[0]);
        terrains[3].SetNeighbors(terrains[2], null, null, terrains[1]);
        Debug.Log($"[LAMONTAGNE] terrain 4 tiles, trees {ntree}");
        return terrains;
    }

    // ------------------------------------------------------------------ lighting / post
    static void SetupLighting()
    {
        var sunGo = new GameObject("Sun");
        var sun = sunGo.AddComponent<Light>();
        sun.type = LightType.Directional; sun.intensity = 1.9f; sun.color = new Color(1f, 0.955f, 0.88f);
        sun.shadows = LightShadows.Soft; sun.shadowStrength = 0.92f;
        // sun at La Montagne (47.19 N, 1.68 W) on 19 April, 15:00 CEST: elevation 52.3°, azimuth 202°
        sunGo.transform.rotation = Quaternion.Euler(52.3f, 202f + 180f, 0f);
        RenderSettings.sun = sun;
        var skyPath = Gen + "/Materials/Sky.mat";
        var sky = AssetDatabase.LoadAssetAtPath<Material>(skyPath);
        if (!sky) { sky = new Material(Shader.Find("Skybox/Procedural")); AssetDatabase.CreateAsset(sky, skyPath); }
        sky.SetFloat("_SunSize", 0.035f); sky.SetFloat("_SunSizeConvergence", 6f);
        sky.SetFloat("_AtmosphereThickness", 1.05f); sky.SetColor("_SkyTint", new Color(0.56f, 0.62f, 0.72f));
        sky.SetColor("_GroundColor", new Color(0.42f, 0.42f, 0.40f)); sky.SetFloat("_Exposure", 1.35f);
        RenderSettings.skybox = sky;
        RenderSettings.ambientMode = AmbientMode.Trilight;
        RenderSettings.ambientSkyColor = new Color(0.60f, 0.68f, 0.82f);
        RenderSettings.ambientEquatorColor = new Color(0.55f, 0.56f, 0.54f);
        RenderSettings.ambientGroundColor = new Color(0.30f, 0.29f, 0.26f);
        RenderSettings.fog = true; RenderSettings.fogMode = FogMode.Exponential;
        RenderSettings.fogDensity = 0.00045f; RenderSettings.fogColor = new Color(0.74f, 0.80f, 0.88f);

        // sky reflection cubemap rendered once in the editor from the procedural sky (no baked lighting in batch builds),
        // so water and glass reflect the sky
        try
        {
            var camGo = new GameObject("SkyCubeCam"); var c = camGo.AddComponent<Camera>();
            c.cullingMask = 0; c.clearFlags = CameraClearFlags.Skybox; camGo.transform.position = new Vector3(2048, 50, 2048);
            var cube = new Cubemap(128, TextureFormat.RGBA32, false);
            bool ok = c.RenderToCubemap(cube);
            Object.DestroyImmediate(camGo);
            string cp = Gen + "/SkyReflection.cubemap";
            AssetDatabase.DeleteAsset(cp); AssetDatabase.CreateAsset(cube, cp);
            RenderSettings.defaultReflectionMode = DefaultReflectionMode.Custom;
            RenderSettings.customReflectionTexture = cube; RenderSettings.reflectionIntensity = 1f;
            var px = cube.GetPixel(CubemapFace.PositiveZ, 64, 64);
            Debug.Log($"[LAMONTAGNE] sky reflection cubemap ok={ok} sample={px}");
        }
        catch (System.Exception e) { Debug.LogWarning("[LAMONTAGNE] sky cubemap failed: " + e.Message); }

        var volGo = new GameObject("PostProcess");
        var vol = volGo.AddComponent<Volume>(); vol.isGlobal = true; vol.priority = 1;
        string pp = Gen + "/PostProfile.asset";
        AssetDatabase.DeleteAsset(pp);
        var prof = ScriptableObject.CreateInstance<VolumeProfile>();
        AssetDatabase.CreateAsset(prof, pp);
        var tm = prof.Add<Tonemapping>(true); tm.mode.value = TonemappingMode.ACES;
        var bl = prof.Add<Bloom>(true); bl.intensity.value = 0.18f; bl.threshold.value = 1.05f; bl.scatter.value = 0.6f;
        var ca = prof.Add<ColorAdjustments>(true); ca.postExposure.value = 0.0f; ca.contrast.value = 10f; ca.saturation.value = 8f;
        ca.colorFilter.value = Color.white; ca.hueShift.value = 0;
        var vg = prof.Add<Vignette>(true); vg.intensity.value = 0.18f; vg.smoothness.value = 0.4f; vg.color.value = Color.black; vg.center.value = new Vector2(0.5f, 0.5f);
        foreach (var c in prof.components) AssetDatabase.AddObjectToAsset(c, prof);
        vol.sharedProfile = prof;
        EditorUtility.SetDirty(prof);
    }

    static void BuildBounds()
    {
        var p = new GameObject("WorldBounds");
        void Wall(Vector3 c, Vector3 s) { var g = new GameObject("Bound"); g.transform.SetParent(p.transform); g.transform.position = c; g.AddComponent<BoxCollider>().size = s; }
        Wall(new Vector3(2048, 200, -5), new Vector3(4200, 600, 10));
        Wall(new Vector3(2048, 200, 4101), new Vector3(4200, 600, 10));
        Wall(new Vector3(-5, 200, 2048), new Vector3(10, 600, 4200));
        Wall(new Vector3(4101, 200, 2048), new Vector3(10, 600, 4200));
    }

    static void BuildPlayer(CityMeta meta)
    {
        var player = new GameObject("Player");
        player.transform.position = new Vector3(meta.spawn.x, meta.spawn.y - 0.6f, meta.spawn.z);
        player.transform.rotation = Quaternion.Euler(0, meta.spawn.yaw, 0);
        var cc = player.AddComponent<CharacterController>();
        cc.height = 1.8f; cc.radius = 0.35f; cc.center = new Vector3(0, 0.9f, 0); cc.stepOffset = 0.45f; cc.slopeLimit = 50f; cc.skinWidth = 0.04f;
        var pivot = new GameObject("CameraPivot"); pivot.transform.SetParent(player.transform, false); pivot.transform.localPosition = new Vector3(0, 1.68f, 0);
        var camGo = new GameObject("PlayerCamera"); camGo.tag = "MainCamera"; camGo.transform.SetParent(pivot.transform, false);
        var cam = camGo.AddComponent<Camera>(); cam.nearClipPlane = 0.03f; cam.farClipPlane = 9000f; cam.fieldOfView = 75f;
        camGo.AddComponent<AudioListener>();
        var acd = camGo.AddComponent<UniversalAdditionalCameraData>();
        acd.renderPostProcessing = true; acd.antialiasing = AntialiasingMode.SubpixelMorphologicalAntiAliasing; acd.antialiasingQuality = AntialiasingQuality.High;
        acd.renderShadows = true;
        var fps = player.AddComponent<FPSController>(); fps.cameraPivot = pivot.transform;
        var gun = player.AddComponent<HitscanGun>(); gun.controller = fps; gun.cam = cam;
        gun.metalMat = Mat("GunMetal", null, null, new Color(0.10f, 0.10f, 0.11f), 0.55f, 0.85f);
        gun.polymerMat = Mat("GunPolymer", null, null, new Color(0.16f, 0.15f, 0.13f), 0.25f);
        gun.flashMat = Transparent("MuzzleFlash", "muzzleflash", new Color(1f, 0.85f, 0.6f, 1f), true);
        gun.decalMat = Transparent("BulletHole", "bullethole", Color.white, false);
        gun.dustMat = Transparent("ImpactDust", null, new Color(0.7f, 0.66f, 0.6f, 0.8f), false);
        var hud = player.AddComponent<FPSHud>(); hud.cam = cam; hud.gun = gun; hud.controller = fps;
        player.AddComponent<PerfLogger>();
        var ac = player.AddComponent<AutoCapture>(); ac.player = fps; ac.cam = cam; ac.gun = gun; ac.hud = hud;
    }

    static void ConfigureUrp()
    {
        foreach (var guid in AssetDatabase.FindAssets("t:UniversalRenderPipelineAsset"))
        {
            var a = AssetDatabase.LoadAssetAtPath<UniversalRenderPipelineAsset>(AssetDatabase.GUIDToAssetPath(guid));
            if (a == null || !a.name.StartsWith("PC")) continue;
            a.shadowDistance = 220f; a.shadowCascadeCount = 4; a.msaaSampleCount = 4;
            EditorUtility.SetDirty(a);
            Debug.Log("[LAMONTAGNE] URP asset configured: " + a.name);
        }
    }
}
