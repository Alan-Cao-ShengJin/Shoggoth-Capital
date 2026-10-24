# Episodic Pivot (EP)

Buy a stock on the day it gaps up sharply on big news, usually earnings, and hold the winners for
days to weeks; only take new trades while the broad market is in an uptrend. The rules follow the
method Qullamaggie describes publicly; this repo is not affiliated with him. Status: **research**.
Execution and accounting defects have been corrected. Paired historical replays of the previous
and corrected code are reported below, with their coverage and modeling limits.

## Rules

| Step | Rule |
|---|---|
| Which stocks | Common stock or ADR that opens at least **10% above the prior close**, price at least $3, market cap at least **$1B**, and at least $5M daily dollar volume (prior day and 20-day average). |
| Volume | Premarket plus regular-session volume before entry must reach **one full average day** (20-day average volume). |
| Entry | Buy when price breaks above the **opening-range high** (the first 5-minute candle), before 11:30 New York time. |
| Stop | The **day's low** at the time of entry. Skip the trade if that is wider than the stock's 20-day average daily range (ADR). |
| Partial | From **day 3**, sell half at the close once the trade is in profit, then move the stop to the entry price. |
| Exit | Sell the rest on the **first daily close below the 20-day SMA** (10-day is the faster alternative). |
| Size | Risk **1%** of a fixed $25,000 account per trade, at most **25%** in one stock, at most **4** positions. No new entries while 4 are open. |
| Market switch | Only take new entries when **QQQ and IWM** both closed above a **rising 21-day EMA** the day before. Open positions are managed as usual either way. |

What is ours rather than his: the $1B market-cap floor (a proxy for "a real company with strong
earnings", because our data plan has no earnings data), the $3 and $5M floors, the 11:30 cut-off,
and selling exactly half on day 3. The market switch is Martin Luk's rule (2025 US Investing
Championship winner); Qullamaggie's own filter (QQQ 10-day SMA above the 20-day, both rising) did
not help in our tests and remains available as an option.

## What was wrong and what changed

| Defect | Previous behavior | Correction |
|---|---|---|
| Future exits released position slots | A position sold at the close, or stopped later in the day, could free a slot for an earlier morning entry. Four allowed positions could therefore overlap with a fifth. | Process opening gaps, entries and intraday stops in time order. Reserve a slot throughout a candle; apply close-based exits after the entry window. |
| Entry stops used the candle's eventual low | The breakout candle's later low could change sizing or cause a losing trade to be skipped. That candle was then omitted from stop execution. | Use completed candles and the current open for the initial stop. Include the entry candle in stop checks, assuming an adverse entry-then-stop ordering when OHLC cannot establish the order. |
| Failed data responses became empty observations | HTTP errors were cached as empty results and could silently remove a trading session. | Raise on HTTP/API errors, reject previously cached error responses, and preserve successful empty holiday responses. |
| Final liquidation understated costs | Terminal exits omitted slippage, and the last curve point preceded the exit commission. | Charge the same slippage and commission as other sales, then record the final curve point so it equals total trade P&L. |

The historical replay also encountered RETA's cash acquisition. An explicitly verified cash
entitlement now cancels those shares on the effective date; an unexplained missing held price
still stops the replay. This additional accounting change is measured separately below.

Synthetic regressions cover these cases, including carried-position stops, same-candle capacity,
opening gap exits, failed and cached API responses, terminal costs, and verified cash entitlements.

## Corrected historical replay — October 24, 2026

The previous implementation at `c2becc1` and the corrected implementation were replayed on the
same cached Massive observations with the default QQQ/IWM EMA21 market filter. Each period starts
flat with a fixed $25,000 sizing base, $0.005/share commission and $0.02/share adverse slippage.
These are returns over each period, with no compounding or parameter tuning.

| Period | Previous profit | Corrected profit | Corrected return | Corrected PF | Trades, previous → corrected |
|---|---:|---:|---:|---:|---:|
| 2021-22, shortened | $319.78 | $337.42 | +1.35% | 1.07 | 32 → 31 |
| 2022-23 | $5,530.33 | $4,559.22 | +18.24% | 1.75 | 44 → 46 |
| 2023-24 | $3,116.47 | $2,042.93 | +8.17% | 1.35 | 40 → 41 |
| 2024-25 | $8,369.60 | $8,076.35 | +32.31% | 2.24 | 54 → 53 |
| 2025-26, through October 23 | $2,023.06 | $1,433.16 | +5.73% | 1.25 | 43 → 44 |
| **Combined independent periods** | **$19,359.24** | **$16,449.08** | | **1.57** | **213 → 215** |

