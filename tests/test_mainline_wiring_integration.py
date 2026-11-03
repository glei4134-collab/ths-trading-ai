# -*- coding: utf-8 -*-
"""
Mainline Wiring End-to-End Integration Tests (Stage 1-3 -> Mainline Pipeline)
Verifies that all quantitative & engineering modules are actively wired into:
1. fast_data.py (5-level order book depth)
2. intraday_timing_engine.py (frame-to-frame VOI + LockFreeRingBuffer + AtomicSnapshotRecovery)
3. ai_brain.py & api/routes/system.py (LowodinOrthogonalizer + HRP/Black-Litterman PortfolioOptimizer)
4. account_manager.py & telemetry_audit.py (Sector Cap + Fractional Kelly/ATR + TamperEvidentAuditChain)
5. ai_scheduler.py (Non-blocking Almgren-Chriss trajectory slice queue)
"""
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


class TestMainlineWiringIntegration(unittest.TestCase):

    def test_step1_fast_data_five_level_depth(self):
        from fast_data import MockMarketDataProvider
        quotes = MockMarketDataProvider.generate_mock_realtime(["000001", "600519"])
        self.assertIn("000001", quotes)
        q = quotes["000001"]
        for key in ("bid_prices", "bid_vols", "ask_prices", "ask_vols"):
            self.assertIn(key, q)
            self.assertEqual(len(q[key]), 5)
        self.assertGreater(q["total_bid_vol"], q["bid1_vol"])
        self.assertGreater(q["total_ask_vol"], q["ask1_vol"])

    def test_step2_intraday_voi_and_snapshot_recovery(self):
        from intraday_timing_engine import (
            intraday_timing_engine,
            save_intraday_state_snapshot,
            restore_intraday_state_snapshot,
        )
        dip_buyer = intraday_timing_engine["dip_buyer"]
        ring_buf = intraday_timing_engine["ring_buffer"]

        dip_buyer.watchlist.pop("600036", None)
        dip_buyer.register_unbought_candidate(
            symbol="600036",
            name="招商银行",
            score=82.0,
            target_price=35.0,
            initial_price=35.0,
        )
        # First quote establishes high_price=36.0 and pullback price=35.28
        q1 = {
            "price": 35.28,
            "bid1_price": 35.28,
            "bid1_vol": 10000,
            "ask1_price": 35.29,
            "ask1_vol": 12000,
            "bid_vols": [10000, 8000, 6000, 5000, 4000],
            "ask_vols": [12000, 8000, 6000, 5000, 4000],
        }
        dip_buyer.update_quote("600036", 35.28, 0.8, high=36.0, low=35.0, volume=100000, quote=q1)

        # Second quote at the same support level with strong bid volume surge -> positive VOI
        q2 = {
            "price": 35.28,
            "bid1_price": 35.28,
            "bid1_vol": 45000,
            "ask1_price": 35.29,
            "ask1_vol": 4000,
            "bid_vols": [45000, 20000, 15000, 10000, 8000],
            "ask_vols": [4000, 5000, 5000, 5000, 5000],
        }
        res2 = dip_buyer.update_quote("600036", 35.28, 0.8, high=36.0, low=35.0, volume=150000, quote=q2)
        self.assertIn("voi", res2)
        self.assertIn("normalized_voi", res2)
        self.assertGreater(res2["normalized_voi"], 0.0)
        self.assertEqual(res2["signal"], "TIMING_DIP_BUY")

        # Verify ring buffer captured events & atomic snapshot round-trips
        events = ring_buf.get_latest_n(10)
        self.assertTrue(isinstance(events, list))
        self.assertGreaterEqual(len(events), 1)
        self.assertTrue(save_intraday_state_snapshot())
        dip_buyer.watchlist.pop("600036", None)
        self.assertNotIn("600036", dip_buyer.watchlist)
        self.assertTrue(restore_intraday_state_snapshot())
        self.assertIn("600036", dip_buyer.watchlist)

    def test_step3_ai_brain_lowdin_and_portfolio_optimizer(self):
        from ai_brain import ai_brain
        candidates = [
            {
                "symbol": "000001",
                "name": "平安银行",
                "industry": "银行",
                "ensemble_score": 82.5,
                "change_pct": 1.8,
                "obi": 0.35,
                "pullback_pct": 1.5,
                "decision": "BUY",
                "recommended_weight": 25.0,
            },
            {
                "symbol": "600519",
                "name": "贵州茅台",
                "industry": "白酒",
                "ensemble_score": 78.0,
                "change_pct": 1.2,
                "obi": 0.22,
                "pullback_pct": 2.1,
                "decision": "BUY",
                "recommended_weight": 25.0,
            },
            {
                "symbol": "300750",
                "name": "宁德时代",
                "industry": "电池",
                "ensemble_score": 74.0,
                "change_pct": 2.9,
                "obi": 0.18,
                "pullback_pct": 1.8,
                "decision": "BUY",
                "recommended_weight": 25.0,
            },
            {
                "symbol": "601318",
                "name": "中国平安",
                "industry": "保险",
                "ensemble_score": 52.0,
                "change_pct": -0.5,
                "obi": -0.15,
                "pullback_pct": 3.5,
                "decision": "HOLD",
                "recommended_weight": 0.0,
            },
        ]
        out = ai_brain.allocate_candidate_weights(candidates, method="black_litterman", max_weight=0.35)
        self.assertEqual(len(out), 4)
        for c in out:
            self.assertIn("ortho_score", c)
        buy_cands = [c for c in out if c["decision"] == "BUY"]
        for c in buy_cands:
            self.assertIn("hrp_weight", c)
            self.assertIn("bl_weight", c)
            self.assertGreater(c["hrp_weight"], 0.0)
            self.assertGreater(c["bl_weight"], 0.0)
            self.assertLessEqual(c["recommended_weight"], 35.0)

    def test_step4_account_manager_audit_chain_and_sector_guard(self):
        from account_manager import account_manager
        from telemetry_audit import global_audit_chain

        res = account_manager.place_order(
            account_id=1,
            symbol="000001",
            action="buy",
            price=11.50,
            amount=200,
            name="平安银行",
            dry_run=True,
            persist=True,
        )
        self.assertTrue(res.get("success"))
        self.assertIn("audit_hash", res)
        self.assertEqual(len(res["audit_hash"]), 64)
        valid, count = global_audit_chain.verify_chain_integrity()
        self.assertTrue(valid)
        self.assertGreaterEqual(count, 1)

    def test_step5_scheduler_non_blocking_almgren_chriss_slices(self):
        from ai_scheduler import ai_scheduler

        with ai_scheduler._algo_slice_lock:
            ai_scheduler._pending_algo_slices.clear()

        sched_res = ai_scheduler.schedule_almgren_chriss_order(
            account_id=1,
            symbol="600036",
            action="buy",
            price=35.0,
            total_shares=1200,
            name="招商银行",
            n_slices=3,
            interval_sec=0.01,
            dry_run=True,
        )
        self.assertTrue(sched_res.get("scheduled"))
        self.assertGreaterEqual(sched_res.get("queued_slices", 0), 1)
        time.sleep(0.05)
        executed = ai_scheduler._tick_algo_order_slices()
        self.assertGreaterEqual(len(executed), 1)
        for item in executed:
            self.assertTrue(item["result"].get("success"))


if __name__ == "__main__":
    unittest.main()
