# -*- coding: utf-8 -*-
"""
PSX Fundamental & Technical Scraper
-----------------------------------
Pulls data from the Pakistan Stock Exchange Data Portal (dps.psx.com.pk)
and computes a professional metric set for each symbol.

Stdlib only - no pip install required.

Sources
  /symbols                  -> full listed universe (symbol, name, sector)
  /screener                 -> market-wide table: mcap, price, P/E, DIV YIELD, 1Y chg, volume
  /company/<SYM>            -> profile, equity, 4y annual financials, quarterly, ratios
  /timeseries/eod/<SYM>     -> end-of-day price history (for technicals)
"""

import json, re, html, math, time, gzip, io, os, sys
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

BASE = "https://dps.psx.com.pk"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def http_get(url, timeout=30, retries=3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/json,*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw.decode("utf-8", errors="ignore")
        except Exception as e:
            last = e
            time.sleep(1.2 * (attempt + 1))
    raise last


# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------
def strip_tags(s):
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).replace("\xa0", " ").strip()


def num(s):
    """'1,234.50' -> 1234.5 ; '(21.31)' -> -21.31 ; '-' / '' -> None"""
    if s is None:
        return None
    s = str(s).strip()
    if s in ("", "-", "--", "N/A", "n/a"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace("%", "").replace("Rs.", "").strip()
    mult = 1.0
    if s.endswith("B"):
        mult, s = 1e9, s[:-1]
    elif s.endswith("M"):
        mult, s = 1e6, s[:-1]
    elif s.endswith("K"):
        mult, s = 1e3, s[:-1]
    try:
        v = float(s) * mult
    except ValueError:
        return None
    return -v if neg else v


def stats_map(page):
    """Every <div class=stats_label>X</div><div class=stats_value>Y</div> pair."""
    out = {}
    pat = r'<div class="stats_label">(.*?)</div>\s*<div class="stats_value[^"]*">(.*?)</div>'
    for lbl, val in re.findall(pat, page, flags=re.S):
        k = strip_tags(lbl).replace("*", "").strip()
        v = strip_tags(re.sub(r'<div class="numRange".*', "", val, flags=re.S))
        if not k:
            continue
        # PSX repeats some labels (e.g. "Free Float" appears twice: shares, then %)
        if k in out:
            i = 2
            while "%s #%d" % (k, i) in out:
                i += 1
            k = "%s #%d" % (k, i)
        out[k] = v
    return out


def parse_table_block(page, anchor_id):
    """Parse the labelled row-tables inside a #section (Financials / Ratios)."""
    m = re.search(r'id="%s".*?(?=<div class="section |<footer|$)' % anchor_id,
                  page, flags=re.S)
    if not m:
        return {}
    blk = m.group(0)
    panels = re.findall(
        r'<div class="tabs__panel" data-name="([^"]+)">(.*?)(?=<div class="tabs__panel"|$)',
        blk, flags=re.S)
    if not panels:
        panels = [("Main", blk)]
    result = {}
    for pname, pbody in panels:
        tm = re.search(r"<table.*?</table>", pbody, flags=re.S)
        if not tm:
            continue
        tbl = tm.group(0)
        periods = [strip_tags(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", tbl, flags=re.S)]
        periods = [p for p in periods if p]
        rows = {}
        for rowhtml in re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, flags=re.S):
            tds = re.findall(r"<td[^>]*>(.*?)</td>", rowhtml, flags=re.S)
            if len(tds) < 2:
                continue
            label = strip_tags(tds[0])
            vals = [num(strip_tags(t)) for t in tds[1:]]
            if label:
                rows[label] = vals
        if rows:
            result[pname] = {"periods": periods, "rows": rows}
    return result


def first_group(pattern, page, flags=re.S):
    m = re.search(pattern, page, flags=flags)
    return strip_tags(m.group(1)) if m else ""


# --------------------------------------------------------------------------
# market-wide sources
# --------------------------------------------------------------------------
def fetch_universe():
    data = json.loads(http_get(BASE + "/symbols"))
    return [{"symbol": d["symbol"], "name": d["name"], "sector": d.get("sectorName", ""),
             "isDebt": bool(d.get("isDebt")), "isETF": bool(d.get("isETF"))}
            for d in data]


def _cell(td_html):
    """Prefer the exact data-order attribute over the rounded display text."""
    m = re.match(r'<td[^>]*\sdata-order="([^"]*)"', td_html)
    if m:
        return m.group(1)
    return strip_tags(td_html)


def fetch_screener():
    """Market-wide table -> {SYMBOL: {...}}  (only place PSX exposes dividend yield)"""
    page = http_get(BASE + "/screener")
    m = re.search(r"<table.*?</table>", page, flags=re.S)
    if not m:
        return {}
    out = {}
    for rowhtml in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), flags=re.S):
        tds = re.findall(r"(<td[^>]*>.*?</td>)", rowhtml, flags=re.S)
        if len(tds) < 11:
            continue
        c = [_cell(t) for t in tds]
        # symbol cell can carry an XD/XB tag div -> data-order is the clean symbol
        sym = c[0].strip()
        if not sym or " " in sym:
            continue
        out[sym] = {
            "sectorCode": strip_tags(tds[1]),
            "listedIn": strip_tags(tds[2]),
            "marketCap": num(c[3]),
            "price": num(c[4]),
            "changePct": num(c[5]),
            "change1Y": num(c[6]),
            "pe": num(c[7]),
            "divYield": num(c[8]),
            "freeFloat": num(c[9]),
            "vol30dAvg": num(c[10]),
        }
    return out


