"""
Unit tests for Stage 2 Algorithmic Execution & Microstructure Latency Guard:
- Almgren-Chriss optimal execution trajectory
- Dynamic Residual VWAP (intraday U-shape profile)
- Kyle's Lambda square-root market impact slippage
- Pegged Iceberg order planner
- Volume Order Imbalance (VOI), Level-2 Queue Position & Spoofing detection
"""
import unittest
from src.execution.algo_orders import (
    AlmgrenChrissExecutor,
    DynamicResidualVWAP,
    PeggedIcebergPlanner,
    estimate_kyle_lambda_slippage
)
from src.intraday_timing_engine import (
    calculate_volume_order_imbalance,
    estimate_l2_queue_position,
    detect_spoofing_and_phantom_liquidity
)


class TestAlgoExecutionAndMicrostructure(unittest.TestCase):

    def test_almgren_chriss_trajectory_conservation_and_frontloading(self):
        # 高风险厌恶交易员 vs 风险中性交易员
        ac_urgent = AlmgrenChrissExecutor(risk_aversion=1e-3)
        ac_neutral = AlmgrenChrissExecutor(risk_aversion=1e-9)

        plan_urgent = ac_urgent.plan_trajectory(total_shares=10000, price=25.0, horizon_minutes=30.0, n_slices=5)
        plan_neutral = ac_neutral.plan_trajectory(total_shares=10000, price=25.0, horizon_minutes=30.0, n_slices=5)

        # 验证分步交易量总和严格守恒 = 10000 股
        sum_urgent = sum(s["trade_shares"] for s in plan_urgent["slices"])
        sum_neutral = sum(s["trade_shares"] for s in plan_neutral["slices"])
        self.assertEqual(sum_urgent, 10000)
        self.assertEqual(sum_neutral, 10000)

        # 高风险厌恶交易员在第一步的出清股数应显著大于风险中性交易员
        self.assertGreater(plan_urgent["slices"][0]["trade_shares"], plan_neutral["slices"][0]["trade_shares"])

    def test_dynamic_residual_vwap_u_shape(self):
        vwap_planner = DynamicResidualVWAP()
        res = vwap_planner.generate_vwap_schedule(total_shares=10000, start_bucket_idx=0, end_bucket_idx=7, observed_volume_ratio=1.0)
        sched = res["schedule"]

        # 总股数守恒
        self.assertEqual(sum(item["order_shares"] for item in sched), 10000)
        # 早盘第一时段 (09:30-10:00) 与尾盘 (14:30-15:00) 分配股数应大于午间低谷 (13:30-14:00)
        self.assertGreater(sched[0]["order_shares"], sched[5]["order_shares"])
        self.assertGreater(sched[7]["order_shares"], sched[5]["order_shares"])

    def test_kyle_lambda_square_root_slippage(self):
        # 高流动性大盘股 vs 极低流动性微盘股
        liquid_res = estimate_kyle_lambda_slippage(order_shares=2000, price=20.0, daily_volume_shares=50_000_000)
        illiquid_res = estimate_kyle_lambda_slippage(order_shares=50000, price=20.0, daily_volume_shares=400_000)

        self.assertLess(liquid_res["slippage_bps"], 10.0)
        self.assertFalse(liquid_res["requires_algo_split"])

        self.assertGreater(illiquid_res["slippage_bps"], 50.0)
        self.assertTrue(illiquid_res["requires_algo_split"])

    def test_pegged_iceberg_planner(self):
        plan = PeggedIcebergPlanner.plan_iceberg(
            total_shares=5000,
            action="buy",
            best_bid=10.00,
            best_ask=10.03,
            best_bid_vol=4000,
            best_ask_vol=4000
        )
        self.assertEqual(plan["visible_tip_shares"] + plan["hidden_reserve_shares"], 5000)
        self.assertEqual(plan["execution_style"], "PASSIVE_MAKER_ICEBERG")
        self.assertEqual(plan["pegged_limit_price"], 10.01)

    def test_voi_and_spoofing_detection(self):
        # 1. VOI 测试：买一价上移且买一放量 -> VOI 显著为正
        voi_res = calculate_volume_order_imbalance(
            bid_p=10.05, bid_v=8000, prev_bid_p=10.04, prev_bid_v=3000,
            ask_p=10.06, ask_v=2000, prev_ask_p=10.06, prev_ask_v=5000
        )
        self.assertGreater(voi_res["voi"], 0)
        self.assertGreater(voi_res["normalized_voi"], 0)

        # 2. Spoofing 测试：上一帧买一挂单 50,000 股，当前骤降至 5,000 股，但期间仅成交 1,000 股 -> 97.7% 为主动撤单
        spoof_res = detect_spoofing_and_phantom_liquidity(
            bid1_vol=5000, ask1_vol=12000,
            prev_bid1_vol=50000, prev_ask1_vol=12000,
            traded_delta_vol=1000
        )
        self.assertTrue(spoof_res["is_spoofing"])
        self.assertTrue(spoof_res["bull_trap_spoofing"])
        self.assertEqual(spoof_res["spoofing_type"], "BULL_TRAP_SPOOFING")

        # 3. Level-2 排单队列估计
        q_res = estimate_l2_queue_position(
            order_shares=1000,
            ahead_queue_volume=20000,
            trade_flow_shares_per_sec=100.0,
            cancel_rate=0.25,
            horizon_sec=60.0
        )
        self.assertGreater(q_res["expected_wait_sec"], 100.0)
        self.assertLess(q_res["fill_probability_60s"], 0.50)


if __name__ == "__main__":
    unittest.main()
