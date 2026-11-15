"""One scrape run: pull catalogues, look up each of your parts on every source, record prices."""
import re
import urllib.error
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from db import now_iso
from scraper import (EbayAPI, Fetcher, classify_grade, fetch_listing_price, pack_size, part_tokens,
                     search_lookup, shopify_catalog, shopify_search, template_lookup)


def _platform(sc):
    return (sc.get("platform") or "auto").lower()


def _filtered_tokens(rows, max_freq):
    """part tokens per row, dropping tokens shared by many products (model names like 'LC125M')."""
    toks = [set(part_tokens(r["sku"], r["title"])) for r in rows]
    df = defaultdict(set)
    for r, t in zip(rows, toks):
        for x in t:
            df[x].add(r["product_id"])
    return [{x for x in t if len(df[x]) <= max_freq} for t in toks]


def _primary_parts(row, tokens, match_on="both"):
    """Part number(s) identifying one of YOUR products: first part number in the title and the SKU."""
    out = []
    if match_on in ("title", "both"):
        first = [t for t in part_tokens(row["title"]) if t in tokens]
        if first:
            out.append(first[0])
            for extra in part_tokens(first[0])[1:]:
                if extra in tokens:
                    out.append(extra)
    if match_on in ("sku", "both"):
        out += [t for t in part_tokens(row.get("sku")) if t in tokens]
    return list(dict.fromkeys(out))


def _product_key(row, tokens, used):
    sku = (row.get("sku") or "").strip().upper()
    if sku:
        key = sku
    else:
        first = [t for t in part_tokens(row["title"]) if t in tokens]
        key = first[0] if first else f"V{row['variant_id']}"
    base, n = key, 2
    while key in used:
        key = f"{base}-{n}"
        n += 1
    used.add(key)
    return key


def _upsert_product(con, key, name, brand, url, grade, part):
    con.execute("INSERT INTO products(key,name,brand,url,grade) VALUES (?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET name=excluded.name, brand=excluded.brand, url=excluded.url, grade=excluded.grade",
                (key, name, brand, url, grade))
    return part


def _listing(con, key, seller, url, units, source, matched_on, grade, title, variant_id=None):
    con.execute("INSERT OR REPLACE INTO listings(key,seller,url,variant_id,units,source,matched_on,grade,title) "
                "VALUES (?,?,?,?,?,?,?,?,?)", (key, seller, url, variant_id, units, source, matched_on, grade, title))