The corrected combined profit is $2,910.16 lower (about 15%). Pooled profit factor falls from
1.71 to 1.57, win rate from 36.15% to 33.02%, and the trade-profit t-statistic from 2.26 to 1.92.
Every recorded period remains profitable; the largest corrected within-period drawdown is 19.0%.
The combined profit is a sum of independently reset accounts, not a compounded account return.

The shortened first period is November 24, 2021 through October 31, 2022: the available daily
history begins October 25, 2021 and supplies 22 completed warm-up sessions for the initial market filter. The
2022-23 benchmark histories have 257 prior sessions; later periods have 289-290. The earliest
period is not a full reproduction of November 1 onward. The latest period is November 3, 2025
through October 23, 2026; the middle periods run November 1 through October 31. Fresh provider
snapshots closely reproduce the earlier reported profits and trade counts, but are not the
research author's original cache.

The real-data replay also exposed RETA's completed cash acquisition. Its shares were canceled
before the September 26, 2023 market open for a $172.50/share cash entitlement, after its last
trading day on September 25. The explicitly catalogued event replaces the missing trading quote;
it is not a market sale and incurs no execution costs. [Nasdaq's confirmed terms](https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2023-540).
Payment timing is not simulated. A separate old-code counterfactual with only this event changed
adds $2.61; the four execution/accounting corrections account for the remaining -$2,912.77.

The committed [replay summary](research/execution_corrections_2026-10-24.json) records parameters,
per-period results, the cash-event counterfactual, and input/source fingerprints without credentials,
local paths or raw vendor responses. Each paired replay verifies final equity against total trade
profit and the four-position limit.
Local audit outputs under the repository root's `output/ep_corrections/` include both complete
replays, per-year comparisons, a cash-event counterfactual, input-response hashes, source hashes,
and a chart. Downloaded data use bounded compressed caches; credentials and downloaded responses
remain outside the committed summary.

## Earlier published research — reference only

**The figures below predate the corrections. Use the paired results above for the corrected
default implementation.** The earlier comparisons with other market filters and cost settings
have not been rerun here.

Data: Massive.com (formerly Polygon.io), consolidated US prices. Fixed $25,000 account, no
compounding, $0.005/share commission and $0.02/share slippage on every fill. Years run November
to October.

| Year | Market | With the market switch | Without it |
|---|---|---|---|
| 2021-22 | Bear | +$320 (+1.3%), PF 1.07, 32 trades | -$2,714 (-10.9%), PF 0.81, 107 trades |
| 2022-23 | Recovery | +$5,531 (+22.1%), PF 1.99, 44 trades | +$4,242 (+17.0%), PF 1.38, 87 trades |
| 2023-24 | Bull | +$3,116 (+12.5%), PF 1.59, 40 trades | +$8,455 (+33.8%), PF 3.13, 47 trades |
| 2024-25 | Bull | +$8,370 (+33.5%), PF 2.34, 54 trades | +$9,863 (+39.5%), PF 2.06, 81 trades |
| 2025-26 | Choppy | +$2,023 (+8.1%), PF 1.38, 43 trades | -$202 (-0.8%), PF 0.98, 78 trades |
| **5 years** | | **213 trades, +$19,360, PF 1.71, t-stat 2.26** | 400 trades, +$19,644, PF 1.41, t-stat 2.01 |

With the market switch, over five years:

- About 36% of trades win. The profit comes from winners that run 5 to 12 times the risk over
  days or weeks.
- Every year is positive; worst drawdown in a year is about 19% (2023-24).
- Still +$6,621 without the five best trades, and +$16,285 (PF 1.58) with an extra $0.02/share
  slippage on every entry and exit. Without the switch, the same extra slippage leaves +$10,645
  (PF 1.21).
- A random sign-flip test puts the chance of doing this well by luck at about 1%.
- The EMA length barely matters: 15 days gives +$21,417 (PF 1.81), 30 days +$24,308 (PF 1.96), both
  positive every year.

How the version was chosen: the entry and exit rules were fixed from his description, tuned only on
2025-26, and then run unchanged on the other four years; the 20-day trail was picked after seeing it
do slightly better in 2025-26 (it was also better in four of the five years). The market switch came
later: knowing which years lost, we tested four market filters taken from traders' published rules
(QQQ/IWM above a rising EMA; SPY EMA50 above EMA200; SPY above its 256-day SMA with SMA30 above SMA120;
at least half of liquid stocks above their 50-day average). QQQ/IWM was the only one that is both
positive every year and insensitive to its setting. Other changes tested and rejected (entries,
exits, sizing) are listed in the research notes behind this strategy.

## Limitations

- No unseen data is left: the switch was chosen after seeing all five years. Paper trading is the
  real test.
- It still depends on the market: the switch keeps it out of weak markets, so it trades about 43
  times a year instead of 80, and the bull years earn less than without it.
- The corrected pooled statistics are not conclusive (t-stat 1.92 on 215 trades), and no unseen
  validation period remains.
- No earnings data: "strong earnings" is approximated by the market-cap floor.
- Fills use 5-minute bars; real fills on fast gap-ups can be worse. No borrow, margin or dividend
  ledger. Only explicitly catalogued cash-merger entitlements are recognized; other corporate
  actions and actual payment timing remain unmodeled.
- Before using real money, paper trade it and compare the live record with these numbers.

## Execution and accounting

- Positions are processed in time order. Opening gap stops can free capacity before entries;
  intraday stops free capacity after their five-minute candle, and close-based exits only at
  the close. Same-candle entries cannot use slots freed by that candle's eventual low.
- The initial stop uses completed regular-session candles and the entry candle's opening price.
  Its eventual low cannot change eligibility or sizing. If the entry candle also touches the
  stop, assume the stop follows the entry; five-minute OHLC data cannot establish the true order.
- Held positions whose daily range crosses a stop require that day's intraday bars to place the
  exit in time. Older caches may need these additional downloads. Missing stop data, HTTP/API
  errors and cached error responses stop the run instead of silently dropping observations.
- A verified pre-market cash merger cancels shares and records their cash entitlement before new
  entries. Missing held prices without an explicitly catalogued event still stop the run.
- Positions still held at the end are liquidated with adverse slippage and commission. The final
  equity-curve point includes these costs and equals the sum of reported trade profits. Curve
  `equity` values are cumulative P&L from zero; add `settings.account` for account value.
- Close-based sales still use the reported daily close as an execution approximation; this is
  not an auction-order or tick-by-tick simulation.

## Run it

Needs Python 3.11+ (standard library plus timezone data from this folder's `requirements.txt`)
and a Massive.com API key in `MASSIVE_API_KEY`: the environment, or a `.env` file in this folder
or at the repo root (ignored by Git). Run from this folder:

```sh
cd strategies/episodic_pivot
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m episodic_pivot --start 2024-11-01 --end 2025-10-31 --output output/ep_2025.json
python -m episodic_pivot --start 2024-11-01 --end 2025-10-31 --market-filter off
```

`--market-filter` is `qqq_iwm` (default), `qqq_sma` (Qullamaggie's QQQ 10/20-day SMA filter) or
`off`; `--market-ema` sets the EMA length for `qqq_iwm`.

The tests use synthetic data only and need no key. Downloads are cached in `data/massive_cache`
(ignored by Git), so reruns are fast. The first run of a year downloads that year's gappers and
can take 30-60 minutes. The output JSON has the summary, the daily equity curve, every trade with
its fills, and why each gapper was skipped.

## Files

| File | Purpose |
|---|---|
| `episodic_pivot/rules.py` | `Settings` (every parameter, with defaults) and the entry, sizing and position rules |
| `episodic_pivot/backtest.py` | Day-by-day portfolio simulation over any data source |
| `episodic_pivot/massive.py` | Massive.com data source with a local cache |
| `episodic_pivot/cash_mergers.json` | Explicitly verified pre-market cash-merger events and source links |
| `episodic_pivot/__main__.py` | Command line |
| `tests/` | Synthetic rule tests and regressions for timing, data failures and accounting |
