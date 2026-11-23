"""
wxtiles/icing_turb.py - model icing, turbulence and freezing level from the ECMWF pressure-level fields (2026-11-16, CN;
icing rewritten 2026-11-21 to follow a forecaster's GFA reasoning).

For the TAF Map's "Model icing & turb" layer, drawn like the Canadian GFA ICG/TURB/FZLVL panel. These are rules
of thumb applied to the model, NOT a forecaster's product:

ICING - every cloud can ice; it MUST hold supercooled droplets, and their supply has to keep up with the ice nuclei
that take them away. The column is read like a tephigram, every 150 m from the ground to 500 hPa (temperature,
humidity and lift interpolated between the levels):
  cloud: relative humidity >= 90 % (the same rule as the "Model cloud & wx" layer); each separate cloud layer is judged
  temperature score: above 0 C -> 0; 0 to -15 C -> 1 (most likely); -15 to -20 C -> 1 down to 0.6 (ice nuclei start
         working - and the model can be a few degrees too cold); -20 to -30 C -> 0.6 down to 0; below -30 C -> 0
  icing share = the depth-weighted score of the layer; cold share = the part of the layer colder than -20 C
  lift (supply), cm/s upward: the larger of ECMWF's vertical velocity (frontal / large-scale) and the wind blowing up
         the model terrain (orographic, fading out 3 km above the ground)
  (2026-11-21, tuned on CN's examples - Lake Erie MOD RIME at FL190 / -15 C, Gaylord MOD MX under -10 to -15 C tops, a dry
  Pickle Lake column, a deep cold-topped prairie cloud) - the column is read up to 400 hPa (~FL240, as the GFA):
  icing part = the cloud from 0 to about -22 C (score >= 0.45), judged on its own even when the cloud goes much colder
  MOD: BKN/OVC (low / mid cloud cover >= 50 %), >= 3,000 ft of icing part, mean score there >= 0.6, and a supply of
       droplets: a cloud top -16 C or warmer (no ice falling in) - or, with a colder top, a mean lift >= 2 cm/s
       (overrunning keeps the droplets coming while snow falls through)
  SEV: top -16 C or warmer, lift >= 13 cm/s, the icing part mostly 0 to -10 C, >= 5,000 ft of it, <= half the cloud below -20 C
  no LGT: anything weaker is left out
  type: RIME - stratiform (CAPE < 100) with the icing part averaging -10 C or colder (small droplets)
        CLR  - large drops / SLD:
               FZRA / PL: a warm layer aloft (> +1 C) over a sub-zero surface with precipitation (>= 0.3 mm in 3 h), under
                          a cloud layer with ice (top -10 C or colder) - snow that melts (2026-11-22)
                          -> SEV CLR from the surface to the top of the warm layer
               FZDZ: a low stratus layer (base under 6,500 ft above the ground, under 8,000 ft deep) with its top warmer
                     than -10 C, mostly below 0 C, and light precipitation (0.1 - 2 mm in 3 h), with no cloud layer
                     anywhere in the column holding ice (top -10 C or colder: seeder-feeder snow) (2026-11-22)
                     -> SEV CLR through the layer (from the surface when it is below 0 C there)
               CB:  CAPE >= 1000 J/kg with precipitation -> SEV CLR from the freezing level to the -40 C level or the
                    300 hPa height (the CB top), whichever is lower
               TCU: cloud reaching about -10 C and either CAPE 300 - 1000 J/kg with precipitation or lift >= 4 cm/s at the
                    icing levels, or EMBEDDED convection: a saturated stretch >= 2 km deep whose saturated equivalent
                    potential temperature does not rise with height (moist-neutral / unstable, even with CAPE ~0)
                    -> MOD CLR from the freezing level to the -10 C level
        MXD  - everything else (the most common on the GFA, e.g. frontal cloud)
  a point keeps its most severe icing (on a tie CLR > MXD > RIME; layers of the same kind are joined: lowest base, highest top)
TURBULENCE:
  MECH  - over land, surface wind >= 25 kt or gusts >= 35 kt (SEV: wind >= 40 / gusts >= 50): surface to 3,000 ft above it
  LEE   - mountain waves: ground >= 800 m and 700 hPa wind >= 40 kt (SEV >= 60 kt): surface to 5,000 ft above 700 hPa
  WS    - vertical wind shear between two levels (925 - 500 hPa): MOD >= 8 kt / 1,000 ft, SEV >= 12
  CAT   - 500 - 250 hPa: shear MOD >= 10 / SEV >= 14 kt per 1,000 ft, or the Ellrod index (shear x deformation)
          MOD >= 12, SEV >= 20 (x 1e-7 s-2)
FREEZING LEVEL: the lowest height where the temperature drops to 0 C (0 = below freezing at the surface).

Output per grid point (the most severe layer): intensity 0-3 + 4 x type / cause, base and top in hundreds of feet ASL.
"""
import numpy as np

