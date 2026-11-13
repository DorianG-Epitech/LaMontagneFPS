using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEngine;

/// Bot that drives the FPSController along a street route (StreamingAssets/LaMontagne/balade_route.json)
/// with natural head movement, a short sprint, a jump and a few shots at a wall, while recording frames.
/// Args: -lmBalade -lmOut dir [-lmW 1280 -lmH 720 -lmSeconds 40]
public class BaladeWalker : MonoBehaviour
{
    public FPSController player; public Camera cam; public HitscanGun gun; public FPSHud hud;
    public string outDir = "/workspace/lamontagne";
    [Serializable] class P { public float x, z; }
    [Serializable] class Route { public P[] points; }
    List<Vector2> pts;
    int seg; int W = 1280, H = 720; float seconds = 40;
    RenderTexture rt; Texture2D tex; FileStream raw; StreamWriter log;
    int frame, totalFrames;
    UnityEngine.Rendering.Universal.UniversalRenderPipelineAsset urp; float oSd; int oSc, oMs;
    float t, lookOff, lookOffVel, lookTarget, glanceT, pitch, pitchVel, pitchTarget, yawVel, stuckT;
    Vector3 lastPos;
    enum Mode { Walk, Shoot }
    Mode mode; float modeT; bool shotDone, jumped;
    System.Random rnd = new System.Random(44620);
    float R(float a, float b) => a + (float)rnd.NextDouble() * (b - a);

    void Start()
    {
        var a = Environment.GetCommandLineArgs();
        string Arg(string k, string d) { int i = Array.IndexOf(a, k); return i >= 0 && i + 1 < a.Length ? a[i + 1] : d; }
        W = int.Parse(Arg("-lmW", "1280")); H = int.Parse(Arg("-lmH", "720")); seconds = float.Parse(Arg("-lmSeconds", "40"), System.Globalization.CultureInfo.InvariantCulture);
        totalFrames = Mathf.RoundToInt(seconds * 30);
        Time.captureFramerate = 30;
        var route = JsonUtility.FromJson<Route>(File.ReadAllText(Path.Combine(Application.streamingAssetsPath, "LaMontagne", "balade_route.json")));
        pts = route.points.Select(p => new Vector2(p.x, p.z)).ToList();
        Directory.CreateDirectory(outDir);
        log = new StreamWriter(Path.Combine(outDir, "balade_capture.log"), false);
        // lighter settings for the CPU renderer
        QualitySettings.lodBias = 0.7f;
        foreach (var tr in FindObjectsByType<Terrain>(FindObjectsSortMode.None)) { tr.treeDistance = 700; tr.heightmapPixelError = 4; }
        cam.farClipPlane = 3000;
        urp = UnityEngine.Rendering.GraphicsSettings.currentRenderPipeline as UnityEngine.Rendering.Universal.UniversalRenderPipelineAsset;
        if (urp) { oSd = urp.shadowDistance; oSc = urp.shadowCascadeCount; oMs = urp.msaaSampleCount; urp.shadowDistance = 90; urp.shadowCascadeCount = 2; }
        hud.showFps = false;
        rt = new RenderTexture(W, H, 24, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB) { antiAliasing = 4 };
        tex = new Texture2D(W, H, TextureFormat.RGB24, false);
        raw = new FileStream(Path.Combine(outDir, "balade_frames.rgb"), FileMode.Create);
        // start on the route, facing the first segment
        Vector3 s = Ground(pts[0]);
        Vector2 d = pts[2] - pts[0];
        player.Teleport(s, Mathf.Atan2(d.x, d.y) * Mathf.Rad2Deg - 70f, -2f);
        player.scripted = true; player.inputEnabled = false;
        lastPos = player.transform.position;
        Log($"balade start {W}x{H} {totalFrames} frames, route {pts.Count} pts");
    }

    void Log(string s) { Debug.Log("[BALADE] " + s); log.WriteLine(s); log.Flush(); }

    Vector3 Ground(Vector2 p)
    {
        if (Physics.Raycast(new Vector3(p.x, 300, p.y), Vector3.down, out var h, 600, ~0, QueryTriggerInteraction.Ignore)) return h.point + Vector3.up * 0.05f;
        return new Vector3(p.x, 40, p.y);
    }

    static float Damp(float cur, float target, ref float vel, float time, float dt) => Mathf.SmoothDamp(cur, target, ref vel, time, Mathf.Infinity, dt);

    void Update()
    {
        if (raw == null) return;
        if (frame >= totalFrames) { Finish(); return; }
        frame++;
        float dt = 1f / 30f; t += dt;
        Drive(dt);
    }

    void LateUpdate()
    {
        if (raw == null || frame == 0) return;
        Canvas.ForceUpdateCanvases();
        var prev = cam.targetTexture; cam.targetTexture = rt; cam.Render(); cam.targetTexture = prev;
        RenderTexture.active = rt; tex.ReadPixels(new Rect(0, 0, W, H), 0, 0); tex.Apply(false); RenderTexture.active = null;
        var b = tex.GetRawTextureData(); int stride = W * 3; for (int r = H - 1; r >= 0; r--) raw.Write(b, r * stride, stride);  // top-down rows (no vflip needed in ffmpeg)
        if (frame % 30 == 0) Log($"frame {frame}/{totalFrames} t={t:F1}s pos={player.transform.position} seg={seg}/{pts.Count} mode={mode} realtime={Time.realtimeSinceStartup:F0}s");
    }

