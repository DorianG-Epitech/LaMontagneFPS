#!/usr/bin/env python3
"""v2 textures from Panoramax street references (La Montagne bourg): facade atlases (window | door | shopfront | garage),
church grey render + granite quoins + tuffeau belfry, light asphalt sidewalks, granite kerbs, green fences, hedges, grass."""
import os, numpy as np
from PIL import Image, ImageDraw, ImageFilter
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Assets", "LaMontagne", "Textures")
rng = np.random.default_rng(4462)
T = 512
def noise(n, scales=(4, 16, 64), amps=(0.5, 0.3, 0.2), m=None):
    m = m or n
    out = np.zeros((n, m))
    for s, a in zip(scales, amps):
        g = rng.random((s, max(1, s * m // n)))
        out += a * np.asarray(Image.fromarray((g * 255).astype(np.uint8)).resize((m, n), Image.BICUBIC), float) / 255
    return (out - out.min()) / (out.max() - out.min() + 1e-9)
def save(name, a, mode="RGB"):
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), mode).save(os.path.join(OUT, name + ".png"))
def nmap(name, h, s=2.0):
    gx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * s; gy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * s
    l = np.sqrt(gx ** 2 + gy ** 2 + 1)
    save(name + "_n", (np.stack([-gx / l, gy / l, 1 / l], -1) * 0.5 + 0.5) * 255)

def render(col, n=T, m=T):
    b = noise(n, (8, 32, 128), (0.4, 0.4, 0.2), m) * 12 - 6 + rng.normal(0, 3, (n, m))
    return np.stack([np.full((n, m), c) + b for c in col], -1)
def rubble(n=T, m=T):
    img = np.zeros((n, m, 3)); h = np.zeros((n, m))
    im = Image.new("RGB", (m, n), (178, 168, 150)); d = ImageDraw.Draw(im); hm = Image.new("L", (m, n), 0); dh = ImageDraw.Draw(hm)
    y = 0
    while y < n:
        rh = int(rng.integers(28, 60)); x = int(-rng.integers(0, 40))
        while x < m:
            rw = int(rng.integers(40, 110)); sv = rng.normal(0, 12); c = (np.array([150, 116, 88]) + sv + np.array([1.0, 0.72, 0.45]) * rng.normal(0, 16)).astype(int)
            d.rounded_rectangle([x + 3, y + 3, x + rw - 3, y + rh - 3], 8, fill=tuple(c)); dh.rounded_rectangle([x + 3, y + 3, x + rw - 3, y + rh - 3], 8, fill=200)
            x += rw
        y += rh
    img = np.asarray(im, float) * (0.9 + 0.2 * noise(n, m=m))[..., None]
    return img, np.asarray(hm.filter(ImageFilter.GaussianBlur(3)), float) / 255

def bay(base, kind, sur, shutter, roller=False):
    """one 3 m x 3 m bay (T x T). kind: window | door | shop | garage. sur: None | brick | tuffeau"""
    img = base.copy(); h = np.zeros(img.shape[:2]); cov = np.zeros(img.shape[:2], bool)
    S = T / 3.0  # px per metre
    def rect(x0, y0, x1, y1, col, hv=0.0):
        X0, Y0, X1, Y1 = int(x0 * S), int(T - y1 * S), int(x1 * S), int(T - y0 * S)
        img[max(Y0, 0):Y1, max(X0, 0):X1] = col; h[max(Y0, 0):Y1, max(X0, 0):X1] = hv; cov[max(Y0, 0):Y1, max(X0, 0):X1] = True
    surc = {"brick": (160, 82, 60), "tuffeau": (232, 226, 210)}.get(sur)
    def surround(x0, y0, x1, y1, w=0.16):
        if surc is None: return
        rect(x0 - w, y0 - 0.05, x1 + w, y1 + w, surc, 0.4)
        if sur == "brick":   # brick courses
            for yy in np.arange(y0 - 0.05, y1 + w, 0.075):
                Y = int(T - yy * S); img[Y:Y + 1, int((x0 - w) * S):int((x1 + w) * S)] = (190, 170, 150)
    def glass(x0, y0, x1, y1):
        gy = np.linspace(0, 1, max(1, int((y1 - y0) * S)))[:, None]
        g = np.stack([55 + 60 * (1 - gy), 68 + 62 * (1 - gy), 82 + 66 * (1 - gy)], -1) * np.ones((1, max(1, int((x1 - x0) * S)), 1))
        Y0 = int(T - y1 * S); X0 = int(x0 * S); img[Y0:Y0 + g.shape[0], X0:X0 + g.shape[1]] = g; h[Y0:Y0 + g.shape[0], X0:X0 + g.shape[1]] = -1; cov[Y0:Y0 + g.shape[0], X0:X0 + g.shape[1]] = True
    if kind == "window":
        x0, x1, y0, y1 = 1.0, 2.0, 0.95, 2.35
        surround(x0, y0, x1, y1)
        rect(x0 - 0.06, y0, x1 + 0.06, y1, (240, 240, 238), -0.3); glass(x0 + 0.06, y0 + 0.06, x1 - 0.06, y1 - 0.06)
        rect(1.47, y0, 1.53, y1, (238, 238, 236), -0.2)
        for k in (1, 2): yy = y0 + (y1 - y0) * k / 3; rect(x0, yy - 0.02, x1, yy + 0.02, (236, 236, 234), -0.2)
        rect(x0 - 0.12, y0 - 0.08, x1 + 0.12, y0, (215, 210, 200), 0.6)   # sill
        if roller:
            rect(x0 - 0.06, y1, x1 + 0.06, y1 + 0.22, (236, 236, 232), 0.2)  # roller shutter box
        elif shutter is not None:
            for sx in (x0 - 0.52 - (0.16 if surc else 0), x1 + 0.02 + (0.16 if surc else 0)):
                rect(sx, y0, sx + 0.5, y1, shutter, 0.3)
                for yy in np.arange(y0 + 0.05, y1, 0.09): rect(sx + 0.03, yy, sx + 0.47, yy + 0.025, tuple(np.array(shutter) * 0.72), 0.1)
    elif kind == "door":
        x0, x1, y0, y1 = 0.95, 2.05, 0.0, 2.3
        surround(x0, y0, x1, y1)
        col = shutter if shutter is not None else (120, 85, 60)
        rect(x0, y0, x1, y1, col, -0.4)
        for (a, b) in ((0.15, 1.0), (1.2, 2.0)): rect(x0 + 0.12, a, x1 - 0.12, b, tuple(np.array(col) * 0.85), -0.5)
        glass(x0 + 0.15, 2.0 - 0.6 + 0.62, x1 - 0.15, 2.25) if False else None
        rect(1.85, 1.0, 1.92, 1.06, (200, 190, 120), 0)
        rect(x0 - 0.1, 0.0, x1 + 0.1, 0.1, (185, 182, 175), 0.3)   # step
    elif kind == "shop":
        rect(0.1, 0.0, 2.9, 2.75, (60, 62, 66), -0.2)
        glass(0.2, 0.35, 2.8, 2.45)
        rect(0.2, 0.0, 2.8, 0.35, (70, 72, 76), -0.1)
        rect(1.47, 0.35, 1.53, 2.45, (60, 62, 66), -0.1)
    elif kind == "garage":
        x0, x1 = 0.3, 2.7
        surround(x0, 0.0, x1, 2.1)
        rect(x0, 0.0, x1, 2.1, (225, 225, 222), -0.3)
        for yy in np.arange(0.0, 2.1, 0.5): rect(x0, yy, x1, yy + 0.03, (190, 190, 186), -0.4)
    return img, h, cov

def atlas(name, base_fn, sur, shutter, roller=False):
    cols = []; hs = []
    for kind in ("window", "door", "shop", "garage"):
        b, hb = base_fn()
        im, h, cov = bay(b, kind, sur, shutter, roller)
        cols.append(im); hs.append(h + hb * (~cov))
    save(name, np.concatenate(cols, 1)); nmap(name, np.concatenate(hs, 1) * 1.0 + noise(T, m=4 * T) * 0.4, 1.5)

STYLES = {
    "bourg_white_brick": (lambda: (render((236, 234, 228)), np.zeros((T, T))), "brick", (118, 40, 46), False),
    "bourg_grey_tuffeau": (lambda: (render((190, 190, 184)), np.zeros((T, T))), "tuffeau", (110, 124, 138), False),
    "bourg_moellon": (lambda: rubble(), "brick", (222, 222, 216), False),
    "bourg_cream": (lambda: (render((230, 222, 202)), np.zeros((T, T))), "tuffeau", (112, 138, 114), False),
    "pav_white_brown": (lambda: (render((238, 237, 232)), np.zeros((T, T))), None, (112, 78, 52), False),
    "pav_white_roller": (lambda: (render((240, 239, 235)), np.zeros((T, T))), None, None, True),
    "pav_cream_blue": (lambda: (render((232, 224, 204)), np.zeros((T, T))), None, (82, 106, 140), False),
    "pav_grey_white": (lambda: (render((214, 213, 208)), np.zeros((T, T))), None, (236, 236, 232), False),
}
for n, (fn, sur, sh, roll) in STYLES.items(): atlas("fa_" + n, fn, sur, sh, roll)

# church: grey render wall bay (5 m wide x 5 m) with lancet window + tuffeau surround
N = 512
img = render((168, 166, 158), N, N); h = np.zeros((N, N))
d = Image.new("L", (N, N)); dr = ImageDraw.Draw(d)
dr.rectangle([N * 0.38, N * 0.18, N * 0.62, N * 0.80], fill=255); dr.ellipse([N * 0.38, N * 0.06, N * 0.62, N * 0.30], fill=255)
sur = np.asarray(d.filter(ImageFilter.MaxFilter(15))) > 0; win = np.asarray(d) > 0
img[sur] = (226, 220, 204); img[win] = (52, 58, 74); img[win & (np.arange(N)[None, :] % 42 < 4)] = (95, 95, 100)
h[sur] = 0.5; h[win] = -1
img[int(N * 0.80):int(N * 0.84), int(N * 0.33):int(N * 0.67)] = (215, 210, 196)
img[N - 30:] = img[N - 30:] * 0.82   # plinth
save("church_wall", img); nmap("church_wall", h + noise(N) * 0.3, 2)
# granite quoins (buttresses, chainages)
img = np.zeros((N, N, 3)); h = np.zeros((N, N))
for r in range(8):
    y0 = r * N // 8; w = N if r % 2 == 0 else int(N * 0.6)
    c = 95 + rng.normal(0, 10)
    img[y0:y0 + N // 8, :w] = (c, c * 1.0, c * 1.04); h[y0:y0 + N // 8, :w] = 1
    img[y0:y0 + N // 8, w:] = (166, 164, 156)
    img[y0:y0 + 3] = (60, 60, 62); h[y0:y0 + 3] = 0
img += rng.normal(0, 8, (N, N))[..., None]
save("granite", img); nmap("granite", h + noise(N) * 0.4, 2)
# tuffeau belfry stage with arched louvred opening
img = render((232, 227, 212), N, N); h = np.zeros((N, N))
d = Image.new("L", (N, N)); dr = ImageDraw.Draw(d)
dr.rectangle([N * 0.33, N * 0.30, N * 0.67, N * 0.85], fill=255); dr.ellipse([N * 0.33, N * 0.17, N * 0.67, N * 0.43], fill=255)
m = np.asarray(d) > 0; img[m] = (60, 55, 50); h[m] = -1
for yy in range(int(N * 0.3), int(N * 0.85), 18): img[yy:yy + 6][..., :][m[yy:yy + 6]] = (110, 100, 90)
for k in range(0, N, 64): img[k:k + 2] = (205, 200, 186)
save("tuffeau", img); nmap("tuffeau", h + noise(N) * 0.2, 2)
# light asphalt sidewalk (enrobé) and granite kerb
n1 = noise(N, (8, 32, 128), (0.3, 0.3, 0.4))
sw = 128 + n1 * 18 + rng.normal(0, 9, (N, N)) + (rng.random((N, N)) > 0.98) * 30
save("sidewalk", np.stack([sw, sw, sw * 1.02], -1)); nmap("sidewalk", n1 * 0.5 + rng.normal(0, 0.2, (N, N)), 1.2)
kb = 198 + noise(N) * 20 + rng.normal(0, 10, (N, N))
for k in range(0, N, N // 2): kb[:, k:k + 3] -= 50
save("kerb", np.stack([kb, kb, kb * 0.98], -1)); nmap("kerb", noise(N) * 0.4, 1)
# road markings
save("marking", np.full((16, 16, 3), 238.0))
# green welded-mesh fence panel (RGBA, alpha-cutout), 2.5 m x 1.5 m
F = np.zeros((256, 512, 4))
col = (52, 110, 72, 255)
for x in range(0, 512, 16): F[:, x:x + 3] = col
for y in (8, 60, 120, 180, 245): F[y:y + 4] = col
F[:, :8] = col; F[:, -8:] = col
Image.fromarray(F.astype(np.uint8), "RGBA").save(os.path.join(OUT, "fence.png"))
# hedge (dense leaves)
lv = noise(N, (16, 64, 256), (0.3, 0.3, 0.4)); hd = 60 + lv * 120 + rng.normal(0, 25, (N, N))
save("hedge", np.stack([hd * 0.45, hd * 0.68, hd * 0.32], -1)); nmap("hedge", lv * 2 + rng.normal(0, 0.5, (N, N)), 2)
# garden wall render (white, slightly dirty bottom)
gw = render((228, 226, 220), N, N); gw[int(N * 0.8):] *= np.linspace(1, 0.85, N - int(N * 0.8))[:, None, None]
save("garden_wall", gw); nmap("garden_wall", noise(N) * 0.8, 1)
# grass blade texture (RGBA) for terrain details
G = Image.new("RGBA", (128, 128), (0, 0, 0, 0)); gd = ImageDraw.Draw(G)
for i in range(46):
    x = rng.uniform(4, 124); hgt = rng.uniform(60, 126); lean = rng.uniform(-14, 14)
    c = (int(rng.uniform(70, 120)), int(rng.uniform(120, 165)), int(rng.uniform(45, 75)), 255)
    gd.polygon([(x - 2.5, 128), (x + 2.5, 128), (x + lean, 128 - hgt)], fill=c)
G.save(os.path.join(OUT, "grass_blades.png"))
print("v2 textures written")
