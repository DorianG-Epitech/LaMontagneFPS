#!/usr/bin/env python3
"""
build_city.py  --  La Montagne (44620) FPS: turns IGN open data into Unity-ready geometry.

Inputs  ($LAMONTAGNE_DATA, default /workspace/lamontagne/data, produced by fetch_ign.py):
  dem/height_2049.f32  RGE ALTI 1 m resampled to 2 m grid (4096 m square, row0 = south)
  dem/mnh_4096.f32     LiDAR HD canopy height model 1 m
  dem/mns_*.f32        LiDAR HD surface model around the churches (spire heights)
  ortho/*.jpg          BD ORTHO 0.5 m
  vector/*.geojson     BD TOPO V3 (EPSG:2154)
Outputs (<project>/ImportData):
  terrain_height.f32   copy of DEM (2049x2049)
  ortho_t{r}{c}.jpg    terrain textures, 2048 m tiles, 4096 px (0.5 m/px), image top = north
  city.bin             chunked meshes (buildings, roads, sidewalks, paths, rail, water)
  trees.bin            tree instances detected from LiDAR canopy model
  meta.json            materials, landmarks, spawn, stats
Coordinates: Unity x = E - x0, z = N - y0, y = altitude NGF-IGN69 (m). 1 unit = 1 m.
Re-run:  python3 Tools/build_city.py
"""
import os, sys, json, math, struct, hashlib, time
import numpy as np
from PIL import Image
import shapely
from shapely.geometry import shape, Polygon, MultiPolygon, LineString, MultiLineString, box, Point
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
import mapbox_earcut as earcut

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "third_party"))
import mathutils
from bpypolyskel import bpypolyskel

