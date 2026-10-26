"""
核心模块单元测试
测试范围: fast_data符号转换、data_cache缓存、db_store数据库、ai_brain决策、market_regime、backtester

运行: cd ths-trading-ai && python -m pytest tests/test_core.py -v
"""

import sys
import os
import time
import sqlite3
import tempfile
import random
from pathlib import Path
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch, MagicMock

# 确保 src 在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# ==================== fast_data 符号转换测试 ====================

class TestSymbolConversion(unittest.TestCase):
    """测试 fast_data.py 中的符号转换函数"""

    def test_symbol_to_tencent_sh_stock(self):
        from fast_data import symbol_to_tencent
        assert symbol_to_tencent("600519") == "sh600519"
        assert symbol_to_tencent("601318") == "sh601318"
        assert symbol_to_tencent("688981") == "sh688981"

    def test_symbol_to_tencent_sz_stock(self):
        from fast_data import symbol_to_tencent
        assert symbol_to_tencent("000001") == "sz000001"
        assert symbol_to_tencent("000858") == "sz000858"
        assert symbol_to_tencent("300750") == "sz300750"

    def test_symbol_to_tencent_index(self):
        from fast_data import symbol_to_tencent
        # 指数代码（如000300）需要 is_index=True 才能正确转换
        assert symbol_to_tencent("000300", is_index=True) == "sh000300"
        assert symbol_to_tencent("000001", is_index=True) == "sh000001"

    def test_symbol_to_eastmoney_sh_stock(self):
        from fast_data import symbol_to_eastmoney
        assert symbol_to_eastmoney("600519") == "1.600519"
        assert symbol_to_eastmoney("601318") == "1.601318"

    def test_symbol_to_eastmoney_sz_stock(self):
        from fast_data import symbol_to_eastmoney
        assert symbol_to_eastmoney("000001") == "0.000001"
        assert symbol_to_eastmoney("300750") == "0.300750"

    def test_symbol_to_eastmoney_index(self):
        from fast_data import symbol_to_eastmoney
        # 指数代码需要用 is_index=True 获取正确的 secid
        assert symbol_to_eastmoney("000300", is_index=True) == "1.000300"
        assert symbol_to_eastmoney("000001", is_index=True) == "1.000001"

    def test_symbol_prefix(self):
        from fast_data import symbol_prefix
        assert symbol_prefix("600519") == "sh"
        assert symbol_prefix("000001") == "sz"
        assert symbol_prefix("300750") == "sz"


# ==================== data_cache 缓存测试 ====================

class TestDataCache(unittest.TestCase):
    """测试 DataCache 内存缓存"""

    def test_set_and_get(self):
        from data_cache import DataCache
        cache = DataCache()
        cache.set("test_key", {"data": 123}, ttl=60)
        result = cache.get("test_key")
        assert result == {"data": 123}

    def test_expiry(self):
        from data_cache import DataCache
        cache = DataCache()
        cache.set("expire_key", "value", ttl=1)  # 1秒过期
        assert cache.get("expire_key") == "value"
        time.sleep(1.1)
        assert cache.get("expire_key") is None

    def test_invalidate_pattern(self):
        from data_cache import DataCache
        cache = DataCache()
        cache.set("kline:000001:day:60", [1, 2, 3], ttl=60)
        cache.set("kline:600519:day:60", [4, 5, 6], ttl=60)
        cache.set("realtime:000001", {"price": 10}, ttl=60)
        cache.invalidate("kline:")
        assert cache.get("kline:000001:day:60") is None
        assert cache.get("kline:600519:day:60") is None
        assert cache.get("realtime:000001") == {"price": 10}

    def test_clear(self):
        from data_cache import DataCache
        cache = DataCache()
        cache.set("a", 1, ttl=60)
        cache.set("b", 2, ttl=60)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_status(self):
        from data_cache import DataCache
        cache = DataCache()
        cache.set("active", 1, ttl=60)
        cache.set("expire_soon", 2, ttl=0)
        time.sleep(0.1)
        status = cache.status()
        assert status["total_keys"] == 2
        assert status["active"] == 1
        assert status["expired"] == 1


# ==================== db_store 数据库测试 ====================

