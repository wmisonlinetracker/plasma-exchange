"""Turns raw observations into market data: tickers, suggested prices, chart series, daily roundup."""
import html as _html
from collections import defaultdict
from datetime import datetime, timedelta

GRADES = ("genuine", "compatible", "budget")
GRADE_LABEL = {"genuine": "Genuine OEM", "compatible": "Branded compatible", "budget": "Budget / unbranded"}


def _dt(s):
    return datetime.fromisoformat(s)


def _pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round((a - b) / b * 100, 2)


def _series(con, key=None, stale_hours=48):
    """{key: [(run_id, ts, {(seller, grade): rec})]} - a seller's last price is carried forward for up to
    `stale_hours` so one failed fetch doesn't make them vanish from the market."""
    q = ("SELECT o.run_id, o.ts, o.key, o.seller, o.url, o.raw_price, o.units, o.unit_price, o.in_stock, "
         "COALESCE(o.grade, l.grade, 'genuine') AS grade FROM obs o "
         "LEFT JOIN listings l ON l.key=o.key AND l.seller=o.seller AND l.url=o.url "
         "WHERE o.ok=1 AND o.unit_price IS NOT NULL")
    args = ()
    if key:
        q += " AND o.key=?"
        args = (key,)
    q += " ORDER BY o.run_id"
    per_key = defaultdict(lambda: defaultdict(list))
    run_ts = {}
    for r in con.execute(q, args):
        per_key[r["key"]][r["run_id"]].append(r)
        run_ts[r["run_id"]] = r["ts"]
    out = {}
    for k, runs in per_key.items():
        last = {}
        snaps = []
        for rid in sorted(runs):
            this = {}
            for r in runs[rid]:
                sk = (r["seller"], r["grade"])
                rec = {"seller": r["seller"], "grade": r["grade"], "price": r["unit_price"], "stock": r["in_stock"],
                       "url": r["url"], "raw": r["raw_price"], "units": r["units"]}
                cur = this.get(sk)
                if cur is None or (rec["stock"] != 0 and (cur["stock"] == 0 or rec["price"] < cur["price"])):
                    this[sk] = rec
            for sk, rec in this.items():
                last[sk] = (rec, run_ts[rid])
            now = _dt(run_ts[rid])
            snap = {sk: rec for sk, (rec, ts) in last.items() if (now - _dt(ts)) <= timedelta(hours=stale_hours)}
            snaps.append((rid, run_ts[rid], snap))
        out[k] = snaps
    return out


def _low(snap, me, grade=None):
    """(cheapest unit price, seller, n in stock) among competitors, optionally of one grade."""
    comp = [v for (s, g), v in snap.items() if s != me and (grade is None or g == grade)]
    instock = [v for v in comp if v["stock"] != 0]
    pool = instock or comp
    if not pool:
        return None, None, 0
    best = min(pool, key=lambda v: v["price"])
    return best["price"], best["seller"], len(instock)


def _mine(snap, me):
    mine = [v for (s, g), v in snap.items() if s == me]
    return min(mine, key=lambda v: v["price"]) if mine else None


def _benchmark(snap, me, my_grade, disc):
    """The price we judge you against: like-for-like if it exists, otherwise genuine minus a discount."""
    like, like_seller, n_like = _low(snap, me, my_grade)
    if like is not None and n_like > 0:
        return like, like_seller, "like"
    if my_grade != "genuine":
        gen, gen_seller, n_gen = _low(snap, me, "genuine")
        if gen is not None:
            return round(gen * (1 - disc / 100), 4), gen_seller, "genuine"
    if like is not None:
        return like, like_seller, "like-oos"
    return None, None, None


def _value_at(series, me, my_grade, disc, when):
    val = None
    for _, ts, snap in series:
        if _dt(ts) <= when:
            v = _benchmark(snap, me, my_grade, disc)[0]
            if v is not None:
                val = v
        else:
            break
    return val


