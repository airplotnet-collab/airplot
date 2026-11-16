#!/usr/bin/env python3
"""
wxtiles/build_tiles.py - Airplot's own model data for the TAF Map "ECMWF forecast" chart (2026-11-15).

Downloads the latest ECMWF IFS open-data run (0.25 deg, CC BY 4.0 - commercial use allowed with
attribution: "ECMWF") plus GFS visibility (NOAA, public domain - ECMWF open data has no visibility),
and cuts the globe into small 10 x 10 degree tiles the browser can read for any spot:

  site/wx/meta.json                      run time, steps, variable list and encodings
  site/wx/t/<lat0>_<lon0>.bin            one tile (gzip): header + packed arrays

Tile = 41 x 41 grid points (0.25 deg, edges shared with the neighbours so the page can interpolate),
every variable for every step (0 - 120 h, every 3 h). Values are packed small (int8 / uint8 / int16
with a scale) so the whole globe stays a few hundred MB.

Run by .github/workflows/wxtiles.yml 4 times a day; the output is published with GitHub Pages.
"""
import datetime as dt
import gzip
import json
import math
import os
import re
import struct
import sys
import time

import numpy as np
import requests

STEPS = list(range(0, 121, 3))           # 0 - 120 h every 3 h
NS = len(STEPS)
# (2026-11-15) WX_RES=0p1: the 9 km (0.1 deg) ECMWF data, cut only for the REGIONS below (the world stays 0.25 deg).
RESOL = os.environ.get("WX_RES", "0p25")
NINE = RESOL == "0p1"
RES = 0.1 if NINE else 0.25
NLAT, NLON = int(round(180 / RES)) + 1, int(round(360 / RES))   # 90 .. -90, -180 .. (180 - RES)
TILE = 10                                # degrees
TP = int(round(TILE / RES)) + 1          # 41 points per side (0.25), 101 (0.1)
OUT = os.environ.get("WX_OUT", "site/wx")
# 9 km regions, as 10 x 10 degree tiles (south-west corners): lat0 / lon0 ranges, ends included
REGIONS = {
    "North America": (range(20, 80, 10), range(-170, -50, 10)),   # 20-80N, 170-50W
    "Europe":        (range(30, 80, 10), range(-30, 50, 10)),     # 30-80N, 30W-50E
    "East Asia":     (range(20, 50, 10), range(110, 150, 10)),    # 20-50N, 110-150E: Hong Kong, Taiwan, Japan
    "Australia":     (range(-50, -10, 10), range(110, 160, 10)),  # 50-10S, 110-160E
}
TILES9 = sorted({(la, lo) for las, los in REGIONS.values() for la in las for lo in los}) if NINE else None
if NINE:
    _rows = [np.clip(np.arange(int(round((90 - (la + TILE)) / RES)), int(round((90 - (la + TILE)) / RES)) + TP), 0, NLAT - 1) for la, lo in TILES9]
    _cols = [(np.arange(int(round((lo + 180) / RES)), int(round((lo + 180) / RES)) + TP)) % NLON for la, lo in TILES9]
    GRID = (len(TILES9), TP, TP)
else:
    GRID = (NLAT, NLON)
def cut(g):
    """a whole-globe field on our grid -> what we keep (9 km: the region tiles only)."""
    if not NINE: return g
    return np.stack([g[np.ix_(r, c)] for r, c in zip(_rows, _cols)]).astype(np.float32)

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

