"""Fetching and price extraction.

Two ways prices come in:
  1. Shopify catalogue pull  -> GET {base}/products.json?limit=250&page=N
     One request per 250 products, so a whole competitor catalogue costs a
     handful of requests. Used for any seller with platform "shopify"/"auto".
  2. Single product page     -> JSON-LD, <meta> price tags, WooCommerce markup,
     Shopify {url}.js, or a custom regex from config. Used for listings you
     add by hand for non-Shopify competitors.
"""
import gzip
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib
from html import unescape

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class Fetcher:
    def __init__(self, user_agent=None, delay=2.0, respect_robots=True, timeout=25, log=print):
        self.ua = user_agent or DEFAULT_UA
        self.delay = float(delay)
        self.respect_robots = respect_robots
        self.timeout = timeout
        self.log = log
        self._last = {}
        self._robots = {}

    def _allowed(self, url):
        if not self.respect_robots:
            return True
        p = urllib.parse.urlsplit(url)
        root = f"{p.scheme}://{p.netloc}"
        rp = self._robots.get(root)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                body = self._raw(root + "/robots.txt", check=False)
                rp.parse(body.splitlines())
            except Exception:
                rp.parse([])  # no robots.txt -> allow
            self._robots[root] = rp
        return rp.can_fetch(self.ua, url)

    def _throttle(self, url):
        host = urllib.parse.urlsplit(url).netloc
        wait = self.delay - (time.time() - self._last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.time()

    def _raw(self, url, check=True):
        if check and not self._allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        self._throttle(url)
        req = urllib.request.Request(url, headers={
            "User-Agent": self.ua,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        })
        last_err = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = r.read()
                    enc = (r.headers.get("Content-Encoding") or "").lower()
                    if enc == "gzip":
                        data = gzip.decompress(data)
                    elif enc == "deflate":
                        data = zlib.decompress(data)
                    charset = r.headers.get_content_charset() or "utf-8"
                    return data.decode(charset, errors="replace")
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (429, 503):
                    time.sleep(10 * (attempt + 1))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
                time.sleep(3 * (attempt + 1))
        raise last_err

    def get(self, url):
        return self._raw(url)


# ---------------------------------------------------------------- parsing helpers

PRICE_RE = re.compile(r"(\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})|\d+(?:\.\d{1,2})?)")


def to_price(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = unescape(str(v)).replace("\xa0", " ")
    m = PRICE_RE.search(s.replace("£", " ").replace("€", " ").replace("$", " "))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "").replace(" ", ""))
    except ValueError:
        return None


PACK_PATTERNS = [
    re.compile(r"\bp(?:ac)?kt?\.?\s*of\s*(\d{1,4})", re.I),      # pack of 5 / pk of 5 / pkt of 5
    re.compile(r"\bpack\s+(\d{1,3})\b", re.I),                     # (pack 5)
    re.compile(r"\b(\d{1,4})\s*/\s*(?:pk|pack)\b", re.I),             # 5/pk
    re.compile(r"box\s*of\s*(\d{1,4})", re.I),
    re.compile(r"(\d{1,4})\s*(?:-|\s)?(?:pk|pack|pcs|pieces|per\s*pack)\b", re.I),
    re.compile(r"\bqty\.?\s*:?\s*(\d{1,4})\b", re.I),
    re.compile(r"\b(?:x|×)\s?(\d{1,3})\b", re.I),
    re.compile(r"\b(\d{1,3})\s?(?:x|×)\b(?!\s*\d)", re.I),
]


def pack_size(*texts):
    for t in texts:
        if not t:
            continue
        for pat in PACK_PATTERNS:
            m = pat.search(t)
            if m:
                n = int(m.group(1))
                if 1 <= n <= 1000:
                    return n
    return 1


TOKEN_RE = re.compile(r"[A-Z0-9][A-Z0-9\-./]*[A-Z0-9]")
AMPS_RE = re.compile(r"^\d+(?:[.,]\d+)?(?:-\d+)?(?:A|AMP|AMPS|V|MM|CM|W|KW|ML|L|LTR|KG|G|PSI|BAR)$")


