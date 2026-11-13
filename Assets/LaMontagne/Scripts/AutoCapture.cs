using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEngine;

/// Automation for verification (inactive in normal play):
///   -lmCapture [-lmOut dir]  : editor play mode, renders 1920x1080 screenshots + render benchmark, then exits.
///   -lmPerf    [-lmOut dir]  : standalone player, scripted walk/turn route for 30 s, logs real FPS, then quits.
public class AutoCapture : MonoBehaviour
{
    public static Action<int> OnFinished;
    public FPSController player;
    public Camera cam;
    public HitscanGun gun;
    public FPSHud hud;
    string outDir = "/workspace/lamontagne";
    bool capture, perf;
    StreamWriter log;
    string shotsFile;
    [Serializable] public class ShotSpec { public string name; public float x, z, yaw, pitch, y = -9999, fov = 75; public bool hud; public bool free; public int w = 1920, h = 1080; }
    [Serializable] class ShotList { public ShotSpec[] shots; }

    void Awake()
    {
        var a = Environment.GetCommandLineArgs();
        capture = a.Contains("-lmCapture"); perf = a.Contains("-lmPerf");
        int i = Array.IndexOf(a, "-lmOut"); if (i >= 0 && i + 1 < a.Length) outDir = a[i + 1];
        if (a.Contains("-lmBalade")) { var w = gameObject.AddComponent<BaladeWalker>(); w.player = player; w.cam = cam; w.gun = gun; w.hud = hud; w.outDir = outDir; enabled = false; return; }
        int si = Array.IndexOf(a, "-lmShots"); if (si >= 0 && si + 1 < a.Length) shotsFile = a[si + 1];
        if (!capture && !perf && shotsFile == null) { enabled = false; return; }
        Directory.CreateDirectory(outDir);
    }

    void Log(string s) { Debug.Log("[CAPTURE] " + s); log?.WriteLine(s); log?.Flush(); }

    IEnumerator Start()
    {
        if (!capture && !perf && shotsFile == null) yield break;
        log = new StreamWriter(Path.Combine(outDir, shotsFile != null ? "shots.log" : perf ? "perf_player.log" : "capture_playmode.log"), false);
        player.inputEnabled = false;
        while (!CityLoader.Loaded) yield return null;
        Log($"city loaded in {CityLoader.LoadSeconds:F2}s, city triangles {CityLoader.Triangles}, GPU {SystemInfo.graphicsDeviceName} ({SystemInfo.graphicsDeviceType}), CPU {SystemInfo.processorType} x{SystemInfo.processorCount}");
        for (int k = 0; k < 10; k++) yield return null;
        var meta = CityMeta.Load();
        if (shotsFile != null) yield return ShotList_(shotsFile);
        else if (perf) yield return PerfRoute(meta);
        else yield return Captures(meta);
        log.Close();
        if (OnFinished != null) OnFinished(0); else Application.Quit(0);
    }

    // ---------------------------------------------------------------- helpers
    Vector3 Ground(Vector3 p)
    {
        if (Physics.Raycast(new Vector3(p.x, 400, p.z), Vector3.down, out var h, 800, ~0, QueryTriggerInteraction.Ignore)) return h.point;
        return new Vector3(p.x, 30, p.z);
    }

    /// street-level spot (on a road/sidewalk mesh) between minD and maxD metres from target, facing it
    bool StreetSpot(Vector3 target, float minD, float maxD, out Vector3 feet, out float yaw)
    {
        feet = default; yaw = 0; float best = 1e9f;
        for (float r = minD; r <= maxD; r += 3)
            for (int k = 0; k < 36; k++)
            {
                float a = k * 10 * Mathf.Deg2Rad;
                var p = new Vector3(target.x + Mathf.Sin(a) * r, 0, target.z + Mathf.Cos(a) * r);
                if (!Physics.Raycast(new Vector3(p.x, 400, p.z), Vector3.down, out var h, 800, ~0, QueryTriggerInteraction.Ignore)) continue;
                if (!h.collider.name.StartsWith("road_")) continue;
                Vector3 eye = h.point + Vector3.up * 1.7f;
                Vector3 to = new Vector3(target.x, eye.y + 2, target.z) - eye;
                // need a clear line of sight to the target
                if (Physics.Raycast(eye, to.normalized, out var los, to.magnitude, ~0, QueryTriggerInteraction.Ignore) && los.distance < to.magnitude - 12) continue;
                float score = Mathf.Abs(r - (minD + maxD) / 2);
                if (score < best) { best = score; feet = h.point; yaw = Mathf.Atan2(target.x - p.x, target.z - p.z) * Mathf.Rad2Deg; }
            }
        return best < 1e8f;
    }

    void SetHudGun(bool on)
    {
        var g = cam.transform.Find("Gun"); if (g) g.gameObject.SetActive(on);
        var h = GameObject.Find("HUD"); if (h) h.GetComponent<Canvas>().enabled = on;
    }

