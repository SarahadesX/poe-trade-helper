# PoE Trade Helper

Reads a Path of Building build and opens a **PoE Trade** search pre-filled with
an item's **literal** mod values (max life 50 → `to maximum Life, min 50`) —
not PoB's translated/weighted trade export.

## Install & auto-updates
Install **Python 3** and **Git**, then **clone** the repo (don't download the
zip, or updates won't work):
```
git clone https://github.com/SarahadesX/poe-trade-helper.git
```
Double-click **`Start PoE Trade Helper.vbs`**. It updates itself, opens in your
browser, and runs with **no console window**. **Close the browser tab to stop
it** (the server shuts itself down a few seconds later).

If nothing happens (e.g. Python not installed), double-click
**`Run PoE Trade Helper.bat`** instead — it shows a console with any error.

## Use
1. Double-click **`Start PoE Trade Helper.vbs`** (hidden), or
   **`Run PoE Trade Helper.bat`** to see the console.
   Opens http://localhost:8770 in your browser.
2. Paste one of:
   - a **maxroll.gg build guide** URL (e.g. `.../poe/build-guides/...`) — the
     progression stages become the tabs automatically,
   - a **pobb.in** / **pastebin** link,
   - or a raw PoB code.
   Then **Load build**.
3. Pick a stage tab (Campaign / Midgame / Endgame — whatever the build defines).
4. **Click a slot** (e.g. Weapon 1).
5. Every mod's **min** box is **pre-filled from the build** (the PoB value, or
   the low end of a `(x-y)` range) so you don't type anything — just
   **Search PoE Trade**. Raise a min for a better roll, clear it for "any", or
   untick a mod to drop it. Uniques also match by name.
   The site opens with the base type + those filters applied.

Every build you load is remembered as a chip under the box (**Saved:**) so you
can flick between builds with one click. Use **✎** to rename one, **✕** to
remove it. The list is stored in `saved_builds.txt`.

### Gear / Skill Tree / Gems tabs
After loading, three tabs appear and stay collapsed until you pick one:
- **🛡 Gear** — the item-set stages + click-a-slot trade search (above), plus a
  **Tree Jewels** section (jewels socketed in the active passive tree —
  regular, cluster, unique and timeless). Clicking **Search PoE Trade** just
  opens the trade site with the item's min stat values filled in — simple.
  **🔍 Search whole set** builds a search for every item in the stage at once
  (throttled to respect trade rate limits), fetches the **cheapest price** for
  each, and shows a **rough total cost** (e.g. "≈ 12.5 Divine") plus a
  **Buy ↗** link per item and **Open all**. Set a **Budget / item** and any
  pricier piece is highlighted **red** ("save up for"). Toggle tree jewels /
  flasks. Prices are a live guide only — click Buy for the real listings.
- **🌳 Skill Tree** — every passive tree the build saved (usually one per
  level/stage), each with a **View tree ↗** link that opens the full
  interactive tree on pathofexile.com.
- **🔮 Gems** — the gem setups per stage: each socket group with its active
  skill (gold) and support gems below, showing level/quality. The active gem
  set is marked ★. Hover a gem for a **buy ↗** link.
- **📖 Guide** — the build author's own notes (leveling order, how to play),
  if the build has any.

### My Gear (see your own character, no login needed)
Click **👤 My Gear** in the header to load your own character's equipped items
and search an upgrade for each piece. The panel walks you through it with links:

1. Set your PoE profile to **Public** (My Account → Privacy Settings) — one-time.
2. Type your **account name** and **character name** → **Show my gear**.

That's it — it reads your public profile from the official site, no cookie or
login required. (If you'd rather keep your profile private, there's an
**advanced** option to paste your POESESSID into `config.json` instead.)

Clicking a tab opens it and hides the others; clicking the open one collapses it.

### maxroll.gg support
Maxroll stores each guide's build in its own planner. The tool follows the
guide's "Open in Path of Building" link to `planners.maxroll.gg`, pulls the
stored `pobCode`, and decodes it like any other PoB build — so pasting the guide
page is all you need.

No install needed — pure Python 3 stdlib.

## Logs
`logs/app.log` records every load/search with timings; `logs/errors.log` holds
errors with full tracebacks. Check these if something hangs or fails. The trade
stat data (~2 MB) is downloaded once at startup (prewarm), so the first build
load is fast rather than stalling on "decoding".

Only one instance can run at a time — launching a second prints "already
running" instead of silently clashing on the port.

## League
The **League** box defaults to the **current challenge league** automatically
(detected from the trade site the moment it goes live — e.g. Curse of the
Allflame), so you don't have to set it. You can still type any league name.

## Config (`config.json`)
- `league` — default league (also selectable in the header dropdown).
- `poesessid` — only needed if Cloudflare blocks the search POST. Copy the
  `POESESSID` cookie value from your logged-in pathofexile.com session.

## How it works
- `pob.py` — resolves the link, base64→zlib→XML decode, parses `<Item>` blocks
  (literal game-copy text) and `<ItemSet>` slot maps.
- `stats.py` — matches each mod line to its trade stat id via the official
  `api/trade/data/stats` dictionary (cached in `cache/`), extracting the number
  as a `min` filter.
- `trade.py` — builds the query JSON, POSTs to `api/trade/search/{league}`,
  returns the real trade URL.
- `app.py` + `index.html` — the local click-a-slot web UI.

## Notes / limits
- An empty min box adds the stat filter with a blank min (fill it in on trade).
- Mods with no matching trade stat are shown as `no trade stat` and skipped.
- PoB ranges like `(214-285)` prefill the low end; "Adds X to Y" uses the first
  number. Edit any min box before searching.
- Same base type is required so power is comparable; loosen by editing the
  opened trade search directly.
