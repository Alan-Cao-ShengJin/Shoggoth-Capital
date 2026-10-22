"""Plan a long-only portfolio, one add/trim stage per new decision time.

This file does not generate trading signals or send orders to a broker.
It returns proposed orders and a simulated next account, assuming all orders
fill at the supplied prices with no fees or slippage. Keep the old account
until real fills are known; simulated shares are not proof of a real fill.

Inputs to manage_portfolio(account, signals, prices, daily_closes, as_of):
    account: {"cash": dollars, "positions": {ticker: fractional_shares}}
    signals: {ticker: {"action": "add" / "trim" / "hold" / "exit",
                       "eligible": True / False}}
    prices: {ticker: current_price_at_decision_time}
    daily_closes: {ticker: {"YYYY-MM-DD": closing_price}}
    as_of: a timezone-aware ISO timestamp, later than account's last as_of.

Optional signal fields: target_weight (0.10 means 10% of the portfolio),
max_add_notional (maximum reference-price dollars to buy on this review),
expected_annual_return (your forecast, not one invented by this tool).
classification: {"group": business_group, "industry": detailed_industry}.
Alternatively pass classifications={ticker: classification} to manage_portfolio.
Industry checks activate when a mapping or signal labels are supplied; legacy
unlabelled calls report them inactive. Missing held-stock labels block buys.
Missing signals mean hold. eligible=False means exit even without an exit action.
Risk limits apply to holds too. Only completed prior ET dates enter the model.

Risk contribution follows Bruder & Roncalli (2012), not an optimizer copied
from that paper. The stages, thresholds and selection rules are our v1 choices.
"""

# HOW MANY STOCKS, AND HOW MUCH MONEY IN EACH?
TARGET_POSITIONS = 20            # Maximum count; never force unsuitable entries.
POSITION_CAP = 0.20              # Hard ceiling. The code rejects anything above 20%.
RESEARCH_REMOVE_POSITION_CAP = False  # Explicit opt-in for the user's uncapped experiment.
CASH_RESERVE = 0.05              # Keep at least 5% as cash; more cash is allowed.
BUY_CASH_BUFFER = None          # None uses CASH_RESERVE; optionally keep more for new buys.
NORMAL_TARGET_WEIGHT = None     # None splits investable money across TARGET_POSITIONS.
ENTRY_PRIORITY = "correlation"  # Alternatives include "risk", "momentum" and research "trend_quality".
BUY_PRIORITY_LOOKBACK = 90     # Research trend ranking: completed prior daily closes.
ADD_STEP = 0.01                  # Each new add signal buys up to 1% of account value.
INITIAL_ADD_STEP = None          # Optional smaller first purchase; later additions use ADD_STEP.
TRIM_STEP = 0.01                 # Each new trim signal sells up to 1% of account value.
TRIM_RULE = "fixed"             # Alternatives: "gentle" or "staged".
TRIM_FRACTION = 0.10             # Gentle trim: sell 10% of the existing holding...
GENTLE_TRIM_CAP = 0.005          # ...but no more than 0.5% of account value.
STAGED_TRIM_FRACTIONS = (1/4, 1/3, 1/2, 1.)  # Sell these fractions of REMAINING shares on successive x signals.
RESET_STAGED_TRIM_ON_BUY = True # An actual new purchase restarts the staged selling sequence.
PARTIAL_ADD_DECREMENT = 0.001    # Research option: retry in 0.1% account steps.
MIN_PARTIAL_ADD = 0.001          # Do not create a research stage smaller than 0.1%.
MIN_ENTRY_WEIGHT = 0.0          # Optional minimum for a NEW position; small top-ups remain allowed.
ADD_RETRY_SESSIONS = 0          # Legal research engine: keep a blocked+ instruction this many sessions;0 discards it.
PARTIAL_STAGE_SESSIONS = 0      # Legal research engine: finish a started buying stage within this window;0 disables it.
REALLOCATE_RISK_ON_ADD = False  # Research only: a small sale may make room for a fresh+ purchase.
REALLOCATION_WEAKER_MOMENTUM_ONLY = False  # Only sell holdings with known momentum below the new purchase.

# HOW DO WE MEASURE STOCKS MOVING TOGETHER?
LONG_WINDOW = 126               # About six months of daily price changes.
SHORT_WINDOW = 20               # Also check roughly one month for recent changes.
MIN_HISTORY = 60                # At least 60 matching daily changes before adding.
MAX_HISTORY_AGE_DAYS = 7         # Block additions if usable history is stale.
TRADING_DAYS = 252              # Convert daily risk estimates to annual estimates.
COVARIANCE_ESTIMATOR = "sample" # Research alternative: "ledoit_wolf" smooths noisy risk estimates.
RISK_MEASURE = "sample_volatility" # Research: "scaled_loss_deviation" budgets portfolio losing days.
HIGH_CORRELATION = 0.80         # Link stocks that have moved very similarly.
CORRELATED_GROUP_CAP = 0.35     # Linked stocks together may occupy at most 35%.
INDUSTRY_GROUP_CAP = 0.20       # With business labels supplied, one broad group <=20%.
INDUSTRY_CAP = 0.10             # One detailed industry <=10%, including price growth.
MAX_PER_INDUSTRY_GROUP = 4      # Do not open a fifth name in one business group.
MAX_PER_INDUSTRY = 2            # Do not open a third name in one detailed industry.

# HOW MUCH ESTIMATED RISK IS ALLOWED?
PORTFOLIO_VOL_CAP = 0.12         # At most 12% estimated annual portfolio volatility.
POSITION_RISK_CAP = 0.03        # One stock contributes at most 3 annual risk points.
RISK_BUDGET_MODE = "fixed"      # Research: "market_volatility" permits more risk in calmer markets.
RISK_BUDGET_MAX_SCALE = 1.25    # Research allowance can rise by no more than 25%.
RISK_REPAIR_BUFFER = 1.0        # After a breach, 0.95 repairs to 95% of the binding limit.
HOLD_RISK_MULTIPLIER = 1.0      # Optional gap: hold risk may drift above the buy budget.
RISK_REPAIR_MODE = "proportional" # Research option: "selective" first trims the stock/group causing a breach.
DRAWDOWN_EXIT = 0.15            # At a 15% fall from recorded peak, exit and pause buys.

from datetime import date, datetime
from math import fsum, isfinite, sqrt
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
EPSILON = 1e-12                  # Allow only tiny computer-rounding differences.


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def timestamp(value):
    # Require a timezone so "today" has one unambiguous meaning.
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("as_of must include a timezone, such as -04:00 or +00:00.")
    return result


