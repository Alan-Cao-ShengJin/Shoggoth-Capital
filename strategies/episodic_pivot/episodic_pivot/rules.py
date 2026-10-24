"""Episodic Pivot rules as pure functions: no data source, no clock, no network.

Bars are dicts with o, h, l, c, v and, for 5-minute bars, `min` = minutes after midnight
New York time of the bar's start. Daily rows are dicts with date, o, h, l, c, v.
"""
from dataclasses import dataclass
from math import floor, isfinite

REGULAR_OPEN = 9 * 60 + 30


@dataclass(frozen=True)
class Settings:
    account: float = 25_000.0        # fixed sizing base (no compounding)
    risk_pct: float = 1.0            # risk per trade, % of account
    max_position_pct: float = 25.0   # largest position, % of account (his small-account rule)
    max_open: int = 4                # positions held at once (his small-account rule)
    min_gap_pct: float = 10.0        # open vs prior close (stated)
    min_price: float = 3.0           # ours
    min_dollar_volume: float = 5e6   # 20-day average and prior day; his rule is 50-100x the account
    min_market_cap: float = 1e9      # ours: a proxy for "a real company with strong earnings"
    volume_multiple: float = 1.0     # volume before entry vs 20-day average daily volume (stated)
    adr_multiple: float = 1.0        # max stop width vs 20-day average daily range (stated)
    entry_until_min: int = 11 * 60 + 30
    partial_day: int = 3             # sell part after 3-5 days (stated); in profit only
    partial_fraction: float = 0.5    # 1/3 to 1/2 (stated)
    trail_sma: int = 20              # exit on the first daily close below this SMA (10 or 20 stated)
    market_filter: str = "qqq_iwm"   # new entries only when the market is in an uptrend:
                                     #   "qqq_iwm" QQQ and IWM above a rising EMA (Martin Luk; default),
                                     #   "qqq_sma" QQQ 10-day SMA above 20-day, both rising (Qullamaggie),
                                     #   "off"
    market_ema: int = 21             # EMA length for "qqq_iwm" (15 and 30 tested as well)
    commission_per_share: float = 0.005
    slippage: float = 0.02


def round_half_up(x):
    return floor(x + 0.5)


def price2(x):
    return float(f"{x:.2f}")


def sma(rows, i, n):
    """Simple moving average of closes ending at row i (inclusive); None without enough rows."""
    if i + 1 < n:
        return None
    return sum(r["c"] for r in rows[i - n + 1:i + 1]) / n


def daily_stats(rows, i, lookback=20):
    """(average daily range as a fraction, average daily volume) over the sessions before row i."""
    past = rows[i - lookback:i]
    adr = sum(r["h"] / r["l"] - 1 for r in past) / lookback
    adv = sum(r["v"] for r in past) / lookback
    return adr, adv


def ema(rows, i, n):
    """Exponential moving average of closes at row i, seeded with the SMA of the first n closes."""
    if i + 1 < n:
        return None
    k = 2 / (n + 1)
    e = sma(rows, n - 1, n)
    for j in range(n, i + 1):
        e = rows[j]["c"] * k + e * (1 - k)
    return e


def above_rising_ema(rows, i, n):
    """As of the prior close (row i - 1): close above the n-day EMA, and the EMA higher than a day before."""
    if i is None or i < n + 1:
        return False
    p = i - 1
    e = ema(rows, p, n)
    return rows[p]["c"] > e and e > ema(rows, p - 1, n)


def qqq_sma_ok(qqq_rows, i):
    """As of the prior close: QQQ 10-day SMA above the 20-day and both rising."""
    if i is None or i < 22:
        return False
    p = i - 1
    s10, s20 = sma(qqq_rows, p, 10), sma(qqq_rows, p, 20)
    s10b, s20b = sma(qqq_rows, p - 1, 10), sma(qqq_rows, p - 1, 20)
    return s10 > s20 and s10 > s10b and s20 > s20b


def market_ok(settings, qqq, iwm):
    """Whether new entries are allowed today. `qqq` and `iwm` are (daily rows, index of today) pairs."""
    if settings.market_filter == "off":
        return True
    if settings.market_filter == "qqq_sma":
        return qqq_sma_ok(*qqq)
    if settings.market_filter == "qqq_iwm":
        return above_rising_ema(*qqq, settings.market_ema) and above_rising_ema(*iwm, settings.market_ema)
    raise ValueError(f"unknown market_filter {settings.market_filter!r}")