def part_tokens(*texts):
    """Candidate manufacturer part numbers from a title/SKU, in order of appearance.
    e.g. 'Hypertherm 220842 Electrode' -> ['220842']; 'BK14300-1 Electrode' -> ['BK14300-1','BK143001']
    """
    out = []
    for t in texts:
        if not t:
            continue
        for tok in TOKEN_RE.findall(str(t).upper()):
            tok = tok.strip("-./")
            digits = sum(c.isdigit() for c in tok)
            letters = sum(c.isalpha() for c in tok)
            if len(tok) < 4 or digits < 3:
                continue
            if digits < 4 and letters == 0:
                continue
            if AMPS_RE.match(tok):
                continue
            if tok not in out:
                out.append(tok)
            # OEM number hidden inside a compatible/pack code: 220990-UR, 220994ATTC, 220842X5, HY220842
            core = re.match(r"^[A-Z]{0,4}-?(\d{6})(?:-?(?:UR|ATTC|ATT|TC|X\d{1,3}|-\d{1,3}))?$", tok)
            if core and core.group(1) not in out:
                out.append(core.group(1))
            flat = re.sub(r"[\-./]", "", tok)
            if flat != tok and len(flat) >= 4 and flat not in out:
                out.append(flat)
    return out


# ---------------------------------------------------------------- Shopify catalogue

KNOWN_BRANDS = ["Hypertherm", "Thermal Dynamics", "ESAB", "Lincoln", "Cebora", "GYS", "Jasic", "AJAN",
                "American Torch Tip", "Kjellberg", "Trafimet", "Miller", "Kemppi", "Telwin", "Binzel",
                "Victor", "Koike", "Messer", "Hortech", "Sherman", "R-Tech", "Parweld", "LaserGlaze"]
MODEL_HINTS = {"POWERMAX": "Hypertherm", "MAXPRO": "Hypertherm", "HPR": "Hypertherm", "PMX": "Hypertherm",
               "DURAMAX": "Hypertherm", "SL60": "Thermal Dynamics", "SL100": "Thermal Dynamics",
               "1TORCH": "Thermal Dynamics", "FLEXCUT": "Lincoln", "TOMAHAWK": "Lincoln", "CUTMASTER": "Thermal Dynamics"}


def detect_brand(*texts, brands=None):
    blob = " ".join(t for t in texts if t).upper()
    for b in (brands or KNOWN_BRANDS):
        if b.upper() in blob:
            return b
    for hint, b in MODEL_HINTS.items():
        if hint in blob:
            return b
    return ""


def shopify_catalog(fetcher, base_url, max_pages=40):
    """Yield dicts for every variant on a Shopify store via products.json."""
    base = base_url.rstrip("/")
    endpoints = [base + "/products.json", base + "/collections/all/products.json"]
    last = None
    for ep in endpoints:
        got_any = False
        try:
            for page in range(1, max_pages + 1):
                body = fetcher.get(f"{ep}?limit=250&page={page}")
                products = json.loads(body).get("products", [])
                if not products:
                    break
                got_any = True
                for p in products:
                    tags = p.get("tags") or []
                    tags = " ".join(tags) if isinstance(tags, list) else str(tags)
                    vendor = detect_brand(p.get("title"), p.get("vendor"), p.get("product_type"), tags) or p.get("vendor") or ""
                    for v in p.get("variants", []):
                        vtitle = v.get("title") or ""
                        full_title = p.get("title", "") + ("" if vtitle in ("", "Default Title") else f" - {vtitle}")
                        yield {
                            "product_id": str(p.get("id")),
                            "variant_id": str(v.get("id")),
                            "title": full_title,
                            "vendor": vendor,
                            "sku": v.get("sku") or "",
                            "url": f"{base}/products/{p.get('handle')}?variant={v.get('id')}",
                            "price": to_price(v.get("price")),
                            "available": 1 if v.get("available", True) else 0,
                            "units": pack_size(vtitle, p.get("title", "")),
                            "body": re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", p.get("body_html") or "")))[:600],
                        }
                if len(products) < 250:
                    break
        except Exception as e:  # try the next endpoint
            if got_any:
                raise
            last = e
            continue
        if got_any:
            return
    raise RuntimeError(f"No Shopify catalogue found at {base_url} ({last or 'empty'})")


# ---------------------------------------------------------------- single page

def _walk_jsonld(node, found):
    if isinstance(node, list):
        for n in node:
            _walk_jsonld(n, found)
    elif isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if "Product" in types or "ProductGroup" in types:
            found.append(node)
        for k in ("@graph", "hasVariant", "itemListElement", "mainEntity"):
            if k in node:
                _walk_jsonld(node[k], found)


def _offer_price(offers):
    if isinstance(offers, list):
        prices = [_offer_price(o) for o in offers]
        prices = [p for p in prices if p and p[0] is not None]
        return min(prices, key=lambda x: x[0]) if prices else None
    if not isinstance(offers, dict):
        return None
    price = offers.get("price") or offers.get("lowPrice")
    if price is None and isinstance(offers.get("priceSpecification"), (dict, list)):
        ps = offers["priceSpecification"]
        ps = ps[0] if isinstance(ps, list) else ps
        price = ps.get("price")
    avail = str(offers.get("availability", "")).lower()
    in_stock = None if not avail else (0 if ("outofstock" in avail or "soldout" in avail or "discontinued" in avail) else 1)
    return (to_price(price), in_stock)


