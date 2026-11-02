"""
Unit tests for Alpha Factor Orthogonalization and Rank IC decay (unittest compatible)
"""
import unittest
import numpy as np
from src.factors.orthogonalizer import (
    LowodinOrthogonalizer,
    GramSchmidtOrthogonalizer,
    calculate_rank_ic,
    factor_ic_decay
)


class TestFactorOrthogonalization(unittest.TestCase):

    def test_lowodin_orthogonality(self):
        # 构造具有高度共线性的合成因子矩阵 (1000 样本 x 4 因子)
        np.random.seed(42)
        n = 1000
        base_factor = np.random.normal(0, 1, n)
        
        # f1, f2, f3, f4 具有强相关性 (多重共线性)
        f1 = base_factor + np.random.normal(0, 0.2, n)
        f2 = base_factor * 0.8 + np.random.normal(0, 0.3, n)
        f3 = base_factor * -0.5 + np.random.normal(0, 0.4, n)
        f4 = np.random.normal(0, 1, n)
        
        X = np.column_stack([f1, f2, f3, f4])
        
        # 验证原始相关性
        orig_corr = np.corrcoef(X.T)
        self.assertGreater(abs(orig_corr[0, 1]), 0.8)
        
        # 进行 Lowödin 对称正交化
        ortho = LowodinOrthogonalizer()
        X_ortho, T = ortho.fit_transform(X, ["f1", "f2", "f3", "f4"])
        
        # 验证正交化后矩阵特征
        ortho_corr = np.corrcoef(X_ortho.T)
        
        # 对角线应全为 1.0
        np.testing.assert_allclose(np.diag(ortho_corr), np.ones(4), atol=1e-5)
        
        # 非对角线两两相关系数应严格接近 0.0 (< 1e-4)
        off_diag = ortho_corr - np.eye(4)
        self.assertLess(np.max(np.abs(off_diag)), 1e-4)

    def test_lowodin_dict_transform(self):
        records = [
            {"momentum": 1.2, "volatility": 0.8, "rsi": 65.0},
            {"momentum": 0.5, "volatility": 1.2, "rsi": 45.0},
            {"momentum": -0.8, "volatility": 1.5, "rsi": 30.0},
            {"momentum": 2.1, "volatility": 0.4, "rsi": 80.0},
            {"momentum": -1.5, "volatility": 2.0, "rsi": 20.0},
        ]
        ortho = LowodinOrthogonalizer()
        result = ortho.transform_dict(records, ["momentum", "volatility", "rsi"])
        
        self.assertEqual(len(result), len(records))
        for row in result:
            self.assertIn("momentum_ortho", row)
            self.assertIn("volatility_ortho", row)
            self.assertIn("rsi_ortho", row)

    def test_rank_ic_calculation(self):
        # 完全同序 -> Rank IC 应为 1.0
        f = [10.0, 20.0, 30.0, 40.0, 50.0]
        r = [0.01, 0.02, 0.03, 0.05, 0.08]
        ic = calculate_rank_ic(f, r)
        self.assertAlmostEqual(ic, 1.0, places=4)
        
        # 完全逆序 -> Rank IC 应为 -1.0
        r_rev = [0.08, 0.05, 0.03, 0.02, 0.01]
        ic_neg = calculate_rank_ic(f, r_rev)
        self.assertAlmostEqual(ic_neg, -1.0, places=4)

    def test_factor_ic_decay(self):
        # 模拟 30 天的多股票因子值和未来收益率
        np.random.seed(42)
        days = 30
        stocks = [f"stock_{i}" for i in range(20)]
        
        factor_hist = []
        returns_hist = []
        
        true_alpha = {s: np.random.normal(0, 1) for s in stocks}
        for d in range(days):
            f_d = {s: true_alpha[s] + np.random.normal(0, 0.5) for s in stocks}
            r_d = {s: true_alpha[s] * 0.02 + np.random.normal(0, 0.02) for s in stocks}
            factor_hist.append(f_d)
            returns_hist.append(r_d)
            
        decay_res = factor_ic_decay(factor_hist, returns_hist, max_lags=5)
        self.assertIn("mean_ic", decay_res)
        self.assertIn("ir", decay_res)
        self.assertIn("half_life_days", decay_res)
        self.assertGreater(decay_res["mean_ic"], 0)


if __name__ == "__main__":
    unittest.main()