def check_settings():
    # Reject bad edits at the top instead of quietly weakening the hard ceiling.
    if not isinstance(TARGET_POSITIONS, int) or isinstance(TARGET_POSITIONS, bool) or TARGET_POSITIONS < 1:
        raise ValueError("TARGET_POSITIONS must be a positive whole number.")
    if type(RESEARCH_REMOVE_POSITION_CAP) is not bool:
        raise ValueError("RESEARCH_REMOVE_POSITION_CAP must be a boolean.")
    if RESEARCH_REMOVE_POSITION_CAP:
        if POSITION_CAP != 1.0:
            raise ValueError("The uncapped research flag requires POSITION_CAP=1; no borrowing remains allowed.")
    elif not number(POSITION_CAP) or not 0 < POSITION_CAP <= 0.20:
        raise ValueError("POSITION_CAP must be positive and cannot exceed 20%.")
    if not number(CASH_RESERVE) or not 0 <= CASH_RESERVE < 1:
        raise ValueError("CASH_RESERVE must be between 0 and less than 1.")
    if BUY_CASH_BUFFER is not None and (not number(BUY_CASH_BUFFER) or not CASH_RESERVE <= BUY_CASH_BUFFER < 1):
        raise ValueError("BUY_CASH_BUFFER must be None or between the cash floor and less than 1.")
    if NORMAL_TARGET_WEIGHT is not None and (not number(NORMAL_TARGET_WEIGHT) or not 0 < NORMAL_TARGET_WEIGHT <= POSITION_CAP):
        raise ValueError("NORMAL_TARGET_WEIGHT must be None or positive and at most the position cap.")
    if not number(MIN_ENTRY_WEIGHT) or not 0 <= MIN_ENTRY_WEIGHT <= POSITION_CAP:
        raise ValueError("MIN_ENTRY_WEIGHT must be between zero and the position cap.")
    if INITIAL_ADD_STEP is not None and (not number(INITIAL_ADD_STEP) or not 0 < INITIAL_ADD_STEP <= min(ADD_STEP, POSITION_CAP)):
        raise ValueError("INITIAL_ADD_STEP must be None or positive and no larger than ADD_STEP and the position cap.")
    if ENTRY_PRIORITY not in ("correlation","risk","momentum","momentum_weighted","momentum_per_risk","trend_quality"):
        raise ValueError("ENTRY_PRIORITY must be correlation, risk, momentum, momentum_weighted, momentum_per_risk or trend_quality.")
    if type(BUY_PRIORITY_LOOKBACK) is not int or BUY_PRIORITY_LOOKBACK < 3:
        raise ValueError("BUY_PRIORITY_LOOKBACK must be a whole number of at least three.")
    if ENTRY_PRIORITY == "trend_quality" and REALLOCATE_RISK_ON_ADD:
        raise ValueError("Trend-quality ranking and risk reallocation are separate experiments.")
    if RISK_REPAIR_MODE not in ("proportional", "selective"):
        raise ValueError("RISK_REPAIR_MODE must be proportional or selective.")
    if COVARIANCE_ESTIMATOR not in ("sample", "ledoit_wolf"):
        raise ValueError("COVARIANCE_ESTIMATOR must be sample or ledoit_wolf.")
    if RISK_MEASURE not in ("sample_volatility", "scaled_loss_deviation"):
        raise ValueError("RISK_MEASURE must be sample_volatility or scaled_loss_deviation.")
    if RISK_MEASURE == "scaled_loss_deviation" and (COVARIANCE_ESTIMATOR != "sample" or RISK_BUDGET_MODE != "fixed" or REALLOCATE_RISK_ON_ADD):
        raise ValueError("Loss-risk research requires sample correlations,fixed budgets and no reallocation.")
    if type(ADD_RETRY_SESSIONS) is not int or ADD_RETRY_SESSIONS < 0:
        raise ValueError("ADD_RETRY_SESSIONS must be a nonnegative whole number.")
    if type(PARTIAL_STAGE_SESSIONS) is not int or PARTIAL_STAGE_SESSIONS < 0:
        raise ValueError("PARTIAL_STAGE_SESSIONS must be a nonnegative whole number.")
    if ADD_RETRY_SESSIONS and PARTIAL_STAGE_SESSIONS:
        raise ValueError("Blocked-add retries and partial-stage completion are separate experiments.")
    if type(REALLOCATE_RISK_ON_ADD) is not bool:
        raise ValueError("REALLOCATE_RISK_ON_ADD must be True or False.")
    if type(REALLOCATION_WEAKER_MOMENTUM_ONLY) is not bool:
        raise ValueError("REALLOCATION_WEAKER_MOMENTUM_ONLY must be True or False.")
    if REALLOCATION_WEAKER_MOMENTUM_ONLY and not REALLOCATE_RISK_ON_ADD:
        raise ValueError("Weaker-momentum reallocation requires REALLOCATE_RISK_ON_ADD.")
    for value in (ADD_STEP, TRIM_STEP, CORRELATED_GROUP_CAP, DRAWDOWN_EXIT):
        if not number(value) or not 0 < value <= 1:
            raise ValueError("Stage sizes, group cap and drawdown limit must be positive fractions <= 1.")
    for value in (PORTFOLIO_VOL_CAP, POSITION_RISK_CAP):
        if not number(value) or value <= 0:
            raise ValueError("Risk limits must be positive numbers.")
    if RISK_BUDGET_MODE not in ("fixed", "market_volatility"):
        raise ValueError("RISK_BUDGET_MODE must be fixed or market_volatility.")
    if not number(RISK_BUDGET_MAX_SCALE) or not 1 <= RISK_BUDGET_MAX_SCALE <= 1.25:
        raise ValueError("RISK_BUDGET_MAX_SCALE must be between 1 and 1.25.")
    if RISK_BUDGET_MODE != "fixed" and REALLOCATE_RISK_ON_ADD:
        raise ValueError("Market-volatility budgets and risk reallocation are separate experiments.")
    if not number(RISK_REPAIR_BUFFER) or not 0 < RISK_REPAIR_BUFFER <= 1:
        raise ValueError("RISK_REPAIR_BUFFER must be positive and at most 1.")
    if not number(HOLD_RISK_MULTIPLIER) or HOLD_RISK_MULTIPLIER < 1:
        raise ValueError("HOLD_RISK_MULTIPLIER must be at least 1.")
    for value in (LONG_WINDOW, SHORT_WINDOW, MIN_HISTORY, MAX_HISTORY_AGE_DAYS, TRADING_DAYS):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("History sizes and trading days must be positive whole numbers.")
    if not 2 <= SHORT_WINDOW <= MIN_HISTORY <= LONG_WINDOW:
        raise ValueError("Require 2 <= SHORT_WINDOW <= MIN_HISTORY <= LONG_WINDOW.")
    if not number(HIGH_CORRELATION) or not 0 < HIGH_CORRELATION <= 1:
        raise ValueError("HIGH_CORRELATION must be positive and no greater than 1.")


