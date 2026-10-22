# Episodic Pivot (EP)

Buy a stock on the day it gaps up sharply on big news, usually earnings, and hold the winners for
days to weeks; only take new trades while the broad market is in an uptrend. The rules follow the
method Qullamaggie describes publicly; this repo is not affiliated with him. Status: **research**. Backtested over five years; paper trading is the next step.

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

## Backtest results

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
- The statistics are promising but not conclusive (t-stat 2.26 on 213 trades).
- No earnings data: "strong earnings" is approximated by the market-cap floor.
- Fills use 5-minute bars; real fills on fast gap-ups can be worse. No borrow, margin, dividends
  or corporate-action ledger.
- Before using real money, paper trade it and compare the live record with these numbers.

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
| `episodic_pivot/__main__.py` | Command line |
| `tests/test_episodic_pivot.py` | Synthetic-data tests |