DATA = os.environ.get("LAMONTAGNE_DATA", "/workspace/lamontagne/data")
OUT = os.path.join(os.path.dirname(HERE), "ImportData")
os.makedirs(OUT, exist_ok=True)
REG = json.load(open(os.path.join(DATA, "region.json")))
X0, Y0, SIZE = REG["x0"], REG["y0"], REG["size"]
CHUNK = 256.0
NCH = int(SIZE // CHUNK)
T0 = time.time()
def log(*a): print(f"[{time.time()-T0:6.1f}s]", *a, flush=True)

def hsh(s, mod=1000):
    return int(hashlib.md5(str(s).encode()).hexdigest()[:8], 16) % mod

# ---------------------------------------------------------------- DEM
H = np.fromfile(os.path.join(DATA, "dem", "height_2049.f32"), "<f4").reshape(2049, 2049)
def hgt(x, z):
    """bilinear DEM sample, local metres (x east, z north). Vectorised."""
    x = np.clip(np.asarray(x, float) / 2.0, 0, 2047.999); z = np.clip(np.asarray(z, float) / 2.0, 0, 2047.999)
    i = np.floor(z).astype(int); j = np.floor(x).astype(int); fz = z - i; fx = x - j
    return (H[i, j] * (1 - fx) * (1 - fz) + H[i, j + 1] * fx * (1 - fz) + H[i + 1, j] * (1 - fx) * fz + H[i + 1, j + 1] * fx * fz)

# ---------------------------------------------------------------- ortho mosaic (row0 = north, 0.5 m/px)
def load_ortho():
    n = int(SIZE / (REG["ortho_res"] * REG["ortho_tile_px"])); px = REG["ortho_tile_px"]
    M = np.zeros((n * px, n * px, 3), np.uint8)
    for r in range(n):
        for c in range(n):
            im = np.asarray(Image.open(os.path.join(DATA, "ortho", f"ortho_r{r}_c{c}.jpg")).convert("RGB"))
            M[(n - 1 - r) * px:(n - r) * px, c * px:(c + 1) * px] = im
    return M
ORTHO = load_ortho()
OPX = ORTHO.shape[0]
def write_terrain_textures():
    half = OPX // 2
    for r in range(2):
        for c in range(2):
            crop = ORTHO[(1 - r) * half:(2 - r) * half, c * half:(c + 1) * half]
            Image.fromarray(crop).save(os.path.join(OUT, f"ortho_t{r}{c}.jpg"), quality=92)
            # RGBA copy with alpha = 0: URP Terrain Lit reads smoothness from the diffuse alpha (ortho = matte ground)
            rgba = np.dstack([crop, np.zeros(crop.shape[:2], np.uint8)])
            Image.fromarray(rgba, "RGBA").save(os.path.join(OUT, f"ortho_t{r}{c}.png"), compress_level=3)
    H.astype("<f4").tofile(os.path.join(OUT, "terrain_height.f32"))
    log("terrain textures written")

def ortho_median(poly, erode=0.8):
    g = poly.buffer(-erode)
    if g.is_empty: g = poly
    minx, miny, maxx, maxy = g.bounds
    xs = np.arange(minx, maxx, 0.5); ys = np.arange(miny, maxy, 0.5)
    if len(xs) == 0 or len(ys) == 0: return None
    if len(xs) * len(ys) > 40000:
        xs = np.linspace(minx, maxx, 200); ys = np.linspace(miny, maxy, 200)
    gx, gy = np.meshgrid(xs, ys); gx = gx.ravel(); gy = gy.ravel()
    inside = shapely.contains_xy(g, gx, gy)
    if inside.sum() < 3: return None
    col = np.clip((gx[inside] / 0.5).astype(int), 0, OPX - 1); row = np.clip(OPX - 1 - (gy[inside] / 0.5).astype(int), 0, OPX - 1)
    return np.median(ORTHO[row, col].astype(float), axis=0)

# ---------------------------------------------------------------- mesh accumulation
class Sub:
    __slots__ = ("p", "n", "uv", "i")
    def __init__(self): self.p = []; self.n = []; self.uv = []; self.i = []
CHUNKS = {}   # (layer, cx, cz) -> {mat: Sub}
def sub(layer, cx, cz, mat):
    d = CHUNKS.setdefault((layer, cx, cz), {})
    s = d.get(mat)
    if s is None: s = d[mat] = Sub()
    return s
def chunk_of(x, z):
    return int(min(NCH - 1, max(0, x // CHUNK))), int(min(NCH - 1, max(0, z // CHUNK)))

def add_poly3(s, pts, uvs, normal=None):
    """pts: list of (x, y_north, z_up) math coords, convex/planar polygon fan, CCW seen from the normal side."""
    pts = np.asarray(pts, float)
    if normal is None:
        nrm = np.cross(pts[1] - pts[0], pts[2] - pts[0])
        for k in range(3, len(pts)):
            if np.linalg.norm(nrm) > 1e-9: break
            nrm = np.cross(pts[1] - pts[0], pts[k] - pts[0])
    else: nrm = np.asarray(normal, float)
    ln = np.linalg.norm(nrm)
    if ln < 1e-9: return
    nrm = nrm / ln
    b = len(s.p)
    for p, t in zip(pts, uvs):
        s.p.append((p[0], p[2], p[1])); s.n.append((nrm[0], nrm[2], nrm[1])); s.uv.append(t)
    for k in range(1, len(pts) - 1):
        s.i.extend((b, b + k + 1, b + k))   # reversed winding for Unity's left-handed space

def add_tris(s, P, UV, tris, normal):
    nrm = np.asarray(normal, float); nrm = nrm / np.linalg.norm(nrm)
    b = len(s.p)
    for p, t in zip(P, UV):
        s.p.append((p[0], p[2], p[1])); s.n.append((nrm[0], nrm[2], nrm[1])); s.uv.append(t)
    for k in range(0, len(tris), 3):
        s.i.extend((b + tris[k], b + tris[k + 2], b + tris[k + 1]))

def add_tris_smooth(s, P, N, UV, tris):
    b = len(s.p)
    for p, nn, t in zip(P, N, UV):
        s.p.append((p[0], p[2], p[1])); s.n.append((nn[0], nn[2], nn[1])); s.uv.append(t)
    for k in range(0, len(tris), 3):
        s.i.extend((b + tris[k], b + tris[k + 2], b + tris[k + 1]))

def earcut_poly(poly):
    """poly: shapely Polygon (2D). returns (verts Nx2, tri index list) CCW upward."""
    poly = orient(poly, 1.0)
    rings = [np.asarray(poly.exterior.coords)[:-1, :2]] + [np.asarray(r.coords)[:-1, :2] for r in poly.interiors]
    verts = np.concatenate(rings)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    tris = earcut.triangulate_float64(verts, ends)
    return verts, tris

# ---------------------------------------------------------------- vector loading
def load(name):
    p = os.path.join(DATA, "vector", name + ".geojson")
    return json.load(open(p))["features"] if os.path.exists(p) else []
def local(geom):
    return shapely.transform(geom, lambda c: c - np.array([X0, Y0]))
def local3(coords):
    a = np.asarray(coords, float); a[:, 0] -= X0; a[:, 1] -= Y0; return a
REGION = box(0, 0, SIZE, SIZE)

def polys_of(g):
    if g is None or g.is_empty: return []
    if isinstance(g, Polygon): return [g]
    if isinstance(g, MultiPolygon): return list(g.geoms)
    if hasattr(g, "geoms"): return [q for x in g.geoms for q in polys_of(x)]
    return []

# ---------------------------------------------------------------- materials & palettes
WALL_COLORS = {  # French render ("enduit") shades + stone
    "Render_White": (0.93, 0.92, 0.89), "Render_Cream": (0.93, 0.89, 0.80), "Render_Beige": (0.87, 0.81, 0.70),
    "Render_Grey": (0.80, 0.80, 0.78), "Render_Ochre": (0.88, 0.78, 0.60)}
WALL_W = [("Render_White", 38), ("Render_Cream", 30), ("Render_Beige", 16), ("Render_Grey", 10), ("Render_Ochre", 6)]
def pick_wall(key):
    r = hsh(key, 100); acc = 0
    for n, w in WALL_W:
        acc += w
        if r < acc: return n
    return "Render_White"

ROOF_PALETTE = {}   # filled by k-means: name -> rgb (0..1)
def build_roof_palettes(samples):
    """samples: list of (kind, rgb). k-means per kind -> palette entries Kind_i."""
    rng = np.random.default_rng(1)
    pal = {}
    for kind, k in (("Tile", 6), ("Slate", 3), ("Flat", 3), ("Metal", 2)):
        X = np.array([c for kd, c in samples if kd == kind and c is not None], float)
        if len(X) < k: X = np.concatenate([X, np.full((k, 3), 128.0)]) if len(X) else np.full((k, 3), 128.0)
        C = X[rng.choice(len(X), k, replace=False)]
        for _ in range(25):
            lab = np.argmin(((X[:, None, :] - C[None]) ** 2).sum(-1), 1)
            for i in range(k):
                if (lab == i).any(): C[i] = X[lab == i].mean(0)
        for i in range(k): pal[f"Roof{kind}_{i}"] = C[i]
    return pal
def roof_mat(kind, rgb):
    if rgb is None: rgb = {"Tile": (170, 95, 70), "Slate": (80, 85, 92), "Flat": (140, 140, 138), "Metal": (150, 150, 150)}[kind]
    best, bd = None, 1e18
    for n, c in ROOF_PALETTE.items():
        if not n.startswith("Roof" + kind + "_"): continue
        d = ((np.asarray(rgb) - c) ** 2).sum()
        if d < bd: best, bd = n, d
    return best

def classify_roof_color(rgb):
    if rgb is None: return None
    r, g, b = rgb
    if r > g * 1.12 and r > b * 1.25 and r > 90: return "Tile"
    if max(r, g, b) < 115: return "Slate"
    return "Flat"

# ---------------------------------------------------------------- buildings
WALL_SINK = 0.8
BAY, FLOOR = 3.0, 3.0
STATS = {}

def offset_poly(poly, d):
    g = poly.buffer(d, join_style=2, mitre_limit=2.5)
    g = polys_of(g)
    if not g: return None
    g = max(g, key=lambda q: q.area).simplify(0.05)
    return orient(Polygon(g.exterior), 1.0)

def clean_ring(poly, tol=0.35):
    p = poly.simplify(tol, preserve_topology=True)
    if p.is_empty or not isinstance(p, Polygon) or p.area < 4: return None
    p = orient(Polygon(p.exterior), 1.0)
    c = np.asarray(p.exterior.coords)[:-1]
    # drop near-collinear vertices
    keep = []
    n = len(c)
    for k in range(n):
        a, b, d = c[k - 1], c[k], c[(k + 1) % n]
        v1 = b - a; v2 = d - b
        cr = abs(v1[0] * v2[1] - v1[1] * v2[0]) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9)
        if cr > 0.04: keep.append(b)
    if len(keep) < 3: return None
    q = Polygon(keep)
    if not q.is_valid or q.area < 4: return None
    return orient(q, 1.0)

def walls(cx, cz, mat, ring, y0, y1s, ground, uscale=BAY, vscale=FLOOR, fa=None):
    """ring: Nx2 CCW; y1s: top height per vertex (array) or scalar.
    Materials 'Fa_<style>' use a 4-column atlas (window | door | shopfront | garage), one 3 m bay per quad;
    fa = dict(fh=floor height, front=edge index, gf={bay: column}) chooses the ground-floor bays of the street facade."""
    s = sub("bld", cx, cz, mat)
    n = len(ring)
    y1s = np.broadcast_to(np.asarray(y1s, float), (n,))
    atlas = mat.startswith("Fa_")
    fh = fa["fh"] if fa else FLOOR
    for k in range(n):
        a = ring[k]; b = ring[(k + 1) % n]
        L = float(np.hypot(*(b - a)))
        if L < 0.05: continue
        nrm = (b[1] - a[1], -(b[0] - a[0]), 0.0)
        ta, tb = y1s[k], y1s[(k + 1) % n]
        if not atlas:
            nb = max(1, round(L / uscale)) if (uscale == BAY or mat == "ChurchWall") else L / uscale
            v0 = (y0 - ground) / vscale
            add_poly3(s, [(a[0], a[1], y0), (b[0], b[1], y0), (b[0], b[1], tb), (a[0], a[1], ta)],
                      [(0, v0), (nb, v0), (nb, (tb - ground) / vscale), (0, (ta - ground) / vscale)], nrm)
            continue
        nb = max(1, round(L / BAY))
        gfcols = fa["gf"] if (fa and fa.get("front") == k) else {}
        for j in range(nb):
            p0 = a + (b - a) * (j / nb); p1 = a + (b - a) * ((j + 1) / nb)
            t0 = ta + (tb - ta) * (j / nb); t1 = ta + (tb - ta) * ((j + 1) / nb)
            col = gfcols.get(j, 0)
            u0, u1 = col * 0.25 + 0.003, (col + 1) * 0.25 - 0.003
            ygf = min(ground + fh, min(t0, t1))
            add_poly3(s, [(p0[0], p0[1], y0), (p1[0], p1[1], y0), (p1[0], p1[1], ygf), (p0[0], p0[1], ygf)],
                      [(u0, (y0 - ground) / fh), (u1, (y0 - ground) / fh), (u1, (ygf - ground) / fh), (u0, (ygf - ground) / fh)], nrm)
            if t0 > ygf + 0.01 or t1 > ygf + 0.01:
                w0, w1 = 0.003, 0.247
                add_poly3(s, [(p0[0], p0[1], ygf), (p1[0], p1[1], ygf), (p1[0], p1[1], t1), (p0[0], p0[1], t0)],
                          [(w0, (ygf - ground) / fh), (w1, (ygf - ground) / fh), (w1, (t1 - ground) / fh), (w0, (t0 - ground) / fh)], nrm)

def flat_cap(cx, cz, mat, poly, y, layer="bld", uv_scale=4.0, down=False):
    s = sub(layer, cx, cz, mat)
    v, t = earcut_poly(poly)
    if len(t) == 0: return
    P = np.column_stack([v, np.full(len(v), y)])
    UV = v / uv_scale
    if down:
        t = np.asarray(t).reshape(-1, 3)[:, ::-1].ravel()
        add_tris(s, P, UV, list(t), (0, 0, -1))
    else:
        add_tris(s, P, UV, list(t), (0, 0, 1))

def roof_face(s, P3, base_a, base_b, tex=2.0):
    """P3: list of 3D points (x, y, z) of a planar roof face; base edge a->b for UV orientation."""
    P3 = np.asarray(P3, float)
    e = np.asarray(base_b[:2], float) - np.asarray(base_a[:2], float); e /= (np.linalg.norm(e) + 1e-12)
    perp = np.array([-e[1], e[0]])
    rel = P3[:, :2] - np.asarray(base_a[:2])
    u = rel @ e; w = rel @ perp
    dz = P3[:, 2] - base_a[2]
    v = np.sqrt(w ** 2 + dz ** 2) * np.sign(w + 1e-9)
    UV = np.column_stack([u / tex, v / tex])
    tris = earcut.triangulate_float64(P3[:, :2], np.array([len(P3)], np.uint32))
    if len(tris) == 0: return
    nrm = np.cross(P3[1] - P3[0], P3[2] - P3[0])
    # robust normal: Newell
    nx = ny = nz = 0.0
    for k in range(len(P3)):
        a = P3[k]; b = P3[(k + 1) % len(P3)]
        nx += (a[1] - b[1]) * (a[2] + b[2]); ny += (a[2] - b[2]) * (a[0] + b[0]); nz += (a[0] - b[0]) * (a[1] + b[1])
    nrm = np.array([nx, ny, nz])
    if nrm[2] < 0:   # face polygon was CW from top -> flip
        P3 = P3[::-1]; UV = UV[::-1]; nrm = -nrm
        tris = earcut.triangulate_float64(P3[:, :2], np.array([len(P3)], np.uint32))
    add_tris(s, P3, UV, list(tris), nrm)

def hip_roof(cx, cz, mat, poly_o, base_y, tanp):
    verts = [mathutils.Vector((float(x), float(y), 0.0)) for x, y in np.asarray(poly_o.exterior.coords)[:-1]]
    n = len(verts)
    faces = bpypolyskel.polygonize(verts, 0, n, None, 0.0, tanp, None, None)
    if not faces: raise RuntimeError("no faces")
    s = sub("bld", cx, cz, mat)
    for f in faces:
        P = [(verts[i].x, verts[i].y, base_y + verts[i].z) for i in f]
        if len(P) < 3: continue
        roof_face(s, P, P[0], P[1])

def gable_roof(cx, cz, mat, ring, base_y_wall, eave, ridge_h, ovh, tanp):
    """ring: 4x2 CCW quad (walls). builds 2 slopes with overhang + 2 gable wall triangles (returned)."""
    L = [np.linalg.norm(ring[(k + 1) % 4] - ring[k]) for k in range(4)]
    o = 0 if L[0] + L[2] <= L[1] + L[3] else 1
    p = [ring[(o + k) % 4] for k in range(4)]   # short edges: p0p1, p2p3
    r0 = (p[0] + p[1]) / 2; r1 = (p[2] + p[3]) / 2
    ax = r1 - r0; ax /= np.linalg.norm(ax)
    ytop = eave + ridge_h
    # overhang: push slope eaves outward perpendicular, ridge ends outward along axis
    def outw(a, b):
        e = (b - a) / np.linalg.norm(b - a); return np.array([e[1], -e[0]])
    n12 = outw(p[1], p[2]); n30 = outw(p[3], p[0])
    yb = eave - ovh * tanp
    q1 = p[1] + n12 * ovh - ax * ovh; q2 = p[2] + n12 * ovh + ax * ovh
    q3 = p[3] + n30 * ovh + ax * ovh; q0 = p[0] + n30 * ovh - ax * ovh
    R0 = r0 - ax * ovh; R1 = r1 + ax * ovh
    s = sub("bld", cx, cz, mat)
    A = [(q1[0], q1[1], yb), (q2[0], q2[1], yb), (R1[0], R1[1], ytop), (R0[0], R0[1], ytop)]
    B = [(q3[0], q3[1], yb), (q0[0], q0[1], yb), (R0[0], R0[1], ytop), (R1[0], R1[1], ytop)]
    roof_face(s, A, A[0], A[1]); roof_face(s, B, B[0], B[1])
    # underside (soffit) faces so the roof is not see-through from below
    for F in (A, B):
        G = [(x, y, z - 0.12) for x, y, z in F[::-1]]
        add_poly3(s, G, [(0, 0)] * 4)
    return p, r0, r1, ytop

CHURCH_C = np.array([1976.0, 2300.0])
BOURG_STYLES = [("bourg_white_brick", 35), ("bourg_grey_tuffeau", 30), ("bourg_moellon", 15), ("bourg_cream", 20)]
PAV_STYLES = [("pav_white_brown", 30), ("pav_white_roller", 35), ("pav_cream_blue", 15), ("pav_grey_white", 20)]
def _pick(key, lst):
    r = hsh(str(key) + "s", 100); acc = 0
    for n, w in lst:
        acc += w
        if r < acc: return n
    return lst[0][0]
# facade styles observed on Panoramax street photos (v2/refs): (x, z, radius m, style)
REF_STYLES = [(1812.5, 2482.7, 9.0, "bourg_moellon"),      # rubble-stone houses with brick arches next to the mairie (ref D)
              (2027.0, 2403.9, 9.0, "bourg_white_brick")]   # rendered terraced houses, brick surrounds, burgundy shutters (ref C)
def pick_style(key, c, floors, usage, wm):
    for rx, rz, rr, st in REF_STYLES:
        if math.hypot(c.x - rx, c.y - rz) < rr: return st
    d = math.hypot(c.x - CHURCH_C[0], c.y - CHURCH_C[1])
    old = wm.startswith("1") or wm.startswith("2")   # pierre / meuliere (fichiers fonciers): rendered rubble-stone houses
    if old and d < 900: return _pick(key, [("bourg_white_brick", 30), ("bourg_grey_tuffeau", 30), ("bourg_moellon", 20), ("bourg_cream", 20)])
    bourg = d < 220 or (d < 420 and (floors or 1) >= 2) or (usage == "Commercial et services" and d < 600)
    return _pick(key, BOURG_STYLES if bourg else PAV_STYLES)

ROAD_TREE = None; ROAD_LINES = []; SHOPS = []; SHOP_SIGNS = []
def init_context():
    """street centrelines (front facade detection) and OSM shops (shopfronts + signs)."""
    global ROAD_TREE, ROAD_LINES, SHOPS
    for f in load("troncon_de_route"):
        if f["properties"].get("nature") not in PAVED: continue
        for co in ([f["geometry"]["coordinates"]] if f["geometry"]["type"] == "LineString" else f["geometry"]["coordinates"]):
            ROAD_LINES.append(LineString(local3(co)[:, :2]))
    ROAD_TREE = shapely.STRtree(ROAD_LINES)
    for e in OSM:
        t = e.get("tags", {})
        if e["type"] != "node" and e["type"] != "way": continue
        kind = t.get("shop") or (t.get("amenity") if t.get("amenity") in ("pharmacy", "bank", "restaurant", "cafe", "bar", "fast_food", "post_office") else None)
        if not kind or not t.get("name"): continue
        p = osm_point(e)
        if p is not None: SHOPS.append(dict(name=t["name"], kind=kind, p=p))
    log("context: road lines", len(ROAD_LINES), "named shops", len(SHOPS))

def facade_plan(key, ring, ring_poly, ground, Hh, floors, usage, annex, wmat):
    fh = 2.9
    if floors and floors >= 1: fh = float(np.clip((Hh - 1.5) / floors, 2.6, 3.3)) if Hh > 3 else 2.8
    fa = dict(fh=fh, front=None, gf={})
    if not wmat.startswith("Fa_") or annex: return fa
    n = len(ring); best = None
    for k in range(n):
        a = ring[k]; b = ring[(k + 1) % n]; L = float(np.hypot(*(b - a)))
        if L < 2.6: continue
        m = (a + b) / 2; nrm = np.array([b[1] - a[1], -(b[0] - a[0])]) / L
        probe = Point(*(m + nrm * 4))
        i = ROAD_TREE.nearest(probe); d = ROAD_LINES[i].distance(Point(*m))
        if d < 22 and (best is None or d < best[0]): best = (d, k, L)
    if best is None: return fa
    _, k, L = best; nb = max(1, round(L / BAY)); fa["front"] = k
    shop = None
    for sh in SHOPS:
        if not sh.get("used") and ring_poly.buffer(4).contains(sh["p"]): shop = sh; break
    if shop is not None and nb >= 1:
        for j in range(nb): fa["gf"][j] = 2
        if nb >= 3: fa["gf"][nb // 2] = 1
        a = ring[k]; b = ring[(k + 1) % n]
        SHOP_SIGNS.append(dict(name=shop["name"], kind=shop["kind"], a=a, b=b, y=ground + fh - 0.05))
        STATS.setdefault("shop_sign_list", []).append(dict(name=shop["name"], x=float((a[0] + b[0]) / 2), z=float((a[1] + b[1]) / 2),
                                                            nx=float((b[1] - a[1]) / L), nz=float(-(b[0] - a[0]) / L)))
        shop["used"] = True
        return fa
    door = nb // 2
    fa["gf"][door] = 1
    pav = "pav_" in wmat
    if pav and nb >= 3 and hsh(str(key) + "g", 100) < 55:
        fa["gf"][0 if door != 0 else nb - 1] = 3
    return fa

def build_buildings():
    feats = load("batiment")
    log("buildings:", len(feats))
    recs = []
    samples = []
    for f in feats:
        pr = f["properties"]
        if pr.get("etat_de_l_objet") not in (None, "En service"): continue
        g = local(shape(f["geometry"]))
        g = g.intersection(REGION)
        for part in polys_of(g):
            if part.area < 6: continue
            rgb = ortho_median(part)
            recs.append((pr, part, rgb))
    # roof kind decision
    def code_kind(c):
        if not c: return None
        for ch in str(c):
            if ch == "1": return "Tile"
            if ch == "2": return "Slate"
            if ch == "3": return "Metal"
            if ch == "4": return "Flat"
        return None
    kinds = []
    for pr, part, rgb in recs:
        nature = pr.get("nature") or ""; usage = pr.get("usage_1") or ""
        area = part.area
        mrr = part.minimum_rotated_rectangle
        cs = np.asarray(mrr.exterior.coords)
        e1 = np.linalg.norm(cs[1] - cs[0]); e2 = np.linalg.norm(cs[2] - cs[1]); w = min(e1, e2)
        kind = code_kind(pr.get("materiaux_de_la_toiture")) or classify_roof_color(rgb) or "Tile"
        colk = classify_roof_color(rgb)
        if colk == "Tile" and kind in ("Slate",) and rgb is not None and rgb[0] > 140: kind = "Tile"
        industrial = nature.startswith("Industriel") or usage in ("Industriel", "Agricole") or (usage == "Commercial et services" and area > 400)
        flat = (industrial and area > 250) or area > 1500 or w > 24 or kind == "Flat" and (area > 120 or colk == "Flat") or nature == "Serre"
        if industrial and not flat: kind = "Metal" if colk != "Tile" else "Tile"
        if flat and kind not in ("Flat", "Metal"): kind = "Flat"
        kinds.append((kind, flat, industrial, w))
        samples.append((kind, rgb))
    global ROOF_PALETTE
    ROOF_PALETTE = build_roof_palettes(samples)
    log("roof palette", {k: [round(x) for x in v] for k, v in ROOF_PALETTE.items()})
    nb = {"gable": 0, "hip": 0, "flat": 0, "fail": 0, "skipped": 0}
    churches = []
    for (pr, part, rgb), (kind, flat, industrial, w) in zip(recs, kinds):
        nature = pr.get("nature") or ""; usage = pr.get("usage_1") or ""
        key = pr.get("cleabs")
        if nature == "Eglise" and usage == "Religieux":
            churches.append((pr, part)); continue
        ring_poly = clean_ring(part, 0.3 if part.area < 400 else 0.5)
        if ring_poly is None: nb["skipped"] += 1; continue
        ring = np.asarray(ring_poly.exterior.coords)[:-1]
        c = ring_poly.centroid; cx, cz = chunk_of(c.x, c.y)
        gz = hgt(ring[:, 0], ring[:, 1]); ground = float(gz.min()); gmax = float(gz.max())
        base = ground - WALL_SINK
        floors = pr.get("nombre_d_etages")
        Hh = pr.get("hauteur")
        annex = usage == "Annexe" or (part.area < 30 and not floors)
        if not Hh or Hh <= 1.0:
            Hh = (floors * 3.0 + 2.0) if floors else (3.0 if annex else (8.0 if industrial else 6.0))
        Hh = float(Hh) + (gmax - ground) * 0.5
        # wall material
        wm = pr.get("materiaux_des_murs") or ""
        if nature == "Serre": wmat = "Glass"
        elif industrial: wmat = "Industrial_" + ("Grey", "Blue", "White")[hsh(key, 3)]
        elif nature == "Château": wmat = "Stone"
        elif annex: wmat = "Plain_" + pick_wall(key)
        else: wmat = "Fa_" + pick_style(key, c, floors, usage, wm)
        fa = facade_plan(key, ring, ring_poly, ground, Hh, floors, usage, annex, wmat)
        if flat:
            top = ground + Hh
            walls(cx, cz, wmat, ring, base, top, ground, fa=fa)
            rm = "Glass" if nature == "Serre" else roof_mat(kind if kind in ("Flat", "Metal") else "Flat", rgb)
            flat_cap(cx, cz, rm, ring_poly, top)
            # parapet / acrotere for flat roofs of real buildings
            if not annex and part.area > 60 and nature != "Serre":
                inner = offset_poly(ring_poly, -0.25)
                if inner is not None and inner.area > 10:
                    ir = np.asarray(inner.exterior.coords)[:-1][::-1]
                    pm = wmat if not wmat.startswith("Fa_") else "GardenWall"
                    walls(cx, cz, pm, ir, top - 0.01, top + 0.5, ground, 4.0, 4.0)  # inner face
                    walls(cx, cz, pm, ring, top, top + 0.5, ground, 4.0, 4.0)
                    rim = ring_poly.difference(inner)
                    for rp in polys_of(rim): flat_cap(cx, cz, pm, rp, top + 0.5)
            nb["flat"] += 1
            continue
        pitch = {"Tile": 30.0, "Slate": 42.0, "Metal": 15.0, "Flat": 8.0}[kind]
        mrr = ring_poly.minimum_rotated_rectangle
        cs = np.asarray(mrr.exterior.coords)
        wmin = min(np.linalg.norm(cs[1] - cs[0]), np.linalg.norm(cs[2] - cs[1]))
        roof_h = (wmin / 2) * math.tan(math.radians(pitch))
        eave_min = 2.4 if annex else (2.7 if not floors or floors <= 1 else floors * 2.75)
        eave_rel = max(eave_min, Hh - roof_h)
        roof_h = max(0.6, min(roof_h, Hh - eave_rel)) if Hh - eave_rel > 0.6 else min(roof_h, 0.6 + 0.15 * wmin)
        tanp = roof_h / max(wmin / 2, 0.5)
        eave = ground + eave_rel
        rmat = roof_mat(kind, rgb)
        ovh = 0.0 if annex else 0.35
        rect = len(ring) == 4 and ring_poly.area / max(mrr.area, 1e-6) > 0.93
        try:
            if rect and not annex and hsh(key, 10) < 6:
                walls(cx, cz, wmat, ring, base, eave, ground, fa=fa)
                p, r0, r1, ytop = gable_roof(cx, cz, rmat, ring, base, eave, roof_h, ovh, tanp)
                gs = sub("bld", cx, cz, wmat)
                for a, b, r in ((p[0], p[1], r0), (p[2], p[3], r1)):
                    nrm = (b[1] - a[1], -(b[0] - a[0]), 0.0)
                    us = 0.247 if wmat.startswith("Fa_") else 1.0; fhh = fa["fh"]
                    add_poly3(gs, [(a[0], a[1], eave), (b[0], b[1], eave), (r[0], r[1], ytop)],
                              [(0.003, (eave - ground) / fhh), (us, (eave - ground) / fhh), (us / 2, (ytop - ground) / fhh)], nrm)
                nb["gable"] += 1
            else:
                po = offset_poly(ring_poly, ovh) if ovh > 0 else ring_poly
                if po is None: raise RuntimeError("offset")
                walls(cx, cz, wmat, ring, base, eave, ground, fa=fa)
                hip_roof(cx, cz, rmat, po, eave - ovh * tanp, tanp)
                if ovh > 0: flat_cap(cx, cz, rmat, po, eave - ovh * tanp - 0.05, down=True)
                nb["hip"] += 1
        except Exception as ex:
            flat_cap(cx, cz, roof_mat("Flat", rgb), ring_poly, eave)
            nb["fail"] += 1
    STATS["buildings"] = nb
    log("buildings done", nb)
    return churches

def build_churches(churches):
    """Churches: stone nave with steep slate roof + bell tower & spire at the BD TOPO 'Clocher' point, height from LiDAR MNS."""
    clochers = [local(shape(f["geometry"])) for f in load("construction_ponctuelle") if f["properties"]["nature"] == "Clocher"]
    mns = {}
    for name, cxm, cym in [("notre_dame", 1985, 2310), ("saint_hermeland", 3065, 3190)]:
        p = os.path.join(DATA, "dem", f"mns_{name}.f32")
        if os.path.exists(p): mns[(cxm, cym)] = np.fromfile(p, "<f4").reshape(512, 512)
    out = []
    for pr, part in churches:
        ring_poly = clean_ring(part, 0.5)
        if ring_poly is None: continue
        ring = np.asarray(ring_poly.exterior.coords)[:-1]
        c = ring_poly.centroid; cx, cz = chunk_of(c.x, c.y)
        ground = float(hgt(ring[:, 0], ring[:, 1]).min())
        Hh = float(pr.get("hauteur") or 12.0)
        mrr = ring_poly.minimum_rotated_rectangle; cs = np.asarray(mrr.exterior.coords)
        wmin = min(np.linalg.norm(cs[1] - cs[0]), np.linalg.norm(cs[2] - cs[1]))
        tanp = math.tan(math.radians(50))
        roof_h = min(wmin / 2 * tanp, 0.55 * Hh)
        eave = ground + Hh - roof_h
        tanp = roof_h / (wmin / 2)
        walls(cx, cz, "ChurchStone", ring, ground - WALL_SINK, eave, ground, 4.0, 4.0)
        po = offset_poly(ring_poly, 0.4)
        try:
            hip_roof(cx, cz, "RoofSlate_Church", po, eave - 0.4 * tanp, tanp)
            flat_cap(cx, cz, "RoofSlate_Church", po, eave - 0.4 * tanp - 0.05, down=True)
        except Exception:
            flat_cap(cx, cz, "RoofSlate_Church", ring_poly, eave)
        # tower
        cl = [q for q in clochers if ring_poly.buffer(8).contains(q)]
        if not cl: continue
        q = cl[0]
        top = None; half = 3.0
        for (mx, my), a in mns.items():
            if abs(mx - q.x) < 100 and abs(my - q.y) < 100:
                ys, xs = np.mgrid[0:512, 0:512]
                wx = mx - 128 + xs / 2.0; wy = my - 128 + ys / 2.0
                near = (np.hypot(wx - q.x, wy - q.y) < 12)
                top = float(a[near].max())
                tall = near & (a > ground + Hh + 2)
                if tall.any(): half = float(np.clip(math.sqrt(tall.sum() * 0.25) / 2, 2.5, 4.5))
                # tower centre = centroid of tall cells
                if tall.any(): q = Point(float(wx[tall].mean()), float(wy[tall].mean()))
        if top is None: top = ground + Hh * 2.4
        total = top - ground
        tower_top = ground + total * 0.55
        # align tower square with the nave's main axis
        e = cs[1] - cs[0] if np.linalg.norm(cs[1] - cs[0]) > np.linalg.norm(cs[2] - cs[1]) else cs[2] - cs[1]
        e = e / np.linalg.norm(e); pe = np.array([-e[1], e[0]])
        ctr = np.array([q.x, q.y])
        sq = np.array([ctr + half * (sx * e + sy * pe) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
        sqp = orient(Polygon(sq), 1.0); sq = np.asarray(sqp.exterior.coords)[:-1]
        tx, tz = chunk_of(q.x, q.y)
        walls(tx, tz, "ChurchStone", sq, ground - WALL_SINK, tower_top, ground, 4.0, 4.0)
        # belfry cornice
        sq2 = np.asarray(offset_poly(sqp, 0.3).exterior.coords)[:-1]
        walls(tx, tz, "ChurchStone", sq2, tower_top - 0.6, tower_top, ground, 4.0, 4.0)
        # octagonal slate spire
        s = sub("bld", tx, tz, "RoofSlate_Church")
        rr = half * 1.0
        oc = [ctr + rr * (math.cos(a) * e + math.sin(a) * pe) for a in np.arange(8) * math.pi / 4 + math.pi / 8]
        apex = (ctr[0], ctr[1], top)
        for k in range(8):
            a = oc[k]; b = oc[(k + 1) % 8]
            add_poly3(s, [(a[0], a[1], tower_top), (b[0], b[1], tower_top), apex], [(0, 0), (1, 0), (0.5, (top - tower_top) / 2)])
        flat_cap(tx, tz, "ChurchStone", Polygon(sq2), tower_top)
        out.append(dict(name=pr.get("cleabs"), x=float(q.x), z=float(q.y), ground=ground, spire_top=top))
        log("church", round(q.x), round(q.y), "spire", round(top - ground, 1), "m above ground")
    STATS["churches"] = out
    return out

# ---------------------------------------------------------------- roads / sidewalks / paths
ROAD_W = {"Route à 1 chaussée": 5.0, "Route à 2 chaussées": 7.0, "Rond-point": 6.0, "Bretelle": 5.0, "Type autoroutier": 7.5,
          "Route empierrée": 3.0, "Chemin": 2.5, "Sentier": 1.3, "Escalier": 2.0}
PAVED = {"Route à 1 chaussée", "Route à 2 chaussées", "Rond-point", "Bretelle", "Type autoroutier"}
GRID = 4.0
def drape(layer, mat, geom, dy, uv_scale, skirt=None):
    """clip polygons to a 4 m grid and drape on the DEM (+dy). skirt: drop height for boundary walls (curbs)."""
    polys = polys_of(geom)
    if not polys: return 0
    nverts = 0
    for poly in polys:
        minx, miny, maxx, maxy = poly.bounds
        xs = np.arange(math.floor(minx / GRID) * GRID, maxx, GRID); ys = np.arange(math.floor(miny / GRID) * GRID, maxy, GRID)
        cells = shapely.box(*np.meshgrid(xs, ys), *np.meshgrid(xs + GRID, ys + GRID)) if len(xs) and len(ys) else []
        if len(cells) == 0: continue
        cells = np.asarray(cells).ravel()
        shapely.prepare(poly)
        hit = shapely.intersects(poly, cells)
        pieces = shapely.intersection(poly, cells[hit])
        for pc in pieces:
            for pp in polys_of(pc):
                if pp.area < 0.01: continue
                c = pp.representative_point(); cx, cz = chunk_of(c.x, c.y)
                v, t = earcut_poly(pp)
                if len(t) == 0: continue
                y = hgt(v[:, 0], v[:, 1]) + dy
                P = np.column_stack([v, y])
                add_tris(sub(layer, cx, cz, mat), P, v / uv_scale, list(t), (0, 0, 1))
                nverts += len(v)
        if skirt:
            for ring in [poly.exterior] + list(poly.interiors):
                r = ring.segmentize(GRID)
                cc = np.asarray(r.coords)
                if not r.is_ccw if ring is poly.exterior else r.is_ccw: pass
                # outward: exterior is CCW after orient -> normal (dy,-dx)
                o = orient(poly, 1.0)
                for k in range(len(cc) - 1):
                    a, b = cc[k], cc[k + 1]
                    m = (a + b) / 2; cx, cz = chunk_of(m[0], m[1])
                    ya, yb = float(hgt(a[0], a[1])) + dy, float(hgt(b[0], b[1])) + dy
                    nrm = np.array([b[1] - a[1], -(b[0] - a[0]), 0.0])
                    # make normal point away from polygon interior
                    test = Point(m[0] + nrm[0] * 0.05 / (np.linalg.norm(nrm) + 1e-9), m[1] + nrm[1] * 0.05 / (np.linalg.norm(nrm) + 1e-9))
                    if poly.contains(test): a, b = b, a; ya, yb = yb, ya; nrm = -nrm
                    L = float(np.hypot(*(b - a)))
                    add_poly3(sub(layer, cx, cz, "Curb"), [(a[0], a[1], ya - skirt), (b[0], b[1], yb - skirt), (b[0], b[1], yb), (a[0], a[1], ya)],
                              [(0, 0), (L, 0), (L, 0.15), (0, 0.15)], nrm)
    return nverts

def build_roads(building_union, water_union):
    feats = load("troncon_de_route")
    road, side, path, bridges = [], [], [], []
    for f in feats:
        pr = f["properties"]; nat = pr.get("nature")
        if nat not in ROAD_W or f["geometry"] is None: continue
        w = pr.get("largeur_de_chaussee") or 0
        if not w or w <= 0: w = ROAD_W[nat]
        w = float(min(max(w, 1.0), 12.0))
        if nat == "Route à 2 chaussées" and not pr.get("largeur_de_chaussee"): w = 7.0
        geoms = [f["geometry"]["coordinates"]] if f["geometry"]["type"] == "LineString" else f["geometry"]["coordinates"]
        for co in geoms:
            a3 = local3(co)
            ls = LineString(a3[:, :2])
            if pr.get("position_par_rapport_au_sol") not in (None, "0"):
                bridges.append((a3, w, nat)); continue
            if nat in PAVED:
                ROAD_SEGS.append((ls, w, nat, pr))
                road.append(ls.buffer(w / 2, cap_style=1, quad_segs=3))
                if pr.get("urbain") and nat not in ("Type autoroutier", "Bretelle") and pr.get("importance") in ("2", "3", "4", "5"):
                    side.append(ls.buffer(w / 2 + 1.7, cap_style=1, quad_segs=3))
            else:
                path.append(ls.buffer(w / 2, cap_style=1, quad_segs=3))
    log("roads: paved", len(road), "side", len(side), "paths", len(path), "bridges", len(bridges))
    RU = unary_union(road).intersection(REGION)
    SW = unary_union(side).difference(RU).difference(building_union).difference(water_union).intersection(REGION).buffer(0)
    SW = unary_union([p for p in polys_of(SW) if p.area > 2.0])
    PU = unary_union(path).difference(RU).difference(SW).intersection(REGION)
    log("road union area m2", round(RU.area), "sidewalk", round(SW.area), "paths", round(PU.area))
    n1 = drape("road", "Road", RU, 0.10, 6.0)
    n2 = drape("road", "Sidewalk", SW, 0.24, 2.0, skirt=0.16)
    n3 = drape("road", "Path", PU, 0.07, 3.0)
    log("road verts", n1, n2, n3)
    PAVED_GEOMS.extend([RU, SW, PU])
    # bridges: ribbons on BD TOPO 3D geometry
    for a3, w, nat in bridges:
        ls = LineString(a3[:, :2]).segmentize(3.0)
        cc = np.asarray(ls.coords)
        # interpolate z along original vertices
        d0 = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(a3[:, :2], axis=0).T))])
        d1 = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(cc, axis=0).T))])
        z = np.interp(d1, d0, a3[:, 2]) if a3.shape[1] > 2 else hgt(cc[:, 0], cc[:, 1])
        z = np.maximum(z, hgt(cc[:, 0], cc[:, 1]))
        for k in range(len(cc) - 1):
            a, b = cc[k], cc[k + 1]
            e = (b - a); L = np.linalg.norm(e)
            if L < 0.01: continue
            e /= L; nrm = np.array([-e[1], e[0]]) * (w / 2 + 0.3)
            m = (a + b) / 2; cx, cz = chunk_of(*m)
            mat = "Road" if nat in PAVED else "Path"
            za, zb = z[k] + 0.1, z[k + 1] + 0.1
            pts = [(a[0] - nrm[0], a[1] - nrm[1], za), (b[0] - nrm[0], b[1] - nrm[1], zb), (b[0] + nrm[0], b[1] + nrm[1], zb), (a[0] + nrm[0], a[1] + nrm[1], za)]
            add_poly3(sub("road", cx, cz, mat), pts, [(p[0] / 6, p[1] / 6) for p in pts])
            # parapets + deck sides
            for sgn in (-1, 1):
                pa = a + sgn * nrm; pb = b + sgn * nrm
                side_n = (sgn * nrm[0], sgn * nrm[1], 0)
                quad = [(pa[0], pa[1], za - 0.9), (pb[0], pb[1], zb - 0.9), (pb[0], pb[1], zb + 1.0), (pa[0], pa[1], za + 1.0)]
                if sgn < 0: quad = [quad[1], quad[0], quad[3], quad[2]]
                add_poly3(sub("road", cx, cz, "Concrete"), quad, [(0, 0), (L / 2, 0), (L / 2, 1), (0, 1)], side_n)
                inner = [(q[0] - sgn * nrm[0] * 0.04, q[1] - sgn * nrm[1] * 0.04, q[2]) for q in quad[::-1]]
                add_poly3(sub("road", cx, cz, "Concrete"), [(x, y, max(zz, (za + zb) / 2)) for x, y, zz in inner], [(0, 0), (L / 2, 0), (L / 2, 1), (0, 1)])
    STATS["roads"] = dict(paved_m2=round(RU.area), sidewalk_m2=round(SW.area), paths_m2=round(PU.area), bridges=len(bridges))
    return RU

