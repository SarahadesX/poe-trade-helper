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

# Mod kinds worth searching separately: only those that occupy a DIFFERENT
# place on the item. An implicit is not interchangeable with a rolled mod, so
# it needs its own id. A bench-crafted or fractured mod sits exactly where a
# rolled one would and trade matches it with the explicit id -- narrowing
# those to crafted.*/fractured.* would only hide equally good items.
_SEARCHABLE_KINDS = frozenset(("implicit", "enchant"))

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


def write_json_atomic(path, data):
    """Write a cache file in one step.

    This file is ~2 MB. Writing it in place means a crash, a closed laptop or
    two threads writing at once leaves half a file behind -- which then fails
    to parse on every later run, and the only cure is deleting it by hand.
    Write to a temp file and rename: readers see either the old file or the
    new one, never a partial one.
    """
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _load_stats():
    """Return the raw stats result list, cached for 7 days."""
    p = _cache_path("stats.json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 7 * 86400:
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (ValueError, OSError):
            pass          # damaged by an older non-atomic write: re-download
    data = _fetch_json(STATS_URL)
    write_json_atomic(p, data)
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
        self._map = {}     # key -> best-ranked global stat
        self._local = {}   # key -> best-ranked "(Local)" spelling
        self._all = {}     # (kind, is_local, key) -> that exact stat
        raw = _load_stats()
        for group in raw.get("result", []):
            gtype = group.get("id", "")  # e.g. "explicit"
            for entry in group.get("entries", []):
                sid = entry.get("id", "")
                text = entry.get("text", "")
                if not sid or not text:
                    continue
                etype = sid.split(".", 1)[0]  # id prefix is authoritative
                is_local = text.endswith(" (Local)")
                key = normalise(text[:-len(" (Local)")] if is_local else text)
                rank = _TYPE_RANK.get(etype, 5)
                rec = {"id": sid, "text": text, "type": etype, "rank": rank}
                bucket = self._local if is_local else self._map
                cur = bucket.get(key)
                if cur is None or rank < cur["rank"]:
                    bucket[key] = rec
                self._all.setdefault((etype, is_local, key), rec)

    def _lookup(self, key, context=None, kind=None):
        """Pick the stat id that matches how this mod sits on the item.

        Two independent axes:
          local  -- is it the item's own armour/damage, or a global bonus?
          kind   -- implicit / crafted / fractured, each its own trade group.
        Either may be unknown, and some stats exist in only one spelling, so
        every step falls back rather than giving up (a wrong-but-close id
        still finds items; no id at all silently drops the requirement).
        """
        want_local = False
        if context:
            pat = _LOCAL_BY_CTX.get(context)
            want_local = bool(pat and pat.match(key))
        order = (True, False) if want_local else (False, True)
        if kind in _SEARCHABLE_KINDS:
            for is_local in order:
                rec = self._all.get((kind, is_local, key))
                if rec:
                    return rec
        for is_local in order:
            rec = (self._local if is_local else self._map).get(key)
            if rec:
                return rec
        return None

    def match(self, mod_line: str, context: str = None, kind: str = None):
        """Return {id, text, value, matched_text} or None.

        context (CTX_ARMOUR / CTX_WEAPON) says what kind of item the line is
        printed on, so defence and weapon-damage lines resolve to the item's
        own "(Local)" stat rather than the global bonus of the same name.
        kind ("implicit"/"crafted"/"fractured") says which trade stat group
        the mod belongs to.
        """
        line = collapse_ranges(mod_line)
        key = normalise(line)
        hit = self._lookup(key, context, kind)
        if not hit:
            # Retry without a trailing "(implicit)"/"(crafted)" style note.
            stripped = re.sub(r"\s*\([^)]*\)\s*$", "", line)
            if stripped != line:
                key = normalise(stripped)
                hit = self._lookup(key, context, kind)
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
