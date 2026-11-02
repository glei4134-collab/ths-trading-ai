"""
Unit tests for Stage 2 Extreme Risk Control in OrderGuard:
- Fractional Half-Kelly + ATR_14 dynamic position sizing
- Shenwan L1 40% sector hard cap & 35% warning
- Pairwise correlation pruning (r > 0.80)
- Clayton Copula lower-tail dependence contagion alert
"""
import unittest
import numpy as np
from src.order_guard import (
    validate_pre_trade,
    calculate_dynamic_position_size,
    prune_correlated_candidates,
    estimate_clayton_lower_tail_dependence
)


class TestOrderGuardStage2(unittest.TestCase):

    def test_dynamic_position_sizing_half_kelly_and_atr(self):
        # 20万总资金，股价 20元，ATR_14 = 0.8元，胜率 60%，盈亏比 2.0
        res = calculate_dynamic_position_size(
            total_equity=200000.0,
            price=20.0,
            atr_14=0.80,
            win_rate=0.60,
            payoff_ratio=2.0,
            risk_per_trade_pct=0.015
        )
        # 2*ATR = 1.6元，1.5%风险预算 = 3000元 -> ATR股数 = floor(3000 / 1.6 / 100)*100 = 1800股
        self.assertEqual(res["atr_shares"], 1800)
        self.assertEqual(res["recommended_shares"] % 100, 0)
        self.assertLessEqual(res["position_pct"], 0.35)

        # 负期望策略自动回退至 5% 探索底仓
        res_neg = calculate_dynamic_position_size(
            total_equity=200000.0,
            price=20.0,
            atr_14=0.20,
            win_rate=0.30,
            payoff_ratio=1.0
        )
        self.assertAlmostEqual(res_neg["half_kelly_pct"], 0.05, places=3)

    def test_sector_cap_40_pct_rejection_and_downsize(self):
        # 账户总资产 200,000，当前半导体行业已持仓 79,000 (39.5%)
        # 再买 100 股 @ 25元 = 2500元 -> 行业总计 81,500 (> 80,000 即 40%)，不足1手空间，应直接拒绝 SECTOR_CAP
        res_reject = validate_pre_trade(
            symbol="688001",
            action="buy",
            price=25.0,
            amount=100,
            total_assets=200000.0,
            available_cash=100000.0,
            existing_position_value=0.0,
            sector="半导体",
            existing_sector_value=79000.0,
            skip_session_check=True
        )
        self.assertFalse(res_reject.allowed)
        self.assertEqual(res_reject.code, "SECTOR_CAP")

        # 若当前行业持仓 65,000 (32.5%)，拟买入 1000股 @ 10元 = 10,000元 -> 75,000 (37.5%)
        # 处于 [35%, 40%] 之间，应允许通过并附带 SECTOR_WARN 预警
        res_warn = validate_pre_trade(
            symbol="600584",
            action="buy",
            price=10.0,
            amount=1000,
            total_assets=200000.0,
            available_cash=100000.0,
            existing_position_value=0.0,
            sector="半导体",
            existing_sector_value=65000.0,
            skip_session_check=True
        )
        self.assertTrue(res_warn.allowed)
        self.assertEqual(res_warn.amount, 1000)
        self.assertIn("SECTOR_WARN", res_warn.message)

    def test_pairwise_correlation_pruning(self):
        np.random.seed(42)
        base_ret = np.random.normal(0, 0.02, 30)
        returns_map = {
            "000001": base_ret,
            "000002": base_ret * 0.98 + np.random.normal(0, 0.001, 30),  # 与 000001 高度共线 r > 0.95
            "600519": np.random.normal(0, 0.02, 30),                     # 独立走势
        }
        candidates = [
            {"symbol": "000001", "score": 88.0},
            {"symbol": "000002", "score": 82.0},
            {"symbol": "600519", "score": 79.0},
        ]
        retained, pruned = prune_correlated_candidates(candidates, returns_map, max_corr=0.80)
        retained_syms = [c["symbol"] for c in retained]
        self.assertIn("000001", retained_syms)
        self.assertIn("600519", retained_syms)
        self.assertNotIn("000002", retained_syms)
        self.assertEqual(len(pruned), 1)
        self.assertEqual(pruned[0]["pruned_symbol"], "000002")

    def test_clayton_copula_lower_tail_dependence(self):
        np.random.seed(42)
        common_crash = np.random.normal(0, 0.03, 60)
        # 构造三只同涨同跌高度共振的股票
        m_high = np.column_stack([
            common_crash + np.random.normal(0, 0.002, 60),
            common_crash + np.random.normal(0, 0.002, 60),
            common_crash + np.random.normal(0, 0.002, 60),
        ])
        res = estimate_clayton_lower_tail_dependence(m_high)
        self.assertGreater(res["lambda_lower_tail"], 0.65)
        self.assertTrue(res["tail_contagion_alert"])
        self.assertLess(res["leverage_scale_factor"], 1.0)


if __name__ == "__main__":
    unittest.main()