class TestDBStore(unittest.TestCase):
    """测试 SQLite 数据库层"""

    def _get_test_db(self):
        """创建使用临时文件的测试数据库"""
        from db_store import DBStore, DB_PATH
        # 保存原始路径
        import db_store
        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        tmp.close()
        # 覆盖 DB_PATH 指向临时文件
        original_path = db_store.DB_PATH
        db_store.DB_PATH = Path(tmp.name)
        db = DBStore()
        return db, tmp.name, original_path

    def _cleanup(self, original_path):
        """恢复原始 DB_PATH"""
        import db_store
        db_store.DB_PATH = original_path

    def test_create_account(self):
        db, path, orig = self._get_test_db()
        try:
            acc = db.create_account("测试账户A", balance=500000)
            assert acc["name"] == "测试账户A"
            assert acc["balance"] == 500000
            assert "id" in acc
        finally:
            db.conn.close()
            os.unlink(path)
            self._cleanup(orig)

    def test_list_accounts(self):
        db, path, orig = self._get_test_db()
        try:
            db.create_account("账户1", balance=100000)
            db.create_account("账户2", balance=200000)
            accounts = db.list_accounts()
            # 可能有JSON迁移带来的预置账户，至少2个
            assert len(accounts) >= 2
            names = [a["name"] for a in accounts]
            assert "账户1" in names
            assert "账户2" in names
        finally:
            db.conn.close()
            os.unlink(path)
            self._cleanup(orig)

    def test_get_account_not_found(self):
        db, path, orig = self._get_test_db()
        try:
            result = db.get_account(999)
            assert result is None
        finally:
            db.conn.close()
            os.unlink(path)
            self._cleanup(orig)

    def test_add_and_get_trades(self):
        db, path, orig = self._get_test_db()
        try:
            acc = db.create_account("交易测试")
            trade = {
                "symbol": "600519",
                "name": "贵州茅台",
                "action": "buy",
                "price": 1800.0,
                "amount": 100,
                "value": 180000,
            }
            db.add_trade(trade, acc["id"])
            trades = db.get_trades(limit=10, account_id=acc["id"])
            assert len(trades) == 1
            assert trades[0]["symbol"] == "600519"
            assert trades[0]["action"] == "buy"
        finally:
            db.conn.close()
            os.unlink(path)
            self._cleanup(orig)

    def test_positions_save_and_load(self):
        db, path, orig = self._get_test_db()
        try:
            acc = db.create_account("持仓测试")
            positions = {
                "600519": {"symbol": "600519", "amount": 200, "total_cost": 350000.0, "avg_price": 1750.0},
                "000001": {"symbol": "000001", "amount": 500, "total_cost": 5750.0, "avg_price": 11.5},
            }
            db.save_positions(positions, acc["id"])
            loaded = db.get_positions(acc["id"])
            assert "600519" in loaded
            assert loaded["600519"]["amount"] == 200
            assert loaded["600519"]["avg_price"] == 1750.0
            assert "000001" in loaded
            assert loaded["000001"]["avg_price"] == 11.5
        finally:
            db.conn.close()
            os.unlink(path)
            self._cleanup(orig)

    def test_active_account_id(self):
        db, path, orig = self._get_test_db()
        try:
            acc1 = db.create_account("主账户")
            acc2 = db.create_account("副账户")
            db.set_active_account_id(acc1["id"])
            assert db.get_active_account_id() == acc1["id"]
            db.set_active_account_id(acc2["id"])
            assert db.get_active_account_id() == acc2["id"]
        finally:
            db.conn.close()
            os.unlink(path)
            self._cleanup(orig)

    def test_delete_account(self):
        db, path, orig = self._get_test_db()
        try:
            acc = db.create_account("待删除")
            assert db.delete_account(acc["id"]) is True
            assert db.get_account(acc["id"]) is None
        finally:
            db.conn.close()
            os.unlink(path)
            self._cleanup(orig)

    def test_get_account_summary(self):
        db, path, orig = self._get_test_db()
        try:
            acc = db.create_account("摘要测试", balance=1000000)
            summary = db.get_account_summary(acc["id"])
            assert summary["balance"] == 1000000
            assert summary["name"] == "摘要测试"
        finally:
            db.conn.close()
            os.unlink(path)
            self._cleanup(orig)


# ==================== ai_brain 决策测试 ====================