ICG_TYPES = ["RIME", "MXD", "CLR"]                 # icg = intensity + 4 * type
TRB_CAUSES = ["", "MECH", "LEE", "WS", "CAT"]      # trb = intensity + 4 * cause
M2HFT = 3.28084 / 100.0                            # metres -> hundreds of feet
FT = 0.3048
ILEV = [1000, 925, 850, 700, 600, 500, 400]   # (2026-11-21) + 400 hPa: icing up to ~FL240, as on the GFA
CLEV = [300, 250]                                  # above ILEV: temperatures for the CB / TCU isotherms
TLEV = [925, 850, 700, 600, 500, 400, 300, 250]
R_EARTH = 6371000.0
KT2MS = 0.514444
RD, G0 = 287.05, 9.80665

# icing limits (see the notes above) - tune here
ICG = dict(
    cloud_rh=90.0, dz=150.0, cover=50.0,
    share=0.5, cold=0.5, depth_mod=2000 * FT, depth_sev=5000 * FT, warm_top=-16.0, icing_score=0.6, main_score=0.45,
    lift_mod=4.0, lift_cold=0.5, lift_sev=13.0, frac10_sev=0.5, etcu_depth=2000.0, etcu_grad=0.5,
    rime_depth=4000 * FT, rime_t=-10.0, conv=100.0, tcu=300.0, cb=1000.0, precip=0.3,
    fzdz_top=-10.0, fzdz_lo=0.1, fzdz_hi=2.0, fzdz_depth=8000 * FT, low_agl=1981.0, oro_fade=3000.0,
)

# the pressure-level fields this needs (for the download list)
NEED = {
    "t": set(ILEV) | set(CLEV),
    "r": set(ILEV),
    "gh": set(ILEV) | set(TLEV),
    "u": set(TLEV) | {1000},
    "v": set(TLEV) | {1000},
}
# wanted but not essential: without it the lift is the orographic part only
OPTIONAL = {"w": set(ILEV)}


def _layers(z, i, nlev):
    """bottom / top (m) of level i in a column of level heights z (nlev, ...): halfway to the neighbours."""
    lo = (z[i] + z[i - 1]) / 2 if i > 0 else z[i] - (z[i + 1] - z[i]) / 2
    hi = (z[i] + z[i + 1]) / 2 if i < nlev - 1 else z[i] + (z[i] - z[i - 1]) / 2
    return lo, hi


def _tscore(T):
    """supercooled-droplet likelihood from the temperature (C): 0 above 0, 1 at 0 to -15, 0.6 at -20, 0 at -30.
    (2026-11-21) softer below -15 C than before (0.3 at -20): the model can be a few degrees too cold (PIREP MOD RIME at
    -15 C where the model said -20 C)."""
    s = np.where(T > 0, 0.0, 1.0)
    s = np.where(T < -15, 1.0 - 0.4 * (-15.0 - T) / 5.0, s)
    s = np.where(T < -20, 0.6 * np.clip((T + 30.0) / 10.0, 0, 1), s)
    return s


def _thes(p, T):
    """saturated equivalent potential temperature (K) at p hPa, T C."""
    Tk = T + 273.15; es = 6.112 * np.exp(17.67 * T / (T + 243.5)); rs = 0.622 * es / np.maximum(p - es, 1.0)
    return Tk * (1000.0 / p) ** 0.2854 * np.exp(2.5e6 * rs / (1004.0 * Tk))