def extract_from_html(html, custom_regex=None):
    """Return (price, in_stock, method) from a product page."""
    if custom_regex:
        m = re.search(custom_regex, html, re.S | re.I)
        if m:
            return to_price(m.group(1)), None, "regex"

    # JSON-LD
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except Exception:
            try:
                data = json.loads(re.sub(r"[\x00-\x1f]", " ", block.strip()))
            except Exception:
                continue
        found = []
        _walk_jsonld(data, found)
        for prod in found:
            r = _offer_price(prod.get("offers"))
            if r and r[0] is not None:
                return r[0], r[1], "json-ld"

    # meta tags
    for prop in ("product:price:amount", "og:price:amount", "product:sale_price:amount"):
        m = re.search(r'<meta[^>]+(?:property|name)=["\']' + re.escape(prop) + r'["\'][^>]+content=["\']([^"\']+)', html, re.I) \
            or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']' + re.escape(prop), html, re.I)
        if m and to_price(m.group(1)) is not None:
            stock = None
            ma = re.search(r'<meta[^>]+(?:property|name)=["\'](?:product|og):availability["\'][^>]+content=["\']([^"\']+)', html, re.I)
            if ma:
                stock = 0 if "out" in ma.group(1).lower() else 1
            return to_price(m.group(1)), stock, "meta"

    m = re.search(r'itemprop=["\']price["\'][^>]*content=["\']([^"\']+)', html, re.I)
    if m:
        return to_price(m.group(1)), None, "microdata"

    # WooCommerce: prefer <ins> (sale) amount inside p.price
    m = re.search(r'<p[^>]+class=["\'][^"\']*\bprice\b[^"\']*["\'][^>]*>(.*?)</p>', html, re.S | re.I)
    if m:
        block = m.group(1)
        ins = re.search(r"<ins.*?</ins>", block, re.S)
        amounts = re.findall(r'woocommerce-Price-amount[^>]*>(.*?)</span>\s*</', ins.group(0) if ins else block, re.S)
        if amounts:
            txt = re.sub(r"<[^>]+>", "", amounts[0])
            return to_price(txt), None, "woocommerce"

    # last resort: first £ amount on the page
    m = re.search(r"£\s?(\d{1,5}(?:,\d{3})*(?:\.\d{2})?)", html)
    if m:
        return to_price(m.group(1)), None, "first-£"
    return None, None, "none"


def fetch_listing_price(fetcher, url, platform="auto", custom_regex=None):
    """Price for one product URL. Returns (price, in_stock, method)."""
    if platform in ("shopify", "auto") and "/products/" in url and not custom_regex:
        parts = urllib.parse.urlsplit(url)
        js_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + ".js", "", ""))
        variant = urllib.parse.parse_qs(parts.query).get("variant", [None])[0]
        try:
            data = json.loads(fetcher.get(js_url))
            variants = data.get("variants", [])
            v = next((x for x in variants if str(x.get("id")) == str(variant)), None) if variant else None
            if v is None and variants:
                v = variants[0]
            price = (v or data).get("price")
            # products/<handle>.js returns prices in minor units (pence)
            return float(price) / 100.0, 1 if (v or data).get("available", True) else 0, "shopify.js"
        except Exception:
            if platform == "shopify":
                raise
    html = fetcher.get(url)
    return extract_from_html(html, custom_regex)


# ---------------------------------------------------------------- genuine vs compatible vs budget

BRANDED_COMPAT = ["thermacut", "american torch tip", "attc", "-ur ", "plascom", "swp ", "specialised welding",
                  "trafimet", "hortech", "tecmo", "kjellberg-type"]
GENUINE_WORDS = ["genuine", "original", " oem", "authorised", "authorized"]
NOT_GENUINE = ["non original", "non-original", "not original", "compatible", "replacement", "to suit", "fit for",
               "fits ", "for hypertherm", "suitable for", "aftermarket", "nachbau", "unbranded", "generic"]
BUDGET_HINTS = ["pcs", "pc ", "unbranded", "fit for", "for hypertherm", "for powermax", "nachbau", "generic", "10pk",
                "20pk", "50pk", "100pk"]


def classify_grade(text, default="genuine", marketplace=False):
    """genuine = made by the OEM; compatible = branded aftermarket (Thermacut, ATT, PLASCOM...);
    budget = unbranded/generic marketplace parts."""
    t = " " + (text or "").lower().replace("\n", " ") + " "
    if any(k in t for k in BRANDED_COMPAT):
        return "compatible"
    if any(k in t for k in GENUINE_WORDS) and not any(k in t for k in NOT_GENUINE[:3]):
        return "genuine"
    if any(k in t for k in NOT_GENUINE):
        return "budget" if (marketplace or any(k in t for k in BUDGET_HINTS)) else "compatible"
    return default


