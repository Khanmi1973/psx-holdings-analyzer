# PSX Holdings Analyzer

Automated tracking and screening for Pakistan Stock Exchange holdings. Scrapes PSX
published data, computes a fundamental and a technical score for every holding, and
shows the full evidence behind each score.

**Live dashboard → https://khanmi1973.github.io/psx-holdings-analyzer/**

**This is a research tool, not investment advice.** See *Limits* at the bottom — they matter.

---

## Two ways to use it

| | Hosted (GitHub Pages) | Local (`start.bat`) |
|---|---|---|
| Open on your phone, PC switched off | ✓ | ✗ |
| Sort, filter, adjust scoring weights, read every detail panel | ✓ | ✓ |
| Re-scrape on demand | ✓ * | ✓ |
| Add or remove a stock | ✓ * | ✓ |
| Fetch balance-sheet ratios | ✓ * | ✓ |
| Edit balance-sheet figures by hand | saved in that browser | saved to `data/manual.json` |

\* after a one-time connection to GitHub — see below.

The hosted copy also refreshes itself unattended: the workflow re-scrapes PSX at
**12:30 UTC on weekdays** (17:30 PKT, after the close), rebuilds `docs/` and commits the
result, which redeploys Pages.

---

## Making Refresh and Add work on the hosted page

A GitHub Pages site is static — it has no server. So the buttons don't call a backend;
they ask **GitHub Actions** to run the very same workflow that runs on the schedule.
It scrapes, commits, and republishes, and the page loads the new data as soon as the run
finishes (straight from the repo, without waiting for Pages to redeploy).

Starting a workflow run requires a GitHub token, and **a token must never live in a
public repository**. So it is stored only in your browser. Nothing secret is committed —
the repo contains no credentials of any kind.

**One-time setup**, from the dashboard's **⚙ Online setup** button:

