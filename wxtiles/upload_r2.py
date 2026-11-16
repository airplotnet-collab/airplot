#!/usr/bin/env python3
"""
wxtiles/upload_r2.py - (2026-11-15) put a built tile folder on Cloudflare R2 (bucket airplot-wx, served at
https://wx.airplot.net/<prefix>/...). Tiles first, meta.json last, so a page never reads a meta.json whose
tiles are not up yet (the page also asks for each tile with ?r=<run>, so caches never mix two runs).

  python upload_r2.py <folder> <prefix>      e.g.  site/wx wx   or   site/wx9 wx9
Needs the GitHub secrets R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET (env). Without them it
only says so (the GitHub Pages copy still goes out).
"""
import os, sys, time
from concurrent.futures import ThreadPoolExecutor

src, prefix = sys.argv[1], sys.argv[2].strip("/")
need = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"]
if not all(os.environ.get(k) for k in need):
    print("::warning title=R2::R2 secrets not set - not uploaded (" + ", ".join(k for k in need if not os.environ.get(k)) + ")")
    sys.exit(0)
import boto3
from botocore.config import Config
s3 = boto3.client("s3", endpoint_url="https://%s.r2.cloudflarestorage.com" % os.environ["R2_ACCOUNT_ID"],
                  aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"], aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
                  region_name="auto", config=Config(retries={"max_attempts": 8, "mode": "adaptive"}, max_pool_connections=32))
bucket = os.environ["R2_BUCKET"]

def put(rel):
    path = os.path.join(src, rel); key = prefix + "/" + rel.replace(os.sep, "/")
    meta = rel.endswith(".json")
    with open(path, "rb") as f:
        s3.put_object(Bucket=bucket, Key=key, Body=f.read(),
                      ContentType="application/json" if meta else "application/octet-stream",
                      CacheControl="public, max-age=60" if meta else "public, max-age=86400")  # tiles: the ?r=<run> in the URL changes each run
    return os.path.getsize(path)

files = [os.path.relpath(os.path.join(d, f), src) for d, _, fs in os.walk(src) for f in fs]
tiles = [f for f in files if not f.endswith(".json")]; metas = [f for f in files if f.endswith(".json")]
t0 = time.time()
with ThreadPoolExecutor(24) as ex:
    total = sum(ex.map(put, tiles))
for m in metas:
    total += put(m)
print("R2: %d files, %.1f MB to %s/%s in %d s" % (len(files), total / 1e6, bucket, prefix, time.time() - t0))
print("::notice title=R2::%d files (%.0f MB) uploaded to https://wx.airplot.net/%s/" % (len(files), total / 1e6, prefix))