def embedded_tcu(L, elev):
    """(2026-11-21) embedded convection from the profile itself (even with CAPE ~0): a saturated stretch (RH >= 90 % at
    neighbouring levels above the ground) at least etcu_depth m deep whose saturated equivalent potential temperature does
    not rise with height (<= etcu_grad K per km - moist-neutral or unstable) -> True."""
    c = ICG; run = np.zeros(elev.shape, np.float32); best = np.zeros(elev.shape, np.float32)
    for a, b in zip(ILEV[:-1], ILEV[1:]):
        za, zb = L[("gh", a)], L[("gh", b)]
        ok = (za > elev + 50.0) & (L[("r", a)] >= c["cloud_rh"]) & (L[("r", b)] >= c["cloud_rh"])
        grad = (_thes(b, L[("t", b)]) - _thes(a, L[("t", a)])) / np.maximum((zb - za) / 1000.0, 0.1)
        ok &= grad <= c["etcu_grad"]
        run = np.where(ok, run + (zb - za), 0.0); best = np.maximum(best, run)
    return best >= c["etcu_depth"]


def isotherm(L, elev, t2, tc, levels=ILEV + CLEV):
    """the lowest height (m ASL) where the temperature drops to tc (C) going up; elev when the surface is already
    colder; nan when it is not reached by the top level."""
    out = np.where(t2 <= tc, elev, np.nan).astype(np.float32)
    zb = elev + 2.0; tb = t2.astype(np.float32)
    done = t2 <= tc
    for p in levels:
        if ("t", p) not in L or ("gh", p) not in L: continue
        T = L[("t", p)]; Z = L[("gh", p)]
        ok = (Z > zb) & ~done
        cross = ok & (T <= tc) & (tb > tc)
        f = np.where(cross, (tb - tc) / np.maximum(tb - T, 1e-6), 0)
        out = np.where(cross, zb + f * (Z - zb), out); done = done | cross
        zb = np.where(ok, Z, zb); tb = np.where(ok, T, tb)
    return out


def _lift(L, elev, lat, res_deg):
    """per ILEV level: upward motion in cm/s - the larger of ECMWF's vertical velocity and the orographic lift
    (10 m-to-level wind up the terrain slope, fading out oro_fade m above the ground)."""
    dy = R_EARTH * np.radians(res_deg)
    dx = np.maximum(R_EARTH * np.cos(np.radians(lat)) * np.radians(res_deg), 1000.0)
    dh_dx = np.gradient(elev, axis=-1) / dx; dh_dy = -np.gradient(elev, axis=-2) / dy   # rows go north -> south
    out = []
    for p in ILEV:
        Z = L[("gh", p)]
        agl = np.maximum(Z - elev, 0)
        oro = (L[("u", p)] * dh_dx + L[("v", p)] * dh_dy) * KT2MS * 100.0 * np.clip(1 - agl / ICG["oro_fade"], 0, 1)
        if ("w", p) in L:   # omega (Pa/s) -> w (cm/s): w = -omega R T / (p g)
            wm = -L[("w", p)] * RD * (L[("t", p)] + 273.15) / (p * 100.0 * G0) * 100.0
        else:
            wm = np.zeros_like(Z)
        out.append(np.maximum(wm, oro).astype(np.float32))
    return np.stack(out)


