"""Local web app: paste PoB link -> click a slot -> open PoE trade search
pre-filled with the item's LITERAL mods as min filters.

Run:  python app.py     (opens http://localhost:8770 in your browser)
Zero dependencies (stdlib only).
"""

import json
import logging
import os
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pob
import stats
import trade

PORT = int(os.environ.get("POE_PORT") or 8770)
HERE = os.path.dirname(os.path.abspath(__file__))

# ---- Logging: logs/app.log (everything) + logs/errors.log (errors only) ----
LOG_DIR = os.path.join(HERE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
log = logging.getLogger("poe")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
if not log.handlers:
    _fh = logging.FileHandler(os.path.join(LOG_DIR, "app.log"), encoding="utf-8")
    _fh.setFormatter(_fmt)
    log.addHandler(_fh)
    _eh = logging.FileHandler(os.path.join(LOG_DIR, "errors.log"), encoding="utf-8")
    _eh.setLevel(logging.ERROR)
    _eh.setFormatter(_fmt)
    log.addHandler(_eh)
    _ch = logging.StreamHandler()
    _ch.setFormatter(_fmt)
    log.addHandler(_ch)


def _load_config():
    p = os.path.join(HERE, "config.json")
    cfg = {"league": "Standard", "poesessid": ""}
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


# ---- Saved builds: a plain text file, one "name<TAB>url" per line ------------
BUILDS_FILE = os.environ.get("POE_BUILDS_FILE") or os.path.join(
    HERE, "saved_builds.txt")


def _default_build_name(url):
    u = url.strip().rstrip("/")
    low = u.lower()
    if "maxroll.gg" in low and "/build-guides/" in low:
        slug = u.split("/build-guides/", 1)[1].split("/")[0].split("?")[0]
        return slug.replace("-", " ").title() or "maxroll build"
    seg = u.split("/")[-1].split("?")[0]
    if "pobb.in" in low:
        return f"pobb.in {seg}"
    if "pastebin.com" in low:
        return f"pastebin {seg}"
    if low.startswith("http"):
        return seg or u
    return "Build " + (seg[:8] if seg else "?")  # raw code


def _read_builds():
    builds = []
    if os.path.exists(BUILDS_FILE):
        try:
            with open(BUILDS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if not line.strip():
                        continue
                    name, url = (line.split("\t", 1) if "\t" in line
                                 else ("", line))
                    url = url.strip()
                    if url:
                        builds.append({"name": name.strip()
                                       or _default_build_name(url), "url": url})
        except Exception:
            log.error("read builds failed\n%s", traceback.format_exc())
    return builds


def _write_builds(builds):
    with open(BUILDS_FILE, "w", encoding="utf-8") as f:
        for b in builds:
            f.write(f"{b['name']}\t{b['url']}\n")


def _save_build(url, name=None):
    """Add the url if new (default name), or set its name if provided."""
    url = (url or "").strip()
    if not url:
        return _read_builds()
    builds = _read_builds()
    for b in builds:
        if b["url"] == url:
            if name:
                b["name"] = name.strip() or b["name"]
            _write_builds(builds)
            return builds
    builds.append({"name": (name or "").strip() or _default_build_name(url),
                   "url": url})
    _write_builds(builds)
    return builds


def _delete_build(url):
    builds = [b for b in _read_builds() if b["url"] != (url or "").strip()]
    _write_builds(builds)
    return builds


# ---- Auto-shutdown when the browser tab closes ------------------------------
# The page holds an SSE connection (/api/keepalive) open the whole time it's on
# screen. Closing the tab drops that connection; if none reconnects within the
# grace window the server quits itself (there's no console to Ctrl+C when it's
# launched hidden). A reload reconnects well within the grace, so it survives.
_ka_lock = threading.Lock()
_active_keepalives = 0
_client_seen = False
_last_disconnect = 0.0
_start_time = time.time()
SHUTDOWN_GRACE = 5.0    # seconds after the last tab closes before quitting
STARTUP_GRACE = 180.0   # quit if a browser never connects at all


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def _handle_keepalive(self):
        global _active_keepalives, _client_seen, _last_disconnect
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
        except Exception:
            return
        with _ka_lock:
            _active_keepalives += 1
            _client_seen = True
        try:
            while True:
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                time.sleep(2)
        except Exception:
            pass  # tab closed -> socket write fails
        finally:
            with _ka_lock:
                _active_keepalives -= 1
                _last_disconnect = time.time()

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj), "application/json")

    def do_GET(self):
        path = self.path.split("?", 1)[0]  # ignore query string
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/api/keepalive":
            self._handle_keepalive()
        elif path == "/api/builds":
            self._json(200, {"builds": _read_builds()})
        elif path == "/api/leagues":
            cfg = _load_config()
            self._json(200, {"leagues": trade.get_leagues(),
                             "current": cfg.get("league", "Standard")})
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw)
        except Exception:
            return self._json(400, {"error": "bad json"})

        if self.path == "/api/load":
            self._handle_load(payload)
        elif self.path == "/api/search":
            self._handle_search(payload)
        elif self.path == "/api/builds/save":
            self._json(200, {"builds": _save_build(payload.get("url"),
                                                   payload.get("name"))})
        elif self.path == "/api/builds/rename":
            self._json(200, {"builds": _save_build(payload.get("url"),
                                                   payload.get("name"))})
        elif self.path == "/api/builds/delete":
            self._json(200, {"builds": _delete_build(payload.get("url"))})
        else:
            self._json(404, {"error": "not found"})

    def _handle_load(self, payload):
        link = (payload.get("link") or "").strip()
        log.info("LOAD requested: %s", link[:120])
        t0 = time.time()
        try:
            build = pob.parse_build(link)
        except Exception as e:
            log.error("LOAD failed for %s\n%s", link[:120], traceback.format_exc())
            return self._json(400, {"error": f"Could not read PoB: {e}"})
        log.info("LOAD parsed in %.2fs (%d item sets)",
                 time.time() - t0, len(build.get("item_sets", [])))

        # Annotate every mod with its matched stat id / value for preview.
        try:
            idx = stats.get_index()
        except Exception:
            log.error("stat index build failed\n%s", traceback.format_exc())
            return self._json(500, {"error": "Failed to load trade stat data. "
                                    "See logs/errors.log (check your connection)."})
        for iset in build["item_sets"]:
            for slot in iset["slots"]:
                enriched = []
                for line in slot["mods"]:
                    m = idx.match(line)
                    enriched.append({
                        "line": line,
                        "matched": bool(m),
                        "value": (m["value"] if m else None),
                        "id": (m["id"] if m else None),
                    })
                slot["mods"] = enriched
        log.info("LOAD done in %.2fs total", time.time() - t0)
        self._json(200, build)

    def _handle_search(self, payload):
        item = payload.get("item") or {}
        # Each entry: {line, min}. min may be None -> stat added with empty box.
        chosen = payload.get("mods") or []
        league = (payload.get("league") or _load_config()["league"]).strip()
        use_name = bool(payload.get("use_name"))
        log.info("SEARCH: %s [%s] %d mods, league=%s",
                 item.get("base"), item.get("rarity"), len(chosen), league)

        idx = stats.get_index()
        specs, skipped, disp = [], [], []
        for entry in chosen:
            if isinstance(entry, dict):
                line, minv = entry.get("line", ""), entry.get("min")
            else:
                line, minv = entry, None
            m = idx.match(line)
            if not m:
                skipped.append(line)
                continue
            specs.append({"id": m["id"], "min": minv})
            disp.append({"text": line, "id": m["id"], "value": minv})

        query = trade.build_query(item, specs, use_type=True,
                                  use_name=use_name)
        cfg = _load_config()
        url, err, note = trade.create_search_smart(query, league,
                                                   cfg.get("poesessid", ""))
        if err:
            log.error("SEARCH create failed: %s | query=%s", err,
                      json.dumps(query))
        else:
            log.info("SEARCH ok -> %s%s", url, f" ({note})" if note else "")
        self._json(200, {
            "url": url,
            "error": err,
            "note": note,
            "query": query,
            "matched": disp,
            "skipped": skipped,
        })


