"""Path of Building code fetching + decoding + parsing.

A PoB share code is URL-safe base64 -> zlib deflate -> XML.
Links (pobb.in / pastebin) resolve to that raw code.
The XML holds <Item> blocks (literal game-copy text) and <ItemSet> tabs
that map equipment slots to item ids (Leveling / Mid game / Endgame, etc.).
"""

import base64
import json
import re
import zlib
import urllib.request
import xml.etree.ElementTree as ET


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PoE-Trade-Helper/1.0"

MAXROLL_LOAD = "https://planners.maxroll.gg/profiles/load/poe/{id}"

# Metadata line keys inside an <Item> block that are NOT mods.
_META_KEYS = (
    "Rarity:", "Unique ID:", "Item Level:", "LevelReq:", "Quality:",
    "Sockets:", "Radius:", "Limited to:", "Prefix:", "Suffix:",
    "Requires ", "Talisman Tier:", "Armour:", "Evasion Rating:",
    "Energy Shield:", "Ward:", "Physical Damage:", "Elemental Damage:",
    "Critical Strike Chance:", "Attacks per Second:", "Weapon Range:",
    "Chance to Block:", "Stack Size:", "Item Class:", "Corrupted",
    "Mirrored", "Split", "Crafted:", "Selected Variant:", "Has Alt Variant",
    "Cluster Jewel", "Catalyst", "Anointed", "Implicits:",
)


def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


def _maxroll_code(link: str) -> str:
    """Resolve any maxroll.gg URL to its stored PoB code.

    Handles build-guide pages (finds the 'Open in Path of Building' link),
    /poe/pob/<id> short links, and planners.maxroll.gg profile URLs.
    The planner profile JSON stores the PoB code at data.pobCode.
    """
    low = link.lower()
    m = re.search(r"/(?:poe/pob|profiles(?:/load)?/poe)/([a-z0-9]+)", low)
    pid = m.group(1) if m else None

    if pid is None:
        # A build-guide (or other) page: find the linked pob short id.
        html = _http_get(link)
        found = re.search(r"/poe/pob/([a-z0-9]+)", html)
        if not found:
            raise ValueError("No 'Open in Path of Building' link found on that "
                             "maxroll page.")
        pid = found.group(1)

    profile = json.loads(_http_get(MAXROLL_LOAD.format(id=pid)))
    data = profile.get("data")
    if isinstance(data, str):
        data = json.loads(data)
    code = (data or {}).get("pobCode")
    if not code:
        raise ValueError("maxroll profile had no pobCode.")
    return code


def _resolve_to_code(link_or_code: str) -> str:
    """Turn a supported URL (or raw code) into the raw PoB code.

    Supports: maxroll.gg build guides / pob links / planner profiles,
    pobb.in, pastebin, or a pasted raw PoB code.
    """
    s = link_or_code.strip()
    if not s:
        raise ValueError("Empty input")

    if s.startswith("http://") or s.startswith("https://"):
        low = s.lower()
        if "maxroll.gg" in low:
            return _maxroll_code(s)
        url = s
        if "pobb.in/" in low and "/raw" not in low:
            url = s.rstrip("/") + "/raw"
        elif "pastebin.com/" in low and "/raw/" not in low:
            key = s.rstrip("/").split("/")[-1]
            url = "https://pastebin.com/raw/" + key
        return _http_get(url).strip()
    return s