# --------------------------------------------------------------------------
# per-company
# --------------------------------------------------------------------------
def fetch_company(sym):
    page = http_get("%s/company/%s" % (BASE, sym))
    st = stats_map(page)

    def g(*keys):
        for k in keys:
            for kk in st:
                if kk.lower().startswith(k.lower()):
                    return st[kk]
        return None

    name = first_group(r'<div class="quote__name">(.*?)(?:<div class="tag|</div>)', page)
    sector = first_group(r'<div class="quote__sector">\s*<span>(.*?)</span>', page)
    price = num(first_group(r'<div class="quote__close">(.*?)</div>', page))

    r52 = re.search(r'52-WEEK RANGE.*?data-low="([\d.]+)" data-high="([\d.]+)"',
                    page, flags=re.S)
    desc = first_group(r'BUSINESS DESCRIPTION</div>\s*<p>(.*?)</p>', page)
    fye = first_group(r'Fiscal Year End</div>\s*<p>(.*?)</p>', page)

    ff_pct, ff_shares = None, None
    for k, v in st.items():
        if k.startswith("Free Float"):
            if "%" in str(v):
                ff_pct = num(v)
            elif ff_shares is None:
                ff_shares = num(v)

    fin = parse_table_block(page, "financials")
    rat = parse_table_block(page, "ratios")

    shares = num(g("Shares"))
    if ff_pct is None and ff_shares and shares:
        ff_pct = ff_shares / shares * 100.0

    return {
        "symbol": sym,
        "name": name,
        "sector": sector,
        "description": desc,
        "fiscalYearEnd": fye,
        "price": price,
        "peTTM": num(g("P/E Ratio")),
        "change1Y": num(g("1-Year Change")),
        "changeYTD": num(g("YTD Change")),
        "volume": num(g("Volume")),
        "wk52Low": float(r52.group(1)) if r52 else None,
        "wk52High": float(r52.group(2)) if r52 else None,
        "marketCap000": num(g("Market Cap")),
        "shares": shares,
        "freeFloatShares": ff_shares,
        "freeFloatPct": ff_pct,
        "annual": fin.get("Annual"),
        "quarterly": fin.get("Quarterly"),
        "ratios": rat.get("Main") or (list(rat.values())[0] if rat else None),
    }