def _prewarm():
    """Fetch/cache the trade stat dictionary + leagues at startup so the first
    build Load is instant instead of downloading ~2 MB mid-click."""
    try:
        t = time.time()
        log.info("Prewarming trade stat data (first run downloads ~2 MB)...")
        stats.get_index()
        trade.get_leagues()
        log.info("Prewarm complete in %.2fs", time.time() - t)
    except Exception:
        log.error("Prewarm failed (will retry on first Load)\n%s",
                  traceback.format_exc())


class Server(ThreadingHTTPServer):
    # Windows' SO_REUSEADDR lets two instances silently bind the same port,
    # so requests hit an arbitrary one. Disable it: a second launch fails
    # loudly instead of shadowing the running server.
    allow_reuse_address = False
    daemon_threads = True


def _watchdog(server):
    """Quit the process when the browser is gone (no console to Ctrl+C)."""
    while True:
        time.sleep(1.0)
        with _ka_lock:
            active, seen, last = _active_keepalives, _client_seen, _last_disconnect
        now = time.time()
        if not seen and now - _start_time > STARTUP_GRACE:
            log.info("No browser connected in %.0fs - shutting down.",
                     STARTUP_GRACE)
            server.shutdown()
            return
        if seen and active == 0 and now - last > SHUTDOWN_GRACE:
            log.info("Browser closed - shutting down server.")
            server.shutdown()
            return


def main():
    try:
        server = Server(("127.0.0.1", PORT), Handler)
    except OSError as e:
        msg = (f"Port {PORT} is already in use - PoE Trade Helper looks like "
               f"it's already running. Open http://localhost:{PORT} in your "
               f"browser, or close the other window first.")
        print(msg)
        log.error("%s (%s)", msg, e)
        return
    url = f"http://localhost:{PORT}"
    log.info("PoE Trade Helper starting on %s", url)
    print(f"PoE Trade Helper running at {url}")
    print(f"Logs: {os.path.join(LOG_DIR, 'app.log')}  |  Errors: "
          f"{os.path.join(LOG_DIR, 'errors.log')}")
    print("Close the browser tab to stop it (or Ctrl+C here).")
    # Warm caches in the background so the UI opens immediately.
    threading.Thread(target=_prewarm, daemon=True).start()
    threading.Thread(target=_watchdog, args=(server,), daemon=True).start()
    if not os.environ.get("POE_NO_BROWSER"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()
    log.info("Server stopped.")
    server.server_close()


if __name__ == "__main__":
    main()