# ---------------------------------------------------------------- rail
def build_rail():
    feats = load("troncon_de_voie_ferree")
    n = 0
    for f in feats:
        pr = f["properties"]
        if pr.get("etat_de_l_objet") not in (None, "En service"): continue
        nt = int(pr.get("nombre_de_voies") or 1) if str(pr.get("nombre_de_voies") or "1").isdigit() else 1
        nt = max(1, min(nt, 2))
        a3 = local3(f["geometry"]["coordinates"])
        ls = LineString(a3[:, :2]).intersection(REGION)
        for part in ([ls] if isinstance(ls, LineString) else list(getattr(ls, "geoms", []))):
            if part.is_empty or part.length < 1: continue
            cc = np.asarray(part.segmentize(4.0).coords)
            z = hgt(cc[:, 0], cc[:, 1])
            if pr.get("position_par_rapport_au_sol") not in (None, "0") and a3.shape[1] > 2:
                d0 = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(a3[:, :2], axis=0).T))])
                z = np.maximum(z, np.interp(np.linspace(0, d0[-1], len(cc)), d0, a3[:, 2]))
            W = 3.2 * nt
            dist = 0.0
            for k in range(len(cc) - 1):
                a, b = cc[k], cc[k + 1]; e = b - a; L = np.linalg.norm(e)
                if L < 0.01: continue
                e /= L; pp = np.array([-e[1], e[0]])
                m = (a + b) / 2; cx, cz = chunk_of(*m)
                za, zb = z[k] + 0.35, z[k + 1] + 0.35
                s = sub("road", cx, cz, "Ballast")
                hw = W / 2
                top = [(a[0] - pp[0] * hw, a[1] - pp[1] * hw, za), (b[0] - pp[0] * hw, b[1] - pp[1] * hw, zb),
                       (b[0] + pp[0] * hw, b[1] + pp[1] * hw, zb), (a[0] + pp[0] * hw, a[1] + pp[1] * hw, za)]
                u0, u1 = dist / 0.65, (dist + L) / 0.65
                add_poly3(s, top, [(u0, 0), (u1, 0), (u1, nt), (u0, nt)])
                for sgn in (-1, 1):  # sloped ballast shoulders
                    o1 = hw * sgn; o2 = (hw + 0.9) * sgn
                    q = [(a[0] + pp[0] * o1, a[1] + pp[1] * o1, za), (b[0] + pp[0] * o1, b[1] + pp[1] * o1, zb),
                         (b[0] + pp[0] * o2, b[1] + pp[1] * o2, zb - 0.6), (a[0] + pp[0] * o2, a[1] + pp[1] * o2, za - 0.6)]
                    if sgn < 0: q = q[::-1]
                    else: q = [q[1], q[0], q[3], q[2]]
                    add_poly3(sub("road", cx, cz, "Gravel"), q, [(0, 0), (L / 2, 0), (L / 2, 0.5), (0, 0.5)])
                # rails (steel boxes)
                rs = sub("road", cx, cz, "Rail")
                for t in range(nt):
                    tc = (t - (nt - 1) / 2) * 3.6
                    for g in (-0.7175, 0.7175):
                        off = tc + g
                        for side in (-0.035, 0.035):
                            pa = a + pp * (off + side); pb = b + pp * (off + side)
                            q = [(pa[0], pa[1], za + 0.08), (pb[0], pb[1], zb + 0.08), (pb[0], pb[1], zb + 0.23), (pa[0], pa[1], za + 0.23)]
                            if side < 0: q = [q[1], q[0], q[3], q[2]]
                            add_poly3(rs, q, [(0, 0), (1, 0), (1, 1), (0, 1)])
                        pa1, pb1, pa2, pb2 = a + pp * (off - 0.035), b + pp * (off - 0.035), a + pp * (off + 0.035), b + pp * (off + 0.035)
                        add_poly3(rs, [(pa1[0], pa1[1], za + 0.23), (pb1[0], pb1[1], zb + 0.23), (pb2[0], pb2[1], zb + 0.23), (pa2[0], pa2[1], za + 0.23)][::-1] if False else
                                  [(pa2[0], pa2[1], za + 0.23), (pb2[0], pb2[1], zb + 0.23), (pb1[0], pb1[1], zb + 0.23), (pa1[0], pa1[1], za + 0.23)], [(0, 0)] * 4, (0, 0, 1))
                dist += L
            n += 1
    STATS["rail_segments"] = n
    log("rail segments", n)