def summary(con, cfg):
    me = cfg["my_seller"]
    thr = float(cfg.get("signal_threshold_pct", 2.0))
    undercut = float(cfg.get("undercut_by", 0.05))
    disc = float(cfg.get("compatible_discount_vs_genuine_pct", 35))
    default_margin = cfg.get("default_min_margin_pct")
    products = {r["key"]: dict(r) for r in con.execute("SELECT * FROM products")}
    series = _series(con)
    tickers = []
    for key, p in products.items():
        s = series.get(key, [])
        if not s:
            continue
        _, last_ts, snap = s[-1]
        now = _dt(last_ts)
        my = _mine(snap, me)
        mine = my["price"] if my else None
        mine_units = (my or {}).get("units") or 1
        g = p.get("grade") or "compatible"
        bench, bench_seller, basis = _benchmark(snap, me, g, disc)
        lows = {gr: _low(snap, me, gr) for gr in GRADES}
        n_comp = len({s_ for (s_, _g) in snap if s_ != me})
        spread = _pct(mine, bench)
        floor = None
        if p.get("cost") is not None:
            margin = (cfg.get("margins") or {}).get(key, default_margin) or 0
            f = p["cost"] * (1 + margin / 100) / mine_units
            if cfg.get("compare_basis", "inc_vat") == "inc_vat" and cfg.get("costs_ex_vat", True):
                f *= 1 + float(cfg.get("vat_rate", 0.2))
            floor = round(f, 4)
        suggestion, why = None, ""
        if mine is None:
            signal = "NOT PRICED"
        elif bench is None:
            signal = "NO MATCH YET"
            why = "No competitor listing found for this part yet."
        else:
            target = bench if basis == "genuine" else bench - undercut
            if basis == "genuine":
                why = f"No {GRADE_LABEL[g]} competitor in stock, so priced {disc:.0f}% below the cheapest genuine part ({bench_seller})."
            elif basis == "like-oos":
                why = f"All {GRADE_LABEL[g]} competitors are out of stock."
            else:
                why = f"{undercut * 100:.0f}p under the cheapest {GRADE_LABEL[g]} seller in stock ({bench_seller})."
            if basis == "like-oos":
                signal = "COMPETITORS OOS"
            elif spread > thr:
                signal = "OVERPRICED"
                suggestion = target
            elif spread < -thr:
                signal = "HEADROOM"
                suggestion = target
            else:
                signal = "COMPETITIVE"
                why = "Within ±%g%% of the market - no change needed." % thr
            max_raise = cfg.get("max_raise_pct", 25)
            if suggestion is not None and max_raise is not None and suggestion > mine * (1 + max_raise / 100):
                suggestion = round(mine * (1 + max_raise / 100), 4)
                why += f" Rise capped at {max_raise:.0f}% in one step."
            if suggestion is not None and floor is not None and suggestion < floor:
                suggestion = floor
                why += " Held at your margin floor."
                if signal == "OVERPRICED":
                    signal = "OVERPRICED · AT FLOOR"
        d1 = _pct(bench, _value_at(s, me, g, disc, now - timedelta(days=1)))
        d7 = _pct(bench, _value_at(s, me, g, disc, now - timedelta(days=7)))
        d30 = _pct(bench, _value_at(s, me, g, disc, now - timedelta(days=30)))
        rank = None
        if mine is not None:
            same = [v["price"] for (s_, gr), v in snap.items() if s_ != me and gr == g]
            rank = 1 + sum(1 for x in same if x < mine - 1e-9)
        sugg_listing = round(suggestion * mine_units, 2) if suggestion is not None else None
        tickers.append({
            "key": key, "name": p["name"], "brand": p.get("brand") or "", "url": p.get("url"), "grade": g,
            "mine": mine, "mine_units": mine_units, "mine_listing": round(mine * mine_units, 2) if mine else None,
            "low": bench, "low_seller": bench_seller, "basis": basis,
            "genuine_low": lows["genuine"][0], "compatible_low": lows["compatible"][0], "budget_low": lows["budget"][0],
            "spread": spread, "d1": d1, "d7": d7, "d30": d30, "sellers": n_comp, "in_stock": sum(l[2] for l in lows.values()),
            "rank": rank, "signal": signal, "why": why,
            "suggested_unit": round(suggestion, 4) if suggestion is not None else None,
            "suggested_listing": sugg_listing,
            "change": round(sugg_listing - mine * mine_units, 2) if (sugg_listing is not None and mine) else None,
            "cost": p.get("cost"), "floor": floor, "updated": last_ts,
        })
    order = {"OVERPRICED": 0, "OVERPRICED · AT FLOOR": 1, "HEADROOM": 2, "COMPETITIVE": 3}
    tickers.sort(key=lambda t: (order.get(t["signal"], 4), -(abs(t["spread"] or 0))))
    spreads = [t["spread"] for t in tickers if t["spread"] is not None]
    runs = [dict(r) for r in con.execute("SELECT id, started, notes FROM runs WHERE finished IS NOT NULL ORDER BY id")]
    return {
        "my_seller": me, "basis": cfg.get("compare_basis", "inc_vat"), "currency": cfg.get("currency_symbol", "£"),
        "threshold": thr, "last_run": runs[-1]["started"] if runs else None,
        "last_note": runs[-1]["notes"] if runs else None, "runs": len(runs),
        "index": round(sum(spreads) / len(spreads), 2) if spreads else None,
        "index_history": _index_history(series, me, products, disc),
        "counts": {sig: sum(1 for t in tickers if t["signal"].startswith(sig))
                   for sig in ("OVERPRICED", "COMPETITIVE", "HEADROOM", "COMPETITORS OOS", "NO MATCH YET")},
        "tickers": tickers,
        "sellers": sorted({s for k in series.values() for _, _, snap in k for (s, _g) in snap}),
        "grade_labels": GRADE_LABEL,
        "drop_alert_pct": float(cfg.get("drop_alert_pct", 10)),
    }