def page_title(html):
    for pat in (r"<h1[^>]*>(.*?)</h1>", r"<title[^>]*>(.*?)</title>", r'<meta[^>]+og:title[^>]+content=["\']([^"\']+)'):
        m = re.search(pat, html, re.S | re.I)
        if m:
            t = unescape(re.sub(r"<[^>]+>", " ", m.group(1)))
            t = re.sub(r"\s+", " ", t).strip()
            if t:
                return t
    return ""


# ---------------------------------------------------------------- per-part sources

def shopify_search(fetcher, base_url, part, limit=10):
    """Shopify's public predictive search - used when a store blocks products.json."""
    base = base_url.rstrip("/")
    url = f"{base}/search/suggest.json?q={urllib.parse.quote(part)}&resources[type]=product&resources[limit]={limit}"
    data = json.loads(fetcher.get(url))
    out = []
    for p in data.get("resources", {}).get("results", {}).get("products", []):
        u = p.get("url") or ""
        if u.startswith("/"):
            u = base + u.split("?")[0]
        out.append({"title": p.get("title", ""), "url": u, "price": to_price(p.get("price") or p.get("price_min")),
                    "available": 1 if p.get("available", True) else 0, "vendor": p.get("vendor", "")})
    return out


def template_lookup(fetcher, url, part, price_regex=None):
    """Fetch a URL built from a template, confirm the page is about `part`, return (price, stock, title)."""
    html = fetcher.get(url)
    flat = re.sub(r"[\-./ ]", "", part.upper())
    if part.upper() not in html.upper() and flat not in re.sub(r"[\-./ ]", "", html.upper()):
        return None
    price, stock, _ = extract_from_html(html, price_regex)
    if price is None or price <= 0:
        return None
    if re.search(r"out of stock|sold out|currently unavailable", html, re.I) and not re.search(r"\bin stock\b", html, re.I):
        stock = 0
    return price, stock, page_title(html)


def search_lookup(fetcher, search_url, link_regex, part, price_regex=None):
    """Search a site for `part`, follow the first matching product link, return (price, stock, title, url)."""
    html = fetcher.get(search_url.format(part=urllib.parse.quote(part), part_lower=part.lower()))
    pat = link_regex.format(part=re.escape(part.lower()))
    m = re.search(pat, html, re.I)
    if not m:
        return None
    link = urllib.parse.urljoin(search_url, unescape(m.group(1)))
    r = template_lookup(fetcher, link, part, price_regex)
    return (r + (link,)) if r else None


class EbayAPI:
    """eBay's official Browse API (free developer keys from developer.ebay.com)."""
    TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
    SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

    def __init__(self, client_id, client_secret, marketplace="EBAY_GB", timeout=25):
        self.cid, self.secret, self.mkt, self.timeout = client_id, client_secret, marketplace, timeout
        self._token, self._exp = None, 0

    def _auth(self):
        if self._token and time.time() < self._exp - 60:
            return self._token
        import base64
        basic = base64.b64encode(f"{self.cid}:{self.secret}".encode()).decode()
        body = urllib.parse.urlencode({"grant_type": "client_credentials",
                                       "scope": "https://api.ebay.com/oauth/api_scope"}).encode()
        req = urllib.request.Request(self.TOKEN_URL, data=body, headers={
            "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            d = json.loads(r.read())
        self._token, self._exp = d["access_token"], time.time() + int(d.get("expires_in", 7200))
        return self._token

    def search(self, query, limit=50):
        q = urllib.parse.urlencode({"q": query, "limit": limit,
                                    "filter": "buyingOptions:{FIXED_PRICE},conditions:{NEW},deliveryCountry:GB"})
        req = urllib.request.Request(f"{self.SEARCH_URL}?{q}", headers={
            "Authorization": f"Bearer {self._auth()}", "X-EBAY-C-MARKETPLACE-ID": self.mkt})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            d = json.loads(r.read())
        out = []
        for it in d.get("itemSummaries", []):
            price = to_price((it.get("price") or {}).get("value"))
            ship = 0.0
            for so in it.get("shippingOptions") or []:
                c = to_price((so.get("shippingCost") or {}).get("value"))
                if c is not None:
                    ship = c
                    break
            out.append({"title": it.get("title", ""), "price": price, "shipping": ship,
                        "url": it.get("itemWebUrl"), "seller": (it.get("seller") or {}).get("username", "?"),
                        "currency": (it.get("price") or {}).get("currency", "GBP")})
        return out