# ---------------------------------------------------------------- water
def build_water():
    feats = load("surface_hydrographique")
    ws = []
    loire_level = 1.75
    for f in feats:
        pr = f["properties"]
        g = local(shape(f["geometry"])).intersection(REGION)
        for p in polys_of(g):
            if p.area < 30: continue
            big = p.area > 500000
            if big: lvl = loire_level
            else:
                v = np.asarray(p.exterior.coords)
                hs = hgt(v[:, 0], v[:, 1])
                lvl = float(np.percentile(hs, 10)) - 0.15
            ws.append((p, lvl))
    # Loire also from DEM: cells at river level north of the coteau not covered (tidal flats) -> rely on BD TOPO polygon
    union = unary_union([p for p, _ in ws]) if ws else Polygon()
    for p, lvl in ws:
        # chunked
        for cxi in range(NCH):
            for czi in range(NCH):
                cb = box(cxi * CHUNK, czi * CHUNK, (cxi + 1) * CHUNK, (czi + 1) * CHUNK)
                if not cb.intersects(p): continue
                for q in polys_of(p.intersection(cb)):
                    if q.area < 1: continue
                    q = q.simplify(0.3)
                    if q.is_empty: continue
                    for qq in polys_of(q): flat_cap(cxi, czi, "Water", qq, lvl, layer="water", uv_scale=20.0)
    STATS["water_polys"] = len(ws)
    STATS["water_area_m2"] = round(union.area)
    log("water", len(ws), "area", round(union.area))
    return union

