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


def _get(url, poesessid=None):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if poesessid:
        headers["Cookie"] = f"POESESSID={poesessid}"
    with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _strip(s):
    return re.sub(r"<<[^>]*>>", "", s or "").strip()  # localisation markup


def get_gear(account, character, poesessid=None):
    """Read a character's equipped items. Works with NO login if the PoE
    profile is public; a POESESSID (config.json) is only needed for private
    profiles. Returns (slots, error)."""
    if not account or not character:
        return [], "Type your account name and character name first."
    url = ("https://www.pathofexile.com/character-window/get-items?accountName="
           + urllib.parse.quote(account) + "&character="
           + urllib.parse.quote(character) + "&realm=pc")
    try:
        data = _get(url, poesessid)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return [], ("Couldn't read that profile. Either the account/"
                        "character name is wrong, or your PoE profile is set to "
                        "private. Set it to Public (see the steps), or add your "
                        "POESESSID for private profiles.")
        if e.code == 400:
            return [], ("Check the character name — it must match exactly "
                        "(capital letters count).")
        if e.code == 404:
            return [], "Account or character not found. Check the spelling."
        return [], f"PoE returned HTTP {e.code}. Try again in a minute."
    except Exception as e:
        return [], f"Could not reach the PoE site: {e}"
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
