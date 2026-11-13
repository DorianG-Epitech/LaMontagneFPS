using System.Collections.Generic;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.Controls;
using UnityEngine.UI;

/// Mobile touch controls (WebGL on phones/tablets): floating joystick on the left half (move),
/// drag anywhere on the right half to look, buttons for fire (drag on it also aims), jump, sprint, crouch, aim, reload.
/// Turns itself on on mobile platforms or at the first touch; mouse input is ignored while active.
public class TouchControls : MonoBehaviour
{
    public static bool Active { get; private set; }
    public FPSController controller;
    public HitscanGun gun;
    public float lookSensitivity = 0.16f;   // degrees per reference pixel (1080p)

    class Btn { public string label; public Vector2 anchor, pos; public float r; public RectTransform rt; public Image img; public bool held; public int finger = -1; public bool toggle, on; }
    readonly List<Btn> buttons = new List<Btn>();
    Canvas canvas; RectTransform root, stickBase, stickKnob;
    int stickFinger = -1, lookFinger = -1; Vector2 stickOrigin, lastLook;
    const float StickRadius = 150f;
    static Sprite circle;
    Btn bFire, bJump, bSprint, bCrouch, bAim, bReload;

    public static void Install(FPSController c, HitscanGun g)
    {
        if (FindAnyObjectByType<TouchControls>() != null) return;
        var go = new GameObject("TouchControls");
        var t = go.AddComponent<TouchControls>(); t.controller = c; t.gun = g;
    }

    static Sprite Circle()
    {
        if (circle) return circle;
        int n = 128; var tex = new Texture2D(n, n, TextureFormat.RGBA32, false);
        var px = new Color32[n * n];
        for (int y = 0; y < n; y++) for (int x = 0; x < n; x++)
        {
            float d = Vector2.Distance(new Vector2(x + 0.5f, y + 0.5f), new Vector2(n / 2f, n / 2f)) / (n / 2f);
            float a = Mathf.Clamp01((1f - d) * 24f);
            float ring = d > 0.9f ? 1f : 0.55f;
            px[y * n + x] = new Color32(255, 255, 255, (byte)(a * ring * 255));
        }
        tex.SetPixels32(px); tex.Apply();
        circle = Sprite.Create(tex, new Rect(0, 0, n, n), new Vector2(0.5f, 0.5f));
        return circle;
    }

    RectTransform Disc(string name, Vector2 anchor, Vector2 pos, float r, Color c, out Image img)
    {
        var g = new GameObject(name, typeof(RectTransform)); g.transform.SetParent(root, false);
        img = g.AddComponent<Image>(); img.sprite = Circle(); img.color = c; img.raycastTarget = false;
        var rt = (RectTransform)g.transform; rt.anchorMin = rt.anchorMax = anchor; rt.pivot = new Vector2(0.5f, 0.5f);
        rt.anchoredPosition = pos; rt.sizeDelta = new Vector2(r * 2, r * 2);
        return rt;
    }

    Btn AddButton(string label, Vector2 anchor, Vector2 pos, float r, bool toggle = false)
    {
        var b = new Btn { label = label, anchor = anchor, pos = pos, r = r, toggle = toggle };
        b.rt = Disc("Btn_" + label, anchor, pos, r, new Color(1, 1, 1, 0.28f), out b.img);
        var tg = new GameObject("Label", typeof(RectTransform)); tg.transform.SetParent(b.rt, false);
        var tx = tg.AddComponent<Text>(); tx.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        tx.text = label; tx.fontSize = Mathf.RoundToInt(r * 0.42f); tx.alignment = TextAnchor.MiddleCenter; tx.color = new Color(1, 1, 1, 0.9f); tx.raycastTarget = false;
        var trt = (RectTransform)tg.transform; trt.anchorMin = Vector2.zero; trt.anchorMax = Vector2.one; trt.offsetMin = trt.offsetMax = Vector2.zero;
        buttons.Add(b);
        return b;
    }