# ---------------------------------------------------------------- trees from LiDAR canopy height model
def build_trees(building_union, water_union, road_union):
    from scipy import ndimage
    M = np.fromfile(os.path.join(DATA, "dem", "mnh_4096.f32"), "<f4").reshape(4096, 4096)
    Ms = ndimage.gaussian_filter(M, 1.0)
    # masks (row0 = south, 1 m cells)
    from PIL import ImageDraw
    mask = Image.new("L", (4096, 4096), 0); dr = ImageDraw.Draw(mask)
    for p in polys_of(building_union.buffer(1.5)):
        dr.polygon([(x, y) for x, y in p.exterior.coords], fill=255)
    for p in polys_of(water_union):
        dr.polygon([(x, y) for x, y in p.exterior.coords], fill=255)
    bad = np.asarray(mask) > 0
    trees = []
    for size, hmin, hmax in ((9, 12.0, 60.0), (5, 3.0, 12.0), (3, 1.4, 3.0)):
        mx = ndimage.maximum_filter(Ms, size=size)
        pk = (Ms == mx) & (Ms >= hmin) & (Ms < hmax) & ~bad
        zz, xx = np.nonzero(pk)
        for z, x in zip(zz, xx):
            h = float(M[z, x])
            if h < hmin * 0.8: continue
            trees.append((x + 0.5, z + 0.5, h))
    trees = np.array(trees, np.float32)
    # thin: remove points closer than 0.35*h to a taller tree (grid hashing)
    order = np.argsort(-trees[:, 2]); keep = []
    grid = {}
    for i in order:
        x, z, h = trees[i]
        r = max(1.6, 0.28 * h)
        gx, gz = int(x // 6), int(z // 6); ok = True
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for (x2, z2, h2) in grid.get((gx + dx, gz + dz), ()):
                    if (x - x2) ** 2 + (z - z2) ** 2 < (max(r, 0.28 * h2)) ** 2: ok = False; break
                if not ok: break
            if not ok: break
        if ok: keep.append(i); grid.setdefault((gx, gz), []).append((x, z, h))
    trees = trees[keep]
    veg = load("zone_de_vegetation")
    poplars = unary_union([local(shape(f["geometry"])) for f in veg if f["properties"].get("nature") == "Peupleraie"]) if veg else Polygon()
    mixed = unary_union([local(shape(f["geometry"])) for f in veg if f["properties"].get("nature") in ("Forêt fermée mixte", "Forêt fermée de conifères")]) if veg else Polygon()
    out = bytearray(b"LMT1"); out += struct.pack("<i", len(trees))
    counts = [0, 0, 0, 0, 0]
    for x, z, h in trees:
        y = float(hgt(x, z))
        if h < 3.0: proto = 4                                  # bush / hedge
        elif poplars.contains(Point(x, z)): proto = 3          # poplar
        elif mixed.contains(Point(x, z)) and hsh((x, z), 3) == 0: proto = 2
        else:
            r = hsh((round(x), round(z)), 100)
            proto = 2 if r < 6 else (0 if r < 55 else 1)       # mostly broadleaf, a few conifers (garden pines/cedars)
        counts[proto] += 1
        out += struct.pack("<ffffi", x, y, z, h, proto)
    open(os.path.join(OUT, "trees.bin"), "wb").write(out)
    STATS["trees"] = dict(total=int(len(trees)), broadleafA=counts[0], broadleafB=counts[1], conifer=counts[2], poplar=counts[3], bush=counts[4])
    log("trees", STATS["trees"])

# ---------------------------------------------------------------- write city.bin
def write_city():
    mats = sorted({m for d in CHUNKS.values() for m in d})
    mid = {m: i for i, m in enumerate(mats)}
    out = bytearray(b"LMC2"); out += struct.pack("<i", len(mats))
    for m in mats:
        b = m.encode(); out += struct.pack("<i", len(b)) + b
    out += struct.pack("<i", sum(1 for d in CHUNKS.values() if any(s.i for s in d.values())))
    tv = tt = 0
    for (layer, cx, cz), d in sorted(CHUNKS.items()):
        name = f"{layer}_{cx:02d}_{cz:02d}".encode()
        P, N, UV, subs = [], [], [], []
        for m, s in d.items():
            if not s.i: continue
            b = len(P)
            P += s.p; N += s.n; UV += s.uv
            subs.append((mid[m], np.asarray(s.i, np.int32) + b))
        if not subs: continue
        out += struct.pack("<i", len(name)) + name
        out += struct.pack("<i", len(P))
        out += np.asarray(P, "<f4").tobytes() + np.asarray(N, "<f4").tobytes() + np.asarray(UV, "<f4").tobytes()
        out += struct.pack("<i", len(subs))
        for m, idx in subs:
            out += struct.pack("<ii", m, len(idx)) + idx.astype("<i4").tobytes()
            tt += len(idx) // 3
        tv += len(P)
    open(os.path.join(OUT, "city.bin"), "wb").write(out)
    STATS["mesh"] = dict(chunks=len(CHUNKS), vertices=tv, triangles=tt, bytes=len(out))
    log("city.bin", STATS["mesh"])
    return mats

def spawn_point():
    # in front of église Notre-Dame-de-l'Assomption, on the street, facing the church
    tgt = np.array([1976.0, 2300.0])
    feats = load("troncon_de_route")
    best = None
    for f in feats:
        if f["properties"].get("nature") not in PAVED or f["properties"].get("position_par_rapport_au_sol") not in (None, "0"): continue
        for co in ([f["geometry"]["coordinates"]] if f["geometry"]["type"] == "LineString" else f["geometry"]["coordinates"]):
            ls = LineString(local3(co)[:, :2])
            d = ls.distance(Point(*tgt))
            if 18 < d < 60 and (best is None or d < best[0]):
                p = ls.interpolate(ls.project(Point(*tgt))); best = (d, p)
    p = best[1]
    yaw = math.degrees(math.atan2(tgt[0] - p.x, tgt[1] - p.y))
    return dict(x=float(p.x), y=float(hgt(p.x, p.y)) + 1.0, z=float(p.y), yaw=yaw)

def viewpoints(bu):
    """Street-level viewpoints computed from the data: Loire view from the coteau crest, Mairie, church."""
    views = []
    feats = [f for f in load("troncon_de_route") if f["properties"].get("nature") in PAVED | {"Chemin", "Sentier"}
             and f["properties"].get("position_par_rapport_au_sol") in (None, "0")]
    # Loire bank: road/path point inside the commune closest to the Loire water surface, facing the river
    loire = [local(shape(f["geometry"])) for f in load("surface_hydrographique")]
    loire = max(loire, key=lambda g: g.area)
    com = [local(shape(f["geometry"])) for f in load("commune") if f["properties"].get("code_insee") == "44101"]
    com = com[0].buffer(150) if com else REGION
    best = None
    for f in feats:
        for co in ([f["geometry"]["coordinates"]] if f["geometry"]["type"] == "LineString" else f["geometry"]["coordinates"]):
            ls = LineString(local3(co)[:, :2]).segmentize(10)
            for x, z in ls.coords:
                pt = Point(x, z)
                if not com.contains(pt) or loire.contains(pt): continue
                d = loire.exterior.distance(pt) if loire.geom_type == "Polygon" else loire.distance(pt)
                d = loire.distance(pt)
                if d < 8: continue
                if best is None or d < best[0]: best = (d, x, z)
    lv = loire_view(bu)
    if lv: views.append(lv); best = None
    if best:
        d, x, z = best
        from shapely.ops import nearest_points
        q = nearest_points(loire, Point(x, z))[0]
        yaw = math.degrees(math.atan2(q.x - x, q.y - z))
        views.append(dict(name="loire", x=x, y=float(hgt(x, z)) + 1.0, z=z, yaw=yaw, pitch=-2.0))
        log("loire view", round(x), round(z), "dist to water", round(d, 1), "yaw", round(yaw))
    m = (1803.0, 2519.0)
    for f in feats:
        pass
    return views

# ================================================================ v2 extras: OSM, church, markings, barriers, furniture, cars, terrain
from pyproj import Transformer
_TR = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)
OSM = json.load(open(os.path.join(DATA, "osm_full.json")))["elements"] if os.path.exists(os.path.join(DATA, "osm_full.json")) else []
def ll2loc(lon, lat):
    x, y = _TR.transform(lon, lat); return x - X0, y - Y0
def osm_point(e):
    if e["type"] == "node": return Point(*ll2loc(e["lon"], e["lat"]))
    g = e.get("geometry")
    if not g: return None
    pts = [ll2loc(q["lon"], q["lat"]) for q in g if q]
    if len(pts) >= 3 and pts[0] == pts[-1]: return Polygon(pts).representative_point()
    return LineString(pts).interpolate(0.5, normalized=True) if len(pts) > 1 else Point(*pts[0])
def osm_line(e):
    g = e.get("geometry")
    if not g: return None
    pts = [ll2loc(q["lon"], q["lat"]) for q in g if q]
    return LineString(pts) if len(pts) > 1 else None
def inside(p): return 2 < p.x < SIZE - 2 and 2 < p.y < SIZE - 2

def face(s, pts, uvs, ctr):
    """polygon with automatic outward orientation (relative to solid centre ctr, math coords x,north,up)."""
    P = np.asarray(pts, float)
    n = np.cross(P[1] - P[0], P[2] - P[0])
    if np.dot(n, P.mean(0) - np.asarray(ctr, float)) < 0: pts = pts[::-1]; uvs = uvs[::-1]
    add_poly3(s, pts, uvs)
def prism(s, ring, y0, y1, ctr=None, us=1.0, vs=1.0, top=True):
    """ring: list of 2D points (convex); y0/y1 scalars or per-vertex lists."""
    n = len(ring); ring = [np.asarray(p, float) for p in ring]
    y0 = np.broadcast_to(np.asarray(y0, float), (n,)); y1 = np.broadcast_to(np.asarray(y1, float), (n,))
    c2 = np.mean(ring, 0); cy = (y0.mean() + y1.mean()) / 2
    ctr = (c2[0], c2[1], cy)
    d = 0.0
    for k in range(n):
        a, b = ring[k], ring[(k + 1) % n]; L = float(np.hypot(*(b - a)))
        if L < 1e-4: continue
        j = (k + 1) % n
        face(s, [(a[0], a[1], y0[k]), (b[0], b[1], y0[j]), (b[0], b[1], y1[j]), (a[0], a[1], y1[k])],
             [(d / us, y0[k] / vs * 0), ((d + L) / us, 0), ((d + L) / us, (y1[j] - y0[j]) / vs), (d / us, (y1[k] - y0[k]) / vs)], ctr)
        d += L
    if top:
        face(s, [(p[0], p[1], y1[k]) for k, p in enumerate(ring)], [(p[0] / us, p[1] / us) for p in ring], (ctr[0], ctr[1], y1.min() - 1))
def rect(c, e, L, W):
    e = np.asarray(e, float); e = e / (np.linalg.norm(e) + 1e-12); p = np.array([-e[1], e[0]]); c = np.asarray(c, float)
    return [c - e * L / 2 - p * W / 2, c + e * L / 2 - p * W / 2, c + e * L / 2 + p * W / 2, c - e * L / 2 + p * W / 2]
def boxm(layer, mat, c, e, L, W, y0, y1, us=1.0, vs=1.0):
    cx, cz = chunk_of(c[0], c[1]); prism(sub(layer, cx, cz, mat), rect(c, e, L, W), y0, y1, us=us, vs=vs)
def cyl(layer, mat, c, r, y0, y1, n=6):
    cx, cz = chunk_of(c[0], c[1])
    ring = [(c[0] + r * math.cos(a), c[1] + r * math.sin(a)) for a in np.arange(n) * 2 * math.pi / n]
    prism(sub(layer, cx, cz, mat), ring, y0, y1)
def panel(layer, mat, c, e, W, y0, y1, uvr, back_uvr=None, thick=0.04):
    """sign panel (thin box) whose two faces carry atlas cells uvr=(u0,v0,u1,v1) (front = +normal side)."""
    e = np.asarray(e, float); e = e / (np.linalg.norm(e) + 1e-12); nrm = np.array([e[1], -e[0]])
    cx, cz = chunk_of(c[0], c[1]); s = sub(layer, cx, cz, mat)
    a = np.asarray(c) - e * W / 2; b = np.asarray(c) + e * W / 2
    for sgn, r in ((1, uvr), (-1, back_uvr or uvr)):
        o = nrm * thick / 2 * sgn
        pa, pb = a + o, b + o
        u0, v0, u1, v1 = r
        if sgn > 0: pts = [(pa[0], pa[1], y0), (pb[0], pb[1], y0), (pb[0], pb[1], y1), (pa[0], pa[1], y1)]; uv = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
        else: pts = [(pb[0], pb[1], y0), (pa[0], pa[1], y0), (pa[0], pa[1], y1), (pb[0], pb[1], y1)]; uv = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
        add_poly3(s, pts, uv, (nrm[0] * sgn, nrm[1] * sgn, 0))