def entry_signal(settings, bars, prev_close, adr, adv):
    """Scan one gap day's 5-minute bars for the entry.

    Returns ("signal", dict) or ("skip", reason). The entry is a stop order at the opening-range
    high (first regular 5-minute bar), live only once premarket plus regular volume so far reaches
    `volume_multiple` x the 20-day average. The initial stop uses completed candles and the
    entry candle's opening price, never its eventual low. An ambiguous entry-candle stop is
    assumed to occur after entry, conservatively.
    """
    pm = [b for b in bars if b["min"] < REGULAR_OPEN]
    reg = [b for b in bars if b["min"] >= REGULAR_OPEN]
    if len(reg) < 2:
        return "skip", "missing regular bars"
    orh = reg[0]["h"]
    vol = sum(b["v"] for b in pm) + reg[0]["v"]
    lod = reg[0]["l"]
    for k in range(1, len(reg)):
        b = reg[k]
        if b["min"] >= settings.entry_until_min:
            break
        if vol >= settings.volume_multiple * adv and b["h"] > orh:
            fill = max(b["o"], orh) + settings.slippage
            stop = min(lod, b["o"])
            if (fill - stop) / fill > settings.adr_multiple * adr:
                return "skip", "stop wider than ADR"
            return "signal", {"bar": b, "fill": fill, "stop": stop, "orh": orh, "adr": adr,
                              "vol_ratio": vol / adv, "gap_pct": (reg[0]["o"] / prev_close - 1) * 100,
                              "rest": reg[k:]}
        vol += b["v"]
        lod = min(lod, b["l"])
    if vol < settings.volume_multiple * adv:
        return "skip", "volume never reached the bar"
    return "skip", "no opening-range-high break in the entry window"


def position_size(settings, fill, stop):
    risk = fill - stop
    return floor(min(settings.account * settings.risk_pct / 100 / risk,
                     settings.account * settings.max_position_pct / 100 / fill))


class Position:
    """An open EP trade. `entry` and `stop0` are kept to the cent, as quoted."""

    def __init__(self, settings, symbol, day, signal, qty):
        self.settings = settings
        self.symbol = symbol
        self.entry_date = day
        self.entry = price2(signal["fill"])
        self.stop0 = price2(signal["stop"])
        self.stop = signal["stop"]
        self.qty = self.init_qty = qty
        self.cost = qty * signal["fill"] + qty * settings.commission_per_share
        self.proceeds = 0.0
        self.fills = []
        self.days_held = 0
        self.partial_done = False
        self.info = {"entry_time": f"{signal['bar']['min'] // 60:02d}:{signal['bar']['min'] % 60:02d}",
                     "gap_pct": round(signal["gap_pct"], 1), "vol_ratio": round(signal["vol_ratio"], 2),
                     "adr_pct": round(100 * signal["adr"], 1)}

    def sell(self, px, qty, why, date):
        n = min(qty, self.qty)
        self.qty -= n
        self.fills.append(f"{n}@{px:.2f} {why} {date}")
        self.proceeds += n * px - n * self.settings.commission_per_share

    def cash_merger(self, price, day):
        """Value canceled shares at their verified cash entitlement, without trade costs."""
        if not isfinite(price) or price <= 0:
            raise ValueError("Invalid cash merger entitlement")
        self.fills.append(f"{self.qty}@{price:.2f} cash-merger {day}")
        self.proceeds += self.qty * price
        self.qty = 0

    def entry_day(self, rest_bars, day):
        """Include the entry candle; its opening price occurred before the purchase."""
        for i, b in enumerate(rest_bars):
            if self.stop_bar(b, day, check_open=i != 0):
                return

    def stop_bar(self, bar, day, *, check_open=True):
        """Execute a stop on one observed bar, with adverse gap pricing when applicable."""
        px = bar["o"] if check_open and bar["o"] <= self.stop else self.stop
        if (check_open and bar["o"] <= self.stop) or bar["l"] <= self.stop:
            why = "breakeven" if self.partial_done else "stop"
            self.sell(px - self.settings.slippage, self.qty, why, day)
        return self.qty <= 0

    def daily(self, bar, trail_value, day):
        """Manage one later session on its daily bar. Returns True when the position is closed."""
        self.days_held += 1
        if self.stop_bar(bar, day):
            return True
        return self.close_day(bar, trail_value, day)

    def close_day(self, bar, trail_value, day):
        """Apply close-only rules after intraday stops, without revisiting earlier lows."""
        s = self.settings
        if not self.partial_done and self.days_held >= s.partial_day and bar["c"] > self.entry:
            self.sell(bar["c"] - s.slippage, round_half_up(self.init_qty * s.partial_fraction), f"partial-d{self.days_held}", day)
            self.partial_done = True
            self.stop = max(self.stop, self.entry)
        if trail_value is not None and bar["c"] < trail_value:
            self.sell(bar["c"] - s.slippage, self.qty, f"close<{s.trail_sma}dma", day)
            return True
        return self.qty <= 0

    def result(self, exit_date):
        pnl = round(self.proceeds - self.cost, 2)
        return {"symbol": self.symbol, "entry_date": self.entry_date, **self.info, "entry": self.entry, "stop0": self.stop0,
                "qty": self.init_qty, "fills": self.fills, "exit_date": exit_date, "days_held": self.days_held, "pnl": pnl,
                "r_multiple": round(pnl / (self.init_qty * (self.entry - self.stop0)), 2)}
