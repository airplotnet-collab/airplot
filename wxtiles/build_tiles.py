#!/usr/bin/env python3
"""
wxtiles/build_tiles.py - Airplot's own model data for the TAF Map "ECMWF forecast" chart (2026-11-15).

Downloads the latest ECMWF IFS open-data run (0.25 deg, CC BY 4.0 - commercial use allowed with
attribution: "ECMWF"), with visibility estimated from its own fields (vis_rules.py - ECMWF open data has no
visibility) and the model ground height from ECMWF's own orography,
and cuts the globe into small 10 x 10 degree tiles the browser can read for any spot:

  site/wx/meta.json                      run time, steps, variable list and encodings
  site/wx/t/<lat0>_<lon0>.bin            one tile (gzip): header + packed arrays

Tile = 41 x 41 grid points (0.25 deg, edges shared with the neighbours so the page can interpolate),
every variable for every step (0 - 120 h, every 3 h; model icing / turbulence too, in their own g/ tiles). Values are packed small (int8 / uint8 / int16
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

STEPS = list(range(0, int(os.environ.get("WX_MAX_STEP", "120")) + 1, 3))   # 0 - 120 h every 3 h (WX_MAX_STEP: fewer, for a check)
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
LATG = (np.stack([(90 - r * RES)[:, None] * np.ones((1, TP)) for r in _rows]).astype(np.float32) if NINE
        else ((90 - np.arange(NLAT) * RES)[:, None] * np.ones((1, NLON))).astype(np.float32))
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
    ("vis",   "u1", 0.05, 0.0,   "lin"),   # visibility, statute miles (max 12.7) - estimated from ECMWF (vis_rules.py)
    # (2026-11-16, CN) for the ceiling estimate (ECMWF open data has no cloud base): humidity and heights of the two
    # lowest levels, and the cloud liquid water in the column (high under real stratus / fog)
    ("z1000", "i2", 1.0, 0.0,    "lin"),   # heights of 1000 / 925 hPa, m ASL (1000 hPa can be below sea level)
    ("z925",  "i2", 1.0, 0.0,    "lin"),
    ("r1000", "u1", 1.0, 0.0,    "lin"),   # humidity, %
    ("r925",  "u1", 1.0, 0.0,    "lin"),
    ("tclw",  "u1", 0.01, 0.0,   "sqrt"),  # total column cloud liquid water, kg/m2 (= mm), up to 6.4
    # (2026-11-21, CN) upper-level inventory complete: every stored level (1000 / 925 / 850 / 700 / 500 hPa) has its
    # height, temperature and humidity. Added at the end so the fields before keep their place.
    ("t1000", "i1", 1.0, 0.0,    "lin"),   # 1000 hPa temperature, deg C
    ("z500",  "i2", 1.0, 0.0,    "lin"),   # 500 hPa height, m ASL
    ("r500",  "u1", 1.0, 0.0,    "lin"),   # 500 hPa humidity, %
    # (2026-11-21, CN) upper air for the route weather (winds / temperature at cruise), the tephigram and upper-level layers:
    # winds at 500 / 300 / 250 hPa (~FL180 / FL300 / FL340) in kt (2 kt steps: jet-stream speeds up to 254 kt fit in one byte),
    # temperature and height at 300 / 250 hPa (500 hPa has them already). At the end, so the fields before keep their place.
    ("u500",  "i1", 2.0, 0.0,    "lin"),
    ("v500",  "i1", 2.0, 0.0,    "lin"),
    ("u300",  "i1", 2.0, 0.0,    "lin"),
    ("v300",  "i1", 2.0, 0.0,    "lin"),
    ("u250",  "i1", 2.0, 0.0,    "lin"),
    ("v250",  "i1", 2.0, 0.0,    "lin"),
    ("t300",  "i1", 1.0, 0.0,    "lin"),   # deg C
    ("t250",  "i1", 1.0, 0.0,    "lin"),
    ("z300",  "i2", 1.0, 0.0,    "lin"),   # m ASL
    ("z250",  "i2", 1.0, 0.0,    "lin"),
    # (2026-11-21, CN) humidity at 300 / 250 hPa too: dew points up there (wx.html layers, tephigram, route weather)
    ("r300",  "u1", 1.0, 0.0,    "lin"),
    ("r250",  "u1", 1.0, 0.0,    "lin"),
    # (2026-11-22, CN) the 100 m wind (ECMWF 100u / 100v), kt: low-level wind shear against the 10 m wind on the TAF Map
    ("u100",  "i1", 1.0, 0.0,    "lin"),
    ("v100",  "i1", 1.0, 0.0,    "lin"),
]
# (2026-11-16, CN) model icing / turbulence / freezing level (icing_turb.py) for the TAF Map's GFA-style layer. In tiles
# of their own (<out>/g/<lat0>_<lon0>.bin - the same layout, these variables only, steps up to DER_MAX_STEP), so the
# map can read a whole chart's worth of them without the full tiles. World set only (not the 9 km one).
# icg = intensity 0-3 (none, LGT, MOD, SEV) + 4 x type (RIME, MXD, CLR); trb = intensity + 4 x cause (MECH, LEE, WS, CAT);
# base / top / freezing level in hundreds of ft ASL (scale 2 = 200 ft steps)
DVARS = [
    ("icg",   "u1", 1.0, 0.0,    "lin"),
    ("icgb",  "u1", 2.0, 0.0,    "lin"),
    ("icgt",  "u1", 2.0, 0.0,    "lin"),
    ("trb",   "u1", 1.0, 0.0,    "lin"),
    ("trbb",  "u1", 2.0, 0.0,    "lin"),
    ("trbt",  "u1", 2.0, 0.0,    "lin"),
    ("fzl",   "u1", 2.0, 0.0,    "lin"),
]
DER = os.environ.get("WX_DER", "0" if NINE else "1") == "1"
DER_MAX_STEP = int(os.environ.get("WX_DER_MAX_STEP", "120"))   # (2026-11-21, CN) every step, as the other fields (was 48)
DSTEPS = [h for h in STEPS if h <= DER_MAX_STEP] if DER else []
MISSING = {"i1": -128, "u1": 255, "i2": -32768}
LIMITS = {"i1": (-127, 127), "u1": (0, 254), "i2": (-32767, 32767)}
MS2KT = 1.943844

data = {name: np.full((NS,) + GRID, np.nan, dtype=np.float32) for name, *_ in VARS}
ddata = {name: np.full((len(DSTEPS),) + GRID, np.nan, dtype=np.float32) for name, *_ in DVARS}
OLD = os.environ.get("WX_OLD") == "1"   # (check only) the previous rules too, into <out>/g_old/
dold = {name: np.full((len(DSTEPS),) + GRID, np.nan, dtype=np.float32) for name, *_ in DVARS} if OLD else None
elev = np.zeros(GRID, dtype=np.float32)       # model ground height, m (ECMWF orography)
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
    SFC = {"2t", "2d", "msl", "10u", "10v", "10fg", "10fg3", "tcc", "lcc", "mcc", "hcc", "tp", "sf", "mucape", "tclw", "100u", "100v"}   # (2026-11-22) + 100 m wind
    PL = {"t": {1000, 925, 850, 700, 500, 300, 250}, "u": {850, 700, 500, 300, 250}, "v": {850, 700, 500, 300, 250}, "r": {1000, 925, 850, 700, 500, 300, 250}, "gh": {1000, 925, 850, 700, 500, 300, 250}}
    import icing_turb
    NX = {k: icing_turb.NEED.get(k, set()) | icing_turb.OPTIONAL.get(k, set()) for k in set(icing_turb.NEED) | set(icing_turb.OPTIONAL)}
    PLX = {k: set(PL.get(k, set())) | NX.get(k, set()) for k in set(PL) | set(NX)}   # + icing / turbulence (+ vertical velocity)
    # (2026-11-16, CN) the model ground height comes from ECMWF itself now: its surface geopotential "z" (orography,
    # step 0 only) / g - no GFS any more. Step 0 is decoded first, so icing / turbulence (above the ground only) have it.
    gfs_c = None
    seen = set()
    def get(step):
        PLs = PLX if step in DSTEPS else PL
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
                    if (j.get("levtype") == "sfc" and (p in SFC or (p == "z" and step == 0))) or (j.get("levtype") == "pl" and p in PLs and lev is not None and int(lev) in PLs[p]):
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
    levs = {}  # step -> {(name, level): field} for icing / turbulence (one step at a time, dropped once used)
    wnose = {} # step -> top of a warm layer aloft over a sub-zero surface (freezing rain once precipitation is known)
    nolift = set()
    def handle(gid, g):
        sn = eccodes.codes_get(gid, "shortName")
        step = int(eccodes.codes_get(gid, "endStep")) if eccodes.codes_is_defined(gid, "endStep") else int(eccodes.codes_get(gid, "step"))
        if step not in STEPS:
            return
        k = STEPS.index(step)
        lev = eccodes.codes_get(gid, "level") if eccodes.codes_is_defined(gid, "level") else 0
        if step in DSTEPS and sn in ("t", "r", "gh", "u", "v", "w") and lev in PLX.get(sn, ()):
            levs.setdefault(k, {})[(sn, lev)] = (g - 273.15 if sn == "t" else g * MS2KT if sn in ("u", "v") else np.clip(g, 0, 100) if sn == "r" else g).astype(np.float32)
        if sn == "2t": data["t2"][k] = g - 273.15
        elif sn == "2d": data["td2"][k] = g - 273.15
        elif sn == "msl": data["msl"][k] = g / 100.0
        elif sn == "10u": data["u10"][k] = g * MS2KT
        elif sn == "10v": data["v10"][k] = g * MS2KT
        elif sn in ("100u", "u100"): data["u100"][k] = g * MS2KT
        elif sn in ("100v", "v100"): data["v100"][k] = g * MS2KT
        elif sn in ("10fg", "fg10", "i10fg", "10fg3"): data["gust"][k] = g * MS2KT
        elif sn in ("tcc", "lcc", "mcc", "hcc"):
            data[sn][k] = g * (100.0 if np.nanmax(g) <= 1.5 else 1.0)
        elif sn in ("tp", "sf"): acc.setdefault(sn, {})[k] = g * 1000.0   # m -> mm
        elif sn in ("mucape", "cape"): data["cape"][k] = g
        elif sn == "t" and lev in (1000, 925, 850, 700, 500, 300, 250): data["t%d" % lev][k] = g - 273.15
        elif sn == "gh" and lev in (1000, 925, 850, 700, 500, 300, 250): data["z%d" % lev][k] = g
        elif sn in ("u", "v") and lev in (850, 700, 500, 300, 250): data["%s%d" % (sn, lev)][k] = g * MS2KT
        elif sn == "r" and lev in (1000, 925, 850, 700, 500, 300, 250): data["r%d" % lev][k] = np.clip(g, 0, 100)
        elif sn == "tclw": data["tclw"][k] = g
        elif sn == "z" and eccodes.codes_get(gid, "typeOfLevel") == "surface":   # orography: geopotential / g
            global have_elev
            elev[:] = g / 9.80665; have_elev = True
    def derive(k):
        L = levs.pop(k, {})
        miss = [key for p in icing_turb.NEED for key in [(p, l) for l in icing_turb.NEED[p]] if key not in L]
        if miss:
            log("  step", STEPS[k], "no icing / turbulence - missing", miss[:4]); return
        if k not in nolift and not any(key[0] == "w" for key in L):
            nolift.add(k); log("  step", STEPS[k], "no vertical velocity (w) - icing lift is orographic only")
        # (2026-11-21) precipitation / snowfall in the 3 h before this step (each file is one step, in order, so the
        # step before is already read): FZRA / FZDZ / CB icing need it
        def per3h(sn):
            a = acc.get(sn, {})
            if k == 0: return np.zeros(GRID, np.float32)
            if k in a and (k - 1) in a: return np.maximum(0, a[k] - a[k - 1])
            return None
        try:
            j = DSTEPS.index(STEPS[k])
            ddata["icg"][j], ddata["icgb"][j], ddata["icgt"][j] = icing_turb.icing(L, elev, data["t2"][k], data["cape"][k], per3h("tp"), per3h("sf"),
                                                                                   data["lcc"][k], data["mcc"][k], LATG, RES)
            ddata["trb"][j], ddata["trbb"][j], ddata["trbt"][j] = icing_turb.turbulence(L, elev, data["u10"][k], data["v10"][k], data["gust"][k], LATG, RES)
            ddata["fzl"][j] = icing_turb.freezing_level(L, elev, data["t2"][k])
            wnose[k] = icing_turb.warm_nose(L, elev, data["t2"][k])
            if OLD:
                import icing_turb_old as io_
                dold["icg"][j], dold["icgb"][j], dold["icgt"][j] = io_.icing(L, elev, data["t2"][k], data["cape"][k], per3h("tp"), per3h("sf"), data["lcc"][k], data["mcc"][k], LATG, RES)
                dold["trb"][j], dold["trbb"][j], dold["trbt"][j] = io_.turbulence(L, elev, data["u10"][k], data["v10"][k], data["gust"][k], LATG, RES)
                dold["fzl"][j] = ddata["fzl"][j]
        except Exception as e:
            log("  step", STEPS[k], "icing / turbulence failed:", e)
    t0 = time.time(); nf = 0
    for f in files:
        nf += read_grib(f, lambda gid, g: handle(gid, cut(g))); os.remove(f)
        for k in list(levs): derive(k)   # (each file is one step)
    log("decoded", nf, "fields in", round(time.time() - t0), "s")
    for sn, byk in acc.items():
        prev = None
        for k in range(NS):
            cur = byk.get(k)
            if cur is None: continue
            data[sn][k] = np.maximum(0, cur - prev) if prev is not None else 0 * cur
            prev = cur
    # (2026-11-21) freezing rain / drizzle icing is worked out in icing_turb.icing now (it gets the 3 h precipitation)
    if DSTEPS:
        nd = sum(1 for j in range(len(DSTEPS)) if not np.isnan(ddata["icg"][j]).all()); j4 = min(4, len(DSTEPS) - 1)
        log("icing / turbulence for", nd, "of", len(DSTEPS), "steps; MOD+ icing share at step %d: %.3f, MOD+ turbulence: %.3f" % (
            DSTEPS[j4], float(np.nanmean((ddata["icg"][j4] % 4) >= 2)), float(np.nanmean((ddata["trb"][j4] % 4) >= 2))))
        ic, tr = ddata["icg"][j4], ddata["trb"][j4]
        log("  icing by type (MOD+): RIME %.3f MXD %.3f CLR %.3f" % tuple(float(np.nanmean((ic % 4 >= 2) & (ic // 4 == t))) for t in (0, 1, 2)))
        log("  icing MOD %.3f SEV %.3f | turbulence by cause (MOD+): MECH %.3f LEE %.3f WS %.3f CAT %.3f, SEV %.3f | land 30-60N MOD+ icing %.3f turb %.3f" % (
            float(np.nanmean(ic % 4 == 2)), float(np.nanmean(ic % 4 == 3)),
            *[float(np.nanmean((tr % 4 >= 2) & (tr // 4 == c))) for c in (1, 2, 3, 4)], float(np.nanmean(tr % 4 == 3)),
            float(np.nanmean(((ic % 4) >= 2)[(LATG >= 30) & (LATG <= 60) & (elev > 1)])) if not NINE else 0.0,
            float(np.nanmean(((tr % 4) >= 2)[(LATG >= 30) & (LATG <= 60) & (elev > 1)])) if not NINE else 0.0))
    # the gust at step 0 is not given (it is the max over the period before): use step 3's
    if np.isnan(data["gust"][0]).all() and NS > 1:
        data["gust"][0] = data["gust"][1]
    # (2026-11-16) after step 90 ECMWF names the gust "10fg3" (max of the last 3 h), not "10fg" - both are read now.
    # (2026-11-15) a step still without a gust: fill it from the
    # next one (its max covers this time), else from the one before
    have = [k for k in range(NS) if not np.isnan(data["gust"][k]).all()]
    for k in range(NS):
        if k in have or not have: continue
        nxt = [h for h in have if h > k]; prv = [h for h in have if h < k]
        data["gust"][k] = data["gust"][nxt[0] if nxt else prv[-1]]
    log("gust steps filled:", NS - len(have))
    run = run.replace(tzinfo=None)
    return run, got, gfs_c

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
    # (2026-11-16) the icing / turbulence / freezing level tiles (world set): <out>/g/<lat0>_<lon0>.bin
    ng = 0; gtotal = 0
    if DSTEPS and not NINE:
        os.makedirs(OUT + "/g", exist_ok=True)
        dpacked = {name: pack(name, dt_, sc, off, tr, ddata[name]) for name, dt_, sc, off, tr in DVARS}
        for lat0 in range(-90, 90, TILE):
            r_top = int(round((90 - (lat0 + TILE)) / RES))
            rows = np.clip(np.arange(r_top, r_top + TP), 0, NLAT - 1)
            for lon0 in range(-180, 180, TILE):
                c0 = int(round((lon0 + 180) / RES)); cols = (np.arange(c0, c0 + TP)) % NLON
                buf = bytearray()
                for name, *_ in DVARS:
                    buf += dpacked[name][:, rows][:, :, cols].tobytes()
                hdr = json.dumps({"lat0": lat0, "lon0": lon0, "n": TP, "res": RES}).encode()
                blob = gzip.compress(struct.pack("<4sI", b"APWX", len(hdr)) + hdr + bytes(buf), 6)
                with open(f"{OUT}/g/{lat0}_{lon0}.bin", "wb") as f:
                    f.write(blob)
                ng += 1; gtotal += len(blob)
        log("icing / turbulence tiles", ng, "total", round(gtotal / 1e6, 1), "MB")
        if OLD:
            os.makedirs(OUT + "/g_old", exist_ok=True)
            opk = {name: pack(name, dt_, sc, off, tr, dold[name]) for name, dt_, sc, off, tr in DVARS}
            for lat0 in range(-90, 90, TILE):
                r_top = int(round((90 - (lat0 + TILE)) / RES)); rows = np.clip(np.arange(r_top, r_top + TP), 0, NLAT - 1)
                for lon0 in range(-180, 180, TILE):
                    c0 = int(round((lon0 + 180) / RES)); cols = (np.arange(c0, c0 + TP)) % NLON
                    buf = bytearray()
                    for name, *_ in DVARS: buf += opk[name][:, rows][:, :, cols].tobytes()
                    hdr = json.dumps({"lat0": lat0, "lon0": lon0, "n": TP, "res": RES}).encode()
                    with open(f"{OUT}/g_old/{lat0}_{lon0}.bin", "wb") as f: f.write(gzip.compress(struct.pack("<4sI", b"APWX", len(hdr)) + hdr + bytes(buf), 6))
    meta = {
        "source": "ECMWF IFS open data " + ("0.1 deg (9 km)" if NINE else "0.25 deg") + " (CC BY 4.0) - visibility estimated from it (dew-point spread, wind, rain, snow); ground height: its own orography (surface geopotential)",
        "vis": "estimated",
        "gfa": ({"dir": "g/", "steps": DSTEPS, "icgTypes": ["RIME", "MXD", "CLR"], "trbCauses": ["", "MECH", "LEE", "WS", "CAT"],
                 "vars": [{"name": nme, "type": t, "scale": sc, "offset": off, "tr": tr, "missing": MISSING[t]} for nme, t, sc, off, tr in DVARS],
                 "order": "per variable: [step][row from north][col from west]",
                 "note": "model icing (cloud-based: temperature, lift, convection, freezing precipitation; MOD / SEV only) / turbulence / freezing level: rules of thumb on ECMWF fields (wxtiles/icing_turb.py), not a forecaster's GFA"} if (DSTEPS and not NINE) else None),
        "resol": RESOL, "tiles": ["%d_%d" % t for t in TILES9] if NINE else None, "regions": {k: [[min(a), max(a) + TILE], [min(b), max(b) + TILE]] for k, (a, b) in REGIONS.items()} if NINE else None,
        "attribution": "Contains modified ECMWF data, CC BY 4.0.",
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
    run, got, c = ecmwf()   # (c: no second model any more - None)
    # visibility from ECMWF's own fields (dew-point spread, 10 m wind, rain, snow) - see vis_rules.py
    from vis_rules import vis_sm
    for k in range(NS):
        data["vis"][k] = vis_sm(data["t2"][k], data["td2"][k], np.hypot(data["u10"][k], data["v10"][k]), data["tp"][k], data["sf"][k])
    log("visibility estimated from ECMWF fields; share of points under 3 SM at step 12: %.3f" % float(np.nanmean(data["vis"][min(4, NS - 1)] < 3)))
    for name, *_ in VARS:
        miss = int(np.isnan(data[name]).all(axis=tuple(range(1, data[name].ndim))).sum())
        log(f"{name:5s} missing steps: {miss}/{NS}")
    write_tiles(run, c)
