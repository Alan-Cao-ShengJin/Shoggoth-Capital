"""Run the stock rules against supplied data and a local simulated account.

No API, broker credentials, order submission or data downloads are involved.
All fills use the supplied review prices, with zero fees and slippage.
"""
from copy import deepcopy
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_DOWN
from math import isfinite

from .config import CONFIG
from .candles import ET, four_hour_events
from .selection import actions, build_watchlist, next_review, quarter_due
from .planner import stock_plan, combined_risk, equity
from .allocation import execute


def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and isfinite(value)


def completed_hourly(rows, now, ticker):
    """Reject unusable observed candles; ignore data unavailable at this review."""
    result,seen = [],set()
    cutoff = now-timedelta(seconds=CONFIG.market_data_delay_seconds)
    for bar in rows:
        stamp = bar["t"]
        if not finite(stamp):
            raise ValueError(ticker+": invalid candle timestamp.")
        instant = datetime.fromtimestamp(stamp/1000,ET)
        if instant+timedelta(hours=1) > cutoff:
            continue
        if stamp in seen:
            raise ValueError(ticker+": duplicate hourly timestamp.")
        seen.add(stamp)
        if any(not finite(bar[k]) for k in ("o","h","l","c","v")):
            raise ValueError(ticker+": non-finite OHLCV.")
        if not 0 < bar["l"] <= min(bar["o"],bar["c"]) <= max(bar["o"],bar["c"]) <= bar["h"] or bar["v"] < 0:
            raise ValueError(ticker+": invalid candle prices or volume.")
        result.append(bar)
    return result


class LocalLedger:
    """A simple cash/share ledger that implements the allocation interface."""
    def __init__(self, account, prices):
        self.book = deepcopy(account)
        self.prices = prices
        self.broker = self
        self.fills = {}

    def snapshot(self):
        return deepcopy(self.book)

    def latest_trades(self, tickers):
        return {t:self.prices[t] for t in tickers}

    def send(self, ticker, side, key, *, qty=None, notional=None):
        identity = (ticker,side,key)
        if identity in self.fills:
            return dict(self.fills[identity],reused=True)
        price = self.prices[ticker]
        if notional is not None:
            amount = float(Decimal(str(notional)).quantize(Decimal('.01'),rounding=ROUND_DOWN))
            qty = amount/price
        qty = float(Decimal(str(qty)).quantize(Decimal('.000000001'),rounding=ROUND_DOWN))
        amount = qty*price
        if qty <= 0:
            return dict(ticker=ticker,status="skipped",reason="quantity rounds to zero")
        if side == "buy":
            if amount > self.book["cash"]+1e-7:
                raise ValueError("A simulated purchase cannot borrow cash.")
            self.book["cash"] -= amount
            self.book["positions"][ticker] = self.book["positions"].get(ticker,0.)+qty
        elif side == "sell":
            held = self.book["positions"].get(ticker,0.)
            if qty > held+1e-9:
                raise ValueError("A simulated sale cannot create a short position.")
            self.book["cash"] += amount
            remaining = held-qty
            if remaining > 1e-9:
                self.book["positions"][ticker] = remaining
            else:
                self.book["positions"].pop(ticker,None)
        else:
            raise ValueError("Order side must be buy or sell.")
        fill = dict(ticker=ticker,side=side,status="simulated_fill",qty_filled=qty,
            filled_avg_price=price,notional=amount,decision_key=key)
        self.fills[identity] = fill
        return fill