def fetch_eod(sym):
    """[[epoch, close, volume, prevClose], ...] -> chronological list of dicts.

    PSX rate-limits bursts, and an empty series silently zeroes every technical
    metric, so this retries deliberately and reports failure to the caller
    rather than pretending the stock has no price history.
    """
    err = None
    for attempt in range(3):
        try:
            d = json.loads(http_get("%s/timeseries/eod/%s" % (BASE, sym), retries=2))
            rows = sorted(d.get("data") or [], key=lambda r: r[0])
            out = [{"t": int(r[0]), "c": float(r[1]), "v": float(r[2])}
                   for r in rows if r[1]]
            if out:
                return out, None
            err = "empty series"
        except Exception as e:
            err = "%s: %s" % (type(e).__name__, e)
        time.sleep(1.5 * (attempt + 1))
    return [], err


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
def cagr(series):
    """series ordered newest -> oldest (as PSX prints it)."""
    vals = [v for v in series if v is not None]
    if len(vals) < 2:
        return None
    new, old = vals[0], vals[-1]
    n = len(vals) - 1
    if old is None or old <= 0 or new is None or new <= 0:
        return None
    return ((new / old) ** (1.0 / n) - 1.0) * 100.0


def pct_change_from(closes, days):
    if len(closes) < days + 1:
        return None
    a, b = closes[-1 - days], closes[-1]
    if not a:
        return None
    return (b / a - 1.0) * 100.0


def sma(closes, n):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def technicals(eod):
    closes = [p["c"] for p in eod]
    vols = [p["v"] for p in eod]
    out = {"bars": len(closes)}
    if len(closes) < 30:
        return out
    out["ret1M"] = pct_change_from(closes, 21)
    out["ret3M"] = pct_change_from(closes, 63)
    out["ret6M"] = pct_change_from(closes, 126)
    out["ret1Y"] = pct_change_from(closes, 252)
    out["ma50"] = sma(closes, 50)
    out["ma200"] = sma(closes, 200)
    out["last"] = closes[-1]

    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
    win = rets[-252:] if len(rets) >= 60 else rets
    if len(win) > 20:
        mu = sum(win) / len(win)
        var = sum((x - mu) ** 2 for x in win) / (len(win) - 1)
        out["volAnnualPct"] = math.sqrt(var) * math.sqrt(252) * 100.0

    w = closes[-252:] if len(closes) >= 252 else closes
    peak, mdd = w[0], 0.0
    for c in w:
        peak = max(peak, c)
        if peak > 0:
            mdd = min(mdd, (c / peak - 1.0) * 100.0)
    out["maxDrawdown1Y"] = mdd
    out["hi252"] = max(w)
    out["lo252"] = min(w)
    if out["hi252"]:
        out["pctFrom52High"] = (closes[-1] / out["hi252"] - 1.0) * 100.0
    if out["lo252"]:
        out["pctFrom52Low"] = (closes[-1] / out["lo252"] - 1.0) * 100.0
    if out["hi252"] > out["lo252"]:
        out["range52Pos"] = (closes[-1] - out["lo252"]) / (out["hi252"] - out["lo252"]) * 100.0

    v30 = vols[-30:]
    if v30:
        out["vol30dAvg"] = sum(v30) / len(v30)
        out["turnover30d"] = out["vol30dAvg"] * closes[-1]
    return out


