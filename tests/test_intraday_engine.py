"""
单元测试：盘中时序量化引擎 (Intraday Timing Engine) 核心测试集
覆盖范围:
1. TimingDipBuyer: 时序低吸安全边界与微观盘口度量 (正常回踩触发买入, >5.0% 拦截, <-7.0% 拦截, OBI/量比/踏空统计)
2. PositionRetentionDecider: 持仓去留全要素决策 (冲高回落落袋, 缩量破位换仓, 涨停封板顺延, 硬止损, 成本三级兜底, 舆情权重修正)
3. UnsoldPositionController: 超时调控状态机与复合键隔离 (AWAITING_TIME -> REPRICING_ORDERED -> 下一周期门禁跳过, (account_id, symbol, order_id) 防覆盖)
"""

import sys
import os
import unittest
import threading
from pathlib import Path
from datetime import datetime, timedelta

# 加入 src 路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from intraday_timing_engine import (
    TimingDipBuyer,
    PositionRetentionDecider,
    UnsoldPositionController,
    get_detailed_timing_metrics,
    intraday_timing_engine,
    HARD_STOP_LOSS
)


class TestTimingDipBuyer(unittest.TestCase):
    """测试时序低吸买入与安全边界判定"""

    def setUp(self):
        self.buyer = TimingDipBuyer()

    def test_normal_dip_buy_trigger(self):
        """测试正常回踩（1.5%~3.8%区间且价格高于均线）触发低吸买入信号"""
        symbol = "002594"
        self.buyer.register_unbought_candidate(symbol, "比亚迪", score=80.0, target_price=240.0)

        # 模拟早盘冲高后健康回踩：现价242.0，最高248.0 -> 回踩 (248-242)/248 = 2.42%
        quote = {
            "bid1_vol": 6000,
            "ask1_vol": 2000,
            "volume_ratio": 1.8,
            "turnover_ratio": 2.5
        }
        res = self.buyer.update_quote(
            symbol=symbol,
            current_price=242.0,
            change_pct=2.0,
            high=248.0,
            low=238.0,
            volume=50000,
            quote=quote
        )

        self.assertEqual(res["signal"], "TIMING_DIP_BUY")
        self.assertEqual(res["suggested_action"], "EXECUTE_DIP_BUY")
        self.assertGreaterEqual(res["dip_score"], 75.0)
        self.assertGreater(res["obi"], 0.0)
        self.assertGreater(res["missed_gain_pct"], 0.0)

    def test_deep_fall_pullback_intercept(self):
        """测试回踩过深（>4.5% 或 >5.0%）被安全门禁拦截，判定为 AVOID_DEEP_FALL"""
        symbol = "000001"
        self.buyer.register_unbought_candidate(symbol, "平安银行", score=75.0, target_price=12.0)

        # 最高 12.50，跌到 11.80 -> 回踩 (12.5-11.8)/12.5 = 5.6% > 4.5%
        res = self.buyer.update_quote(
            symbol=symbol,
            current_price=11.80,
            change_pct=-1.5,
            high=12.50,
            low=11.75,
            volume=30000
        )

        self.assertEqual(res["signal"], "AVOID_DEEP_FALL")
        self.assertEqual(res["suggested_action"], "WAIT_FOR_STABILIZE")
        self.assertLessEqual(res["dip_score"], 45.0)

    def test_sharp_plunge_intercept(self):
        """测试日内暴跌或跳水（<-7.0%）被拦截，拒绝盲目抄底接飞刀 (REJECT_FALLING_KNIFE)"""
        symbol = "600000"
        self.buyer.register_unbought_candidate(symbol, "浦发银行", score=72.0, target_price=10.0)

        # 跌幅 -7.5%，最高10.20，现价9.30 -> 命中 change_pct < -7.0% 安全门禁
        res = self.buyer.update_quote(
            symbol=symbol,
            current_price=9.30,
            change_pct=-7.5,
            high=10.20,
            low=9.25,
            volume=80000
        )

        self.assertEqual(res["signal"], "AVOID_DEEP_FALL")
        self.assertEqual(res["suggested_action"], "REJECT_FALLING_KNIFE")
        self.assertLessEqual(res["dip_score"], 40.0)

    def test_overbought_high_intercept(self):
        """测试冲高顶部（change_pct > 5.0%）追高风险拦截 (REJECT_CHASE_HIGH)"""
        symbol = "300750"
        self.buyer.register_unbought_candidate(symbol, "宁德时代", score=85.0, target_price=200.0)

        # 涨幅 6.0% > 5.0%
        res = self.buyer.update_quote(
            symbol=symbol,
            current_price=211.0,
            change_pct=6.0,
            high=212.0,
            low=200.0,
            volume=60000
        )

        self.assertEqual(res["signal"], "OVERBOUGHT_HIGH")
        self.assertEqual(res["suggested_action"], "REJECT_CHASE_HIGH")


