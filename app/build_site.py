"""Builds the dashboard as a static website (for GitHub Pages) from the price database.

  python app/build_site.py --db data/market.db --out site
"""
import argparse
import csv
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import analytics  # noqa: E402
from db import connect  # noqa: E402
from market import load_cfg  # noqa: E402


def safe_key(k):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(k))


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, default=str, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--db", default=os.path.join(HERE, "market.db"))
    ap.add_argument("--out", default="site")
    ap.add_argument("--archive", default=None, help="folder to keep a copy of each day's roundup")
    a = ap.parse_args()

    cfg = load_cfg(a.config)
    con = connect(a.db)
    out = a.out
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "data", "products"))

    s = analytics.summary(con, cfg)
    write_json(os.path.join(out, "data", "summary.json"), s)
    for t in s["tickers"]:
        d = analytics.product_detail(con, cfg, t["key"])
        if d:
            write_json(os.path.join(out, "data", "products", safe_key(t["key"]) + ".json"), d)
    write_json(os.path.join(out, "data", "events.json"), analytics.events(con, cfg, limit=150))
    r = analytics.roundup(con, cfg)
    write_json(os.path.join(out, "data", "roundup.json"), r)
    html = analytics.roundup_html(r)
    with open(os.path.join(out, "roundup.html"), "w", encoding="utf-8") as f:
        f.write(html)
    if a.archive and r["generated"]:
        os.makedirs(a.archive, exist_ok=True)
        with open(os.path.join(a.archive, r["generated"][:10] + ".html"), "w", encoding="utf-8") as f:
            f.write(html)

    cols = ["key", "name", "grade", "mine_units", "mine_listing", "suggested_listing", "change", "signal", "why",
            "mine", "low", "low_seller", "spread", "genuine_low", "compatible_low", "budget_low", "d1", "d7", "d30", "url"]
    with open(os.path.join(out, "data", "market.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(s["tickers"])

    with open(os.path.join(HERE, "web", "index.html"), encoding="utf-8") as f:
        page = f.read()
    repo = os.environ.get("GITHUB_REPOSITORY")
    actions = f"https://github.com/{repo}/actions" if repo else "#"
    page = page.replace("<script>", f"<script>window.PX_STATIC=true;window.PX_ACTIONS_URL={json.dumps(actions)};</script>\n<script>", 1)
    page = page.replace("<head>", '<head>\n<meta name="robots" content="noindex,nofollow">', 1)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    with open(os.path.join(out, "robots.txt"), "w") as f:
        f.write("User-agent: *\nDisallow: /\n")
    open(os.path.join(out, ".nojekyll"), "w").close()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # keep the whole history inside market.db before it is saved
    con.close()
    print(f"Website built in {out}/ ({len(s['tickers'])} parts)")


if __name__ == "__main__":
    main()