def icing(L, elev, t2, cape, tp3, sf3, lcc, mcc, lat, res_deg):
    """L[(name, level)] -> (code, base, top) arrays; code = intensity + 4 * type (RIME / MXD / CLR), base / top
    hundreds of ft ASL. tp3 / sf3: precipitation / snowfall (mm) in the 3 h before (None = not known: no FZRA / FZDZ
    / CB)."""
    c = ICG; shape = elev.shape
    t2 = np.nan_to_num(t2, nan=10.0); cv = np.nan_to_num(cape) if cape is not None else np.zeros(shape)
    tp = np.nan_to_num(tp3) if tp3 is not None else np.zeros(shape)
    sf = np.nan_to_num(sf3) if sf3 is not None else np.zeros(shape)
    lc = np.nan_to_num(lcc) if lcc is not None else np.full(shape, 100.0)
    mc = np.nan_to_num(mcc) if mcc is not None else np.full(shape, 100.0)
    T = np.stack([L[("t", p)] for p in ILEV]); RH = np.stack([L[("r", p)] for p in ILEV]); Z = np.stack([L[("gh", p)] for p in ILEV])
    W = _lift(L, elev, lat, res_deg)
    n = len(ILEV)

    best = np.zeros(shape, np.int8); btyp = np.zeros(shape, np.int8)
    bb = np.zeros(shape, np.float32); bt = np.zeros(shape, np.float32)

    def put(inten, typ, lo, hi):
        nonlocal best, btyp, bb, bt
        inten = np.asarray(inten, np.int8); typ = np.asarray(typ, np.int8)
        new = (inten > best) | ((inten == best) & (inten > 0) & (typ > btyp))
        same = (inten == best) & (inten > 0) & ~new
        bb = np.where(new, lo, np.where(same, np.minimum(bb, lo), bb)).astype(np.float32)
        bt = np.where(new, hi, np.where(same, np.maximum(bt, hi), bt)).astype(np.float32)
        best = np.where(new, inten, best); btyp = np.where(new, typ, btyp)

    # --- layer clouds: read the column every dz metres, from just above the lowest ground to the 500 hPa height
    dz = c["dz"]
    z0 = max(float(np.nanmin(elev)), 0.0)
    ztop = float(np.nanmax(Z[-1]))
    zs = np.arange(z0 + dz / 2, ztop, dz)
    st = {}
    def reset(m=None):
        for k in ("D", "sd", "cold", "dice", "d10", "lsum", "tsum", "smain", "ib", "it", "cb", "ct", "ttop"):
            v = {"ib": np.inf, "cb": np.inf, "it": -np.inf, "ct": -np.inf}.get(k, 0.0)
            if m is None: st[k] = np.full(shape, v, np.float32)
            else: st[k] = np.where(m, v, st[k]).astype(np.float32)
    reset()
    incl = np.zeros(shape, bool)

    def finish(m):
        """judge the layers that end at the points m."""
        D = st["D"]; Dm = np.maximum(D, 1.0); dice = st["dice"]
        share = st["sd"] / Dm; coldf = st["cold"] / Dm; f10 = st["d10"] / Dm
        lift = st["lsum"] / np.maximum(dice, 1.0); tmean = st["tsum"] / np.maximum(dice, 1.0)
        ttop = st["ttop"]; base_agl = st["cb"] - elev; top_agl = st["ct"] - elev
        cover = np.maximum(np.where(base_agl < c["low_agl"], lc, 0), np.where(top_agl >= c["low_agl"], mc, 0))
        # (2026-11-21) the icing part (0 to about -22 C) is judged on its own: a deep cloud with a cold top still ices in
        # its lower part. A top at -16 C or warmer (no ice falling in) is enough supply; a colder top needs lift >= 4 cm/s
        # and never gives SEV. SEV: strong lift, a top -16 C or warmer, mostly 0 to -10 C, deep.
        smain = st["smain"] / np.maximum(dice, 1.0); f10i = st["d10"] / np.maximum(dice, 1.0)
        warm = ttop >= c["warm_top"]
        supply = warm | (lift >= c["lift_cold"])   # cold top: some lift (overrunning ~2 cm/s) keeps the droplets coming
        mod = m & (cover >= c["cover"]) & (smain >= c["icing_score"]) & (dice >= c["depth_mod"]) & supply
        sev = mod & warm & (lift >= c["lift_sev"]) & (f10i >= c["frac10_sev"]) & (dice >= c["depth_sev"]) & (coldf <= c["cold"])
        rime = (cv < c["conv"]) & (tmean <= c["rime_t"])   # stratiform, the icing part mostly -10 C or colder: small droplets
        inten = np.where(sev, 3, np.where(mod, 2, 0)); typ = np.where(rime & ~sev, 0, 1)
        lo = np.maximum(st["ib"], elev); hi = st["it"]
        ok = m & np.isfinite(lo) & np.isfinite(hi)
        put(np.where(ok, inten, 0), typ, np.where(ok, lo, 0), np.where(ok, hi, 0))
        # freezing drizzle: a low, warm-topped stratus layer with light precipitation. (2026-11-22, CN) decided after the
        # whole column is read: only when NO cloud layer has ice (top -10 C or colder) - snow from a higher layer falling
        # through it (seeder-feeder) makes it snow; ECMWF's own rain / snow split is no longer used
        fzdz = (ok & (base_agl < c["low_agl"]) & (D < c["fzdz_depth"]) & (ttop > c["fzdz_top"]) & (ttop <= 0)
                & (share >= c["share"]) & (tp >= c["fzdz_lo"]) & (tp < c["fzdz_hi"]) & (cover >= c["cover"]))
        if fzdz.any(): pend.append((fzdz, np.where(t2 <= 0, elev, np.where(ok, lo, 0)), np.where(ok, st["ct"], 0)))
        icy = m & np.isfinite(st["ct"]) & (ttop <= c["fzdz_top"])
        iceany[...] = iceany | icy; icetop[...] = np.where(icy, np.maximum(icetop, st["ct"]), icetop)

    pend = []; iceany = np.zeros(shape, bool); icetop = np.full(shape, -np.inf, np.float32)   # (2026-11-22) cloud layers with ice
    anycloud = np.zeros(shape, bool); c10 = np.zeros(shape, bool); liftice = np.full(shape, -99.0, np.float32)
    for z in zs:
        # interpolate the levels to the height z
        Tz = np.full(shape, np.nan, np.float32); Rz = np.full(shape, np.nan, np.float32); Wz = np.zeros(shape, np.float32)
        below = z < Z[0]
        Tz = np.where(below, T[0], Tz); Rz = np.where(below, RH[0], Rz); Wz = np.where(below, W[0], Wz)
        for i in range(n - 1):
            m = (z >= Z[i]) & (z < Z[i + 1])
            f = np.where(m, (z - Z[i]) / np.maximum(Z[i + 1] - Z[i], 1.0), 0)
            Tz = np.where(m, T[i] + f * (T[i + 1] - T[i]), Tz)
            Rz = np.where(m, RH[i] + f * (RH[i + 1] - RH[i]), Rz)
            Wz = np.where(m, W[i] + f * (W[i + 1] - W[i]), Wz)
        cl = np.isfinite(Tz) & (z > elev + 50.0) & (Rz >= c["cloud_rh"])
        ends = incl & ~cl
        if ends.any():
            finish(ends); reset(ends)
        s = np.where(cl, _tscore(Tz), 0)
        main = cl & (s >= c["main_score"])   # 0 to about -22 C: the icing part (its base / top are the label)
        st["D"] += np.where(cl, dz, 0); st["sd"] += s * dz
        st["cold"] += np.where(cl & (Tz < -20), dz, 0)
        st["dice"] += np.where(main, dz, 0)
        st["d10"] += np.where(cl & (Tz <= 0) & (Tz >= -10), dz, 0)
        st["lsum"] += np.where(main, Wz * dz, 0); st["tsum"] += np.where(main, Tz * dz, 0); st["smain"] += np.where(main, s * dz, 0)
        c10 |= cl & (Tz <= -9.0); liftice = np.maximum(liftice, np.where(main, Wz, -99.0))
        st["cb"] = np.where(cl, np.minimum(st["cb"], z - dz / 2), st["cb"]); st["ct"] = np.where(cl, np.maximum(st["ct"], z + dz / 2), st["ct"])
        st["ib"] = np.where(main, np.minimum(st["ib"], z - dz / 2), st["ib"]); st["it"] = np.where(main, np.maximum(st["it"], z + dz / 2), st["it"])
        st["ttop"] = np.where(cl, Tz, st["ttop"])
        incl = cl; anycloud |= cl
    if incl.any():
        finish(incl)
    # (2026-11-23, CN) freezing drizzle: MOD CLR (as the GFA's "DUE FZDZ"); SEV CLR where it comes with a warm layer aloft or
    # heavier precipitation (>= 1 mm in 3 h) - freezing precipitation at a warm front, the GFA's "SEV CLR DUE FZ PCPN"
    wn_any = np.isfinite(warm_nose(L, elev, t2))
    for fzdz, lo_, hi_ in pend:
        f = fzdz & ~iceany
        put(np.where(f, np.where(wn_any | (tp >= 1.0), 3, 2), 0), 2, lo_, hi_)   # (2026-11-23, CN) MOD CLR, as the GFA ("MX / CLR DUE FZDZ"); SEV is for FZRA

    # --- convection: TCU (MOD CLR, freezing level to -10 C) and CB (SEV CLR, freezing level to -40 C / the CB top)
    z0c = isotherm(L, elev, t2, 0.0)
    pr = tp >= c["precip"]
    if ("gh", 300) in L:
        z40 = isotherm(L, elev, t2, -40.0); cbtop = L[("gh", 300)]
        cbtop = np.where(np.isfinite(z40), np.minimum(z40, cbtop), cbtop)
        cb = anycloud & (cv >= c["cb"]) & pr & np.isfinite(z0c)
        put(np.where(cb, 3, 0), 2, np.where(cb, z0c, 0), np.where(cb, cbtop, 0))
    z10 = isotherm(L, elev, t2, -10.0)
    # (2026-11-21) a real TCU: cloud reaching -10 C, with precipitation or lift at the icing levels
    tcu = c10 & (cv >= c["tcu"]) & (cv < c["cb"]) & (pr | (liftice >= c["lift_mod"])) & np.isfinite(z0c) & np.isfinite(z10)
    tcu |= c10 & embedded_tcu(L, elev) & np.isfinite(z0c) & np.isfinite(z10)   # embedded TCU in frontal cloud (no CAPE needed)
    if ("gh", 500) in L:
        z10 = np.minimum(z10, L[("gh", 500)])
    put(np.where(tcu, 2, 0), 2, np.where(tcu, z0c, 0), np.where(tcu, z10, 0))

    # --- freezing rain / ice pellets: a warm layer aloft over a sub-zero surface, with precipitation. (2026-11-22, CN) only
    # with ice to melt: a cloud layer whose top is -10 C or colder, the warm layer below that top
    wz = warm_nose(L, elev, t2, icetop)
    fz = np.isfinite(wz) & pr & iceany
    put(np.where(fz, 3, 0), 2, np.where(fz, elev, 0), np.where(fz, np.nan_to_num(wz), 0))

    code = np.where(best > 0, best + 4 * btyp, 0)
    base = np.where(best > 0, np.maximum(bb, 0) * M2HFT, 0); top = np.where(best > 0, bt * M2HFT, 0)
    return code.astype(np.float32), base.astype(np.float32), top.astype(np.float32)


