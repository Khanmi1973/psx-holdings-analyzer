# -*- coding: utf-8 -*-
"""
Build the static site into docs/ for GitHub Pages.

The dashboard already runs read-only from a baked-in dataset, so publishing is
just: copy index.html, copy the generated data file, add .nojekyll.

    python build_docs.py
"""

import json, os, shutil, time

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "docs")
DATA = os.path.join(HERE, "data")


def main():
    src_js = os.path.join(DATA, "psx_data.js")
    if not os.path.exists(src_js):
        raise SystemExit("data/psx_data.js is missing - run  python psx_scraper.py  first.")

    os.makedirs(os.path.join(DOCS, "data"), exist_ok=True)
    shutil.copyfile(os.path.join(HERE, "index.html"), os.path.join(DOCS, "index.html"))
    shutil.copyfile(src_js, os.path.join(DOCS, "data", "psx_data.js"))

    # Stop GitHub Pages running the files through Jekyll.
    open(os.path.join(DOCS, ".nojekyll"), "w").close()

    with open(os.path.join(DATA, "psx_data.json"), encoding="utf-8") as f:
        ds = json.load(f)
    n = len(ds.get("stocks", {}))
    m = len(ds.get("manual", {}))
    size = os.path.getsize(os.path.join(DOCS, "data", "psx_data.js")) / 1024.0
    print("docs/ built at %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("  holdings          : %d" % n)
    print("  with balance sheet: %d" % m)
    print("  data payload      : %.0f KB" % size)


if __name__ == "__main__":
    main()
