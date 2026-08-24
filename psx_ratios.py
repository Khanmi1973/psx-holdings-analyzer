# -*- coding: utf-8 -*-
"""
Balance-sheet ratio harvester
-----------------------------
PSX publishes no balance-sheet data, so the debt / ROE / cash-flow half of the
analysis has to come from elsewhere. This fetches it from sarmaaya.pk, whose
robots.txt allows all crawlers (`User-Agent: * / Allow: /`, no AI-agent
restrictions) and whose pages carry `robots: index, follow`.

The page renders its ratio table client-side, so there is no plain-HTTP route to
the numbers. Rather than pull in a heavyweight automation library, this drives
the Chrome (or Edge) already installed on the machine in headless mode and reads
the rendered DOM:

    chrome --headless=new --dump-dom <url>

No pip install, no npm install, no driver download.

Usage
    python psx_ratios.py               # every symbol in watchlist.json
    python psx_ratios.py EFERT MEBL    # just these

Results are merged into data/manual.json, which the dashboard already reads.
Hand-entered values are never overwritten unless you pass --overwrite.
"""

import json, os, re, html, subprocess, sys, time, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
MANUAL_PATH = os.path.join(DATA_DIR, "manual.json")
SOURCE_URL = "https://sarmaaya.pk/stocks/%s"
PAUSE_SECONDS = 2.0          # be a polite guest


