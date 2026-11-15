"""Local web server for the dashboard (stdlib only)."""
import csv
import io
import json
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import analytics

import sys
WEB = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "web")


STATUS = {"running": False, "log": [], "started": None}


def make_handler(con, cfg, lock, scrape_now, save_cfg=None):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype="application/json", code=200, extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, default=str)
            if isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            q = urllib.parse.parse_qs(u.query)
            try:
                with lock:
                    if u.path in ("/", "/index.html"):
                        with open(os.path.join(WEB, "index.html"), "rb") as f:
                            return self._send(f.read(), "text/html; charset=utf-8")
                    if u.path == "/api/summary":
                        return self._send(analytics.summary(con, cfg))
                    if u.path == "/api/product":
                        d = analytics.product_detail(con, cfg, q.get("key", [""])[0])
                        return self._send(d or {"error": "not found"}, code=200 if d else 404)
                    if u.path == "/api/status":
                        return self._send({"running": STATUS["running"], "started": STATUS["started"],
                                           "log": STATUS["log"][-6:]})
                    if u.path == "/api/settings":
                        eb = cfg.get("sellers", {}).get("eBay", {})
                        return self._send({"editable": save_cfg is not None,
                                           "ebay_client_id": eb.get("client_id", ""), "ebay_has_secret": bool(eb.get("client_secret")),
                                           "ebay_exclude": ", ".join(eb.get("exclude_sellers", [])),
                                           "drop_alert_pct": cfg.get("drop_alert_pct", 10),
                                           "compatible_discount_vs_genuine_pct": cfg.get("compatible_discount_vs_genuine_pct", 35),
                                           "max_raise_pct": cfg.get("max_raise_pct", 25),
                                           "default_min_margin_pct": cfg.get("default_min_margin_pct", 20),
                                           "undercut_by": cfg.get("undercut_by", 0.05)})
                    if u.path == "/api/roundup":
                        return self._send(analytics.roundup(con, cfg))
                    if u.path == "/roundup":
                        return self._send(analytics.roundup_html(analytics.roundup(con, cfg)), "text/html; charset=utf-8")
                    if u.path == "/api/events":
                        return self._send(analytics.events(con, cfg, limit=int(q.get("limit", ["80"])[0])))
                    if u.path == "/api/export.csv":
                        s = analytics.summary(con, cfg)
                        buf = io.StringIO()
                        cols = ["key", "name", "grade", "mine_units", "mine_listing", "suggested_listing", "change", "signal", "why",
                                "mine", "low", "low_seller", "spread", "genuine_low", "compatible_low", "budget_low",
                                "d1", "d7", "d30", "sellers", "rank", "cost", "floor", "url"]
                        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
                        w.writeheader()
                        w.writerows(s["tickers"])
                        return self._send(buf.getvalue(), "text/csv", extra={"Content-Disposition": "attachment; filename=market.csv"})
                return self._send({"error": "not found"}, code=404)
            except Exception as e:
                return self._send({"error": str(e)}, code=500)

        def do_POST(self):
            if self.path == "/api/settings":
                if save_cfg is None:
                    return self._send({"error": "settings can't be changed in demo mode"}, code=400)
                n = int(self.headers.get("Content-Length") or 0)
                d = json.loads(self.rfile.read(n) or b"{}")
                with lock:
                    eb = cfg.setdefault("sellers", {}).setdefault("eBay", {"platform": "ebay_api", "prices_include_vat": True})
                    if "ebay_client_id" in d:
                        eb["client_id"] = d["ebay_client_id"].strip()
                    if d.get("ebay_client_secret"):
                        eb["client_secret"] = d["ebay_client_secret"].strip()
                    if "ebay_exclude" in d:
                        eb["exclude_sellers"] = [x.strip() for x in d["ebay_exclude"].split(",") if x.strip()]
                    for k in ("drop_alert_pct", "compatible_discount_vs_genuine_pct", "max_raise_pct",
                              "default_min_margin_pct", "undercut_by"):
                        if d.get(k) not in (None, ""):
                            cfg[k] = float(d[k])
                    save_cfg(cfg)
                return self._send({"saved": True})
            if self.path == "/api/scrape":
                if scrape_now is None:
                    return self._send({"error": "scraping disabled in demo mode"}, code=400)
                threading.Thread(target=scrape_now, daemon=True).start()
                return self._send({"started": True})
            return self._send({"error": "not found"}, code=404)
    return H


def serve(con, cfg, port=8765, scrape_fn=None, every_hours=None, save_cfg=None):
    lock = threading.Lock()
    running = threading.Lock()

    def scrape_now():
        if scrape_fn is None or not running.acquire(blocking=False):
            return
        STATUS.update(running=True, started=time.strftime("%H:%M"), log=[])
        try:
            scrape_fn(lambda m: (print(m), STATUS["log"].append(str(m).strip())))
        except Exception as e:
            STATUS["log"].append(f"Scan stopped: {e}")
        finally:
            STATUS["running"] = False
            running.release()

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(con, cfg, lock, scrape_now if scrape_fn else None, save_cfg))
    except OSError:
        print("Plasma Exchange is already running in another window - opening your dashboard.")
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{port}")
        time.sleep(3)
        return

    if every_hours and scrape_fn:
        def loop():
            while True:
                scrape_now()
                time.sleep(float(every_hours) * 3600)
        threading.Thread(target=loop, daemon=True).start()
    print(f"Dashboard: http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
