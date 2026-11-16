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
obs = []
for bb in ["20,-170,72,-50", "30,-30,72,45", "20,110,46,150", "-46,110,-10,156"]:  # the 9 km regions: N. America, Europe, East Asia, Australia
    try: obs += requests.get("https://aviationweather.gov/api/data/metar", params={"bbox": bb, "hours": 16, "format": "json"}, timeout=120).json()
    except Exception as e: print("metar", bb, e)
print("METARs:", len(obs))
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
    if any(np.isnan(x) for x in (s["t2"][st], s["td2"][st], wind)): continue
    rows.append((o["icaoId"], m["steps"][st], min(ov, 10), est, min(float(s["vis"][st]), 10) if not np.isnan(s["vis"][st]) else np.nan,
                 round(s["t2"][st] - s["td2"][st], 2), round(wind), round(s["tp"][st], 2), round(s["sf"][st], 2), o.get("wxString") or "", float(s["t2"][st])))
rows.sort()
open("rows.json", "w").write(json.dumps(rows))

print("run", m["run"], "-", len(rows), "matched obs\n")
print("STN   +h   OBS  RULES   GFS  spread wind  tp3h  sf3h  wx")
for r in rows:
    if r[2] <= 5: print("%s %4d %5.2f %6.2f %5.2f  %5.1f %4d %5.2f %5.2f  %s" % r[:10])
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

# ── RH-based rules (2026-11-16, CN: "<2 C will have fog, often 3 C has mist - work with RH") ──
import itertools
obsv = np.array([r[2] for r in rows]); sp = np.array([r[5] for r in rows], float); w = np.array([r[6] for r in rows], float)
tp = np.array([r[7] for r in rows], float); sf = np.array([r[8] for r in rows], float)
t2 = np.array([r[10] for r in rows], float); td = t2 - sp
es = lambda x: np.exp(17.625 * x / (243.04 + x))
rh = 100 * es(td) / es(t2)
def precip(v):
    rain = np.maximum(0, tp - sf) / 3; snow = sf / 3
    v = np.minimum(v, np.where(rain >= 2.0, 3.0, np.where(rain >= 0.5, 5.0, 10.0)))
    sv = np.where(snow >= 3, 1.0, np.where(snow > 1.5, 2.0 - (snow - 1.5) / 1.5, np.where(snow >= 0.5, 5.0 - (snow - 0.5) * 2.0, np.where(snow > 0.05, 6.0, 10.0))))
    return np.minimum(v, sv)
def rule_rh(fr, mr, calm, lift):
    v = np.full(len(rows), 10.0)
    fog = rh >= fr
    v = np.where(fog & (w < calm), np.where(rh >= (fr + 100) / 2, 0.25, 0.5), v)
    v = np.where(fog & (w >= calm) & (w < lift), 1.0, v)
    v = np.where(fog & (w >= lift), 4.0, v)
    v = np.where(~fog & (rh >= mr) & (w < lift), np.minimum(v, 4.0), v)
    return precip(v)
def rule_spread(fs, ms, calm, lift):
    v = np.full(len(rows), 10.0)
    fog = sp < fs
    v = np.where(fog & (w < calm), np.where(sp < fs / 2, 0.25, 0.5), v)
    v = np.where(fog & (w >= calm) & (w < lift), 1.0, v)
    v = np.where(fog & (w >= lift), 4.0, v)
    v = np.where(~fog & (sp < ms) & (w < lift), np.minimum(v, 4.0), v)
    return precip(v)
def stats(f):
    out = []
    for th in (5, 2.99):
        a = np.sum((obsv <= th) & (f <= th)); b = np.sum((obsv > th) & (f <= th)); c = np.sum((obsv <= th) & (f > th))
        out += [a / max(1, a + b + c), int(a), int(b)]
    return out
gfs = np.array([r[4] if not np.isnan(r[4]) else 10 for r in rows])
print("\n%d obs · <= 5 SM: %d · under 3 SM: %d · RH quantiles 10/25/50/75/90%%: %s" % (len(rows), np.sum(obsv <= 5), np.sum(obsv < 3), np.round(np.percentile(rh, [10, 25, 50, 75, 90]), 1)))
print("RH where obs <= 5 SM, quantiles: %s · where obs > 5: %s" % (np.round(np.percentile(rh[obsv <= 5], [10, 25, 50, 75]), 1), np.round(np.percentile(rh[obsv > 5], [50, 75, 90]), 1)))
fmt = lambda name, st: print("%-48s CSI<=5 %.2f (hit %3d, false %3d) · CSI<3 %.2f (hit %3d, false %3d)" % (name, *st))
fmt("GFS (old)", stats(gfs))
fmt("CN: spread<2 fog, <3 mist, 5/10 kt", stats(rule_spread(2, 3, 5, 10)))
fmt("live now: spread<1.5 fog, 5/10 kt", stats(rule_spread(1.5, 1.5, 5, 10)))
res = []
for fr, mr, calm, lift in itertools.product([90, 92, 94, 95, 96, 97, 98], [80, 83, 85, 87, 89, 91, 93], [3, 5, 8], [10, 15]):
    if mr >= fr: continue
    st = stats(rule_rh(fr, mr, calm, lift)); res.append((st[0] + st[3], (fr, mr, calm, lift), st))
res.sort(key=lambda x: -x[0])
print("best RH rules (fog RH, mist RH, calm kt, lift kt):")
for t in res[:10]: fmt("  RH fog>=%d mist>=%d, %d/%d kt" % t[1], t[2])
