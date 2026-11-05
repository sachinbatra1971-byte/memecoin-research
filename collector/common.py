"""Shared helpers: HTTP with polite retries, CSV state files, time."""
import csv
import gzip
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = "Mozilla/5.0 (compatible; memecoin-research/1.0)"


def now_utc():
    return datetime.now(timezone.utc)


def get_json(url, headers=None, tries=4, pause=2.0):
    """GET a URL and return parsed JSON, or None after retries."""
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            wait = pause * (2 ** attempt) * (3 if e.code == 429 else 1)
            print(f"  HTTP {e.code} on {url[:110]} - retry in {wait:.0f}s")
            if e.code in (400, 404):
                return None
            time.sleep(wait)
        except Exception as e:  # network hiccup
            wait = pause * (2 ** attempt)
            print(f"  {type(e).__name__} on {url[:110]} - retry in {wait:.0f}s")
            time.sleep(wait)
    print(f"  GAVE UP: {url[:110]}")
    return None


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields, gz=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    opener = (lambda p: gzip.open(p, "wt", newline="", encoding="utf-8")) if gz else \
             (lambda p: open(p, "w", newline="", encoding="utf-8"))
    with opener(path) as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