class TestAIBrain(unittest.TestCase):
    """测试 AI 决策引擎（mock外部数据）"""

    def _get_brain(self):
        from ai_brain import AIBrain
        brain = AIBrain()
        return brain

    def test_dynamic_thresholds_default(self):
        brain = self._get_brain()
        # 样本不足时使用地板值
        thresholds = brain._calc_dynamic_thresholds()
        assert thresholds["weak"] >= 0.03
        assert thresholds["normal"] >= 0.10
        assert thresholds["strong"] >= 0.25

    def test_dynamic_thresholds_accumulation(self):
        brain = self._get_brain()
        # 填充200个历史分数
        for _ in range(200):
            brain._score_history.append(random.uniform(0.0, 0.5))
        thresholds = brain._calc_dynamic_thresholds()
        # 阈值应该基于百分位数
        assert thresholds["weak"] < thresholds["normal"] < thresholds["strong"]

    def test_get_dynamic_thresholds_api(self):
        brain = self._get_brain()
        result = brain.get_dynamic_thresholds()
        assert "weak" in result
        assert "normal" in result
        assert "strong" in result
        assert "sample_count" in result
        assert result["sample_count"] == 0

    def test_decide_with_mock_data(self):
        """用mock数据测试决策生成"""
        brain = self._get_brain()
        # 直接传入stock_data和indicators，无需mock内部方法
        stock_data = {
            "price": 100.0,
            "change_pct": 2.5,
            "volume": 1000000,
            "turnover": 3.0,
            "pe": 20.0,
            "high": 102.0,
            "low": 98.0,
        }
        indicators = {
            "rsi": 55.0,
            "ma5": 99.0,
            "ma10": 98.0,
            "ma20": 97.0,
            "macd": 0.5,
            "signal": 0.3,
            "histogram": 0.2,
        }
        result = brain.decide("000001", stock_data, indicators)
        # BrainDecision对象
        assert hasattr(result, 'action')
        assert hasattr(result, 'confidence')
        assert hasattr(result, 'reasons')
        assert result.action in ("buy", "sell", "hold")
        assert 0 <= result.confidence <= 1


# ==================== market_regime 测试 ====================

class TestMarketRegime(unittest.TestCase):
    """测试市场环境识别"""

    def test_default_result(self):
        from market_regime import MarketRegimeDetector
        detector = MarketRegimeDetector()
        result = detector._default_result()
        assert result["regime"] == "unknown"
        assert result["confidence"] == 0
        assert "factors" in result

    def test_strategy_adjustments(self):
        from market_regime import MarketRegimeDetector
        detector = MarketRegimeDetector()

        bull = detector.get_strategy_adjustments("bull")
        assert bull["position_factor"] > 1.0
        assert "trend" in bull["prefer_categories"]

        bear = detector.get_strategy_adjustments("bear")
        assert bear["position_factor"] < 1.0
        assert "reversal" in bear["prefer_categories"]

        sideways = detector.get_strategy_adjustments("sideways")
        assert sideways["position_factor"] == 0.8

        volatile = detector.get_strategy_adjustments("volatile")
        assert volatile["position_factor"] == 0.5

    def test_detect_with_mock_kline(self):
        """用mock K线数据测试市场环境识别"""
        from market_regime import MarketRegimeDetector
        detector = MarketRegimeDetector()

        # 生成上涨趋势的mock K线
        mock_kline = []
        base = 4000
        for i in range(90):
            base *= (1 + random.uniform(-0.005, 0.01))  # 偏上涨
            mock_kline.append({
                "date": (datetime.now() - timedelta(days=90-i)).strftime("%Y-%m-%d"),
                "open": round(base * 0.99, 2),
                "close": round(base, 2),
                "high": round(base * 1.01, 2),
                "low": round(base * 0.98, 2),
                "volume": random.randint(1000000, 5000000),
                "change_pct": round(random.uniform(-1, 2), 2),
            })

        with patch('data_cache.get_index_kline', return_value=mock_kline):
            result = detector.detect()
            assert result["regime"] in ("bull", "bear", "sideways", "volatile")
            assert result["confidence"] > 0

    def test_detect_fallback_to_basket(self):
        """当K线不可用时，应降级到市场篮子"""
        from market_regime import MarketRegimeDetector
        detector = MarketRegimeDetector()

        with patch('data_cache.get_index_kline', return_value=[]):
            with patch('data_cache.get_market_basket_quote') as mock_basket:
                mock_basket.return_value = {
                    "avg_change_pct": 1.5,
                    "sample_size": 20,
                    "source": "market_basket"
                }
                result = detector.detect()
                assert result["regime"] == "bull"
                assert any("篮子" in f for f in result["factors"])

    def test_detect_all_data_unavailable(self):
        """K线和篮子都不可用时返回unknown"""
        from market_regime import MarketRegimeDetector
        detector = MarketRegimeDetector()

        with patch('data_cache.get_index_kline', return_value=[]):
            with patch('data_cache.get_market_basket_quote', return_value=None):
                result = detector.detect()
                assert result["regime"] == "unknown"