# ── variables: name, dtype, scale, offset, transform ('lin' or 'sqrt'), missing ──
# stored = round((value - offset) / scale) (sqrt: round(sqrt(value) / scale)); the page undoes it.
VARS = [
    ("t2",    "i2", 0.1, 0.0,    "lin"),   # 2 m temperature, deg C
    ("td2",   "i2", 0.1, 0.0,    "lin"),   # 2 m dew point, deg C
    ("msl",   "i2", 0.1, 1000.0, "lin"),   # MSL pressure, hPa
    ("u10",   "i1", 1.0, 0.0,    "lin"),   # 10 m wind u, kt
    ("v10",   "i1", 1.0, 0.0,    "lin"),   # 10 m wind v, kt
    ("gust",  "u1", 1.0, 0.0,    "lin"),   # 10 m gust (max in the last period), kt
    ("tcc",   "u1", 1.0, 0.0,    "lin"),   # cloud cover %, total / low / mid / high
    ("lcc",   "u1", 1.0, 0.0,    "lin"),
    ("mcc",   "u1", 1.0, 0.0,    "lin"),
    ("hcc",   "u1", 1.0, 0.0,    "lin"),
    ("tp",    "u1", 0.05, 0.0,   "sqrt"),  # precipitation in the 3 h before, mm
    ("sf",    "u1", 0.05, 0.0,   "sqrt"),  # snowfall (water equivalent) in the 3 h before, mm
    ("cape",  "u1", 0.25, 0.0,   "sqrt"),  # most-unstable CAPE, J/kg
    ("t925",  "i1", 1.0, 0.0,    "lin"),   # temperatures aloft, deg C
    ("t850",  "i1", 1.0, 0.0,    "lin"),
    ("t700",  "i1", 1.0, 0.0,    "lin"),
    ("t500",  "i1", 1.0, 0.0,    "lin"),
    ("z850",  "i2", 1.0, 0.0,    "lin"),   # heights of 850 / 700 hPa, m ASL
    ("z700",  "i2", 1.0, 0.0,    "lin"),
    ("u850",  "i1", 1.0, 0.0,    "lin"),   # winds aloft, kt
    ("v850",  "i1", 1.0, 0.0,    "lin"),
    ("u700",  "i1", 1.0, 0.0,    "lin"),
    ("v700",  "i1", 1.0, 0.0,    "lin"),
    ("r850",  "u1", 1.0, 0.0,    "lin"),   # humidity aloft, %
    ("r700",  "u1", 1.0, 0.0,    "lin"),
    ("vis",   "u1", 0.05, 0.0,   "lin"),   # visibility (GFS), statute miles (max 12.7)
]
MISSING = {"i1": -128, "u1": 255, "i2": -32768}
LIMITS = {"i1": (-127, 127), "u1": (0, 254), "i2": (-32767, 32767)}
MS2KT = 1.943844

data = {name: np.full((NS,) + GRID, np.nan, dtype=np.float32) for name, *_ in VARS}
elev = np.zeros(GRID, dtype=np.float32)       # model ground height, m (GFS)
have_elev = False

def to_grid(values, ni, nj, lat0, lon0, dj_pos, res=None):
    """GRIB values -> a grid of lat 90..-90, lon -180..(180 - res)."""
    res = res or RES
    a = np.asarray(values, dtype=np.float32).reshape(nj, ni)
    if dj_pos:            # south -> north: flip
        a = a[::-1]
    lon0 = ((lon0 + 180.0) % 360.0) - 180.0
    shift = int(round((lon0 + 180.0) / res))
    if shift:
        a = np.roll(a, shift, axis=1)
    return a

def read_grib(path, handle, res=None):
    """every field of a GRIB file on a regular res-degree global grid -> handle(gid, grid)."""
    res = res or RES
    nlat, nlon = int(round(180 / res)) + 1, int(round(360 / res))
    import eccodes
    n = 0
    with open(path, "rb") as f:
        while True:
            gid = eccodes.codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                ni = eccodes.codes_get(gid, "Ni"); nj = eccodes.codes_get(gid, "Nj")
                if ni != nlon or nj != nlat:
                    continue
                lat0 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
                lon0 = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
                dj_pos = eccodes.codes_get(gid, "jScansPositively") == 1
                vals = eccodes.codes_get_values(gid)
                miss = eccodes.codes_get(gid, "missingValue") if eccodes.codes_is_defined(gid, "missingValue") else None
                grid = to_grid(vals, ni, nj, lat0, lon0, dj_pos, res)
                if miss is not None:
                    grid[grid == miss] = np.nan
                handle(gid, grid)
                n += 1
            finally:
                eccodes.codes_release(gid)
    return n

