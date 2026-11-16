# one-off: every surface parameter ECMWF open data gives at a few steps (looking for a cloud base height, cbh)
import json, requests, datetime as dt
now = dt.datetime.utcnow()
for back in range(8, 40, 6):
    r = now - dt.timedelta(hours=back); r = r.replace(hour=r.hour // 6 * 6, minute=0, second=0, microsecond=0)
    if r.hour not in (0, 12): continue
    for res, part in (("0p25", "fc"), ("0p1", "fc-sfc")):
        for step in (0, 12, 96):
            u = "https://data.ecmwf.int/forecasts/%s/%02dz/ifs/%s/oper/%s%02d0000-%dh-oper-%s.index" % (r.strftime("%Y%m%d"), r.hour, res, r.strftime("%Y%m%d"), r.hour, step, part)
            x = requests.get(u, timeout=60)
            if x.status_code != 200: print(res, step, x.status_code); continue
            ps = sorted({(json.loads(l).get("param"), json.loads(l).get("levtype")) for l in x.text.splitlines() if l.strip()})
            print(res, part, step, "sfc params:", [p for p, lt in ps if lt == "sfc"])
            print("   pl params:", sorted({p for p, lt in ps if lt == "pl"}), " levels:", sorted({json.loads(l).get("levelist") for l in x.text.splitlines() if l.strip() and json.loads(l).get("levtype") == "pl"}, key=lambda v: int(v) if v and str(v).isdigit() else 0))
    break
