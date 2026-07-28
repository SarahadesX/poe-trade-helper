"""Map literal PoB mod lines -> PoE trade stat ids + numeric min filters.

Uses the official trade stat dictionary (api/trade/data/stats), cached to
disk. Matching is done by normalising both the PoB line and each stat's
template text to a canonical "# for every number" form, so
"+50 to maximum Life" resolves to stat "# to maximum Life" with min=50.
"""

import json
import os
import re
import time
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PoE-Trade-Helper/1.0"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
STATS_URL = "https://www.pathofexile.com/api/trade/data/stats"

# Preference order when the same normalised text exists in several groups.
_TYPE_RANK = {"explicit": 0, "implicit": 1, "fractured": 2, "enchant": 3,
              "crafted": 4, "veiled": 5, "scourge": 6, "pseudo": 9,
              "delve": 7, "monster": 8}

_NUM_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")
# PoB writes rolled/unique ranges as "(low-high)"; collapse to the low end
# so the min filter matches every valid roll ("this item or better").
_RANGE_RE = re.compile(r"\(\s*([+-]?\d+(?:\.\d+)?)\s*-\s*[+-]?\d+(?:\.\d+)?\s*\)")


def collapse_ranges(text: str) -> str:
    return _RANGE_RE.sub(lambda m: m.group(1), text)


# ---- Local vs global stats --------------------------------------------------
# The trade dictionary stores some stats TWICE: once as a global bonus (what a
# jewel or passive grants) and once suffixed "(Local)" -- the value printed on
# the item itself. A body armour's "+88 to Armour" is the local one; asking for
# the global id searches for a chest that ALSO grants +88 armour to everything,
# which essentially nothing has, so the search silently returns nothing.
# Which spelling is right depends on the kind of item the mod sits on.
CTX_ARMOUR = "armour"   # helmet / body / gloves / boots / shield
CTX_WEAPON = "weapon"   # anything you attack with

# Matched against the NORMALISED text (numbers already replaced by '#').
_LOCAL_BY_CTX = {
    CTX_ARMOUR: re.compile(
        r"^#%? (?:to|increased) [\w, ]*"
        r"\b(?:armour|evasion|energy shield|ward)\b"),
    CTX_WEAPON: re.compile(
        r"^(?:adds # to # \w+ damage"
        r"|#% increased (?:attack speed|critical strike chance"
        r"|accuracy rating|physical damage)"
        r"|# to accuracy rating"
        r"|#% chance to poison on hit"
        r"|#% of physical attack damage leeched as (?:life|mana))$"),
}


def _cache_path(name):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, name)


def _fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def _load_stats():
    """Return the raw stats result list, cached for 7 days."""
    p = _cache_path("stats.json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 7 * 86400:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    data = _fetch_json(STATS_URL)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def normalise(text: str) -> str:
    """Canonical form: lowercase, every number -> #, tidy signs/space."""
    t = text.strip().lower()
    t = _NUM_RE.sub("#", t)
    t = t.replace("+#", "#").replace("-#", "#")
    t = re.sub(r"\s+", " ", t).strip()
    return t


class StatIndex:
    """Normalised-text -> best matching stat entry."""

    def __init__(self):
        self._map = {}
        self._local = {}   # same key, but the "(Local)" spelling of the stat
        raw = _load_stats()
        for group in raw.get("result", []):
            gtype = group.get("id", "")  # e.g. "explicit"
            for entry in group.get("entries", []):
                sid = entry.get("id", "")
                text = entry.get("text", "")
                if not sid or not text:
                    continue
                etype = sid.split(".", 1)[0]  # id prefix is authoritative
                key = normalise(text)
                rank = _TYPE_RANK.get(etype, 5)
                if text.endswith(" (Local)"):
                    key = normalise(text[:-len(" (Local)")])
                    cur = self._local.get(key)
                    if cur is None or rank < cur["rank"]:
                        self._local[key] = {"id": sid, "text": text,
                                            "type": etype, "rank": rank}
                    continue
                cur = self._map.get(key)
                if cur is None or rank < cur["rank"]:
                    self._map[key] = {"id": sid, "text": text,
                                      "type": etype, "rank": rank}

    def _lookup(self, key, context):
        """Local spelling first when the item kind calls for it, else global.

        Some stats ("#% increased Armour and Energy Shield") exist ONLY in the
        local spelling, so always fall back to it -- otherwise they'd stop
        matching entirely whenever the item kind is unknown.
        """
        if context:
            pat = _LOCAL_BY_CTX.get(context)
            if pat and pat.match(key):
                hit = self._local.get(key)
                if hit:
                    return hit
        return self._map.get(key) or self._local.get(key)

    def match(self, mod_line: str, context: str = None):
        """Return {id, text, value, matched_text} or None.

        context (CTX_ARMOUR / CTX_WEAPON) says what kind of item the line is
        printed on, so defence and weapon-damage lines resolve to the item's
        own "(Local)" stat rather than the global bonus of the same name.
        """
        line = collapse_ranges(mod_line)
        key = normalise(line)
        hit = self._lookup(key, context)
        if not hit:
            # Retry without a trailing "(implicit)"/"(crafted)" style note.
            stripped = re.sub(r"\s*\([^)]*\)\s*$", "", line)
            if stripped != line:
                key = normalise(stripped)
                hit = self._lookup(key, context)
        if not hit:
            return None
        nums = _NUM_RE.findall(line)
        value = None
        if nums:
            try:
                value = float(nums[0])
                if value == int(value):
                    value = int(value)
            except ValueError:
                value = None
        return {"id": hit["id"], "text": hit["text"],
                "type": hit["type"], "value": value,
                "matched_text": mod_line}


_INDEX = None


def get_index() -> StatIndex:
    global _INDEX
    if _INDEX is None:
        _INDEX = StatIndex()
    return _INDEX
