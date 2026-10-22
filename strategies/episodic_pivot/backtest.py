"""Day-by-day portfolio backtest of the Episodic Pivot rules over any data source.

A data source provides (see massive.py and the synthetic one in tests):
  trading_days(start, end) -> ["YYYY-MM-DD", ...]
  previous_trading_day(day) -> "YYYY-MM-DD"
  gappers(day, prev_day, settings) -> [(ticker, prev_close), ...]   prefiltered by price, gap, prior-day $ volume
  market_cap(ticker, day) -> float or None
  daily(ticker) -> [{date, o, h, l, c, v}, ...] in date order
  intraday(ticker, day) -> 5-minute bars 04:00-16:00 ET with `min` set, in time order
"""
from math import sqrt

from .rules import Position, daily_stats, entry_signal, position_size, regime_ok, sma


def run(source, start, end, settings, log=None):
    days = source.trading_days(start, end)
    prev_day = source.previous_trading_day(days[0])
    index = {}

    def rows(t):
        if t not in index:
            r = source.daily(t)
            index[t] = (r, {x["date"]: i for i, x in enumerate(r)})
        return index[t]

    open_, closed, curve, day_log = [], [], [], []
    realized = 0.0

    for day in days:
        # 1) Positions bought on earlier days, managed on today's daily bar.
        for p in list(open_):
            r, idx = rows(p.symbol)
            i = idx.get(day)
            if i is None:
                continue
            if p.daily(r[i], sma(r, i, settings.trail_sma), day):
                res = p.result(day)
                realized += res["pnl"]
                closed.append(res)
                open_.remove(p)

        # 2) New entries.
        candidates = source.gappers(day, prev_day, settings)
        market_ok = not settings.regime_filter or regime_ok(rows("QQQ")[0], rows("QQQ")[1].get(day))
        outcomes = []
        for ticker, prev_close in (candidates if market_ok else []):
            outcomes.append((ticker, *_signal(source, settings, rows, ticker, day, prev_close)))
        signals = sorted((o for o in outcomes if o[1] == "signal"), key=lambda o: (o[2]["bar"]["min"], -o[2]["vol_ratio"]))
        entries, skipped = [], [f"{t}: {why}" for t, kind, why in outcomes if kind == "skip"]
        for ticker, _, sig in signals:
            if len(open_) >= settings.max_open:
                skipped.append(f"{ticker}: {settings.max_open} positions already open")
                continue
            if any(p.symbol == ticker for p in open_):
                skipped.append(f"{ticker}: already holding")
                continue
            qty = position_size(settings, sig["fill"], sig["stop"])
            if qty < 1:
                skipped.append(f"{ticker}: under 1 share")
                continue
            p = Position(settings, ticker, day, sig, qty)
            p.entry_day(sig["rest"], day)
            open_.append(p)
            entries.append(ticker)
            if p.qty <= 0:
                res = p.result(day)
                realized += res["pnl"]
                closed.append(res)
                open_.remove(p)

        # 3) Mark to market at the close.
        unreal = 0.0
        for p in open_:
            r, idx = rows(p.symbol)
            i = idx.get(day)
            if i is None:
                earlier = [j for d, j in idx.items() if d <= day]
                i = max(earlier) if earlier else None
            if i is not None:
                unreal += p.qty * r[i]["c"] + p.proceeds - p.cost
        curve.append({"date": day, "equity": round(realized + unreal, 2), "open": len(open_)})
        day_log.append({"date": day, "candidates": len(candidates), "entries": entries, "no_entry": skipped})
        if log:
            log(f"{day}  {len(candidates):3d} gappers  +{len(entries)} entries  open {len(open_)}  equity {realized + unreal:.0f}")
        prev_day = day

    # Anything still open is closed at the last close and flagged.
    for p in list(open_):
        r, _ = rows(p.symbol)
        last = [x for x in r if x["date"] <= days[-1]][-1]
        p.sell(last["c"], p.qty, "still-open", last["date"])
        res = p.result(last["date"])
        res["still_open"] = True
        realized += res["pnl"]
        closed.append(res)

    return {"summary": summarize(closed, curve, days, settings), "equity_curve": curve, "trades": closed, "days": day_log}


def _signal(source, settings, rows, ticker, day, prev_close):
    cap = source.market_cap(ticker, day)
    if settings.min_market_cap and not (cap is not None and cap >= settings.min_market_cap):
        return "skip", "below market cap floor"
    r, idx = rows(ticker)
    i = idx.get(day)
    if i is None or i < 21:
        return "skip", "not enough daily history"
    adr, adv = daily_stats(r, i)
    if adv * r[i - 1]["c"] < settings.min_dollar_volume:
        return "skip", "illiquid (20-day dollar volume)"
    return entry_signal(settings, source.intraday(ticker, day), prev_close, adr, adv)


def summarize(trades, curve, days, settings):
    pnls = [t["pnl"] for t in trades]
    n = len(pnls)
    total = sum(pnls)
    mean = total / n if n else 0.0
    sd = sqrt(sum((x - mean) ** 2 for x in pnls) / (n - 1)) if n > 1 else 0.0
    gross_win = sum(x for x in pnls if x > 0)
    gross_loss = -sum(x for x in pnls if x <= 0)
    peak = max_dd = 0.0
    for c in curve:
        peak = max(peak, c["equity"])
        max_dd = max(max_dd, peak - c["equity"])
    top3 = sum(sorted(pnls, reverse=True)[:3])
    return {
        "period": f"{days[0]}..{days[-1]}", "trading_days": len(days), "trades": n,
        "wins": sum(1 for x in pnls if x > 0), "win_rate_pct": round(100 * sum(1 for x in pnls if x > 0) / n, 1) if n else None,
        "net_pnl": round(total, 2), "return_pct": round(100 * total / settings.account, 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_r": round(sum(t["r_multiple"] for t in trades) / n, 2) if n else None,
        "best_r": max((t["r_multiple"] for t in trades), default=None),
        "t_stat": round(mean / (sd / sqrt(n)), 2) if sd > 0 else None,
        "max_drawdown": round(max_dd, 2), "max_drawdown_pct": round(100 * max_dd / settings.account, 1),
        "net_without_top3": round(total - top3, 2),
        "avg_days_held": round(sum(t["days_held"] for t in trades) / n, 1) if n else None,
        "still_open_at_end": sum(1 for t in trades if t.get("still_open")),
    }