class TestPositionRetentionDecider(unittest.TestCase):
    """测试持仓去留决策（去/留/减/换）"""

    def setUp(self):
        self.decider = PositionRetentionDecider()

    def test_cost_triple_fallback(self):
        """测试成本价三级兜底：cost_price -> avg_price -> total_cost / amount，防止 profit_rate 被误算为 0.0"""
        pos = {
            "symbol": "000001",
            "name": "平安银行",
            "amount": 1000,
            "total_cost": 10000.0
            # 缺失 cost_price 与 avg_price
        }
        quote = {"price": 10.50, "change_pct": 2.0, "high": 10.60, "vwap": 10.30}
        res = self.decider.evaluate_retention(pos, quote, news_sentiment_score=50)

        self.assertAlmostEqual(res["cost_price"], 10.0, places=2)
        self.assertAlmostEqual(res["profit_rate"], 0.05, places=2)
        self.assertEqual(res["action"], "KEEP_RIDE_TREND")

    def test_pullback_trim_protect_profit(self):
        """测试曾经大赚但从日内高点回落过深（profit>=3.5% 且 pullback>=2.5%）触发减仓锁定战果"""
        pos = {
            "symbol": "600519",
            "name": "贵州茅台",
            "cost_price": 1500.0,
            "amount": 100
        }
        # 现价 1560 (浮盈 4.0% >= 3.5%)，最高 1610 (回落 (1610-1560)/1610 = 3.1% >= 2.5%)
        quote = {
            "price": 1560.0,
            "high": 1610.0,
            "change_pct": 2.5,
            "vwap": 1550.0
        }
        res = self.decider.evaluate_retention(pos, quote)
        self.assertEqual(res["action"], "TRIM_PROTECT_PROFIT")
        self.assertIn("减", res["retention_decision"])

    def test_weak_break_down_sell_exit(self):
        """测试缩量破位（跌破日内分时均线 1.8% 且处于绿盘翻绿）触发破位换仓去留指令"""
        pos = {
            "symbol": "600000",
            "name": "浦发银行",
            "cost_price": 10.0,
            "amount": 1000
        }
        # vwap=10.0, 现价=9.80 (< 10.0 * 0.982 = 9.82), change_pct=-1.5%
        quote = {
            "price": 9.80,
            "high": 10.10,
            "change_pct": -1.5,
            "vwap": 10.0
        }
        res = self.decider.evaluate_retention(pos, quote)
        self.assertEqual(res["action"], "SELL_WEAK_EXIT")
        self.assertIn("去", res["retention_decision"])

    def test_limit_up_locked_keep_ride(self):
        """测试股票封死涨停（change_pct>=9.8% 且 ask1_vol=0）触发坚决留仓"""
        pos = {"symbol": "002594", "name": "比亚迪", "cost_price": 200.0, "amount": 200}
        quote = {
            "price": 220.0,
            "high": 220.0,
            "change_pct": 10.0,
            "ask1_vol": 0,
            "vwap": 215.0
        }
        res = self.decider.evaluate_retention(pos, quote)
        self.assertEqual(res["action"], "KEEP_LIMIT_UP")
        self.assertIn("留", res["retention_decision"])
        self.assertGreaterEqual(res["confidence"], 0.95)

    def test_hard_stop_loss_trigger(self):
        """测试触及硬止损红线（浮亏 <= HARD_STOP_LOSS -4.5%）坚决清仓防大回撤"""
        pos = {"symbol": "000001", "name": "平安银行", "cost_price": 10.0, "amount": 1000}
        # 现价 9.50，浮亏 -5.0% <= HARD_STOP_LOSS (-4.5%)
        quote = {"price": 9.50, "change_pct": -5.0, "high": 9.90, "vwap": 9.60}
        res = self.decider.evaluate_retention(pos, quote)
        self.assertEqual(res["action"], "EMERGENCY_STOP_LOSS")
        self.assertEqual(res["urgency"], "HIGH")

    def test_sentiment_weighting_modification(self):
        """测试舆情负面降低容忍度提前避险 & 舆情高分增强多头信心"""
        pos_loss = {"symbol": "600000", "cost_price": 10.0, "amount": 1000}
        quote_loss = {"price": 9.80, "change_pct": -2.0, "vwap": 9.85}

        # 情绪分 20 < 30 且浮亏 -> 提前触发 SELL_WEAK_EXIT
        res_sent_weak = self.decider.evaluate_retention(pos_loss, quote_loss, news_sentiment_score=20)
        self.assertEqual(res_sent_weak["action"], "SELL_WEAK_EXIT")
        self.assertIn("舆情极度悲观", res_sent_weak["reason"])

        # 情绪分 85 > 70 且处于多头均线上方 -> confidence >= 0.85
        pos_bull = {"symbol": "600519", "cost_price": 1600.0, "amount": 100}
        quote_bull = {"price": 1650.0, "change_pct": 3.0, "vwap": 1630.0}
        res_sent_bull = self.decider.evaluate_retention(pos_bull, quote_bull, news_sentiment_score=85)
        self.assertEqual(res_sent_bull["action"], "KEEP_RIDE_TREND")
        self.assertGreaterEqual(res_sent_bull["confidence"], 0.85)