    void Drive(float dt)
    {
        Vector3 pos = player.transform.position; Vector2 p2 = new Vector2(pos.x, pos.z);
        // advance along route (pure pursuit with 6 m look-ahead)
        while (seg < pts.Count - 2 && (pts[seg + 1] - p2).magnitude < 3.5f) seg++;
        int la = seg; float acc = 0;
        while (la < pts.Count - 1 && acc < 6f) { acc += (pts[la + 1] - pts[la]).magnitude; la++; }
        Vector2 dir = (pts[la] - p2); if (dir.sqrMagnitude < 0.01f) dir = Vector2.up;
        dir.Normalize();
        float pathYaw = Mathf.Atan2(dir.x, dir.y) * Mathf.Rad2Deg;

        // stuck handling: skip ahead on the route
        if ((pos - lastPos).magnitude < 0.02f && mode == Mode.Walk && t > 1.5f) stuckT += dt; else stuckT = 0;
        lastPos = pos;
        if (stuckT > 1.0f) { seg = Mathf.Min(seg + 2, pts.Count - 2); stuckT = 0; Log($"stuck at {pos}, skipping to seg {seg}"); }

        // behaviour timeline: sprint 13-17 s, jump ~19 s, stop & shoot a wall ~25-29 s
        bool sprint = t > 13f && t < 17f;
        if (!jumped && t > 19.2f) { player.scriptedJump = true; jumped = true; Log("jump"); }
        if (!shotDone && t > 25f && mode == Mode.Walk)
        {
            mode = Mode.Shoot; modeT = 0; shotDone = true;
            // look at the nearest wall to the left or right
            float best = 99; float side = 70;
            foreach (float s in new[] { -75f, 75f, -45f, 45f })
            {
                var d3 = Quaternion.Euler(0, pathYaw + s, 0) * Vector3.forward;
                if (Physics.Raycast(pos + Vector3.up * 1.6f, d3, out var h, 25f, ~0, QueryTriggerInteraction.Ignore) && h.collider.name.StartsWith("bld") && h.distance < best) { best = h.distance; side = s; }
            }
            lookTarget = side; pitchTarget = -3f;
            Log($"shoot mode: wall at {best:F1} m, offset {side}");
        }

        Vector2 move = Vector2.zero;
        if (mode == Mode.Walk)
        {
            // occasional glances at buildings / around
            glanceT -= dt;
            if (glanceT <= 0)
            {
                bool glance = rnd.NextDouble() < 0.6;
                lookTarget = glance ? R(35f, 65f) * (rnd.NextDouble() < 0.5 ? -1 : 1) : R(-8f, 8f);
                pitchTarget = glance ? R(-14f, -4f) : R(-2f, 4f);
                glanceT = glance ? R(1.6f, 2.8f) : R(2.5f, 4.5f);
                if (sprint) { lookTarget = R(-6, 6); pitchTarget = 2; }
            }
            move = Vector2.up;
        }
        else
        {
            modeT += dt;
            gun.externalTrigger = modeT > 0.9f && modeT < 2.2f && (int)(modeT * 10) % 4 != 3;
            if (modeT > 3.2f) { mode = Mode.Walk; gun.externalTrigger = false; glanceT = 0; Log($"shots fired {gun.ShotsFired}"); }
        }

        lookOff = Damp(lookOff, lookTarget, ref lookOffVel, 0.55f, dt);
        float desiredYaw = pathYaw + lookOff;
        float yawErr = Mathf.DeltaAngle(player.Yaw, desiredYaw);
        float yawStep = Mathf.Clamp(yawErr * (1f - Mathf.Exp(-6f * dt)), -6f, 6f);
        float pitchNew = Damp(player.Pitch, pitchTarget + Mathf.Sin(t * 0.7f) * 1.2f, ref pitchVel, 0.6f, dt);
        // controller: yaw += look.x ; pitch -= look.y
        player.scriptedLook = new Vector2(yawStep, -(pitchNew - player.Pitch));
        // move in world along the path, expressed in the body frame (allows looking aside while walking)
        float rel = Mathf.DeltaAngle(player.Yaw + yawStep, pathYaw) * Mathf.Deg2Rad;
        Vector2 local = new Vector2(Mathf.Sin(rel), Mathf.Cos(rel));
        player.scriptedMove = mode == Mode.Walk ? local * (sprint ? 1f : 0.6f) : Vector2.zero;
        player.scriptedSprint = sprint && Mathf.Abs(rel) < 0.5f;
        if (seg >= pts.Count - 2) player.scriptedMove = Vector2.zero;
    }

    void Finish()
    {
        if (raw == null) return;
        raw.Close(); raw = null;
        if (urp) { urp.shadowDistance = oSd; urp.shadowCascadeCount = oSc; urp.msaaSampleCount = oMs; }
        Log($"done frames={frame} shots={gun.ShotsFired} final pos={player.transform.position} realtime={Time.realtimeSinceStartup:F0}s");
        log.Close();
        if (AutoCapture.OnFinished != null) AutoCapture.OnFinished(0); else Application.Quit(0);
    }
}