def run_scrape(con, cfg, log=print):
    started = now_iso()
    run_id = con.execute("INSERT INTO runs(started) VALUES (?)", (started,)).lastrowid
    con.commit()
    fcfg = cfg.get("fetch", {})
    fetcher = Fetcher(fcfg.get("user_agent"), fcfg.get("delay_seconds", 2),
                      fcfg.get("respect_robots_txt", True), log=log)
    sellers = {k: v for k, v in cfg["sellers"].items() if v.get("enabled", True)}
    me = cfg["my_seller"]
    mcfg = cfg.get("matching", {})
    max_freq = int(mcfg.get("max_token_frequency", 3))
    notes = []
    obs = []  # dicts

    def add_obs(key, seller, url, price, units, stock, grade, err=None):
        units = units or 1
        unit = None
        if price is not None and price > 0:
            unit = price / units
            inc = sellers.get(seller, cfg["sellers"].get(seller.split(" · ")[0], {})).get("prices_include_vat", True)
            vat = float(cfg.get("vat_rate", 0.2))
            if cfg.get("compare_basis", "inc_vat") == "inc_vat" and not inc:
                unit *= (1 + vat)
            elif cfg.get("compare_basis") == "ex_vat" and inc:
                unit /= (1 + vat)
            unit = round(unit, 4)
        elif err is None:
            err = "no price (POA)"
        obs.append(dict(run_id=run_id, ts=now_iso(), key=key, seller=seller, url=url, raw_price=price, units=units,
                        unit_price=unit, in_stock=None if stock is None else int(stock), ok=0 if err else 1,
                        error=err, grade=grade))

    # 1. catalogue pulls (Shopify) ---------------------------------------------------
    catalogs, needs_search = {}, set()
    for name, sc in sellers.items():
        if not sc.get("url") or _platform(sc) not in ("shopify", "auto"):
            continue
        try:
            rows = [r for r in shopify_catalog(fetcher, sc["url"]) if r["price"] and r["price"] > 0]
            if not rows:
                raise RuntimeError("catalogue has no prices")
            catalogs[name] = rows
            log(f"  {name}: {len(rows)} priced items in catalogue")
        except Exception as e:
            if name != me:
                needs_search.add(name)
                log(f"  {name}: catalogue not available ({str(e)[:80]}) - will search part by part instead")
            else:
                log(f"  {name}: couldn't read your shop ({str(e)[:80]}) - using your last known prices")
            continue
        toks = _filtered_tokens(rows, max_freq)
        ts = now_iso()
        con.execute("DELETE FROM catalog WHERE seller=?", (name,))
        con.executemany(
            "INSERT OR REPLACE INTO catalog(seller,variant_id,product_id,title,sku,url,part_numbers,units,price,available,updated)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(name, r["variant_id"], r["product_id"], r["title"], r["sku"], r["url"], " ".join(sorted(t)),
              r["units"], r["price"], r["available"], ts) for r, t in zip(rows, toks)])
        for r, t in zip(rows, toks):
            r["_tokens"] = t

    # 2. your products ---------------------------------------------------------------------
    my_default_grade = cfg["sellers"].get(me, {}).get("grade", "compatible")
    only = [b.lower() for b in cfg.get("only_brands", [])]
    part_of = {}  # key -> clean part number used for per-part lookups
    if me in catalogs:
        con.execute("DELETE FROM listings WHERE seller=? AND source='seed'", (me,))
        mine = [r for r in catalogs[me] if not only or any(b in (r["title"] + " " + r.get("vendor", "") + " " + r.get("body", "")).lower() for b in only)]
        existing = {row["url"]: row["key"] for row in con.execute("SELECT key,url FROM listings WHERE source='own'")}
        used = {row["key"] for row in con.execute("SELECT DISTINCT key FROM listings WHERE source='own'")}
        for r in mine:
            key = existing.get(r["url"])
            if key is None:
                cand = (r.get("sku") or "").strip().upper()
                key = cand if cand and cand not in used else _product_key(r, r["_tokens"], used)
                used.add(key)
            r["_key"] = key
            text = r["title"] + " " + r.get("body", "") + " " + r.get("vendor", "")
            if re.search(r"compatible|replacement|aftermarket|american torch tip|\bATTC?\b|\bPHD", text, re.I) and \
                    not re.search(r"\b(original|genuine)\b", r["title"], re.I):
                grade = "compatible"
            else:
                grade = my_default_grade
            prim = _primary_parts(r, r["_tokens"] or set(part_tokens(r["title"], r["sku"])), mcfg.get("match_on", "both"))
            r["_primary"] = prim
            part_of[key] = prim[0] if prim else None
            _upsert_product(con, key, r["title"], r.get("vendor", ""), r["url"], grade, part_of[key])
            _listing(con, key, me, r["url"], r["units"], "own", "", grade, r["title"], r["variant_id"])
    for p in cfg.get("products", []):
        _upsert_product(con, p["key"], p.get("name", p["key"]), p.get("brand", ""), p.get("url", ""),
                        p.get("grade", my_default_grade), p.get("part", p["key"]))
        part_of[p["key"]] = p.get("part", p["key"])
        if p.get("url"):
            _listing(con, p["key"], me, p["url"], p.get("units", pack_size(p.get("name", ""))), "own", "",
                     p.get("grade", my_default_grade), p.get("name", ""))
    if not part_of:  # catalogue failed this run: reuse known products
        for row in con.execute("SELECT key, name FROM products"):
            toks = part_tokens(row["name"] or "", row["key"])
            part_of[row["key"]] = toks[0] if toks else row["key"]
    for key, cost in (cfg.get("costs") or {}).items():
        con.execute("UPDATE products SET cost=? WHERE key=?", (cost, key))

    # 3. match competitor catalogues by part number -------------------------------------------
    if mcfg.get("auto_match", True) and me in catalogs:
        con.execute("DELETE FROM listings WHERE source='auto' AND seller IN (%s)" % ",".join("?" * len(catalogs)), list(catalogs))
        mine = [r for r in catalogs[me] if "_key" in r]
        for comp, rows in catalogs.items():
            if comp == me:
                continue
            sc = sellers[comp]
            index = defaultdict(list)
            for r in rows:
                for t in r["_tokens"]:
                    index[t].append(r)
            n = 0
            for m in mine:
                seen = set()
                for t in m["_primary"]:
                    for r in index.get(t, []):
                        if r["variant_id"] in seen:
                            continue
                        seen.add(r["variant_id"])
                        g = classify_grade(f"{r['title']} {r.get('vendor', '')} {r.get('sku', '')}", sc.get("grade", "genuine"))
                        _listing(con, m["_key"], comp, r["url"], r["units"], "auto", t, g, r["title"], r["variant_id"])
                        n += 1
            log(f"  {comp}: matched {n} listings to your parts")

    for ml in cfg.get("manual_listings", []):
        _listing(con, ml["key"], ml["seller"], ml["url"], ml.get("units") or 0, "manual", "manual",
                 ml.get("grade", sellers.get(ml["seller"], {}).get("grade", "genuine")), ml.get("title", ""))
    for ex in cfg.get("exclude_matches", []):
        con.execute("DELETE FROM listings WHERE source='auto' AND key=? AND seller LIKE ? AND url LIKE ?",
                    (ex["key"], ex["seller"] + "%", f"%{ex.get('url_contains', '')}%"))
    for ov in cfg.get("units_override", []):
        con.execute("UPDATE listings SET units=? WHERE url LIKE ?", (ov["units"], f"%{ov['url_contains']}%"))
    con.commit()

    # 4. prices for catalogue-based listings + manual URLs ---------------------------------------
    by_variant = {(s, r["variant_id"]): r for s, rows in catalogs.items() for r in rows}
    by_path = defaultdict(list)
    for s, rows in catalogs.items():
        for r in rows:
            by_path[(s, urllib.parse.urlsplit(r["url"]).path.rstrip("/"))].append(r)
    per_part_sellers = {n for n, sc in sellers.items() if _platform(sc) in ("template", "search", "ebay_api")} | needs_search
    for L in con.execute("SELECT * FROM listings").fetchall():
        seller = L["seller"]
        if seller in per_part_sellers or seller.startswith("eBay · ") or L["source"] == "seed":
            continue  # refreshed in step 5
        sc = sellers.get(seller, {})
        row = None
        if seller in catalogs:
            row = by_variant.get((seller, L["variant_id"])) if L["variant_id"] else None
            if row is None:
                parts = urllib.parse.urlsplit(L["url"])
                cands = by_path.get((seller, parts.path.rstrip("/")), [])
                v = urllib.parse.parse_qs(parts.query).get("variant", [None])[0]
                row = next((c for c in cands if c["variant_id"] == v), cands[0] if cands else None)
        if row is not None:
            add_obs(L["key"], seller, L["url"], row["price"], L["units"] or row["units"], row["available"], L["grade"])
        elif L["source"] == "manual" or seller not in catalogs:
            if seller not in sellers:
                continue
            try:
                price, stock, _ = fetch_listing_price(fetcher, L["url"], _platform(sc), sc.get("price_regex"))
                add_obs(L["key"], seller, L["url"], price, L["units"] or 1, stock, L["grade"],
                        None if price else "price not found on page")
            except Exception as e:
                add_obs(L["key"], seller, L["url"], None, L["units"] or 1, None, L["grade"], str(e)[:200])

    # 5. per-part sources: Rapid-style URL templates, site search, Shopify search, eBay API ----------------
    keys_by_part = defaultdict(list)
    for k, p in part_of.items():
        if p:
            keys_by_part[p].append(k)
    miss_days = int(cfg.get("skip_not_found_days", 7))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=miss_days)).isoformat()
    recent_miss = {(r["seller"], r["part"]) for r in con.execute("SELECT seller, part FROM misses WHERE ts > ?", (cutoff,))}
    ebay = None
    for name, sc in sellers.items():
        plat = _platform(sc)
        if name == me or (plat not in ("template", "search", "ebay_api") and name not in needs_search):
            continue
        if plat == "ebay_api":
            if not (sc.get("client_id") and sc.get("client_secret")):
                log(f"  {name}: skipped - add your free eBay API keys to switch it on (see README)")
                continue
            ebay = EbayAPI(sc["client_id"], sc["client_secret"], sc.get("marketplace", "EBAY_GB"))
        rx = re.compile(sc["parts_regex"]) if sc.get("parts_regex") else None
        parts = [p for p in keys_by_part if (not rx or rx.search(p))]
        found, fails = 0, 0
        log(f"  {name}: checking {len(parts)} parts...")
        for part in parts:
            if fails >= 5:
                log(f"  {name}: site not responding - skipped the rest this time")
                break
            if (name, part) in recent_miss:
                continue
            keys = keys_by_part[part]
            try:
                if plat == "ebay_api":
                    hits = [h for h in ebay.search(part) if h["price"]]
                    hits = [h for h in hits if part in part_tokens(h["title"]) or part.replace("-", "") in part_tokens(h["title"])]
                    excl = [x.lower() for x in sc.get("exclude_sellers", [])] + [me.lower()]
                    hits = [h for h in hits if h["seller"].lower() not in excl and me.lower() not in h["title"].lower()]
                    best = {}
                    for h in hits:
                        g = classify_grade(h["title"], "budget", marketplace=True)
                        u = pack_size(h["title"])
                        total = h["price"] + (h["shipping"] if sc.get("include_postage", True) else 0)
                        h.update(grade=g, units=u, total=total, unit=total / u)
                        best.setdefault(g, []).append(h)
                    picked = [h for g in best for h in sorted(best[g], key=lambda x: x["unit"])[:int(sc.get("max_per_grade", 3))]]
                    for h in picked:
                        seller_label = f"eBay · {h['seller']}"
                        for k in keys:
                            _listing(con, k, seller_label, h["url"], h["units"], "auto", part, h["grade"], h["title"])
                            add_obs(k, seller_label, h["url"], h["total"], h["units"], 1, h["grade"])
                    fails = 0
                    if picked:
                        found += 1
                    else:
                        con.execute("INSERT OR REPLACE INTO misses VALUES (?,?,?)", (name, part, now_iso()))
                    continue
                if plat == "template":
                    url = sc["url_template"].format(part=urllib.parse.quote(part), part_lower=part.lower())
                    r = template_lookup(fetcher, url, part, sc.get("price_regex"))
                    res = [(r[0], r[1], r[2], url)] if r else []
                elif plat == "search":
                    r = search_lookup(fetcher, sc["search_url"], sc["link_regex"], part, sc.get("price_regex"))
                    res = [r] if r else []
                else:  # Shopify store that blocks products.json
                    hits = shopify_search(fetcher, sc["url"], part)
                    res = [(h["price"], h["available"], h["title"], h["url"]) for h in hits
                           if h["price"] and (part in part_tokens(h["title"]) or part in h["url"].upper())]
                fails = 0
                if not res:
                    con.execute("INSERT OR REPLACE INTO misses VALUES (?,?,?)", (name, part, now_iso()))
                    continue
                found += 1
                for price, stock, title, url in res:
                    units = pack_size(title)
                    g = classify_grade(title, sc.get("grade", "genuine"))
                    for k in keys:
                        _listing(con, k, name, url, units, "auto", part, g, title)
                        add_obs(k, name, url, price, units, stock, g)
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    con.execute("INSERT OR REPLACE INTO misses VALUES (?,?,?)", (name, part, now_iso()))
                    fails = 0
                else:
                    fails += 1
                    log(f"    {part}: {str(e)[:100]}")
            except Exception as e:
                fails += 1
                log(f"    {part}: {str(e)[:100]}")
        log(f"  {name}: found {found} of your parts")
        con.commit()

    con.executemany("INSERT INTO obs(run_id,ts,key,seller,url,raw_price,units,unit_price,in_stock,ok,error,grade) "
                    "VALUES (:run_id,:ts,:key,:seller,:url,:raw_price,:units,:unit_price,:in_stock,:ok,:error,:grade)", obs)
    ok = sum(1 for o in obs if o["ok"])
    notes.append(f"{ok}/{len(obs)} listings priced")
    con.execute("UPDATE runs SET finished=?, notes=? WHERE id=?", (now_iso(), "; ".join(notes), run_id))
    con.commit()
    log(f"Run {run_id} done: {ok}/{len(obs)} listings priced")
    return run_id
