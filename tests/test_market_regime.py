"""
Unit tests for Market Regime 4-Quadrant detector and GARCH(1,1) model
"""
import unittest
import numpy as np
from src.market_regime import MarketRegimeDetector


class TestMarketRegimeDetector(unittest.TestCase):

    def setUp(self):
        self.detector = MarketRegimeDetector(cache_ttl=0)

    def test_garch11_volatility_estimation(self):
        # 构造高波动序列 (年化 ~ 40%)
        np.random.seed(42)
        high_vol_ret = np.random.normal(0, 0.025, 60)
        daily_vol, annual_vol = self.detector._estimate_garch11_vol(high_vol_ret)
        self.assertGreater(annual_vol, 0.25)
        self.assertLess(annual_vol, 0.60)

        # 构造低波动序列 (年化 ~ 10%)
        low_vol_ret = np.random.normal(0, 0.006, 60)
        daily_vol_low, annual_vol_low = self.detector._estimate_garch11_vol(low_vol_ret)
        self.assertLess(annual_vol_low, 0.18)
        self.assertLess(annual_vol_low, annual_vol)

    def test_adjustments_mapping(self):
        # 测试四象限风控调整
        bull_adj = self.detector.get_strategy_adjustments(MarketRegimeDetector.REGIME_BULL_LOW_VOL)
        self.assertGreaterEqual(bull_adj["position_factor"], 1.2)
        self.assertIn("trend", bull_adj["prefer_categories"])

        bear_adj = self.detector.get_strategy_adjustments(MarketRegimeDetector.REGIME_BEAR_HIGH_VOL)
        self.assertLessEqual(bear_adj["position_factor"], 0.4)
        self.assertIn("reversal", bear_adj["prefer_categories"])

    def test_fallback_basket_regime(self):
        basket = {"avg_change_pct": 2.5, "sample_size": 40}
        res = self.detector._fallback_basket_regime(basket)
        self.assertEqual(res["quadrant_regime"], MarketRegimeDetector.REGIME_BULL_LOW_VOL)
        self.assertEqual(res["regime"], "bull")
        self.assertIn("bull_low_vol", res["regime_probs"])


if __name__ == "__main__":
    unittest.main()