def decode_code(code: str) -> str:
    """Decode a raw PoB code into its XML string.

    pobb.in uses URL-safe base64; maxroll uses standard base64 -- try both.
    """
    code = code.strip()
    padded = code + "=" * (-len(code) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return zlib.decompress(decoder(padded)).decode("utf-8", "replace")
        except Exception:
            continue
    raise ValueError("Could not decode PoB code (base64/zlib failed).")


def _parse_item_block(text: str):
    """Parse one <Item> text block into {name, base, rarity, mods:[...]}."""
    lines = [ln.rstrip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return None

    rarity = ""
    idx = 0
    if lines[0].startswith("Rarity:"):
        rarity = lines[0].split(":", 1)[1].strip()
        idx = 1

    # Name / base type lines.
    name, base = "", ""
    if rarity in ("RARE", "UNIQUE") and idx + 1 < len(lines):
        name = lines[idx]
        base = lines[idx + 1]
        idx += 2
    elif idx < len(lines):
        # Magic/Normal: single line is the (affixed) base type.
        base = lines[idx]
        name = base
        idx += 1

    # Uniques can carry several historical versions of the item; each mod is
    # tagged {variant:N} and the build picks one via "Selected Variant: N"
    # (plus optional "Selected Alt Variant" for a 2nd dimension). Keep only the
    # mods for the selected variant(s), else every version's roll shows up.
    selected = set()
    for ln in lines:
        sv = re.match(r"Selected (?:Alt )?Variant(?: \d+)?:\s*(\d+)", ln)
        if sv:
            selected.add(sv.group(1))

    mods = []
    for ln in lines[idx:]:
        if any(ln.startswith(k) for k in _META_KEYS):
            continue
        vm = re.match(r"\{variant:([\d,]+)\}", ln)
        if vm and selected and not (set(vm.group(1).split(",")) & selected):
            continue  # a different version's roll -- skip it
        # Strip PoB tag prefixes like {crafted}{range:0.5} and variant markers.
        clean = re.sub(r"^(\{[^}]*\})+", "", ln).strip()
        clean = re.sub(r"\{[^}]*\}", "", clean).strip()  # inline {tags}
        if not clean:
            continue
        # Skip pure metadata that slipped through.
        if ":" in clean and clean.split(":", 1)[0] in (
            "Variant", "Requires Level", "Requires Class"):
            continue
        mods.append(clean)
    return {"name": name, "base": base, "rarity": rarity, "mods": mods}


def parse_build(link_or_code: str) -> dict:
    """Return {item_sets:[{title, slots:[{slot,name,base,rarity,mods}]}]}."""
    code = _resolve_to_code(link_or_code)
    xml = decode_code(code)
    root = ET.fromstring(xml)

    items_node = root.find("Items")
    if items_node is None:
        raise ValueError("No <Items> section in this PoB build.")

    # id -> parsed item.
    items_by_id = {}
    for item_el in items_node.findall("Item"):
        iid = item_el.get("id")
        parsed = _parse_item_block(item_el.text or "")
        if iid and parsed:
            items_by_id[iid] = parsed

    sets = []
    set_els = items_node.findall("ItemSet")
    if not set_els:
        # Older builds: slots live directly on <Items>.
        set_els = [items_node]

    for i, se in enumerate(set_els, 1):
        title = se.get("title") or f"Item Set {i}"
        slots = []
        for slot_el in se.findall("Slot"):
            slot_name = slot_el.get("name", "")
            item_id = slot_el.get("itemId", "")
            if item_id == "0" or not item_id:
                continue
            it = items_by_id.get(item_id)
            if not it:
                continue
            slots.append({
                "slot": slot_name,
                "name": it["name"],
                "base": it["base"],
                "rarity": it["rarity"],
                "mods": it["mods"],
            })
        if slots:
            sets.append({"title": title, "slots": slots})

    if not sets:
        raise ValueError("No equipped items found in this build.")

    return {"item_sets": sets, "trees": _parse_trees(root),
            "skills": _parse_skills(root)}


def _parse_skills(root):
    """Gem setups. <SkillSet>s (often per stage) -> <Skill> socket groups ->
    <Gem>s. gemId containing 'SupportGem' marks a support gem."""
    node = root.find("Skills")
    if node is None:
        return []
    active = str(node.get("activeSkillSet", ""))
    set_els = node.findall("SkillSet") or [node]
    out = []
    for i, ss in enumerate(set_els, 1):
        groups = []
        for sk in ss.findall("Skill"):
            grp_on = sk.get("enabled", "true") != "false"
            gems = []
            for g in sk.findall("Gem"):
                gid = g.get("gemId", "")
                skid = g.get("skillId", "")
                if not (g.get("nameSpec") or skid):
                    continue
                try:
                    lvl = int(g.get("level", "0"))
                except ValueError:
                    lvl = 0
                try:
                    qual = int(g.get("quality", "0"))
                except ValueError:
                    qual = 0
                gems.append({
                    "name": g.get("nameSpec") or skid,
                    "level": lvl,
                    "quality": qual,
                    "support": "SupportGem" in gid or skid.startswith("Support"),
                    "enabled": grp_on and g.get("enabled", "true") != "false",
                })
            if gems:
                groups.append({"gems": gems})
        if groups:
            sid = ss.get("id")
            # PoB's activeSkillSet is the SkillSet *id* (unlike trees, which use
            # the spec index). Matching by index too caused a false 2nd active.
            out.append({
                "title": ss.get("title") or f"Gem Set {i}",
                "active": sid is not None and str(sid) == active,
                "groups": groups,
            })
    return out


# Passive-tree class ids -> name (ascendancy names vary per class, skipped).
_CLASSES = {"0": "Scion", "1": "Marauder", "2": "Ranger", "3": "Witch",
            "4": "Duelist", "5": "Templar", "6": "Shadow"}


def _parse_trees(root):
    """Each <Spec> is a passive tree (often per level); PoB stores a ready
    <URL> to the official interactive tree, plus a title + node list."""
    trees = []
    tree_node = root.find("Tree")
    if tree_node is None:
        return trees
    active = str(tree_node.get("activeSpec", ""))
    for i, spec in enumerate(tree_node.findall("Spec"), 1):
        url_el = spec.find("URL")
        url = (url_el.text or "").strip() if url_el is not None else ""
        nodes = spec.get("nodes", "")
        count = len([n for n in nodes.split(",") if n.strip()]) if nodes else 0
        trees.append({
            "title": spec.get("title") or f"Tree {i}",
            "url": url,
            "nodes": count,
            "version": (spec.get("treeVersion", "") or "").replace("_", "."),
            "class": _CLASSES.get(spec.get("classId", ""), ""),
            "active": str(i) == active,
        })
    return trees
