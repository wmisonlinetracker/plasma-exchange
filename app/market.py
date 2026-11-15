#!/usr/bin/env python3
"""Plasma Exchange - competitor price tracking, stock-market style.

  python market.py demo                 # sample data, opens dashboard (no internet needed)
  python market.py scrape               # one scrape run using config.json
  python market.py serve                # dashboard on http://127.0.0.1:8765
  python market.py serve --every 6      # dashboard + scrape every 6 hours
  python market.py matches              # review which competitor listings matched which of your products
  python market.py check URL            # test price extraction on one page
"""
import argparse
import json
import os
import sys
import webbrowser

import analytics
from db import connect

# When packaged as PlasmaExchange.exe, keep config.json and the database next to the .exe
if getattr(sys, "frozen", False):
    HERE = os.path.dirname(sys.executable)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))


def load_cfg(path):
    if not os.path.exists(path):
        sys.exit(f"Config not found: {path}\nCopy config.example.json to config.json and edit it.")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    # secrets can come from environment variables (GitHub Actions secrets) instead of the file
    eb = cfg.setdefault("sellers", {}).get("eBay")
    if eb is not None:
        if os.environ.get("EBAY_CLIENT_ID"):
            eb["client_id"] = os.environ["EBAY_CLIENT_ID"].strip()
        if os.environ.get("EBAY_CLIENT_SECRET"):
            eb["client_secret"] = os.environ["EBAY_CLIENT_SECRET"].strip()
        if os.environ.get("EBAY_EXCLUDE_SELLERS"):
            eb["exclude_sellers"] = [x.strip() for x in os.environ["EBAY_EXCLUDE_SELLERS"].split(",") if x.strip()]
    return cfg


def main():
    ap = argparse.ArgumentParser(description="Plasma Exchange")
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--db", default=os.path.join(HERE, "market.db"))
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("scrape")
    sp = sub.add_parser("serve")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--every", type=float, help="also scrape every N hours")
    sp.add_argument("--no-browser", action="store_true")
    dp = sub.add_parser("demo")
    dp.add_argument("--port", type=int, default=8765)
    dp.add_argument("--no-browser", action="store_true")
    dp.add_argument("--build-only", action="store_true")
    mp = sub.add_parser("matches")
    mp.add_argument("--key")
    cp = sub.add_parser("check")
    cp.add_argument("url")
    cp.add_argument("--regex")
    sub.add_parser("export")
    argv = sys.argv[1:]
    if not any(x in argv for x in ("scrape", "serve", "demo", "matches", "check", "export")):
        # double-clicked with no command: real data if configured, otherwise the demo
        if os.path.exists(os.path.join(HERE, "config.json")):
            argv += ["serve", "--every", "6"]
        else:
            print("No config.json found next to the program - showing sample data.")
            print("To track real prices: copy config.example.json to config.json, add competitors, restart.\n")
            argv += ["demo"]
    a = ap.parse_args(argv)

    if a.cmd == "demo":
        import demo
        db_path = os.path.join(HERE, "demo.db")
        cfg = demo.build(db_path)
        if a.build_only:
            return
        con = connect(db_path)
        from server import serve
        if not a.no_browser:
            webbrowser.open(f"http://127.0.0.1:{a.port}")
        return serve(con, cfg, a.port)

    if a.cmd == "check":
        from scraper import Fetcher, fetch_listing_price, pack_size, part_tokens
        f = Fetcher(delay=0)
        price, stock, method = fetch_listing_price(f, a.url, "auto", a.regex)
        print(f"price={price} in_stock={stock} method={method}")
        return

    cfg = load_cfg(a.config)
    con = connect(a.db)
    import seed
    seed.load_if_empty(con, cfg)

    def save_roundup(c):
        os.makedirs(os.path.join(HERE, "roundups"), exist_ok=True)
        r = analytics.roundup(c, load_cfg(a.config))
        if r["generated"]:
            path = os.path.join(HERE, "roundups", r["generated"][:10] + ".html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(analytics.roundup_html(r))
            return path

    if a.cmd == "scrape":
        from pipeline import run_scrape
        run_scrape(con, cfg)
        p = save_roundup(con)
        if p:
            print(f"Daily roundup saved: {p}")
    elif a.cmd == "serve":
        from pipeline import run_scrape
        from server import serve

        def scrape_fn(log=print):
            c2 = connect(a.db)
            try:
                run_scrape(c2, load_cfg(a.config), log=log)
                p = save_roundup(c2)
                if p:
                    log(f"Daily roundup saved to {p}")
            finally:
                c2.close()
        if not a.no_browser:
            import threading
            threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{a.port}")).start()
        def save_cfg(c):
            with open(a.config, "w", encoding="utf-8") as f:
                json.dump(c, f, indent=2, ensure_ascii=False)
        serve(con, cfg, a.port, scrape_fn, a.every, save_cfg)
    elif a.cmd == "matches":
        q = "SELECT l.key, p.name, l.seller, l.units, l.source, l.matched_on, l.url FROM listings l LEFT JOIN products p USING(key)"
        args = ()
        if a.key:
            q += " WHERE l.key=?"
            args = (a.key,)
        q += " ORDER BY l.key, l.source='own' DESC, l.seller"
        cur = None
        for r in con.execute(q, args):
            if r["key"] != cur:
                cur = r["key"]
                print(f"\n{r['key']}  {r['name'] or ''}")
            print(f"   {r['seller']:<18} x{r['units']:<4} {r['source']:<6} {r['matched_on'] or '':<12} {r['url']}")
    elif a.cmd == "export":
        import csv
        s = analytics.summary(con, cfg)
        w = csv.writer(sys.stdout)
        cols = ["key", "name", "mine", "low", "spread", "d1", "d7", "sellers", "signal", "suggested_listing"]
        w.writerow(cols)
        for t in s["tickers"]:
            w.writerow([t.get(c) for c in cols])


if __name__ == "__main__":
    main()
