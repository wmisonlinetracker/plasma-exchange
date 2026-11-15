"""Real prices collected from the web on 15 Nov 2026, loaded on first start so the dashboard opens with
real data straight away. The app's own scans replace and extend these from the first run.
Prices are as listed (some ex VAT); the app converts everything to per-unit inc VAT."""
from db import now_iso

SNAPSHOT_DATE = "2026-11-15T12:00:00+00:00"

# key, name, grade, your listing price (inc VAT), units in your listing
MY_PARTS = [
    ("220941", "Powermax 45XP/65/85/105 Nozzle 45A Pack of 5", "compatible", 16.65, 5),
    ("220816", "Powermax 85/105 Nozzle 85A Pack of 5", "compatible", 16.65, 5),
    ("220990", "Powermax 105 Nozzle 105A Pack of 5", "compatible", 16.65, 5),
    ("220930", "Powermax 45XP/65/85/105 Nozzle Finecut 45A Pack of 5", "compatible", 16.65, 5),
    ("220854", "Powermax 45XP/65/85/105 Retaining Cap", "compatible", 14.40, 1),
    ("220953", "Powermax 45XP/65/85/105 Retaining Cap Ohmic", "compatible", 23.40, 1),
    ("220994", "Powermax 105 Swirl Ring 30-105A", "compatible", 14.40, 1),
    ("220993", "Powermax 105 Shield Machine 105A", "compatible", 12.60, 1),
    ("220777", "Powermax 45XP/65/85/105 Electrode 30-105A Original Hypertherm", "genuine", 11.00, 1),
    ("220975", "PMX-125 Nozzle 125A", "compatible", 7.46, 1),
    ("220971", "PMX-125 Electrode", "compatible", 8.57, 1),
    ("420169", "PMX-125 Nozzle 65A", "compatible", 3.95, 1),
]

# key, seller, grade, listed price, units, price includes VAT?, url, listing title
COMPETITORS = [
    ("220941", "H&F Welding Supplies", "genuine", 7.14, 1, False,
     "https://www.hfweldingsupplies.co.uk/products/hypertherm-45a-nozzle-220941", "Hypertherm 45A Nozzle 220941"),
    ("220941", "Rapid Welding", "genuine", 6.96, 1, False,
     "https://www.rapidwelding.com/dynamic/DisplayItem.aspx?c=220941", "Hypertherm Nozzle, for All Duramax Torches (45A)"),
    ("220941", "Basic Welding Supplies", "genuine", 42.00, 5, True,
     "https://www.basicwelding.co.uk/products/powermax45-xp-nozzle", "Hypertherm Powermax45 Plasma Nozzle 220941 (pack 5)"),
    ("220941", "eBay · Hypertherm authorised seller", "genuine", 25.49 + 23.61, 5, True,
     "https://www.ebay.co.uk/itm/Hypertherm-Powermax45-XP-Plasma-w-CPC-25ft-Mech-and-Hand-Torches-088123-/132233370615",
     "Hypertherm Genuine Powermax 45 XP 45 Amp Nozzles 220941 5 Pack (inc. P&P)"),
    ("220941", "eBay · unbranded 10-pack", "budget", 8.32, 10, True,
     "https://www.ebay.co.uk/i/161136936434", "10PK Plasma Cutting Nozzle 220941 for Hypertherm Powermax 65/85/105"),
    ("220816", "eBay · genuine 5-pack", "genuine", 19.89, 5, True,
     "https://www.ebay.co.uk/p/20028320200", "Genuine Hypertherm 220816 Plasma Cutting Nozzle 85 amp Pack of 5"),
    ("220816", "ManoMano · STUNACY", "budget", 14.99, 5, True,
     "https://www.manomano.co.uk/p/5pcs-85a-plasma-cutter-nozzle-tips-220816-fit-for-max85-plasma-cutting-torch-consumable-213641369",
     "5Pcs 85A Plasma Cutter Nozzle Tips 220816 Fit for MAX85"),
    ("220816", "Rapid Welding", "genuine", 168.00, 25, False,
     "https://www.rapidwelding.com/dynamic/DisplayItem.aspx?c=228760", "Hypertherm 220816 85A Nozzle Bulk (Pack of 25)"),
    ("220854", "Rapid Welding", "genuine", 33.60, 1, False,
     "https://www.rapidwelding.com/dynamic/DisplayItem.aspx?c=220854", "Hypertherm Retaining Cap, for All Duramax Torches (10 - 105A)"),
    ("220854", "Basic Welding Supplies", "genuine", 29.90, 1, True,
     "https://www.basicwelding.co.uk/products/powermax45-xp-retaining-cap", "POWERMAX45/45/65/85/105 XP RETAINING CAP 220854"),
    ("220854", "eBay · genuine listing", "genuine", 22.73 + 2.70, 1, True,
     "https://www.ebay.co.uk/b/bn_89309762", "Hypertherm Cutting Torch Retaining Cap 220854 (inc. P&P)"),
    ("220854", "eBay · unbranded 3-pack", "budget", 28.97, 3, True,
     "https://www.ebay.co.uk/b/bn_7022891340", "3Pcs For Hypertherm Retaining Cap 65A 105A 220854"),
    ("220854", "eBay · unbranded single", "budget", 7.99, 1, True,
     "https://www.ebay.co.uk/i/163931690405", "220854 Plasma Retaining Cap Max105"),
    ("220777", "Rapid Welding", "genuine", 12.08, 1, False,
     "https://www.rapidwelding.com/dynamic/DisplayItem.aspx?c=220777", "Hypertherm CopperPlus Electrode, for All Duramax Torches (45 - 105A)"),
    ("220930", "eBay · Hypertherm authorised seller", "genuine", 26.10 + 23.61, 5, True,
     "https://www.ebay.co.uk/itm/Hypertherm-Powermax45-XP-Plasma-w-CPC-25ft-Mech-and-Hand-Torches-088123-/132233370615",
     "Hypertherm Genuine Powermax 45 XP Fine Cut Nozzles 220930 5 Pack (inc. P&P)"),
]


