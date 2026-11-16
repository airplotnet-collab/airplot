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
RES = 0.25
NLAT, NLON = 721, 1440                   # 90 .. -90, -180 .. 179.75
TILE = 10                                # degrees
TP = int(TILE / RES) + 1                 # 41 points per side
OUT = os.environ.get("WX_OUT", "site/wx")

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

data = {name: np.full((NS, NLAT, NLON), np.nan, dtype=np.float32) for name, *_ in VARS}
elev = np.zeros((NLAT, NLON), dtype=np.float32)       # model ground height, m (GFS)
have_elev = False

def to_grid(values, ni, nj, lat0, lon0, dj_pos):
    """GRIB values -> our grid (lat 90..-90, lon -180..179.75)."""
    a = np.asarray(values, dtype=np.float32).reshape(nj, ni)
    if dj_pos:            # south -> north: flip
        a = a[::-1]
    lon0 = ((lon0 + 180.0) % 360.0) - 180.0
    shift = int(round((lon0 + 180.0) / RES))
    if shift:
        a = np.roll(a, shift, axis=1)
    return a

def read_grib(path, handle):
    import eccodes
    n = 0
    with open(path, "rb") as f:
        while True:
            gid = eccodes.codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                ni = eccodes.codes_get(gid, "Ni"); nj = eccodes.codes_get(gid, "Nj")
                if ni != NLON or nj != NLAT:
                    continue
                lat0 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
                lon0 = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
                dj_pos = eccodes.codes_get(gid, "jScansPositively") == 1
                vals = eccodes.codes_get_values(gid)
                miss = eccodes.codes_get(gid, "missingValue") if eccodes.codes_is_defined(gid, "missingValue") else None
                grid = to_grid(vals, ni, nj, lat0, lon0, dj_pos)
                if miss is not None:
                    grid[grid == miss] = np.nan
                handle(gid, grid)
                n += 1
            finally:
                eccodes.codes_release(gid)
    return n

# ── ECMWF ──
def ecmwf():
    from ecmwf.opendata import Client
    client = Client(source=os.environ.get("ECMWF_SOURCE", "aws"), model="ifs", resol="0p25")
    run = client.latest(type="fc", step=120, param="2t")
    log("ECMWF run", run)
    sfc = ["2t", "2d", "msl", "10u", "10v", "10fg", "tcc", "lcc", "mcc", "hcc", "tp", "sf", "mucape"]
    pl = ["t", "u", "v", "r", "gh"]
    date, hour = run.strftime("%Y%m%d"), run.hour
    # which parameters this run has (one small request each; a missing one is left out, not fatal)
    def probe(p, extra):
        try:
            client.retrieve(date=date, time=hour, type="fc", step=[3], param=[p], target=f"probe_{p}.grib2", **extra)
            os.remove(f"probe_{p}.grib2"); return True
        except Exception as e:
            log("  not available:", p, str(e)[:120]); return False
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex:
        sfc_ok = [p for p, ok in zip(sfc, ex.map(lambda p: probe(p, {}), sfc)) if ok]
        pl_ok = [p for p, ok in zip(pl, ex.map(lambda p: probe(p, {"levelist": [850]}), pl)) if ok]
    log("parameters:", sfc_ok, pl_ok)
    # (2026-11-15) the downloads in parallel: one request per step and level type, 10 at a time (one by one
    # took most of the run waiting on the network)
    jobs = [(s, "sfc", sfc_ok, {}) for s in STEPS] + [(s, "pl", pl_ok, {"levelist": [925, 850, 700, 500]}) for s in STEPS]
    def get(job):
        s, kind, params, extra = job
        target = f"ec_{kind}_{s:03d}.grib2"
        for attempt in range(3):
            try:
                Client(source=os.environ.get("ECMWF_SOURCE", "aws"), model="ifs", resol="0p25").retrieve(
                    date=date, time=hour, type="fc", step=[s], param=params, target=target, **extra)
                return target
            except Exception as e:
                log("  retry", target, str(e)[:100]); time.sleep(5 * (attempt + 1))
        return None
    t0 = time.time()
    with ThreadPoolExecutor(10) as ex:
        files = [f for f in ex.map(get, jobs) if f]
    log("downloaded", len(files), "files,", round(sum(os.path.getsize(f) for f in files) / 1e6), "MB in", round(time.time() - t0), "s")
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
        nf += read_grib(f, handle); os.remove(f)
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
    return run, got

# ── GFS visibility + ground height (NOAA, public domain) ──
GFS = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
def gfs(run_ec):
    global have_elev
    # newest GFS cycle that is complete to the hours we need (published ~4.5 h after the cycle)
    now = dt.datetime.utcnow()
    for back in range(0, 48, 6):
        c = (now - dt.timedelta(hours=back)).replace(minute=0, second=0, microsecond=0)
        c = c.replace(hour=c.hour - c.hour % 6)
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
    def decode(raw):
        fn = "gfs_tmp.grib2"; open(fn, "wb").write(raw); res = {}
        read_grib(fn, lambda gid, g: res.setdefault("g", g)); os.remove(fn)
        return res.get("g")
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
    for lat0 in range(-90, 90, TILE):
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
        "source": "ECMWF IFS open data 0.25 deg (CC BY 4.0) - visibility: NOAA GFS 0.25 deg",
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
        miss = int(np.isnan(data[name]).all(axis=(1, 2)).sum())
        log(f"{name:5s} missing steps: {miss}/{NS}")
    write_tiles(run, c)
