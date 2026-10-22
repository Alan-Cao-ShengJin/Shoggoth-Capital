"""Core and local replay tests: synthetic data only, no credentials/network."""
from copy import deepcopy
from datetime import datetime, timedelta
from math import sin
from pathlib import Path
import hashlib
import json
import unittest
from unittest.mock import patch

from stock_strategy import CONFIG, PARAMETERS, risk, indicator
from stock_strategy.candles import ET, four_hour_events
from stock_strategy.selection import actions, quarter_due, next_review, build_watchlist
from stock_strategy.planner import stock_plan, fund_refill, whole_account_allowed, combined_risk
from stock_strategy.local import review, LocalLedger
from stock_strategy.allocation import execute
from examples.demo import demo_reviews, daily_history
from run_local import replay


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = demo_reviews()

    def setUp(self):
        self.payload = deepcopy(self.demo[0])
        self.now = datetime.fromisoformat(self.payload["as_of"])
        self.closes = self.payload["daily_closes"]
        self.prices = self.payload["prices"]
        self.labels = self.payload["labels"]
        self.universe = self.payload["universe"]

    def test_default_matches_retained_case(self):
        self.assertEqual(PARAMETERS["case"],"entry_momentum_legal_5bps")
        self.assertEqual((CONFIG.pool_size,risk.TARGET_POSITIONS),(100,20))
        self.assertEqual((risk.NORMAL_TARGET_WEIGHT,risk.ADD_STEP,risk.TRIM_FRACTION),(.075,.08,.025))
        self.assertEqual((risk.POSITION_CAP,risk.CORRELATED_GROUP_CAP),(.2,.35))

    def test_copied_sources_match_manifest(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root/"strategy_origin.json").read_text())
        for name,expected in manifest["copied_files"].items():
            self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(),expected,name)

    def test_pool_does_not_buy_without_plus(self):
        result = review(**self.payload)
        self.assertEqual(len(result["watchlist"]),2)
        self.assertEqual(set(result["account"]["positions"]),{"SGOV"})
        self.assertAlmostEqual(result["cash_weight"],.001,places=8)

    def test_full_demo_uses_real_computed_plus_and_x(self):
        opened,added,trimmed = replay(deepcopy(self.demo))
        self.assertEqual(added["signals"]["DEMO_A"]["action"],"add")
        self.assertEqual(trimmed["signals"]["DEMO_A"]["action"],"trim")
        self.assertAlmostEqual(added["weights"]["DEMO_A"],.075,places=8)
        old = added["account"]["positions"]["DEMO_A"]
        new = trimmed["account"]["positions"]["DEMO_A"]
        self.assertAlmostEqual(new,old*.975,places=8)
        self.assertEqual([(f["ticker"],f["side"]) for f in added["fills"]],[("SGOV","sell"),("DEMO_A","buy")])

    def test_repeat_review_is_rejected(self):
        result = review(**self.payload)
        repeated = dict(self.payload,account=result["account"],state=result["state"])
        with self.assertRaisesRegex(ValueError,"move forward"):
            review(**repeated)

    def test_preparation_never_fills_or_consumes(self):
        result = review(**dict(self.payload,phase="prepare"))
        self.assertEqual(result["fills"],[])
        self.assertEqual(result["account"],self.payload["account"])
        self.assertEqual(result["state"]["last_signal"],{})
        self.assertNotIn("last_review",result["state"])

    def test_current_and_future_daily_rows_are_ignored(self):
        original = review(**self.payload)
        for rows in self.payload["daily_closes"].values():
            rows[self.now.date().isoformat()] = 1e8
            rows[(self.now.date()+timedelta(days=1)).isoformat()] = .01
        self.assertEqual(review(**self.payload),original)

    def test_invalid_prices_stale_data_and_short_fund_rejected(self):
        bad = deepcopy(self.payload)
        bad["prices"]["SGOV"] = float('nan')
        with self.assertRaisesRegex(ValueError,"positive finite"):
            review(**bad)
        bad = deepcopy(self.payload)
        bad["as_of"] = (self.now+timedelta(days=10)).isoformat()
        with self.assertRaisesRegex(ValueError,"stale"):
            review(**bad)
        bad = deepcopy(self.payload)
        bad["account"]["positions"] = {"SGOV":-1.}
        with self.assertRaisesRegex(ValueError,"long-only"):
            review(**bad)

    def test_universe_date_must_agree(self):
        with self.assertRaisesRegex(ValueError,"must agree"):
            review(**dict(self.payload,universe_date="2026-10-09"))

    def test_invalid_or_duplicate_observed_candles_rejected(self):
        bad = deepcopy(self.payload)
        bar = bad["hourly"]["DEMO_A"][0]
        bad["hourly"]["DEMO_A"].append(dict(bar))
        with self.assertRaisesRegex(ValueError,"duplicate"):
            review(**bad)
        bad = deepcopy(self.payload)
        bad["hourly"]["DEMO_A"][0]["v"] = -1.
        with self.assertRaisesRegex(ValueError,"invalid candle"):
            review(**bad)

    def test_quarterly_watchlist(self):
        state = dict(watchlist_date="2026-10-12")
        self.assertFalse(quarter_due(state,"2026-11-12"))
        self.assertTrue(quarter_due(state,"2027-01-12"))
        self.assertEqual(next_review("2026-11-30"),"2027-02-28")
        ranked = build_watchlist(self.universe,self.closes,sorted(self.closes["SPY"]))
        self.assertEqual(ranked,["DEMO_A","DEMO_B"])

    def test_unknown_momentum_retains_and_blocks(self):
        calendar = sorted(self.closes["SPY"])
        self.closes["DEMO_A"].pop(calendar[-1])
        state = dict(activated_at=(self.now-timedelta(days=1)).isoformat(),last_signal={})
        signals,_,_ = actions(self.universe,self.closes,calendar,["DEMO_A"],["DEMO_A"],{},state,self.now)
        self.assertEqual(signals["DEMO_A"]["action"],"hold")
        self.assertTrue(signals["DEMO_A"]["needs_data_review"])
        self.assertTrue(signals["DEMO_A"]["eligible"])

    def test_known_momentum_failure_exits_without_x(self):
        calendar = sorted(self.closes["SPY"])
        self.closes["DEMO_A"][calendar[-1]] = 1.
        state = dict(activated_at=(self.now-timedelta(days=1)).isoformat(),last_signal={})
        signals,_,_ = actions(self.universe,self.closes,calendar,["DEMO_A"],["DEMO_A"],{},state,self.now)
        self.assertEqual(signals["DEMO_A"]["action"],"exit")

    def test_holding_outside_pool_can_keep_adding(self):
        state = dict(activated_at=(self.now-timedelta(days=1)).isoformat(),last_signal={})
        event = dict(signal=1,confirmed_at=(self.now-timedelta(minutes=20)).isoformat())
        signals,_,_ = actions(self.universe,self.closes,sorted(self.closes["SPY"]),["DEMO_B"],["DEMO_A"],{"DEMO_A":[event]},state,self.now)
        self.assertEqual(signals["DEMO_A"]["action"],"add")

    def test_consumed_and_backdated_markers_do_not_repeat(self):
        event = dict(signal=1,confirmed_at=(self.now-timedelta(minutes=20)).isoformat())
        state = dict(activated_at=(self.now-timedelta(days=1)).isoformat(),last_signal={"DEMO_A":event["confirmed_at"]})
        signals,_,_ = actions(self.universe,self.closes,sorted(self.closes["SPY"]),["DEMO_A"],[],{"DEMO_A":[event]},state,self.now)
        self.assertEqual(signals["DEMO_A"]["action"],"hold")
        state["last_signal"] = {}
        state["last_review"] = self.now.isoformat()
        signals,_,_ = actions(self.universe,self.closes,sorted(self.closes["SPY"]),["DEMO_A"],[],{"DEMO_A":[event]},state,self.now+timedelta(minutes=1))
        self.assertEqual(signals["DEMO_A"]["action"],"hold")

    def test_plus_targets_seven_point_five_percent(self):
        plan = stock_plan(dict(cash=100000.,positions={}),{"DEMO_A":dict(action="add",eligible=True,momentum_score=1.)},self.prices,self.closes,self.labels,self.now.isoformat())
        self.assertAlmostEqual(plan["orders"][0]["notional"],7500.)

    def test_gentle_trim_is_two_point_five_percent(self):
        self.prices["DEMO_A"] = 100.
        plan = stock_plan(dict(cash=92500.,positions={"DEMO_A":75.}),{"DEMO_A":dict(action="trim",eligible=True)},self.prices,self.closes,self.labels,self.now.isoformat())
        self.assertAlmostEqual(plan["orders"][0]["notional"],187.5)

    def test_position_and_industry_drift_are_repaired(self):
        self.prices["DEMO_A"] = 100.
        plan = stock_plan(dict(cash=75000.,positions={"DEMO_A":250.}),{},self.prices,self.closes,self.labels,self.now.isoformat())
        self.assertTrue(all(o["side"] == "sell" for o in plan["orders"]))
        self.assertLessEqual(plan["weights"]["DEMO_A"],.15)

    def test_sgov_is_not_limited_to_twenty_percent(self):
        self.assertAlmostEqual(fund_refill(dict(cash=100000.,positions={}),self.prices,self.closes,self.labels,self.now.isoformat()),99900.)

    def test_instrument_count_includes_fund(self):
        held = {f"S{i}":1. for i in range(20)}
        held["SGOV"] = 1.
        self.assertFalse(whole_account_allowed(dict(cash=97900.,positions=held),{t:100. for t in held},self.closes,self.labels,self.now.isoformat()))

    def test_connected_correlated_group_cap(self):
        held,prices,closes,labels = {},{},{},{}
        for i in range(5):
            ticker = f"S{i}"
            held[ticker],prices[ticker] = 80.,100.
            closes[ticker] = self.closes["DEMO_A"]
            labels[ticker] = dict(group=str(i),industry=str(i))
        self.assertFalse(whole_account_allowed(dict(cash=60000.,positions=held),prices,closes,labels,self.now.isoformat()))

    def test_missing_labels_block_buys(self):
        self.labels["DEMO_A"] = None
        self.prices["DEMO_A"] = 100.
        self.assertFalse(whole_account_allowed(dict(cash=92500.,positions={"DEMO_A":75.}),self.prices,self.closes,self.labels,self.now.isoformat()))

    def test_combined_sgov_risk_is_repaired_without_x(self):
        self.closes["SGOV"] = daily_history(self.now.date()-timedelta(days=1),.00005,amplitude=.03)
        ledger = LocalLedger(dict(cash=10000.,positions={"SGOV":900.}),self.prices)
        self.assertLess(risk.allowed_risk_scale(combined_risk(ledger.snapshot(),self.prices,self.closes,self.now.isoformat())),1.)
        fills = execute(ledger,dict(orders=[]),self.prices,self.closes,self.labels,self.now.isoformat(),"noon",{})
        self.assertEqual(fills[0]["ticker"],"SGOV")
        self.assertGreaterEqual(risk.allowed_risk_scale(combined_risk(ledger.snapshot(),self.prices,self.closes,self.now.isoformat())),1-1e-10)

    def test_no_fund_refill_at_noon(self):
        ledger = LocalLedger(dict(cash=100000.,positions={}),self.prices)
        fills = execute(ledger,dict(orders=[]),self.prices,self.closes,self.labels,self.now.isoformat(),"noon",{})
        self.assertEqual(fills,[])

    def test_missing_combined_history_blocks_buys(self):
        ledger = LocalLedger(dict(cash=50000.,positions={"SGOV":500.}),self.prices)
        self.closes["SGOV"] = {}
        fills = execute(ledger,dict(orders=[dict(ticker="DEMO_A",side="buy",notional=7500.,shares=75.,reasons=["fresh +"])]),self.prices,self.closes,self.labels,self.now.isoformat(),"noon",{"DEMO_A":self.now.isoformat()})
        self.assertEqual(fills[0]["status"],"blocked")
        self.assertEqual(ledger.snapshot()["positions"],{"SGOV":500.})

    def test_failed_sale_stops_the_buy(self):
        ledger = LocalLedger(dict(cash=0.,positions={"SGOV":1000.}),self.prices)
        ledger.send = unittest.mock.Mock(side_effect=ValueError("sale failed"))
        with self.assertRaisesRegex(ValueError,"sale failed"):
            execute(ledger,dict(orders=[dict(ticker="DEMO_A",side="buy",notional=7500.,shares=75.,reasons=["fresh +"])]),self.prices,self.closes,self.labels,self.now.isoformat(),"noon",{"DEMO_A":self.now.isoformat()})
        self.assertEqual(ledger.send.call_args.args[:2],("SGOV","sell"))
        self.assertEqual(ledger.send.call_count,1)

    def test_ledger_cannot_borrow_short_or_duplicate(self):
        ledger = LocalLedger(dict(cash=1000.,positions={}),self.prices)
        with self.assertRaisesRegex(ValueError,"borrow"):
            ledger.send("SGOV","buy","too large",notional=2000.)
        first = ledger.send("SGOV","buy","same",notional=100.)
        second = ledger.send("SGOV","buy","same",notional=100.)
        self.assertTrue(second["reused"])
        self.assertEqual(first["qty_filled"],1.)
        self.assertEqual(ledger.snapshot()["cash"],900.)
        with self.assertRaisesRegex(ValueError,"short"):
            ledger.send("SGOV","sell","too much",qty=2.)

    def test_closed_indicator_prefix_never_changes(self):
        bars = self.payload["hourly"]["DEMO_A"]
        full = indicator.calculate_signals(bars,last_bar_closed=True)
        self.assertTrue(any(full))
        for length in (150,220,300,400):
            self.assertEqual(indicator.calculate_signals(bars[:length],last_bar_closed=True),full[:length])

    def test_four_hour_volume_and_delayed_confirmation(self):
        bars = [dict(t=int(datetime(2026,10,13,h,tzinfo=ET).timestamp()*1000),o=100.,h=102.,l=99.,c=101.,v=10.) for h in range(8,15)]
        now = datetime(2026,10,13,12,20,tzinfo=ET)
        with patch("stock_strategy.candles.indicator.calculate_signals",return_value=[1]) as calculate:
            events = four_hour_events(bars,now)
            self.assertEqual(calculate.call_args.args[0][0]["v"],40.)
            self.assertEqual(events[0]["confirmed_at"],"2026-10-13T12:00:00-04:00")
        with patch("stock_strategy.candles.indicator.calculate_signals",return_value=[]) as calculate:
            self.assertEqual(four_hour_events(bars,now.replace(minute=5)),[])
            self.assertEqual(calculate.call_args.args[0],[])


if __name__ == "__main__":
    unittest.main()
