"""
wxtiles/clouds_wx.py - model clouds and weather from the ECMWF fields (2026-11-16, CN), for the TAF Map's
"Model cloud & wx" layer, drawn like the Canadian GFA CLDS & WX panel. Rules of thumb on the model, NOT a
forecaster's product:

CLOUD (the lowest significant layer)
  cloudy level: relative humidity >= 90 % at a pressure level above the ground (1000 - 500 hPa)
  base: halfway down to the level below the lowest cloudy one - or, when the cloud reaches down to the
        lowest level, the lifting condensation level from the 2 m temperature / dew point (125 m per C of spread)
  top:  halfway up to the first level above the layer that is not cloudy (contiguous layer)
  amount: the model's low / mid cloud cover for that height (low under 6,500 ft above the ground): FEW < 25 %,
          SCT < 50 %, BKN < 87.5 %, OVC; a layer from the humidity with no cloud cover is left out
  type: CB (CAPE >= 1000 J/kg with precipitation, top at the 300 hPa height), TCU (CAPE >= 300), CU (CAPE >= 100),
        else a layer cloud
WEATHER (in the 3 h before)
  precipitation >= 0.3 mm: FZRA (a warm layer aloft over a sub-zero surface), snow (snowfall >= half of it), else rain;
  showers when convective (CAPE >= 100), thunderstorms (CAPE >= 500 and >= 1 mm);
  intensity from the rate: rain light < 2.5 mm/h, heavy >= 7.6; snow (water) light < 1 mm/h, heavy >= 2.5
  obstruction with no precipitation: FG (visibility < 5/8 SM), BR (visibility <= 6 SM)

cldc = amount (0 none / FEW, 1 SCT, 2 BKN, 3 OVC) + 4 x type (0 layer, 1 CU, 2 TCU, 3 CB); cldb / cldt hundreds of ft ASL
wx   = intensity (0 none, 1 light, 2 moderate, 3 heavy) + 4 x type (0 RA, 1 SN, 2 FZRA, 3 SHRA, 4 SHSN, 5 TSRA)
obsc = 0 none, 1 BR, 2 FG
"""
import numpy as np

M2HFT = 3.28084 / 100.0
LEVS = [1000, 925, 850, 700, 600, 500]
CLD_TYPES = ["", "CU", "TCU", "CB"]
WX_TYPES = ["RA", "SN", "FZRA", "SHRA", "SHSN", "TSRA"]


def clouds(L, elev, t2, td2, lcc, mcc, cape):
    """(cldc, cldb, cldt) arrays - the lowest cloud layer (precipitation is not known yet: CB uses CAPE alone here,
    refined in weather())."""
    RH = np.stack([L[("r", p)] for p in LEVS]); Z = np.stack([L[("gh", p)] for p in LEVS])
    n = len(LEVS)
    above = Z > (elev + 50.0)[None]
    cloudy = above & (RH >= 90.0)
    shape = elev.shape
    base = np.full(shape, np.nan, np.float32); top = np.full(shape, np.nan, np.float32)
    first_above = np.full(shape, -1, np.int16)
    for i in range(n):
        first_above = np.where((first_above < 0) & above[i], i, first_above)
    found = np.zeros(shape, bool); inlayer = np.zeros(shape, bool); done = np.zeros(shape, bool)
    lcl = elev + np.clip(np.nan_to_num(t2 - td2), 0, 30) * 125.0
    for i in range(n):
        c = cloudy[i]
        start = c & ~found
        if i == 0:
            lo = np.full(shape, -np.inf)
        else:
            lo = (Z[i] + Z[i - 1]) / 2
        # cloud from the lowest level above the ground: the condensation level (not a guess below the level)
        b = np.where(first_above == i, np.maximum(elev, np.minimum(lcl, Z[i])), np.maximum(lo, elev))
        base = np.where(start, b, base)
        found = found | c
        inlayer = np.where(start, True, inlayer)
        hi = (Z[i] + Z[i + 1]) / 2 if i < n - 1 else Z[i] + (Z[i] - Z[i - 1]) / 2
        top = np.where(inlayer & c & ~done, hi, top)
        ended = inlayer & ~c & ~done & found
        done = done | ended
    agl = base - elev
    cover = np.where(agl < 1981.0, np.nan_to_num(lcc), np.nan_to_num(mcc))   # under 6,500 ft above the ground: low cloud
    amt = np.where(cover >= 87.5, 3, np.where(cover >= 50, 2, np.where(cover >= 25, 1, 0)))
    amt = np.where(found, amt, 0)
    cv = np.nan_to_num(cape)
    typ = np.where(cv >= 1000, 3, np.where(cv >= 300, 2, np.where(cv >= 100, 1, 0)))
    typ = np.where(amt > 0, typ, 0)
    # a CB / TCU reaches far above the humid layer: its top at the 300 / 400 hPa height
    if ("gh", 300) in L:
        top = np.where(typ == 3, np.maximum(np.nan_to_num(top), L[("gh", 300)]), top)
    if ("gh", 500) in L:
        top = np.where(typ == 2, np.maximum(np.nan_to_num(top), L[("gh", 500)]), top)
    code = np.where(amt > 0, amt + 4 * typ, 0).astype(np.float32)
    return code, np.where(amt > 0, base * M2HFT, 0).astype(np.float32), np.where(amt > 0, np.nan_to_num(top) * M2HFT, 0).astype(np.float32)


def weather(tp, sf, t2, cape, vis, warm_top):
    """(wx, obsc): precipitation type / intensity in the 3 h before, and fog / mist where it is not precipitating."""
    tp = np.nan_to_num(tp); sf = np.nan_to_num(sf); cv = np.nan_to_num(cape)
    pr = tp >= 0.3
    fz = pr & np.isfinite(warm_top) if warm_top is not None else np.zeros(tp.shape, bool)
    snow = pr & ~fz & (sf >= 0.5 * tp)
    conv = cv >= 100
    ts = pr & (cv >= 500) & (tp >= 1.0) & ~snow & ~fz
    typ = np.where(fz, 2, np.where(ts, 5, np.where(snow, np.where(conv, 4, 1), np.where(conv, 3, 0))))
    rate = tp / 3.0                                          # mm per hour
    inten_r = np.where(rate >= 7.6, 3, np.where(rate >= 2.5, 2, 1))
    inten_s = np.where(rate >= 2.5, 3, np.where(rate >= 1.0, 2, 1))
    inten = np.where(snow, inten_s, inten_r)
    wx = np.where(pr, inten + 4 * typ, 0).astype(np.float32)
    v = np.nan_to_num(vis, nan=10)
    obsc = np.where(~pr & (v < 0.625), 2, np.where(~pr & (v <= 6), 1, 0)).astype(np.float32)
    return wx, obsc
