"""
tests/test_stock_analyzer.py
股票多维正交分析模块 (stock_analyzer) 全套单元测试
覆盖:
1. TechnicalAnalyzer (技术面均线/MACD/RSI/布林带)
2. MicrostructureAnalyzer (五档订单簿/OBI/VOI/价差)
3. StrategyEnsembleAnalyzer (5大策略集成/Lowdin正交化/HRP与BL权重)
4. MacroSectorAnalyzer (4象限宏观状态机/申万一级行业/舆情)
5. CompositeStockAnalyzer (综合分析门面/雷达图/硬止损/目标价)
6. 历史调用方兼容性 (TradingEngine.analyze_stock)
"""

import unittest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch

from stock_analyzer.base import StockAnalysisResult
from stock_analyzer.technical_analyzer import TechnicalAnalyzer
from stock_analyzer.microstructure_analyzer import MicrostructureAnalyzer
from stock_analyzer.strategy_ensemble_analyzer import StrategyEnsembleAnalyzer
from stock_analyzer.macro_sector_analyzer import MacroSectorAnalyzer
from stock_analyzer.composite_analyzer import CompositeStockAnalyzer
from strategy import TradingEngine


class TestStockAnalyzer(unittest.TestCase):

    def setUp(self):
        # 构造基准行情模拟数据
        self.symbol = "000001"
        self.quote = {
            "symbol": self.symbol,
            "name": "平安银行",
            "price": 12.00,
            "change_pct": 1.5,
            "high": 12.10,
            "low": 11.85,
            "volume": 50000000,
            "amount": 600000000
        }
        
        # 构造 100 天 K 线合成序列
        dates = pd.date_range(end="2026-11-03", periods=100)
        np.random.seed(42)
        base = 10.0
        returns = np.random.normal(0.001, 0.02, 100)
        prices = base * np.cumprod(1 + returns)
        
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "open": prices * 0.99,
            "high": prices * 1.02,
            "low": prices * 0.98,
            "close": prices,
            "volume": np.random.randint(10000, 50000, 100) * 100,
            "amount": prices * np.random.randint(10000, 50000, 100) * 100
        })
        self.kline_list = df.to_dict(orient="records")

    def test_01_technical_analyzer(self):
        """测试维度 1: 技术面均线多头、MACD、RSI 与布林带"""
        analyzer = TechnicalAnalyzer()
        res = analyzer.analyze(self.symbol, self.quote, kline=self.kline_list)

        self.assertIn("score", res)
        self.assertIn("ma5", res)
        self.assertIn("ma20", res)
        self.assertIn("macd_hist", res)
        self.assertIn("rsi", res)
        self.assertIn("bb_upper", res)
        self.assertIn("bb_lower", res)
        self.assertIn("is_bull_alignment", res)
        self.assertIn("signals", res)

        # 评分应在 0 到 100 之间
        self.assertTrue(0.0 <= res["score"] <= 100.0)
        # RSI 应在 0 到 100 之间
        self.assertTrue(0.0 <= res["rsi"] <= 100.0)
        # 布林带上轨应大于下轨
        self.assertGreater(res["bb_upper"], res["bb_lower"])

    def test_02_microstructure_analyzer_dict_and_object(self):
        """测试维度 2: 微观盘口 OBI、VOI 与价差（字典与对象两种形态）"""
        analyzer = MicrostructureAnalyzer()

        # 1. 字典格式五档测试
        mock_depth_dict = {
            "bid_volumes": [1000, 800, 600, 400, 200],
            "ask_volumes": [200, 300, 400, 500, 600],
            "bid_prices": [12.00, 11.99, 11.98, 11.97, 11.96],
            "ask_prices": [12.01, 12.02, 12.03, 12.04, 12.05],
            "voi": 50.0,
            "normalized_voi": 0.015
        }

        with patch("execution_optimizer.fetch_orderbook_depth", return_value=mock_depth_dict):
            res_dict = analyzer.analyze(self.symbol, self.quote)
            self.assertGreater(res_dict["obi"], 0.0)
            self.assertEqual(res_dict["spread"], 0.01)
            self.assertGreater(res_dict["score"], 50.0)
            self.assertTrue(any("买盘" in s for s in res_dict["signals"]))

        # 2. 空盘口容错测试
        with patch("execution_optimizer.fetch_orderbook_depth", return_value=None):
            res_none = analyzer.analyze(self.symbol, self.quote)
            self.assertEqual(res_none["score"], 50.0)
            self.assertEqual(res_none["obi"], 0.0)

    def test_03_strategy_ensemble_analyzer(self):
        """测试维度 3: 多策略集成表决与 Lowdin 正交化打分"""
        analyzer = StrategyEnsembleAnalyzer()
        indicators = {
            "close": 12.00,
            "ma5": 11.80,
            "ma10": 11.50,
            "ma20": 11.20,
            "rsi": 62.0,
            "macd": 0.15
        }
        res = analyzer.analyze(self.symbol, self.quote, indicators, regime="bull")

        self.assertIn("score", res)
        self.assertIn("signal", res)
        self.assertIn("positive_votes", res)
        self.assertIn("total_strategies", res)
        self.assertIn("ortho_score", res)
        self.assertIn("hrp_weight", res)
        self.assertIn("bl_weight", res)

        # 验证评分与策略总数
        self.assertEqual(res["total_strategies"], 5)
        self.assertTrue(0.0 <= res["score"] <= 100.0)
        self.assertIn(res["signal"], ["STRONG_BUY", "BUY", "WEAK_BUY", "HOLD", "WEAK_SELL", "SELL", "STRONG_SELL"])

    def test_04_macro_sector_analyzer(self):
        """测试维度 4: 宏观状态机与板块轮动"""
        analyzer = MacroSectorAnalyzer()
        res = analyzer.analyze(self.symbol, "平安银行")

        self.assertIn("score", res)
        self.assertIn("regime", res)
        self.assertIn("regime_desc", res)
        self.assertIn("signals", res)
        self.assertTrue(0.0 <= res["score"] <= 100.0)

    def test_05_composite_stock_analyzer(self):
        """测试综合分析门面 (CompositeStockAnalyzer) 全流程集成"""
        composite = CompositeStockAnalyzer()
        
        result = composite.analyze(self.symbol, quote=self.quote, kline=self.kline_list)

        self.assertIsInstance(result, StockAnalysisResult)
        d = result.to_dict()

        # 验证顶层字段
        self.assertEqual(d["symbol"], self.symbol)
        self.assertEqual(d["name"], "平安银行")
        self.assertEqual(d["price"], 12.0)
        self.assertIn(d["signal"], ["STRONG_BUY", "BUY", "WEAK_BUY", "HOLD", "WEAK_SELL", "SELL", "STRONG_SELL"])
        self.assertTrue(0.0 <= d["score"] <= 100.0)

        # 验证风控边界：止损幅度严格在 -3.8% ~ -4.5% 之间
        curr_p = d["price"]
        stop_loss = d["stop_loss"]
        self.assertIsNotNone(stop_loss)
        self.assertLess(stop_loss, curr_p)
        stop_loss_pct = (curr_p - stop_loss) / curr_p
        self.assertTrue(0.035 <= stop_loss_pct <= 0.046, f"Stop loss pct {stop_loss_pct} out of team bounds")

        # 验证 4 大维度与雷达图
        self.assertIn("technical", d)
        self.assertIn("microstructure", d)
        self.assertIn("ensemble", d)
        self.assertIn("macro_sector", d)
        self.assertIn("radar_scores", d)
        self.assertEqual(len(d["radar_scores"]), 5)

        # 验证旧前端兼容字段 indicators
        self.assertIn("indicators", d)
        self.assertEqual(d["indicators"]["price"], 12.0)
        self.assertIn("ma5", d["indicators"])
        self.assertIn("ma20", d["indicators"])
        self.assertIn("rsi", d["indicators"])

    def test_06_strategy_engine_backward_compatibility(self):
        """测试 TradingEngine.analyze_stock 向后兼容调用"""
        engine = TradingEngine()
        res = engine.analyze_stock(self.symbol)

        self.assertIsInstance(res, dict)
        self.assertEqual(res["symbol"], self.symbol)
        self.assertIn("signal", res)
        self.assertIn("score", res)
        self.assertIn("signals", res)
        self.assertIn("timestamp", res)


if __name__ == "__main__":
    unittest.main()