class TestUnsoldPositionController(unittest.TestCase):
    """测试超时调控状态机与复合键防覆盖"""

    def setUp(self):
        self.ctrl = UnsoldPositionController()

    def test_composite_key_prevents_overwrite(self):
        """测试 T10: 同一账户针对同一标的分批多笔卖单登记，复合键 (account_id, symbol, order_id) 互不覆盖"""
        symbol = "000001"
        # 登记第一笔分批挂单
        key1 = self.ctrl.register_expected_sell(
            symbol=symbol,
            name="平安银行",
            expected_sell_time_str="09:40:00",
            target_price=11.50,
            order_id="ORD_001",
            account_id=1
        )

        # 登记第二笔分批挂单（不同 order_id 与 target_price）
        key2 = self.ctrl.register_expected_sell(
            symbol=symbol,
            name="平安银行",
            expected_sell_time_str="09:45:00",
            target_price=11.80,
            order_id="ORD_002",
            account_id=1
        )

        self.assertNotEqual(key1, key2)
        self.assertEqual(len(self.ctrl.pending_sells), 2)
        self.assertIn((1, symbol, "ORD_001"), self.ctrl.pending_sells)
        self.assertIn((1, symbol, "ORD_002"), self.ctrl.pending_sells)

        # 验证汇总列表能够同时包含两笔挂单
        summary = self.ctrl.get_unsold_summary_list(account_id=1)
        self.assertEqual(len(summary), 2)
        order_ids = [x.get("order_id") for x in summary]
        self.assertIn("ORD_001", order_ids)
        self.assertIn("ORD_002", order_ids)

    def test_state_machine_transition_and_gate_skip(self):
        """测试超时调控状态机迁移：AWAITING_TIME -> REPRICING_ORDERED -> 门禁短路跳过"""
        symbol = "600519"
        self.ctrl.register_expected_sell(
            symbol=symbol,
            name="贵州茅台",
            expected_sell_time_str="09:40:00",
            target_price=1600.0,
            order_id="ORD_MT_1",
            account_id=1
        )

        # 1. 尚未到达预期时间（09:35:00 < 09:40:00）-> 维持挂单等待
        st1 = self.ctrl.evaluate_unsold_status(
            symbol=symbol,
            current_price=1600.0,
            change_pct=1.0,
            now_time_str="09:35:00",
            account_id=1,
            order_id="ORD_MT_1"
        )
        self.assertEqual(st1["status"], "AWAITING_TIME")
        self.assertEqual(st1["tuning_action"], "WAIT_SCHEDULED_TIME")

        # 2. 已超时且价格跌破挂单价 1.5% (1570 < 1600 * 0.985) -> 紧急降价并迁移为 REPRICING_ORDERED
        quote = {"bid1_vol": 1000, "ask1_vol": 5000}
        st2 = self.ctrl.evaluate_unsold_status(
            symbol=symbol,
            current_price=1570.0,
            change_pct=-1.5,
            now_time_str="09:42:00",
            account_id=1,
            order_id="ORD_MT_1",
            quote=quote
        )
        self.assertEqual(st2["status"], "REPRICING_ORDERED")
        self.assertEqual(st2["tuning_action"], "REPRICE_URGENT_SELL")
        self.assertGreater(st2["slippage_pct"], 1.5)

        # 3. 门禁测试：下一个 tick (如 09:42:30) 再次触发评估时，命中门禁短路直接跳过，防止幽灵重复刷单
        st3 = self.ctrl.evaluate_unsold_status(
            symbol=symbol,
            current_price=1568.0,
            change_pct=-1.6,
            now_time_str="09:42:30",
            account_id=1,
            order_id="ORD_MT_1"
        )
        self.assertTrue(st3.get("skip"))
        self.assertEqual(st3.get("tuning_action"), "ALREADY_ORDERED")

    def test_overdue_limit_up_cancel_and_ride(self):
        """测试超时但股价封死涨停时，调控为取消卖单并顺延持仓"""
        symbol = "002594"
        self.ctrl.register_expected_sell(
            symbol=symbol,
            name="比亚迪",
            expected_sell_time_str="09:40:00",
            target_price=240.0,
            order_id="ORD_BYD_1",
            account_id=1
        )

        res = self.ctrl.evaluate_unsold_status(
            symbol=symbol,
            current_price=245.0,
            change_pct=10.0,  # 涨停板
            now_time_str="09:45:00",
            account_id=1,
            order_id="ORD_BYD_1"
        )
        self.assertEqual(res["tuning_action"], "CANCEL_AND_RIDE")
        self.assertIn("取消卖出继续享受涨幅", res["tuning_reason"])

    def test_cancel_specific_order_id(self):
        """测试按指定 order_id 撤销挂单，不影响同标的其他挂单"""
        symbol = "000001"
        self.ctrl.register_expected_sell(symbol, "平安银行", "09:40:00", 11.5, order_id="ORD_A", account_id=1)
        self.ctrl.register_expected_sell(symbol, "平安银行", "09:45:00", 11.8, order_id="ORD_B", account_id=1)

        # 仅撤销 ORD_A
        cancelled = self.ctrl.cancel_expected_sell(symbol=symbol, account_id=1, order_id="ORD_A")
        self.assertTrue(cancelled)

        summary = self.ctrl.get_unsold_summary_list(account_id=1)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["order_id"], "ORD_B")