def warm_nose(L, elev, t2, below=None):
    """freezing-rain set-up (before precipitation is known): a level above the ground warmer than +1 C over a
    sub-zero surface -> the top of that warm layer (m), else nan. below: only levels under this height (the ice cloud's top)."""
    T = np.stack([L[("t", p)] for p in ILEV]); Z = np.stack([L[("gh", p)] for p in ILEV])
    above = Z > (elev + 150.0)[None]
    if below is not None: above = above & (Z < below[None])
    warm = above & (T > 1.0)
    topz = np.where(warm, Z, -np.inf).max(axis=0)
    return np.where((t2 < 0) & np.isfinite(topz), topz, np.nan).astype(np.float32)


def freezing_level(L, elev, t2):
    """the lowest height (hundreds of ft ASL) where the temperature drops to 0 C; 0 = below freezing at the surface."""
    out = np.full(t2.shape, np.nan, dtype=np.float32)
    out = np.where(t2 <= 0, 0.0, out)
    zb = elev + 2.0; tb = t2.astype(np.float32)
    done = t2 <= 0
    for p in ILEV:
        T = L[("t", p)]; Z = L[("gh", p)]
        ok = (Z > zb) & ~done
        cross = ok & (T <= 0) & (tb > 0)
        f = np.where(cross, tb / np.maximum(tb - T, 1e-6), 0)
        zc = zb + f * (Z - zb)
        out = np.where(cross, zc * M2HFT, out); done = done | cross
        zb = np.where(ok, Z, zb); tb = np.where(ok, T, tb)
    return out


