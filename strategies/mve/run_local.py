"""Run a synthetic example or your own JSON reviews locally, with no broker."""
from pathlib import Path
import argparse
import json
from stock_strategy.local import review
from examples.demo import demo_reviews


def replay(reviews):
    results,account,state = [],None,None
    for payload in reviews:
        payload = dict(payload)
        payload.setdefault("account",account)
        payload.setdefault("state",state)
        if payload["account"] is None:
            raise ValueError("The first review needs a cash/positions account.")
        result = review(**payload)
        results.append(result)
        account,state = result["account"],result["state"]
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo",action="store_true",help="Use clearly labelled synthetic data")
    source.add_argument("--input",type=Path,help="One JSON review or {reviews: [...]} with your data")
    parser.add_argument("--output",type=Path,help="Write the complete decision/account report")
    args = parser.parse_args(argv)
    if args.demo:
        inputs = demo_reviews()
    else:
        value = json.loads(args.input.read_text(encoding="utf-8"))
        inputs = value["reviews"] if "reviews" in value else [value]
    results = replay(inputs)
    print("LOCAL SIMULATION | "+("SYNTHETIC DATA, not a profitability backtest" if args.demo else "user-supplied data, zero fees/slippage"))
    for result in results:
        fills = [f for f in result["fills"] if f.get("status") == "simulated_fill"]
        print(f"{result['as_of']} {result['phase']}: {len(result['watchlist'])} watchlist names; {len(fills)} simulated fills; cash {result['cash_weight']:.2%}")
        for fill in fills:
            print(f"  {fill['side']} {fill['ticker']}: ${fill['notional']:.2f}")
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(dict(mode="local_simulation",synthetic=args.demo,
            results=results),indent=2,allow_nan=False)+"\n",encoding="utf-8")
        print("Report:",args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
