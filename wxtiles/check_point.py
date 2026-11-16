"""One-off check (2026-11-15): visibility at one spot - our published tiles vs GFS's own value, time by time."""
import gzip, json, struct, sys, datetime as dt, requests, numpy as np, os
lat, lon = float(sys.argv[1]), float(sys.argv[2])
def tile_series(base):
    m = requests.get(base + "meta.json", timeout=30).json()
    T, R, N, NS = m["tile"], m["res"], m["n"], len(m["steps"])
    la0 = int(np.floor(lat / T) * T); lo0 = int(np.floor((lon + 180) / T) * T - 180)
    b = gzip.decompress(requests.get(base + "t/%d_%d.bin?r=%s" % (la0, lo0, m["run"]), timeout=60).content)
    hl = struct.unpack("<I", b[4:8])[0]; off = 8 + hl
    r = int(round((la0 + T - lat) / R)); c = int(round((lon - lo0) / R))
    out = {}
    for v in m["vars"]:
        sz = {"i1": 1, "u1": 1, "i2": 2}[v["type"]]; dtp = {"i1": "<i1", "u1": "<u1", "i2": "<i2"}[v["type"]]
        a = np.frombuffer(b[off:off + NS * N * N * sz], dtype=dtp).reshape(NS, N, N)[:, r, c].astype(float)
        a[a == v["missing"]] = np.nan
        a = (a * v["scale"]) ** 2 if v["tr"] == "sqrt" else a * v["scale"] + v["offset"]
        out[v["name"]] = a; off += NS * N * N * sz
    return m, out
import sys as _s
sys.path.insert(0, ".")
from vis_rules import vis_sm, rh_pct
for base in ["https://wx.airplot.net/wx9/", "https://wx.airplot.net/wx/"]:
    try:
        m, o = tile_series(base)
        print("==", base, "run", m["run"], "made", m.get("made"), "tile", m["tile"], "res", m["res"])
        print("step  vis  t2  td2  rh  w10kt  tp  sf  recomputed")
        for k, h in enumerate(m["steps"][:20]):
            w = np.hypot(o["u10"][k], o["v10"][k])
            print("%3d %5.2f %5.1f %5.1f %5.1f %5.1f %5.2f %5.2f %5.2f" % (h, o["vis"][k], o["t2"][k], o["td2"][k], rh_pct(o["t2"][k], o["td2"][k]), w, o["tp"][k], o["sf"][k], vis_sm(o["t2"][k], o["td2"][k], w, o["tp"][k], o["sf"][k])))
    except Exception as e:
        import traceback; traceback.print_exc()
_s.exit(0)
for base in ["https://wx.airplot.net/wx/", "https://wx.airplot.net/wx9/"]:
    try:
        m, o = tile_series(base)
        print("==", base, "run", m["run"], "gfs", m.get("gfs"))
        print("step  vis_SM  tp_mm  lcc%  t2")
        for k, h in enumerate(m["steps"][:41]):
            print("%3d  %6.2f  %5.2f  %4.0f  %5.1f" % (h, o["vis"][k], o["tp"][k], o["lcc"][k], o["t2"][k]))
    except Exception as e:
        print(base, "failed:", e)
# GFS itself: the VIS field at this point, a few forecast hours, straight from NOAA
m = requests.get("https://wx.airplot.net/wx/meta.json", timeout=30).json()
c = dt.datetime.strptime(m["gfs"], "%Y-%m-%dT%H:%MZ")
import eccodes
G = "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.%s/%s/atmos/gfs.t%sz.pgrb2.0p25.f%03d"
print("== GFS raw VIS at the point, cycle", c)
for fh in range(0, 49, 3):
    base = G % (c.strftime("%Y%m%d"), c.strftime("%H"), c.strftime("%H"), fh)
    idx = requests.get(base + ".idx", timeout=60).text.strip().split("\n")
    vis_lines = [l for l in idx if ":VIS:" in l]
    for i, line in enumerate(idx):
        p = line.split(":")
        if p[3] == "VIS" and p[4] == "surface":
            start = int(p[1]); end = int(idx[i + 1].split(":")[1]) - 1
            raw = requests.get(base, headers={"Range": "bytes=%d-%d" % (start, end)}, timeout=120).content
            open("v.grib2", "wb").write(raw)
            with open("v.grib2", "rb") as f:
                gid = eccodes.codes_grib_new_from_file(f)
                near = eccodes.codes_grib_find_nearest(gid, lat, lon % 360)[0]
                print("f%03d  VIS %.0f m = %.2f SM   (%d VIS records in the file: %s)" % (fh, near["value"], near["value"] / 1609.34, len(vis_lines), " | ".join(l.split(":", 3)[3] for l in vis_lines)))
                eccodes.codes_release(gid)
            break