1. Go to [Fine-grained tokens](https://github.com/settings/personal-access-tokens/new)
2. **Repository access** → *Only select repositories* → `psx-holdings-analyzer`
3. **Permissions** → Repository permissions → **Actions: Read and write**.
   Nothing else — leave every other permission at *No access*.
4. Choose an expiry, generate, and paste it into the panel → **Save & test**

That token can start workflow runs on this one repository and do nothing else. It cannot
read your other repositories, your account, or anything private. Revoke it any time from
the same settings page, or press **Forget token** to clear it from the browser.

Each cloud action takes a couple of minutes (the run has to boot a machine, scrape, and
commit). A bar at the bottom of the page shows progress and links to the live log.

You can always drive the same workflow by hand from the **Actions** tab →
*Refresh PSX data* → *Run workflow*, which also accepts symbols to add or remove.

Changes you make locally reach the hosted copy with:

```bash
python psx_scraper.py
python build_docs.py
git add -A && git commit -m "Update watchlist" && git push
```

---

## Run it locally

```bash
start.bat
```

Opens `http://127.0.0.1:8777`. First run scrapes PSX automatically (about a minute).
Live mode gives you **Refresh data**, **+ Add stock** and **Remove**.

No `pip install` needed — Python standard library only.

Then, once per results season, pull the balance-sheet half:

```bash
python psx_ratios.py
```

Data-only refresh, without the server:

```bash
python psx_scraper.py
```

Scrape specific symbols ad hoc:

```bash
python psx_scraper.py LUCK SYS PSO
```

---

## Where the data comes from

Everything is scraped from the PSX Data Portal (`dps.psx.com.pk`), which serves data
supplied by PSX / Capital Stake.

| Endpoint | What it gives |
|---|---|
| `/symbols` | Every listed symbol, company name, sector — powers the Add-stock search |
| `/screener` | Market cap, price, P/E (TTM), **dividend yield**, 1-year change, 30-day volume for ~740 symbols |
| `/company/<SYM>` | Profile, shares, free float, 4 years of Sales / Profit / EPS, last 4 quarters, reported margins and EPS growth |
| `/timeseries/eod/<SYM>` | ~5 years of end-of-day closes and volume — everything technical is derived from this |

Sector median P/E and median dividend yield are computed across the whole market so
each holding can be judged against its own peer group rather than against the index.

---

## How the scores work

Two independent scores, because "good company" and "good moment" are different questions.

### Long-term score (0–100) — the business

| Pillar | Default weight | What it measures |
|---|---|---|
| Earnings growth | 25 | 3-year profit CAGR, consistency of the trend, latest-year direction |
| Profitability | 20 | Net margin level, its direction vs the 4-year average, and its stability |
| Revenue growth | 10 | 3-year top-line CAGR — must clear a real hurdle, not just inflation |
| Valuation | 20 | Absolute P/E, P/E relative to the sector median, and PEG |
| Dividend & payout | 15 | Yield **and** whether the payout ratio is sustainable |
| Size / stability | 10 | Market cap, free float, price volatility, years of profitability |
| Balance sheet ✎ | 20 | ROE, debt/equity, current ratio, interest cover, cash-flow quality, price-to-book — **entered by hand**, see below |

### Short-term score (0–100) — the price

| Pillar | Default weight | What it measures |
|---|---|---|
| Trend | 30 | Price vs 50-day average, 50-day vs 200-day |
| Momentum | 25 | 1-month, 3-month and 6-month returns |
| 52-week position | 15 | Mid-range scores best — near the high is extended, near the low may still be falling |
| Volatility | 15 | Annualised; lower is better for holding |
| Liquidity | 15 | 30-day average turnover in rupees — can you actually get out |

**Every weight is adjustable.** Click *Scoring weights* and drag; scores, grades and
verdicts recompute instantly and your settings persist. If dividends are what you care
about, weight income up and momentum to zero.

### Grades and stance

Grade comes from the long-term score: **STRONG** ≥70, **SOLID** ≥55, **MIXED** ≥40, **WEAK** below.

The stance line combines both scores, which is what answers "long term, short term, or not":

| Long-term | Short-term | Reading |
|---|---|---|
| Strong | Strong | Fundamentals and trend agree — the combination that suits a multi-year hold |
| Strong | Weak | Business firm, price marked down — accumulation window, or the market knows something the numbers don't show yet |
| Weak | Strong | Momentum only — has the character of a trade, and a trade needs an exit rule set in advance |
| Weak | Weak | Neither supports holding — what specifically has to change to justify keeping it? |

---

## Balance-sheet data (the important part)

PSX publishes only Sales, Profit and EPS. Debt, gearing, ROE, interest cover and cash
flow — the things that actually sink companies — are in no PSX feed. There are three
ways to get them in, and the first is automatic.

### 1. Automatic (recommended)

```bash
python psx_ratios.py
```

Or press **Fetch balance-sheet data** in the dashboard header.

This pulls ROE, debt-to-equity, current and quick ratio, interest cover, book value and
free cash flow per share from **sarmaaya.pk**, whose `robots.txt` permits all crawlers
(`User-Agent: * / Allow: /`, no AI-agent restrictions) and whose pages are marked
`robots: index, follow`.

That site renders its ratio table in the browser, so there is no plain-HTTP route to the
numbers. Rather than add a heavyweight automation dependency, the harvester drives the
**Chrome or Edge already installed on your machine** in headless mode and reads the
rendered DOM (`chrome --headless=new --dump-dom`). No `pip install`, no `npm install`,
no driver download. It runs one symbol at a time with a pause between, uses a throwaway
browser profile so your real one is never touched, and checkpoints after each symbol.

Allow roughly 15–20 seconds per holding. Once per results season is plenty.

```bash
python psx_ratios.py EFERT MEBL      # just these
python psx_ratios.py --overwrite     # replace hand-entered values too
```

By default **your typed values win** — a re-fetch fills the gaps around them rather than
overwriting what you entered yourself. The detail card always shows which source a
figure came from and the date it was taken.

### 2. Per stock, by hand

Open any holding, fill the *Balance sheet* card, press Save.

### 3. In bulk, from a spreadsheet

Click *Balance-sheet CSV* → *Download template* → fill it in Excel → paste it back →
*Import*. Use this for figures from your own sources — annual reports, broker research,
or a provider you subscribe to.

| Field | Unit | Rough comfort zone | Auto-fetched |
|---|---|---|---|
| `roe` | % | 15%+ sustained is strong | ✓ |
| `de` | × | 0.5 or below is conservative; above 2 is heavily geared | ✓ |
| `current` | × | 1.5+ covers short-term bills; below 1 is a red flag | ✓ |
| `quick` | × | same test, ignoring inventory | ✓ |
| `icover` | × | 3+ means profit comfortably pays the interest bill | ✓ |
| `fcfps` | Rs | free cash flow per share; scored as a yield on the price | ✓ |
| `bvps` | Rs | book value per share — price-to-book is computed from it | ✓ |
| `ocfni` | × | operating cash flow ÷ net income; ~1.0 means profit is arriving as real cash | by hand |

### Banks and other leveraged businesses

A 2.7× debt-to-equity is normal for a bank and alarming for a cement plant. For
commercial banks, insurers, investment companies, leasing companies and REITs, the
gearing and liquidity ratios are **excluded** from the balance-sheet score and from the
risk flags rather than scored as distress — those sectors are judged on ROE, book value
and cash flow instead. The detail card says explicitly which fields were excluded and why.

Blank fields are **skipped, not scored as zero**, so a half-filled stock is never falsely
punished — the pillar simply averages whatever you have provided, and a stock with no
entries at all keeps its score computed from the other six pillars. The dashboard's
*B/Sheet* column shows `add` wherever data is still missing.

Entries are saved to `data/manual.json` (server mode) and to browser storage, so they
survive refreshes and re-scrapes.

Filling these switches on a set of flags nothing else can catch: heavy gearing, interest
cover under 3, current ratio below 1, profit not converting to cash, and weak ROE.

### Which sources this tool uses, and why

| Source | Used | Why |
|---|---|---|
| `dps.psx.com.pk` | ✓ automatic | Official exchange data, no crawl restrictions |
| `sarmaaya.pk` | ✓ automatic | `robots.txt`: `User-Agent: * / Allow: /`, no AI-agent restrictions, pages marked `index, follow` |
| `askanalyst.com.pk` | ✗ not scraped | `robots.txt` disallows AI crawlers **by name** — `ClaudeBot`, `GPTBot`, `CCBot`, `Google-Extended`, `Bytespider` — with `Content-Signal: ai-train=no` |

askanalyst publishes the same kind of ratios and its content signal permits `use=reference`,
so you are free to read it yourself. This tool does not automate collection against an
operator who asked AI agents not to, and does not disguise its user agent to get around
the block. If you want figures from there, read them and use the CSV import — that path
exists precisely so any source you have the right to use can feed the model.

### Risk flags

Rules that fire on the actual numbers, each explained in plain English: earnings
contracting, losses in the record, margin compression, shrinking revenue, payout above
100% of earnings, P/E above 35, no dividend, price below the 200-day average, drawdown
over 35%, volatility over 45%, thin trading, and accounting artefacts.

---

## Data-quality handling

Real filings are messier than a screener suggests. The scraper handles:

- **Share splits and bonus issues.** PSX does not restate historic EPS. MARI's 2023 EPS
  of 420.75 next to 2024's 64.37 is a share-count change, not a 6× collapse — a naive
  EPS CAGR reads −44%. The scraper detects the step change, cross-checks it against
  Profit after Taxation (which is immune to share count), and switches the growth
  metric to a profit basis. MARI scores on +15.8% instead. The detail panel always
  states which basis was used.
- **Banks and REITs.** They report "Mark-up Earned" or "Total Income" instead of "Sales",
  so the revenue line resolves per industry.
- **Margins above 100%.** HUBC and DCR report net margins over 100% because profit
  includes associate income or fair-value gains booked against a small revenue line.
  The profitability scale saturates so this cannot inflate a score, and a flag explains it.
- **Extreme P/E.** ENGROH's post-restructuring P/E of ~2000 is shown as *n/m* and
  scored at the floor rather than being allowed to break the sector comparison.
- **The XD tag.** PSX puts an ex-dividend badge inside the symbol cell; parsing the
  visible text yields `EFERTXD` and silently loses dividend yield for exactly the
  stocks that pay one. The parser reads the clean `data-order` attribute instead.

---

## Limits — read these

- **Balance-sheet data is second-hand.** PSX publishes only Sales, Profit and EPS, so
  debt, ROE, interest cover and cash flow come from sarmaaya.pk (or from you). That is
  a third party's calculation, not the audited filing — definitions of "debt",
  "equity" and "interest cover" vary between providers. For a holding you are seriously
  weighing, check the figures against the company's own annual report. The `add`
  markers in the B/Sheet column show what the model still cannot see at all.
- **TTM figures move.** Auto-fetched ratios are trailing-twelve-month by default and
  will shift after each quarterly result. The date they were taken is shown on the card.
- **Four years of history.** Too short to see a full cycle in cement, autos or E&P.
- **Nothing qualitative.** Management quality, related-party transactions, pledged
  sponsor shares, auditor changes, governance — none of it is here, and all of it has
  sunk companies that screened well.
- **Dividend gaps.** PSX has no payout record for some symbols (REITs such as DCR
  among them), which shows as 0% yield. Verify on the company's PSX payouts tab before
  concluding it pays nothing.
- **Scores are arithmetic, not judgement.** They rank what is measurable. Treat a high
  score as "worth reading the annual report", not as a decision.
- Data may be delayed or later restated by the issuer.
- **This repository is public.** `watchlist.json` and the published dashboard show which
  stocks are tracked here. Nothing about position sizes, purchase prices or account
  details is stored anywhere in this project — but the list of symbols is visible to
  anyone who finds the repo. Make the repo private if that changes for you (Pages on a
  private repo needs GitHub Pro).

---

## Files

| File | Purpose |
|---|---|
| `start.bat` | Launch the server and open the dashboard |
| `update-data.bat` | Re-scrape without the server |
| `psx_scraper.py` | PSX scraping, parsing, metric computation |
| `psx_ratios.py` | Balance-sheet ratio harvester (headless Chrome) |
| `psx_server.py` | Local server and the add / remove / refresh API |
| `index.html` | Dashboard and scoring engine |
| `watchlist.json` | Tracked symbols — edit directly if you prefer |
| `build_docs.py` | Builds the static site into `docs/` for GitHub Pages |
| `.github/workflows/refresh.yml` | Scheduled re-scrape that republishes the hosted copy |
| `docs/` | The published static site — generated, do not edit by hand |
| `data/manual.json` | Balance-sheet figures (harvested or hand-entered) |
| `data/psx_data.json` | Cached dataset |
| `data/psx_data.js` | Same data, so `index.html` also opens directly from disk |
