"""SQLite storage for catalogues, product mappings and price observations."""
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started TEXT NOT NULL,
  finished TEXT,
  notes TEXT
);
-- Every variant seen on a seller's catalogue (Shopify products.json etc.)
CREATE TABLE IF NOT EXISTS catalog(
  seller TEXT NOT NULL,
  variant_id TEXT NOT NULL,
  product_id TEXT,
  title TEXT,
  sku TEXT,
  url TEXT,
  part_numbers TEXT,      -- space separated, upper-case
  units INTEGER DEFAULT 1,
  price REAL,
  available INTEGER,
  updated TEXT,
  PRIMARY KEY(seller, variant_id)
);
-- Your products = the "tickers"
CREATE TABLE IF NOT EXISTS products(
  key TEXT PRIMARY KEY,
  name TEXT,
  brand TEXT,
  cost REAL,
  url TEXT
);
-- Which listing on which seller corresponds to which ticker
CREATE TABLE IF NOT EXISTS listings(
  key TEXT NOT NULL,
  seller TEXT NOT NULL,
  url TEXT NOT NULL,
  variant_id TEXT,
  units INTEGER DEFAULT 1,
  source TEXT,           -- auto | manual | own
  matched_on TEXT,
  PRIMARY KEY(key, seller, url)
);
CREATE TABLE IF NOT EXISTS obs(
  run_id INTEGER,
  ts TEXT NOT NULL,
  key TEXT NOT NULL,
  seller TEXT NOT NULL,
  url TEXT,
  raw_price REAL,
  units INTEGER,
  unit_price REAL,        -- normalised: per unit, VAT basis per config
  in_stock INTEGER,
  ok INTEGER,
  error TEXT
);
CREATE TABLE IF NOT EXISTS misses(
  seller TEXT, part TEXT, ts TEXT, PRIMARY KEY(seller, part)
);
CREATE INDEX IF NOT EXISTS obs_key_ts ON obs(key, ts);
CREATE INDEX IF NOT EXISTS obs_run ON obs(run_id);
"""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path):
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.executescript(SCHEMA)
    # upgrades for databases created by older versions
    for table, col, typ in (("products", "grade", "TEXT"), ("listings", "grade", "TEXT"),
                            ("listings", "title", "TEXT"), ("obs", "grade", "TEXT")):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        if col not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    con.commit()
    return con
