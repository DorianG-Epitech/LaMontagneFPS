#!/usr/bin/env python3
"""Pack ImportData/city.bin (LMC2, float32) into a compact web format (LMW1) for the WebGL build:
positions int16 (cm, relative to the 256 m chunk origin), octahedral normals int8x2, UVs uint16 with a per-chunk
range, indices uint16 when possible. Output: Assets/LaMontagne/Resources/LaMontagne/city_web_N.bytes (45 MB parts) (+ meta.json copy)."""
import os, struct, sys, shutil, numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = os.path.join(ROOT, "ImportData", "city.bin")
dst_dir = os.path.join(ROOT, "Assets", "LaMontagne", "Resources", "LaMontagne")
os.makedirs(dst_dir, exist_ok=True)
b = open(src, "rb").read(); o = 0
def rd(fmt):
    global o; v = struct.unpack_from(fmt, b, o); o += struct.calcsize(fmt); return v
assert b[:4] == b"LMC2"; o = 4
nm, = rd("<i"); names = []
for _ in range(nm):
    n, = rd("<i"); names.append(b[o:o + n].decode()); o += n
nc, = rd("<i")
out = bytearray(b"LMW1"); out += struct.pack("<i", nm)
for n in names: e = n.encode(); out += struct.pack("<i", len(e)) + e
chunks = []
for _ in range(nc):
    n, = rd("<i"); name = b[o:o + n].decode(); o += n
    nv, = rd("<i")
    P = np.frombuffer(b, "<f4", nv * 3, o).reshape(nv, 3); o += nv * 12
    N = np.frombuffer(b, "<f4", nv * 3, o).reshape(nv, 3); o += nv * 12
    UV = np.frombuffer(b, "<f4", nv * 2, o).reshape(nv, 2); o += nv * 8
    ns, = rd("<i"); subs = []
    for _ in range(ns):
        mid, ni = rd("<ii"); idx = np.frombuffer(b, "<i4", ni, o); o += ni * 4; subs.append((mid, idx))
    chunks.append((name, P, N, UV, subs))
out += struct.pack("<i", len(chunks))
maxerr = 0
for name, P, N, UV, subs in chunks:
    pmin = P.min(0).astype(np.float64); prng = np.maximum(P.max(0) - pmin, 1e-3)
    q = np.round((P - pmin) / prng * 65535).astype(np.uint16)
    perr = float((np.abs(q / 65535 * prng + pmin - P)).max()); maxperr = max(globals().get("maxperr", 0), perr); globals()["maxperr"] = maxperr
    # octahedral normal encoding
    n = N / np.maximum(np.abs(N).sum(1, keepdims=True), 1e-9)
    x, y, z = n[:, 0], n[:, 1], n[:, 2]   # y = up in Unity space; fold on y
    ex = np.where(y >= 0, x, (1 - np.abs(z)) * np.sign(x + 1e-12))
    ez = np.where(y >= 0, z, (1 - np.abs(x)) * np.sign(z + 1e-12))
    on = np.round(np.stack([ex, ez], 1) * 127).astype(np.int8)
    # uvs: shift by integer floor (texture repeat) and quantize over the chunk range
    u0 = np.floor(UV.min(0)); uvs = UV - u0; rng = np.maximum(uvs.max(0), 1e-6)
    uq = np.round(uvs / rng * 65535).astype(np.uint16)
    e = float(np.abs(uq / 65535 * rng - uvs).max())
    uvf32 = e > 0.004
    if not uvf32: maxerr = max(maxerr, e)
    nm_ = name.encode()
    out += struct.pack("<i", len(nm_)) + nm_
    out += struct.pack("<i", len(P)) + struct.pack("<3f", *pmin) + struct.pack("<3f", *prng) + struct.pack("<2f", *rng) + struct.pack("<i", 1 if uvf32 else 0)
    out += q.astype("<u2").tobytes() + on.tobytes()
    out += uvs.astype("<f4").tobytes() if uvf32 else uq.astype("<u2").tobytes()
    if not uvf32 and (len(P) * 4) % 4: pass
    out += struct.pack("<i", len(subs))
    small = len(P) <= 65535
    for mid, idx in subs:
        out += struct.pack("<iii", mid, len(idx), 16 if small else 32)
        out += idx.astype("<u2" if small else "<u4").tobytes()
        if (len(idx) * (2 if small else 4)) % 4: out += b"\0\0"
# split into parts < 45 MB so every file stays under GitHub's 50 MB warning (CityLoader concatenates them)
for f in os.listdir(dst_dir):
    if f.startswith("city_web"): os.remove(os.path.join(dst_dir, f))
PART = 45 * 1024 * 1024
for i in range(0, len(out), PART):
    open(os.path.join(dst_dir, f"city_web_{i // PART}.bytes"), "wb").write(out[i:i + PART])
shutil.copy(os.path.join(ROOT, "ImportData", "meta.json"), os.path.join(dst_dir, "meta.json"))
print("chunks", len(chunks), "bytes", len(out), "uv max quant err", round(maxerr, 5), "pos max err m", round(globals().get("maxperr", 0), 4))