class TestDetailedTimingMetricsIntegration(unittest.TestCase):
    """测试全局时序微观量化指标汇聚接口"""

    def test_get_detailed_timing_metrics(self):
        metrics = get_detailed_timing_metrics(account_id=1)
        self.assertEqual(metrics["status"], "success")
        self.assertIn("statistics", metrics)
        self.assertIn("unbought_candidates", metrics)
        self.assertIn("actionable_dips", metrics)
        self.assertIn("unsold_positions", metrics)
        self.assertIn("weights", metrics)




class TestConcurrencyAndBoundaryConditions(unittest.TestCase):
    """测试高烈度并发线程安全与时间边界容错"""

    def setUp(self):
        self.buyer = TimingDipBuyer()
        self.ctrl = UnsoldPositionController()

    def test_concurrent_intraday_engine_thread_safety(self):
        """
        方案 8: 高并发压力测试 (10 个线程利用 Barrier 同步起跑，5 读 5 写，每线程 200 次，共 2000 次操作)
        验证 TimingDipBuyer 与 UnsoldPositionController 内部 RLock 保护下零 RuntimeError / 字典迭代修改异常
        """
        num_writers = 5
        num_readers = 5
        total_threads = num_writers + num_readers
        iterations = 200
        barrier = threading.Barrier(total_threads)
        errors = []

        def writer_task(tid: int):
            try:
                barrier.wait(timeout=10.0)
                for i in range(iterations):
                    sym = f"600{tid:02d}{i%10}"
                    # 1. 写 TimingDipBuyer
                    self.buyer.register_unbought_candidate(
                        symbol=sym,
                        name=f"标的_{sym}",
                        score=75.0 + (i % 20),
                        target_price=10.0 + (i % 50) * 0.1
                    )
                    self.buyer.update_quote(
                        symbol=sym,
                        current_price=10.0 + (i % 50) * 0.1,
                        change_pct=1.5,
                        quote={"bid1_vol": 1000, "ask1_vol": 500}
                    )
                    # 2. 写 UnsoldPositionController
                    ord_id = f"ORD_{tid}_{i}"
                    self.ctrl.register_expected_sell(
                        symbol=sym,
                        name=f"标的_{sym}",
                        expected_sell_time_str="09:40:00",
                        target_price=11.0,
                        order_id=ord_id,
                        account_id=(tid % 3) + 1
                    )
                    if i % 3 == 0:
                        self.ctrl.cancel_expected_sell(symbol=sym, account_id=(tid % 3) + 1, order_id=ord_id)
            except Exception as e:
                errors.append(f"Writer-{tid} Exception: {e}")

        def reader_task(tid: int):
            try:
                barrier.wait(timeout=10.0)
                for i in range(iterations):
                    sym = f"600{(i%num_writers):02d}{i%10}"
                    # 1. 读 TimingDipBuyer 汇总
                    _ = self.buyer.get_summary_list()
                    _ = self.buyer.get_actionable_dip_candidates()

                    # 2. 读 UnsoldPositionController
                    _ = self.ctrl.get_unsold_summary_list(account_id=(tid % 3) + 1)
                    _ = self.ctrl.evaluate_unsold_status(
                        symbol=sym,
                        current_price=10.5,
                        change_pct=1.0,
                        now_time_str="09:45:00",
                        account_id=(tid % 3) + 1,
                        order_id=f"ORD_{tid}_{i}"
                    )
            except Exception as e:
                errors.append(f"Reader-{tid} Exception: {e}")

        threads = []
        for t in range(num_writers):
            th = threading.Thread(target=writer_task, args=(t,))
            threads.append(th)
            th.start()

        for t in range(num_readers):
            th = threading.Thread(target=reader_task, args=(t,))
            threads.append(th)
            th.start()

        for th in threads:
            th.join(timeout=15.0)

        self.assertEqual(len(errors), 0, f"并发安全测试中抛出异常: {errors}")

    def test_time_parsing_and_boundary(self):
        """
        方案 8: 边界测试 (时间解析健壮性与边界覆盖)
        验证缺失前导零 ('9:30:00')、跨分钟格式 ('9:5:0')、异常字符串、None、空串等均能安全容错且正确判断超时
        """
        # 1. 缺失前导零的情况：'9:30:00' vs '09:35:00' (34200 < 34500 -> 未超时)
        self.assertFalse(UnsoldPositionController._check_is_overdue("9:30:00", "09:35:00"))
        # 2. 缺失前导零且确实超时：'9:40:00' vs '09:35:00' (34800 > 34500 -> 超时)
        self.assertTrue(UnsoldPositionController._check_is_overdue("9:40:00", "09:35:00"))
        # 3. 双方均无前导零：'9:40:00' vs '9:35:00' -> 超时
        self.assertTrue(UnsoldPositionController._check_is_overdue("9:40:00", "9:35:00"))
        # 4. HH:MM 短格式测试
        self.assertTrue(UnsoldPositionController._check_is_overdue("09:40", "09:35"))
        self.assertFalse(UnsoldPositionController._check_is_overdue("09:30", "09:35"))
        # 5. 异常格式与 None 容错 (不抛异常崩溃，安全返回 bool)
        self.assertFalse(UnsoldPositionController._check_is_overdue("", "09:35:00"))
        self.assertFalse(UnsoldPositionController._check_is_overdue(None, "09:35:00"))
        self.assertFalse(UnsoldPositionController._check_is_overdue("invalid_time", "09:35:00"))
        self.assertFalse(UnsoldPositionController._check_is_overdue("09:35:00", None))

        # 6. 在 evaluate_unsold_status 中集成验证无前导零的场景
        sym = "600000"
        self.ctrl.register_expected_sell(
            symbol=sym,
            name="浦发银行",
            expected_sell_time_str="09:35:00",
            target_price=10.0,
            order_id="ORD_TIME_1",
            account_id=1
        )
        # 当前时间 9:30:00 (尚未到达 09:35:00)
        res_early = self.ctrl.evaluate_unsold_status(
            symbol=sym,
            current_price=10.0,
            change_pct=0.5,
            now_time_str="9:30:00",
            account_id=1,
            order_id="ORD_TIME_1"
        )
        self.assertEqual(res_early["status"], "AWAITING_TIME")
        self.assertFalse(res_early["is_overdue"])

        # 当前时间 9:40:00 (已超过 09:35:00)
        res_late = self.ctrl.evaluate_unsold_status(
            symbol=sym,
            current_price=10.0,
            change_pct=0.5,
            now_time_str="9:40:00",
            account_id=1,
            order_id="ORD_TIME_1"
        )
        self.assertTrue(res_late["is_overdue"])


if __name__ == "__main__":
    unittest.main()