def _index_history(series, me, products, disc):
    per_day = defaultdict(list)
    for key, s in series.items():
        g = (products.get(key) or {}).get("grade") or "compatible"
        for _, ts, snap in s:
            my = _mine(snap, me)
            bench = _benchmark(snap, me, g, disc)[0]
            sp = _pct(my["price"] if my else None, bench)
            if sp is not None:
                per_day[ts[:10]].append(sp)
    return [{"d": d, "v": round(sum(v) / len(v), 2)} for d, v in sorted(per_day.items())]


def product_detail(con, cfg, key):
    me = cfg["my_seller"]
    disc = float(cfg.get("compatible_discount_vs_genuine_pct", 35))
    s = _series(con, key).get(key, [])
    if not s:
        return None
    prod = dict(con.execute("SELECT * FROM products WHERE key=?", (key,)).fetchone() or {})
    g = prod.get("grade") or "compatible"
    # one point per day (last scrape of the day) for: you, like-for-like low, genuine low, budget low
    days = {}
    for _, ts, snap in s:
        my = _mine(snap, me)
        days[ts[:10]] = {
            "d": ts[:10],
            "you": my["price"] if my else None,
            "like": _low(snap, me, g)[0],
            "genuine": _low(snap, me, "genuine")[0],
            "compatible": _low(snap, me, "compatible")[0],
            "budget": _low(snap, me, "budget")[0],
            "target": _benchmark(snap, me, g, disc)[0],
        }
    _, last_ts, last = s[-1]
    book = sorted(last.values(), key=lambda x: (x["stock"] == 0, x["price"]))
    listings = [dict(r) for r in con.execute(
        "SELECT seller,url,units,source,matched_on,grade,title FROM listings WHERE key=? ORDER BY seller", (key,))]
    return {"key": key, "product": prod, "grade": g, "daily": [days[d] for d in sorted(days)],
            "book": book, "listings": listings, "events": events(con, cfg, key=key, limit=40)}


def events(con, cfg, key=None, limit=80, min_pct=0.5, since=None):
    """Price moves between consecutive observations of the same listing."""
    me = cfg["my_seller"]
    q = ("SELECT o.ts,o.key,o.seller,o.url,o.unit_price,o.in_stock, COALESCE(o.grade,l.grade,'genuine') AS grade "
         "FROM obs o LEFT JOIN listings l ON l.key=o.key AND l.seller=o.seller AND l.url=o.url "
         "WHERE o.ok=1 AND o.unit_price IS NOT NULL")
    args = []
    if key:
        q += " AND o.key=?"
        args.append(key)
    q += " ORDER BY o.run_id"
    last = {}
    out = []
    prods = {r["key"]: dict(r) for r in con.execute("SELECT key,name,grade FROM products")}
    for r in con.execute(q, args):
        k = (r["key"], r["seller"], r["url"])
        prev = last.get(k)
        if prev and (since is None or r["ts"] >= since):
            pp, ps = prev
            p = prods.get(r["key"], {})
            base = {"ts": r["ts"], "key": r["key"], "name": p.get("name", ""), "seller": r["seller"],
                    "grade": r["grade"], "like_for_like": r["grade"] == (p.get("grade") or "compatible"),
                    "mine": r["seller"] == me, "url": r["url"]}
            ch = _pct(r["unit_price"], pp)
            if ch is not None and abs(ch) >= min_pct:
                out.append({**base, "from": pp, "to": r["unit_price"], "pct": ch, "kind": "price"})
            if ps is not None and r["in_stock"] is not None and ps != r["in_stock"]:
                out.append({**base, "kind": "in stock" if r["in_stock"] else "out of stock"})
        last[k] = (r["unit_price"], r["in_stock"])
    out.reverse()
    return out[:limit]


# ---------------------------------------------------------------- daily roundup

