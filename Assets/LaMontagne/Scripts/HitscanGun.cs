using System.Collections.Generic;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.Rendering;

/// Simple hitscan rifle: LMB fire (auto), R reload, RMB aim. Muzzle flash, impact decals + dust, recoil, procedural sound.
public class HitscanGun : MonoBehaviour
{
    public FPSController controller;
    public Camera cam;
    public Material metalMat, polymerMat, flashMat, decalMat, dustMat;
    public float fireRate = 10f, range = 1500f, baseSpread = 0.15f, moveSpread = 1.2f;
    public int magSize = 30, reserve = 120;
    public int Ammo { get; private set; }
    public int Reserve => reserve;
    public bool Reloading => reloadT > 0;
    public float CurrentSpread { get; private set; }
    public float HitMarkerT { get; private set; }
    public int ShotsFired { get; private set; }
    public string LastHitName { get; private set; } = "";
    public float LastHitDistance { get; private set; }
    public bool externalTrigger;   // for automated captures

    Transform gunRoot, muzzle, flashQuad;
    Light flashLight;
    float cooldown, flashT, reloadT, kick, aimT;
    readonly List<Transform> decals = new List<Transform>();
    int decalIdx;
    ParticleSystem dust;
    AudioSource audioSrc; AudioClip shotClip;
    Vector3 gunRest = new Vector3(0.20f, -0.19f, 0.42f), gunAim = new Vector3(0f, -0.115f, 0.36f);

    void Start()
    {
        if (!cam) cam = GetComponentInChildren<Camera>();
        Ammo = magSize;
        BuildModel();
        BuildFx();
    }

    static Material Fallback(Color c)
    {
        var m = new Material(Shader.Find("Universal Render Pipeline/Lit")); m.SetColor("_BaseColor", c); return m;
    }

    GameObject Part(PrimitiveType t, Vector3 pos, Vector3 scale, Material m, Vector3 euler = default)
    {
        var g = GameObject.CreatePrimitive(t);
        Destroy(g.GetComponent<Collider>());
        g.transform.SetParent(gunRoot, false);
        g.transform.localPosition = pos; g.transform.localScale = scale; g.transform.localEulerAngles = euler;
        var r = g.GetComponent<MeshRenderer>(); r.sharedMaterial = m; r.shadowCastingMode = ShadowCastingMode.Off;
        return g;
    }

    void BuildModel()
    {
        if (!metalMat) metalMat = Fallback(new Color(0.12f, 0.12f, 0.13f));
        if (!polymerMat) polymerMat = Fallback(new Color(0.18f, 0.17f, 0.15f));
        gunRoot = new GameObject("Gun").transform;
        gunRoot.SetParent(cam.transform, false);
        gunRoot.localPosition = gunRest;
        // receiver, barrel, handguard, magazine, grip, stock, sight
        Part(PrimitiveType.Cube, new Vector3(0, 0, 0), new Vector3(0.055f, 0.075f, 0.30f), metalMat);
        Part(PrimitiveType.Cube, new Vector3(0, 0.0f, 0.24f), new Vector3(0.06f, 0.065f, 0.20f), polymerMat);
        Part(PrimitiveType.Cylinder, new Vector3(0, 0.008f, 0.42f), new Vector3(0.022f, 0.10f, 0.022f), metalMat, new Vector3(90, 0, 0));
        Part(PrimitiveType.Cylinder, new Vector3(0, 0.008f, 0.53f), new Vector3(0.032f, 0.025f, 0.032f), metalMat, new Vector3(90, 0, 0));
        Part(PrimitiveType.Cube, new Vector3(0, -0.085f, 0.06f), new Vector3(0.04f, 0.13f, 0.07f), metalMat, new Vector3(-12, 0, 0));
        Part(PrimitiveType.Cube, new Vector3(0, -0.07f, -0.08f), new Vector3(0.04f, 0.10f, 0.045f), polymerMat, new Vector3(15, 0, 0));
        Part(PrimitiveType.Cube, new Vector3(0, -0.01f, -0.25f), new Vector3(0.045f, 0.08f, 0.20f), polymerMat);
        Part(PrimitiveType.Cube, new Vector3(0, 0.055f, 0.02f), new Vector3(0.035f, 0.035f, 0.10f), metalMat);
        Part(PrimitiveType.Cube, new Vector3(0, 0.08f, 0.02f), new Vector3(0.012f, 0.02f, 0.02f), metalMat);
        muzzle = new GameObject("Muzzle").transform; muzzle.SetParent(gunRoot, false); muzzle.localPosition = new Vector3(0, 0.008f, 0.57f);
    }

