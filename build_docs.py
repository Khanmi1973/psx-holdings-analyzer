# -*- coding: utf-8 -*-
"""
Build the static site into docs/ for GitHub Pages.

The data is split so the page loads fast and so every visitor can keep their
own watchlist without downloading the whole market:

  docs/data/index.js         small: the full listed universe, headline figures
                             for every symbol, sector medians, ratio data
  docs/data/stocks/<SYM>.json  one file per covered stock, fetched on demand

    python build_docs.py
"""

import json, os, shutil, time

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "docs")
DATA = os.path.join(HERE, "data")


def main():
    src = os.path.join(DATA, "psx_data.json")
    if not os.path.exists(src):
        raise SystemExit("data/psx_data.json is missing - run  python psx_scraper.py  first.")
    with open(src, encoding="utf-8") as f:
        ds = json.load(f)

    stocks_dir = os.path.join(DOCS, "data", "stocks")
    os.makedirs(stocks_dir, exist_ok=True)
    shutil.copyfile(os.path.join(HERE, "index.html"), os.path.join(DOCS, "index.html"))
    open(os.path.join(DOCS, ".nojekyll"), "w").close()

    # one file per covered stock
    written = set()
    for sym, rec in (ds.get("stocks") or {}).items():
        with open(os.path.join(stocks_dir, sym + ".json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        written.add(sym)

    # drop files for stocks no longer covered
    removed = 0
    for fn in os.listdir(stocks_dir):
        if fn.endswith(".json") and fn[:-5] not in written:
            os.remove(os.path.join(stocks_dir, fn))
            removed += 1

    index = {
        "generatedAt": ds.get("generatedAt"),
        "source": ds.get("source"),
        "covered": sorted(written),
        # what a first-time visitor sees before they make the list their own
        "starter": [s for s in (ds.get("watchlist") or []) if s in written]
                   or sorted(written)[:15],
        "market": ds.get("market") or {},
        "sectorStats": ds.get("sectorStats") or {},
        "universe": ds.get("universe") or [],
        "manual": ds.get("manual") or {},
        "errors": ds.get("errors") or {},
    }
    ipath = os.path.join(DOCS, "data", "index.js")
    with open(ipath, "w", encoding="utf-8") as f:
        f.write("window.PSX_INDEX = ")
        json.dump(index, f, ensure_ascii=False)
        f.write(";")

    # Older builds shipped one big psx_data.js; remove it so nothing stale loads.
    legacy = os.path.join(DOCS, "data", "psx_data.js")
    if os.path.exists(legacy):
        os.remove(legacy)

    kb = lambda p: os.path.getsize(p) / 1024.0
    total = sum(kb(os.path.join(stocks_dir, f)) for f in os.listdir(stocks_dir))
    print("docs/ built at %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("  covered stocks     : %d  (%.0f KB total, loaded on demand)" % (len(written), total))
    print("  searchable symbols : %d" % len(index["market"]))
    print("  index payload      : %.0f KB  (this is all the page loads up front)" % kb(ipath))
    if removed:
        print("  removed stale files: %d" % removed)


if __name__ == "__main__":
    main()
