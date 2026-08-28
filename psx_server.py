# -*- coding: utf-8 -*-
"""
PSX Holdings Analyzer - local server

Serves the dashboard and gives it live scraping endpoints:

  GET  /                  dashboard
  GET  /api/data          current dataset (cached on disk)
  POST /api/refresh       re-scrape every watchlist symbol
  POST /api/add?symbol=X  scrape X, add to watchlist
  POST /api/remove?symbol=X

Run:  python psx_server.py       ->  http://127.0.0.1:8777
"""

import json, os, sys, threading, webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import psx_scraper as ps

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PSX_PORT", "8777"))
LOCK = threading.Lock()
_cache = {"ds": None}


MANUAL_PATH = os.path.join(ps.DATA_DIR, "manual.json")


def load_manual():
    """Balance-sheet figures the user enters by hand (PSX does not publish them)."""
    if os.path.exists(MANUAL_PATH):
        try:
            with open(MANUAL_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_manual(d):
    os.makedirs(ps.DATA_DIR, exist_ok=True)
    with open(MANUAL_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    return d


def dataset():
    """Load from disk once, scrape if we have never run before."""
    if _cache["ds"] is None:
        p = os.path.join(ps.DATA_DIR, "psx_data.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                _cache["ds"] = json.load(f)
        else:
            _cache["ds"] = refresh(ps.load_watchlist())
    ds = _cache["ds"]
    ds["manual"] = load_manual()          # always served fresh
    return ds


def refresh(symbols):
    ds = ps.build_dataset(symbols, progress=lambda m: print("  " + m, flush=True))
    ps.save(ds)
    ps.save_watchlist(symbols)
    _cache["ds"] = ds
    return ds


def add_symbol(sym):
    """Scrape one symbol and merge it into the existing dataset."""
    sym = sym.strip().upper()
    ds = dataset()
    if sym in ds["stocks"]:
        return ds
    universe = ds.get("universe") or ps.fetch_universe()
    uidx = {u["symbol"]: u for u in universe}
    if sym not in uidx:
        raise ValueError("%s is not a symbol listed on PSX" % sym)

    screener = ps.fetch_screener()
    rec = ps.build_symbol(sym, screener, uidx)
    rec["sectorStats"] = (ds.get("sectorStats") or {}).get(rec["sector"], {})

    ds["stocks"][sym] = rec
    if sym not in ds["symbols"]:
        ds["symbols"].append(sym)
    ds["errors"].pop(sym, None)
    ps.save(ds)
    ps.save_watchlist(ds["symbols"])
    _cache["ds"] = ds
    return ds


def remove_symbol(sym):
    sym = sym.strip().upper()
    ds = dataset()
    ds["stocks"].pop(sym, None)
    ds["symbols"] = [s for s in ds["symbols"] if s != sym]
    ps.save(ds)
    ps.save_watchlist(ds["symbols"])
    _cache["ds"] = ds
    return ds


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            sys.stderr.write("  %s\n" % (fmt % args))

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        return True          # routes test this; without it every reply got a 404 chaser

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _route(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        sym = (q.get("symbol") or [""])[0]
        try:
            if u.path == "/api/data":
                return self._json(dataset())
            if u.path == "/api/index":
                ds = dataset()
                return self._json({
                    "generatedAt": ds.get("generatedAt"),
                    "source": ds.get("source"),
                    "covered": sorted(ds.get("stocks") or {}),
                    "starter": [s for s in ps.load_watchlist()
                                if s in (ds.get("stocks") or {})],
                    "market": ds.get("market") or {},
                    "sectorStats": ds.get("sectorStats") or {},
                    "universe": ds.get("universe") or [],
                    "manual": load_manual(),
                    "errors": ds.get("errors") or {},
                })
            if u.path.startswith("/api/stock/"):
                sym = u.path.rsplit("/", 1)[-1].upper()
                rec = (dataset().get("stocks") or {}).get(sym)
                return self._json(rec) if rec else self._json(
                    {"error": "%s has not been collected" % sym}, 404)
            if u.path == "/api/ratios":
                with LOCK:
                    import psx_ratios as pr
                    syms = [sym.upper()] if sym else dataset()["symbols"]
                    print("\n>> Fetching balance-sheet ratios for %d symbol(s)..." % len(syms))
                    ok, failed = pr.harvest(
                        syms, progress=lambda m: print("  " + m, flush=True))
                    return self._json({"ok": ok, "failed": failed,
                                       "manual": load_manual()})
            if u.path == "/api/manual":
                if self.command == "POST":
                    with LOCK:
                        cur = load_manual()
                        payload = self._body()
                        # {"SYM": {field: value, ...}} - null value clears a field
                        for s, fields in (payload or {}).items():
                            s = s.upper()
                            rec = cur.get(s, {})
                            for k, v in (fields or {}).items():
                                if v is None or v == "":
                                    rec.pop(k, None)
                                else:
                                    rec[k] = v
                            if rec:
                                cur[s] = rec
                            else:
                                cur.pop(s, None)
                        return self._json(save_manual(cur))
                return self._json(load_manual())
            if u.path == "/api/refresh":
                with LOCK:
                    print("\n>> Refreshing all holdings...")
                    return self._json(refresh(dataset()["symbols"]))
            if u.path == "/api/add":
                with LOCK:
                    print("\n>> Adding %s..." % sym)
                    return self._json(add_symbol(sym))
            if u.path == "/api/remove":
                with LOCK:
                    return self._json(remove_symbol(sym))
        except Exception as e:
            return self._json({"error": "%s: %s" % (type(e).__name__, e)}, 400)
        return None

    def do_GET(self):
        if self.path.startswith("/api/"):
            if self._route() is None:
                self._json({"error": "unknown endpoint"}, 404)
            return
        if self.path in ("/", ""):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            if self._route() is None:
                self._json({"error": "unknown endpoint"}, 404)
            return
        self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    n = len(ps.load_watchlist())
    print("=" * 62)
    print(" PSX Holdings Analyzer")
    print(" watchlist: %d symbols" % n)
    print(" open:      http://127.0.0.1:%d" % PORT)
    print(" stop:      Ctrl+C")
    print("=" * 62)
    if not os.path.exists(os.path.join(ps.DATA_DIR, "psx_data.json")):
        print("\nNo cached data - scraping PSX for the first time...")
        refresh(ps.load_watchlist())
        print("done.\n")
    threading.Timer(0.8, lambda: webbrowser.open("http://127.0.0.1:%d" % PORT)).start()
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