def market_risk_budget(daily_closes, as_of):
    # Use yesterday or earlier SPY closes. Calm means the recent price changes
    # are smaller than the longer history. Missing history gives no extra risk.
    cutoff = timestamp(as_of).astimezone(ET).date()
    history = daily_closes.get("SPY", {})
    days = sorted(d for d in history if date.fromisoformat(d) < cutoff)[-(LONG_WINDOW+1):]
    budget = {"mode": RISK_BUDGET_MODE, "available": False, "scale": 1.,
              "portfolio_vol_cap": PORTFOLIO_VOL_CAP, "position_risk_cap": POSITION_RISK_CAP,
              "first_date": days[0] if days else None, "last_date": days[-1] if days else None,
              "long_window": LONG_WINDOW, "short_window": SHORT_WINDOW}
    if len(days) != LONG_WINDOW+1 or any(not number(history[d]) or history[d] <= 0 for d in days):
        budget["reason"] = "Incomplete or invalid SPY history; use base risk budgets."
        return budget
    if (cutoff-date.fromisoformat(days[-1])).days > MAX_HISTORY_AGE_DAYS:
        budget["reason"] = "Stale SPY history; use base risk budgets."
        return budget
    changes = [history[b]/history[a]-1 for a,b in zip(days, days[1:])]
    if any(not number(x) for x in changes):
        budget["reason"] = "Invalid SPY price changes; use base risk budgets."
        return budget
    def volatility(values):
        mean = fsum(values)/len(values)
        return sqrt(fsum((x-mean)**2 for x in values)/(len(values)-1)*TRADING_DAYS)
    long_vol, short_vol = volatility(changes), volatility(changes[-SHORT_WINDOW:])
    ratio = long_vol/short_vol if short_vol else (RISK_BUDGET_MAX_SCALE if long_vol else 1.)
    scale = min(RISK_BUDGET_MAX_SCALE, max(1., ratio))
    budget.update(available=True, scale=scale, long_volatility=long_vol, short_volatility=short_vol,
                  portfolio_vol_cap=PORTFOLIO_VOL_CAP*scale, position_risk_cap=POSITION_RISK_CAP*scale)
    return budget


def build_risk_model(daily_closes, tickers, as_of):
    # 1. Turn each stock's past daily closing prices into percentage changes.
    #    Ignore the decision's current ET date: that day may still be developing.
    cutoff = timestamp(as_of).astimezone(ET).date()
    changes = {}
    for ticker in sorted(tickers):
        history = daily_closes.get(ticker, {})
        days = sorted(day for day in history if date.fromisoformat(day) < cutoff)
        if any(not number(history[day]) or history[day] <= 0 for day in days):
            raise ValueError(f"{ticker}: historical closing prices must be positive finite numbers.")
        changes[ticker] = {}
        for previous, current in zip(days, days[1:]):
            change = history[current] / history[previous] - 1
            if not number(change):
                raise ValueError(f"{ticker}: unusable daily price change.")
            # Match BOTH dates, so a two-day move is never compared with a one-day move.
            changes[ticker][(previous, current)] = change

    budget = {"risk_budget": market_risk_budget(daily_closes, as_of)} if RISK_BUDGET_MODE != "fixed" else {}
    if not changes:
        return {"windows": {}, "correlations": {}, "first_date": None, "last_date": None, **budget}

    common = set.intersection(*(set(series) for series in changes.values()))
    dates = sorted(common)[-LONG_WINDOW:]
    if len(dates) < MIN_HISTORY:
        raise ValueError(f"Need {MIN_HISTORY} matching daily changes; found {len(dates)}.")
    if (cutoff - date.fromisoformat(dates[-1][1])).days > MAX_HISTORY_AGE_DAYS:
        raise ValueError("Matching daily history is stale; additions are blocked.")

    # 2. Covariance measures how strongly two stocks move together, including size.
    #    Keep the recent and longer estimates separate; both must pass risk checks.
    windows = {}
    sample_windows = {}
    for name, length in (("long", LONG_WINDOW), ("short", SHORT_WINDOW)):
        chosen = dates[-length:]
        values = {ticker: [changes[ticker][day] for day in chosen] for ticker in changes}
        means = {ticker: fsum(series) / len(chosen) for ticker, series in values.items()}
        covariance = {}
        for a in changes:
            covariance[a] = {}
            for b in changes:
                products = [(x - means[a]) * (y - means[b]) for x, y in zip(values[a], values[b])]
                covariance[a][b] = fsum(products) / (len(chosen) - 1) * TRADING_DAYS
                if not number(covariance[a][b]):
                    raise ValueError("Historical changes are too large for a finite risk estimate.")
        if any(not number(value * TRADING_DAYS) for value in means.values()):
            raise ValueError("Historical changes are too large for a finite return estimate.")
        # Keep unsmoothed correlations for grouping and entry priority. Only
        # the covariance used to budget volatility/stock risk is smoothed.
        sample_windows[name] = covariance
        if COVARIANCE_ESTIMATOR == "ledoit_wolf":
            from portfolio_covariance_shrinkage import estimate
            daily_covariance, intensity = estimate(values)
            covariance = {a:{b:daily_covariance[a][b]*TRADING_DAYS for b in changes} for a in changes}
            if any(not number(v) for row in covariance.values() for v in row.values()):
                raise ValueError("Historical changes are too large for a finite risk estimate.")
        windows[name] = {"observations": len(chosen), "covariance": covariance,
                         "historical_annual_mean": {t: means[t] * TRADING_DAYS for t in means}}
        if RISK_MEASURE == "scaled_loss_deviation":
            windows[name]["return_observations"] = values
        if COVARIANCE_ESTIMATOR == "ledoit_wolf":
            windows[name].update(covariance_estimator=COVARIANCE_ESTIMATOR,shrinkage_intensity=intensity)

    # 3. Correlation strips out movement size: +1 means identical direction patterns.
    #    For grouping/ranking, use the higher correlation from the two windows.
    correlations = {a: {} for a in changes}
    for a in changes:
        for b in changes:
            estimates = []
            for cov in sample_windows.values():
                denominator = sqrt(max(0, cov[a][a] * cov[b][b]))
                estimates.append(cov[a][b] / denominator if denominator else 0.0)
            correlations[a][b] = max(-1.0, min(1.0, max(estimates)))
    return {"windows": windows, "correlations": correlations,
            "first_date": dates[0][0], "last_date": dates[-1][1], **budget}


