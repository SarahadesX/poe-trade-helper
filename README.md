# PoE Trade Helper

Reads a Path of Building build and opens a **PoE Trade** search pre-filled with
an item's **literal** mod values (max life 50 → `to maximum Life, min 50`) —
not PoB's translated/weighted trade export.

## Use
1. Double-click **`Run PoE Trade Helper.bat`** (or `python app.py`).
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
