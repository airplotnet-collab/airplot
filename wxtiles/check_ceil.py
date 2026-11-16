"""(2026-11-16, CN) Check ceiling estimates against real METARs, for the steps of the published run that are already past.
ECMWF open data has no cloud base, so the ceiling is estimated:
  now:   low cloud >= 60 % -> base = 2 m spread x 400 ft (100 - 6,500 ft); mid cloud >= 60 % -> about 6,500 ft
  RH:    under enough low cloud (or cloud liquid water), the base is the lowest near-saturated level - the 2 m air,
         1000, 925 or 850 hPa - interpolated between levels by humidity
Prints the scores of each version and the best settings (CSI = hits / (hits + misses + false alarms))."""
import gzip, json, struct, time, itertools, requests, numpy as np
base = "https://wx.airplot.net/wx/"
m = requests.get(base + "meta.json", timeout=30).json()
run = time.mktime(time.strptime(m["run"], "%Y-%m-%dT%H:%MZ")) - time.timezone
T, R, N, NS = m["tile"], m["res"], m["n"], len(m["steps"])
names = [v["name"] for v in m["vars"]]
for need in ("r1000", "r925", "z1000", "z925", "tclw"):
    if need not in names: raise SystemExit("the published tiles do not have " + need + " yet - run the tile job first")
tiles = {}
def series(lat, lon):
    la0 = int(np.floor(lat / T) * T); lo0 = int(np.floor((lon + 180) / T) * T - 180)
    if (la0, lo0) not in tiles:
        tiles[(la0, lo0)] = gzip.decompress(requests.get(base + "t/%d_%d.bin?r=%s" % (la0, lo0, m["run"]), timeout=60).content)
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
for bb in ["20,-170,72,-50", "30,-30,72,45", "20,110,46,150", "-46,110,-10,156"]:
    try: obs += requests.get("https://aviationweather.gov/api/data/metar", params={"bbox": bb, "hours": 16, "format": "json"}, timeout=120).json()
    except Exception as e: print("metar", bb, e)
print("METARs:", len(obs))

def obs_ceiling(o):
    c = None
    for cl in o.get("clouds") or []:
        if cl.get("cover") in ("BKN", "OVC", "OVX", "VV") and cl.get("base") is not None:
            c = cl["base"] if c is None else min(c, cl["base"])
    if o.get("vertVis") is not None: c = o["vertVis"] * (100 if o["vertVis"] < 100 else 1) if c is None else min(c, o["vertVis"])
    return float(c) if c is not None else None

rows = []
for o in obs:
    if not o.get("obsTime") or o.get("lat") is None or not (o.get("clouds") or o.get("vertVis") is not None): continue
    k = (o["obsTime"] - run) / 3600.0
    if k < 0 or abs(k - round(k / 3) * 3) > 0.34: continue
    st = int(round(k / 3))
    if st >= NS: continue
    s = series(o["lat"], o["lon"])
    vals = {n: float(s[n][st]) for n in ("t2", "td2", "lcc", "mcc", "r1000", "r925", "r850", "z1000", "z925", "z850", "tclw")}
    if any(np.isnan(vals[n]) for n in ("t2", "td2", "lcc", "mcc")): continue
    vals["elev"] = float(o.get("elev") or 0)            # station, m
    vals["obs"] = obs_ceiling(o); vals["id"] = o["icaoId"]; vals["h"] = m["steps"][st]
    rows.append(vals)
print("run", m["run"], "-", len(rows), "matched obs")

def es(x): return np.exp(17.625 * x / (243.04 + x))
def ceil_now(v):
    sp = v["t2"] - v["td2"]
    if v["lcc"] >= 60: return min(6500, max(100, sp * 400))
    if v["mcc"] >= 60: return 6500
    return None