# ==================== backtester 测试 ====================

class TestBacktester(unittest.TestCase):
    """测试回测引擎"""

    def _get_backtester(self):
        from backtester import Backtester
        return Backtester()

    def test_mock_data_generation(self):
        bt = self._get_backtester()
        data = bt.generate_mock_data("000001", days=60)
        assert len(data) == 60
        assert all("date" in d and "open" in d and "close" in d for d in data)
        assert all(d["high"] >= d["low"] for d in data)

    def test_calculate_indicators(self):
        bt = self._get_backtester()
        data = bt.generate_mock_data("000001", days=60)
        result = bt.calculate_indicators_for_data(data)
        # 60天数据应该有 MA5, MA10, MA20
        assert result[25].get("ma5") is not None
        assert result[25].get("ma10") is not None
        assert result[25].get("ma20") is not None

    def test_backtest_with_mock_data(self):
        """使用mock数据进行完整回测"""
        bt = self._get_backtester()
        with patch.object(bt, 'fetch_real_data') as mock_fetch:
            mock_fetch.return_value = bt.generate_mock_data("600519", days=80)
            result = bt.run_strategy_backtest(
                symbol="600519",
                strategy_names=["multi_factor"],
                initial_capital=1000000,
                days=60,
            )
            assert result.initial_capital == 1000000
            assert result.final_capital > 0
            assert result.start_date != ""
            assert result.end_date != ""
            assert len(result.equity_curve) > 0

    def test_empty_result(self):
        bt = self._get_backtester()
        result = bt._empty_result(1000000, 60)
        assert result.total_return == 0
        assert result.total_trades == 0

    def test_commission_calculation(self):
        """验证手续费计算逻辑"""
        bt = self._get_backtester()
        # 买入10000元，佣金 = max(10000 * 0.0003, 5) = max(3, 5) = 5 元（最低佣金）
        value = 10000
        commission = max(value * bt.commission_rate, bt.min_commission)
        assert commission == bt.min_commission

        # 买入100000元，佣金 = max(100000 * 0.0003, 5) = max(30, 5) = 30 元
        value = 100000
        commission = max(value * bt.commission_rate, bt.min_commission)
        assert commission == round(value * bt.commission_rate, 2) or commission == value * bt.commission_rate

    def test_stamp_tax(self):
        """验证印花税计算"""
        bt = self._get_backtester()
        revenue = 100000
        tax = revenue * bt.stamp_tax_rate
        assert tax == 100  # 0.1%


# ==================== data_store 多账户测试 ====================

class TestDataStore(unittest.TestCase):
    """测试 DataStore 多账户切换"""

    def test_switch_account(self):
        from data_store import DataStore
        ds = DataStore()
        original_id = ds.account_id

        # 列出账户
        accounts = ds._db.list_accounts()
        if len(accounts) >= 2:
            other = [a for a in accounts if a["id"] != original_id][0]
            ds.switch_account(other["id"])
            assert ds.account_id == other["id"]
            # 切回
            ds.switch_account(original_id)
            assert ds.account_id == original_id


# ==================== notification 测试 ====================

class TestNotification(unittest.TestCase):
    """测试通知管理器"""

    def test_add_notification(self):
        from notification import NotificationManager
        nm = NotificationManager()
        nm.add_signal_notification({
            "symbol": "600519",
            "action": "buy",
            "price": 1800,
            "reason": "测试通知"
        })
        notifications = nm.get_all()
        assert len(notifications) > 0
        assert notifications[0]["type"] == "signal"

    def test_daily_summary_content(self):
        from notification import NotificationManager
        nm = NotificationManager()
        summary = nm.add_daily_summary({
            "trade_count": 5,
            "buy_count": 3,
            "sell_count": 2,
            "total_pnl": 1500.50,
            "holdings": [{"symbol": "600519", "profit_pct": 2.5}]
        })
        # add_daily_summary 通过 nm.add() 添加到通知列表
        assert len(nm.get_all()) > 0


# ==================== stock_screener 测试 ====================

