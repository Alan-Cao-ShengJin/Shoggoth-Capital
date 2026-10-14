# MVE — Momentum Volume Efficiency

The core of Alan's current stock strategy, packaged for local testing with Tim.
It combines a quarterly momentum watchlist, confirmed four-hour absorption
signals, portfolio sizing/risk checks and an SGOV cash sleeve.

## Try it

Use **Python 3.11 or later**. The only dependency is timezone data; the strategy
calculations use the Python standard library.

```sh
git clone https://github.com/Alan-Cao-ShengJin/MVE.git
cd MVE
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python run_local.py --demo --output output/demo.json
```

The example generates **synthetic data** and demonstrates:

1. An opening review puts unused cash into SGOV.
2. A + computed by the actual indicator sells enough SGOV to fund a stock add.
3. A later x computed by the indicator gently trims the remaining stock holding.

It produces a local cash/share ledger and an auditable JSON report. It requires
no API key or broker account and cannot submit broker orders. The example is a
functionality test, not evidence of real profitability.

## Current strategy, step by step

1. **Build an entry watchlist every three calendar months.** From the top 500
   stocks by liquidity with a completed close of at least $5, rank positive
   231-session price returns ending 21 sessions ago. Keep up to 100 names.
2. **Check suitability using completed daily data.** Long momentum must be
   positive. The latest 63-session price return must also be positive and exceed
   SPY's. A suitable existing holding can stay outside the new watchlist.
3. **Buy on a new confirmed +.** Aim toward 7.5% of current account equity,
   with at most 8% of equity per add, subject to cash and risk checks. Watchlist
   membership alone does not buy a stock. Higher momentum has entry priority.
4. **Trim on a new confirmed x.** Sell 2.5% of the current holding, capped at
   0.5% of account equity. This retained setup uses gentle trimming.
5. **Exit known qualification failures.** Do not wait for x. Missing information
   retains an existing holding and blocks additions. Risk breaches can force
   reductions even when there is no x.
6. **Check diversification and risk.** At most 20 instruments including SGOV;
   20% per stock; 35% for connected groups with correlation >=0.8; 15% per
   detailed industry and 30% per broad industry, with at most 3/6 industry names.
   Estimated annual volatility is capped at 20%; each instrument's contribution
   is capped at five volatility percentage points. Check both 20- and 126-session
   estimates with at least 60 matching daily changes.
7. **Use SGOV for spare cash.** Refill at opening reviews if the deficit is at
   least 1% of equity, leaving a 0.1% execution buffer. SGOV can exceed the stock
   20% cap. It is excluded from stock industry/correlation-group money limits,
   but included in whole-account covariance and the 20-instrument count. Confirm
   funding sales before purchases; repair combined covariance breaches first.

This selects sequentially with risk checks. It does not mathematically optimize
the least-correlated combination of 20 names. The portfolio can own fewer stocks
while it awaits suitable signals or risk capacity.

## What + and x mean

The indicator marks the **start** of bullish (+) and bearish (x) absorption
zones. It compares candle body size and volume with the **previous 100 candles**:
body percentile <=30 and volume percentile >=70. EMA20's earlier three-bar slope
supplies direction. Confirmed swings and an active-zone rule suppress duplicates.

Hourly observations are grouped into ET-midnight anchored four-hour windows
starting at 00:00, 04:00, 08:00, 12:00, 16:00 and 20:00, including observed
extended-hours activity. Empty intervals stay empty. A window becomes usable
only after its end **plus a 16-minute data buffer**. Reviews normally occur at
09:30 and about 12:20 ET. Only the latest new marker since the previous review
is acted on. The indicator never backdates swing confirmation, and tests check
that adding future candles does not change already closed markers.

## Test with your own data

```sh
python run_local.py --input data/my_reviews.json --output output/my_result.json
```

The file can contain one review object or `{"reviews": [review1, review2, ...]}`.
The first review supplies an account. Later reviews automatically use the
previous returned account and state unless explicitly overridden.

Each review has these fields:

