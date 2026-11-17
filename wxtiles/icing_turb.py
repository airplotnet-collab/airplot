"""
wxtiles/icing_turb.py - model icing, turbulence and freezing level from the ECMWF pressure-level fields (2026-11-16, CN).

For the TAF Map's "Model icing & turb" layer, drawn like the Canadian GFA ICG/TURB/FZLVL panel. These are rules
of thumb applied to the model, NOT a forecaster's product:

ICING (each pressure level above the ground, 1000-500 hPa):
  potential = temperature factor x humidity factor
    temperature: 0 at 0 C, full from -2.5 to -12 C, half at -17 C, 0 at -22 C (supercooled water range)
  humidity: 0 at RH 80 %, full at 98 %. LGT >= 0.6; MOD >= 0.85 with liquid cloud water in the column (>= 0.15 kg/m2);
  SEV >= 0.95 at -12 C or warmer with a lot of cloud water (>= 0.5 kg/m2)
  type: RIME (layer colder than -14 C), CLR (-8 C or warmer in convection, CAPE > 100), MXD otherwise
  freezing rain: a warm layer aloft (> +1 C) over a sub-zero surface with precipitation -> SEV CLR from the surface
TURBULENCE:
  MECH  - over land, surface wind >= 25 kt or gusts >= 35 kt (SEV: wind >= 40 / gusts >= 50): surface to 3,000 ft above it
  LEE   - mountain waves: ground >= 800 m and 700 hPa wind >= 40 kt (SEV >= 60 kt): surface to 5,000 ft above 700 hPa
  WS    - vertical wind shear between two levels (925 - 500 hPa): MOD >= 8 kt / 1,000 ft, SEV >= 12
  CAT   - 500 - 250 hPa: the same shear, or the Ellrod index (shear x deformation): MOD >= 8, SEV >= 16 (x 1e-7 s-2)
FREEZING LEVEL: the lowest height where the temperature drops to 0 C (0 = below freezing at the surface).

Output per grid point (the most severe layer): intensity 0-3 + 4 x type / cause, base and top in hundreds of feet ASL.
"""
import numpy as np

ICG_TYPES = ["RIME", "MXD", "CLR"]                 # icg = intensity + 4 * type
TRB_CAUSES = ["", "MECH", "LEE", "WS", "CAT"]      # trb = intensity + 4 * cause
M2HFT = 3.28084 / 100.0                            # metres -> hundreds of feet
ILEV = [1000, 925, 850, 700, 600, 500]
TLEV = [925, 850, 700, 600, 500, 400, 300, 250]
R_EARTH = 6371000.0
KT2MS = 0.514444

# the pressure-level fields this needs (for the download list)
NEED = {
    "t": set(ILEV),
    "r": set(ILEV),
    "gh": set(ILEV) | set(TLEV),
    "u": set(TLEV),
    "v": set(TLEV),
}


def _layers(z, i, nlev):
    """bottom / top (m) of level i in a column of level heights z (nlev, ...): halfway to the neighbours."""
    lo = (z[i] + z[i - 1]) / 2 if i > 0 else z[i] - (z[i + 1] - z[i]) / 2
    hi = (z[i] + z[i + 1]) / 2 if i < nlev - 1 else z[i] + (z[i] - z[i - 1]) / 2
    return lo, hi


def icing(L, elev, tclw, cape):
    """L[(name, level)] -> (code, base, top) arrays; code = intensity + 4 * type, base / top hundreds of ft ASL."""
    T = np.stack([L[("t", p)] for p in ILEV]); RH = np.stack([L[("r", p)] for p in ILEV]); Z = np.stack([L[("gh", p)] for p in ILEV])
    above = Z > (elev + 150.0)[None]
    fT = np.clip(-T / 2.5, 0, 1) * np.clip((T + 22.0) / 10.0, 0, 1)
    fRH = np.clip((RH - 80.0) / 18.0, 0, 1)
    pot = np.where(above, fT * fRH, 0.0)
    cw = np.nan_to_num(tclw)[None] if tclw is not None else 0.0
    # MOD needs liquid cloud water in the column as well (supercooled water - not just a humid layer)
    inten = np.where((pot >= 0.85) & (cw >= 0.15), 2, np.where(pot >= 0.6, 1, 0))
    sev = (pot >= 0.95) & (T >= -12.0) & (cw >= 0.5)
    inten = np.where(sev, 3, inten)
    best = inten.max(axis=0)
    # the layer: every level at the best intensity (and LGT levels touching it count for base / top too)
    on = (inten >= np.maximum(best, 1)[None]) | ((inten >= 1) & (best >= 1)[None])
    base = np.full(best.shape, np.inf, dtype=np.float32); top = np.full(best.shape, -np.inf, dtype=np.float32)
    tsum = np.zeros(best.shape, dtype=np.float32); tn = np.zeros(best.shape, dtype=np.float32)
    n = len(ILEV)
    for i in range(n):
        lo, hi = _layers(Z, i, n)
        lo = np.maximum(lo, elev)
        m = on[i]
        base = np.where(m, np.minimum(base, lo), base); top = np.where(m, np.maximum(top, hi), top)
        tsum += np.where(m, T[i], 0); tn += m
    tmean = np.where(tn > 0, tsum / np.maximum(tn, 1), 0)
    cv = np.nan_to_num(cape) if cape is not None else 0.0
    typ = np.where(tmean < -14, 0, np.where((tmean >= -8) & (cv > 100), 2, 1))
    code = np.where(best > 0, best + 4 * typ, 0)
    base = np.where(best > 0, base * M2HFT, 0); top = np.where(best > 0, top * M2HFT, 0)
    return code.astype(np.float32), base.astype(np.float32), top.astype(np.float32)


def warm_nose(L, elev, t2):
    """freezing-rain set-up (before precipitation is known): a level above the ground warmer than +1 C over a
    sub-zero surface -> the top of that warm layer (m), else nan."""
    T = np.stack([L[("t", p)] for p in ILEV]); Z = np.stack([L[("gh", p)] for p in ILEV])
    above = Z > (elev + 150.0)[None]
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
    land = elev > 1.0   # (over the sea a strong wind is not "mechanical" turbulence - nothing to trip it)
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
        inten = np.where(vws >= 12, 3, np.where(vws >= 8, 2, 0))
        cz = 3
        if a <= 500:   # upper levels: the Ellrod index as well
            cz = 4
            vws_s = np.hypot(du, dv) * KT2MS / np.maximum(zb - za, 100.0)
            de = _deformation((L[("u", a)] + L[("u", b)]) / 2, (L[("v", a)] + L[("v", b)]) / 2, lat, res_deg)
            ti = vws_s * de * 1e7
            inten = np.maximum(inten, np.where(ti >= 16, 3, np.where(ti >= 8, 2, 0)))
        put(np.where(ok, inten, 0), cz, za, zb)
    code = np.where(best > 0, best + 4 * cause, 0)
    return code.astype(np.float32), np.where(best > 0, np.maximum(base, 0) * M2HFT, 0).astype(np.float32), np.where(best > 0, top * M2HFT, 0).astype(np.float32)