    void BuildFx()
    {
        // muzzle flash: two crossed additive quads + light
        flashQuad = new GameObject("Flash").transform; flashQuad.SetParent(muzzle, false);
        for (int i = 0; i < 2; i++)
        {
            var q = GameObject.CreatePrimitive(PrimitiveType.Quad); Destroy(q.GetComponent<Collider>());
            q.transform.SetParent(flashQuad, false);
            q.transform.localEulerAngles = i == 0 ? Vector3.zero : new Vector3(0, 90, 0);
            q.transform.localPosition = i == 0 ? new Vector3(0, 0, 0.02f) : new Vector3(0, 0, 0.09f);
            q.transform.localScale = i == 0 ? new Vector3(0.16f, 0.16f, 1) : new Vector3(0.18f, 0.07f, 1);
            var r = q.GetComponent<MeshRenderer>(); r.sharedMaterial = flashMat; r.shadowCastingMode = ShadowCastingMode.Off;
        }
        flashQuad.gameObject.SetActive(false);
        var lg = new GameObject("FlashLight"); lg.transform.SetParent(muzzle, false); lg.transform.localPosition = new Vector3(0, 0, 0.08f);
        flashLight = lg.AddComponent<Light>(); flashLight.type = LightType.Point; flashLight.range = 9; flashLight.intensity = 0;
        flashLight.color = new Color(1f, 0.78f, 0.45f); flashLight.shadows = LightShadows.None;

        // impact dust
        var dg = new GameObject("ImpactDust");
        dust = dg.AddComponent<ParticleSystem>();
        dust.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
        var main = dust.main; main.loop = false; main.playOnAwake = false; main.startLifetime = 0.6f; main.startSpeed = 0;
        main.startSize = new ParticleSystem.MinMaxCurve(0.05f, 0.18f); main.gravityModifier = 0.6f; main.maxParticles = 2000;
        main.simulationSpace = ParticleSystemSimulationSpace.World;
        var em = dust.emission; em.enabled = false;
        var sh = dust.shape; sh.enabled = false;
        var col = dust.colorOverLifetime; col.enabled = true;
        var gr = new Gradient(); gr.SetKeys(new[] { new GradientColorKey(Color.white, 0), new GradientColorKey(Color.white, 1) },
                                            new[] { new GradientAlphaKey(0.9f, 0), new GradientAlphaKey(0, 1) });
        col.color = gr;
        var pr = dg.GetComponent<ParticleSystemRenderer>(); pr.sharedMaterial = dustMat; pr.shadowCastingMode = ShadowCastingMode.Off;

        // decal pool
        for (int i = 0; i < 160; i++)
        {
            var q = GameObject.CreatePrimitive(PrimitiveType.Quad); Destroy(q.GetComponent<Collider>());
            q.name = "BulletHole"; var r = q.GetComponent<MeshRenderer>(); r.sharedMaterial = decalMat; r.shadowCastingMode = ShadowCastingMode.Off;
            q.SetActive(false); decals.Add(q.transform);
        }

        // procedural gunshot
        audioSrc = gameObject.AddComponent<AudioSource>(); audioSrc.spatialBlend = 0; audioSrc.volume = 0.6f;
        int sr = 44100, n = (int)(sr * 0.35f); var d = new float[n]; var rnd = new System.Random(4462); float lp = 0;
        for (int i = 0; i < n; i++)
        {
            float t = i / (float)sr; float env = Mathf.Exp(-t * 18f) + 0.25f * Mathf.Exp(-t * 5f);
            float w = (float)(rnd.NextDouble() * 2 - 1); lp += (w - lp) * (0.35f - 0.25f * Mathf.Clamp01(t * 6));
            d[i] = Mathf.Clamp((lp * 1.6f + Mathf.Sin(t * 2 * Mathf.PI * 70) * Mathf.Exp(-t * 30)) * env, -1, 1);
        }
        shotClip = AudioClip.Create("shot", n, 1, sr, false); shotClip.SetData(d, 0);
    }

    [System.NonSerialized] public bool touchTrigger, touchAim, touchReload;

