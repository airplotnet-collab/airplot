"""(2026-11-15) Check the visibility rules (vis_rules.py) and the GFS visibility against real METARs, for the
steps of the published run that are already past."""
import gzip, json, struct, time, requests, numpy as np
from vis_rules import vis_sm
IDS = "CYVR,CYYC,CYEG,CYYZ,CYUL,CYOW,CYWG,CYHZ,CYXE,CYQR,KSEA,KPDX,KSFO,KLAX,KDEN,KORD,KJFK,KBOS,KATL,KDFW,KIAH,KMSP,KDTW,KSLC,EGLL,EGKK,EGCC,EGPH,EHAM,EDDF,EDDM,LFPG,LEMD,LIRF,EIDW,EKCH,ESSA,VHHH,RCTP,RJTT,RJAA,RKSI,YSSY,YMML,YBBN,NZAA"
base = "https://wx.airplot.net/wx/"
m = requests.get(base + "meta.json", timeout=30).json()
run = time.mktime(time.strptime(m["run"], "%Y-%m-%dT%H:%MZ")) - time.timezone
T, R, N, NS = m["tile"], m["res"], m["n"], len(m["steps"])
tiles = {}
def series(lat, lon):
    la0 = int(np.floor(lat / T) * T); lo0 = int(np.floor((lon + 180) / T) * T - 180)
    if (la0, lo0) not in tiles:
        b = gzip.decompress(requests.get(base + "t/%d_%d.bin?r=%s" % (la0, lo0, m["run"]), timeout=60).content)
        tiles[(la0, lo0)] = b
    b = tiles[(la0, lo0)]; hl = struct.unpack("<I", b[4:8])[0]; off = 8 + hl
    fy, fx = (la0 + T - lat) / R, (lon - lo0) / R; r0, c0 = int(fy), int(fx); wy, wx = fy - r0, fx - c0
    out = {}
    for v in m["vars"]:
        sz = {"i1": 1, "u1": 1, "i2": 2}[v["type"]]; dtp = {"i1": "<i1", "u1": "<u1", "i2": "<i2"}[v["type"]]
        a = np.frombuffer(b[off:off + NS * N * N * sz], dtype=dtp).reshape(NS, N, N).astype(float)
        a[a == v["missing"]] = np.nan
        a = (a * v["scale"]) ** 2 if v["tr"] == "sqrt" else a * v["scale"] + v["offset"]
        out[v["name"]] = a[:, r0, c0] * (1 - wy) * (1 - wx) + a[:, r0, c0 + 1] * (1 - wy) * wx + a[:, r0 + 1, c0] * wy * (1 - wx) + a[:, r0 + 1, c0 + 1] * wy * wx
        off += NS * N * N * sz
    return out
obs = requests.get("https://aviationweather.gov/api/data/metar", params={"ids": IDS, "hours": 24, "format": "json"}, timeout=60).json()
def vnum(x):
    if x is None: return None
    s = str(x).replace("+", "")
    try: return float(s)
    except: 
        try: a, b = s.split("/"); return float(a) / float(b)
        except: return None
rows = []
for o in obs:
    if not o.get("obsTime") or o.get("lat") is None: continue
    k = (o["obsTime"] - run) / 3600.0
    if k < 0 or abs(k - round(k / 3) * 3) > 0.34: continue  # only obs within 20 min of a 3-hour step
    st = int(round(k / 3)); 
    if st >= NS: continue
    s = series(o["lat"], o["lon"])
    wind = float(np.hypot(s["u10"][st], s["v10"][st]))
    est = float(vis_sm(s["t2"][st], s["td2"][st], wind, s["tp"][st], s["sf"][st]))
    ov = vnum(o.get("visib"))
    if ov is None: continue
    rows.append((o["icaoId"], m["steps"][st], min(ov, 10), est, min(float(s["vis"][st]), 10) if not np.isnan(s["vis"][st]) else np.nan,
                 round(s["t2"][st] - s["td2"][st], 1), round(wind), round(s["tp"][st], 2), round(s["sf"][st], 2), o.get("wxString") or ""))
rows.sort()
print("run", m["run"], "-", len(rows), "matched obs\n")
print("STN   +h   OBS  RULES   GFS  spread wind  tp3h  sf3h  wx")
for r in rows:
    print("%s %4d %5.2f %6.2f %5.2f  %5.1f %4d %5.2f %5.2f  %s" % r)
cat = lambda v: 0 if v < 1 else 1 if v < 3 else 2 if v <= 5 else 3
names = ["<1", "1-3", "3-5", ">5"]
for col, lab in ((3, "RULES"), (4, "GFS")):
    good = [r for r in rows if not np.isnan(r[col])]
    hit = sum(cat(r[2]) == cat(r[col]) for r in good)
    print("\n%s: same category as observed %d/%d (%.0f%%)" % (lab, hit, len(good), 100 * hit / max(1, len(good))))
    M = np.zeros((4, 4), int)
    for r in good: M[cat(r[2]), cat(r[col])] += 1
    print("obs\\fcst " + " ".join("%5s" % n for n in names))
    for i in range(4): print("%8s " % names[i] + " ".join("%5d" % M[i, j] for j in range(4)))
    low_obs = [r for r in good if r[2] <= 5]; low_hit = sum(r[col] <= 5 for r in low_obs)
    fa = sum(r[col] <= 5 and r[2] > 5 for r in good)
    print("  obs <= 5 SM: caught %d/%d · forecast <= 5 SM but obs > 5: %d" % (low_hit, len(low_obs), fa))
