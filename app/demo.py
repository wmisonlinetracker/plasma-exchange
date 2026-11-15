"""Synthetic sample data so the dashboard can be explored before any real scraping.
Competitor names and prices are invented."""
import os
import random
from datetime import datetime, timedelta, timezone

from db import connect

ME = "PLASCOM"
COMPS = ["Competitor A", "Competitor B", "Competitor C", "Competitor D"]

PRODUCTS = [
    # key, name, brand, units in my listing, my pack price
    ("220842", "220842 Electrode, Powermax 65/85/105 - Pack of 5", "Hypertherm", 5, 46.00),
    ("220819", "220819 Nozzle 85A, Powermax - Pack of 5", "Hypertherm", 5, 41.50),
    ("220854", "220854 Retaining Cap, Powermax", "Hypertherm", 1, 24.90),
    ("220857", "220857 Swirl Ring, Powermax", "Hypertherm", 1, 16.80),
    ("220669", "220669 Electrode, Powermax45 - Pack of 5", "Hypertherm", 5, 38.00),
    ("220671", "220671 Nozzle 45A, Powermax45 - Pack of 5", "Hypertherm", 5, 33.00),
    ("9-8215", "9-8215 Electrode, SL60/SL100 - Pack of 5", "Thermal Dynamics", 5, 29.50),
    ("9-8208", "9-8208 Tip 40A, SL60/SL100 - Pack of 5", "Thermal Dynamics", 5, 24.00),
    ("9-8218", "9-8218 Shield Cup, SL60/SL100", "Thermal Dynamics", 1, 21.00),
    ("BK14300-1", "BK14300-1 FlexCut LC125M Electrode 45-125A, Pack of 5", "Lincoln", 5, 35.00),
    ("BK14300-7", "BK14300-7 FlexCut LC125M Nozzle 45A, Pack of 5", "Lincoln", 5, 23.50),
    ("BK14300-13", "BK14300-13 FlexCut LC125M Swirl Ring 45-125A", "Lincoln", 1, 22.00),
    ("BK14300-18", "BK14300-18 FlexCut LC125M Isolator Assembly", "Lincoln", 1, 55.00),
    ("CP-1033", "CP-1033 Electrode, Cebora CP70/CP90 - Pack of 10", "Cebora", 10, 32.00),
    ("JS-P80E", "JS-P80E Electrode, Jasic P80 - Pack of 10", "Jasic", 10, 18.50),
    ("LG-500", "LaserGlaze Anti-Spatter Coating 500ml", "Maintenance", 1, 19.95),
]


def build(path, days=60, runs_per_day=2, seed=7):
    if os.path.exists(path):
        os.remove(path)
    for ext in ("-wal", "-shm"):
        if os.path.exists(path + ext):
            os.remove(path + ext)
    rnd = random.Random(seed)
    con = connect(path)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=days)
    state = {}
    for key, name, brand, units, price in PRODUCTS:
        cost = round(price * rnd.uniform(0.5, 0.65), 2)  # cost of the whole listing, ex VAT
        con.execute("INSERT INTO products(key,name,brand,cost,url,grade) VALUES (?,?,?,?,?,?)",
                    (key, name, brand, cost, f"https://wmis.uk/products/{key.lower()}", "genuine"))
        unit = price / units
        sellers = {ME: {"units": units, "unit": unit, "stock": 1, "url": f"https://wmis.uk/products/{key.lower()}"}}
        con.execute("INSERT INTO listings(key,seller,url,variant_id,units,source,matched_on,grade) VALUES (?,?,?,?,?,?,?,?)", (key, ME, sellers[ME]["url"], None, units, "own", "", "genuine"))
        for c in COMPS:
            if rnd.random() < 0.2:
                continue  # not every competitor stocks every part
            cu = rnd.choice([1, units]) if units > 1 else 1
            u = f"https://{c.lower().replace(' ', '-')}.example/products/{key.lower()}"
            b = unit * rnd.uniform(0.96, 1.16)
            sellers[c] = {"units": cu, "unit": b, "base": b, "stock": 1, "url": u}
            con.execute("INSERT INTO listings(key,seller,url,variant_id,units,source,matched_on,grade) VALUES (?,?,?,?,?,?,?,?)", (key, c, u, None, cu, "auto", key, "genuine"))
        state[key] = sellers

    total = days * runs_per_day
    for i in range(total + 1):
        ts = (start + timedelta(hours=24 / runs_per_day * i)).isoformat()
        rid = con.execute("INSERT INTO runs(started,finished,notes) VALUES (?,?,?)", (ts, ts, "demo")).lastrowid
        rows = []
        for key, sellers in state.items():
            for s, st in sellers.items():
                if s == ME:
                    if rnd.random() < 0.006:
                        st["unit"] *= rnd.uniform(0.95, 1.05)
                else:
                    st["unit"] += (st["base"] - st["unit"]) * 0.01
                    r = rnd.random()
                    if r < 0.015:
                        st["unit"] *= rnd.uniform(0.9, 0.98)   # price cut
                    elif r < 0.03:
                        st["unit"] *= rnd.uniform(1.02, 1.08)  # price rise
                    if st["stock"] and rnd.random() < 0.01:
                        st["stock"] = 0
                    elif not st["stock"] and rnd.random() < 0.15:
                        st["stock"] = 1
                raw = round(st["unit"] * st["units"], 2)
                rows.append((rid, ts, key, s, st["url"], raw, st["units"], round(raw / st["units"], 4), st["stock"], 1, None, "genuine"))
        con.executemany("INSERT INTO obs(run_id,ts,key,seller,url,raw_price,units,unit_price,in_stock,ok,error,grade) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return {
        "my_seller": ME, "compare_basis": "inc_vat", "currency_symbol": "£",
        "signal_threshold_pct": 2.0, "undercut_by": 0.05, "default_min_margin_pct": 20,
        "sellers": {ME: {}, **{c: {} for c in COMPS}},
    }
