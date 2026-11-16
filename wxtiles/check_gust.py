# one-off: which gust fields ECMWF open data gives at each step (index lines with 'fg')
import json, requests, datetime as dt
now = dt.datetime.utcnow()
for back in range(0, 48, 6):
    r = now - dt.timedelta(hours=back + 8); r = r.replace(hour=r.hour // 6 * 6, minute=0, second=0, microsecond=0)
    if r.hour not in (0, 12): continue
    for res in ("0p25", "0p1"):
        parts = ["fc"] if res == "0p25" else ["fc-sfc"]
        for step in (84, 87, 90, 93, 96, 102, 120):
            for pt in parts:
                u = "https://data.ecmwf.int/forecasts/%s/%02dz/ifs/%s/oper/%s%02d0000-%dh-oper-%s.index" % (r.strftime("%Y%m%d"), r.hour, res, r.strftime("%Y%m%d"), r.hour, step, pt)
                x = requests.get(u, timeout=60)
                if x.status_code != 200: print(res, step, pt, x.status_code); continue
                fg = [l for l in x.text.splitlines() if 'fg' in l]
                print(res, step, pt, len(x.text.splitlines()), "lines;", [ {k: json.loads(l).get(k) for k in ("param", "step", "levtype")} for l in fg ])
    break
