#!/usr/bin/env python3
"""Procedural PBR-ish textures for La Montagne FPS (albedo + normal maps). Output: Assets/LaMontagne/Textures/*.png"""
import os, numpy as np
from PIL import Image, ImageDraw, ImageFilter
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Assets", "LaMontagne", "Textures")
os.makedirs(OUT, exist_ok=True)
rng = np.random.default_rng(44620)

def noise(n, scales=(4, 16, 64), amps=(0.5, 0.3, 0.2)):
    out = np.zeros((n, n))
    for s, a in zip(scales, amps):
        g = rng.random((s, s))
        g = np.asarray(Image.fromarray((g * 255).astype(np.uint8)).resize((n, n), Image.BICUBIC), float) / 255
        # make tileable: blend with rolled copy
        out += a * g
    # tileable trick: average with shifted versions
    out = (out + np.roll(out, n // 2, 0) + np.roll(out, n // 2, 1) + np.roll(np.roll(out, n // 2, 0), n // 2, 1)) / 4
    return (out - out.min()) / (out.max() - out.min() + 1e-9)
def fine(n, amp=1.0):
    return rng.normal(0, amp, (n, n))
def save(name, arr):
    a = np.clip(arr, 0, 255).astype(np.uint8)
    Image.fromarray(a).save(os.path.join(OUT, name + ".png"))
def normal_from_height(name, h, strength=2.0):
    gx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * strength
    gy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * strength
    nz = np.ones_like(h)
    l = np.sqrt(gx ** 2 + gy ** 2 + nz ** 2)
    n = np.stack([-gx / l, gy / l, nz / l], -1)
    save(name + "_n", (n * 0.5 + 0.5) * 255)

N = 512
# ---------------- render (enduit) base, near white so the material colour tints it
def render_base(n=N):
    b = 236 + noise(n, (8, 32, 128), (0.4, 0.4, 0.2)) * 14 - 7 + fine(n, 3)
    return np.repeat(b[..., None], 3, -1)

def facade(name, base, shutter=(200, 205, 205), win_w=0.40, win_h=0.48, sill=0.26):
    n = base.shape[0]
    img = base.copy()
    x0, x1 = int(n * (0.5 - win_w / 2)), int(n * (0.5 + win_w / 2))
    y1 = int(n * (1 - sill)); y0 = y1 - int(n * win_h)
    # shutters (persiennes)
    sw = int(n * win_w / 2) - 6
    for sx0 in (x0 - sw - 4, x1 + 4):
        img[y0:y1, sx0:sx0 + sw] = shutter
        for yy in range(y0, y1, 9): img[yy:yy + 2, sx0:sx0 + sw] = np.array(shutter) * 0.75
        img[y0:y1, sx0:sx0 + 2] = np.array(shutter) * 0.6; img[y0:y1, sx0 + sw - 2:sx0 + sw] = np.array(shutter) * 0.6
    # frame
    img[y0 - 6:y1 + 4, x0 - 6:x1 + 6] = (245, 245, 243)
    # glass with sky reflection gradient
    gy = np.linspace(0, 1, y1 - y0)[:, None]
    glass = np.stack([60 + 70 * (1 - gy) + 0 * np.arange(x1 - x0)[None], 75 + 75 * (1 - gy) + 0 * np.arange(x1 - x0)[None], 90 + 80 * (1 - gy) + 0 * np.arange(x1 - x0)[None]], -1)
    img[y0:y1, x0:x1] = glass
    # mullions (2 battants + petits bois)
    xm = (x0 + x1) // 2
    img[y0:y1, xm - 3:xm + 3] = (240, 240, 238)
    for k in (1, 2):
        yy = y0 + (y1 - y0) * k // 3; img[yy - 2:yy + 2, x0:x1] = (238, 238, 236)
    # sill (appui de fenetre)
    img[y1 + 4:y1 + 14, x0 - 14:x1 + 14] = (205, 200, 192)
    # subtle dirt streak under the sill
    for xx in range(x0 - 10, x1 + 10):
        L = int(rng.integers(10, 40))
        img[y1 + 14:y1 + 14 + L, xx] *= np.linspace(0.93, 1.0, L)[:, None]
    # floor band shadow at the bottom
    save(name, img)
    h = np.zeros((n, n)); h[y0 - 6:y1 + 4, x0 - 6:x1 + 6] = -1.0; h[y0:y1, x0:x1] = -2.0; h[y1 + 4:y1 + 14, x0 - 14:x1 + 14] = 1.0
    h = h + noise(n) * 0.6 + fine(n, 0.08)
    normal_from_height(name, np.asarray(Image.fromarray(((h - h.min()) * 40).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1)), float) / 40, 1.5)

rb = render_base()
facade("facade_render", rb)
save("wall_plain", rb); normal_from_height("wall_plain", noise(N) * 0.8 + fine(N, 0.05), 1.2)

# stone (granite / tuffeau blocks)
def stone(n=N, rows=8, cols=4, base=(190, 180, 160), var=22, mortar=(205, 198, 185)):
    img = np.zeros((n, n, 3)); h = np.zeros((n, n))
    rh = n // rows
    for r in range(rows):
        off = (r % 2) * (n // cols // 2)
        cw = n // cols
        for c in range(cols + 1):
            x0 = (c * cw + off) % n
            sv = float(np.clip(rng.normal(0, var), -40, 40)); col = np.array(base) + sv + rng.normal(0, 3, 3) + np.array([1.0, 0.4, -0.6]) * float(rng.normal(0, 6))
            for xx in range(x0, x0 + cw):
                img[r * rh:(r + 1) * rh, xx % n] = col
                h[r * rh:(r + 1) * rh, xx % n] = 1.0
            img[r * rh:(r + 1) * rh, x0 % n:(x0 + 3) % n or n] = mortar; h[r * rh:(r + 1) * rh, x0 % n:(x0 + 3) % n or n] = 0
        img[r * rh:r * rh + 3] = mortar; h[r * rh:r * rh + 3] = 0
    nz = noise(n, (16, 64, 256), (0.3, 0.4, 0.3))
    img *= (0.85 + 0.25 * nz)[..., None]; img += fine(n, 6)[..., None]
    return img, h + nz * 0.5
st, sh = stone()
facade("facade_stone", st, shutter=(120, 130, 135))
cs, ch = stone(rows=10, cols=3, base=(200, 192, 172), var=14, mortar=(215, 210, 198))
# church stone: lancet window in the middle
img = cs.copy(); d = Image.new("L", (N, N), 0); dr = ImageDraw.Draw(d)
dr.rectangle([N * 0.42, N * 0.35, N * 0.58, N * 0.85], fill=255); dr.ellipse([N * 0.42, N * 0.23, N * 0.58, N * 0.47], fill=255)
m = np.asarray(d) > 0
img[m] = (55, 60, 80); img[m & (np.arange(N)[None, :] % 40 < 3)] = (90, 90, 95)
save("church_stone", img); normal_from_height("church_stone", ch, 2.0)

# industrial cladding (bac acier)
x = np.arange(N)
rib = ((x % 64) < 10).astype(float) * 1.0 + ((x % 64) > 54) * 0.5
ind = np.tile((228 - rib * 25)[None, :], (N, 1)) + noise(N) * 8
save("industrial", np.repeat(ind[..., None], 3, -1)); normal_from_height("industrial", np.tile(rib[None, :], (N, 1)) * 3, 1.5)

# roof tiles (tuiles romanes/canal): rows of rounded tiles, light so the palette colour tints them
def roof_tile(n=N, rows=12, cols=8):
    yy, xx = np.mgrid[0:n, 0:n]
    rh = n / rows; cw = n / cols
    r = (yy // rh).astype(int)
    off = (r % 2) * cw / 2
    u = ((xx + off) % cw) / cw
    v = (yy % rh) / rh
    h = np.sin(u * np.pi) * 0.8 + v * 0.4
    h = h + noise(n) * 0.3
    tilecol = rng.normal(0, 1, (rows, cols + 1))
    tv = tilecol[r, ((xx + off) // cw).astype(int) % (cols + 1)]
    lum = 205 + 30 * (np.sin(u * np.pi) - 0.5) + tv * 10 - (1 - v) ** 6 * 70 + noise(n, (8, 32, 128)) * 25 - 12
    img = np.stack([lum, lum * 0.97, lum * 0.95], -1) + fine(n, 4)[..., None]
    return img, h
img, h = roof_tile(); save("roof_tile", img); normal_from_height("roof_tile", h, 3.0)

def roof_slate(n=N, rows=16, cols=10):
    yy, xx = np.mgrid[0:n, 0:n]
    rh = n / rows; cw = n / cols; r = (yy // rh).astype(int); off = (r % 2) * cw / 2
    u = ((xx + off) % cw) / cw; v = (yy % rh) / rh
    sc = rng.normal(0, 1, (rows, cols + 1))[r, ((xx + off) // cw).astype(int) % (cols + 1)]
    lum = 200 + sc * 10 - (1 - v) ** 8 * 90 + noise(n) * 20 - (u < 0.03) * 60
    h = v * 0.6 - (u < 0.03) * 0.5 + noise(n) * 0.2
    return np.stack([lum, lum * 1.01, lum * 1.04], -1) + fine(n, 3)[..., None], h
img, h = roof_slate(); save("roof_slate", img); normal_from_height("roof_slate", h, 3.0)

g = 210 + noise(N, (16, 64, 256)) * 30 + fine(N, 10)
save("roof_flat", np.repeat(g[..., None], 3, -1)); normal_from_height("roof_flat", noise(N, (32, 128, 256)) + fine(N, 0.2), 1.0)
seam = ((x % 85) < 6).astype(float)
save("roof_metal", np.repeat((np.tile((225 - seam * 30)[None, :], (N, 1)) + noise(N) * 10)[..., None], 3, -1)); normal_from_height("roof_metal", np.tile(seam[None, :], (N, 1)) * 2, 1.5)

# asphalt
n1 = noise(N, (8, 32, 128), (0.3, 0.3, 0.4))
agg = (rng.random((N, N)) > 0.985) * rng.uniform(20, 60, (N, N))
asph = 70 + n1 * 22 + fine(N, 7) + agg
save("asphalt", np.stack([asph, asph, asph * 1.02], -1)); normal_from_height("asphalt", n1 * 0.5 + fine(N, 0.25), 1.5)
# sidewalk: concrete slabs with joints (tile = 2 m)
sw = 178 + noise(N) * 18 + fine(N, 5)
hh = noise(N) * 0.3
for k in range(0, N, N // 2):
    sw[:, k:k + 3] -= 45; sw[k:k + 3, :] -= 45; hh[:, k:k + 3] -= 0.8; hh[k:k + 3, :] -= 0.8
save("sidewalk", np.stack([sw, sw * 0.99, sw * 0.97], -1)); normal_from_height("sidewalk", hh, 2.0)
# path (stabilised sand / gravel)
pv = 168 + noise(N, (8, 32, 128)) * 30 + fine(N, 12)
save("path", np.stack([pv, pv * 0.93, pv * 0.80], -1)); normal_from_height("path", noise(N) + fine(N, 0.3), 1.5)
gv = 140 + noise(N, (16, 64, 256)) * 30 + fine(N, 18)
save("gravel", np.stack([gv, gv * 0.97, gv * 0.92], -1)); normal_from_height("gravel", fine(N, 0.6), 1.5)
# ballast with sleeper (traverse): u along track (1 sleeper per tile), v across one track
bal = gv.copy()
img = np.stack([bal, bal * 0.97, bal * 0.92], -1)
yy, xx = np.mgrid[0:N, 0:N]
slp = (xx > N * 0.30) & (xx < N * 0.70) & (yy > N * 0.10) & (yy < N * 0.90)
img[slp] = np.stack([105 + noise(N)[slp] * 15] * 3, -1) * np.array([1, 0.95, 0.9])
save("ballast", img); hb = fine(N, 0.5); hb[slp] = 2.0; normal_from_height("ballast", hb, 1.0)
cc = 175 + noise(N) * 25 + fine(N, 6)
save("concrete", np.repeat(cc[..., None], 3, -1)); normal_from_height("concrete", noise(N) * 0.5, 1.0)
rl = 120 + noise(N) * 30
save("rail", np.stack([rl, rl * 0.9, rl * 0.85], -1))
# water normal map (tileable waves)
yy, xx = np.mgrid[0:N, 0:N] / N * 2 * np.pi
wh = sum(np.sin(xx * kx + yy * ky + rng.uniform(0, 6.28)) / (abs(kx) + abs(ky)) for kx, ky in [(3, 1), (1, 4), (5, -2), (-2, 6), (7, 3), (9, -5), (4, 8)])
normal_from_height("water", wh * 0.3, 2.0)
save("water", np.full((8, 8, 3), 255.0))
# bullet hole decal (RGBA)
n = 128; yy, xx = np.mgrid[0:n, 0:n] - n / 2; r = np.hypot(xx, yy) / (n / 2)
a = np.clip(1.4 - r * 1.6, 0, 1) ** 0.7 * (1 + rng.normal(0, 0.15, (n, n)))
c = np.clip(30 + r * 80, 0, 255)
rgba = np.stack([c, c * 0.95, c * 0.9, np.clip(a, 0, 1) * 255], -1); rgba[r < 0.25] = (10, 10, 10, 255)
Image.fromarray(np.clip(rgba, 0, 255).astype(np.uint8), "RGBA").save(os.path.join(OUT, "bullethole.png"))
# muzzle flash (RGBA star)
ang = np.arctan2(yy, xx); star = np.clip(1 - r / (0.45 + 0.4 * np.abs(np.cos(ang * 3)) ** 4), 0, 1)
fl = np.stack([np.full_like(r, 255), 200 + 55 * star, 120 + 100 * star, star ** 1.3 * 255], -1)
Image.fromarray(np.clip(fl, 0, 255).astype(np.uint8), "RGBA").save(os.path.join(OUT, "muzzleflash.png"))
# leaves/bark
lv = 120 + noise(256, (8, 32, 64)) * 80 + fine(256, 20)
save("leaves", np.stack([lv * 0.42, lv * 0.62, lv * 0.30], -1))
bk = 110 + noise(256, (4, 16, 64)) * 40 + fine(256, 15)
save("bark", np.stack([bk * 0.55, bk * 0.45, bk * 0.36], -1))
print("textures ->", OUT, sorted(os.listdir(OUT)))
