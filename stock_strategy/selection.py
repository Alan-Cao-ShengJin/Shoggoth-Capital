"""Quarterly entry pool; completed daily qualification for owned stocks."""
from calendar import monthrange
from datetime import date, datetime
from math import isfinite
from .config import CONFIG


def next_review(day):
    old = date.fromisoformat(day)
    offset = old.year*12+old.month-1+CONFIG.review_months
    year, month = offset//12,offset%12+1
    return date(year,month,min(old.day,monthrange(year,month)[1])).isoformat()


def quarter_due(state, data_day):
    return not state.get("watchlist_date") or data_day >= next_review(state["watchlist_date"])


def qualification(ticker, universe, closes, calendar):
    row = universe.get(ticker)
    if row is None:
        return False,"outside the completed common-stock universe",None
    if row["rank"] > CONFIG.max_liquidity_rank:
        return False,"liquidity rank above500",None
    data_day = calendar[-1]
    values = closes.get(ticker,{})
    price = values.get(data_day)
    if price is None or not isfinite(price) or price <= 0:
        return None,"missing completed close",None
    if price < CONFIG.min_price:
        return False,"price below5USD",None
    needed = CONFIG.momentum_window+CONFIG.momentum_skip+1
    days = calendar[-needed:]
    if len(days) != needed or any(values.get(d,0) <= 0 or not isfinite(values.get(d,0)) for d in days):
        return None,"incomplete long momentum history",None
    momentum = values[days[-CONFIG.momentum_skip-1]]/values[days[0]]-1
    if momentum <= 0:
        return False,"long momentum is not positive",momentum
    recent = calendar[-CONFIG.recent_window-1:]
    market = closes.get("SPY",{})
    if len(recent) != CONFIG.recent_window+1 or any(values.get(d,0) <= 0 or market.get(d,0) <= 0 for d in recent):
        return None,"incomplete recent stock/SPY history",momentum
    change = values[recent[-1]]/values[recent[0]]-1
    spy = market[recent[-1]]/market[recent[0]]-1
    if change <= 0 or change <= spy:
        return False,"recent63-session return is not positive and aboveSPY",momentum
    return True,"momentum and basic filters pass",momentum


def build_watchlist(universe, closes, calendar):
    # The entry pool ranks positive LONG momentum; the recent guard is checked
    # at each actual purchase, not used to silently rewrite the frozen pool.
    needed = CONFIG.momentum_window+CONFIG.momentum_skip+1
    days = calendar[-needed:]
    if len(days) != needed:
        raise ValueError("Insufficient completed sessions for quarterly momentum.")
    ranked = []
    for ticker,row in universe.items():
        values = closes.get(ticker,{})
        if row["rank"] > CONFIG.max_liquidity_rank or values.get(days[-1],0) < CONFIG.min_price:
            continue
        if any(values.get(d,0) <= 0 or not isfinite(values.get(d,0)) for d in days):
            continue
        score = values[days[-CONFIG.momentum_skip-1]]/values[days[0]]-1
        if score > 0:
            ranked.append((ticker,score))
    return [t for t,_ in sorted(ranked,key=lambda r:(-r[1],r[0]))[:CONFIG.pool_size]]


def actions(universe, closes, calendar, watchlist, held, events, state, now):
    # Missing data retains an owned stock and blocks adding to it. A known
    # failure exits it even when a + also exists. Being outside the new pool
    # alone does not force a suitable holding out.
    result, consumed, explanations = {},{},{}
    floor = datetime.fromisoformat(state["activated_at"])
    if state.get("last_review"):
        floor = max(floor,datetime.fromisoformat(state["last_review"]))
    for ticker in sorted(set(watchlist)|set(held)):
        eligible,reason,momentum = qualification(ticker,universe,closes,calendar)
        explanations[ticker] = reason
        action = "exit" if ticker in held and eligible is False else "hold"
        known = state.get("last_signal",{}).get(ticker)
        cutoff = max(floor,datetime.fromisoformat(known)) if known else floor
        fresh = [e for e in events.get(ticker,[]) if cutoff < datetime.fromisoformat(e["confirmed_at"]) <= now]
        if fresh:
            event = max(fresh,key=lambda e:datetime.fromisoformat(e["confirmed_at"]))
            consumed[ticker] = event["confirmed_at"]
            if eligible is True and (ticker in held or ticker in watchlist):
                action = "add" if event["signal"] == 1 else "trim"
        if ticker in held and eligible is False:
            action = "exit"
        result[ticker] = dict(action=action,eligible=eligible is not False,momentum_score=momentum,
            needs_data_review=eligible is None)
        if eligible is None and result[ticker]["action"] == "add":
            result[ticker]["action"] = "hold"
    return result,consumed,explanations