# ---------------------------------------------------------------- signs atlas (shop names, town signs, road signs)
SIGN_CELLS = {}
def build_sign_atlas(shop_signs):
    from PIL import ImageDraw, ImageFont
    W, Hh = 2048, 2048; cw, ch = 512, 128
    im = Image.new("RGB", (W, Hh), (240, 240, 240)); dr = ImageDraw.Draw(im)
    fb = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    idx = [0]
    def cell():
        i = idx[0]; idx[0] += 1
        c, r = i % 4, i // 4
        return c * cw, r * ch, (c * cw / W, 1 - (r + 1) * ch / Hh, (c + 1) * cw / W, 1 - r * ch / Hh)
    def text(x0, y0, w, h, t, fill, maxsize=80):
        size = maxsize
        while size > 14:
            f = ImageFont.truetype(fb, size); bb = dr.textbbox((0, 0), t, font=f)
            if bb[2] - bb[0] <= w and bb[3] - bb[1] <= h: break
            size -= 2
        bb = dr.textbbox((0, 0), t, font=f)
        dr.text((x0 + (w - (bb[2] - bb[0])) / 2 - bb[0], y0 + (h - (bb[3] - bb[1])) / 2 - bb[1]), t, font=f, fill=fill)
    bgs = [(28, 60, 40), (110, 24, 36), (24, 40, 80), (30, 30, 30), (240, 236, 226), (60, 90, 110)]
    for sgn in shop_signs:
        nm = sgn["name"]
        if nm in SIGN_CELLS: continue
        x, y, uv = cell(); bg = bgs[hsh(nm, len(bgs))]
        fg = (30, 30, 30) if sum(bg) > 500 else (245, 240, 225)
        if sgn["kind"] == "pharmacy": bg, fg = (240, 240, 240), (20, 140, 60)
        dr.rectangle([x, y, x + cw - 1, y + ch - 1], fill=bg)
        text(x + 24, y + 14, cw - 48, ch - 28, nm.upper() if len(nm) < 18 else nm, fg)
        SIGN_CELLS[nm] = uv
    for town in ("La Montagne", "Indre", "Saint-Jean-de-Boiseau", "Boiseau", "Bouguenais"):
        for barred in (False, True):
            x, y, uv = cell()
            dr.rectangle([x, y, x + cw - 1, y + ch - 1], fill=(250, 250, 250))
            dr.rectangle([x + 6, y + 6, x + cw - 7, y + ch - 7], outline=(200, 20, 30), width=10)
            text(x + 30, y + 22, cw - 60, ch - 44, town.upper(), (15, 15, 15), 70)
            if barred: dr.line([x + 30, y + ch - 20, x + cw - 30, y + 20], fill=(200, 20, 30), width=12)
            SIGN_CELLS[("town", town, barred)] = uv
    # road signs: stop (octagon) & give way (triangle), drawn in a 128x128 square at the cell's left
    x, y, uv = cell()
    dr.rectangle([x, y, x + ch - 1, y + ch - 1], fill=(200, 20, 30))
    text(x + 10, y + 34, ch - 20, 60, "STOP", (255, 255, 255), 40)
    SIGN_CELLS["stop"] = (uv[0], uv[1], uv[0] + ch / W, uv[3])
    x, y, uv = cell()
    dr.rectangle([x, y, x + ch - 1, y + ch - 1], fill=(250, 250, 250))
    dr.polygon([(x + 4, y + 10), (x + ch - 4, y + 10), (x + ch / 2, y + ch - 6)], outline=(200, 20, 30), width=14)
    SIGN_CELLS["give_way"] = (uv[0], uv[1], uv[0] + ch / W, uv[3])
    x, y, uv = cell()
    dr.rectangle([x, y, x + cw - 1, y + ch - 1], fill=(20, 90, 160))
    text(x + 20, y + 16, cw - 40, ch - 32, "BUS", (255, 255, 255), 70)
    SIGN_CELLS["bus"] = uv
    x, y, uv = cell()
    dr.rectangle([x, y, x + cw - 1, y + ch - 1], fill=(160, 160, 160))
    SIGN_CELLS["grey"] = uv
    im.save(os.path.join(OUT, "signs.png"))
    log("sign atlas cells", idx[0])

def build_shop_signs():
    for sg in SHOP_SIGNS:
        uv = SIGN_CELLS.get(sg["name"])
        if not uv: continue
        a, b = np.asarray(sg["a"]), np.asarray(sg["b"]); L = float(np.hypot(*(b - a)))
        e = (b - a) / L; nrm = np.array([e[1], -e[0]])
        W = min(L - 0.6, max(2.5, 0.42 * len(sg["name"]) + 1.0), 7.0)
        if W < 1.2: continue
        c = (a + b) / 2 + nrm * 0.06
        y = sg["y"]
        panel("bld", "Signs", c, e, W, y + 0.05, y + 0.65, uv, uv, 0.08)
    STATS["shop_signs"] = len(SHOP_SIGNS)

