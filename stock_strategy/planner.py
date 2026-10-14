"""Use the stock manager and check the actual stock-plus-SGOV book."""
from copy import deepcopy
from math import fsum
from .config import CONFIG, risk


def equity(account,prices):
    return account["cash"]+fsum(q*prices[t] for t,q in account["positions"].items())


def combined_risk(account, prices, closes, as_of):
    total = equity(account,prices)
    if total <= 0:
        raise ValueError("Account equity must be positive.")
    weights = {t:q*prices[t]/total for t,q in account["positions"].items() if q > 1e-10}
    stocks = {t:w for t,w in weights.items() if t != CONFIG.fund}
    model = risk.build_risk_model(closes,weights,as_of)
    report = risk.risk_report(weights,model)
    report["correlated_groups"] = risk.risk_report(stocks,model)["correlated_groups"]
    return report


def whole_account_allowed(account, prices, closes, labels, as_of):
    total = equity(account,prices)
    if total <= 0 or account["cash"] < -1e-7 or len(account["positions"]) > risk.TARGET_POSITIONS:
        return False
    weights = {t:q*prices[t]/total for t,q in account["positions"].items() if q > 1e-10}
    if any(w > (CONFIG.fund_target if t == CONFIG.fund else risk.POSITION_CAP)+1e-10 for t,w in weights.items()):
        return False
    stocks = {t:w for t,w in weights.items() if t != CONFIG.fund}
    try:
        exposure = risk.industry_report(stocks,labels)
        for field,cap,count_cap in (("group",risk.INDUSTRY_GROUP_CAP,risk.MAX_PER_INDUSTRY_GROUP),
                ("industry",risk.INDUSTRY_CAP,risk.MAX_PER_INDUSTRY)):
            if any(value > cap+1e-10 for value in exposure[field].values()):
                return False
            if any(sum(labels[t][field] == bucket for t in stocks) > count_cap for bucket in exposure[field]):
                return False
        report = combined_risk(account,prices,closes,as_of)
        return risk.allowed_risk_scale(report) >= 1-1e-10
    except (ValueError,OverflowError,AttributeError,KeyError):
        return False


def candidate(account, ticker, delta, price):
    result = deepcopy(account)
    result["cash"] -= delta
    quantity = result["positions"].get(ticker,0.)+delta/price
    if quantity > 1e-9:
        result["positions"][ticker] = quantity
    else:
        result["positions"].pop(ticker,None)
    return result


def stock_plan(account, signals, prices, closes, labels, as_of):
    # Treat SGOV's mark as potentially releasable cash for planning only. Its
    # sale must actually fill before any corresponding stock buy is submitted.
    stocks = {t:q for t,q in account["positions"].items() if t != CONFIG.fund}
    virtual = dict(cash=account["cash"]+account["positions"].get(CONFIG.fund,0.)*prices.get(CONFIG.fund,0.),positions=stocks)
    labels = {t:v for t,v in labels.items() if v is not None and t != CONFIG.fund}
    reduced = {t:s for t,s in signals.items() if t != CONFIG.fund}
    plan = risk.manage_portfolio(virtual,reduced,prices,closes,as_of,classifications=labels,trim_rule="gentle",partial_buys=False)
    return plan


def fund_refill(account, prices, closes, labels, as_of):
    total = equity(account,prices)
    maximum = max(0.,account["cash"]-CONFIG.execution_cash_buffer*total)
    if maximum < max(CONFIG.minimum_order_usd,CONFIG.fund_refill_deficit*total):
        return 0.
    low, high = 0.,maximum
    if not whole_account_allowed(account,prices,closes,labels,as_of):
        return 0.
    if whole_account_allowed(candidate(account,CONFIG.fund,high,prices[CONFIG.fund]),prices,closes,labels,as_of):
        return high
    for _ in range(35):
        middle = (low+high)/2
        if whole_account_allowed(candidate(account,CONFIG.fund,middle,prices[CONFIG.fund]),prices,closes,labels,as_of):
            low = middle
        else:
            high = middle
    return low
