using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

/// Runtime settings for the browser (WebGL) build, stricter on phones: draw distances, tree/terrain LOD,
/// fog to hide the shorter view distance, no bloom on mobile, and installs the touch controls.
public static class WebTuning
{
    public static bool IsWeb => Application.platform == RuntimePlatform.WebGLPlayer;
    public static bool IsMobile => Application.isMobilePlatform;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Apply()
    {
        if (!IsWeb && !IsMobile) return;
        bool mob = IsMobile;
        var fps = Object.FindAnyObjectByType<FPSController>();
        var gun = Object.FindAnyObjectByType<HitscanGun>();
        if (fps) TouchControls.Install(fps, gun);
        foreach (var cam in Object.FindObjectsByType<Camera>(FindObjectsSortMode.None))
        {
            cam.farClipPlane = mob ? 1600f : 2600f;
            var acd = cam.GetComponent<UniversalAdditionalCameraData>();
            if (acd) { acd.antialiasing = mob ? AntialiasingMode.None : AntialiasingMode.FastApproximateAntialiasing; }
        }
        RenderSettings.fog = true; RenderSettings.fogMode = FogMode.Exponential;
        RenderSettings.fogDensity = mob ? 0.0016f : 0.0009f;
        foreach (var t in Terrain.activeTerrains)
        {
            t.treeDistance = mob ? 450f : 900f;
            t.treeBillboardDistance = mob ? 50f : 120f;
            t.treeMaximumFullLODCount = mob ? 80 : 250;
            t.heightmapPixelError = mob ? 10f : 5f;
            t.basemapDistance = 400f;
            t.detailObjectDistance = 0f;
            t.shadowCastingMode = ShadowCastingMode.Off;
        }
        QualitySettings.lodBias = mob ? 0.6f : 1f;
        if (GraphicsSettings.currentRenderPipeline is UniversalRenderPipelineAsset urp && !mob)
        { urp.renderScale = 1f; urp.shadowDistance = 90f; }
        if (mob)
        {
            foreach (var v in Object.FindObjectsByType<Volume>(FindObjectsSortMode.None))
                if (v.sharedProfile && v.sharedProfile.TryGet<Bloom>(out var bl)) bl.active = false;
        }
        Debug.Log($"[WEB] tuning applied (mobile={mob})");
    }
}
