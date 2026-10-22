# Episodic Pivot (EP)

Buy a stock on the day it gaps up sharply on big news, usually earnings, and hold the winners for
days to weeks. The rules follow the method Qullamaggie describes publicly; this repo is not
affiliated with him. Status: **research**. Backtested over five years; paper trading is the next step.

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

Optional market filter (stated by Qullamaggie, off by default because it did not help in our
tests): only enter when QQQ's 10-day SMA is above its 20-day and both are rising.

What is ours rather than his: the $1B market-cap floor (a proxy for "a real company with strong
earnings", because our data plan has no earnings data), the $3 and $5M floors, the 11:30 cut-off,
and selling exactly half on day 3.

## Backtest results

Data: Massive.com (formerly Polygon.io), consolidated US prices. Fixed $25,000 account, no
compounding, $0.005/share commission and $0.02/share slippage on every fill. Years run November
to October.

| Year | Market | 20-day trail | 10-day trail |
|---|---|---|---|
| 2021-22 | Bear | -$2,714 (-10.9%), PF 0.81 | -$4,147 (-16.6%), PF 0.75 |
| 2022-23 | Recovery | +$4,242 (+17.0%), PF 1.38 | +$3,160 (+12.6%), PF 1.25 |
| 2023-24 | Bull | +$8,455 (+33.8%), PF 3.13 | +$6,945 (+27.8%), PF 1.93 |
| 2024-25 | Bull | +$9,863 (+39.5%), PF 2.06 | +$9,630 (+38.5%), PF 1.79 |
| 2025-26 | Choppy | -$202 (-0.8%), PF 0.98 | -$1,335 (-5.3%), PF 0.88 |
| **5 years** | | **441 trades, +$18,708, PF 1.35, t-stat 1.87** | 521 trades, +$12,068, PF 1.18, t-stat 1.20 |

For the 20-day trail over five years:

- About 37% of trades win. The profit comes from winners that run 5 to 12 times the risk over
  days or weeks.
- Still +$6,099 without the five best trades, and +$14,984 with an extra $0.02/share slippage on
  every entry and exit.
- A random sign-flip test puts the chance of doing this well by luck at about 3%.
- Worst drawdown in a year: about 21% (2021-22).

How the version was chosen: the rules were fixed from his description, tuned only on 2025-26,
and then run unchanged on the other four years. The 20-day trail was picked after seeing it do
slightly better in 2025-26; it was also better in four of the five years.

## Limitations

- It depends heavily on the market: strong momentum years carry it, bear and choppy years lose.
- The statistics are promising but not conclusive (t-stat 1.87, just under the usual bar of 2).
- No earnings data: "strong earnings" is approximated by the market-cap floor.
- Fills use 5-minute bars; real fills on fast gap-ups can be worse. No borrow, margin, dividends
  or corporate-action ledger.
- Before using real money, paper trade it and compare the live record with these numbers.

## Run it

Needs Python 3.11+ (standard library only) and a Massive.com API key in `MASSIVE_API_KEY`
(environment or `.env` at the repo root; both are ignored by Git).

```sh
python -m strategies.episodic_pivot --start 2024-11-01 --end 2025-10-31 --output output/ep_2025.json
python -m strategies.episodic_pivot --start 2024-11-01 --end 2025-10-31 --trail 10 --regime
```

Downloads are cached in `data/massive_cache` (ignored by Git), so reruns are fast. The first run
of a year downloads that year's gappers and can take 30-60 minutes. The output JSON has the
summary, the daily equity curve, every trade with its fills, and why each gapper was skipped.

Tests use synthetic data only: `python -m unittest discover -s tests -v`.

## Files

| File | Purpose |
|---|---|
| `rules.py` | `Settings` (every parameter, with defaults) and the entry, sizing and position rules |
| `backtest.py` | Day-by-day portfolio simulation over any data source |
| `massive.py` | Massive.com data source with a local cache |
| `__main__.py` | Command line |
| `../../tests/test_episodic_pivot.py` | Synthetic-data tests |
