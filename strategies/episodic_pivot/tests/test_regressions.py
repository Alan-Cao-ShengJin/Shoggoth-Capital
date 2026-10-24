"""Regression coverage for causal fills, position capacity and data/accounting failures."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from episodic_pivot.backtest import run
from episodic_pivot.massive import MassiveSource
from episodic_pivot.rules import Position, entry_signal
from test_episodic_pivot import S, SyntheticSource, gap_day


class CloseExitSource(SyntheticSource):
    """Four holdings exit at tomorrow's close, after a fifth stock's morning signal."""
    def __init__(self):
        super().__init__(tickers=("AAA", "BBB", "CCC", "DDD", "EEE"))

    def gappers(self, day, prev_day, settings):
        if day == self.days[0]:
            return [(ticker, 9.5) for ticker in self.tickers[:4]]
        return [("EEE", 9.5)] if day == self.days[1] else []

    def daily(self, ticker):
        rows = super().daily(ticker)
        if ticker != "EEE":
            for row in rows[:len(self.history) - 1]:
                row.update(o=12.0, h=12.5, l=11.5, c=12.0)
            next(row for row in rows if row["date"] == self.days[1]).update(
                o=11.6, h=11.7, l=10.65, c=10.7)
        else:
            next(row for row in rows if row["date"] == self.days[0]).update(
                o=9.5, h=9.9, l=9.2, c=9.5)
        return rows


class IntradayExitSource(SyntheticSource):
    def __init__(self):
        super().__init__(tickers=("AAA", "BBB", "CCC"))

    def intraday(self, ticker, day):
        rows = gap_day(break_at={"AAA": 580, "BBB": 585, "CCC": 610}[ticker])
        if ticker == "AAA":
            next(row for row in rows if row["min"] == 600).update(o=11.3, h=11.4, l=9.9, c=10.0)
        return rows


class EntryTimingTests(unittest.TestCase):
    def test_entry_candle_eventual_low_cannot_change_entry_or_initial_stop(self):
        original = gap_day()
        changed = deepcopy(original)
        next(row for row in changed if row["min"] == 580).update(l=8.0, c=8.2)
        kind, first = entry_signal(S, original, 9.5, 0.08, 1_000_000)
        later_kind, second = entry_signal(S, changed, 9.5, 0.08, 1_000_000)
        self.assertEqual((kind, later_kind), ("signal", "signal"))
        self.assertEqual((first["fill"], first["stop"]), (second["fill"], second["stop"]))

    def test_a_stop_in_the_entry_candle_is_executed(self):
        rows = gap_day()
        next(row for row in rows if row["min"] == 580).update(l=10.5, c=10.55)
        _, signal = entry_signal(S, rows, 9.5, 0.08, 1_000_000)
        position = Position(S, "AAA", "2026-02-02", signal, 100)
        position.entry_day(signal["rest"], "2026-02-02")
        self.assertEqual(position.qty, 0)
        self.assertAlmostEqual(position.proceeds, 100 * (10.6 - S.slippage - S.commission_per_share))


class PortfolioTimingTests(unittest.TestCase):
    def test_carried_intraday_stops_release_capacity_only_after_their_candle(self):
        for gap_at_candle_open in (False, True):
            with self.subTest(gap_at_candle_open=gap_at_candle_open):
                class CarriedStopSource(SyntheticSource):
                    def gappers(self, day, prev_day, settings):
                        return [("AAA", 9.5)] if day == self.days[0] else [("BBB", 9.5), ("CCC", 9.5)]

                    def daily(self, ticker):
                        rows = super().daily(ticker)
                        if ticker == "AAA":
                            next(row for row in rows if row["date"] == self.days[1]).update(
                                o=11.3, h=11.4, l=10.4, c=11.3)
                        return rows

                    def intraday(self, ticker, day):
                        if ticker == "AAA" and day == self.days[1]:
                            rows = [{"min": minute, "o": 11.3, "h": 11.4, "l": 11.2, "c": 11.3, "v": 80000}
                                    for minute in range(570, 960, 5)]
                            next(row for row in rows if row["min"] == 600).update(
                                o=10.5 if gap_at_candle_open else 11.3, l=10.4, c=10.7)
                            return rows
                        return gap_day(break_at=610 if ticker == "CCC" else 580)
                source = CarriedStopSource(tickers=("AAA", "BBB", "CCC"))
                result = run(source, source.days[0], source.days[1], replace(S, max_open=1))
                self.assertEqual(result["days"][1]["entries"], ["CCC"])
                self.assertTrue(all(row["peak_open"] <= 1 for row in result["days"]))

    def test_same_candle_stop_does_not_free_capacity_for_a_simultaneous_entry(self):
        class SameCandleSource(SyntheticSource):
            def intraday(self, ticker, day):
                rows = gap_day()
                if ticker == "AAA":
                    next(row for row in rows if row["min"] == 580).update(l=10.5, c=10.55)
                return rows
        source = SameCandleSource(tickers=("AAA", "BBB"))
        result = run(source, source.days[0], source.days[0], replace(S, max_open=1))
        self.assertEqual(result["days"][0]["entries"], ["AAA"])

    def test_closing_exits_do_not_free_slots_for_morning_entries(self):
        source = CloseExitSource()
        result = run(source, source.days[0], source.days[1], S)
        self.assertEqual(result["days"][0]["entries"], ["AAA", "BBB", "CCC", "DDD"])
        self.assertEqual(result["days"][1]["entries"], [])
        self.assertTrue(any("4 positions already open" in reason for reason in result["days"][1]["no_entry"]))

    def test_slots_are_reused_only_after_an_intraday_stop(self):
        source = IntradayExitSource()
        result = run(source, source.days[0], source.days[0], replace(S, max_open=1))
        # AAA enters at 09:40, BBB signals 09:45, AAA stops at 10:00-10:05,
        # and CCC signals at 10:10, after capacity really becomes available.
        self.assertEqual(result["days"][0]["entries"], ["AAA", "CCC"])

    def test_a_gap_stop_at_the_open_frees_capacity_for_morning_entries(self):
        class GapExitSource(CloseExitSource):
            def daily(self, ticker):
                rows = super().daily(ticker)
                if ticker != "EEE":
                    next(row for row in rows if row["date"] == self.days[1]).update(o=9.5, h=10.0, l=9.0, c=9.8)
                return rows
        source = GapExitSource()
        result = run(source, source.days[0], source.days[1], S)
        self.assertEqual(result["days"][1]["entries"], ["EEE"])


class TerminalAccountingTests(unittest.TestCase):
    def test_terminal_liquidation_charges_slippage_and_both_commissions(self):
        source = SyntheticSource(tickers=("AAA",))
        day = source.days[0]
        result = run(source, day, day, S)
        trade = result["trades"][0]
        close = next(row["c"] for row in source.daily("AAA") if row["date"] == day)
        expected = round(trade["qty"] * (close - S.slippage - trade["entry"] - 2 * S.commission_per_share), 2)
        self.assertAlmostEqual(result["summary"]["net_pnl"], expected)

    def test_final_equity_curve_reconciles_to_summary_after_liquidation(self):
        source = SyntheticSource()
        result = run(source, source.days[0], source.days[-1], S)
        self.assertEqual(result["equity_curve"][-1]["equity"], result["summary"]["net_pnl"])
        self.assertEqual(result["equity_curve"][-1]["open"], 0)


class DataFailureTests(unittest.TestCase):
    def test_an_empty_trading_range_has_an_explicit_data_error(self):
        source = SyntheticSource()
        with self.assertRaisesRegex(ValueError, "No trading"):
            run(source, "2027-01-01", "2027-01-02", S)

    def test_a_held_stop_requires_intraday_data_to_order_it_against_entries(self):
        class MissingBarsSource(CloseExitSource):
            def daily(self, ticker):
                rows = super().daily(ticker)
                if ticker != "EEE":
                    next(row for row in rows if row["date"] == self.days[1])["l"] = 10.0
                return rows

            def intraday(self, ticker, day):
                return [] if day == self.days[1] else super().intraday(ticker, day)
        source = MissingBarsSource()
        with self.assertRaisesRegex(ValueError, "intraday"):
            run(source, source.days[0], source.days[1], S)

    def test_http_failure_raises_and_is_not_cached_as_empty_data(self):
        with TemporaryDirectory() as folder:
            source = MassiveSource("2026-02-02", "2026-02-02", folder)
            source.key = "synthetic-test-key"
            error = HTTPError("https://example.invalid", 403, "Forbidden", {}, None)
            with patch("episodic_pivot.massive.urlopen", side_effect=error):
                with self.assertRaisesRegex(RuntimeError, "403"):
                    source.trading_days("2026-02-02", "2026-02-02")
            self.assertEqual(list(Path(folder).glob("*.json")), [])

    def test_old_cached_http_errors_are_rejected(self):
        with TemporaryDirectory() as folder:
            source = MassiveSource("2026-02-02", "2026-02-02", folder)
            path = "/v2/aggs/grouped/locale/us/market/stocks/2026-02-02?adjusted=true"
            source._cache_file(path).write_text(json.dumps({"results": None, "status": "HTTP 403"}))
            with self.assertRaisesRegex(RuntimeError, "403"):
                source.trading_days("2026-02-02", "2026-02-02")

    def test_api_error_payload_is_rejected_before_caching(self):
        with TemporaryDirectory() as folder:
            source = MassiveSource("2026-02-02", "2026-02-02", folder)
            source.key = "synthetic-test-key"
            response = BytesIO(json.dumps({"status": "ERROR", "error": "not entitled"}).encode())
            with patch("episodic_pivot.massive.urlopen", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "ERROR"):
                    source._api("/example")
            self.assertEqual(list(Path(folder).glob("*.json")), [])

    def test_successful_empty_holiday_data_remains_valid(self):
        with TemporaryDirectory() as folder:
            source = MassiveSource("2026-02-02", "2026-02-02", folder)
            path = "/v2/aggs/grouped/locale/us/market/stocks/2026-02-02?adjusted=true"
            source._cache_file(path).write_text(json.dumps({"status": "OK", "resultsCount": 0}))
            self.assertEqual(source.trading_days("2026-02-02", "2026-02-02"), [])


if __name__ == "__main__":
    unittest.main()