    void Update()
    {
        float dt = Time.deltaTime;
        var ms = Mouse.current; var kb = Keyboard.current; var gp = Gamepad.current;
        bool trigger = externalTrigger, aim = false, reload = false;
        if (controller == null || controller.inputEnabled)
        {
            trigger |= touchTrigger; aim |= touchAim; reload |= touchReload; touchReload = false;
            if (ms != null && !TouchControls.Active && Cursor.lockState == CursorLockMode.Locked) { trigger |= ms.leftButton.isPressed; aim |= ms.rightButton.isPressed; }
            if (kb != null) reload |= kb.rKey.wasPressedThisFrame;
            if (gp != null) { trigger |= gp.rightTrigger.isPressed; aim |= gp.leftTrigger.isPressed; reload |= gp.buttonWest.wasPressedThisFrame; }
        }
        if (reload && !Reloading && Ammo < magSize && reserve > 0) reloadT = 1.8f;
        if (Reloading)
        {
            reloadT -= dt;
            if (reloadT <= 0) { int take = Mathf.Min(magSize - Ammo, reserve); Ammo += take; reserve -= take; reloadT = 0; }
        }
        cooldown -= dt;
        float spd = controller ? new Vector2(controller.Velocity.x, controller.Velocity.z).magnitude : 0;
        aimT = Mathf.MoveTowards(aimT, aim ? 1 : 0, dt * 6);
        CurrentSpread = Mathf.Lerp(baseSpread + moveSpread * Mathf.Clamp01(spd / 7f) + kick * 2f, baseSpread * 0.3f, aimT * 0.8f);
        if (trigger && cooldown <= 0 && !Reloading)
        {
            if (Ammo > 0) Fire();
            else if (reserve > 0) reloadT = 1.8f;
            cooldown = 1f / fireRate;
        }
        // flash & kick animation
        if (flashT > 0) { flashT -= dt; if (flashT <= 0) { flashQuad.gameObject.SetActive(false); flashLight.intensity = 0; } }
        kick = Mathf.MoveTowards(kick, 0, dt * 0.6f);
        HitMarkerT = Mathf.Max(0, HitMarkerT - dt);
        // sway + reload dip
        float bob = controller ? Mathf.Sin(Time.time * 9f) * 0.004f * Mathf.Clamp01(spd / 4f) : 0;
        Vector3 basePos = Vector3.Lerp(gunRest, gunAim, aimT);
        float dip = Reloading ? Mathf.Sin(Mathf.Clamp01(1 - reloadT / 1.8f) * Mathf.PI) : 0;
        gunRoot.localPosition = basePos + new Vector3(bob, Mathf.Abs(bob) - dip * 0.12f, -kick * 0.6f);
        gunRoot.localRotation = Quaternion.Euler(-kick * 60f + dip * 25f, 0, dip * -20f);
        if (cam) cam.fieldOfView = Mathf.Lerp(75f, 58f, aimT);
    }

    public void Fire()
    {
        Ammo--; ShotsFired++;
        kick = Mathf.Min(kick + 0.035f, 0.12f);
        if (controller) controller.AddRecoil(Random.Range(0.6f, 1.0f), Random.Range(-0.25f, 0.25f));
        flashT = 0.045f; flashQuad.gameObject.SetActive(true);
        flashQuad.localEulerAngles = new Vector3(0, 0, Random.Range(0f, 360f));
        flashQuad.localScale = Vector3.one * Random.Range(0.8f, 1.25f);
        flashLight.intensity = 4.5f;
        if (shotClip) audioSrc.PlayOneShot(shotClip, Random.Range(0.85f, 1f));

        Vector3 dir = cam.transform.forward;
        Vector2 r = Random.insideUnitCircle * Mathf.Tan(CurrentSpread * Mathf.Deg2Rad);
        dir = (dir + cam.transform.right * r.x + cam.transform.up * r.y).normalized;
        if (Physics.Raycast(cam.transform.position, dir, out var hit, range, ~0, QueryTriggerInteraction.Ignore))
        {
            HitMarkerT = 0.12f;
            LastHitName = hit.collider.name; LastHitDistance = hit.distance;
            var d = decals[decalIdx]; decalIdx = (decalIdx + 1) % decals.Count;
            d.gameObject.SetActive(true);
            d.position = hit.point + hit.normal * 0.012f;
            d.rotation = Quaternion.LookRotation(-hit.normal) * Quaternion.Euler(0, 0, Random.Range(0f, 360f));
            d.localScale = Vector3.one * Random.Range(0.13f, 0.19f);
            var ep = new ParticleSystem.EmitParams();
            for (int i = 0; i < 14; i++)
            {
                ep.position = hit.point + hit.normal * 0.03f;
                ep.velocity = (hit.normal * Random.Range(1f, 3.5f) + Random.insideUnitSphere * 1.6f);
                ep.startColor = new Color(0.62f, 0.58f, 0.52f, 1);
                ep.startSize = Random.Range(0.04f, 0.16f);
                ep.startLifetime = Random.Range(0.35f, 0.9f);
                dust.Emit(ep, 1);
            }
        }
        else LastHitName = "";
    }
}