    Texture2D tex; RenderTexture rt;
    void Shot(string name)
    {
        if (rt == null)
        {
            rt = new RenderTexture(1920, 1080, 24, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB) { antiAliasing = 4 };
            tex = new Texture2D(1920, 1080, TextureFormat.RGB24, false);
        }
        Canvas.ForceUpdateCanvases();
        var prev = cam.targetTexture;
        cam.targetTexture = rt; cam.Render(); cam.targetTexture = prev;
        RenderTexture.active = rt; tex.ReadPixels(new Rect(0, 0, 1920, 1080), 0, 0); tex.Apply(); RenderTexture.active = null;
        string p = Path.Combine(outDir, name + ".png");
        File.WriteAllBytes(p, tex.EncodeToPNG());
        Log($"shot {name} pos={cam.transform.position} rot={cam.transform.eulerAngles} -> {p}");
    }

    IEnumerator Frames(int n) { for (int i = 0; i < n; i++) yield return null; }

    void PlaceCamFree(Vector3 pos, Vector3 lookAt, float fov)
    {
        cam.transform.position = pos; cam.transform.rotation = Quaternion.LookRotation(lookAt - pos); cam.fieldOfView = fov;
    }

    IEnumerator ShotList_(string file)
    {
        var list = JsonUtility.FromJson<ShotList>(File.ReadAllText(file));
        float fog = RenderSettings.fogDensity;
        foreach (var s in list.shots)
        {
            SetHudGun(s.hud);
            if (s.free)
            {
                RenderSettings.fogDensity = fog * 0.35f;
                cam.transform.position = new Vector3(s.x, s.y, s.z);
                cam.transform.rotation = Quaternion.Euler(s.pitch, s.yaw, 0);
            }
            else
            {
                RenderSettings.fogDensity = fog;
                cam.transform.localPosition = Vector3.zero; cam.transform.localRotation = Quaternion.identity;
                var g = s.y > -9000 ? new Vector3(s.x, s.y, s.z) : Ground(new Vector3(s.x, 0, s.z));
                player.Teleport(g, s.yaw, s.pitch);
            }
            cam.fieldOfView = s.fov;
            yield return Frames(3);
            if (rt == null || rt.width != s.w || rt.height != s.h)
            {
                rt = new RenderTexture(s.w, s.h, 24, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB) { antiAliasing = 4 };
                tex = new Texture2D(s.w, s.h, TextureFormat.RGB24, false);
            }
            Canvas.ForceUpdateCanvases();
            var prev = cam.targetTexture; cam.targetTexture = rt; cam.Render(); cam.targetTexture = prev;
            RenderTexture.active = rt; tex.ReadPixels(new Rect(0, 0, s.w, s.h), 0, 0); tex.Apply(); RenderTexture.active = null;
            File.WriteAllBytes(Path.Combine(outDir, s.name + ".png"), tex.EncodeToPNG());
            Log($"shot {s.name} cam={cam.transform.position} rot={cam.transform.eulerAngles} t={Time.realtimeSinceStartup:F0}s");
        }
        RenderSettings.fogDensity = fog;
    }

    // ---------------------------------------------------------------- render benchmark (sync each frame)
    IEnumerator Bench(string label, int frames)
    {
        var small = new Texture2D(4, 4, TextureFormat.RGB24, false);
        var times = new List<float>();
        float yaw0 = player.Yaw;
        for (int i = 0; i < frames; i++)
        {
            player.Teleport(player.transform.position, yaw0 + i * (360f / frames), 0);
            yield return null;
            float t0 = Time.realtimeSinceStartup;
            var prev = cam.targetTexture; cam.targetTexture = rt; cam.Render(); cam.targetTexture = prev;
            RenderTexture.active = rt; small.ReadPixels(new Rect(0, 0, 4, 4), 0, 0); small.Apply(); RenderTexture.active = null;
            times.Add(Time.realtimeSinceStartup - t0);
        }
        times.Sort();
        float avg = times.Average();
        Log($"[BENCH] {label}: 1920x1080 MSAA4 render avg {avg * 1000:F1} ms ({1 / avg:F0} fps), median {times[times.Count / 2] * 1000:F1} ms, worst {times[times.Count - 1] * 1000:F1} ms over {frames} frames (360° turn)");
    }

