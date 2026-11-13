using System;
using System.IO;
using UnityEngine;

[Serializable] public class CityPoi { public string name; public string kind; public float x, y, z; }
[Serializable] public class CityView { public string name; public float x, y, z, yaw, pitch; }
[Serializable] public class CityChurch { public string name; public float x, z, ground, spire_top; }
[Serializable] public class CityColor { public string name; public float r, g, b; }

/// Subset of ImportData/meta.json (written by Tools/build_city.py), read with JsonUtility.
[Serializable]
public class CityMeta
{
    public CityView spawn;
    public CityView[] views;
    public CityPoi[] poi;
    public CityChurch[] churches;
    public CityColor[] palette_list;

    static CityMeta cached;
    public static CityMeta Load()
    {
        if (cached != null) return cached;
        string p = Path.Combine(Application.streamingAssetsPath, "LaMontagne", "meta.json");
        if (Application.platform != RuntimePlatform.WebGLPlayer && File.Exists(p))
            cached = JsonUtility.FromJson<CityMeta>(File.ReadAllText(p));
        else
        {
            var ta = Resources.Load<TextAsset>("LaMontagne/meta");   // web build: packed by Tools/pack_web.py
            if (ta != null) cached = JsonUtility.FromJson<CityMeta>(ta.text);
        }
        return cached;
    }

    // Lambert-93 origin of the local frame (see region.json)
    public const double X0 = 343552.0, Y0 = 6684852.0;
}