def review(*, account, universe, daily_closes, hourly, prices, labels, as_of,
           state=None, phase="noon", universe_date=None):
    """One review. Return decisions, simulated fills, account and reusable state.

    hourly uses Unix-millisecond start times and o/h/l/c/v candle fields.
    daily_closes is {ticker: {YYYY-MM-DD: close}}; SPY defines the calendar.
    prices must be quotes available at as_of, not future closes.
    Preserve the returned account and state for the next review.
    """
    now = datetime.fromisoformat(as_of.replace("Z","+00:00"))
    if now.utcoffset() is None:
        raise ValueError("as_of must include a timezone.")
    now = now.astimezone(ET)
    if phase not in ("open","noon","prepare"):
        raise ValueError("Use open, noon or prepare.")
    if not finite(account["cash"]) or account["cash"] < 0 or any(not finite(q) or q < 0 for q in account["positions"].values()):
        raise ValueError("Account must have nonnegative cash and long-only finite quantities.")
    account = dict(cash=account["cash"],positions={t:q for t,q in account["positions"].items() if q > 0})
    state = deepcopy(state or {})
    if state.get("last_review") and datetime.fromisoformat(state["last_review"]) >= now:
        raise ValueError("Reviews must move forward; use the previous returned state.")
    state.setdefault("activated_at",now.isoformat())
    state.setdefault("last_signal",{})
    # Remove current/future daily rows before computing ANY qualification/risk.
    cutoff = now.date().isoformat()
    closes = {t:{d:p for d,p in rows.items() if d < cutoff} for t,rows in daily_closes.items()}
    calendar = sorted(closes.get("SPY",{}))
    if not calendar:
        raise ValueError("Completed SPY daily closes are required.")
    data_day = calendar[-1]
    if universe_date is not None and universe_date != data_day:
        raise ValueError("Universe date and the latest completed SPY date must agree.")
    if (now.date()-date.fromisoformat(data_day)).days > CONFIG.data_age_days:
        raise ValueError("Completed daily data is stale.")
    if quarter_due(state,data_day):
        state["watchlist"] = build_watchlist(universe,closes,calendar)
        state["watchlist_date"] = data_day
    held = {t:q for t,q in account["positions"].items() if q > 0 and t != CONFIG.fund}
    names = set(state["watchlist"])|set(held)
    events = {t:four_hour_events(completed_hourly(hourly.get(t,[]),now,t),now) for t in names}
    signals,consumed,reasons = actions(universe,closes,calendar,state["watchlist"],held,events,state,now)
    required = set(account["positions"])|{t for t,s in signals.items() if s["action"] == "add"}|{CONFIG.fund}
    if required-set(prices):
        raise ValueError("Missing review prices: "+", ".join(sorted(required-set(prices))))
    if any(not isinstance(p,(int,float)) or isinstance(p,bool) or not isfinite(p) or p <= 0 for p in prices.values()):
        raise ValueError("Review prices must be positive finite numbers.")
    # Candidates that are only on hold need no execution price.
    signals = {t:s for t,s in signals.items() if t in prices or t in account["positions"]}
    plan = stock_plan(account,signals,prices,closes,labels,now.isoformat())
    try:
        account_risk = combined_risk(account,prices,closes,now.isoformat())
    except (ValueError,OverflowError,KeyError) as exc:
        account_risk = dict(available=False,reason=str(exc))
    result = dict(mode="local_simulation",as_of=now.isoformat(),phase=phase,
        watchlist=state["watchlist"],watchlist_date=state["watchlist_date"],
        next_watchlist_review=next_review(state["watchlist_date"]),signals=signals,
        qualification=reasons,confirmed_events=events,plan=plan,
        whole_account_risk_before=account_risk,
        assumptions="Supplied contemporaneous prices; fractional shares; zero fees/slippage; no dividends/corporate-action engine; no broker orders.")
    ledger = LocalLedger(account,prices)
    fills = []
    if phase != "prepare":
        fills = execute(ledger,plan,prices,closes,labels,now.isoformat(),phase,consumed)
        state["last_signal"].update(consumed)
        state["last_review"] = now.isoformat()
    result.update(fills=fills,account=ledger.snapshot(),state=state)
    result["equity"] = equity(result["account"],prices)
    result["cash_weight"] = result["account"]["cash"]/result["equity"]
    result["weights"] = {t:q*prices[t]/result["equity"] for t,q in result["account"]["positions"].items()}
    return result