def _deformation(u, v, lat, res_deg):
    """total deformation (s-1) of a wind field in kt on a regular lat / lon grid (last two axes: row from the north, col)."""
    um, vm = u * KT2MS, v * KT2MS
    dy = R_EARTH * np.radians(res_deg)
    dx = np.maximum(R_EARTH * np.cos(np.radians(lat)) * np.radians(res_deg), 1000.0)
    du_dx = np.gradient(um, axis=-1) / dx; dv_dx = np.gradient(vm, axis=-1) / dx
    du_dy = -np.gradient(um, axis=-2) / dy; dv_dy = -np.gradient(vm, axis=-2) / dy   # rows go north -> south
    return np.hypot(du_dx - dv_dy, dv_dx + du_dy)


def turbulence(L, elev, u10, v10, gust, lat, res_deg):
    """(code, base, top): code = intensity + 4 * cause, base / top hundreds of ft ASL."""
    shape = elev.shape
    best = np.zeros(shape, dtype=np.int8); cause = np.zeros(shape, dtype=np.int8)
    base = np.zeros(shape, dtype=np.float32); top = np.zeros(shape, dtype=np.float32)

    def put(inten, cz, lo_m, hi_m):
        nonlocal best, cause, base, top
        inten = np.asarray(inten, dtype=np.int8)
        new = inten > best
        same = (inten == best) & (inten > 0)
        best = np.where(new, inten, best); cause = np.where(new, cz, cause)
        base = np.where(new, lo_m, np.where(same, np.minimum(base, lo_m), base))
        top = np.where(new, hi_m, np.where(same, np.maximum(top, hi_m), top))

    # mechanical
    w10 = np.hypot(np.nan_to_num(u10), np.nan_to_num(v10)); g = np.nan_to_num(gust)
    land = elev > 5.0   # (over the sea a strong wind is not "mechanical" turbulence - nothing to trip it)
    mech = np.where(land, np.where((w10 >= 40) | (g >= 50), 3, np.where((w10 >= 25) | (g >= 35), 2, 0)), 0)
    put(mech, 1, elev * 0, elev + 914.0)
    # mountain waves
    if ("u", 700) in L:
        w7 = np.hypot(L[("u", 700)], L[("v", 700)])
        lee = np.where(elev >= 800, np.where(w7 >= 60, 3, np.where(w7 >= 40, 2, 0)), 0)
        put(lee, 2, elev * 0, L[("gh", 700)] + 1524.0)
    # shear and CAT between neighbouring levels
    for a, b in zip(TLEV[:-1], TLEV[1:]):
        za, zb = L[("gh", a)], L[("gh", b)]
        ok = za > elev + 150.0
        dz_kft = np.maximum((zb - za) * 3.28084 / 1000.0, 0.5)
        du, dv = L[("u", b)] - L[("u", a)], L[("v", b)] - L[("v", a)]
        vws = np.hypot(du, dv) / dz_kft                             # kt per 1,000 ft
        # (2026-11-23, CN, checked against the GFAs of all 7 regions) shear only makes turbulence where the layer is not
        # too stable: bulk Richardson number Ri = N^2 / S^2 (< 1 MOD, < 0.5 SEV). A strong shear across an inversion or
        # a frontal layer (cold air under warm) is stable - the long SEV bands the GFA never had.
        tha = (L[("t", a)] + 273.15) * (1000.0 / a) ** 0.2854; thb = (L[("t", b)] + 273.15) * (1000.0 / b) ** 0.2854
        dzm = np.maximum(zb - za, 100.0)
        n2 = G0 / ((tha + thb) / 2) * (thb - tha) / dzm
        s2 = (np.hypot(du, dv) * KT2MS / dzm) ** 2
        ri = n2 / np.maximum(s2, 1e-8)
        inten = np.where((vws >= 12) & (ri < 0.5), 3, np.where((vws >= 8) & (ri < 1.0), 2, 0))
        cz = 3
        if a <= 500:   # upper levels (the jet): a higher shear, or the Ellrod index
            cz = 4
            inten = np.where((vws >= 14) & (ri < 0.5), 3, np.where((vws >= 10) & (ri < 1.0), 2, 0))
            vws_s = np.hypot(du, dv) * KT2MS / np.maximum(zb - za, 100.0)
            de = _deformation((L[("u", a)] + L[("u", b)]) / 2, (L[("v", a)] + L[("v", b)]) / 2, lat, res_deg)
            ti = vws_s * de * 1e7
            inten = np.maximum(inten, np.where(ti >= 20, 3, np.where(ti >= 12, 2, 0)))
        put(np.where(ok, inten, 0), cz, za, zb)
    code = np.where(best > 0, best + 4 * cause, 0)
    return code.astype(np.float32), np.where(best > 0, np.maximum(base, 0) * M2HFT, 0).astype(np.float32), np.where(best > 0, top * M2HFT, 0).astype(np.float32)
