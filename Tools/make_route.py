#!/usr/bin/env python3
"""Random-ish stroll route for the gameplay video: random walk on the BD TOPO street graph around the church,
offset onto the right-hand sidewalk. Output: Assets/StreamingAssets/LaMontagne/balade_route.json"""
import os, json, math, random
import numpy as np
from shapely.geometry import shape, LineString, Point
DATA = os.environ.get("LAMONTAGNE_DATA", "/workspace/lamontagne/data")
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
X0, Y0 = 343552.0, 6684852.0
meta = json.load(open(os.path.join(PROJ, "ImportData", "meta.json")))
sp = meta["spawn"]; church = np.array([1976.0, 2300.0])
feats = json.load(open(os.path.join(DATA, "vector", "troncon_de_route.geojson")))["features"]
PAVED = {"Route à 1 chaussée", "Route à 2 chaussées", "Rond-point"}
edges = []
def key(p): return (round(p[0], 0), round(p[1], 0))
for f in feats:
    pr = f["properties"]
    if pr.get("nature") not in PAVED or pr.get("position_par_rapport_au_sol") not in (None, "0"): continue
    co = f["geometry"]["coordinates"]
    if f["geometry"]["type"] != "LineString": co = co[0]
    a = np.asarray(co)[:, :2] - [X0, Y0]
    if np.hypot(*(a.mean(0) - church)) > 450: continue
    w = pr.get("largeur_de_chaussee") or 5.0
    edges.append(dict(pts=a, w=float(w), name=pr.get("nom_collaboratif_gauche") or ""))
adj = {}
for i, e in enumerate(edges):
    adj.setdefault(key(e["pts"][0]), []).append((i, False)); adj.setdefault(key(e["pts"][-1]), []).append((i, True))
rng = random.Random(44620)
# start edge: closest to spawn
spp = Point(sp["x"], sp["z"])
i0 = min(range(len(edges)), key=lambda i: LineString(edges[i]["pts"]).distance(spp))
e0 = edges[i0]; ls0 = LineString(e0["pts"])
# go in the direction the spawn is facing
yaw = math.radians(sp["yaw"]); fwd = np.array([math.sin(yaw), math.cos(yaw)])
d = ls0.project(spp)
rev = np.dot(np.asarray(ls0.interpolate(min(ls0.length, d + 5)).coords[0]) - np.asarray(ls0.interpolate(max(0, d - 5)).coords[0]), fwd) < 0
pts0 = e0["pts"][::-1] if rev else e0["pts"]
ls0 = LineString(pts0); d = ls0.project(spp)
seq = [(np.asarray(LineString(pts0).coords)[[k for k in range(len(pts0)) if ls0.project(Point(pts0[k])) > d]], e0["w"], e0["name"])]
seq[0] = (np.vstack([[ls0.interpolate(d).coords[0]], seq[0][0]]), e0["w"], e0["name"])
used = {i0}; cur = key(pts0[-1]); heading = pts0[-1] - pts0[-2]; total = ls0.length - d
while total < 900:
    opts = []
    for i, r in adj.get(cur, []):
        if i in used: continue
        p = edges[i]["pts"][::-1] if r else edges[i]["pts"]
        h = p[1] - p[0]
        ang = abs(math.degrees(math.atan2(heading[0] * h[1] - heading[1] * h[0], heading @ h)))
        far = np.hypot(*(p[-1] - church))
        score = ang * 0.6 + max(0, far - 300) * 0.5 + rng.uniform(0, 60)
        opts.append((score, i, p))
    if not opts: break
    _, i, p = min(opts)
    used.add(i); seq.append((p, edges[i]["w"], edges[i]["name"])); total += LineString(p).length
    cur = key(p[-1]); heading = p[-1] - p[-2]
route = []
for p, w, name in seq:
    if len(p) < 2: continue
    ls = LineString(p)
    if ls.length < 1: continue
    off = ls.offset_curve(-(w / 2 + 1.0))   # negative = right side
    if off.is_empty or off.geom_type != "LineString": off = ls
    for x, z in off.segmentize(4).coords: route.append([round(x, 2), round(z, 2)])
json.dump(dict(points=[dict(x=x, z=z) for x, z in route], length=total, streets=[s[2] for s in seq]),
          open(os.path.join(PROJ, "Assets", "StreamingAssets", "LaMontagne", "balade_route.json"), "w"), ensure_ascii=False)
print("route points", len(route), "length m", round(total), "streets", [s[2] for s in seq][:12])
