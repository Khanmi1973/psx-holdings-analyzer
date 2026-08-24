# -*- coding: utf-8 -*-
"""
Add or remove watchlist symbols from the command line / CI.

Used by the GitHub Actions workflow so the hosted dashboard can add and remove
holdings without a server. Symbols arrive as untrusted input, so they are
validated hard: shape-checked, then confirmed against the live PSX symbol list.

    python manage_watchlist.py --add "LUCK,SYS" --remove "DCR"
"""

import argparse, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHLIST = os.path.join(HERE, "watchlist.json")
MAX_SYMBOLS = 60                       # keeps a runaway dispatch from bloating the run
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,12}$")


def parse_list(s):
    """'luck, sys ;psx' -> ['LUCK','SYS','PSX'] - anything odd is dropped."""
    if not s:
        return []
    out = []
    for raw in re.split(r"[,\s;]+", s.strip().upper()):
        raw = raw.strip()
        if not raw:
            continue
        if not SYMBOL_RE.match(raw):
            print("  ignoring %r - not a valid symbol shape" % raw)
            continue
        if raw not in out:
            out.append(raw)
    return out


def load():
    if os.path.exists(WATCHLIST):
        with open(WATCHLIST, encoding="utf-8") as f:
            return json.load(f)
    return []


def save(lst):
    with open(WATCHLIST, "w", encoding="utf-8") as f:
        json.dump(lst, f, indent=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", default="")
    ap.add_argument("--remove", default="")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip checking symbols against the PSX listing")
    a = ap.parse_args()

    add, remove = parse_list(a.add), parse_list(a.remove)
    if not add and not remove:
        print("Nothing to do.")
        return 0

    wl = load()

    if add and not a.no_verify:
        try:
            import psx_scraper as ps
            listed = {u["symbol"] for u in ps.fetch_universe()}
            unknown = [s for s in add if s not in listed]
            if unknown:
                print("  not listed on PSX, skipping: %s" % ", ".join(unknown))
                add = [s for s in add if s in listed]
        except Exception as e:
            print("  could not verify against PSX (%s) - adding unverified" % e)

    for s in add:
        if s not in wl:
            wl.append(s)
            print("  + %s" % s)
        else:
            print("  = %s already tracked" % s)

    for s in remove:
        if s in wl:
            wl.remove(s)
            print("  - %s" % s)

    if not wl:
        print("Refusing to empty the watchlist.")
        return 1
    if len(wl) > MAX_SYMBOLS:
        print("Watchlist would exceed %d symbols; trimming the additions." % MAX_SYMBOLS)
        wl = wl[:MAX_SYMBOLS]

    save(wl)
    print("Watchlist now has %d symbols." % len(wl))

    # Tell the workflow which symbols are new, so it can fetch just their ratios.
    new = [s for s in add if s in wl]
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            f.write("new_symbols=%s\n" % " ".join(new))
    return 0


if __name__ == "__main__":
    sys.exit(main())
