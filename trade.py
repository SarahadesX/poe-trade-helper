"""Build + create PoE trade searches from matched literal stats."""

import copy
import json
import os
import re
import time
import urllib.request
import urllib.error
import urllib.parse

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PoE-Trade-Helper/1.0"
LEAGUES_URL = "https://www.pathofexile.com/api/trade/data/leagues"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")


def _load_config():
    p = os.path.join(os.path.dirname(__file__), "config.json")
    cfg = {"league": "Standard", "poesessid": ""}
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def get_leagues():
    """Return list of league ids (main realm). Cached 10 min so a new league
    (which appears in the trade API only at launch) is picked up quickly."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = os.path.join(CACHE_DIR, "leagues.json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 600:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    try:
        req = urllib.request.Request(LEAGUES_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        leagues = [x["id"] for x in data.get("result", [])
                   if x.get("realm", "pc") == "pc"]
        with open(p, "w", encoding="utf-8") as f:
            json.dump(leagues, f)
        return leagues
    except Exception:
        return ["Standard", "Hardcore"]


_PERMANENT = {"Standard", "Hardcore", "Ruthless", "Hardcore Ruthless"}


def current_league(leagues):
    """The live softcore challenge league (e.g. 'Curse of the Allflame'),
    auto-detected as the first non-permanent, non-HC, non-Ruthless pc league.
    Returns None between leagues (nothing but permanent leagues live)."""
    for lg in leagues:
        if lg in _PERMANENT:
            continue
        if lg.startswith("Hardcore") or "Ruthless" in lg or "SSF" in lg:
            continue
        return lg
    return None


# Clean flask base types (magic flasks store an affixed name, not the base).
_FLASK_SIZES = ["Small", "Medium", "Large", "Greater", "Grand", "Giant",
                "Colossal", "Sacred", "Hallowed", "Sanctified", "Divine",
                "Eternal"]
_FLASK_BASES = (
    [f"{s} Life Flask" for s in _FLASK_SIZES]
    + [f"{s} Mana Flask" for s in _FLASK_SIZES]
    + [f"{s} Hybrid Flask" for s in _FLASK_SIZES]
    + [f"{u} Flask" for u in (
        "Quicksilver", "Quartz", "Granite", "Jade", "Stibnite", "Silver",
        "Sulphur", "Basalt", "Bismuth", "Amethyst", "Ruby", "Sapphire",
        "Topaz", "Aquamarine", "Diamond", "Gold")]
)
# Longest first so "Colossal Hybrid Flask" wins over "Hybrid Flask".
_FLASK_BASES.sort(key=len, reverse=True)


def resolve_type(item):
    """The exact trade base type to search, or None to search by mods only.

    Rare/Normal/Unique keep their parsed base (already the clean base type).
    Magic items store an *affixed* name, so we recover a known flask base;
    if none matches (magic jewellery/armour), return None -> mods-only search.
    """
    base = (item.get("base") or "").strip()
    if not base:
        return None
    if item.get("rarity") == "MAGIC":
        for fb in _FLASK_BASES:
            if fb in base:
                return fb
        return None  # unknown magic base -> don't send a bogus type
    # Strip PoB's variant annotation, e.g. "Two-Stone Ring (Fire/Cold)".
    base = re.sub(r"\s*\([^)]*\)\s*$", "", base).strip()
    return base or None


def build_query(item, specs, use_type=True, use_name=False, opts=None):
    """Assemble the trade query JSON.

    item: parsed slot dict (base, name, rarity).
    specs: list of {id, min} — min may be None to add the stat filter with an
    EMPTY min box (so the user can type a threshold on the trade site).
    opts: {max_price, currency, corrupted, min_links} extra search filters.
    """
    filters = []
    for sp in specs:
        if not sp or not sp.get("id"):
            continue
        f = {"id": sp["id"], "disabled": False}
        if sp.get("min") is not None:
            f["value"] = {"min": sp["min"]}
        filters.append(f)

    query = {
        "query": {
            "status": {"option": "online"},
            "stats": [{"type": "and", "filters": filters}],
        },
        "sort": {"price": "asc"},
    }

    # Match same base type so power is comparable (e.g. same weapon base dps).
    if use_type:
        t = resolve_type(item)
        if t:
            query["query"]["type"] = t
    if use_name and item.get("rarity") == "UNIQUE" and item.get("name"):
        # Strip PoB's trailing notes, e.g. "The Hand of Phrecia (+1 Corrupt)" or
        # a timeless jewel's "Elegant Hubris [9000; 3; Pain Attunement]".
        nm = re.sub(r"(\s*(\([^)]*\)|\[[^\]]*\]))+\s*$", "", item["name"]).strip()
        if nm:
            query["query"]["name"] = nm

    # Always show only "Buyout or Fixed Price" listings (instant buy) — never
    # negotiate-by-whisper ones. This is the trade site's buyout filter.
    opts = opts or {}
    fdict = {"trade_filters": {"filters": {"sale_type": {"option": "priced"}}}}
    try:
        max_price = float(opts["max_price"]) if opts.get("max_price") not in (
            None, "") else None
    except (TypeError, ValueError):
        max_price = None
    if max_price is not None:
        fdict.setdefault("trade_filters", {}).setdefault("filters", {})[
            "price"] = {"max": max_price, "option": opts.get("currency") or "chaos"}
    if opts.get("corrupted") in ("true", "false"):
        fdict.setdefault("misc_filters", {}).setdefault("filters", {})[
            "corrupted"] = {"option": opts["corrupted"]}
    try:
        min_links = int(opts["min_links"]) if opts.get("min_links") not in (
            None, "") else None
    except (TypeError, ValueError):
        min_links = None
    if min_links:
        fdict.setdefault("socket_filters", {}).setdefault("filters", {})[
            "links"] = {"min": min_links}
    if fdict:
        query["query"]["filters"] = fdict
    return query


def _post_search(query, league, poesessid=""):
    """POST a search; return (data, error). data has id/result/total."""
    url = f"https://www.pathofexile.com/api/trade/search/{urllib.parse.quote(league)}"
    body = json.dumps(query).encode("utf-8")
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://www.pathofexile.com",
        "Referer": f"https://www.pathofexile.com/trade/search/{league}",
    }
    if poesessid:
        headers["Cookie"] = f"POESESSID={poesessid}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        if e.code in (403, 429):
            return None, (f"Blocked by Cloudflare/rate-limit (HTTP {e.code}). "
                          f"Add your POESESSID to config.json. {detail}")
        return None, f"HTTP {e.code}: {detail}"
    except Exception as e:
        return None, f"Request failed: {e}"


def _search_url(league, sid):
    return (f"https://www.pathofexile.com/trade/search/"
            f"{urllib.parse.quote(league)}/{sid}")


def create_search(query, league, poesessid=""):
    """POST the query; return (url, error) for the human trade page."""
    data, err = _post_search(query, league, poesessid)
    if err:
        return None, err
    sid = data.get("id")
    if not sid:
        return None, "Trade API returned no search id."
    return _search_url(league, sid), None


def _fetch_price(item_hash, sid, league, poesessid=""):
    """Return the cheapest listing's {amount, currency} (or None)."""
    u = (f"https://www.pathofexile.com/api/trade/fetch/{item_hash}"
         f"?query={sid}")
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if poesessid:
        headers["Cookie"] = f"POESESSID={poesessid}"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(u, headers=headers), timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        res = data.get("result") or []
        price = ((res[0].get("listing") or {}).get("price") or {}) if res else {}
        amt, cur = price.get("amount"), price.get("currency")
        return {"amount": float(amt), "currency": cur} if amt and cur else None
    except Exception:
        return None


