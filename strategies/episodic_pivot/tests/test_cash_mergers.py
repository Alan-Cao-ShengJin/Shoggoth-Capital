"""A verified cash merger cancels shares without requiring invented market prices."""
from dataclasses import replace
from tempfile import TemporaryDirectory
import unittest

from episodic_pivot.backtest import run
from episodic_pivot.massive import MassiveSource
from test_episodic_pivot import S, SyntheticSource


class MergerSource(SyntheticSource):
    def __init__(self):
        super().__init__(tickers=("AAA", "BBB"))

    def gappers(self, day, prev_day, settings):
        if day == self.days[0]:
            return [("AAA", 9.5)]
        return [("BBB", 9.5)] if day == self.days[1] else []

    def daily(self, ticker):
        rows = super().daily(ticker)
        return [r for r in rows if r["date"] < self.days[1]] if ticker == "AAA" else rows

    def cash_merger(self, ticker, day):
        if ticker == "AAA" and day == self.days[1]:
            return {"cash_per_share": 12.0, "effective_date": day, "source": "synthetic verified event"}
        return None


class CashMergerTests(unittest.TestCase):
    def replay(self):
        source = MergerSource()
        try:
            result = run(source, source.days[0], source.days[2], replace(S, max_open=1))
        except ValueError as error:
            self.fail("A verified cash merger should replace the missing trading quote: " + str(error))
        return source, result

    def test_merger_cancels_shares_and_frees_capacity_on_the_effective_date(self):
        source, result = self.replay()
        trade = next(t for t in result["trades"] if t["symbol"] == "AAA")
        self.assertEqual(trade["exit_date"], source.days[1])
        self.assertIn("cash-merger", trade["fills"][-1])
        self.assertEqual(result["days"][1]["entries"], ["BBB"])
        self.assertEqual(trade["cash_merger"]["cash_per_share"], 12.0)

    def test_entitlement_does_not_incur_market_sale_slippage_or_commission(self):
        _, result = self.replay()
        trade = next(t for t in result["trades"] if t["symbol"] == "AAA")
        expected = trade["qty"] * (12.0 - trade["entry"] - S.commission_per_share)
        self.assertAlmostEqual(trade["pnl"], expected, delta=.01)
        self.assertEqual(result["equity_curve"][-1]["equity"], result["summary"]["net_pnl"])

    def test_reta_has_an_exchange_verified_event_only_on_its_closing_date(self):
        with TemporaryDirectory() as folder:
            source = MassiveSource("2023-01-01", "2023-10-31", folder)
            lookup = getattr(source, "cash_merger", None)
            self.assertTrue(callable(lookup), "Verified merger catalog is missing")
            event = lookup("RETA", "2023-09-26")
            self.assertEqual(event["cash_per_share"], 172.50)
            self.assertEqual(event["last_trading_date"], "2023-09-25")
            self.assertIn("nasdaqtrader.com", event["source"])
            self.assertIsNone(lookup("RETA", "2023-09-25"))
            self.assertIsNone(lookup("OTHER", "2023-09-26"))

    def test_unexplained_missing_held_price_still_stops_the_run(self):
        class UnknownEventSource(MergerSource):
            def cash_merger(self, ticker, day):
                return None
        source = UnknownEventSource()
        with self.assertRaisesRegex(ValueError, "Missing daily price"):
            run(source, source.days[0], source.days[2], S)


if __name__ == "__main__":
    unittest.main()
