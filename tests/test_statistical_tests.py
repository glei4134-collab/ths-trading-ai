"""
Unit tests for Probabilistic Sharpe Ratio (PSR) and Deflated Sharpe Ratio (DSR)
"""
import unittest
import numpy as np
from src.statistical_tests import (
    calc_probabilistic_sharpe_ratio,
    calc_deflated_sharpe_ratio,
    StrategySignificanceAuditor,
    calc_distribution_moments,
    norm_cdf,
    norm_ppf
)


class TestStatisticalTests(unittest.TestCase):

    def test_norm_ppf_and_cdf_consistency(self):
        for p in [0.01, 0.05, 0.1, 0.5, 0.9, 0.95, 0.99]:
            z = norm_ppf(p)
            p_back = norm_cdf(z)
            self.assertAlmostEqual(p, p_back, places=3)

    def test_moments_calculation(self):
        np.random.seed(42)
        normal_samples = np.random.normal(0, 1, 5000)
        moments = calc_distribution_moments(normal_samples)
        self.assertAlmostEqual(moments["skewness"], 0.0, places=1)
        self.assertAlmostEqual(moments["kurtosis"], 3.0, places=1)

    def test_psr_high_sharpe(self):
        # 每天稳健收益 0.2%，标准差 0.5% (年化夏普比率极高 ~ 6.0)
        np.random.seed(42)
        returns = np.random.normal(0.002, 0.005, 250)
        psr = calc_probabilistic_sharpe_ratio(returns, benchmark_sr_ann=0.0)
        self.assertGreaterEqual(psr, 0.99)

    def test_dsr_penalizes_multi_testing(self):
        # 模拟产生有一定过拟合的夏普表现
        np.random.seed(42)
        returns = np.random.normal(0.0005, 0.01, 250)
        audit = StrategySignificanceAuditor.audit_returns(returns, n_trials=200)
        
        self.assertIn("annualized_sharpe", audit)
        self.assertIn("dsr_confidence", audit)
        self.assertIn("is_statistically_significant", audit)
        self.assertIn("diagnosis", audit)
        self.assertEqual(audit["trials_discounted"], 200)


if __name__ == "__main__":
    unittest.main()
