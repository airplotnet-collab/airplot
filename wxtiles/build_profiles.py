#!/usr/bin/env python3
"""
wxtiles/build_profiles.py - (2026-11-24, CN) the upper-air profile tiles for the tephigram (modules/tephi.js).

Its own small job, separate from build_tiles.py (the map tiles stay as they are; these are read only when a
tephigram is opened). From the latest ECMWF IFS open-data run (0.25 deg, CC BY 4.0), every 3 h to +120 h:

  pressure levels 1000 925 850 700 600 500 400 300 250 200 150 hPa: temperature, relative humidity, height, wind
  vertical velocity (omega, Pa/s) at 850 700 500 hPa, surface pressure

thinned to 0.5 deg (about 50 km - plenty for a sounding), cut into 10 x 10 degree tiles of 21 x 21 points
(edges shared with the neighbours), 60 S - 80 N:

  site/wxp/meta.json                 run, steps, levels, variable list and encodings (as the map tiles' meta.json)
  site/wxp/p/<lat0>_<lon0>.bin       one tile (gzip): "APWX" + header + per variable [step][row from north][col from west]

Uploaded to Cloudflare R2 only (https://wx.airplot.net/wxp/...), by .github/workflows/wxprofiles.yml. Each run
overwrites the last, so the storage stays about the size of one run.
"""
import datetime as dt
import gzip
import json
import os
import struct
import time

import numpy as np
import requests

STEPS = list(range(0, 121, 3))                  # 0 - 120 h every 3 h
NS = len(STEPS)
LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150]
WLEV = [850, 700, 500]                          # vertical velocity
SRC_RES, RES, TILE = 0.25, 0.5, 10              # ECMWF grid, kept grid (every 2nd point), tile size (deg)
THIN = int(round(RES / SRC_RES))
NLAT, NLON = int(round(180 / RES)) + 1, int(round(360 / RES))   # 90 .. -90, -180 .. (180 - RES)
TP = int(round(TILE / RES)) + 1                 # 21 points per side
LAT_RANGE = range(-60, 80, TILE)                # tile south edges: 60 S - 80 N
OUT = os.environ.get("WX_OUT", "site/wxp")
MS2KT = 1.943844
MISSING = {"i1": -128, "u1": 255, "i2": -32768}
LIMITS = {"i1": (-127, 127), "u1": (0, 254), "i2": (-32767, 32767)}

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

# variables: name, dtype, scale, offset (stored = round((value - offset) / scale)); the page undoes it
VARS = []
for L in LEVELS:
    VARS += [("t%d" % L, "i2", 0.1, 0.0),          # deg C
             ("r%d" % L, "u1", 1.0, 0.0),          # %
             ("z%d" % L, "i2", 1.0, 0.0),          # m ASL (geopotential height)
             ("u%d" % L, "i1", 1.0 if L >= 400 else 2.0, 0.0),   # kt (2 kt steps at jet levels: up to 254 kt)
             ("v%d" % L, "i1", 1.0 if L >= 400 else 2.0, 0.0)]
VARS += [("w%d" % L, "i1", 0.05, 0.0) for L in WLEV]  # Pa/s (negative = rising)
VARS += [("sp", "i2", 0.1, 1000.0)]                    # surface pressure, hPa
VIDX = {v[0]: i for i, v in enumerate(VARS)}
data = {name: np.full((NS, NLAT, NLON), MISSING[dt_], dtype={"i1": "<i1", "u1": "<u1", "i2": "<i2"}[dt_]) for name, dt_, *_ in VARS}

def pack_into(name, k, g):
    _, dt_, sc, off = VARS[VIDX[name]]
    a = (np.asarray(g, dtype=np.float64) - off) / sc
    m = np.isnan(a); lo, hi = LIMITS[dt_]
    a = np.clip(np.round(np.nan_to_num(a)), lo, hi); a[m] = MISSING[dt_]
    data[name][k] = a

# ── ECMWF download (as build_tiles.py: read each step's .index, fetch only our fields, switch mirror when busy) ──
MIRRORS = [m for m in os.environ.get("ECMWF_MIRRORS", "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com,https://data.ecmwf.int/forecasts").split(",") if m]
SESSION = requests.Session()
SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16))

def http_get(path, rng=None, tries=8):
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

def ec_path(run, stream, step, ext):
    return "%s/%02dz/ifs/0p25/%s/%s%02d0000-%dh-%s-fc.%s" % (run.strftime("%Y%m%d"), run.hour, stream, run.strftime("%Y%m%d"), run.hour, step, stream, ext)

def latest_run():
    now = dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    for back in range(0, 48, 6):
        c = now - dt.timedelta(hours=back); c = c.replace(hour=c.hour - c.hour % 6)
        for st in ("oper", "scda"):
            try:
                if http_get(ec_path(c, st, STEPS[-1], "index"), tries=2):
                    return c, st
            except Exception as e:
                log("  check", c, st, e)
    raise SystemExit("no complete ECMWF run found")

PL = {"t": set(LEVELS), "r": set(LEVELS), "gh": set(LEVELS), "u": set(LEVELS), "v": set(LEVELS), "w": set(WLEV)}

def download(run, stream, step):
    idx = http_get(ec_path(run, stream, step, "index"))
    if not idx:
        log("  no index for step", step); return None
    want = []
    for line in idx.splitlines():
        if not line.strip(): continue
        j = json.loads(line); p = j.get("param"); lev = j.get("levelist")
        if (j.get("levtype") == "sfc" and p == "sp") or (j.get("levtype") == "pl" and p in PL and lev is not None and int(lev) in PL[p]):
            want.append((int(j["_offset"]), int(j["_length"])))
    want.sort(); ranges = []
    for o, n in want:   # neighbours merged -> fewer requests
        if ranges and o == ranges[-1][1] + 1: ranges[-1][1] = o + n - 1
        else: ranges.append([o, o + n - 1])
    target = "pf_%03d.grib2" % step
    with open(target, "wb") as f:
        for o, e in ranges:
            f.write(http_get(ec_path(run, stream, step, "grib2"), (o, e)))
    return target