# --------------------------------------------------------------------------
# find a Chromium-family browser already on this machine
# --------------------------------------------------------------------------
def find_browser():
    env = os.environ
    candidates = [
        env.get("PSX_BROWSER"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(env.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Chromium\Application\chrome.exe",
        "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    return None


def render(url, browser, budget_ms=25000, timeout=180):
    """Render a JS page and return the resulting DOM."""
    profile = tempfile.mkdtemp(prefix="psxdom_")
    cmd = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--no-first-run",
        "--disable-extensions",
        "--disable-background-networking",
        "--user-data-dir=" + profile,        # never touch the user's real profile
        "--virtual-time-budget=%d" % budget_ms,
        "--run-all-compositor-stages-before-draw",
        "--dump-dom",
        url,
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.stdout.decode("utf-8", errors="ignore")
    except subprocess.TimeoutExpired:
        return ""
    finally:
        shutil.rmtree(profile, ignore_errors=True)


# --------------------------------------------------------------------------
# parse the rendered ratio table
# --------------------------------------------------------------------------
def _txt(x):
    return re.sub(r"\s+", " ",
                  html.unescape(re.sub(r"<[^>]+>", " ", x)).replace("\xa0", " ")).strip()


def _num(s):
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "--", "N/A", "n/a", "NaN"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def parse_ratio_table(dom):
    """
    The table is laid out:
        Ratios | Vs Industry | Vs History | TTM | <FY> | <FY-1> | ...
    Columns 1 and 2 are sparklines whose SVG labels leak into the text, so only
    the TTM column (index 3) and the latest full year (index 4) are trusted.
    Returns {label: {"ttm": float|None, "fy": float|None}}, plus the FY heading.
    """
    # Several tables on the page can mention a ratio name (peer comparisons,
    # summary strips). Score each candidate by how many known ratio labels it
    # carries and take the richest, rather than whichever happened to be last.
    MARKERS = ("Interest Coverage", "Debt to Equity", "Current Ratio",
               "Book Value Per Share", "Return on Average Total Equity",
               "Quick Ratio", "Free Cash Flow Per Share", "Dividend Payout Ratio")
    best, best_hits = None, 0
    for t in re.findall(r"<table.*?</table>", dom, flags=re.S):
        hits = sum(1 for m in MARKERS if m in t)
        if hits > best_hits:
            best, best_hits = t, hits
    table = best if best_hits >= 2 else None
    if not table:
        return {}, None

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, flags=re.S)
    fy_label, out = None, {}
    for r in rows:
        cells = [_txt(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.S)]
        if len(cells) < 5:
            continue
        if cells[0].lower().startswith("ratio"):
            fy_label = cells[4] if len(cells) > 4 else None
            continue
        out[cells[0]] = {"ttm": _num(cells[3]), "fy": _num(cells[4])}
    return out, fy_label


# Which sarmaaya row feeds which field in the dashboard.
# (field, row label, transform)
FIELD_MAP = [
    ("roe",     "Return on Average Total Equity (%)", None),
    ("roe",     "Return on Common Equity (%)",        None),      # fallback
    ("de",      "Debt to Equity (%)",                 lambda v: v / 100.0),
    ("current", "Current Ratio (x)",                  None),
    ("quick",   "Quick Ratio (x)",                    None),
    ("icover",  "Interest Coverage (x)",              None),
    ("bvps",    "Book Value Per Share",               None),
    ("fcfps",   "Free Cash Flow Per Share",           None),
]
# Extra context worth keeping alongside, for cross-checking the PSX-derived numbers
EXTRA_MAP = [
    ("sarPayout",  "Dividend Payout Ratio"),
    ("sarDivYield", "Dividend Yield (%)"),
    ("sarEps",     "Basic EPS"),
    ("sarPe",      "Price to Earnings"),
    ("sarPb",      "Price to Book Value"),
    ("sarNetMargin", "Net Income Margin (%)"),
    ("sarEvEbitda", "Enterprise Value to EBITDA (x)"),
]


def extract(dom):
    table, fy_label = parse_ratio_table(dom)
    if not table:
        return None
    rec, used = {}, {}

    def pick(label):
        row = table.get(label)
        if not row:
            return None, None
        if row["ttm"] is not None:
            return row["ttm"], "TTM"
        if row["fy"] is not None:
            return row["fy"], (fy_label or "FY")
        return None, None

    for field, label, tf in FIELD_MAP:
        if field in rec:                      # first match wins (fallbacks come later)
            continue
        v, basis = pick(label)
        if v is None:
            continue
        if tf:
            v = tf(v)
        rec[field] = round(v, 4)
        used[field] = "%s (%s)" % (label, basis)

    for field, label in EXTRA_MAP:
        v, basis = pick(label)
        if v is not None:
            rec[field] = round(v, 4)

    if not rec:
        return None
    rec["_src"] = "sarmaaya.pk"
    rec["_asof"] = time.strftime("%Y-%m-%d")
    rec["_fields"] = used
    return rec


def fetch_symbol(sym, browser, attempts=2):
    """Heavy pages occasionally exceed the render budget, so give them a second go."""
    dom = ""
    for i in range(attempts):
        dom = render(SOURCE_URL % sym, browser, budget_ms=25000 + i * 15000)
        if dom and ("Interest Coverage" in dom or "Debt to Equity" in dom):
            break
        if i + 1 < attempts:
            time.sleep(3)
    if not dom:
        return None, "render failed or timed out"
    if "Interest Coverage" not in dom and "Debt to Equity" not in dom:
        if "Page Not Found" in dom or "doesn't exist" in dom:
            return None, "no page for this symbol on the source site"
        return None, "ratio table not present in rendered page"
    rec = extract(dom)
    return (rec, None) if rec else (None, "could not parse the ratio table")


# --------------------------------------------------------------------------
# merge into manual.json
# --------------------------------------------------------------------------
def load_manual():
    if os.path.exists(MANUAL_PATH):
        try:
            with open(MANUAL_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_manual(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(MANUAL_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def merge(cur, sym, rec, overwrite):
    """Hand-entered values win unless --overwrite: you may have better numbers."""
    old = cur.get(sym, {})
    hand_entered = old and old.get("_src") not in ("sarmaaya.pk",)
    if hand_entered and not overwrite:
        merged = dict(rec)
        for k, v in old.items():
            if not k.startswith("_"):
                merged[k] = v            # keep what the user typed
        merged["_note"] = "auto-fetched; your hand-entered values kept"
        cur[sym] = merged
    else:
        cur[sym] = rec
    return cur


def harvest(symbols, overwrite=False, progress=print):
    browser = find_browser()
    if not browser:
        raise RuntimeError(
            "No Chrome/Edge/Chromium found. Install Chrome, or set PSX_BROWSER "
            "to the full path of a Chromium-based browser executable.")
    progress("Using browser: %s" % browser)
    progress("Source: sarmaaya.pk (robots.txt allows all crawlers)")

    cur = load_manual()
    ok, failed = [], {}
    for i, sym in enumerate(symbols, 1):
        rec, err = fetch_symbol(sym, browser)
        if rec:
            merge(cur, sym, rec, overwrite)
            bits = [k for k in ("roe", "de", "current", "icover", "bvps", "fcfps") if k in rec]
            progress("[%d/%d] %-9s OK   %s" % (i, len(symbols), sym, ", ".join(bits)))
            ok.append(sym)
        else:
            progress("[%d/%d] %-9s FAIL %s" % (i, len(symbols), sym, err))
            failed[sym] = err
        save_manual(cur)                       # checkpoint after each symbol
        if i < len(symbols):
            time.sleep(PAUSE_SECONDS)
    return ok, failed


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    overwrite = "--overwrite" in args
    syms = [a.upper() for a in args if not a.startswith("--")]
    if not syms:
        wl = os.path.join(HERE, "watchlist.json")
        syms = json.load(open(wl, encoding="utf-8")) if os.path.exists(wl) else []
    if not syms:
        print("Nothing to do - watchlist.json is empty.")
        sys.exit(1)

    print("Fetching balance-sheet ratios for %d symbol(s).\n"
          "Each one renders a page in headless Chrome, so allow ~10s per symbol.\n"
          % len(syms))
    ok, failed = harvest(syms, overwrite=overwrite)
    print("\nDone. %d succeeded, %d failed -> data/manual.json" % (len(ok), len(failed)))
    for s, e in failed.items():
        print("  ! %s: %s" % (s, e))
    print("\nReload the dashboard to see the balance-sheet pillar light up.")
