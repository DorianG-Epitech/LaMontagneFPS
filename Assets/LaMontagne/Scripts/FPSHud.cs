using UnityEngine;
using UnityEngine.UI;

/// uGUI HUD rendered by the player camera (Screen Space - Camera so it also appears in camera captures):
/// dynamic crosshair, hit marker, ammo, FPS, location (nearest BD TOPO place name) + Lambert-93 coordinates.
public class FPSHud : MonoBehaviour
{
    public Camera cam;
    public HitscanGun gun;
    public FPSController controller;
    public bool showFps = true;
    RectTransform[] cross = new RectTransform[4];
    Image[] crossImg = new Image[4];
    RectTransform[] hitm = new RectTransform[4];
    Text ammo, fps, loc, help;
    float fpsSmooth = 60, locT;
    CityMeta meta;

    Text MakeText(Transform p, string name, int size, TextAnchor anchor, Vector2 aMin, Vector2 aMax, Vector2 pos, Vector2 sizeDelta)
    {
        var g = new GameObject(name, typeof(RectTransform)); g.transform.SetParent(p, false);
        var t = g.AddComponent<Text>();
        t.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        t.fontSize = size; t.alignment = anchor; t.color = new Color(1, 1, 1, 0.95f);
        t.horizontalOverflow = HorizontalWrapMode.Overflow; t.verticalOverflow = VerticalWrapMode.Overflow;
        var sh = g.AddComponent<Shadow>(); sh.effectColor = new Color(0, 0, 0, 0.8f); sh.effectDistance = new Vector2(2, -2);
        var rt = (RectTransform)g.transform; rt.anchorMin = aMin; rt.anchorMax = aMax; rt.pivot = (aMin + aMax) / 2;
        rt.anchoredPosition = pos; rt.sizeDelta = sizeDelta;
        return t;
    }

    RectTransform Bar(Transform p, Vector2 size, Color c, out Image img)
    {
        var g = new GameObject("bar", typeof(RectTransform)); g.transform.SetParent(p, false);
        img = g.AddComponent<Image>(); img.color = c;
        var o = g.AddComponent<Outline>(); o.effectColor = new Color(0, 0, 0, 0.7f); o.effectDistance = new Vector2(1, -1);
        var rt = (RectTransform)g.transform; rt.sizeDelta = size; rt.anchorMin = rt.anchorMax = new Vector2(0.5f, 0.5f);
        return rt;
    }

    void Start()
    {
        meta = CityMeta.Load();
        var cg = new GameObject("HUD", typeof(RectTransform));
        var canvas = cg.AddComponent<Canvas>();
        canvas.renderMode = RenderMode.ScreenSpaceCamera; canvas.worldCamera = cam; canvas.planeDistance = 0.06f;
        var cs = cg.AddComponent<CanvasScaler>(); cs.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize; cs.referenceResolution = new Vector2(1920, 1080); cs.matchWidthOrHeight = 0.5f;
        var root = cg.transform;
        for (int i = 0; i < 4; i++)
        {
            cross[i] = Bar(root, i < 2 ? new Vector2(3, 14) : new Vector2(14, 3), new Color(1, 1, 1, 0.9f), out crossImg[i]);
            hitm[i] = Bar(root, new Vector2(3, 12), new Color(1, 0.3f, 0.25f, 0.95f), out _);
            hitm[i].localRotation = Quaternion.Euler(0, 0, 45 + 90 * i);
        }
        var dot = Bar(root, new Vector2(2, 2), Color.white, out _);
        ammo = MakeText(root, "Ammo", 46, TextAnchor.LowerRight, new Vector2(1, 0), new Vector2(1, 0), new Vector2(-60, 50), new Vector2(400, 60));
        fps = MakeText(root, "Fps", 22, TextAnchor.UpperRight, new Vector2(1, 1), new Vector2(1, 1), new Vector2(-30, -24), new Vector2(400, 30));
        loc = MakeText(root, "Location", 24, TextAnchor.UpperLeft, new Vector2(0, 1), new Vector2(0, 1), new Vector2(30, -24), new Vector2(900, 60));
        help = MakeText(root, "Help", 18, TextAnchor.LowerLeft, new Vector2(0, 0), new Vector2(0, 0), new Vector2(30, 30), new Vector2(900, 30));
        help.text = TouchControls.Active ? "" : (Application.platform == RuntimePlatform.WebGLPlayer
            ? "Clic pour jouer · ZQSD/WASD · Shift sprint · Espace saut · Ctrl accroupi · Clic tir · Clic droit viser · R recharger · Échap souris"
            : "ZQSD/WASD · Shift sprint · Espace saut · Ctrl accroupi · Clic tir · Clic droit viser · R recharger");
        help.color = new Color(1, 1, 1, 0.7f);
    }

    void Update()
    {
        if (TouchControls.Active && help != null && help.text.Length > 0) help.text = "";
        float dt = Mathf.Max(Time.unscaledDeltaTime, 1e-4f);
        fpsSmooth = Mathf.Lerp(fpsSmooth, 1f / dt, 0.05f);
        if (fps) fps.text = showFps ? $"{fpsSmooth:F0} FPS" : "";
        if (gun && ammo) ammo.text = gun.Reloading ? "RECHARGEMENT…" : $"{gun.Ammo} <size=28>/ {gun.Reserve}</size>";
        if (ammo) ammo.supportRichText = true;
        float spread = gun ? gun.CurrentSpread : 0.2f;
        float gap = 7 + spread * 22;
        if (cross[0])
        {
            cross[0].anchoredPosition = new Vector2(0, gap + 7); cross[1].anchoredPosition = new Vector2(0, -gap - 7);
            cross[2].anchoredPosition = new Vector2(gap + 7, 0); cross[3].anchoredPosition = new Vector2(-gap - 7, 0);
            bool hm = gun && gun.HitMarkerT > 0;
            for (int i = 0; i < 4; i++)
            {
                hitm[i].gameObject.SetActive(hm);
                Vector2 d = new Vector2(Mathf.Cos((45 + 90 * i) * Mathf.Deg2Rad + Mathf.PI / 2), Mathf.Sin((45 + 90 * i) * Mathf.Deg2Rad + Mathf.PI / 2));
                hitm[i].anchoredPosition = d * 13;
            }
        }
        locT -= Time.unscaledDeltaTime;
        if (locT <= 0 && loc && cam)
        {
            locT = 0.5f;
            Vector3 p = cam.transform.position;
            string near = "";
            float best = 1e9f;
            if (meta != null && meta.poi != null)
                foreach (var q in meta.poi)
                {
                    float d = (new Vector2(q.x, q.z) - new Vector2(p.x, p.z)).magnitude;
                    if (d < best) { best = d; near = q.name; }
                }
            double e = CityMeta.X0 + p.x, n = CityMeta.Y0 + p.z;
            loc.text = $"<b>La Montagne (44620)</b>  ·  {(best < 400 ? near : "")}\n<size=17>Lambert-93  E {e:F0}  N {n:F0}  ·  alt {p.y - 1.68f:F0} m</size>";
            loc.supportRichText = true;
        }
    }
}
