"""Core allocation order: sales, combined-risk repair, stock buys, SGOV refill.

The executor is supplied by the caller. MVE's executor simulates local fills;
this module contains no broker connection or credential handling.
"""
from .config import CONFIG, risk
from .planner import candidate, equity, whole_account_allowed, fund_refill, combined_risk

def execute(execution, plan, prices, closes, labels, as_of, phase, event_keys):
    records = []
    # Real sales fund real purchases. Proposed account balances never authorize
    # a buy. Every purchase re-reads broker cash, equity, holdings and quotes.
    for order in plan["orders"]:
        if order["side"] != "sell":
            continue
        held = execution.snapshot()["positions"].get(order["ticker"],0.)
        count = min(held,order["shares"])
        if count <= 1e-9:
            continue
        key = event_keys.get(order["ticker"],as_of[:10]+"|"+phase+"|"+";".join(order["reasons"]))
        records.append(execution.send(order["ticker"],"sell",key,qty=count))
    # Stock repairs above do not include SGOV covariance. Check the actually
    # remaining combined book too, and reduce it before authorizing any buys.
    account = execution.snapshot()
    current_prices = dict(prices)
    current_prices.update(execution.broker.latest_trades(list(account["positions"])))
    try:
        scale = risk.allowed_risk_scale(combined_risk(account,current_prices,closes,as_of))
    except (ValueError,OverflowError,KeyError):
        records.append(dict(status="blocked",reason="combined covariance history unavailable; no new buys"))
        return records
    if scale < 1-1e-10:
        for ticker,quantity in sorted(account["positions"].items()):
            count = quantity*(1-scale*(1-1e-9))
            if count > 1e-9:
                records.append(execution.send(ticker,"sell",as_of[:10]+"|"+phase+"|whole-account-risk",qty=count))
    for order in plan["orders"]:
        if order["side"] != "buy":
            continue
        ticker = order["ticker"]
        account = execution.snapshot()
        current_prices = dict(prices)
        current_prices.update(execution.broker.latest_trades(list(set(account["positions"])|{ticker,CONFIG.fund})))
        total = equity(account,current_prices)
        already = account["positions"].get(ticker,0.)*current_prices[ticker]
        amount = min(order["notional"],risk.ADD_STEP*total,max(0.,risk.NORMAL_TARGET_WEIGHT*total-already))
        if amount < CONFIG.minimum_order_usd:
            continue
        buffer = CONFIG.execution_cash_buffer*total
        required = max(0.,amount+buffer-account["cash"])
        fund_value = account["positions"].get(CONFIG.fund,0.)*current_prices[CONFIG.fund]
        if ticker not in account["positions"] and len(account["positions"]) >= risk.TARGET_POSITIONS and fund_value > 0:
            required = fund_value   # Release the fund's slot before a twentieth stock.
        sale = min(required,fund_value)
        amount = min(amount,max(0.,account["cash"]+sale-buffer))
        forecast = candidate(account,CONFIG.fund,-sale,current_prices[CONFIG.fund]) if sale > 0 else account
        forecast = candidate(forecast,ticker,amount,current_prices[ticker])
        if amount < CONFIG.minimum_order_usd or not whole_account_allowed(forecast,current_prices,closes,labels,as_of):
            records.append(dict(ticker=ticker,status="blocked",reason="combined portfolio risk/caps before funding"))
            continue
        if required > 0 and fund_value > 0:
            records.append(execution.send(CONFIG.fund,"sell",event_keys[ticker]+"|funding|"+ticker,
                qty=sale/current_prices[CONFIG.fund]))
            account = execution.snapshot()
            total = equity(account,current_prices)
        amount = min(amount,max(0.,account["cash"]-CONFIG.execution_cash_buffer*total))
        if amount < CONFIG.minimum_order_usd:
            continue
        proposed = candidate(account,ticker,amount,current_prices[ticker])
        if not whole_account_allowed(proposed,current_prices,closes,labels,as_of):
            records.append(dict(ticker=ticker,status="blocked",reason="actual combined portfolio risk/caps"))
            continue
        records.append(execution.send(ticker,"buy",event_keys[ticker],notional=amount))
    if phase == "open":
        account = execution.snapshot()
        current_prices = dict(prices)
        current_prices.update(execution.broker.latest_trades(list(set(account["positions"])|{CONFIG.fund})))
        amount = fund_refill(account,current_prices,closes,labels,as_of)
        if amount >= CONFIG.minimum_order_usd:
            records.append(execution.send(CONFIG.fund,"buy",as_of[:10]+"|opening-refill",notional=amount))
    return records
