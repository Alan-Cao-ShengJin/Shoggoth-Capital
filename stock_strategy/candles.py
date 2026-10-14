"""Aggregate observed hourly candles into fully completed four-hour events."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .config import CONFIG
from . import indicator

ET = ZoneInfo("America/New_York")

def four_hour_events(hourly, now):
    # Preserve the research's ET-midnight anchored 0/4/8/12/16/20 windows,
    # including observed extended-hours bars. Empty windows stay empty.
    groups = {}
    cutoff = now-timedelta(seconds=CONFIG.market_data_delay_seconds)
    for b in sorted(hourly, key=lambda b:b["t"]):
        instant = datetime.fromtimestamp(b["t"]/1000,ET)
        if instant+timedelta(hours=1) > cutoff:
            continue
        start = instant.replace(hour=instant.hour//4*4,minute=0,second=0,microsecond=0)
        if start+timedelta(hours=4) > cutoff:
            continue
        stamp = int(start.timestamp()*1000)
        if stamp not in groups:
            groups[stamp] = dict(b,t=stamp)
        else:
            current = groups[stamp]
            current.update(h=max(current["h"],b["h"]),l=min(current["l"],b["l"]),c=b["c"],v=current["v"]+b["v"])
    bars = list(groups.values())
    signals = indicator.calculate_signals(bars,last_bar_closed=True)
    return [dict(signal=s,confirmed_at=(datetime.fromtimestamp(b["t"]/1000,ET)+timedelta(hours=4)).isoformat())
        for b,s in zip(bars,signals) if s]