# ── ECMWF ──
# (2026-11-15) our own downloader: the ecmwf-opendata client waited 120 s after every "503 busy" from the
# mirror and the job ran out of time. Here: read each step's .index, fetch only our fields (adjacent ones
# merged into one request), short retries, and switch mirror (AWS -> ECMWF's own server) when one is busy.
MIRRORS = [m for m in os.environ.get("ECMWF_MIRRORS", "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com,https://data.ecmwf.int/forecasts").split(",") if m]
SESSION = requests.Session()
SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16))

def http_get(path, rng=None, tries=8):
    """GET from the first mirror that answers; short backoff on 429/5xx."""
    last = None
    for attempt in range(tries):
        m = MIRRORS[attempt % len(MIRRORS)]
        try:
            r = SESSION.get(m + "/" + path, headers={"Range": "bytes=%d-%d" % rng} if rng else None, timeout=120)
            if r.status_code in (200, 206):
                return r.content if rng else r.text
            if r.status_code == 404:
                return None
            last = "HTTP %d from %s" % (r.status_code, m.split("/")[2])
        except Exception as e:
            last = str(e)[:100]
        time.sleep(min(30, 2 * (attempt + 1)))
    raise RuntimeError(last or "download failed")

# the 0.25 deg set has one file per step (...-fc.grib2); the 9 km set two: surface (-fc-sfc) and pressure levels (-fc-pl)
PARTS = ["fc-sfc", "fc-pl"] if NINE else ["fc"]
def ec_path(run, stream, step, ext, part="fc"):
    return ("%s/%02dz/ifs/" + RESOL + "/%s/%s%02d0000-%dh-%s-%s.%s") % (run.strftime("%Y%m%d"), run.hour, stream, run.strftime("%Y%m%d"), run.hour, step, stream, part, ext)

