"""Episodic Pivot tests: synthetic data only, no credentials/network."""
from dataclasses import replace
import unittest

from episodic_pivot.backtest import run
from episodic_pivot.rules import Position, Settings, above_rising_ema, entry_signal, position_size

S = Settings(min_market_cap=0, market_filter="off")


def bar(minute, o, h, l, c, v):
    return {"min": minute, "o": o, "h": h, "l": l, "c": c, "v": v}


def gap_day(break_at=9 * 60 + 40, volume=600_000):
    """Premarket, a 9:30 bar with high 11.0 / low 10.6, then a break of 11.0 at `break_at`."""
    bars = [bar(8 * 60, 10.5, 10.8, 10.4, 10.7, volume)]
    minute = 9 * 60 + 30
    bars.append(bar(minute, 10.8, 11.0, 10.6, 10.9, volume))
    while minute < 16 * 60 - 5:
        minute += 5
        if minute < break_at:
            bars.append(bar(minute, 10.9, 10.95, 10.7, 10.9, 50_000))
        else:
            bars.append(bar(minute, 10.95, 11.4, 10.9, 11.3, 80_000))
    return bars


class EntryTests(unittest.TestCase):
    def test_buys_the_opening_range_high_break(self):
        kind, sig = entry_signal(S, gap_day(), prev_close=9.5, adr=0.08, adv=1_000_000)
        self.assertEqual(kind, "signal")
        self.assertEqual(sig["bar"]["min"], 9 * 60 + 40)
        self.assertAlmostEqual(sig["fill"], 11.0 + S.slippage)
        self.assertAlmostEqual(sig["stop"], 10.6)
        self.assertAlmostEqual(sig["gap_pct"], (10.8 / 9.5 - 1) * 100)

    def test_waits_for_a_full_days_volume(self):
        kind, why = entry_signal(S, gap_day(volume=100_000), prev_close=9.5, adr=0.08, adv=10_000_000)
        self.assertEqual((kind, why), ("skip", "volume never reached the bar"))

    def test_skips_a_stop_wider_than_the_daily_range(self):
        kind, why = entry_signal(S, gap_day(), prev_close=9.5, adr=0.01, adv=1_000_000)
        self.assertEqual((kind, why), ("skip", "stop wider than ADR"))

    def test_no_entry_after_the_window(self):
        kind, _ = entry_signal(S, gap_day(break_at=12 * 60), prev_close=9.5, adr=0.08, adv=1_000_000)
        self.assertEqual(kind, "skip")

    def test_later_bars_do_not_change_the_entry(self):
        full = gap_day()
        _, a = entry_signal(S, full, 9.5, 0.08, 1_000_000)
        cut = [b for b in full if b["min"] <= 9 * 60 + 40]
        _, b = entry_signal(S, cut, 9.5, 0.08, 1_000_000)
        self.assertEqual((a["fill"], a["stop"], a["bar"]), (b["fill"], b["stop"], b["bar"]))

    def test_size_is_risk_based_and_capped(self):
        self.assertEqual(position_size(S, 11.0, 10.0), 250)          # 1% of 25k / $1 risk
        self.assertEqual(position_size(S, 100.0, 99.9), 62)          # capped at 25% of 25k


class PositionTests(unittest.TestCase):
    def position(self, qty=100):
        sig = {"fill": 11.0, "stop": 10.0, "bar": {"min": 9 * 60 + 40}, "gap_pct": 15.0, "vol_ratio": 1.5, "adr": 0.08}
        return Position(S, "TEST", "2026-01-05", sig, qty)

    def test_gap_through_the_stop_fills_at_the_open(self):
        p = self.position()
        self.assertTrue(p.daily({"o": 9.5, "h": 9.8, "l": 9.4, "c": 9.6}, None, "2026-01-06"))
        self.assertIn("100@9.48 stop", p.fills[0])

    def test_partial_on_day_three_then_breakeven(self):
        p = self.position()
        for d in ("01-06", "01-07"):
            self.assertFalse(p.daily({"o": 11.5, "h": 12, "l": 11.2, "c": 11.8}, 11.0, f"2026-{d}"))
        self.assertFalse(p.daily({"o": 12, "h": 12.6, "l": 11.9, "c": 12.5}, 11.2, "2026-01-08"))
        self.assertTrue(p.partial_done)
        self.assertEqual(p.qty, 50)
        self.assertEqual(p.stop, 11.0)

    def test_trailing_exit_on_close_below_the_average(self):
        p = self.position()
        self.assertTrue(p.daily({"o": 11.5, "h": 11.6, "l": 10.5, "c": 10.6}, 10.8, "2026-01-06"))
        self.assertIn("close<20dma", p.fills[-1])
        self.assertAlmostEqual(p.result("2026-01-06")["pnl"], round(100 * (10.6 - S.slippage) - 100 * 11.0 - 200 * S.commission_per_share, 2))