def roundup(con, cfg, hours=24):
    s = summary(con, cfg)
    drop = float(cfg.get("drop_alert_pct", 10))
    if not s["last_run"]:
        return {"generated": None, "drops": [], "overpriced": [], "headroom": [], "drop_pct": drop}
    since = (_dt(s["last_run"]) - timedelta(hours=hours)).isoformat()
    tick = {t["key"]: t for t in s["tickers"]}
    drops = []
    for e in events(con, cfg, limit=5000, since=since):
        if e["kind"] != "price" or e["mine"] or e["pct"] > -drop:
            continue
        t = tick.get(e["key"], {})
        mine = t.get("mine")
        drops.append({**e, "your_unit": mine, "units": t.get("mine_units", 1),
                      "now_cheaper_than_you": mine is not None and e["to"] < mine,
                      "suggested_listing": t.get("suggested_listing"), "signal": t.get("signal"), "why": t.get("why")})
    drops.sort(key=lambda d: (not d["like_for_like"], not d["now_cheaper_than_you"], d["pct"]))
    over = [t for t in s["tickers"] if t["signal"].startswith("OVERPRICED")][:15]
    head = sorted([t for t in s["tickers"] if t["signal"] == "HEADROOM"], key=lambda t: t["spread"])[:10]
    return {"generated": s["last_run"], "since": since, "drop_pct": drop, "drops": drops,
            "overpriced": over, "headroom": head, "currency": s["currency"], "my_seller": s["my_seller"]}


def roundup_html(r):
    c = r.get("currency", "£")
    e = _html.escape

    def m(v):
        return "–" if v is None else f"{c}{v:,.2f}"

    rows = []
    for d in r["drops"]:
        tag = "LIKE-FOR-LIKE" if d["like_for_like"] else GRADE_LABEL.get(d["grade"], d["grade"]).upper()
        alert = "<b style='color:#c0392b'>Now cheaper than you</b>" if d["now_cheaper_than_you"] else "Still above you"
        rows.append(f"<tr><td><b>{e(d['key'])}</b><br><small>{e(d['name'])}</small></td><td>{e(d['seller'])}<br><small>{tag}</small></td>"
                    f"<td>{m(d['from'])} → <b>{m(d['to'])}</b><br><small style='color:#c0392b'>{d['pct']:+.1f}%</small></td>"
                    f"<td>{m(d['your_unit'])}</td><td>{alert}</td>"
                    f"<td>{m(d['suggested_listing'])}{'' if not d['suggested_listing'] else ' for ' + str(d['units'])}</td>"
                    f"<td><a href='{e(d['url'] or '#')}'>view</a></td></tr>")
    drops = ("<table><tr><th>Part</th><th>Competitor</th><th>Price per unit</th><th>Your price / unit</th><th>Status</th>"
             "<th>Suggested listing price</th><th></th></tr>" + "".join(rows) + "</table>") if rows else \
        f"<p>No competitor cut a price by {r['drop_pct']:.0f}% or more in the last 24 hours.</p>"

    def tlist(items):
        if not items:
            return "<p>None.</p>"
        return "<table><tr><th>Part</th><th>You (listing)</th><th>Suggested</th><th>Change</th><th>Why</th></tr>" + "".join(
            f"<tr><td><b>{e(t['key'])}</b><br><small>{e(t['name'])}</small></td><td>{m(t['mine_listing'])}</td>"
            f"<td><b>{m(t['suggested_listing'])}</b></td><td>{'' if t['change'] is None else ('+' if t['change'] > 0 else '') + m(t['change']).replace(c + '-', '-' + c)}</td>"
            f"<td><small>{e(t['why'])}</small></td></tr>" for t in items) + "</table>"

    when = r["generated"][:16].replace("T", " ") if r["generated"] else "–"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Daily roundup {when[:10]}</title>
<style>body{{font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#1d2733}}
h1{{font-size:22px;margin:0}}h2{{font-size:16px;margin:28px 0 8px;border-bottom:2px solid #1d2733;padding-bottom:4px}}
table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:7px 8px;border-bottom:1px solid #e3e7ec;vertical-align:top}}
th{{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#66727f}}small{{color:#66727f}}a{{color:#1f6feb}}</style></head><body>
<h1>{e(r.get('my_seller', ''))} · Daily pricing roundup</h1><p style="color:#66727f">Data up to {when} UTC</p>
<h2>1. Act now: competitors who cut prices by {r['drop_pct']:.0f}%+ in the last 24h ({len(r['drops'])})</h2>{drops}
<h2>2. You're dearer than the market ({len(r['overpriced'])})</h2>{tlist(r['overpriced'])}
<h2>3. You could charge more ({len(r['headroom'])})</h2>{tlist(r['headroom'])}
</body></html>"""
