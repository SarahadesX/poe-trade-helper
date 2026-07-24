"""Read the user's own characters + equipped gear from GGG's account API.

Needs the user's POESESSID cookie (set in config.json). The gear is returned
in the SAME shape as parsed build items, so it flows into the same search flow.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PoE-Trade-Helper/1.0"

_FRAME_RARITY = {0: "NORMAL", 1: "MAGIC", 2: "RARE", 3: "UNIQUE", 4: "GEM"}

# GGG inventoryId -> friendly slot label.
_SLOTS = {
    "Weapon": "Weapon 1", "Offhand": "Weapon 2", "Weapon2": "Weapon 1 (swap)",
    "Offhand2": "Weapon 2 (swap)", "Helm": "Helmet", "BodyArmour": "Body Armour",
    "Gloves": "Gloves", "Boots": "Boots", "Amulet": "Amulet", "Ring": "Ring 1",
    "Ring2": "Ring 2", "Belt": "Belt",
}


def _get(url, poesessid):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json",
        "Cookie": f"POESESSID={poesessid}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _friendly_error(e):
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403):
            return ("Login not accepted. Check your POESESSID in config.json "
                    "(it expires — copy a fresh one).")
        if e.code == 404:
            return "Character or account not found."
        return f"PoE returned HTTP {e.code}."
    return f"Could not reach PoE: {e}"


def get_characters(poesessid):
    """Return (characters, error). characters: [{name, league, level, class}]."""
    if not poesessid:
        return [], ("No POESESSID set. Add your POESESSID cookie to "
                    "config.json to connect your account.")
    try:
        data = _get("https://www.pathofexile.com/character-window/"
                    "get-characters", poesessid)
    except Exception as e:
        return [], _friendly_error(e)
    chars = [{"name": c.get("name", ""), "league": c.get("league", ""),
              "level": c.get("level", 0), "class": c.get("class", "")}
             for c in (data or []) if c.get("name")]
    return chars, None


def _strip(s):
    return re.sub(r"<<[^>]*>>", "", s or "").strip()  # localisation markup


def get_gear(character, poesessid, account=None):
    """Return (slots, error). Each slot is {slot,name,base,rarity,mods}."""
    if not poesessid:
        return [], "No POESESSID set (see config.json)."
    url = ("https://www.pathofexile.com/character-window/get-items?character="
           + urllib.parse.quote(character))
    if account:
        url += "&accountName=" + urllib.parse.quote(account)
    try:
        data = _get(url, poesessid)
    except Exception as e:
        return [], _friendly_error(e)
    slots = []
    for it in (data.get("items") or []):
        inv = it.get("inventoryId", "")
        if inv not in _SLOTS:  # skip flasks/jewels/inventory for now
            continue
        mods = ((it.get("implicitMods") or []) + (it.get("explicitMods") or [])
                + (it.get("craftedMods") or []) + (it.get("fracturedMods") or []))
        slots.append({
            "slot": _SLOTS.get(inv, inv),
            "name": _strip(it.get("name", "")),
            "base": _strip(it.get("typeLine") or it.get("baseType", "")),
            "rarity": _FRAME_RARITY.get(it.get("frameType", 0), "NORMAL"),
            "mods": [_strip(m) for m in mods],
        })
    return slots, None