def closes(*cs):
    return [{"c": c} for c in cs]


class MarketFilterTests(unittest.TestCase):
    def test_needs_a_close_above_a_rising_average(self):
        rising = closes(*range(1, 40))
        self.assertTrue(above_rising_ema(rising, len(rising), 21))

    def test_uses_only_the_prior_close(self):
        rows = closes(*range(1, 40)) + [{"c": 0.5}]       # today's collapse is not known before the open
        self.assertTrue(above_rising_ema(rows, len(rows) - 1, 21))

    def test_off_when_below_the_average_or_falling(self):
        self.assertFalse(above_rising_ema(closes(*range(40, 1, -1)), 39, 21))
        self.assertFalse(above_rising_ema(closes(*[10] * 40), 40, 21))  # flat: not rising

    def test_off_without_enough_history(self):
        self.assertFalse(above_rising_ema(closes(*range(1, 10)), 9, 21))
        self.assertFalse(above_rising_ema(closes(*range(1, 40)), None, 21))


class SyntheticSource:
    """Six tickers gap on the same day; each then trends up for the rest of the period.

    QQQ and IWM are flat before the period, or rising if `market_rising`.
    """

    def __init__(self, tickers=("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"), market_rising=False):
        self.tickers = tickers
        self.market_rising = market_rising
        self.days = [f"2026-02-{d:02d}" for d in range(2, 28) if d not in (7, 8, 14, 15, 21, 22)]
        self.history = [f"2026-01-{d:02d}" for d in range(2, 31) if d not in (3, 4, 10, 11, 17, 18, 24, 25)]

    def trading_days(self, start, end):
        return [d for d in self.days if start <= d <= end]

    def previous_trading_day(self, day):
        allowed = self.history + self.days
        return allowed[allowed.index(day) - 1]

    def gappers(self, day, prev_day, settings):
        return [(t, 9.5) for t in self.tickers] if day == self.days[0] else []

    def market_cap(self, ticker, day):
        return 5e9

    def daily(self, ticker):
        if ticker in ("QQQ", "IWM"):
            dates = self.history + self.days
            return [{"date": d, "o": 100, "h": 101, "l": 99, "c": 100 + (k if self.market_rising else 0), "v": 1}
                    for k, d in enumerate(dates)]
        rows = [{"date": d, "o": 9.5, "h": 9.9, "l": 9.2, "c": 9.5, "v": 1_000_000} for d in self.history]
        price = 11.3
        for d in self.days:
            rows.append({"date": d, "o": price, "h": price * 1.03, "l": price * 0.99, "c": price * 1.02, "v": 1_500_000})
            price *= 1.02
        return rows

    def intraday(self, ticker, day):
        return gap_day()


class BacktestTests(unittest.TestCase):
    def test_holds_at_most_four_and_closes_the_rest_at_the_end(self):
        src = SyntheticSource()
        result = run(src, src.days[0], src.days[-1], S)
        self.assertEqual(result["summary"]["trades"], 4)
        self.assertEqual(result["days"][0]["entries"], ["AAA", "BBB", "CCC", "DDD"])
        self.assertTrue(any("4 positions already open" in s for s in result["days"][0]["no_entry"]))
        self.assertTrue(all(t["fills"][0].split()[1].startswith("partial") for t in result["trades"]))
        self.assertEqual(result["summary"]["still_open_at_end"], 4)
        self.assertGreater(result["summary"]["net_pnl"], 0)

    def test_market_cap_floor(self):
        src = SyntheticSource()
        result = run(src, src.days[0], src.days[-1], replace(S, min_market_cap=1e10))
        self.assertEqual(result["summary"]["trades"], 0)
        self.assertTrue(all(s.endswith("below market cap floor") for s in result["days"][0]["no_entry"]))

    def test_market_filter_blocks_entries_in_a_flat_market(self):
        src = SyntheticSource()
        result = run(src, src.days[0], src.days[-1], replace(S, market_filter="qqq_iwm", market_ema=10))
        self.assertEqual(result["summary"]["trades"], 0)
        self.assertFalse(result["days"][0]["market_ok"])

    def test_market_filter_allows_entries_in_an_uptrend(self):
        src = SyntheticSource(market_rising=True)
        result = run(src, src.days[0], src.days[-1], replace(S, market_filter="qqq_iwm", market_ema=10))
        self.assertTrue(result["days"][0]["market_ok"])
        self.assertEqual(result["summary"]["trades"], 4)

    def test_the_default_is_the_qqq_iwm_filter(self):
        self.assertEqual((Settings().market_filter, Settings().market_ema), ("qqq_iwm", 21))


if __name__ == "__main__":
    unittest.main()