def decode(path):
    """every field of one step's file -> packed, thinned to 0.5 deg, lat 90..-90, lon -180..(180 - 0.5)."""
    import eccodes
    nlat0, nlon0 = int(round(180 / SRC_RES)) + 1, int(round(360 / SRC_RES))
    n = 0; seen = set()
    with open(path, "rb") as f:
        while True:
            gid = eccodes.codes_grib_new_from_file(f)
            if gid is None: break
            try:
                ni = eccodes.codes_get(gid, "Ni"); nj = eccodes.codes_get(gid, "Nj")
                if ni != nlon0 or nj != nlat0: continue
                sn = eccodes.codes_get(gid, "shortName")
                step = int(eccodes.codes_get(gid, "endStep")) if eccodes.codes_is_defined(gid, "endStep") else int(eccodes.codes_get(gid, "step"))
                if step not in STEPS: continue
                k = STEPS.index(step)
                lev = int(eccodes.codes_get(gid, "level")) if eccodes.codes_is_defined(gid, "level") else 0
                a = np.asarray(eccodes.codes_get_values(gid), dtype=np.float32).reshape(nj, ni)
                if eccodes.codes_get(gid, "jScansPositively") == 1: a = a[::-1]
                lon0 = ((eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees") + 180.0) % 360.0) - 180.0
                sh = int(round((lon0 + 180.0) / SRC_RES))
                if sh: a = np.roll(a, sh, axis=1)
                if eccodes.codes_is_defined(gid, "missingValue"): a[a == eccodes.codes_get(gid, "missingValue")] = np.nan
                a = a[::THIN, ::THIN]
                if sn == "sp": pack_into("sp", k, a / 100.0)
                elif sn == "t" and lev in LEVELS: pack_into("t%d" % lev, k, a - 273.15)
                elif sn == "r" and lev in LEVELS: pack_into("r%d" % lev, k, np.clip(a, 0, 100))
                elif sn == "gh" and lev in LEVELS: pack_into("z%d" % lev, k, a)
                elif sn in ("u", "v") and lev in LEVELS: pack_into("%s%d" % (sn, lev), k, a * MS2KT)
                elif sn == "w" and lev in WLEV: pack_into("w%d" % lev, k, a)
                else: continue
                n += 1; seen.add(sn)
            finally:
                eccodes.codes_release(gid)
    return n, seen

def write(run):
    os.makedirs(OUT + "/p", exist_ok=True)
    n = 0; total = 0
    for lat0 in LAT_RANGE:
        r_top = int(round((90 - (lat0 + TILE)) / RES))
        rows = np.clip(np.arange(r_top, r_top + TP), 0, NLAT - 1)
        for lon0 in range(-180, 180, TILE):
            c0 = int(round((lon0 + 180) / RES)); cols = np.arange(c0, c0 + TP) % NLON
            buf = bytearray()
            for name, *_ in VARS:
                buf += np.ascontiguousarray(data[name][:, rows][:, :, cols]).tobytes()
            hdr = json.dumps({"lat0": lat0, "lon0": lon0, "n": TP, "res": RES}).encode()
            blob = gzip.compress(struct.pack("<4sI", b"APWX", len(hdr)) + hdr + bytes(buf), 6)
            with open("%s/p/%d_%d.bin" % (OUT, lat0, lon0), "wb") as f:
                f.write(blob)
            n += 1; total += len(blob)
    meta = {
        "source": "ECMWF IFS open data 0.25 deg (CC BY 4.0), thinned to 0.5 deg: pressure-level profiles for the tephigram",
        "attribution": "Contains modified ECMWF data, CC BY 4.0.",
        "run": run.strftime("%Y-%m-%dT%H:%MZ"), "made": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
        "steps": STEPS, "levels": LEVELS, "wlevels": WLEV, "tile": TILE, "res": RES, "n": TP, "dir": "p/",
        "lat": [min(LAT_RANGE), max(LAT_RANGE) + TILE], "hasElev": False,
        "vars": [{"name": nme, "type": t, "scale": sc, "offset": off, "tr": "lin", "missing": MISSING[t]} for nme, t, sc, off in VARS],
        "order": "per variable: [step][row from north][col from west]",
        "units": "t deg C, r %, z m ASL, u / v kt, w Pa/s (negative = rising), sp hPa",
    }
    with open(OUT + "/meta.json", "w") as f:
        json.dump(meta, f)
    log("profile tiles", n, "total", round(total / 1e6, 1), "MB")

if __name__ == "__main__":
    run, stream = latest_run()
    log("ECMWF run", run, stream)
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time(); got = 0; params = set(); mb = 0
    # download a few steps at a time, decode each as it arrives, then delete it (disk and memory stay small)
    with ThreadPoolExecutor(6) as ex:
        for path in ex.map(lambda s: download(run, stream, s), STEPS):
            if not path: continue
            mb += os.path.getsize(path) / 1e6
            nf, seen = decode(path); got += nf; params |= seen
            os.remove(path)
    log("downloaded and decoded", got, "fields,", round(mb), "MB in", round(time.time() - t0), "s; params", sorted(params))
    for name, dt_, *_ in VARS:
        miss = int((data[name] == MISSING[dt_]).all(axis=(1, 2)).sum())
        if miss: log("  %-6s missing steps: %d/%d" % (name, miss, NS))
    if got == 0:
        raise SystemExit("nothing decoded")
    write(run)
