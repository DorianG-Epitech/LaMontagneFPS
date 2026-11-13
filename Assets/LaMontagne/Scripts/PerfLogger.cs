using System.Collections.Generic;
using UnityEngine;

/// Logs frame-rate statistics every few seconds ([PERF] lines in the Player/Editor log).
public class PerfLogger : MonoBehaviour
{
    public float interval = 5f;
    readonly List<float> dts = new List<float>();
    float t;
    public static float LastAvgFps, LastLow1Fps;

    void Update()
    {
        float dt = Time.unscaledDeltaTime;
        if (Time.frameCount < 10) return;
        dts.Add(dt); t += dt;
        if (t >= interval && dts.Count > 10)
        {
            dts.Sort();
            float sum = 0; foreach (var d in dts) sum += d;
            float avg = sum / dts.Count;
            float low1 = dts[Mathf.Min(dts.Count - 1, (int)(dts.Count * 0.99f))];
            LastAvgFps = 1f / avg; LastLow1Fps = 1f / low1;
            Debug.Log($"[PERF] frames={dts.Count} avg={avg * 1000:F2}ms ({LastAvgFps:F1} fps) 1%low={LastLow1Fps:F1} fps max={dts[dts.Count - 1] * 1000:F1}ms tris(city)={CityLoader.Triangles}");
            dts.Clear(); t = 0;
        }
    }
}