def risk_report(weights, model):
    # 4. Work out total risk and how much of it comes from each holding.
    #    Contribution includes BOTH the stock's own movement and its relationships.
    windows = {}
    for name, window in model["windows"].items():
        cov = window["covariance"]
        combined = {a: fsum(cov[a][b] * weights[b] for b in weights) for a in weights}
        variance = fsum(weights[a] * combined[a] for a in weights)
        volatility = sqrt(max(0.0, variance))
        loss = None
        if RISK_MEASURE == "scaled_loss_deviation":
            # Measure the whole portfolio first: a gaining stock can offset
            # another stock's loss. Twice the loss moment is an explicit new
            # risk definition; it does not satisfy the old volatility budget.
            from portfolio_downside_risk import measure
            loss = measure(weights, window["return_observations"], TRADING_DAYS)
            volatility = loss["risk"]
        stocks = {}
        for ticker, weight in weights.items():
            marginal = (loss["contributions"][ticker]/weight if weight else 0.) if loss is not None else (
                combined[ticker] / volatility if volatility else 0.0)
            contribution = weight * marginal
            stocks[ticker] = {
                "marginal_volatility": marginal,
                "volatility_contribution": contribution,
                "share_of_risk": contribution / volatility if volatility else 0.0,
                "historical_annual_return_contribution": weight * window["historical_annual_mean"][ticker],
            }
        windows[name] = {"observations": window["observations"], "volatility": volatility, "stocks": stocks}

    # 5. Join highly correlated stocks into groups, including indirect links.
    #    If A follows B and B follows C, all three count as one shared bet.
    remaining = set(weights)
    groups = []
    while remaining:
        todo = [min(remaining)]
        group = set()
        while todo:
            ticker = todo.pop()
            if ticker in group:
                continue
            group.add(ticker)
            remaining.discard(ticker)
            todo.extend(other for other in sorted(remaining)
                        if model["correlations"][ticker][other] >= HIGH_CORRELATION)
        groups.append({"tickers": sorted(group), "weight": fsum(weights[t] for t in group)})
    gross = fsum(weights.values())
    return {"available": True, "first_date": model["first_date"], "last_date": model["last_date"],
            "windows": windows, "correlated_groups": groups,
            "correlations": {a: {b: model["correlations"][a][b] for b in weights} for a in weights},
            "volatility": max((w["volatility"] for w in windows.values()), default=0.0),
            "loss_if_all_stocks_fall_40_percent": gross * 0.40,
            **({"risk_budget": model["risk_budget"]} if "risk_budget" in model else {})}


def risk_caps(report):
    # The market allowance changes only these two risk limits. Stock, industry
    # and correlation money limits keep their existing hard ceilings.
    budget = report.get("risk_budget", {})
    return budget.get("portfolio_vol_cap", PORTFOLIO_VOL_CAP), budget.get("position_risk_cap", POSITION_RISK_CAP)


def allowed_risk_scale(report, *, holding=False):
    # All these risks shrink in proportion when every holding is reduced together.
    # Return 1 if safe, or the fraction of current holdings that we may retain.
    scale = 1.0
    multiplier=HOLD_RISK_MULTIPLIER if holding else 1.0
    vol_cap, stock_cap = risk_caps(report)
    for window in report["windows"].values():
        if window["volatility"] > vol_cap*multiplier:
            scale = min(scale, vol_cap*multiplier / window["volatility"])
        for stock in window["stocks"].values():
            if stock["volatility_contribution"] > stock_cap*multiplier:
                scale = min(scale, stock_cap*multiplier / stock["volatility_contribution"])
    for group in report["correlated_groups"]:
        if group["weight"] > CORRELATED_GROUP_CAP:
            scale = min(scale, CORRELATED_GROUP_CAP / group["weight"])
    return scale


def industry_report(weights, classifications):
    # Business limits use MONEY weights, not just the number of stock names.
    # Missing labels mean unknown exposure; never invent a separate safe bucket.
    report = {"group":{},"industry":{}}
    for ticker,weight in weights.items():
        label = classifications.get(ticker,{})
        for key in report:
            value = label.get(key)
            if not isinstance(value,str) or not value.strip() or value.upper() in ("UNK","UNKNOWN"):
                raise ValueError(f"{ticker}: missing industry classification; block purchases.")
            report[key][value] = report[key].get(value,0.0)+weight
    return report


def selective_risk_repair(weights, model):
    # A breach in one stock/group need not require selling unrelated holdings.
    # Recheck after each adjustment: changing a weight changes risk contributions.
    repaired = dict(weights)
    for _ in range(30):
        report = risk_report(repaired, model)
        if allowed_risk_scale(report) >= 1 - EPSILON:
            return repaired
        ratios = {t: 1.0 for t in repaired}
        _, stock_cap = risk_caps(report)
        for window in report["windows"].values():
            for ticker, stock in window["stocks"].items():
                contribution = stock["volatility_contribution"]
                if contribution > stock_cap + EPSILON:
                    ratios[ticker] = min(ratios[ticker], stock_cap / contribution)
        for group in report["correlated_groups"]:
            if group["weight"] > CORRELATED_GROUP_CAP + EPSILON:
                for ticker in group["tickers"]:
                    ratios[ticker] = min(ratios[ticker], CORRELATED_GROUP_CAP / group["weight"])
        targeted = any(r < 1 - EPSILON for r in ratios.values())
        if targeted:
            for ticker, ratio in ratios.items():
                if ratio < 1 - EPSILON:
                    repaired[ticker] *= ratio * RISK_REPAIR_BUFFER
        else:
            # A remaining portfolio-wide volatility breach affects the whole
            # account. Scale it down under the original rule and the same limits.
            scale = allowed_risk_scale(report) * RISK_REPAIR_BUFFER
            repaired = {t: w * scale for t, w in repaired.items()}
        repaired = {t: w for t, w in repaired.items() if w > EPSILON}
    # Guard awkward covariance cases: the original proportional repair remains
    # the conservative fallback. Never return an unresolved risk breach.
    report = risk_report(repaired, model)
    scale = allowed_risk_scale(report) * RISK_REPAIR_BUFFER
    repaired = {t: w * scale for t, w in repaired.items()}
    assert allowed_risk_scale(risk_report(repaired, model)) >= 1 - EPSILON
    return repaired