| Field | Format |
|---|---|
| `as_of` | Review timestamp with timezone, e.g. `2026-10-13T12:20:00-04:00` |
| `phase` | `open`, `noon` or `prepare`; prepare has no simulated fills |
| `account` | `{"cash": 100000, "positions": {"AAPL": 10.5}}` |
| `universe_date` | Latest completed daily snapshot date |
| `universe` | `{"AAPL": {"rank": 1}, ...}`; rank by trailing liquidity |
| `daily_closes` | `{"SPY": {"2026-10-12": 650.0, ...}, "AAPL": {...}, "SGOV": {...}}` |
| `hourly` | `{"AAPL": [{"t": 1791813600000, "o": 200, "h": 201, "l": 199, "c": 200.5, "v": 100000}, ...]}` |
| `prices` | Quotes actually available at the review: `{"AAPL": 200.5, "SGOV": 100.4, ...}` |
| `labels` | `{"AAPL": {"group": "35", "industry": "3571"}, ...}`; same classification scheme across names |
| `state` | Optional previous returned state; preserve it across reviews |

Use active common stocks/common ADRs in the supplied liquidity universe. SPY
defines the completed-session calendar. Supply at least 253 completed daily
closes for momentum, risk history for every held/add instrument including SGOV,
and enough hourly history to warm up the indicator. Use consistently
split-adjusted prices and volume. `t` is the **hour's start in Unix milliseconds**.

Supply the full candidate universe and all holdings to compare the current
strategy fairly; a two-stock input tests mechanics only. Missing a holding from
the universe means it is outside the eligible universe and may be exited.
The first review initializes at its timestamp, so old +/x markers are ignored.
Start replay before the period being evaluated if positions must accumulate first.

Current/future daily rows and unfinished hourly windows are excluded. The local
wrapper checks stale/mismatched daily dates and invalid/duplicate observed bars.
The caller remains responsible for a truthful completed-session universe and
contemporaneous quotes. This offline package has no exchange-calendar or data
provider connection.

Every result includes the watchlist, qualification reasons, signals, risk report,
proposed stock plan, simulated fill sequence, actual local ledger after those
fills, position weights and reusable state.

## Files and settings

| File | Purpose |
|---|---|
| `stock_strategy/indicator.py` | +/x calculation; indicator variables are at the top |
| `stock_strategy/selection.py` | Momentum ranking, qualification and fresh-marker rules |
| `stock_strategy/risk.py` | Reused portfolio manager and covariance calculations |
| `stock_strategy/planner.py` | Stock plan and combined stock/SGOV risk checks |
| `stock_strategy/allocation.py` | Sales, combined-risk repair, purchases and fund refill |
| `stock_strategy/candles.py` | Closed four-hour grouping and confirmation timestamps |
| `stock_strategy/local.py` | Input validation and local simulated fills |
| `stock_strategy/research_parameters.json` | **Active** stock sizing/risk defaults |
| `stock_strategy/config.py` | Momentum, watchlist, SGOV and data-delay settings |
| `strategy_origin.json` | Source revision and copied-source hashes |

The package applies `research_parameters.json` on import. The generic defaults
inside `risk.py` are overridden by that file; edit the JSON to test different
supported sizing/risk parameters. Edit the variables at the top of `indicator.py`
for indicator changes, and `config.py` for selection/SGOV settings. Restart Python
after edits. Disabled research extensions in the generic manager are not bundled.
The provenance test identifies edits to the copied baseline; update its manifest
deliberately if adopting a new shared baseline.

## Scope and comparison limits

The six copied core files and two extracted functions retain the current
deployed stock rules. Only line endings are normalized; there is no parameter
retuning. Source checksums identify the baseline shared by Alan and Tim.

The local simulation uses supplied review prices, fractional quantities and
**zero fees/slippage**. It sizes from current total account equity, including
SGOV, as the deployed runner does. It has no dividend/corporate-action ledger,
spread model, margin/settlement simulation or full historical performance engine.
The research case name includes `5bps`, but this local fill adapter does not
apply that research cost assumption. Add those components in a real backtest
before comparing return, drawdown or Sharpe with earlier research results.

Data, output reports, account records and credentials are ignored by Git. The
only GitHub workflow runs tests and the synthetic example on Windows and Linux.

See [NOTICE.md](NOTICE.md) for the absorption indicator's MPL-2.0 attribution.