def fundamentals(co):
    """Derive the fundamental metric set from the parsed financial tables."""
    f = {}
    ann = co.get("annual") or {}
    rows = ann.get("rows") or {}
    f["years"] = ann.get("periods") or []

    # Top line differs by industry: manufacturers report "Sales",
    # banks report "Mark-up Earned"/"Total Income", REITs "Total Income".
    rev_label = next((k for k in ("Sales", "Total Income", "Mark-up Earned")
                      if rows.get(k)), None)
    f["revenueLabel"] = rev_label or "Revenue"
    f["sales"] = rows.get(rev_label) if rev_label else []
    f["pat"] = rows.get("Profit after Taxation") or []
    f["eps"] = rows.get("EPS") or []

    f["salesCAGR"] = cagr(f["sales"])
    f["patCAGR"] = cagr(f["pat"])
    f["epsCAGR"] = cagr(f["eps"])

    eps = [e for e in f["eps"] if e is not None]
    f["epsLatest"] = eps[0] if eps else None
    f["epsPositiveYears"] = sum(1 for e in eps if e > 0)
    f["epsYears"] = len(eps)
    f["epsUpYears"] = sum(1 for i in range(len(eps) - 1) if eps[i] > eps[i + 1])

    # Detect a share split / bonus issue / restructuring: a step change in EPS
    # that PSX does NOT restate makes multi-year EPS CAGR meaningless.
    # Profit after Taxation is immune to share-count changes, so cross-check.
    f["epsMaxStep"] = None
    if len(eps) > 1:
        steps = []
        for i in range(len(eps) - 1):
            a, b = eps[i], eps[i + 1]
            if a and b and a * b > 0:
                steps.append(max(abs(a / b), abs(b / a)))
        if steps:
            f["epsMaxStep"] = max(steps)
    pat = [p for p in f["pat"] if p is not None]
    pat_step = None
    if len(pat) > 1:
        ps = []
        for i in range(len(pat) - 1):
            a, b = pat[i], pat[i + 1]
            if a and b and a * b > 0:
                ps.append(max(abs(a / b), abs(b / a)))
        pat_step = max(ps) if ps else None
    # EPS jumped hard but profit did not -> share count changed, not the business
    f["shareCountEvent"] = bool(
        f["epsMaxStep"] and f["epsMaxStep"] > 4
        and (pat_step is None or pat_step < f["epsMaxStep"] / 2.5))

    # Growth used for scoring: profit-based when the share count moved
    f["growthCAGR"] = (f["patCAGR"] if f["shareCountEvent"] and f["patCAGR"] is not None
                       else f["epsCAGR"])
    f["growthBasis"] = ("Profit after Taxation (share count changed)"
                        if f["shareCountEvent"] and f["patCAGR"] is not None else "EPS")

    rt = (co.get("ratios") or {}).get("rows") or {}
    f["ratioPeriods"] = (co.get("ratios") or {}).get("periods") or []
    f["gpMargin"] = rt.get("Gross Profit Margin (%)") or []
    f["npMargin"] = rt.get("Net Profit Margin (%)") or []
    f["epsGrowth"] = rt.get("EPS Growth (%)") or []
    f["peg"] = rt.get("PEG") or []

    npm = [x for x in f["npMargin"] if x is not None]
    if npm:
        f["npMarginLatest"] = npm[0]
        f["npMarginAvg"] = sum(npm) / len(npm)
        rest = npm[1:] if len(npm) > 1 else npm
        f["npMarginTrend"] = npm[0] - (sum(rest) / len(rest))
        if len(npm) > 1:
            mu = sum(npm) / len(npm)
            f["npMarginStdev"] = math.sqrt(sum((x - mu) ** 2 for x in npm) / (len(npm) - 1))
    gpm = [x for x in f["gpMargin"] if x is not None]
    if gpm:
        f["gpMarginLatest"] = gpm[0]
        f["gpMarginAvg"] = sum(gpm) / len(gpm)

    q = (co.get("quarterly") or {}).get("rows") or {}
    f["qPeriods"] = (co.get("quarterly") or {}).get("periods") or []
    f["qEps"] = q.get("EPS") or []
    f["qSales"] = q.get("Sales") or []
    f["qPat"] = q.get("Profit after Taxation") or []
    qe = [x for x in f["qEps"] if x is not None]
    if len(qe) >= 2 and qe[1]:
        f["qEpsQoQ"] = (qe[0] - qe[1]) / abs(qe[1]) * 100.0
    if len(qe) >= 4 and qe[3]:
        f["qEpsYoY"] = (qe[0] - qe[3]) / abs(qe[3]) * 100.0
    return f