def smaller_add_stage(weights, ticker, requested, model):
    # Try smaller amounts in order. Each trial must pass ALL risk checks.
    # This uses the same historical estimate; no later prices or new signals.
    if requested <= MIN_PARTIAL_ADD + EPSILON:
        return 0.0, None
    count = 1
    while True:
        stage = max(MIN_PARTIAL_ADD, requested-count*PARTIAL_ADD_DECREMENT)
        report = risk_report({**weights,ticker:weights.get(ticker,0.0)+stage},model)
        if allowed_risk_scale(report) >= 1-EPSILON:
            return stage, report
        if stage <= MIN_PARTIAL_ADD + EPSILON:
            return 0.0, None
        count += 1


def risk_limit_names(report):
    # Diagnostic labels explain which estimated risk limit a stage would cross.
    names = []
    vol_cap, stock_cap = risk_caps(report)
    for window_name,window in report["windows"].items():
        if window["volatility"] > vol_cap + EPSILON:
            names.append(window_name+" portfolio volatility")
        if any(s["volatility_contribution"] > stock_cap+EPSILON for s in window["stocks"].values()):
            names.append(window_name+" stock contribution")
    if any(group["weight"] > CORRELATED_GROUP_CAP+EPSILON for group in report["correlated_groups"]):
        names.append("correlated-group weight")
    return names


def reallocate_for_add(weights, ticker, stage, model, protected, momentum_scores=None):
    # Keep the full requested buy. Try selling 1%, 2%, ... 25% of other holdings.
    # This is a fixed feasibility search, not a historical parameter sweep.
    sellers = sorted(set(weights) - {ticker} - set(protected))
    if REALLOCATION_WEAKER_MOMENTUM_ONLY:
        # Unknown, equal or stronger momentum is never permission to sell.
        scores = momentum_scores or {}
        if not number(scores.get(ticker)):
            return None
        sellers = [t for t in sellers if number(scores.get(t)) and scores[t] < scores[ticker]]
    seller_weight = fsum(weights[t] for t in sellers)
    if not seller_weight:
        return None
    for percent in range(1, 26):
        fraction = percent / 100
        sold = seller_weight * fraction
        # Do not exchange more stock exposure than the purchase adds.
        if sold >= stage - EPSILON:
            break
        trial = {t: w * (1 - fraction) if t in sellers else w for t, w in weights.items()}
        trial[ticker] = weights.get(ticker, 0.) + stage
        after = risk_report(trial, model)
        if allowed_risk_scale(after) >= 1 - EPSILON:
            return {"weights": trial, "report": after, "fraction_sold": fraction,
                    "sellers": sellers, "sold_weight": sold,
                    "protected": sorted(protected)}
    return None