_DIV_RATE_CACHE = {}


def get_divine_rate(league):
    """Chaos per Divine Orb from GGG's bulk-exchange (median ignores troll
    listings). Cached 1h. Returns float or None."""
    hit = _DIV_RATE_CACHE.get(league)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    rate = None
    try:
        body = json.dumps({"query": {"status": {"option": "online"},
                                     "have": ["chaos"], "want": ["divine"]},
                           "sort": {"have": "asc"}, "engine": "new"}).encode()
        u = ("https://www.pathofexile.com/api/trade/exchange/"
             + urllib.parse.quote(league))
        req = urllib.request.Request(
            u, data=body, method="POST",
            headers={"User-Agent": UA, "Content-Type": "application/json",
                     "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        ratios = []
        for v in (data.get("result") or {}).values():
            for o in ((v.get("listing") or {}).get("offers") or []):
                it, ex = o.get("item") or {}, o.get("exchange") or {}
                if it.get("amount") and ex.get("amount"):
                    ratios.append(ex["amount"] / it["amount"])
        ratios.sort()
        if ratios:
            rate = ratios[len(ratios) // 2]  # median
    except Exception:
        pass
    _DIV_RATE_CACHE[league] = (time.time(), rate)
    return rate


def fetch_results(query, league, poesessid="", limit=8):
    """Create a search and return the top listings' full details (price, mods,
    weapon/defence properties) for in-app comparison. Falls back to 'any'
    status if nothing is online (e.g. during a league launch)."""
    data, err = _post_search(query, league, poesessid)
    note = None
    if not err and data and not data.get("result"):
        q2 = copy.deepcopy(query)
        q2["query"]["status"] = {"option": "any"}
        d2, e2 = _post_search(q2, league, poesessid)
        if not e2 and d2 and d2.get("result"):
            data, note = d2, "includes offline sellers"
    if err or not data:
        return {"url": None, "error": err, "items": [], "count": 0, "note": None}
    sid = data.get("id")
    hashes = (data.get("result") or [])[:limit]
    out = {"url": _search_url(league, sid), "error": None, "items": [],
           "count": data.get("total", len(hashes)), "note": note}
    if not hashes:
        return out
    try:
        u = ("https://www.pathofexile.com/api/trade/fetch/" + ",".join(hashes)
             + "?query=" + sid)
        headers = {"User-Agent": UA, "Accept": "application/json"}
        if poesessid:
            headers["Cookie"] = f"POESESSID={poesessid}"
        with urllib.request.urlopen(
                urllib.request.Request(u, headers=headers), timeout=25) as r:
            fd = json.loads(r.read().decode("utf-8"))
    except Exception:
        return out
    for it in (fd.get("result") or []):
        item = it.get("item") or {}
        price = (it.get("listing") or {}).get("price") or {}
        mods = []
        for m in ((item.get("implicitMods") or []) + (item.get("explicitMods")
                  or []) + (item.get("craftedMods") or [])):
            txt = m.get("description") if isinstance(m, dict) else m
            if txt:
                mods.append(txt)
        props = {}
        for p in (item.get("properties") or []):
            vals = p.get("values") or []
            if vals and vals[0]:
                props[p.get("name", "")] = vals[0][0]
        out["items"].append({
            "price": ({"amount": price.get("amount"),
                       "currency": price.get("currency")}
                      if price.get("amount") is not None else None),
            "name": item.get("name") or "",
            "base": item.get("typeLine") or "",
            "corrupted": bool(item.get("corrupted")),
            "props": props,
            "mods": mods,
        })
    return out


def search_and_price(query, league, poesessid=""):
    """Create a search (name/type fallback) AND fetch the cheapest price.
    Returns {url, error, note, price, count}."""
    data, err = _post_search(query, league, poesessid)
    note, q = None, query
    if err and "Unknown item name" in err and q["query"].get("name"):
        q = copy.deepcopy(q)
        q["query"].pop("name", None)
        data, err = _post_search(q, league, poesessid)
        if not err:
            note = "matched by base type + stats (name not recognised)"
    if err and "Unknown item" in err and q["query"].get("type"):
        q = copy.deepcopy(q)
        q["query"].pop("name", None)
        q["query"].pop("type", None)
        data, err = _post_search(q, league, poesessid)
        if not err:
            note = "matched by stats only (base not recognised)"
    if err or not data:
        return {"url": None, "error": err, "note": note, "price": None,
                "count": 0}
    sid = data.get("id")
    result = data.get("result") or []
    price = _fetch_price(result[0], sid, league, poesessid) if result else None
    return {"url": _search_url(league, sid), "error": None, "note": note,
            "price": price, "count": data.get("total", len(result))}


def create_search_smart(query, league, poesessid=""):
    """Create a search; if trade rejects the name/type, progressively drop them
    so the user still gets a working search. Returns (url, error, note)."""
    url, err = create_search(query, league, poesessid)
    if not err:
        return url, None, None
    # Trade doesn't know this exact unique name (often a corrupted/variant
    # item) -> drop the name and search by base type + stats.
    if "Unknown item name" in err and query["query"].get("name"):
        q = copy.deepcopy(query)
        q["query"].pop("name", None)
        url, err = create_search(q, league, poesessid)
        if not err:
            return url, None, ("Trade didn't recognise the unique's exact name, "
                               "so it searched by base type + stats instead.")
    # Base type also unrecognised -> drop it and search by stats only.
    if err and "Unknown item" in err and query["query"].get("type"):
        q = copy.deepcopy(query)
        q["query"].pop("name", None)
        q["query"].pop("type", None)
        url, err = create_search(q, league, poesessid)
        if not err:
            return url, None, ("Trade didn't recognise the item's base type, "
                               "so it searched by stats only.")
    return url, err, None