# ---------------------------------------------------------------- church (rebuilt from Panoramax references)
def build_church_v2(churches):
    """Notre-Dame-de-l'Assomption: grey rendered nave with tall lancet windows, granite buttresses,
    square tower with a tuffeau belfry stage under a low slate pyramid + cross; height from LiDAR."""
    clochers = [local(shape(f["geometry"])) for f in load("construction_ponctuelle") if f["properties"]["nature"] == "Clocher"]
    mns = {}
    for name, cxm, cym in [("notre_dame", 1985, 2310), ("saint_hermeland", 3065, 3190)]:
        p = os.path.join(DATA, "dem", f"mns_{name}.f32")
        if os.path.exists(p): mns[(cxm, cym)] = np.fromfile(p, "<f4").reshape(512, 512)
    out = []
    for pr, part in churches:
        ring_poly = clean_ring(part, 0.5)
        if ring_poly is None: continue
        ring = np.asarray(ring_poly.exterior.coords)[:-1]
        c = ring_poly.centroid; cx, cz = chunk_of(c.x, c.y)
        ground = float(hgt(ring[:, 0], ring[:, 1]).min())
        Hh = float(pr.get("hauteur") or 12.0)
        mrr = ring_poly.minimum_rotated_rectangle; cs = np.asarray(mrr.exterior.coords)
        wmin = min(np.linalg.norm(cs[1] - cs[0]), np.linalg.norm(cs[2] - cs[1]))
        tanp = math.tan(math.radians(48))
        roof_h = min(wmin / 2 * tanp, 0.5 * Hh)
        eave = ground + Hh - roof_h
        tanp = roof_h / (wmin / 2)
        walls(cx, cz, "ChurchWall", ring, ground - WALL_SINK, eave, ground, 5.0, eave - ground)
        # granite buttresses every ~5 m along long walls + plinth
        s_gr = sub("bld", cx, cz, "Granite")
        n = len(ring)
        for k in range(n):
            a, b = ring[k], ring[(k + 1) % n]; L = float(np.hypot(*(b - a)))
            if L < 4: continue
            e = (b - a) / L; nrm = np.array([e[1], -e[0]])
            nbay = max(1, round(L / 5.0))
            for j in range(nbay + 1):
                if L < 9 and 0 < j < nbay: continue
                p = a + e * (L * j / nbay) + nrm * 0.35
                prism(s_gr, rect(p, e, 0.8, 0.7), ground - 0.5, eave - 1.2)
                # sloped cap
                q = rect(p, e, 0.8, 0.7)
                face(s_gr, [(q[0][0], q[0][1], eave - 1.2), (q[1][0], q[1][1], eave - 1.2), ((q[1] - nrm * 0.35)[0], (q[1] - nrm * 0.35)[1], eave - 0.6), ((q[0] - nrm * 0.35)[0], (q[0] - nrm * 0.35)[1], eave - 0.6)],
                     [(0, 0), (1, 0), (1, 1), (0, 1)], (p[0] - nrm[0], p[1] - nrm[1], eave - 2))
        po = offset_poly(ring_poly, 0.4)
        try:
            hip_roof(cx, cz, "RoofSlate_Church", po, eave - 0.4 * tanp, tanp)
            flat_cap(cx, cz, "RoofSlate_Church", po, eave - 0.4 * tanp - 0.05, down=True)
        except Exception:
            flat_cap(cx, cz, "RoofSlate_Church", ring_poly, eave)
        cl = [q for q in clochers if ring_poly.buffer(8).contains(q)]
        if not cl: continue
        q = cl[0]; top = None; half = 3.0
        for (mx, my), a in mns.items():
            if abs(mx - q.x) < 100 and abs(my - q.y) < 100:
                ys, xs = np.mgrid[0:512, 0:512]
                wx = mx - 128 + xs / 2.0; wy = my - 128 + ys / 2.0
                near = (np.hypot(wx - q.x, wy - q.y) < 12)
                top = float(a[near].max())
                tall = near & (a > ground + Hh + 2)
                if tall.any(): half = float(np.clip(math.sqrt(tall.sum() * 0.25) / 2, 2.5, 4.5))
                if tall.any(): q = Point(float(wx[tall].mean()), float(wy[tall].mean()))
        if top is None: top = ground + Hh * 2.4
        # low slate pyramid (~45 deg) + 2 m cross: roof base = top - cross - roof height
        cross_h = 2.0
        roof_h2 = half * 1.15
        roof_base = top - cross_h - roof_h2
        belfry_h = 5.0
        e = cs[1] - cs[0] if np.linalg.norm(cs[1] - cs[0]) > np.linalg.norm(cs[2] - cs[1]) else cs[2] - cs[1]
        e = e / np.linalg.norm(e); pe = np.array([-e[1], e[0]])
        ctr = np.array([q.x, q.y])
        def square(hh):
            sq = np.array([ctr + hh * (sx * e + sy * pe) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
            return np.asarray(orient(Polygon(sq), 1.0).exterior.coords)[:-1]
        tx, tz = chunk_of(q.x, q.y)
        sq = square(half)
        walls(tx, tz, "ChurchWall", sq, ground - WALL_SINK, roof_base - belfry_h, ground, 2 * half, roof_base - belfry_h - ground)
        # string course + belfry stage in tuffeau with louvred arch on each face
        walls(tx, tz, "Granite", square(half + 0.15), roof_base - belfry_h - 0.35, roof_base - belfry_h, ground, 2.0, 0.35)
        walls(tx, tz, "Tuffeau", square(half - 0.1), roof_base - belfry_h, roof_base, roof_base - belfry_h, 2 * half - 0.2, belfry_h)
        # granite corner quoins on the tower body
        s_gr = sub("bld", tx, tz, "Granite")
        for p in sq:
            d = p - ctr; d = d / np.linalg.norm(d)
            prism(s_gr, rect(p - d * 0.2, e, 0.75, 0.75), ground - 0.5, roof_base - belfry_h - 0.35)
        # cornice
        walls(tx, tz, "Tuffeau", square(half + 0.35), roof_base - 0.4, roof_base, roof_base - belfry_h, 2.0, 0.4)
        flat_cap(tx, tz, "Tuffeau", Polygon(square(half + 0.35)), roof_base - 0.4, down=True)
        # pyramid
        s = sub("bld", tx, tz, "RoofSlate_Church")
        sq3 = square(half + 0.45)
        apex = (ctr[0], ctr[1], roof_base + roof_h2)
        for k in range(4):
            a, b = sq3[k], sq3[(k + 1) % 4]
            face(s, [(a[0], a[1], roof_base), (b[0], b[1], roof_base), apex], [(0, 0), (2 * half / 2, 0), (half / 2, roof_h2 / 2)], (ctr[0], ctr[1], roof_base))
        # cross (dark metal)
        s_c = sub("bld", tx, tz, "MetalDark")
        prism(s_c, rect(ctr, e, 0.12, 0.12), roof_base + roof_h2 - 0.2, top)
        prism(s_c, rect(np.asarray(ctr), e, 0.9, 0.1), top - 0.75, top - 0.6)
        out.append(dict(name=pr.get("cleabs"), x=float(q.x), z=float(q.y), ground=ground, spire_top=top))
        log("church v2", round(q.x), round(q.y), "top", round(top - ground, 1), "m; belfry base", round(roof_base - belfry_h - ground, 1))
    STATS["churches"] = out
    return out

# ---------------------------------------------------------------- markings & crossings
ROAD_SEGS = []   # (LineString, width, nature, props)
def build_markings():
    s_n = 0
    for ls, w, nat, pr in ROAD_SEGS:
        imp = str(pr.get("importance") or "5")
        if w < 5.5 or imp not in ("1", "2", "3", "4") or nat == "Rond-point": continue
        L = ls.length
        if L < 25: continue
        d = 10.0
        while d + 3 < L - 10:
            a = np.asarray(ls.interpolate(d).coords[0]); b = np.asarray(ls.interpolate(d + 3).coords[0])
            e = b - a; ln = np.linalg.norm(e)
            if ln > 0.5:
                e /= ln; p = np.array([-e[1], e[0]]) * 0.06
                q = [a - p, b - p, b + p, a + p]
                ys = hgt(np.array([t[0] for t in q]), np.array([t[1] for t in q])) + 0.125
                cx, cz = chunk_of(*a)
                add_poly3(sub("mark", cx, cz, "Marking"), [(t[0], t[1], y) for t, y in zip(q, ys)], [(0, 0), (1, 0), (1, 1), (0, 1)], (0, 0, 1))
                s_n += 1
            d += 13.0
    # zebra crossings from OSM
    tree = shapely.STRtree([r[0] for r in ROAD_SEGS])
    nz = 0
    for e in OSM:
        t = e.get("tags", {})
        if e["type"] != "node" or t.get("highway") != "crossing": continue
        mk = t.get("crossing") in ("marked", "zebra", "traffic_signals", "uncontrolled") or t.get("crossing:markings") not in (None, "no") or t.get("crossing_ref") == "zebra"
        if not mk or t.get("crossing") == "unmarked": continue
        p = osm_point(e)
        if not inside(p): continue
        i = tree.nearest(p); ls, w, nat, pr = ROAD_SEGS[i]
        if ls.distance(p) > 6: continue
        dd = ls.project(p); a = np.asarray(ls.interpolate(max(0, dd - 1)).coords[0]); b = np.asarray(ls.interpolate(min(ls.length, dd + 1)).coords[0])
        ed = b - a; ln = np.linalg.norm(ed)
        if ln < 0.3: continue
        ed /= ln; pp = np.array([-ed[1], ed[0]])
        c0 = np.asarray(ls.interpolate(dd).coords[0])
        off = -w / 2 + 0.5
        while off <= w / 2 - 0.45:
            cc = c0 + pp * (off + 0.25)
            q = rect(cc, ed, 2.5, 0.5)
            ys = hgt(np.array([t[0] for t in q]), np.array([t[1] for t in q])) + 0.13
            cx, cz = chunk_of(*cc)
            add_poly3(sub("mark", cx, cz, "Marking"), [(t[0], t[1], y) for t, y in zip(q, ys)], [(0, 0), (1, 0), (1, 1), (0, 1)], (0, 0, 1))
            off += 1.0
        nz += 1
    STATS["markings"] = dict(centre_dashes=s_n, zebra_crossings=nz)
    log("markings", STATS["markings"])

# ---------------------------------------------------------------- barriers (walls, fences, hedges, retaining walls, guard rails)
def build_barriers(bu):
    M = np.fromfile(os.path.join(DATA, "dem", "mnh_4096.f32"), "<f4").reshape(4096, 4096)
    bub = bu.buffer(0.4); shapely.prepare(bub)
    items = []
    for e in OSM:
        t = e.get("tags", {}); b = t.get("barrier")
        if e["type"] != "way" or b not in ("wall", "fence", "hedge", "retaining_wall", "guard_rail", "city_wall"): continue
        ls = osm_line(e)
        if ls is None: continue
        try: h = float(str(t.get("height", "")).replace("m", "").strip())
        except ValueError: h = None
        items.append((b, ls, h, t))
    bd = []
    for f in load("haie"):
        g = local(shape(f["geometry"]))
        for part in ([g] if g.geom_type == "LineString" else list(getattr(g, "geoms", []))):
            bd.append(part)
    bdu = unary_union([l.buffer(2.5) for l in bd]) if bd else Polygon(); shapely.prepare(bdu)
    items = [it for it in items if not (it[0] == "hedge" and bdu.contains(it[1].interpolate(0.5, normalized=True)))]
    items += [("hedge", l, None, {}) for l in bd]
    cnt = {}; tot = {}
    for kind, ls, h, t in items:
        ls = ls.intersection(REGION)
        if ls.is_empty: continue
        for part in ([ls] if ls.geom_type == "LineString" else [g for g in getattr(ls, "geoms", []) if g.geom_type == "LineString"]):
            cc = np.asarray(part.segmentize(2.5).coords)
            for k in range(len(cc) - 1):
                a, b = cc[k], cc[k + 1]
                m = (a + b) / 2
                if bub.contains(Point(*m)): continue
                L = float(np.hypot(*(b - a)))
                if L < 0.05: continue
                e = (b - a) / L; pp = np.array([-e[1], e[0]])
                ya, yb = float(hgt(*a)), float(hgt(*b))
                cx, cz = chunk_of(*m)
                if kind in ("wall", "city_wall"):
                    hh = h or (1.2 if hsh(t.get("wall", "") + str(int(m[0])), 3) else 1.8)
                    w = 0.25
                    prism(sub("bar", cx, cz, "GardenWall"), [a - pp * w / 2, b - pp * w / 2, b + pp * w / 2, a + pp * w / 2], [ya - 0.3, yb - 0.3, yb - 0.3, ya - 0.3], [ya + hh, yb + hh, yb + hh, ya + hh], us=2.0, vs=2.0)
                elif kind == "retaining_wall":
                    hh = h or 1.0; w = 0.45
                    prism(sub("bar", cx, cz, "Stone"), [a - pp * w / 2, b - pp * w / 2, b + pp * w / 2, a + pp * w / 2], [ya - 1.5, yb - 1.5, yb - 1.5, ya - 1.5], [ya + hh, yb + hh, yb + hh, ya + hh], us=3.0, vs=3.0)
                elif kind == "fence":
                    hh = h or 1.5
                    s = sub("bar", cx, cz, "Fence")
                    for sgn in (1, -1):
                        pts = [(a[0], a[1], ya - 0.05), (b[0], b[1], yb - 0.05), (b[0], b[1], yb + hh), (a[0], a[1], ya + hh)]
                        uv = [(0, 0), (L / 2.5, 0), (L / 2.5, 1), (0, 1)]
                        if sgn < 0: pts = pts[::-1]; uv = uv[::-1]
                        add_poly3(s, pts, uv)
                elif kind == "hedge":
                    if h is None:
                        r0, c0 = int(np.clip(m[1], 0, 4095)), int(np.clip(m[0], 0, 4095))
                        mh = float(M[r0, c0])
                        hh = float(np.clip(mh, 1.2, 2.2)) if mh < 4 else 2.0
                    else: hh = h
                    w = 0.9
                    prism(sub("bar", cx, cz, "Hedge"), [a - pp * w / 2, b - pp * w / 2, b + pp * w / 2, a + pp * w / 2], [ya - 0.2, yb - 0.2, yb - 0.2, ya - 0.2], [ya + hh, yb + hh, yb + hh, ya + hh], us=2.0, vs=2.0)
                elif kind == "guard_rail":
                    s = sub("bar", cx, cz, "MetalGrey")
                    prism(s, [a - pp * 0.05, b - pp * 0.05, b + pp * 0.05, a + pp * 0.05], [ya + 0.45, yb + 0.45, yb + 0.45, ya + 0.45], [ya + 0.75, yb + 0.75, yb + 0.75, ya + 0.75])
                    prism(s, rect(a, e, 0.1, 0.1), ya - 0.2, ya + 0.7)
                tot[kind] = tot.get(kind, 0) + L
            cnt[kind] = cnt.get(kind, 0) + 1
    STATS["barriers_m"] = {k: round(v) for k, v in tot.items()}
    log("barriers", cnt, STATS["barriers_m"])

# ---------------------------------------------------------------- street furniture
def nearest_road(p, maxd=25):
    i = ROAD_TREE.nearest(p); ls = ROAD_LINES[i]
    if ls.distance(p) > maxd: return None
    d = ls.project(p); a = np.asarray(ls.interpolate(max(0, d - 1.5)).coords[0]); b = np.asarray(ls.interpolate(min(ls.length, d + 1.5)).coords[0])
    e = b - a; n = np.linalg.norm(e)
    if n < 1e-3: return None
    return ls, e / n, np.asarray(ls.interpolate(d).coords[0])

def build_furniture(bu, commune_poly):
    bub = bu.buffer(0.3); shapely.prepare(bub)
    st = {}
    def inc(k): st[k] = st.get(k, 0) + 1
    # lamps (procedural: OSM has no street_lamp here) along urban streets
    grid = {}
    def free(p, r):
        gx, gz = int(p[0] // r), int(p[1] // r)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for q in grid.get((gx + dx, gz + dz), ()):
                    if (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 < r * r: return False
        grid.setdefault((gx, gz), []).append(p); return True
    for si, (ls, w, nat, pr) in enumerate(ROAD_SEGS):
        if not pr.get("urbain") or nat in ("Type autoroutier", "Bretelle"): continue
        L = ls.length; d = 8.0 + (si % 3) * 5
        side = 1 if si % 2 else -1
        while d < L - 4:
            a = np.asarray(ls.interpolate(d).coords[0]); b = np.asarray(ls.interpolate(min(L, d + 1)).coords[0])
            e = b - a; n = np.linalg.norm(e)
            if n > 1e-3:
                e /= n; pp = np.array([-e[1], e[0]]) * side
                base = a + pp * (w / 2 + 0.55)
                if inside(Point(*base)) and not bub.contains(Point(*base)) and free(tuple(base), 18.0):
                    y = float(hgt(*base))
                    cyl("furn", "MetalGrey", base, 0.09, y - 0.3, y + 7.0)
                    arm = base - pp * 0.9
                    boxm("furn", "MetalGrey", (base + arm) / 2, pp, 1.0, 0.07, y + 6.9, y + 7.0)
                    boxm("furn", "LampHead", arm - pp * 0.15, pp, 0.6, 0.28, y + 6.75, y + 6.95)
                    inc("lamp")
            d += 30.0; side = -side
    for e in OSM:
        t = e.get("tags", {})
        p = osm_point(e) if (t.get("barrier") == "bollard" or t.get("amenity") in ("bench", "waste_basket", "post_box", "shelter") or t.get("highway") in ("bus_stop", "stop", "give_way") or t.get("traffic_sign") == "city_limit") else None
        if p is None or not inside(p): continue
        c = np.array([p.x, p.y]); y = float(hgt(*c))
        nr = nearest_road(p, 30)
        e_road = nr[1] if nr else np.array([1.0, 0.0])
        if t.get("barrier") == "bollard":
            cyl("furn", "GreenMetal", c, 0.075, y - 0.2, y + 0.95, 8); inc("bollard")
        elif t.get("amenity") == "bench":
            # face the road: seat along road direction
            to_road = (nr[2] - c) if nr else np.array([0.0, 1.0]); tr = to_road / (np.linalg.norm(to_road) + 1e-9)
            boxm("furn", "Wood", c, e_road, 1.8, 0.45, y + 0.42, y + 0.48)
            boxm("furn", "Wood", c - tr * 0.22, e_road, 1.8, 0.06, y + 0.5, y + 0.9)
            for sx in (-0.75, 0.75):
                boxm("furn", "MetalDark", c + e_road * sx, e_road, 0.06, 0.45, y - 0.1, y + 0.42)
            inc("bench")
        elif t.get("amenity") == "waste_basket":
            cyl("furn", "GreenMetal", c, 0.22, y + 0.25, y + 0.95, 8); cyl("furn", "MetalGrey", c, 0.04, y - 0.1, y + 0.6); inc("bin")
        elif t.get("amenity") == "post_box":
            boxm("furn", "YellowPaint", c, e_road, 0.45, 0.35, y + 0.6, y + 1.35); cyl("furn", "MetalGrey", c, 0.05, y - 0.1, y + 0.6); inc("post_box")
        elif t.get("highway") == "bus_stop":
            cyl("furn", "MetalGrey", c, 0.04, y - 0.2, y + 2.6)
            pn = c + np.array([-e_road[1], e_road[0]]) * 0.0
            panel("furn", "Signs", pn + e_road * 0.0, np.array([-e_road[1], e_road[0]]), 0.6, y + 2.2, y + 2.55, SIGN_CELLS["bus"])
            if t.get("shelter") == "yes":
                tr = (nr[2] - c) if nr else np.array([0.0, 1.0]); tr = tr / (np.linalg.norm(tr) + 1e-9)
                sc = c - tr * 1.2
                boxm("furn", "CarGlass", sc - tr * 0.6, e_road, 3.2, 0.04, y, y + 2.3)
                for sx in (-1.6, 1.6): boxm("furn", "CarGlass", sc + e_road * sx, tr, 1.2, 0.04, y, y + 2.3)
                boxm("furn", "MetalGrey", sc, e_road, 3.4, 1.4, y + 2.3, y + 2.4)
                inc("shelter")
            inc("bus_stop")
        elif t.get("highway") in ("stop", "give_way") and nr:
            ls, er, rp = nr
            side_v = c - rp; sn = np.linalg.norm(side_v)
            pp = side_v / sn if sn > 0.5 else np.array([-er[1], er[0]])
            pole = rp + pp * 4.2
            if bub.contains(Point(*pole)): continue
            yp = float(hgt(*pole))
            cyl("furn", "MetalGrey", pole, 0.035, yp - 0.2, yp + 2.3)
            kind = t["highway"]; uv = SIGN_CELLS[kind]
            if kind == "stop":
                s = sub("furn", *chunk_of(*pole), "Signs"); r = 0.38
                yc = yp + 2.0; cu, cv = (uv[0] + uv[2]) / 2, (uv[1] + uv[3]) / 2; ru, rv = (uv[2] - uv[0]) / 2, (uv[3] - uv[1]) / 2
                for sgn in (1, -1):
                    nrm2 = er * sgn
                    pts = []; uvs = []
                    for k in range(8):
                        an = math.pi / 8 + k * math.pi / 4
                        lat = np.array([-er[1], er[0]]) * math.cos(an) * r
                        pts.append((pole[0] + lat[0] + nrm2[0] * 0.03, pole[1] + lat[1] + nrm2[1] * 0.03, yc + math.sin(an) * r))
                        uvs.append((cu + math.cos(an) * ru * (sgn), cv + math.sin(an) * rv))
                    face(s, pts, uvs, (pole[0] - nrm2[0], pole[1] - nrm2[1], yc))
            else:
                s = sub("furn", *chunk_of(*pole), "Signs"); r = 0.5; yc = yp + 2.0
                lat = np.array([-er[1], er[0]])
                for sgn in (1, -1):
                    o = er * 0.03 * sgn
                    pts = [(pole[0] - lat[0] * r + o[0], pole[1] - lat[1] * r + o[1], yc + 0.28), (pole[0] + lat[0] * r + o[0], pole[1] + lat[1] * r + o[1], yc + 0.28), (pole[0] + o[0], pole[1] + o[1], yc - 0.5)]
                    uvs = [(uv[0], uv[3]), (uv[2], uv[3]), ((uv[0] + uv[2]) / 2, uv[1])]
                    face(s, pts, uvs, (pole[0] - er[0] * sgn, pole[1] - er[1] * sgn, yc))
            inc(kind)
        elif t.get("traffic_sign") == "city_limit" and nr:
            town = t.get("name") or "La Montagne"
            if ("town", town, False) not in SIGN_CELLS: continue
            ls, er, rp = nr
            side_v = c - rp; sn = np.linalg.norm(side_v)
            pp = side_v / sn if sn > 0.5 else np.array([-er[1], er[0]])
            pole = rp + pp * 4.5
            yp = float(hgt(*pole))
            # front faces arriving traffic: for La Montagne, traffic arriving from outside the commune
            out_dir = er if not commune_poly.contains(Point(*(rp + er * 30))) else -er
            front = out_dir if town == "La Montagne" else -out_dir
            for sx in (-0.6, 0.6):
                cyl("furn", "MetalGrey", pole + np.array([-er[1], er[0]]) * sx, 0.04, yp - 0.2, yp + 2.4)
            pdir = np.array([-front[1], front[0]])   # panel direction so that its +normal = front
            panel("furn", "Signs", pole, -pdir, 1.6, yp + 1.75, yp + 2.35, SIGN_CELLS[("town", town, False)], SIGN_CELLS[("town", town, True)], 0.05)
            inc("city_limit")
    STATS["furniture"] = st
    log("furniture", st)

# ---------------------------------------------------------------- parked cars on OSM parking spaces
CAR_COLS = [("Car_White", 30), ("Car_Grey", 25), ("Car_Black", 15), ("Car_Blue", 10), ("Car_Red", 8), ("Car_Silver", 12)]
def car(c, e, y, key):
    col = _pick(key, CAR_COLS)
    e = np.asarray(e, float); e /= np.linalg.norm(e); pp = np.array([-e[1], e[0]])
    L = 3.9 + hsh(str(key) + "l", 6) * 0.1; W = 1.75
    boxm("car", col, c, e, L, W, y + 0.32, y + 0.95)
    # cabin: tapered with glass
    cc = c - e * 0.15
    cx, cz = chunk_of(*c); sg = sub("car", cx, cz, "CarGlass"); sr = sub("car", cx, cz, col)
    lo = rect(cc, e, L * 0.58, W * 0.96); hi = rect(cc - e * 0.1, e, L * 0.36, W * 0.84)
    ctr = (cc[0], cc[1], y + 1.1)
    for k in range(4):
        a, b = lo[k], lo[(k + 1) % 4]; a2, b2 = hi[k], hi[(k + 1) % 4]
        face(sg, [(a[0], a[1], y + 0.95), (b[0], b[1], y + 0.95), (b2[0], b2[1], y + 1.42), (a2[0], a2[1], y + 1.42)], [(0, 0), (1, 0), (1, 1), (0, 1)], ctr)
    face(sr, [(p[0], p[1], y + 1.42) for p in hi], [(0, 0), (1, 0), (1, 1), (0, 1)], (cc[0], cc[1], y))
    for sx in (-L * 0.32, L * 0.32):
        for sy in (-W / 2 + 0.12, W / 2 - 0.12):
            wc = c + e * sx + pp * sy
            ring = [(0.33 * math.cos(a), 0.33 * math.sin(a)) for a in np.arange(8) * math.pi / 4]
            st = sub("car", cx, cz, "Tyre")
            P1 = [(wc[0] + e[0] * u - pp[0] * 0.11, wc[1] + e[1] * u - pp[1] * 0.11, y + 0.33 + v) for u, v in ring]
            P2 = [(wc[0] + e[0] * u + pp[0] * 0.11, wc[1] + e[1] * u + pp[1] * 0.11, y + 0.33 + v) for u, v in ring]
            ctrw = (wc[0], wc[1], y + 0.33)
            face(st, P1, [(0, 0)] * 8, ctrw); face(st, P2, [(0, 0)] * 8, ctrw)
            for k in range(8):
                j = (k + 1) % 8
                face(st, [P1[k], P1[j], P2[j], P2[k]], [(0, 0)] * 4, ctrw)
def build_cars(bu):
    bub = bu.buffer(0.2); shapely.prepare(bub)
    n = 0
    for e in OSM:
        t = e.get("tags", {})
        if e["type"] != "way" or t.get("amenity") != "parking_space": continue
        g = e.get("geometry")
        if not g or len(g) < 4: continue
        poly = Polygon([ll2loc(q["lon"], q["lat"]) for q in g])
        if not poly.is_valid or poly.area < 6 or poly.area > 30: continue
        c = poly.centroid
        if not inside(c) or bub.contains(c): continue
        if hsh(e["id"], 100) >= 68: continue
        cs = np.asarray(poly.minimum_rotated_rectangle.exterior.coords)
        e1 = cs[1] - cs[0]; e2 = cs[2] - cs[1]
        ax = e1 if np.linalg.norm(e1) > np.linalg.norm(e2) else e2
        if hsh(str(e["id"]) + "f", 2): ax = -ax
        car(np.array([c.x, c.y]), ax, float(hgt(c.x, c.y)) + 0.1, e["id"]); n += 1
    STATS["parked_cars"] = n
    log("parked cars", n)

# ---------------------------------------------------------------- terrain: lowered under roads, grass density
def write_terrain_v2(paved_geoms, bu, wu):
    from PIL import ImageDraw
    from scipy import ndimage
    m = Image.new("L", (2049, 2049), 0); dr = ImageDraw.Draw(m)
    for g in paved_geoms:
        for p in polys_of(g):
            dr.polygon([(x / 2, y / 2) for x, y in p.exterior.coords], fill=255)
            for r in p.interiors: dr.polygon([(x / 2, y / 2) for x, y in r.coords], fill=0)
    mk = np.asarray(m) > 0
    mk = ndimage.binary_erosion(mk, iterations=1)   # keep road-edge vertices at DEM height (no gaps)
    Ht = H.copy(); Ht[mk] -= 0.3
    Ht.astype("<f4").tofile(os.path.join(OUT, "terrain_height.f32"))
    # grass density (1 m) from ortho greenness, minus roads / buildings / water
    mm = Image.new("L", (4096, 4096), 0); d2 = ImageDraw.Draw(mm)
    for g in list(paved_geoms) + [bu.buffer(0.5), wu]:
        for p in polys_of(g): d2.polygon([(x, y) for x, y in p.exterior.coords], fill=255)
    bad = np.asarray(mm) > 0
    O = ORTHO[::-1].astype(np.float32)   # row0 = south
    O = O.reshape(4096, 2, 4096, 2, 3).mean(axis=(1, 3))
    r, gg, b = O[..., 0], O[..., 1], O[..., 2]
    exg = 2 * gg - r - b                      # excess-green index (ortho is bluish / dark: lawns ~ 10-40)
    dens = np.clip((exg - 5) / 3.0, 0, 8)
    dens[bad] = 0
    dens = ndimage.uniform_filter(dens, 3)
    D = np.clip(np.round(dens), 0, 9).astype(np.uint8)
    for rr in range(2):
        for cc in range(2):
            D[rr * 2048:(rr + 1) * 2048, cc * 2048:(cc + 1) * 2048].tofile(os.path.join(OUT, f"grass_t{rr}{cc}.u8"))
    STATS["grass_cover"] = round(float((D > 0).mean()), 3)
    log("terrain v2: lowered verts", int(mk.sum()), "grass cover", STATS["grass_cover"])

def loire_view(bu):
    """riverside point with a clear (canopy-free) line of sight to the Loire, near the bourg."""
    M = np.fromfile(os.path.join(DATA, "dem", "mnh_4096.f32"), "<f4").reshape(4096, 4096)
    loire = max([local(shape(f["geometry"])) for f in load("surface_hydrographique")], key=lambda g: g.area)
    com = [local(shape(f["geometry"])) for f in load("commune") if f["properties"].get("code_insee") == "44101"]
    com = com[0].buffer(200) if com else REGION
    from shapely.ops import nearest_points
    best = None
    feats = [f for f in load("troncon_de_route") if f["properties"].get("nature") in PAVED | {"Chemin", "Sentier", "Route empierrée"}
             and f["properties"].get("position_par_rapport_au_sol") in (None, "0")]
    for f in feats:
        for co in ([f["geometry"]["coordinates"]] if f["geometry"]["type"] == "LineString" else f["geometry"]["coordinates"]):
            ls = LineString(local3(co)[:, :2]).segmentize(8)
            for x, z in ls.coords:
                pt = Point(x, z)
                if not com.contains(pt) or loire.contains(pt) or not inside(pt): continue
                d = loire.distance(pt)
                if d > 150 or d < 3: continue
                q = nearest_points(loire, pt)[0]
                v = np.array([q.x - x, q.y - z]); v /= np.linalg.norm(v)
                # canopy along the first 80 m of the view ray
                ts = np.arange(2, 80, 1.0); xs = np.clip((x + v[0] * ts).astype(int), 0, 4095); zs = np.clip((z + v[1] * ts).astype(int), 0, 4095)
                blocked = float((M[zs, xs] > 2.0).mean())
                # terrain must stay below the eye line all the way to the water (+20 m into the river)
                eye = float(hgt(x, z)) + 1.7
                tt = np.arange(3, d + 20, 2.0)
                hh = hgt(x + v[0] * tt, z + v[1] * tt)
                wl = 1.75
                need = eye + (wl - eye) * tt / (d + 20)   # sight line to the water surface 20 m beyond the bank
                if (hh > need + 0.2).any(): blocked += 2
                if bu.contains(Point(x + v[0] * 10, z + v[1] * 10)): blocked += 1
                score = blocked * 100 + d * 0.3 + math.hypot(x - 1980, z - 2300) * 0.01
                if best is None or score < best[0]: best = (score, x, z, q, d, blocked)
    if not best: return None
    _, x, z, q, d, bl = best
    yaw = math.degrees(math.atan2(q.x - x, q.y - z))
    log("loire view v2", round(x), round(z), "dist", round(d), "blocked", round(bl, 2), "yaw", round(yaw))
    return dict(name="loire", x=float(x), y=float(hgt(x, z)) + 1.0, z=float(z), yaw=yaw, pitch=-3.0)

PAVED_GEOMS = []
def main():
    write_terrain_textures()
    init_context()
    churches = build_buildings()
    ch = build_church_v2(churches)
    bfeats = load("batiment")
    bu = unary_union([local(shape(f["geometry"])) for f in bfeats]).buffer(0)
    wu = build_water()
    ru = build_roads(bu, wu)
    build_rail()
    build_trees(bu, wu, ru)
    commune = [local(shape(f["geometry"])) for f in load("commune") if f["properties"].get("code_insee") == "44101"]
    cpoly = commune[0] if commune else REGION
    build_markings()
    build_barriers(bu)
    build_sign_atlas(SHOP_SIGNS)
    build_shop_signs()
    build_furniture(bu, cpoly)
    build_cars(bu)
    write_terrain_v2(PAVED_GEOMS, bu, wu)
    mats = write_city()
    poi = []
    for f in load("zone_d_activite_ou_d_interet"):
        pr = f["properties"]
        if pr.get("toponyme"):
            c = local(shape(f["geometry"])).centroid
            if 0 <= c.x <= SIZE and 0 <= c.y <= SIZE: poi.append(dict(name=pr["toponyme"], kind=pr.get("nature"), x=round(c.x, 1), z=round(c.y, 1), y=round(float(hgt(c.x, c.y)), 2)))
    commune = [f for f in load("commune") if f["properties"].get("code_insee") == "44101"]
    border = []
    if commune:
        g = local(shape(commune[0]["geometry"]))
        for p in polys_of(g): border.append([[round(x, 1), round(y, 1)] for x, y in p.exterior.coords])
    meta = dict(region=REG, materials=mats, roof_palette={k: [float(x) / 255 for x in v] for k, v in ROOF_PALETTE.items()},
                wall_colors=WALL_COLORS,
                palette_list=[dict(name=k, r=float(v[0]) / 255, g=float(v[1]) / 255, b=float(v[2]) / 255) for k, v in ROOF_PALETTE.items()] +
                             [dict(name=k, r=v[0], g=v[1], b=v[2]) for k, v in WALL_COLORS.items()],
                spawn=spawn_point(), views=viewpoints(bu), churches=ch, poi=poi, commune_border=border, stats=STATS,
                terrain=dict(res=2049, tiles=2, tile_size=2048.0, height_scale=64.0))
    json.dump(meta, open(os.path.join(OUT, "meta.json"), "w"), indent=1, ensure_ascii=False)
    log("done", json.dumps(STATS, ensure_ascii=False))

if __name__ == "__main__":
    main()
