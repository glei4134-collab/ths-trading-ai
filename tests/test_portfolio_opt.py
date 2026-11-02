"""
Unit tests for Stage 2 Portfolio Optimization (HRP, Black-Litterman, CVaR, and Bayesian Ensemble Weights)
"""
import unittest
import numpy as np
from src.portfolio_opt import (
    HierarchicalRiskParity,
    BlackLittermanModel,
    PortfolioOptimizer,
    calc_var,
    calc_cvar,
    portfolio_cvar
)
from src.strategies.team_ensemble import TeamEnsembleStrategy


class TestPortfolioOptimization(unittest.TestCase):

    def test_hrp_handles_collinear_and_high_vol_assets(self):
        np.random.seed(42)
        T = 120
        # 资产0和资产1高度共线，资产2独立且低波动，资产3高波动
        f_common = np.random.normal(0, 0.02, T)
        r0 = f_common + np.random.normal(0, 0.002, T)
        r1 = f_common + np.random.normal(0, 0.002, T)
        r2 = np.random.normal(0, 0.008, T)
        r3 = np.random.normal(0, 0.040, T)

        R = np.column_stack([r0, r1, r2, r3])
        names = ["600000", "600001", "000001", "300001"]

        weights = PortfolioOptimizer.optimize_hrp(R, names)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=3)
        # 低波动独立资产 000001 的权重应显著高于高波动资产 300001
        self.assertGreater(weights["000001"], weights["300001"])

        report = PortfolioOptimizer.calc_portfolio_risk_report(weights, R, names)
        self.assertGreater(report["annualized_volatility"], 0.0)
        self.assertGreaterEqual(report["cvar_95"], report["var_95"])

    def test_black_litterman_posterior_view_tilt(self):
        np.random.seed(42)
        R = np.random.normal(0.0005, 0.02, (120, 3))
        symbols = ["A", "B", "C"]
        # 专家观点：强烈看多 A (+15%)，看空 B (-5%)
        views = {"A": 0.15, "B": -0.05}
        confs = {"A": 0.85, "B": 0.80}

        w = PortfolioOptimizer.optimize_black_litterman(R, symbols, views=views, confidences=confs)
        self.assertAlmostEqual(sum(w.values()), 1.0, places=3)
        self.assertGreater(w["A"], w["B"])

    def test_cvar_exceeds_var(self):
        np.random.seed(42)
        returns = np.random.standard_t(df=4, size=500) * 0.015
        v95 = calc_var(returns, confidence_level=0.95)
        cv95 = calc_cvar(returns, confidence_level=0.95)
        self.assertGreater(v95, 0.0)
        self.assertGreaterEqual(cv95, v95)

    def test_team_ensemble_bayesian_quadrant_weights(self):
        ens = TeamEnsembleStrategy()
        mkt_bear_high = {
            "quadrant_regime": "bear_high_vol",
            "regime_probs": {
                "bull_low_vol": 0.05,
                "bull_high_vol": 0.05,
                "bear_low_vol": 0.05,
                "bear_high_vol": 0.85
            },
            "strategy_perf": {
                "volume_shrink_reversal": {"win_rate": 0.70}
            }
        }
        w_bear = ens._get_regime_weights(mkt_bear_high, is_auction=False)
        self.assertAlmostEqual(sum(w_bear.values()), 1.0, places=3)
        self.assertGreater(w_bear["volume_shrink_reversal"], w_bear["competition_breakout"])


if __name__ == "__main__":
    unittest.main()
