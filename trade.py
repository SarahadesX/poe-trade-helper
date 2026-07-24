"""Build + create PoE trade searches from matched literal stats."""

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
    """Return list of league ids (main realm), cached 1 day."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = os.path.join(CACHE_DIR, "leagues.json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 86400:
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


def build_query(item, specs, use_type=True, use_name=False):
    """Assemble the trade query JSON.

    item: parsed slot dict (base, name, rarity).
    specs: list of {id, min} — min may be None to add the stat filter with an
    EMPTY min box (so the user can type a threshold on the trade site).
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
        query["query"]["name"] = item["name"]
    return query


def create_search(query, league, poesessid=""):
    """POST the query; return (url, error).

    On success url is the human trade page. On Cloudflare/login block,
    url is None and error explains the fallback.
    """
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
            data = json.loads(r.read().decode("utf-8"))
        sid = data.get("id")
        if not sid:
            return None, "Trade API returned no search id."
        page = f"https://www.pathofexile.com/trade/search/{urllib.parse.quote(league)}/{sid}"
        return page, None
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