    void Start()
    {
        var cg = new GameObject("TouchCanvas", typeof(RectTransform));
        cg.transform.SetParent(transform, false);
        canvas = cg.AddComponent<Canvas>(); canvas.renderMode = RenderMode.ScreenSpaceOverlay; canvas.sortingOrder = 50;
        var cs = cg.AddComponent<CanvasScaler>(); cs.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
        cs.referenceResolution = new Vector2(1920, 1080); cs.matchWidthOrHeight = 1f;   // landscape: scale with height
        root = (RectTransform)cg.transform;
        stickBase = Disc("StickBase", Vector2.zero, new Vector2(270, 270), StickRadius, new Color(1, 1, 1, 0.22f), out _);
        stickKnob = Disc("StickKnob", Vector2.zero, new Vector2(270, 270), 62, new Color(1, 1, 1, 0.5f), out _);
        var br = new Vector2(1, 0); var bl = new Vector2(0, 0);
        bFire = AddButton("TIR", br, new Vector2(-260, 270), 115);
        bAim = AddButton("VISER", br, new Vector2(-480, 150), 72);
        bJump = AddButton("SAUT", br, new Vector2(-110, 480), 80);
        bReload = AddButton("R", br, new Vector2(-470, 360), 58);
        bCrouch = AddButton("ACCR.", br, new Vector2(-110, 140), 64, true);
        bSprint = AddButton("SPRINT", bl, new Vector2(150, 600), 70, true);
        SetVisible(Application.isMobilePlatform);
        Active = Application.isMobilePlatform;
    }

    void SetVisible(bool v) { if (root) root.gameObject.SetActive(v); }

    Vector2 ScreenOf(Btn b)
    {
        float k = canvas.scaleFactor;
        Vector2 a = new Vector2(b.anchor.x * Screen.width, b.anchor.y * Screen.height);
        return a + b.pos * k;
    }

    Btn HitButton(Vector2 p)
    {
        float k = canvas.scaleFactor;
        foreach (var b in buttons) if (Vector2.Distance(p, ScreenOf(b)) < b.r * k * 1.15f) return b;
        return null;
    }

    void Update()
    {
        var ts = Touchscreen.current;
        if (ts == null || canvas == null) return;
        if (!Active)
        {
            foreach (var t in ts.touches) if (t.press.isPressed) { Active = true; break; }
            if (!Active) return;
            Cursor.lockState = CursorLockMode.None; Cursor.visible = true;
        }
        SetVisible(true);
        float k = canvas.scaleFactor;
        float refScale = 1080f / Mathf.Max(1, Screen.height);
        Vector2 move = Vector2.zero, look = Vector2.zero;
        var seen = new HashSet<int>();
        foreach (TouchControl t in ts.touches)
        {
            if (!t.press.isPressed) continue;
            int id = t.touchId.ReadValue(); seen.Add(id);
            Vector2 p = t.position.ReadValue();
            bool began = t.press.wasPressedThisFrame || (id != stickFinger && id != lookFinger && !IsButtonFinger(id));
            if (began)
            {
                var b = HitButton(p);
                if (b != null)
                {
                    b.finger = id; b.held = true; if (b.toggle) b.on = !b.on;
                    if (b == bJump) controller.touchJump = true;
                    if (b == bReload && gun) gun.touchReload = true;
                    if (b == bFire) lastLook = p;
                }
                else if (p.x < Screen.width * 0.45f && stickFinger < 0) { stickFinger = id; stickOrigin = p; }
                else if (lookFinger < 0) { lookFinger = id; lastLook = p; }
                continue;
            }
            if (id == stickFinger)
            {
                Vector2 d = (p - stickOrigin) / (StickRadius * k);
                if (d.magnitude > 1) { stickOrigin += (p - stickOrigin) - d.normalized * StickRadius * k; d = d.normalized; }  // follow finger
                move = d;
            }
            else if (id == lookFinger || (bFire.finger == id))
            {
                look += (p - lastLook) * refScale * lookSensitivity; lastLook = p;
            }
        }
        // releases
        if (stickFinger >= 0 && !seen.Contains(stickFinger)) stickFinger = -1;
        if (lookFinger >= 0 && !seen.Contains(lookFinger)) lookFinger = -1;
        foreach (var b in buttons) if (b.finger >= 0 && !seen.Contains(b.finger)) { b.finger = -1; b.held = false; }

        // outputs
        if (controller)
        {
            controller.touchMove = move;
            controller.touchLook += new Vector2(look.x, look.y);
            controller.touchSprint = bSprint.on;
            controller.touchCrouch = bCrouch.on;
            if (bSprint.on && move.y < 0.1f && stickFinger < 0) { }   // sprint toggle stays on
        }
        if (gun) { gun.touchTrigger = bFire.held; gun.touchAim = bAim.held; }

        // visuals
        Vector2 basePos = stickFinger >= 0 ? stickOrigin / k : new Vector2(270, 270);
        stickBase.anchoredPosition = basePos;
        stickKnob.anchoredPosition = basePos + move * StickRadius;
        foreach (var b in buttons)
            b.img.color = (b.held || (b.toggle && b.on)) ? new Color(1f, 0.85f, 0.4f, 0.55f) : new Color(1, 1, 1, 0.28f);
    }

    bool IsButtonFinger(int id) { foreach (var b in buttons) if (b.finger == id) return true; return false; }

    void OnDisable() { Active = false; }
}