def valuation(co, scr, fund):
    v = {}
    price = co.get("price") or (scr or {}).get("price")
    pe = co.get("peTTM") or (scr or {}).get("pe")
    dy = (scr or {}).get("divYield")
    v["price"] = price
    v["pe"] = pe if pe and pe > 0 else None
    v["divYield"] = dy
    if v["pe"]:
        v["earningsYieldPct"] = 100.0 / v["pe"]
        if price:
            v["epsTTM"] = price / v["pe"]
    if dy and v.get("epsTTM") and v["epsTTM"] > 0 and price:
        v["dpsTTM"] = dy / 100.0 * price
        v["payoutRatioPct"] = v["dpsTTM"] / v["epsTTM"] * 100.0
    g = fund.get("epsCAGR")
    if v.get("pe") and g and g > 0:
        v["pegCalc"] = v["pe"] / g
    v["marketCap"] = (scr or {}).get("marketCap") or ((co.get("marketCap000") or 0) * 1000 or None)
    return v


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------
def build_symbol(sym, screener, universe_idx):
    co = fetch_company(sym)
    eod, eod_err = fetch_eod(sym)
    scr = screener.get(sym, {})
    u = universe_idx.get(sym, {})
    fund = fundamentals(co)
    tech = technicals(eod)
    val = valuation(co, scr, fund)

    if not co.get("name"):
        co["name"] = u.get("name", sym)
    if not co.get("sector"):
        co["sector"] = u.get("sector", "")

    spark = [round(p["c"], 2) for p in eod[-260:]]
    warnings = []
    if eod_err:
        warnings.append("Price history could not be fetched (%s), so the short-term "
                        "score and all technical metrics are unavailable. Refresh to retry."
                        % eod_err)
    if not (co.get("annual") or {}).get("rows"):
        warnings.append("PSX publishes no annual financials for this symbol, so the "
                        "long-term score rests only on valuation and size.")
    return {
        "symbol": sym,
        "dataWarnings": warnings,
        "name": co["name"],
        "sector": co["sector"] or u.get("sector", ""),
        "description": co.get("description", ""),
        "fiscalYearEnd": co.get("fiscalYearEnd", ""),
        "shares": co.get("shares"),
        "freeFloatPct": co.get("freeFloatPct"),
        "wk52Low": co.get("wk52Low"),
        "wk52High": co.get("wk52High"),
        "change1Y": co.get("change1Y") if co.get("change1Y") is not None else scr.get("change1Y"),
        "changeYTD": co.get("changeYTD"),
        "changePct": scr.get("changePct"),
        "vol30dAvg": scr.get("vol30dAvg") or tech.get("vol30dAvg"),
        "fundamentals": fund,
        "technicals": tech,
        "valuation": val,
        "spark": spark,
        "annualTable": co.get("annual"),
        "quarterlyTable": co.get("quarterly"),
        "ratioTable": co.get("ratios"),
    }