def manage_portfolio(account, signals, prices, daily_closes, as_of, *, classifications=None,
                     trim_rule=None, risk_cache=None, partial_buys=False, add_diagnostics=None,
                     record_entry_chain=False):
    check_settings()
    if not isinstance(partial_buys,bool):
        raise ValueError("partial_buys must be True or False.")
    if type(record_entry_chain) is not bool:
        raise ValueError("record_entry_chain must be True or False.")
    if any(not number(x) or not 0 < x <= 1 for x in (PARTIAL_ADD_DECREMENT,MIN_PARTIAL_ADD)):
        raise ValueError("Partial-buy decrement and minimum must be positive fractions <=1.")
    trim_rule = TRIM_RULE if trim_rule is None else trim_rule
    if trim_rule not in ("fixed","gentle","staged") or any(not number(x) or not 0 < x <= 1 for x in (TRIM_FRACTION,GENTLE_TRIM_CAP)):
        raise ValueError("Use fixed, gentle or staged trimming and positive trim fractions <=1.")
    if trim_rule == "staged":
        if (not STAGED_TRIM_FRACTIONS or STAGED_TRIM_FRACTIONS[-1] != 1 or
                any(not number(x) or not 0 < x <= 1 for x in STAGED_TRIM_FRACTIONS)):
            raise ValueError("Staged fractions must be positive, at most 1, and finish with a full sale.")
        if not isinstance(RESET_STAGED_TRIM_ON_BUY,bool):
            raise ValueError("RESET_STAGED_TRIM_ON_BUY must be True or False.")
    if any(not isinstance(x,int) or isinstance(x,bool) or x < 1 for x in (MAX_PER_INDUSTRY_GROUP,MAX_PER_INDUSTRY)):
        raise ValueError("Industry position counts must be positive integers.")
    if any(not number(cap) or not 0 < cap <= 1 for cap in (INDUSTRY_GROUP_CAP,INDUSTRY_CAP)):
        raise ValueError("Industry weight limits must be positive and <=100%.")
    # stock_actions supplies these labels automatically. Other callers can pass
    # a complete mapping explicitly. Legacy calls without labels retain their
    # previous behavior and clearly report industry checks as inactive.
    if classifications is None and any("classification" in s for s in signals.values()):
        classifications = {t:s["classification"] for t,s in signals.items() if "classification" in s}
    decision = timestamp(as_of)
    if account.get("as_of") and decision <= timestamp(account["as_of"]):
        raise ValueError("Use a newer decision time; the same signal batch cannot be consumed twice.")
    if not number(account["cash"]) or account["cash"] < 0:
        raise ValueError("Cash must be a nonnegative finite number.")
    receivables=dict(account.get("receivables",{}))
    if any(not number(v) or v<0 for v in receivables.values()):
        raise ValueError("Cash-merger receivables must be nonnegative finite amounts.")
    receivable_value=fsum(receivables.values())
    if not isinstance(account.get("halted", False), bool):
        raise ValueError("halted must be True or False.")
    for ticker, shares in account["positions"].items():
        if not number(shares) or shares < 0:
            raise ValueError(f"{ticker}: shares must be nonnegative; short positions are not supported.")
    for ticker in set(account["positions"]) | set(signals):
        if not number(prices.get(ticker)) or prices[ticker] <= 0:
            raise ValueError(f"{ticker}: supply a positive current price.")
    for ticker, signal in signals.items():
        if signal.get("action", "hold") not in ("add", "trim", "hold", "exit"):
            raise ValueError(f"{ticker}: action must be add, trim, hold or exit.")
        if not isinstance(signal.get("eligible", True), bool):
            raise ValueError(f"{ticker}: eligible must be True or False.")
        if type(signal.get("require_stock_mix_diversification",False)) is not bool:
            raise ValueError("require_stock_mix_diversification must be True or False.")
        if signal.get("require_stock_mix_diversification",False) and REALLOCATE_RISK_ON_ADD:
            raise ValueError("Stock mix entry filtering cannot be combined with risk reallocation.")
        if ENTRY_PRIORITY in ("momentum","momentum_weighted","momentum_per_risk","trend_quality") and signal.get("action")=="add":
            if not number(signal.get("momentum_score")):
                raise ValueError(f"{ticker}: momentum priority needs a known historical momentum_score.")
            if ENTRY_PRIORITY=="momentum_per_risk" and signal["momentum_score"] <= 0:
                raise ValueError(f"{ticker}: momentum_per_risk needs positive historical momentum.")
        if "target_weight" in signal:
            target = signal["target_weight"]
            if not number(target) or not 0 < target <= POSITION_CAP:
                raise ValueError(f"{ticker}: target_weight must be positive and at most the position cap.")
        if "max_add_notional" in signal:
            if not number(signal["max_add_notional"]) or signal["max_add_notional"] <= 0:
                raise ValueError(f"{ticker}: max_add_notional must be a positive finite dollar amount.")
        forecast = signal.get("expected_annual_return")
        if forecast is not None and not number(forecast):
            raise ValueError(f"{ticker}: expected_annual_return must be finite or omitted.")

    # 6. Value the whole account at today's supplied prices, INCLUDING cash.
    equity = account["cash"] + receivable_value + fsum(shares * prices[t] for t, shares in account["positions"].items())
    if not number(equity) or equity <= 0:
        raise ValueError("The account must have a positive finite total value.")
    peak = account.get("peak_equity", equity)
    if not number(peak) or peak <= 0:
        raise ValueError("peak_equity must be a positive finite number.")
    peak = max(peak, equity)
    drawdown = 1 - equity / peak
    halted = account.get("halted", False) or drawdown >= DRAWDOWN_EXIT
    original = {t: shares * prices[t] / equity for t, shares in account["positions"].items() if shares > 0}
    # Remember how many ordinary x sales each holding has completed. Holds,
    # blocked buys and mandatory risk sales do not advance this sequence.
    trim_stages = dict(account.get("trim_stages",{})) if trim_rule == "staged" else {}
    if any(not isinstance(s,int) or isinstance(s,bool) or not 0 <= s < len(STAGED_TRIM_FRACTIONS)
           for s in trim_stages.values()):
        raise ValueError("Each saved trim stage must be a valid whole-number index.")
    staged_sales = {}
    weights = original.copy()
    reasons = {t: [] for t in set(original) | set(signals)}
    blocked = set()
    decisions = []
    add_attempts = {}
    buying_buffer = CASH_RESERVE if BUY_CASH_BUFFER is None else BUY_CASH_BUFFER
    # A cash-merger claim has value, but cannot fund a purchase before payment.
    receivable_weight=receivable_value/equity
    buying_buffer += receivable_weight
    default_target = NORMAL_TARGET_WEIGHT if NORMAL_TARGET_WEIGHT is not None else min(POSITION_CAP, (1 - buying_buffer) / TARGET_POSITIONS)
    models = {}

    def model_for(holdings):
        # Reuse the same estimate when several candidate stages need the same stocks.
        key = tuple(sorted(holdings))
        if key not in models:
            # A backtest may reuse estimates within one market day. Its cache
            # must be cleared if its daily input history/settings change.
            shared_key = (decision.astimezone(ET).date().isoformat(),key)
            if risk_cache is not None and shared_key in risk_cache:
                models[key] = risk_cache[shared_key]
            else:
                models[key] = build_risk_model(daily_closes, key, as_of)
                if risk_cache is not None:
                    risk_cache[shared_key] = models[key]
        return models[key]

    # 7. Exit unsuitable holdings immediately. A trim removes one stage instead.
    for ticker in list(weights):
        signal = signals.get(ticker, {})
        if halted or not signal.get("eligible", True) or signal.get("action") == "exit":
            del weights[ticker]
            reasons[ticker].append("Drawdown pause: exit to cash." if halted else "Exit: no longer suitable.")
            blocked.add(ticker)
        elif signal.get("action") == "trim":
            if trim_rule == "staged":
                stage = trim_stages.get(ticker,0)
                fraction = STAGED_TRIM_FRACTIONS[stage]
                amount = weights[ticker]*fraction
                staged_sales[ticker] = {"stage_index":stage,"fraction_of_remaining":fraction}
                trim_stages[ticker] = stage+1
            else:
                amount = min(weights[ticker]*TRIM_FRACTION,GENTLE_TRIM_CAP) if trim_rule == "gentle" else TRIM_STEP
            weights[ticker] = max(0.0, weights[ticker] - amount)
            reasons[ticker].append("Trim one stage.")
            blocked.add(ticker)
    weights = {t: w for t, w in weights.items() if w > EPSILON}

    # 8. Hard rules come before normal stages, including holdings enlarged by price moves.
    #    If there are too many existing positions, close the smallest ones first.
    excess = max(0, len(weights) - TARGET_POSITIONS)
    for ticker in sorted(weights, key=lambda t: (weights[t], t))[:excess]:
        del weights[ticker]
        reasons[ticker].append("Position-count limit: close a smallest holding.")
        blocked.add(ticker)
    for ticker in weights:
        if weights[ticker] > POSITION_CAP:
            weights[ticker] = POSITION_CAP
            reasons[ticker].append("Hard position cap: reduce to at most 20%.")
            blocked.add(ticker)
    gross = fsum(weights.values())
    if gross > 1 - CASH_RESERVE - receivable_weight:
        scale = (1 - CASH_RESERVE - receivable_weight) / gross
        for ticker in weights:
            weights[ticker] *= scale
            reasons[ticker].append("Restore the minimum cash reserve.")
            blocked.add(ticker)

    # Business concentration can grow even without a new signal. Reduce an
    # oversized bucket proportionally; ordinary trims do not delay a hard limit.
    industry_problem = None
    if classifications is not None:
        try:
            industry_report(weights,classifications)  # Validate all remaining holdings first.
            for key,cap in (("group",INDUSTRY_GROUP_CAP),("industry",INDUSTRY_CAP)):
                exposure = industry_report(weights,classifications)[key]
                for bucket,value in exposure.items():
                    if value > cap:
                        for ticker in weights:
                            if classifications[ticker][key] == bucket:
                                weights[ticker] *= cap/value
                                reasons[ticker].append(f"Restore {key} weight limit.")
                                blocked.add(ticker)
        except ValueError as error:
            industry_problem = str(error)
            decisions.append({"action":"block_additions","reason":industry_problem})

    # 9. Optional breathing room applies to existing holdings, never new buys.
    #    Once the wider holding limit breaks, repair to the original buy budget.
    #    If history is missing, still allow sales, but refuse ALL new purchases.
    history_problem = None
    try:
        current_risk = risk_report(weights, model_for(weights))
        if allowed_risk_scale(current_risk,holding=True) < 1 - EPSILON:
            if RISK_REPAIR_MODE == "selective":
                repaired = selective_risk_repair(weights, model_for(weights))
                for ticker in list(weights):
                    if repaired.get(ticker, 0.) >= weights[ticker] - EPSILON:
                        continue
                    weights[ticker] = repaired.get(ticker, 0.)
                    reasons[ticker].append("Reduce exposure to satisfy portfolio/group/stock risk limits.")
                    blocked.add(ticker)
                weights = {t: w for t, w in weights.items() if w > EPSILON}
            else:
                scale = allowed_risk_scale(current_risk)*RISK_REPAIR_BUFFER
                for ticker in weights:
                    weights[ticker] *= scale
                    reasons[ticker].append("Reduce exposure to satisfy portfolio/group/stock risk limits.")
                    blocked.add(ticker)
    except (ValueError, OverflowError) as error:
        history_problem = str(error)
        decisions.append({"action": "block_additions", "reason": history_problem})

    # 10. Consider one stage per add signal. Prefer stocks moving less like our holdings.
    #     Break ties by the smaller increase in estimated portfolio risk, then ticker.
    pending = {t for t, s in signals.items() if s.get("action") == "add" and s.get("eligible", True)}
    purchased = set()
    while pending:
        choices = []
        reallocations = {}
        for ticker in sorted(pending):
            reason = None
            if halted:
                reason = "Buying is paused after the drawdown exit."
            elif history_problem:
                reason = "Existing portfolio risk cannot be measured; no purchases."
            elif industry_problem:
                reason = "Existing industry exposure cannot be measured; no purchases."
            elif ticker in blocked:
                reason = "A reduction already took priority at this decision."
            elif ticker not in weights and len(weights) >= TARGET_POSITIONS:
                reason = "All position slots are occupied."
            current = weights.get(ticker, 0.0)
            target = signals[ticker].get("target_weight", default_target)
            # Start an unowned stock with the configured opening stage. Each
            # later fresh add signal can still build quickly toward its target.
            stage_limit = INITIAL_ADD_STEP if INITIAL_ADD_STEP is not None and ticker not in weights else ADD_STEP
            step = min(stage_limit, target - current, POSITION_CAP - current,
                       1 - buying_buffer - fsum(weights.values()))
            if "max_add_notional" in signals[ticker]:
                step = min(step, signals[ticker]["max_add_notional"] / equity)
            if not reason and classifications is not None:
                try:
                    industry_report({ticker:1.0},classifications)  # Candidate labels must also be usable.
                    exposure = industry_report(weights,classifications)
                    if ticker not in weights:
                        for key,limit in (("group",MAX_PER_INDUSTRY_GROUP),("industry",MAX_PER_INDUSTRY)):
                            count = sum(classifications[t][key] == classifications[ticker][key] for t in weights)
                            if count >= limit:
                                reason = f"All permitted {key} position slots are occupied."
                    for key,cap in (("group",INDUSTRY_GROUP_CAP),("industry",INDUSTRY_CAP)):
                        step = min(step,cap-exposure[key].get(classifications[ticker][key],0.0))
                except ValueError as error:
                    reason = str(error)
            if not reason and step <= EPSILON:
                reason = "Already at target, position cap, cash or industry limit."
            if not reason and ticker not in weights and step < MIN_ENTRY_WEIGHT-EPSILON:
                reason = "New position would be below the minimum entry weight."
            attempt = {"at":as_of,"ticker":ticker,"new_entry":ticker not in original,
                       "configured_stage":stage_limit,"requested_stage":max(0.0,step),
                       "current_weight":current,"cash_weight_before_add":1-fsum(weights.values()),
                       "outcome":"blocked","used_stage":0.0,"full_stage_risk_failed":False,
                       "smaller_stage_that_fits":None,"binding_risk_limits":[]}
            add_attempts[ticker] = attempt
            if reason:
                attempt["reason"] = reason
                decisions.append({"ticker": ticker, "action": "blocked_add", "reason": reason})
                continue
            trial = {**weights, ticker: current + step}
            try:
                model = model_for(trial)
                after = risk_report(trial, model)
                before = risk_report(weights, model)
            except (ValueError, OverflowError) as error:
                attempt["reason"] = str(error)
                decisions.append({"ticker": ticker, "action": "blocked_add", "reason": str(error)})
                continue
            if allowed_risk_scale(after) < 1 - EPSILON:
                attempt["full_stage_risk_failed"] = True
                attempt["binding_risk_limits"] = risk_limit_names(after)
                attempt["volatility_before_add"] = before["volatility"]
                fitting, smaller_report = (smaller_add_stage(weights,ticker,step,model)
                    if partial_buys or add_diagnostics is not None else (0.0,None))
                attempt["smaller_stage_that_fits"] = fitting
                reallocation = (reallocate_for_add(weights,ticker,step,model,purchased,
                    {t:s.get("momentum_score") for t,s in signals.items()})
                    if REALLOCATE_RISK_ON_ADD else None)
                if reallocation:
                    after = reallocation["report"]
                    reallocations[ticker] = reallocation
                elif not partial_buys or not fitting:
                    attempt["reason"] = "This stage would breach a portfolio/group/stock risk limit."
                    decisions.append({"ticker": ticker, "action": "blocked_add", "reason": attempt["reason"]})
                    continue
                else:
                    step,after = fitting,smaller_report
            if ticker not in weights and step < MIN_ENTRY_WEIGHT-EPSILON:
                attempt["reason"] = "New position would be below the minimum entry weight."
                decisions.append({"ticker": ticker, "action": "blocked_add", "reason": attempt["reason"]})
                continue
            if signals[ticker].get("require_stock_mix_diversification",False):
                from portfolio_monthly_diversification_measure import measure
                addition = {**weights,ticker:current+step}
                diversification = measure(weights,addition,model)
                if not diversification["both_windows_stock_mix_volatility_falls"]:
                    attempt["reason"] = "This monthly opening does not diversify both risk windows."
                    decisions.append({"ticker":ticker,"action":"blocked_add","reason":attempt["reason"]})
                    continue
            others = {t: w for t, w in weights.items() if t != ticker}
            total = fsum(others.values())
            correlation = (fsum(w * max(0.0, model["correlations"][ticker][t]) for t, w in others.items())
                           / total if total else 0.0)
            extra_risk = after["volatility"] - before["volatility"]
            choices.append((correlation, extra_risk, ticker, step))
        # Recheck successful candidates after the best one changes our holdings.
        pending = {choice[2] for choice in choices}
        if not choices:
            break
        # Risk-first ranks each dollar fairly when stages have different sizes.
        # All candidates have already passed the same hard risk/diversity checks.
        if ENTRY_PRIORITY in ("momentum","trend_quality"):
            best = min(choices,key=lambda c:(-signals[c[2]]["momentum_score"],c[0],c[1]/c[3],c[2]))
        elif ENTRY_PRIORITY=="momentum_weighted":
            # Compare strength times the feasible NEW purchase, not total holding.
            # A tiny stage should not win merely because its stock rose a little more.
            # Historical momentum orders legal choices; it is not a return forecast.
            best = min(choices,key=lambda c:(-signals[c[2]]["momentum_score"]*c[3],c[0],c[1]/c[3],c[2]))
        elif ENTRY_PRIORITY=="momentum_per_risk":
            from portfolio_purchase_priority import momentum_per_added_risk
            best = min(choices,key=lambda c:momentum_per_added_risk(c,signals[c[2]]["momentum_score"]))
        else:
            best = min(choices) if ENTRY_PRIORITY=="correlation" else min(choices,key=lambda c:(c[1]/c[3],c[0],c[2]))
        correlation, extra_risk, ticker, step = best
        entry_before = weights.copy()
        reallocation = reallocations.get(ticker)
        if reallocation:
            weights = reallocation["weights"]
            for seller in reallocation["sellers"]:
                reasons[seller].append("Release risk for a fresh+ purchase; net stock exposure increases.")
                blocked.add(seller)
        else:
            weights[ticker] = weights.get(ticker, 0.0) + step
        purchased.add(ticker)
        if trim_rule == "staged" and (ticker not in original or RESET_STAGED_TRIM_ON_BUY):
            trim_stages[ticker] = 0
        add_attempts[ticker]["outcome"] = ("accepted_reallocation" if reallocation else
            "accepted_smaller" if add_attempts[ticker]["full_stage_risk_failed"] else "accepted_full")
        add_attempts[ticker]["used_stage"] = step
        reasons[ticker].append("Add one stage; diversification and risk checks passed.")
        decisions.append({"ticker": ticker, "action": "accepted_add", "stage_weight": step,
                          "average_positive_correlation": correlation,
                          "incremental_portfolio_volatility": extra_risk})
        if REALLOCATE_RISK_ON_ADD or record_entry_chain:
            decisions[-1].update(entry_weights_before=entry_before,entry_weights_after=weights.copy())
        if REALLOCATE_RISK_ON_ADD:
            decisions[-1]["risk_reallocation"] = ({k:reallocation[k] for k in ("fraction_sold","sellers","sold_weight","protected")}
                    if reallocation else None)
        if record_entry_chain:
            required = signals[ticker].get("require_stock_mix_diversification",False)
            decisions[-1]["requires_stock_mix_diversification"] = required
            if required:
                from portfolio_monthly_diversification_measure import measure
                decisions[-1]["stock_mix_diversification"] = measure(entry_before,weights,model_for(weights))
        pending.remove(ticker)

    # 11. Return one net order per stock, selling before buying. Money is conserved.
    #     The optional return estimate assumes this change is funded from/to cash.
    orders = []
    for ticker in sorted(set(original) | set(weights)):
        change = weights.get(ticker, 0.0) - original.get(ticker, 0.0)
        if abs(change) <= EPSILON:
            continue
        forecast = signals.get(ticker, {}).get("expected_annual_return")
        orders.append({"ticker": ticker, "side": "buy" if change > 0 else "sell",
                       "shares": abs(change) * equity / prices[ticker], "price": prices[ticker],
                       "notional": abs(change) * equity, "from_weight": original.get(ticker, 0.0),
                       "to_weight": weights.get(ticker, 0.0), "reasons": reasons[ticker],
                       "forecast_annual_portfolio_return_change": change * forecast if forecast is not None else None})
    orders.sort(key=lambda order: (order["side"] == "buy", order["ticker"]))
    for order in orders:
        if order["ticker"] in staged_sales:
            order["staged_trim"] = staged_sales[order["ticker"]]
    cash_weight = 1 - fsum(weights.values()) - receivable_weight
    if len(weights) > TARGET_POSITIONS or any(w > POSITION_CAP + EPSILON for w in weights.values()):
        raise RuntimeError("Internal error: proposed portfolio breaks the position limits.")
    if cash_weight < CASH_RESERVE - EPSILON:
        raise RuntimeError("Internal error: proposed portfolio breaks the cash reserve.")
    final_industry = {"active":classifications is not None,"available":False}
    if classifications is not None:
        try:
            final_industry.update({"available":True,**industry_report(weights,classifications)})
            for key,cap in (("group",INDUSTRY_GROUP_CAP),("industry",INDUSTRY_CAP)):
                if any(w > cap+EPSILON for w in final_industry[key].values()):
                    raise RuntimeError("Internal error: proposed portfolio breaks an industry limit.")
        except ValueError as error:
            final_industry["reason"] = str(error)
    try:
        final_risk = risk_report(weights, model_for(weights))
        if allowed_risk_scale(final_risk,holding=True) < 1 - 1e-10:
            raise RuntimeError("Internal error: proposed portfolio breaks a risk limit.")
    except (ValueError, OverflowError) as error:
        final_risk = {"available": False, "reason": str(error)}
    if add_diagnostics is not None:
        add_diagnostics.extend(add_attempts.values())
    next_account = {"cash": cash_weight * equity,
                    "positions": {t: w * equity / prices[t] for t, w in weights.items()},
                    "peak_equity": peak, "halted": halted, "as_of": as_of}
    if "receivables" in account:
        next_account["receivables"]=receivables
    if trim_rule == "staged":
        # A fully exited holding has no old selling sequence on a later entry.
        next_account["trim_stages"] = {t:trim_stages.get(t,0) for t in weights}
    return {
        "as_of": as_of, "equity": equity, "target_positions": TARGET_POSITIONS,
        "trim_rule":trim_rule,
        # Save the numeric settings with the plan so later edits do not hide its rules.
        "settings": {name: value for name, value in globals().items() if name.isupper() and number(value)},
        "default_target_weight": default_target, "weights": weights, "cash_weight": cash_weight,
        "drawdown": drawdown, "halted": halted, "risk": final_risk,
        "industry_exposure":final_industry,
        "orders": orders, "decisions": decisions,
        "next_account": next_account,
        "assumptions": "Fractional long shares; supplied decision prices; zero fees/slippage/cash interest. Orders are a plan, not broker fills.",
    }
