"""Spot-checks the price readers against parts with known prices, so a website change is noticed quickly.
Prints one line per check into the scan log (these are public competitor prices only)."""
import json
import os
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from scraper import Fetcher, template_lookup  # noqa: E402

cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
f = Fetcher(delay=1)
bad = 0
for name, sc in cfg["sellers"].items():
    for part, expect in (sc.get("selftest") or {}).items():
        url = sc["url_template"].format(part=urllib.parse.quote(part), part_lower=part.lower())
        try:
            r = template_lookup(f, url, part, sc.get("price_regex"))
            got = r[0] if r else None
        except Exception as e:
            got = f"error: {e}"
        ok = isinstance(got, float) and abs(got - expect) / expect < 0.25
        bad += not ok
        print(f"{'OK ' if ok else 'CHECK'}  {name} {part}: read {got}, expected about {expect}")
print("Price reader self-test:", "all good" if not bad else f"{bad} reading(s) look wrong - see above")