def load_if_empty(con, cfg, log=print):
    if con.execute("SELECT COUNT(*) FROM obs").fetchone()[0]:
        return False
    me = cfg["my_seller"]
    vat = float(cfg.get("vat_rate", 0.2))
    rid = con.execute("INSERT INTO runs(started,finished,notes) VALUES (?,?,?)",
                      (SNAPSHOT_DATE, SNAPSHOT_DATE, "starting snapshot from web search")).lastrowid
    rows = []
    for key, name, grade, price, units in MY_PARTS:
        url = f"https://wmis.uk/search?q={key}"
        con.execute("INSERT OR IGNORE INTO products(key,name,brand,url,grade) VALUES (?,?,?,?,?)",
                    (key, f"{key} - {name}", "Hypertherm", url, grade))
        con.execute("INSERT OR REPLACE INTO listings(key,seller,url,units,source,grade,title) VALUES (?,?,?,?,?,?,?)",
                    (key, me, url, units, "seed", grade, name))
        rows.append((rid, SNAPSHOT_DATE, key, me, url, price, units, round(price / units, 4), 1, 1, None, grade))
    for key, seller, grade, price, units, inc, url, title in COMPETITORS:
        unit = price / units * (1 if inc else 1 + vat)
        con.execute("INSERT OR REPLACE INTO listings(key,seller,url,units,source,grade,title) VALUES (?,?,?,?,?,?,?)",
                    (key, seller, url, units, "seed", grade, title))
        rows.append((rid, SNAPSHOT_DATE, key, seller, url, round(price, 2), units, round(unit, 4), 1, 1, None, grade))
    con.executemany("INSERT INTO obs(run_id,ts,key,seller,url,raw_price,units,unit_price,in_stock,ok,error,grade) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    log(f"Loaded starting snapshot: {len(MY_PARTS)} of your parts, {len(COMPETITORS)} competitor prices")
    return True
