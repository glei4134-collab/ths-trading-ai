"""
Unit tests for backtester transaction costs (stamp duty split, min commission) and metrics (Sortino, Calmar)
"""
import unittest
from src.backtester import Backtester, BacktestResult, BacktestTrade


class TestBacktestCostsAndMetrics(unittest.TestCase):

    def setUp(self):
        self.bt = Backtester()

    def test_stamp_duty_historical_split(self):
        amount = 100000.0  # 10万元成交额
        
        # 2023-08-28 之前: 卖出印花税 0.1% = 100元
        fee_pre, comm_pre, tax_pre = self.bt.calc_transaction_cost("2023-08-25", amount, action="sell")
        self.assertAlmostEqual(tax_pre, 100.0, places=2)
        
        # 2023-08-28 之后: 卖出印花税 0.05% = 50元
        fee_post, comm_post, tax_post = self.bt.calc_transaction_cost("2023-08-29", amount, action="sell")
        self.assertAlmostEqual(tax_post, 50.0, places=2)
        
        # 买入方无论何时免征印花税
        fee_buy, comm_buy, tax_buy = self.bt.calc_transaction_cost("2023-08-25", amount, action="buy")
        self.assertEqual(tax_buy, 0.0)

    def test_min_commission_floor(self):
        # 极小单: 成交 1000元，按 0.0003 算只有 0.3元，必须触发 5元保底
        small_amount = 1000.0
        fee, comm, tax = self.bt.calc_transaction_cost("2024-01-01", small_amount, action="buy")
        self.assertEqual(comm, 5.0)

    def test_calc_curve_metrics_sortino_and_calmar(self):
        # 构造一条稳健向上的净值曲线
        curve = [
            {"date": f"2024-01-{i:02d}", "equity": 100000.0 + i * 500.0}
            for i in range(1, 21)
        ]
        # 在第10天模拟小回撤
        curve[9]["equity"] = curve[8]["equity"] - 300.0
        
        trades = [
            {"action": "sell", "profit": 500.0},
            {"action": "sell", "profit": -200.0},
            {"action": "sell", "profit": 800.0}
        ]
        
        metrics = self.bt._calc_curve_metrics(curve, trades, initial_capital=100000.0, days=20)
        self.assertIn("sortino_ratio", metrics)
        self.assertIn("calmar_ratio", metrics)
        self.assertGreater(metrics["sortino_ratio"], 0.0)
        self.assertGreater(metrics["calmar_ratio"], 0.0)
        self.assertGreater(metrics["total_return"], 0.0)


if __name__ == "__main__":
    unittest.main()
