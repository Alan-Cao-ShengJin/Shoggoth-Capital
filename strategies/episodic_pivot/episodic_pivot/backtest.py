"""Day-by-day portfolio backtest of the Episodic Pivot rules over any data source.

A data source provides (see massive.py and the synthetic one in tests):
  trading_days(start, end) -> ["YYYY-MM-DD", ...]
  previous_trading_day(day) -> "YYYY-MM-DD"
  gappers(day, prev_day, settings) -> [(ticker, prev_close), ...]   prefiltered by price, gap, prior-day $ volume
  market_cap(ticker, day) -> float or None
  daily(ticker) -> [{date, o, h, l, c, v}, ...] in date order
  intraday(ticker, day) -> 5-minute bars 04:00-16:00 ET with `min` set, in time order
  optional cash_merger(ticker, day) -> verified pre-market cash entitlement or None
"""
from math import sqrt

from .rules import REGULAR_OPEN, Position, daily_stats, entry_signal, market_ok, position_size, sma


def run(source, start, end, settings, log=None):
    days = source.trading_days(start, end)
    if not days:
        raise ValueError(f"No trading data in {start}..{end}")
    prev_day = source.previous_trading_day(days[0])
    index = {}

    def rows(t):
        if t not in index:
            r = source.daily(t)
            index[t] = (r, {x["date"]: i for i, x in enumerate(r)})
        return index[t]

    open_, closed, curve, day_log = [], [], [], []
    realized = 0.0

    def finish(position, day, *, terminal=False, cash_merger=None):
        nonlocal realized
        result = position.result(day)
        if terminal:
            result["still_open"] = True
        if cash_merger is not None:
            result["cash_merger"] = cash_merger
        realized += result["pnl"]
        closed.append(result)
        open_.remove(position)

    for day in days:
        carried = list(open_)
        stop_bars = {}
        peak_open = len(open_)

        # Daily data can tell us whether intraday stop data must be fetched, but
        # only observed opens/bars may release capacity before today's close.
        for p in carried:
            p.days_held += 1
            lookup_merger = getattr(source, "cash_merger", None)
            event = lookup_merger(p.symbol, day) if lookup_merger is not None else None
            if event is not None:
                p.cash_merger(event["cash_per_share"], day)
                finish(p, day, cash_merger=event)
                continue
            r, idx = rows(p.symbol)
            i = idx.get(day)
            if i is None:
                raise ValueError(f"Missing daily price for held {p.symbol} on {day}")
            bar = r[i]
            if bar["o"] <= p.stop:
                p.stop_bar({"o": bar["o"], "l": bar["o"]}, day)
                finish(p, day)
            elif bar["l"] <= p.stop:
                intraday = source.intraday(p.symbol, day)
                stop_bars[p.symbol] = {b["min"]: b for b in intraday if REGULAR_OPEN <= b["min"] < 960}
                if not stop_bars[p.symbol]:
                    raise ValueError(f"Missing intraday stop data for {p.symbol} on {day}")

        candidates = source.gappers(day, prev_day, settings)
        allowed = settings.market_filter == "off" or market_ok(
            settings, *((rows(t)[0], rows(t)[1].get(day)) for t in ("QQQ", "IWM")))
        outcomes = []
        for ticker, prev_close in (candidates if allowed else []):
            outcomes.append((ticker, *_signal(source, settings, rows, ticker, day, prev_close)))
        signals = sorted((o for o in outcomes if o[1] == "signal"), key=lambda o: (o[2]["bar"]["min"], -o[2]["vol_ratio"]))
        entries, skipped = [], [f"{t}: {why}" for t, kind, why in outcomes if kind == "skip"]
        by_minute = {}
        for ticker, _, sig in signals:
            by_minute.setdefault(sig["bar"]["min"], []).append((ticker, sig))

        for minute in range(REGULAR_OPEN, 960, 5):
            # A stop crossed at a candle's open is known before new entries.
            for p in list(open_):
                bar = stop_bars.get(p.symbol, {}).get(minute)
                if bar is not None and bar["o"] <= p.stop:
                    p.stop_bar({"o": bar["o"], "l": bar["o"]}, day)
                    finish(p, day)

            for ticker, sig in by_minute.get(minute, []):
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
                open_.append(Position(settings, ticker, day, sig, qty))
                stop_bars[ticker] = {b["min"]: b for b in sig["rest"]}
                entries.append(ticker)
                peak_open = max(peak_open, len(open_))

            # Reserve capacity throughout this candle. When the ordering of its
            # high and low is unknown, assume an entry was followed by its stop.
            for p in list(open_):
                bar = stop_bars.get(p.symbol, {}).get(minute)
                if bar is not None and p.stop_bar(bar, day, check_open=False):
                    finish(p, day)

        # Close-based sales and stop changes cannot affect earlier entries.
        for p in carried:
            if p not in open_:
                continue
            r, idx = rows(p.symbol)
            i = idx[day]
            if r[i]["l"] <= p.stop:
                raise ValueError(f"Daily stop not covered by intraday data for {p.symbol} on {day}")
            if p.close_day(r[i], sma(r, i, settings.trail_sma), day):
                finish(p, day)

        # Liquidate with the same execution costs as every other fill, before
        # the final curve point so it reconciles to the trade-level summary.
        if day == days[-1]:
            for p in list(open_):
                r, idx = rows(p.symbol)
                last = r[idx[day]]
                p.sell(last["c"] - settings.slippage, p.qty, "still-open", day)
                finish(p, day, terminal=True)

        # The curve stores cumulative P&L, as in the original report format.
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
        day_log.append({"date": day, "candidates": len(candidates), "market_ok": allowed, "entries": entries,
                        "no_entry": skipped, "peak_open": peak_open})
        if log:
            log(f"{day}  {len(candidates):3d} gappers  +{len(entries)} entries  open {len(open_)}  equity {realized + unreal:.0f}")
        prev_day = day

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
