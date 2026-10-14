"""Generate fake data that demonstrates SGOV funding, a real + and a real x."""
from datetime import datetime, timedelta, date
from math import sin, cos
from stock_strategy import indicator
from stock_strategy.candles import ET


def daily_history(last_day, rate, phase=0., amplitude=.001):
    # A fake weekday calendar and smooth fake prices, NOT exchange observations.
    cursor,price = date(2025,1,1),100.
    result,i = {},0
    while cursor <= last_day:
        if cursor.weekday() < 5:
            price *= 1+rate+amplitude*sin(i*.73+phase)+amplitude*.4*cos(i*1.91+phase)
            result[cursor.isoformat()] = price
            i += 1
        cursor += timedelta(days=1)
    return result


def next_review_after(confirmation):
    # The demonstration only reviews at an opening or noon, on weekdays.
    if confirmation.hour <= 8:
        now,phase = confirmation.replace(hour=9,minute=30),"open"
    elif confirmation.hour == 12:
        now,phase = confirmation.replace(minute=20),"noon"
    else:
        now,phase = (confirmation+timedelta(days=1)).replace(hour=9,minute=30),"open"
    while now.weekday() >= 5:
        now,phase = (now+timedelta(days=1)).replace(hour=9,minute=30),"open"
    return now,phase


def demo_reviews():
    bars = []
    for i in range(700):
        opened = 100+sin(i*.13)*8
        close = opened+(.02 if i%7 == 0 else sin(i*.31)*.6)
        bars.append(dict(o=opened,h=max(opened,close)+.3,l=min(opened,close)-.3,
            c=close,v=5000 if i%7 == 0 else 1000+i%53))
    marks = indicator.calculate_signals(bars,last_bar_closed=True)
    plus = next(i for i,s in enumerate(marks) if i >= 400 and s == 1)
    cross = next(i for i,s in enumerate(marks) if i > plus and s == -1)
    noon = datetime(2026,10,13,12,tzinfo=ET)
    for i,b in enumerate(bars):
        # One observed hourly candle in each fake four-hour window. The other
        # hours are deliberately empty. The real grouping code builds the bars.
        start = noon+timedelta(hours=4*(i-plus-1))
        b["t"] = int(start.timestamp()*1000)
    cross_time = noon+timedelta(hours=4*(cross-plus))
    exit_review,exit_phase = next_review_after(cross_time)
    times = [(noon.replace(hour=9,minute=30),"open",plus),
        (noon.replace(minute=20),"noon",plus),(exit_review,exit_phase,cross)]
    output = []
    for now,phase,end in times:
        last = now.date()-timedelta(days=1)
        while last.weekday() >= 5:
            last -= timedelta(days=1)
        histories = dict(DEMO_A=daily_history(last,.002),DEMO_B=daily_history(last,.0012,1.8),
            SPY=daily_history(last,.0003),SGOV=daily_history(last,.00005,3,.00001))
        output.append(dict(as_of=now.isoformat(),phase=phase,universe_date=last.isoformat(),
            universe={"DEMO_A":{"rank":1},"DEMO_B":{"rank":2}},daily_closes=histories,
            hourly={"DEMO_A":bars[:end+1]},
            prices={"DEMO_A":bars[end]["c"],"DEMO_B":100.,"SPY":100.,"SGOV":100.},
            labels={"DEMO_A":{"group":"10","industry":"1000"},"DEMO_B":{"group":"20","industry":"2000"}}))
    output[0]["account"] = dict(cash=100000.,positions={})
    return output
