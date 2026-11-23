#!/usr/bin/env python3
"""wxtiles/gfa_compare.py (2026-11-23, CN) - a CHECK, not part of the tiles: the NAV CANADA GFA icing / turbulence /
freezing level charts (TURBC) for all 7 regions next to Airplot's model icing / turbulence (wx.airplot.net/wx/g/) for the
same valid times. Output: gfacmp/<region>_<valid>_gfa.png and _model.png (+ the raw GFA JSON)."""
import datetime as dt, gzip, json, os, struct, sys
import numpy as np, requests
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import cartopy.crs as ccrs, cartopy.feature as cf

OUT = "gfacmp"; os.makedirs(OUT, exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = "Airplot model check (airplot.net)"
SITES = {"GFACN31": "CYVR", "GFACN32": "CYYC", "GFACN33": "CYYZ", "GFACN34": "CYHZ", "GFACN35": "CYZF", "GFACN36": "CYFB", "GFACN37": "CYRB"}
EXT = {"GFACN31": (-142, -110, 46, 62), "GFACN32": (-124, -86, 47, 62), "GFACN33": (-98, -60, 41, 58), "GFACN34": (-72, -48, 41, 61),
       "GFACN35": (-145, -98, 58, 76), "GFACN36": (-102, -58, 56, 78), "GFACN37": (-135, -55, 70, 84)}

def old_urls(reg, out):
    # fallback: the older static NAV CANADA addresses - frames 000 / 006 / 012 h of the latest issue
    import calendar, math
    t = calendar.timegm(dt.datetime.utcnow().timetuple()) - 5.6 * 3600   # issued ~0.5 h before its first valid time
    v0 = dt.datetime.utcfromtimestamp(math.ceil(t / 21600) * 21600)
    for k, h in enumerate((0, 6, 12)):
        code = {"GFACN31": "pa", "GFACN32": "pr", "GFACN33": "oq", "GFACN34": "at", "GFACN35": "nw", "GFACN36": "nu", "GFACN37": "ar"}[reg]
        u = f"https://aviationweather.gov/data/products/fax/F{h:02d}_canfa_icetb_{code}.gif"   # the same GFA charts, from the US AWC
        out.setdefault(reg, []).append(((v0 + dt.timedelta(hours=h)).strftime("%Y-%m-%d %H:%M"), "URL:" + u))

def gfa_frames():
    out = {}
    for reg, site in SITES.items():
        try:
            raise RuntimeError("CFPS skipped (refuses GitHub servers) - AWC copy")
            print(reg, "CFPS", r.status_code, r.headers.get("content-type"), r.text[:200].replace("\n", " "))
            js = r.json(); open(f"{OUT}/{reg}_raw.json", "w").write(json.dumps(js)[:200000])
        except Exception as e:
            print(reg, "GFA fetch failed", e); old_urls(reg, out); continue
        for d in js.get("data", []):
            try: t = json.loads(d.get("text") or "{}")
            except Exception: continue
            if t.get("sub_product") != "TURBC": continue
            geo = t.get("geography") or reg
            fl = (t.get("frame_lists") or [])
            if not fl: continue
            for fr in fl[-1].get("frames", []):
                img = (fr.get("images") or [{}])[0].get("id")
                sv = fr.get("sv") or fr.get("valid") or ""
                if img: out.setdefault(geo, []).append((sv, img))
    return out

LOCAL = os.environ.get("WX_LOCAL")   # a local build (site/wx): g/ = new rules, g_old/ = the previous ones
def get_meta():
    if LOCAL:
        m = json.load(open(LOCAL + "/meta.json")); m["_b"] = LOCAL + "/"; return m
    for b in ["https://wx.airplot.net/wx/", "https://airplotnet-collab.github.io/airplot/wx/"]:
        try:
            m = S.get(b + "meta.json", timeout=60).json(); m["_b"] = b; return m
        except Exception as e: print("meta", b, e)
    sys.exit("no model meta")

TILES = {}; GDIR = ["g"]
def tile(m, la, lo):
    k = (GDIR[0], la, lo)
    if k not in TILES:
        if LOCAL: b = open(f"{LOCAL}/{GDIR[0]}/{la}_{lo}.bin", "rb").read()
        else: b = S.get(m["_b"] + f"g/{la}_{lo}.bin", params={"r": m["run"] + "_" + m.get("made", "")}, timeout=60).content
        if b[:2] == b"\x1f\x8b": b = gzip.decompress(b)
        hl = struct.unpack("<I", b[4:8])[0]; TILES[k] = (json.loads(b[8:8 + hl]), b[8 + hl:])
    return TILES[k]

SZ = {"i1": 1, "u1": 1, "i2": 2}; NP = {"i1": "<i1", "u1": "<u1", "i2": "<i2"}
def field(m, name, si, la0, la1, lo0, lo1):
    g = m["gfa"]; ns = len(g["steps"]); n = m["n"]; res = m["res"]
    lats = np.arange(la1, la0 - 1e-6, -res); lons = np.arange(lo0, lo1 + 1e-6, res)
    A = np.full((len(lats), len(lons)), np.nan)
    for tla in range(int(np.floor(la0 / 10) * 10), int(la1), 10):
        for tlo in range(int(np.floor(lo0 / 10) * 10), int(lo1), 10):
            if tla < -90 or tla > 80: continue
            try: hdr, d = tile(m, tla, tlo)
            except Exception as e: print("tile", tla, tlo, e); continue
            off = 0
            for v in g["vars"]:
                cnt = ns * n * n
                if v["name"] == name:
                    a = np.frombuffer(d, dtype=NP[v["type"]], count=cnt, offset=off).reshape(ns, n, n)[si].astype(float)
                    a[a == v["missing"]] = np.nan
                    a = a * v["scale"] + v["offset"]
                    for i in range(n):
                        la = tla + 10 - i * res
                        if la < la0 - 1e-6 or la > la1 + 1e-6: continue
                        ri = int(round((la1 - la) / res))
                        for j in range(n):
                            lo = tlo + j * res
                            if lo < lo0 - 1e-6 or lo > lo1 + 1e-6: continue
                            A[ri, int(round((lo - lo0) / res))] = a[i, j]
                    break
                off += cnt * SZ[v["type"]]
    return lats, lons, A

def draw_model(m, reg, valid, si, tag="model"):
    lo0, lo1, la0, la1 = EXT[reg]
    lats, lons, icg = field(m, "icg", si, la0, la1, lo0, lo1)
    _, _, trb = field(m, "trb", si, la0, la1, lo0, lo1)
    _, _, icgb = field(m, "icgb", si, la0, la1, lo0, lo1); _, _, icgt = field(m, "icgt", si, la0, la1, lo0, lo1)
    _, _, fzl = field(m, "fzl", si, la0, la1, lo0, lo1)
    pc = ccrs.LambertConformal(central_longitude=(lo0 + lo1) / 2, standard_parallels=(49, 77))
    fig = plt.figure(figsize=(11, 8.5)); ax = plt.axes(projection=pc); ax.set_extent([lo0, lo1, la0, la1], ccrs.PlateCarree())
    ax.add_feature(cf.COASTLINE, lw=.6); ax.add_feature(cf.BORDERS, lw=.5); ax.add_feature(cf.STATES, lw=.3, edgecolor="#888")
    X, Y = np.meshgrid(lons, lats); ii = np.where(np.isnan(icg), 0, icg) % 4; ti = np.where(np.isnan(trb), 0, trb) % 4
    ax.contourf(X, Y, ii, levels=[1.5, 2.5, 3.5], colors=["#9ec5ff", "#1f4fd1"], alpha=.55, transform=ccrs.PlateCarree())
    ax.contour(X, Y, ii, levels=[1.5], colors=["#1f4fd1"], linewidths=1, transform=ccrs.PlateCarree())
    ax.contourf(X, Y, ti, levels=[1.5, 2.5, 3.5], colors="none", hatches=["//", "xx"], transform=ccrs.PlateCarree())
    ax.contour(X, Y, ti, levels=[1.5, 2.5], colors=["#d6201a", "#7a0000"], linewidths=[1, 1.6], transform=ccrs.PlateCarree())
    cs = ax.contour(X, Y, np.nan_to_num(fzl) * 100, levels=[500, 2500, 5000, 7500, 10000], colors="#c8a000", linestyles="--", linewidths=.7, transform=ccrs.PlateCarree())
    ax.clabel(cs, fmt=lambda v: "FZL %03d" % (v / 100), fontsize=7)
    # numbers: share of the area with MOD / SEV icing and turbulence, typical icing base / top
    mi, si_ = np.mean(ii == 2) * 100, np.mean(ii == 3) * 100; mt, st = np.mean(ti == 2) * 100, np.mean(ti == 3) * 100
    sel = ii >= 2; bt = (" icing base %d-%d top %d-%d (00s ft)" % (np.nanpercentile(icgb[sel], 25), np.nanpercentile(icgb[sel], 75), np.nanpercentile(icgt[sel], 25), np.nanpercentile(icgt[sel], 75))) if sel.sum() > 5 else ""
    ax.set_title(f"{tag.upper()} {reg} valid {valid}  (run {m['run']}, +{m['gfa']['steps'][si]}h)\nICG area MOD {mi:.0f}% SEV {si_:.0f}%  TURB MOD {mt:.0f}% SEV {st:.0f}%{bt}\nblue = MOD icing, dark blue = SEV; red / = MOD turb, dark red x = SEV; yellow dashed = freezing level", fontsize=9)
    fig.savefig(f"{OUT}/{reg}_{valid}_{tag}.png", dpi=90, bbox_inches="tight"); plt.close(fig)
    return dict(icgMOD=round(mi, 1), icgSEV=round(si_, 1), trbMOD=round(mt, 1), trbSEV=round(st, 1))

def main():
    m = get_meta(); run = dt.datetime.strptime(m["run"], "%Y-%m-%dT%H:%MZ")
    fr = gfa_frames(); summ = {"run": m["run"], "regions": {}}
    print("GFA frames:", {k: len(v) for k, v in fr.items()})
    for reg, frames in sorted(fr.items()):
        for sv, img in frames:
            try: valid = dt.datetime.strptime(sv[:16].replace("T", " "), "%Y-%m-%d %H:%M")
            except Exception: print(reg, "bad time", sv); continue
            vs = valid.strftime("%d%HZ")
            try:
                r = S.get(img[4:] if img.startswith("URL:") else f"https://plan.navcanada.ca/weather/images/{img}.image", timeout=60)
                print(reg, vs, "image", r.status_code, r.headers.get("content-type"), len(r.content))
                if r.ok and r.headers.get("content-type", "").startswith("image"):
                    import io; from PIL import Image
                    Image.open(io.BytesIO(r.content)).convert("RGB").save(f"{OUT}/{reg}_{vs}_gfa.png")
            except Exception as e: print(reg, "img", e)
            h = (valid - run).total_seconds() / 3600
            if reg in EXT and h in m["gfa"]["steps"]:
                try:
                    si = m["gfa"]["steps"].index(h); out = {}
                    for gd, tag in ([("g", "new"), ("g_old", "old")] if LOCAL else [("g", "model")]):
                        GDIR[0] = gd; out[tag] = draw_model(m, reg, vs, si, tag)
                    summ["regions"].setdefault(reg, {})[vs] = out
                except Exception as e: print(reg, vs, "model draw failed", e)
            else: print(reg, vs, "no model step", h)
    json.dump(summ, open(f"{OUT}/summary.json", "w"), indent=1); print(json.dumps(summ, indent=1))

if __name__ == "__main__":
    main()