def ceil_rh(v, thr, lcc_min, w_min, fallback, fog=200):
    # fog: the 2 m air this humid gives a (very low) ceiling even without model low cloud (fog / stratus it misses)
    if 100 * es(v["td2"]) / es(v["t2"]) >= fog: return max(100, (v["t2"] - v["td2"]) * 400)
    low = v["lcc"] >= lcc_min or (w_min > 0 and not np.isnan(v["tclw"]) and v["tclw"] >= w_min)
    if low:
        rh2 = 100 * es(v["td2"]) / es(v["t2"]); sp = v["t2"] - v["td2"]
        lv = [(0.0, rh2)]
        for p in (1000, 925, 850):
            z, r = v["z%d" % p], v["r%d" % p]
            if np.isnan(z) or np.isnan(r): continue
            h = (z - v["elev"]) * 3.28084
            if h > 50: lv.append((h, r))
        lv.sort()
        if lv[0][1] >= thr: return min(6500, max(100, sp * 400))   # saturated at the ground: the spread rule (fog / very low stratus)
        for (h0, r0), (h1, r1) in zip(lv, lv[1:]):
            if r1 >= thr:
                f = (thr - r0) / max(1e-6, r1 - r0)
                return max(100, h0 + f * (h1 - h0))
        if fallback and v["lcc"] >= 60: return min(6500, max(100, sp * 400))
    if v["mcc"] >= 60: return 6500
    return None

INF = 1e9
def score(pred_fn):
    o = np.array([r["obs"] if r["obs"] is not None else INF for r in rows]); p = np.array([pred_fn(r) if pred_fn(r) is not None else INF for r in rows])
    out = {}
    for lim in (1000, 3001):
        a, b = o < lim, p < lim
        h, mi, fa = int((a & b).sum()), int((a & ~b).sum()), int((~a & b).sum())
        out[lim] = (h / max(1, h + mi + fa), h, mi, fa)
    cat = lambda x: np.where(x < 500, 0, np.where(x < 1000, 1, np.where(x <= 3000, 2, 3)))
    out["cat"] = float((cat(o) == cat(p)).mean())
    both = (o < 10000) & (p < 10000)
    out["medlog"] = float(np.median(np.abs(np.log10(p[both] / o[both])))) if both.any() else np.nan
    return out
def fmt(name, sc):
    print("%-44s CSI<1000 %.2f (h%d m%d fa%d)  CSI<=3000 %.2f (h%d m%d fa%d)  cat ok %.2f  |log err| %.2f" % (name, *sc[1000], *sc[3001], sc["cat"], sc["medlog"]))
n1k = sum(1 for r in rows if r["obs"] is not None and r["obs"] < 1000); n3k = sum(1 for r in rows if r["obs"] is not None and r["obs"] <= 3000)
print("observed: ceiling < 1000 ft:", n1k, " <= 3000 ft:", n3k, " of", len(rows), "\n")
fmt("now: lcc>=60 spread x 400", score(ceil_now))
res = []
for thr, lmin, wmin, fb, fog in itertools.product((88, 90, 91, 92, 93, 94), (30, 40, 50, 60), (0, 0.1), (False,), (200, 99, 98, 97, 96)):
    sc = score(lambda v: ceil_rh(v, thr, lmin, wmin, fb, fog))
    res.append(((sc[1000][0] + sc[3001][0]) / 2, (thr, lmin, wmin, fb, fog), sc))
res.sort(key=lambda x: -x[0])
print("\nbest RH rules (RH thr, low cloud min %, cloud water min kg/m2, fall back to spread):")
for t in res[:14]: fmt("  RH>=%d lcc>=%d tclw>=%.2f fb=%s fogRH>=%d" % t[1], t[2])
best = res[0][1]
print("\nsample (best rule): STN +h  OBS  NOW  RH-rule  lcc  rh2  r1000 r925 r850  tclw")
for r in rows[:0]:
    if r["obs"] is not None and r["obs"] <= 3000:
        print("%s %3d %5s %5s %6s %4.0f %4.0f %5.0f %4.0f %4.0f %5.2f" % (r["id"], r["h"], int(r["obs"]), ceil_now(r) and int(ceil_now(r)), (ceil_rh(r, *best) and int(ceil_rh(r, *best))),
              r["lcc"], 100 * es(r["td2"]) / es(r["t2"]), r["r1000"], r["r925"], r["r850"], r["tclw"]))