    // ---------------------------------------------------------------- editor captures
    IEnumerator Captures(CityMeta meta)
    {
        Vector3 spawn = new Vector3(meta.spawn.x, meta.spawn.y, meta.spawn.z);
        var church = meta.churches != null && meta.churches.Length > 0 ? meta.churches.OrderBy(c => (new Vector2(c.x, c.z) - new Vector2(spawn.x, spawn.z)).sqrMagnitude).First() : null;
        Vector3 churchPos = church != null ? new Vector3(church.x, church.ground, church.z) : spawn;
        float fog = RenderSettings.fogDensity;
        Shot("_warmup");

        // 1. aerial overview of the commune (from the south, Loire in the background)
        SetHudGun(false);
        RenderSettings.fogDensity = fog * 0.35f;
        PlaceCamFree(new Vector3(1350, 820, -420), new Vector3(2150, 0, 2350), 55);
        yield return Frames(3); Shot("01_aerial_overview");
        // 2. low aerial over the bourg
        PlaceCamFree(churchPos + new Vector3(-170, 140, -210), churchPos + new Vector3(0, 10, 0), 55);
        yield return Frames(3); Shot("02_aerial_bourg");
        RenderSettings.fogDensity = fog;
        cam.transform.localPosition = new Vector3(0, 1.68f, 0); cam.transform.localRotation = Quaternion.identity; cam.fieldOfView = 75;
        SetHudGun(true);

        // 3. town centre, street level, facing the church
        player.Teleport(Ground(spawn), meta.spawn.yaw, -8f);
        cam.transform.localPosition = Vector3.zero; cam.transform.localRotation = Quaternion.identity;
        yield return Frames(5); Shot("03_bourg_street_church");
        yield return Bench("bourg street", 6);

        // 4. Mairie
        var mairie = meta.poi.FirstOrDefault(p => p.name.Contains("Mairie de La Montagne"));
        if (mairie != null && StreetSpot(new Vector3(mairie.x, 0, mairie.z), 22, 55, out var mf, out var my))
        {
            player.Teleport(mf, my, -4f); yield return Frames(5); Shot("04_mairie_street");
        }
        // 5. towards the Loire from the coteau
        var lv = meta.views?.FirstOrDefault(v => v.name == "loire");
        if (lv != null)
        {
            player.Teleport(Ground(new Vector3(lv.x, lv.y, lv.z)), lv.yaw, lv.pitch); yield return Frames(5); Shot("05_view_loire");
            yield return Bench("Loire bank view", 6);
        }
        // 6. gun + HUD: fire at the church wall
        player.Teleport(Ground(spawn), meta.spawn.yaw, -2f);
        yield return Frames(3);
        gun.externalTrigger = true;
        float t0 = Time.realtimeSinceStartup;
        int target = gun.ShotsFired + 7;
        while (gun.ShotsFired < target && Time.realtimeSinceStartup - t0 < 10) yield return null;
        gun.externalTrigger = false;
        yield return Frames(2);
        Log($"gun: shots={gun.ShotsFired} lastHit={gun.LastHitName} dist={gun.LastHitDistance:F1}m ammo={gun.Ammo}/{gun.Reserve}");
        gun.Fire();
        Shot("06_gun_hud_firing");
        // aerial render benchmark
        SetHudGun(false);
        PlaceCamFree(new Vector3(1350, 820, -420), new Vector3(2150, 0, 2350), 55);
        RenderSettings.fogDensity = fog * 0.35f;
        var times = new List<float>(); var small = new Texture2D(4, 4, TextureFormat.RGB24, false);
        for (int i = 0; i < 4; i++)
        {
            yield return null; float s = Time.realtimeSinceStartup;
            var prev = cam.targetTexture; cam.targetTexture = rt; cam.Render(); cam.targetTexture = prev;
            RenderTexture.active = rt; small.ReadPixels(new Rect(0, 0, 4, 4), 0, 0); small.Apply(); RenderTexture.active = null;
            times.Add(Time.realtimeSinceStartup - s);
        }
        Log($"[BENCH] aerial overview: avg {times.Average() * 1000:F1} ms ({1 / times.Average():F0} fps)");
        RenderSettings.fogDensity = fog;
    }

    // ---------------------------------------------------------------- standalone player perf route
    IEnumerator PerfRoute(CityMeta meta)
    {
        var spots = new List<(Vector3, float)> { (new Vector3(meta.spawn.x, meta.spawn.y, meta.spawn.z), meta.spawn.yaw) };
        if (meta.views != null) foreach (var v in meta.views) spots.Add((new Vector3(v.x, v.y, v.z), v.yaw));
        var all = new List<float>();
        foreach (var (p, yaw) in spots)
        {
            player.Teleport(Ground(p), yaw, 0);
            yield return Frames(30);
            var dts = new List<float>();
            float t = 0;
            while (t < 12f)
            {
                float dt = Time.unscaledDeltaTime; t += dt; dts.Add(dt);
                player.Teleport(player.transform.position + player.transform.forward * dt * 1.5f, yaw + t * 30f, -2);
                yield return null;
            }
            dts.Sort(); all.AddRange(dts);
            Log($"[PLAYER] spot {p}: frames={dts.Count} avg {1 / dts.Average():F1} fps, 1% low {1 / dts[(int)(dts.Count * 0.99f)]:F1} fps, worst {dts[dts.Count - 1] * 1000:F1} ms");
        }
        all.Sort();
        Log($"[PLAYER] overall {Screen.width}x{Screen.height}: avg {1 / all.Average():F1} fps, 1% low {1 / all[(int)(all.Count * 0.99f)]:F1} fps");
    }
}
