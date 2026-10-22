"""Backtest the Episodic Pivot strategy with Massive.com data.

Run from strategies/episodic_pivot:
python -m episodic_pivot --start 2024-11-01 --end 2025-10-31 --output output/ep_2025.json
"""
from dataclasses import asdict, replace
from pathlib import Path
import argparse
import json
import sys

from .backtest import run
from .massive import MassiveSource
from .rules import Settings


def main(argv=None):
    defaults = Settings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", required=True, help="First trading day, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Last trading day, YYYY-MM-DD")
    parser.add_argument("--output", type=Path, help="Write summary, equity curve, trades and daily log as JSON")
    parser.add_argument("--cache-dir", help="Where downloaded Massive responses are kept (default data/massive_cache)")
    parser.add_argument("--trail", type=int, default=defaults.trail_sma, help="Daily SMA for the trailing exit (10 or 20)")
    parser.add_argument("--min-cap", type=float, default=defaults.min_market_cap, help="Market cap floor in dollars")
    parser.add_argument("--regime", action="store_true", help="Only enter when QQQ's 10-day SMA is above its 20-day and both rise")
    parser.add_argument("--quiet", action="store_true", help="Don't print a line per day")
    args = parser.parse_args(argv)

    settings = replace(defaults, trail_sma=args.trail, min_market_cap=args.min_cap, regime_filter=args.regime)
    source = MassiveSource(args.start, args.end, args.cache_dir)
    log = None if args.quiet else (lambda line: print(line, file=sys.stderr))
    result = run(source, args.start, args.end, settings, log)
    result["settings"] = asdict(settings)
    print(json.dumps(result["summary"], indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Saved {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