def ecmwf():
    # newest run whose last step is out (00/12Z: stream "oper"; 06/18Z: "scda" or "oper")
    now = dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    run = stream = None
    for back in range(0, 48, 6):
        c = now - dt.timedelta(hours=back); c = c.replace(hour=c.hour - c.hour % 6)
        for st in ("oper", "scda"):
            try:
                if all(http_get(ec_path(c, st, STEPS[-1], "index", pt), tries=2) for pt in PARTS):
                    run, stream = c, st; break
            except Exception as e:
                log("  check", c, st, e)
        if run: break
    if not run:
        raise SystemExit("no complete ECMWF run found")
    log("ECMWF run", run, stream, "mirrors", [m.split("/")[2] for m in MIRRORS])
    SFC = {"2t", "2d", "msl", "10u", "10v", "10fg", "tcc", "lcc", "mcc", "hcc", "tp", "sf", "mucape"}
    PL = {"t": {925, 850, 700, 500}, "u": {850, 700}, "v": {850, 700}, "r": {850, 700}, "gh": {850, 700}}
    seen = set()
    def get(step):
        target = "ec_%03d.grib2" % step
        with open(target, "wb") as f:
            for pt in PARTS:
                idx = http_get(ec_path(run, stream, step, "index", pt))
                if not idx:
                    log("  no index for step", step, pt); continue
                want = []
                for line in idx.splitlines():
                    if not line.strip(): continue
                    j = json.loads(line); p = j.get("param"); lev = j.get("levelist")
                    if (j.get("levtype") == "sfc" and p in SFC) or (j.get("levtype") == "pl" and p in PL and lev is not None and int(lev) in PL[p]):
                        want.append((int(j["_offset"]), int(j["_length"]))); seen.add(p)
                want.sort(); ranges = []
                for o, n in want:   # merge neighbours -> fewer requests
                    if ranges and o == ranges[-1][1] + 1: ranges[-1][1] = o + n - 1
                    else: ranges.append([o, o + n - 1])
                for o, e in ranges:
                    f.write(http_get(ec_path(run, stream, step, "grib2", pt), (o, e)))
        return target if os.path.getsize(target) else None
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time(); files = []
    with ThreadPoolExecutor(6) as ex:
        for f in ex.map(get, STEPS):
            if f: files.append(f)
    log("downloaded", len(files), "steps,", round(sum(os.path.getsize(f) for f in files) / 1e6), "MB in", round(time.time() - t0), "s; params", sorted(seen))
    sfc_ok, pl_ok = sorted(seen & SFC), sorted(seen & set(PL))
    got = set(sfc_ok + pl_ok)
    import eccodes
    acc = {}   # tp / sf: accumulated since the start -> per 3 h
    def handle(gid, g):
        sn = eccodes.codes_get(gid, "shortName")
        step = int(eccodes.codes_get(gid, "endStep")) if eccodes.codes_is_defined(gid, "endStep") else int(eccodes.codes_get(gid, "step"))
        if step not in STEPS:
            return
        k = STEPS.index(step)
        lev = eccodes.codes_get(gid, "level") if eccodes.codes_is_defined(gid, "level") else 0
        if sn == "2t": data["t2"][k] = g - 273.15
        elif sn == "2d": data["td2"][k] = g - 273.15
        elif sn == "msl": data["msl"][k] = g / 100.0
        elif sn == "10u": data["u10"][k] = g * MS2KT
        elif sn == "10v": data["v10"][k] = g * MS2KT
        elif sn in ("10fg", "fg10", "i10fg", "10fg3"): data["gust"][k] = g * MS2KT
        elif sn in ("tcc", "lcc", "mcc", "hcc"):
            data[sn][k] = g * (100.0 if np.nanmax(g) <= 1.5 else 1.0)
        elif sn in ("tp", "sf"): acc.setdefault(sn, {})[k] = g * 1000.0   # m -> mm
        elif sn in ("mucape", "cape"): data["cape"][k] = g
        elif sn == "t" and lev in (925, 850, 700, 500): data["t%d" % lev][k] = g - 273.15
        elif sn == "gh" and lev in (850, 700): data["z%d" % lev][k] = g
        elif sn in ("u", "v") and lev in (850, 700): data["%s%d" % (sn, lev)][k] = g * MS2KT
        elif sn == "r" and lev in (850, 700): data["r%d" % lev][k] = g
    t0 = time.time(); nf = 0
    for f in files:
        nf += read_grib(f, lambda gid, g: handle(gid, cut(g))); os.remove(f)
    log("decoded", nf, "fields in", round(time.time() - t0), "s")
    for sn, byk in acc.items():
        prev = None
        for k in range(NS):
            cur = byk.get(k)
            if cur is None: continue
            data[sn][k] = np.maximum(0, cur - prev) if prev is not None else 0 * cur
            prev = cur
    # the gust at step 0 is not given (it is the max over the period before): use step 3's
    if np.isnan(data["gust"][0]).all() and NS > 1:
        data["gust"][0] = data["gust"][1]
    # (2026-11-15) later in the run ECMWF gives the gust less often (every 6 h): fill a missing step from the
    # next one (its max covers this time), else from the one before
    have = [k for k in range(NS) if not np.isnan(data["gust"][k]).all()]
    for k in range(NS):
        if k in have or not have: continue
        nxt = [h for h in have if h > k]; prv = [h for h in have if h < k]
        data["gust"][k] = data["gust"][nxt[0] if nxt else prv[-1]]
    log("gust steps filled:", NS - len(have))
    run = run.replace(tzinfo=None)
    return run, got

# ── GFS visibility + ground height (NOAA, public domain) ──
_gfs_w = None
def gfs_to_ours(g):
    """a 0.25 deg global field (721 x 1440) -> our grid / region tiles."""
    global _gfs_w
    if not NINE: return g
    if _gfs_w is None:  # where each tile point falls in the 0.25 grid (once)
        lat = np.stack([90 - r * RES for r in _rows]); lon = np.stack([((c * RES) % 360) - 180 for c in _cols])
        fy = np.clip((90 - lat) / 0.25, 0, 719.9999); fx = ((lon + 180) / 0.25) % 1440
        y0 = np.floor(fy).astype(int); x0 = np.floor(fx).astype(int); wy = (fy - y0)[:, :, None]; wx = (fx - x0)[:, None, :]
        _gfs_w = (y0, x0, (x0 + 1) % 1440, wy, wx)
    y0, x0, x1, wy, wx = _gfs_w
    out = []
    for t in range(len(TILES9)):
        r0, r1 = y0[t][:, None], (y0[t] + 1)[:, None]; c0, c1 = x0[t][None, :], x1[t][None, :]
        a = g[r0, c0] * (1 - wy[t]) * (1 - wx[t]) + g[r0, c1] * (1 - wy[t]) * wx[t] + g[r1, c0] * wy[t] * (1 - wx[t]) + g[r1, c1] * wy[t] * wx[t]
        out.append(a)
    return np.stack(out).astype(np.float32)