def sector_stats(screener, universe):
    """Median P/E and dividend yield per sector -> peer-relative valuation."""
    by = {}
    sec_of = {u["symbol"]: u["sector"] for u in universe}
    for sym, r in screener.items():
        sec = sec_of.get(sym)
        if not sec:
            continue
        by.setdefault(sec, {"pe": [], "dy": []})
        if r.get("pe") and 0 < r["pe"] < 200:
            by[sec]["pe"].append(r["pe"])
        if r.get("divYield") is not None and 0 <= r["divYield"] < 100:
            by[sec]["dy"].append(r["divYield"])

    def med(a):
        a = sorted(a)
        if not a:
            return None
        n = len(a)
        return a[n // 2] if n % 2 else (a[n // 2 - 1] + a[n // 2]) / 2.0

    return {s: {"medianPE": med(v["pe"]), "medianDY": med(v["dy"]), "count": len(v["pe"])}
            for s, v in by.items()}


def market_summary(screener, universe):
    """Headline figures for every listed symbol, from the single screener call.

    This is what lets any visitor search and add any stock without waiting for a
    scrape: it is small enough to ship whole (a few hundred KB) and covers the
    entire market, while the heavy per-company data is fetched only for the
    covered set.
    """
    by_sym = {u["symbol"]: u for u in universe}
    out = {}
    for sym, r in screener.items():
        u = by_sym.get(sym)
        if not u or u.get("isDebt"):
            continue
        out[sym] = {
            "name": u["name"],
            "sector": u["sector"],
            "price": r.get("price"),
            "pe": r.get("pe"),
            "divYield": r.get("divYield"),
            "marketCap": r.get("marketCap"),
            "change1Y": r.get("change1Y"),
            "changePct": r.get("changePct"),
            "vol30dAvg": r.get("vol30dAvg"),
            "indices": r.get("listedIn") or "",
        }
    return out


def covered_symbols(screener, extra=(), index="KSE100"):
    """Which symbols get the full treatment: the index constituents plus any
    symbol someone has actually asked for."""
    idx = sorted(s for s, r in screener.items()
                 if index in (r.get("listedIn") or ""))
    out = list(idx)
    for s in extra:
        if s not in out:
            out.append(s)
    return out


def build_dataset(symbols, progress=None, universe=None, screener=None):
    """`universe` and `screener` may be passed in to avoid re-downloading them -
    the screener alone is ~700KB and PSX will reset the connection if a large
    run asks for it twice."""
    if universe is None:
        if progress:
            progress("Fetching market universe...")
        universe = fetch_universe()
    universe_idx = {u["symbol"]: u for u in universe}
    if screener is None:
        if progress:
            progress("Fetching market-wide screener (P/E, dividend yield)...")
        screener = fetch_screener()
    secstats = sector_stats(screener, universe)

    results, errors = {}, {}

    def work(s):
        # A dropped connection mid-run must cost one symbol, not the whole job.
        last = None
        for attempt in range(3):
            try:
                return s, build_symbol(s, screener, universe_idx), None
            except Exception as e:
                last = e
                time.sleep(2.0 * (attempt + 1))
        return s, None, "%s: %s" % (type(last).__name__, last)

    # PSX throttles bursts; 3 workers keeps even a 100-symbol run reliable
    with ThreadPoolExecutor(max_workers=3) as ex:
        for i, (s, rec, err) in enumerate(ex.map(work, symbols), 1):
            if progress:
                progress("[%d/%d] %s %s" % (i, len(symbols), s,
                                            "OK" if rec else "FAILED " + str(err)))
            if rec:
                rec["sectorStats"] = secstats.get(rec["sector"], {})
                results[s] = rec
            else:
                errors[s] = err

    return {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "dps.psx.com.pk (Pakistan Stock Exchange Data Portal)",
        "symbols": symbols,              # everything collected in full
        "watchlist": load_watchlist(),   # the list a first-time visitor starts with
        "stocks": results,
        "errors": errors,
        "sectorStats": secstats,
        "universe": [u for u in universe if not u["isDebt"]],
        "market": market_summary(screener, universe),
    }


def save(dataset):
    os.makedirs(DATA_DIR, exist_ok=True)
    # Fold in the hand-entered / harvested balance-sheet figures so the static
    # build (GitHub Pages) carries them too - there is no API to fetch them from.
    mpath = os.path.join(DATA_DIR, "manual.json")
    if os.path.exists(mpath):
        try:
            with open(mpath, encoding="utf-8") as f:
                dataset["manual"] = json.load(f)
        except Exception:
            pass
    with open(os.path.join(DATA_DIR, "psx_data.json"), "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False)
    with open(os.path.join(DATA_DIR, "psx_data.js"), "w", encoding="utf-8") as f:
        f.write("window.PSX_DATA = ")
        json.dump(dataset, f, ensure_ascii=False)
        f.write(";")


def load_watchlist():
    p = os.path.join(HERE, "watchlist.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_watchlist(lst):
    with open(os.path.join(HERE, "watchlist.json"), "w", encoding="utf-8") as f:
        json.dump(lst, f, indent=0)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only_watchlist = "--watchlist-only" in sys.argv

    print("Fetching the market universe and screener...")
    universe = fetch_universe()
    screener = fetch_screener()

    if args:
        syms = [s.upper() for s in args]
    elif only_watchlist:
        syms = load_watchlist()
    else:
        # Cover the whole KSE-100 plus anything anyone has asked for, so every
        # visitor can add those stocks instantly without a scrape of their own.
        syms = covered_symbols(screener, load_watchlist())

    print("Scraping %d symbols from PSX..." % len(syms))
    ds = build_dataset(syms, progress=lambda m: print("  " + m, flush=True),
                       universe=universe, screener=screener)
    save(ds)
    print("\nSaved -> data/psx_data.json  (%d ok, %d failed, %d in market index)"
          % (len(ds["stocks"]), len(ds["errors"]), len(ds.get("market", {}))))
    for s, e in ds["errors"].items():
        print("  ! %s -> %s" % (s, e))
