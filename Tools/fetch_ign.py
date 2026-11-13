#!/usr/bin/env python3
"""Re-runnable fetch of IGN open data (Licence Ouverte Etalab 2.0) for La Montagne (44620, INSEE 44101).
Region: 4096 m x 4096 m square in Lambert-93 (EPSG:2154).
Outputs (data/):
  region.json                        region definition (origin, size)
  dem/height_2049.f32                2049x2049 float32 heights (m, NGF-IGN69), row 0 = south, 2 m spacing (RGE ALTI 1m resampled)
  ortho/ortho_r{row}_c{col}.jpg      BD ORTHO 0.5 m/px tiles 2048px (1024 m), row 0 = south
  vector/<layer>.geojson             BD TOPO V3 layers in EPSG:2154
"""
import os, sys, json, time, requests, numpy as np
D = os.environ.get("LAMONTAGNE_DATA", "/workspace/lamontagne/data")
X0, Y0, SIZE = 343552.0, 6684852.0, 4096.0   # SW corner L93, size m
UA = {"User-Agent": "GrokBot-LaMontagneFPS/1.0"}
WMS = "https://data.geopf.fr/wms-r"
WFS = "https://data.geopf.fr/wfs/ows"

def get(url, params, tries=5):
    for t in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=120)
            if r.status_code == 200 and not r.headers.get("content-type","").startswith(("text/xml","application/xml")) :
                return r
            print("  retry", r.status_code, r.text[:200])
        except Exception as e:
            print("  retry", e)
        time.sleep(2 + 3*t)
    raise RuntimeError("failed " + str(params))

def wms(layer, bbox, w, h, fmt):
    return get(WMS, dict(SERVICE="WMS", VERSION="1.3.0", REQUEST="GetMap", LAYERS=layer, STYLES="",
        CRS="EPSG:2154", BBOX=",".join(f"{v:.3f}" for v in bbox), WIDTH=w, HEIGHT=h, FORMAT=fmt)).content

def dem():
    out = os.path.join(D, "dem", "height_2049.f32")
    if os.path.exists(out): print("dem exists"); return
    N, step, T = 2049, 2.0, 256
    H = np.zeros((N, N), np.float32)
    for ty in range(8):
        for tx in range(8):
            # samples i in [tx*T, tx*T+T] -> pixel centres at X0 + i*step
            bx = X0 + tx*T*step - step/2; by = Y0 + ty*T*step - step/2
            b = wms("ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES", (bx, by, bx+(T+1)*step, by+(T+1)*step), T+1, T+1, "image/x-bil;bits=32")
            a = np.frombuffer(b, "<f4").reshape(T+1, T+1)[::-1]  # flip so row0 = south
            H[ty*T:ty*T+T+1, tx*T:tx*T+T+1] = a
            print("dem tile", ty, tx, float(a.min()), float(a.max()))
    bad = (H < -100) | (H > 2000)
    if bad.any():
        print("nodata cells", int(bad.sum())); H[bad] = np.median(H[~bad])
    H.tofile(out)

def ortho():
    res, px = 0.5, 2048
    n = int(SIZE / (res*px))
    for r in range(n):
        for c in range(n):
            out = os.path.join(D, "ortho", f"ortho_r{r}_c{c}.jpg")
            if os.path.exists(out): continue
            bx = X0 + c*px*res; by = Y0 + r*px*res
            b = wms("HR.ORTHOIMAGERY.ORTHOPHOTOS", (bx, by, bx+px*res, by+px*res), px, px, "image/jpeg")
            open(out, "wb").write(b); print("ortho", r, c, len(b))

def mnh():
    """LiDAR HD canopy height model (MNH, height above ground), 1 m, 4096x4096 float32, row0 = south."""
    out = os.path.join(D, "dem", "mnh_4096.f32")
    if os.path.exists(out): print("mnh exists"); return
    N, T = 4096, 1024
    M = np.zeros((N, N), np.float32)
    for ty in range(4):
        for tx in range(4):
            bx = X0 + tx*T; by = Y0 + ty*T
            b = wms("IGNF_LIDAR-HD_MNH_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93", (bx, by, bx+T, by+T), T, T, "image/x-bil;bits=32")
            a = np.frombuffer(b, "<f4").reshape(T, T)[::-1]
            M[ty*T:(ty+1)*T, tx*T:(tx+1)*T] = a
            print("mnh tile", ty, tx, float(a.min()), float(a.max()))
    M[(M < 0) | (M > 80)] = 0
    M.tofile(out)

def mns():
    """LiDAR HD surface model (MNS) 0.5 m around the two churches (spire heights). 512x512 float32 each, row0 = south."""
    for name, cx, cy in [("notre_dame", 1985, 2310), ("saint_hermeland", 3065, 3190)]:
        out = os.path.join(D, "dem", f"mns_{name}.f32")
        if os.path.exists(out): continue
        bx, by = X0 + cx - 128, Y0 + cy - 128
        b = wms("IGNF_LIDAR-HD_MNS_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93", (bx, by, bx+256, by+256), 512, 512, "image/x-bil;bits=32")
        a = np.frombuffer(b, "<f4").reshape(512, 512)[::-1].copy(); a.tofile(out); print("mns", name, float(a.max()))

LAYERS = ["batiment", "troncon_de_route", "troncon_de_voie_ferree", "surface_hydrographique", "troncon_hydrographique",
          "zone_de_vegetation", "commune", "terrain_de_sport", "cimetiere", "construction_surfacique", "construction_lineaire",
          "construction_ponctuelle", "haie", "toponymie", "zone_d_activite_ou_d_interet", "plan_d_eau", "equipement_de_transport",
          "reservoir", "pylone", "ligne_electrique", "voie_nommee", "erp"]
def vectors():
    bbox = f"{X0},{Y0},{X0+SIZE},{Y0+SIZE},urn:ogc:def:crs:EPSG::2154"
    for L in LAYERS:
        out = os.path.join(D, "vector", L + ".geojson")
        if os.path.exists(out): continue
        feats, start = [], 0
        while True:
            try:
                r = get(WFS, dict(SERVICE="WFS", VERSION="2.0.0", REQUEST="GetFeature", TYPENAMES="BDTOPO_V3:"+L,
                    OUTPUTFORMAT="application/json", SRSNAME="EPSG:2154", BBOX=bbox, COUNT=5000, STARTINDEX=start), tries=4)
            except RuntimeError as e:
                print("skip", L, e); break
            j = r.json(); f = j.get("features", [])
            feats += f; start += len(f)
            if len(f) < 5000: break
        json.dump({"type": "FeatureCollection", "features": feats}, open(out, "w"))
        print("vector", L, len(feats))

if __name__ == "__main__":
    json.dump(dict(crs="EPSG:2154", x0=X0, y0=Y0, size=SIZE, dem_res=2049, ortho_res=0.5, ortho_tile_px=2048,
                   note="Unity: x = E - x0, z = N - y0, y = height (m). 1 unit = 1 m"), open(os.path.join(D, "region.json"), "w"), indent=1)
    what = sys.argv[1:] or ["dem", "mnh", "mns", "vectors", "ortho"]
    for w in what: globals()[w]()
