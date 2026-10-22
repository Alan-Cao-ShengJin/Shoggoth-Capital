# Shoggoth Capital

Systematic stock-trading strategies, kept in one place for review and local testing. Each strategy
has its own folder with its rules written out in plain language, the code that implements them,
tests, and its research results.

Everything here runs locally on historical or synthetic data. Nothing connects to a broker or
places orders. This is research, not financial advice.

## Strategies

| Strategy | What it does | Status |
|---|---|---|
| [MVE: Momentum Volume Efficiency](strategies/mve/README.md) | Builds a quarterly momentum watchlist, buys and trims on confirmed four-hour absorption signals, and manages a diversified, risk-capped portfolio with an SGOV cash sleeve. | Current deployed core |
| [Episodic Pivot (EP)](strategies/episodic_pivot/README.md) | Buys stocks that gap up 10%+ on big news on the opening-range-high break, only while QQQ and IWM are in uptrends, and holds the winners for days to weeks. | Research: five-year backtest, paper trading next |

## Layout

```
README.md            this page
.gitignore           repo-wide: caches, credentials, downloaded data and outputs
.gitattributes       repo-wide: LF line endings everywhere
.github/workflows/   runs every strategy's tests on Windows and Linux
strategies/
  mve/               MVE: code, tests, synthetic demo, source manifest, notice, requirements
  episodic_pivot/    EP: code, tests, Massive.com data source, requirements
```

Each strategy folder is self-contained: its README explains the rules, results and how to run
it, it lists its own Python requirements, and its commands run from inside that folder. Changing
one strategy never touches another.

## Getting started

Use Python 3.11 or later.

```sh
git clone https://github.com/Alan-Cao-ShengJin/Shoggoth-Capital.git
cd Shoggoth-Capital/strategies/<name>
python -m pip install -r requirements.txt
```

Then follow the README in that folder.

## Adding a strategy

1. Create `strategies/<name>/` with a README that states the rules, the results and their
   limits, a `requirements.txt`, a Python package for the code, and `tests/` using synthetic
   data only.
2. Add the strategy to the table above.
3. Add its test steps to `.github/workflows/tests.yml`.

## Data and credentials

API keys go in `.env` files and downloaded data, outputs and reports in `data/` and `output/`
folders; all of these are ignored by Git. Tests never need credentials or network access.

## Licences

MVE's absorption indicator is under the Mozilla Public License 2.0; see
[strategies/mve/NOTICE.md](strategies/mve/NOTICE.md).
