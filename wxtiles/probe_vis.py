"""One-off check: is visibility ('vis') in ECMWF's free open data (any resolution, e.g. the 9 km / 0.1 deg set)?
Walks https://data.ecmwf.int/forecasts/<date>/<run>/ifs/<res>/oper/ and reads one .index per resolution."""
import re, json, datetime as dt, requests
B = 'https://data.ecmwf.int/forecasts/'
S = requests.Session()
def ls(u):
    r = S.get(u, timeout=60); r.raise_for_status()
    return sorted(set(re.findall(r'href="([^"?]+)"', r.text)))
now = dt.datetime.utcnow()
found_any = False
for back in range(0, 30, 6):
    t = now - dt.timedelta(hours=back + 3); run = t.hour // 6 * 6
    base = f'{B}{t:%Y%m%d}/{run:02d}z/ifs/'
    try: res = [x.rstrip('/').split('/')[-1] for x in ls(base) if x.endswith('/')]
    except Exception as e: print('skip', base, e); continue
    res = [r for r in res if re.match(r'^\d+p\d+', r)]
    print('run', base, 'resolutions:', res)
    for r in res:
        for stream in ('oper', 'scda'):
            u = f'{base}{r}/{stream}/'
            try: files = [f for f in ls(u) if f.endswith('.index')]
            except Exception as e: print(' ', r, stream, 'no listing'); continue
            if not files: continue
            names = [f.split('/')[-1] for f in files]
            pick = [n for n in names if '-3h-' in n] or names[:3]  # every file type for one step (surface, levels, ...)
            params = set()
            for name in pick:
                ps = sorted({json.loads(l).get('param') for l in S.get(u + name, timeout=60).text.splitlines() if l.strip()})
                print(f'    {name}: ' + ' '.join(p for p in ps if p)); params |= set(ps)
            name = ', '.join(pick)
            has = 'vis' in params
            found_any |= has
            print(f'  {r}/{stream}: {len(files)} steps, sample {name}: {len(params)} params -> vis {"FOUND" if has else "not found"}')
    if any(r != '0p25' for r in res): break  # keep going back until a run with the 9 km set
print('::notice title=ECMWF visibility::' + ('vis FOUND in free open data' if found_any else 'vis NOT in free open data'))