GFS = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
def gfs(run_ec):
    global have_elev
    # newest GFS cycle that is complete to the hours we need (published ~4.5 h after the cycle)
    now = dt.datetime.utcnow()
    for back in range(0, 48, 6):
        c = (now - dt.timedelta(hours=back)).replace(minute=0, second=0, microsecond=0)
        c = c.replace(hour=c.hour - c.hour % 6)
        if c > run_ec: continue  # (2026-11-15) a GFS run newer than the ECMWF one has no data for its first hours
        need = int((run_ec - c).total_seconds() // 3600) + STEPS[-1]
        need = min(384, max(0, need - need % 3))
        url = f"{GFS}/gfs.{c:%Y%m%d}/{c:%H}/atmos/gfs.t{c:%H}z.pgrb2.0p25.f{need:03d}.idx"
        if requests.head(url, timeout=30).status_code == 200:
            break
    else:
        log("no GFS cycle found - no visibility"); return None
    log("GFS cycle", c)
    import eccodes
    def fetch_raw(fh, pats):   # bytes of the wanted fields (network only - safe in threads)
        base = f"{GFS}/gfs.{c:%Y%m%d}/{c:%H}/atmos/gfs.t{c:%H}z.pgrb2.0p25.f{fh:03d}"
        idx = requests.get(base + ".idx", timeout=60)
        if idx.status_code != 200: return {}
        lines = idx.text.strip().split("\n"); out = {}
        for i, line in enumerate(lines):
            parts = line.split(":")
            key = parts[3] + ":" + parts[4]
            if key in pats:
                start = int(parts[1]); end = int(lines[i + 1].split(":")[1]) - 1 if i + 1 < len(lines) else ""
                r = requests.get(base, headers={"Range": f"bytes={start}-{end}"}, timeout=120)
                if r.status_code in (200, 206): out[key] = r.content
        return out
    def decode(raw):  # GFS is 0.25 deg: onto our grid (9 km: bilinear to the region tiles' points)
        fn = "gfs_tmp.grib2"; open(fn, "wb").write(raw); res = {}
        read_grib(fn, lambda gid, g: res.setdefault("g", g), 0.25); os.remove(fn)
        g = res.get("g")
        return None if g is None else gfs_to_ours(g)
    def fetch(fh, pats):
        return {k: g for k, g in ((k, decode(b)) for k, b in fetch_raw(fh, pats).items()) if g is not None}
    f0 = fetch(0, ["HGT:surface"])
    if "HGT:surface" in f0:
        elev[:] = f0["HGT:surface"]; have_elev = True
    def one(k):
        valid = run_ec + dt.timedelta(hours=STEPS[k])
        fh = int((valid - c).total_seconds() // 3600)
        if fh < 0 or fh > 384: return k, None
        fh -= fh % 3
        for attempt in range(3):
            try:
                return k, fetch_raw(fh, ["VIS:surface"]).get("VIS:surface")
            except Exception as e:
                log("  GFS retry", fh, e); time.sleep(5)
        return k, None
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    with ThreadPoolExecutor(10) as ex:
        raws = list(ex.map(one, range(NS)))
    for k, raw in raws:
        if raw:
            g = decode(raw)
            if g is not None: data["vis"][k] = g / 1609.34
    log("GFS visibility in", round(time.time() - t0), "s")
    log("GFS visibility steps:", int(np.sum([~np.isnan(data["vis"][k]).all() for k in range(NS)])))
    return c

def pack(name, dtype, scale, offset, tr, arr):
    a = np.array(arr, dtype=np.float64)
    a = np.sqrt(np.maximum(a, 0)) / scale if tr == "sqrt" else (a - offset) / scale
    lo, hi = LIMITS[dtype]
    m = np.isnan(a)
    a = np.clip(np.round(np.nan_to_num(a)), lo, hi)
    a[m] = MISSING[dtype]
    return a.astype({"i1": "<i1", "u1": "<u1", "i2": "<i2"}[dtype])

def write_tiles(run, gfs_cycle):
    os.makedirs(OUT + "/t", exist_ok=True)
    # pre-pack every variable once (whole globe), then cut
    packed = {name: pack(name, dt_, sc, off, tr, data[name]) for name, dt_, sc, off, tr in VARS}
    elev_p = np.clip(np.round(elev), -500, 9000).astype("<i2")
    n = 0; total = 0
    if NINE:
        for t, (lat0, lon0) in enumerate(TILES9):
            buf = bytearray()
            for name, *_ in VARS:
                buf += np.ascontiguousarray(packed[name][:, t]).tobytes()
            buf += np.ascontiguousarray(elev_p[t]).tobytes()
            hdr = json.dumps({"lat0": lat0, "lon0": lon0, "n": TP, "res": RES}).encode()
            blob = gzip.compress(struct.pack("<4sI", b"APWX", len(hdr)) + hdr + bytes(buf), 6)
            with open(f"{OUT}/t/{lat0}_{lon0}.bin", "wb") as f:
                f.write(blob)
            n += 1; total += len(blob)
    for lat0 in ([] if NINE else range(-90, 90, TILE)):
        r_top = int(round((90 - (lat0 + TILE)) / RES))
        rows = np.clip(np.arange(r_top, r_top + TP), 0, NLAT - 1)
        for lon0 in range(-180, 180, TILE):
            c0 = int(round((lon0 + 180) / RES))
            cols = (np.arange(c0, c0 + TP)) % NLON
            buf = bytearray()
            for name, *_ in VARS:
                buf += packed[name][:, rows][:, :, cols].tobytes()
            buf += elev_p[rows][:, cols].tobytes()
            hdr = json.dumps({"lat0": lat0, "lon0": lon0, "n": TP, "res": RES}).encode()
            blob = gzip.compress(struct.pack("<4sI", b"APWX", len(hdr)) + hdr + bytes(buf), 6)
            with open(f"{OUT}/t/{lat0}_{lon0}.bin", "wb") as f:
                f.write(blob)
            n += 1; total += len(blob)
    meta = {
        "source": "ECMWF IFS open data " + ("0.1 deg (9 km)" if NINE else "0.25 deg") + " (CC BY 4.0) - visibility: NOAA GFS 0.25 deg",
        "resol": RESOL, "tiles": ["%d_%d" % t for t in TILES9] if NINE else None, "regions": {k: [[min(a), max(a) + TILE], [min(b), max(b) + TILE]] for k, (a, b) in REGIONS.items()} if NINE else None,
        "attribution": "Contains ECMWF data, CC BY 4.0. Visibility: NOAA GFS.",
        "run": run.strftime("%Y-%m-%dT%H:%MZ"), "gfs": gfs_cycle.strftime("%Y-%m-%dT%H:%MZ") if gfs_cycle else None,
        "made": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
        "steps": STEPS, "tile": TILE, "res": RES, "n": TP, "hasElev": have_elev,
        "vars": [{"name": nme, "type": t, "scale": sc, "offset": off, "tr": tr, "missing": MISSING[t]} for nme, t, sc, off, tr in VARS],
        "order": "per variable: [step][row from north][col from west]; then elevation int16 [row][col]",
    }
    with open(OUT + "/meta.json", "w") as f:
        json.dump(meta, f)
    log("tiles", n, "total", round(total / 1e6, 1), "MB")

if __name__ == "__main__":
    run, got = ecmwf()
    try:
        c = gfs(run)
    except Exception as e:
        log("GFS failed (no visibility this run):", e); c = None
    for name, *_ in VARS:
        miss = int(np.isnan(data[name]).all(axis=tuple(range(1, data[name].ndim))).sum())
        log(f"{name:5s} missing steps: {miss}/{NS}")
    write_tiles(run, c)