class TestStockScreener(unittest.TestCase):
    """测试多因子股票筛选器"""

    def _get_screener(self):
        from stock_screener import StockScreener
        return StockScreener()

    def test_empty_input(self):
        """空输入返回空列表"""
        screener = self._get_screener()
        result = screener.screen([], regime="sideways")
        assert result == []

    def test_hard_filter_removes_limit_up(self):
        """涨停股应被过滤"""
        screener = self._get_screener()
        import pandas as pd
        df = pd.DataFrame([
            {"代码": "600519", "名称": "贵州茅台", "最新价": 1800.0, "涨跌幅": 10.0,
             "最高": 1800.0, "最低": 1650.0, "今开": 1650.0, "昨收": 1636.0,
             "成交量": 100000, "成交额": 180000000, "换手率": 2.0},
            {"代码": "000001", "名称": "平安银行", "最新价": 11.5, "涨跌幅": 2.0,
             "最高": 11.6, "最低": 11.2, "今开": 11.3, "昨收": 11.3,
             "成交量": 5000000, "成交额": 60000000, "换手率": 5.0},
        ])
        result = screener.screen(df, regime="sideways", max_count=10)
        codes = [s["code"] for s in result]
        assert "600519" not in codes  # 涨停被排除
        assert "000001" in codes

    def test_hard_filter_removes_low_price(self):
        """低价股应被过滤"""
        screener = self._get_screener()
        import pandas as pd
        df = pd.DataFrame([
            {"代码": "600001", "名称": "低价股", "最新价": 1.0, "涨跌幅": 3.0,
             "最高": 1.05, "最低": 0.95, "今开": 0.98, "昨收": 0.97,
             "成交量": 1000000, "成交额": 10000000, "换手率": 5.0},
            {"代码": "600519", "名称": "正常股", "最新价": 100.0, "涨跌幅": 2.0,
             "最高": 101.0, "最低": 99.0, "今开": 99.5, "昨收": 98.0,
             "成交量": 500000, "成交额": 50000000, "换手率": 4.0},
        ])
        result = screener.screen(df, regime="sideways", max_count=10)
        codes = [s["code"] for s in result]
        assert "600001" not in codes  # 低价股被排除

    def test_stage2_scores_exist(self):
        """评分应被添加到结果中"""
        screener = self._get_screener()
        import pandas as pd
        df = pd.DataFrame([
            {"代码": "600519", "名称": "茅台", "最新价": 1800.0, "涨跌幅": 2.0,
             "最高": 1820.0, "最低": 1780.0, "今开": 1790.0, "昨收": 1764.0,
             "成交量": 100000, "成交额": 180000000, "换手率": 3.5},
        ])
        result = screener.screen(df, regime="sideways", max_count=10)
        assert len(result) == 1
        assert "score" in result[0]
        assert result[0]["score"] > 0

    def test_regime_bull_prefers_momentum(self):
        """牛市环境应偏好高动量股"""
        screener = self._get_screener()
        import pandas as pd
        # 两只股票: 其他条件相同，仅涨跌幅不同
        base = {
            "最高": 51.0, "最低": 49.0, "今开": 49.5, "昨收": 50.0,
            "成交量": 3000000, "成交额": 150000000, "换手率": 5.0,
            "最新价": 50.0,
        }
        df = pd.DataFrame([
            {**base, "代码": "600001", "名称": "涨股", "涨跌幅": 8.0},
            {**base, "代码": "600002", "名称": "跌股", "涨跌幅": -3.0},
        ])
        result_bull = screener.screen(df, regime="bull", max_count=10)
        # 牛市中涨股评分应高于跌股
        assert result_bull[0]["code"] == "600001"

    def test_factor_weights_sum_to_one(self):
        """因子权重之和应为1"""
        screener = self._get_screener()
        total = sum(screener.factor_weights.values())
        assert abs(total - 1.0) < 0.01

    def test_max_per_prefix_limit(self):
        """每个代码前缀的股票数量应受限制"""
        screener = self._get_screener()
        screener.max_per_prefix = 1  # 每组最多1只
        import pandas as pd
        df = pd.DataFrame([
            {"代码": f"60000{i}", "名称": f"股票{i}", "最新价": 50.0, "涨跌幅": 1.0,
             "最高": 51.0, "最低": 49.0, "今开": 49.5, "昨收": 49.0,
             "成交量": 3000000, "成交额": 150000000, "换手率": 5.0}
            for i in range(5)
        ])
        result = screener.screen(df, regime="sideways", max_count=10)
        # 所有代码前缀都是 "600"，最多只能取 max_per_prefix 只
        assert len(result) <= screener.max_per_prefix


# ==================== 运行入口 ====================

if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
