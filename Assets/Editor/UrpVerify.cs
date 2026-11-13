using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

public static class UrpVerify
{
    public static void Run()
    {
        var rp = GraphicsSettings.defaultRenderPipeline;
        Debug.Log("VERIFY defaultRenderPipeline=" + (rp == null ? "NULL" : rp.GetType().FullName + " asset=" + rp.name));
        if (rp == null || rp.GetType().Name != "UniversalRenderPipelineAsset")
            throw new System.Exception("GraphicsSettings.defaultRenderPipeline is not a UniversalRenderPipelineAsset: " + (rp == null ? "null" : rp.GetType().FullName));

        string urpVer = "(not found)";
        foreach (var p in UnityEditor.PackageManager.PackageInfo.GetAllRegisteredPackages())
        {
            if (p.name == "com.unity.render-pipelines.universal")
            {
                urpVer = p.version;
                Debug.Log("VERIFY URP version=" + p.version + " source=" + p.source + " resolved=" + p.resolvedPath);
            }
        }

        for (int i = 0; i < QualitySettings.names.Length; i++)
        {
            var qrp = QualitySettings.GetRenderPipelineAssetAt(i);
            Debug.Log("VERIFY quality[" + i + "]=" + QualitySettings.names[i] + " rp=" + (qrp == null ? "null" : qrp.name + " " + qrp.GetType().Name));
        }

        EditorSceneManager.OpenScene("Assets/Scenes/SampleScene.unity");
        var cam = Camera.main;
        if (cam == null) cam = Object.FindFirstObjectByType<Camera>();
        if (cam == null) throw new System.Exception("No camera in SampleScene");
        var extra = cam.GetComponent<UniversalAdditionalCameraData>();
        if (extra != null) extra.renderPostProcessing = false;

        var lit = Shader.Find("Universal Render Pipeline/Lit");
        if (lit == null) lit = Shader.Find("Universal Render Pipeline/Simple Lit");
        if (lit == null) throw new System.Exception("URP Lit shader not found");
        Debug.Log("VERIFY litShader=" + lit.name);

        var groundMat = new Material(lit);
        var cubeMat = new Material(lit);
        if (groundMat.HasProperty("_BaseColor")) groundMat.SetColor("_BaseColor", new Color(0.55f, 0.56f, 0.58f, 1f));
        if (cubeMat.HasProperty("_BaseColor")) cubeMat.SetColor("_BaseColor", new Color(0.15f, 0.55f, 1f, 1f));
        if (cubeMat.HasProperty("_Metallic")) cubeMat.SetFloat("_Metallic", 0f);
        if (cubeMat.HasProperty("_Smoothness")) cubeMat.SetFloat("_Smoothness", 0.25f);
        if (groundMat.HasProperty("_Smoothness")) groundMat.SetFloat("_Smoothness", 0.2f);
        groundMat.color = new Color(0.55f, 0.56f, 0.58f, 1f);
        cubeMat.color = new Color(0.15f, 0.55f, 1f, 1f);

        var camGo = cam.transform;
        camGo.position = new Vector3(3.4f, 2.4f, -5.2f);
        camGo.LookAt(new Vector3(0f, 0.45f, 0f));

        var ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
        ground.name = "VerifyGround";
        ground.transform.position = Vector3.zero;
        ground.transform.localScale = new Vector3(2.2f, 1f, 2.2f);
        ground.GetComponent<Renderer>().sharedMaterial = groundMat;

        var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
        cube.name = "VerifyCube";
        cube.transform.position = new Vector3(0f, 0.5f, 0f);
        cube.GetComponent<Renderer>().sharedMaterial = cubeMat;

        const int w = 1280, h = 720;
        var rt = new RenderTexture(w, h, 24, RenderTextureFormat.ARGB32);
        rt.Create();
        cam.targetTexture = rt;
        cam.Render();

        var prev = RenderTexture.active;
        RenderTexture.active = rt;
        var tex = new Texture2D(w, h, TextureFormat.RGB24, false);
        tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
        tex.Apply();
        RenderTexture.active = prev;
        cam.targetTexture = null;

        var pixels = tex.GetPixels32();
        int pink = 0, black = 0, other = 0, sampled = 0;
        for (int i = 0; i < pixels.Length; i += 4)
        {
            sampled++;
            var c = pixels[i];
            if (c.r > 180 && c.g < 90 && c.b > 180) pink++;
            else if (c.r < 10 && c.g < 10 && c.b < 10) black++;
            else other++;
        }
        Debug.Log("VERIFY pixels sampled=" + sampled + " pink=" + pink + " black=" + black + " other=" + other);

        Directory.CreateDirectory("/workspace/lamontagne");
        var png = tex.EncodeToPNG();
        File.WriteAllBytes("/workspace/lamontagne/urp_check.png", png);
        Debug.Log("VERIFY wrote /workspace/lamontagne/urp_check.png bytes=" + png.Length);

        File.WriteAllText("/tmp/lamontagne-verify-meta.txt",
            "urp_version=" + urpVer + "\n" +
            "pipeline_asset=" + rp.name + "\n" +
            "pipeline_type=" + rp.GetType().FullName + "\n" +
            "lit_shader=" + lit.name + "\n" +
            "pink=" + pink + "\nblack=" + black + "\nother=" + other + "\n" +
            "png_bytes=" + png.Length + "\n");

        Object.DestroyImmediate(rt);
        Object.DestroyImmediate(tex);

        if (pink > sampled * 0.12f)
            throw new System.Exception("Render looks magenta/pink; URP shaders likely missing");
        if (other < sampled * 0.05f)
            throw new System.Exception("Render is almost entirely black; capture likely failed");

        Debug.Log("VERIFY OK");
    }
}
