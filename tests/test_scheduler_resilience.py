"""
单元测试：本地断续运行环境下的时序状态机与审计账本测试
测试范围:
1. 2026年第十三届全国证券投资模拟实训大赛（团队赛段）比赛规则与日历加载
2. 调度审计账本 (scheduler_ledger) 状态迁移与 SQLite 双重持久化
3. 断续开机/晚开机 (如 09:40 启动) 的漏步审计：时效废弃 (CRITICAL_EXPIRE) 与强补偿 (MUST_CATCHUP)
4. 虚拟时间注入与时序窗口匹配 (Time Travel Mock Clock)
5. 模拟单步演练 (Dry-Run Step Runner)
6. 系统休眠跳跃自愈逻辑 (Wakeup Leap Detection)
"""

import os
import sys
import unittest
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from unittest.mock import patch, MagicMock

# 将 src 加入 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from competition import competition_manager
from scheduler_ledger import scheduler_ledger, StepStatus, CatchupPolicy
from ai_scheduler import ai_scheduler


class TestCompetitionRulesAndWindows(unittest.TestCase):
    """测试比赛规则及细分时序窗口"""

    def test_team_competition_dates_and_rules(self):
        rules = competition_manager.get_rules()
        self.assertIn("第十三届", rules.get("competition_name", ""))
        self.assertIn("团队赛段", rules.get("competition_name", ""))
        period = rules.get("period", {})
        self.assertEqual(period.get("start"), "2026-10-19")
        self.assertEqual(period.get("end"), "2026-11-29")
        self.assertEqual(rules.get("initial_funds"), 1000000)

        # 测试国庆休市
        self.assertFalse(competition_manager.is_trading_day(date(2026, 10, 1)))
        self.assertFalse(competition_manager.is_trading_day(date(2026, 10, 2)))

    def test_time_window_classification(self):
        # 09:18 应为盘前新闻
        t_pre = datetime(2026, 10, 20, 9, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        win_pre = competition_manager.get_detailed_time_window(t_pre)
        self.assertEqual(win_pre["window_id"], "W1_PRE_MARKET_NEWS")
        self.assertTrue(win_pre["is_active"])

        # 09:27 应为集合竞价
        t_call = datetime(2026, 10, 20, 9, 27, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        win_call = competition_manager.get_detailed_time_window(t_call)
        self.assertEqual(win_call["window_id"], "W2_CALL_AUCTION")
        self.assertEqual(win_call["catchup_policy"], "POLICY_TIME_CRITICAL_DISCARD")

        # 09:32 应为早盘开盘
        t_open = datetime(2026, 10, 20, 9, 32, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        win_open = competition_manager.get_detailed_time_window(t_open)
        self.assertEqual(win_open["window_id"], "W3_MORNING_OPEN")

        # 10:15 应为连续竞价早盘
        t_intra = datetime(2026, 10, 20, 10, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        win_intra = competition_manager.get_detailed_time_window(t_intra)
        self.assertEqual(win_intra["window_id"], "W4_INTRADAY_MORNING")

        # 12:00 应为午间休市
        t_lunch = datetime(2026, 10, 20, 12, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        win_lunch = competition_manager.get_detailed_time_window(t_lunch)
        self.assertEqual(win_lunch["window_id"], "W5_MIDDAY_RECESS")

        # 14:55 应为尾盘调仓
        t_close = datetime(2026, 10, 20, 14, 55, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        win_close = competition_manager.get_detailed_time_window(t_close)
        self.assertEqual(win_close["window_id"], "W7_CLOSING_REBALANCE")


class TestSchedulerLedger(unittest.TestCase):
    """测试调度审计账本与持久化"""

    def setUp(self):
        self.test_date = "2026-10-20"
        conn = scheduler_ledger._get_db_conn()
        with scheduler_ledger._lock:
            conn.cursor().execute("DELETE FROM scheduler_execution_ledger WHERE date = ?", (self.test_date,))
            conn.commit()
        scheduler_ledger.init_daily_ledger(self.test_date)

    def tearDown(self):
        conn = scheduler_ledger._get_db_conn()
        with scheduler_ledger._lock:
            conn.cursor().execute("DELETE FROM scheduler_execution_ledger WHERE date = ?", (self.test_date,))
            conn.commit()

    def test_ledger_initialization(self):
        entries = scheduler_ledger.get_daily_ledger(self.test_date)
        self.assertGreaterEqual(len(entries), 6)
        step_ids = [e["step_id"] for e in entries]
        self.assertIn("pre_market_news", step_ids)
        self.assertIn("call_auction", step_ids)
        self.assertIn("morning_open", step_ids)
        self.assertIn("midday_recess", step_ids)
        self.assertIn("closing_rebalance", step_ids)
        self.assertIn("after_hours_settle", step_ids)

        for e in entries:
            self.assertEqual(e["status"], StepStatus.PENDING.value)

    def test_ledger_record_and_finish(self):
        now_dt = datetime(2026, 10, 20, 9, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        scheduler_ledger.record_start("pre_market_news", now=now_dt)

        finish_dt = datetime(2026, 10, 20, 9, 21, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
        scheduler_ledger.record_finish(
            step_id="pre_market_news",
            status=StepStatus.SUCCESS,
            duration_ms=1250,
            summary="新闻抓取完成，情绪评分58分",
            now=finish_dt
        )

        entries = scheduler_ledger.get_daily_ledger(self.test_date)
        entry_map = {e["step_id"]: e for e in entries}
        pm_entry = entry_map["pre_market_news"]
        self.assertEqual(pm_entry["status"], StepStatus.SUCCESS.value)
        self.assertEqual(pm_entry["duration_ms"], 1250)
        self.assertIn("情绪评分58分", pm_entry["output_summary"])

    def test_boot_late_audit_missed_steps(self):
        """测试用户本地电脑在 09:42 晚开机时，漏步审计能否精准分辨'安全废弃'与'补偿执行'"""
        test_date = "2026-10-21"
        conn = scheduler_ledger._get_db_conn()
        with scheduler_ledger._lock:
            conn.cursor().execute("DELETE FROM scheduler_execution_ledger WHERE date = ?", (test_date,))
            conn.commit()
        scheduler_ledger.init_daily_ledger(test_date)
        late_boot_time = datetime(2026, 10, 21, 9, 42, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

        needs_catchup, safe_skipped = scheduler_ledger.audit_missed_steps(late_boot_time)

        # 09:15-09:25 的 pre_market_news 为 MUST_CATCHUP，必须在补偿列表中
        catchup_ids = [s.step_id for s in needs_catchup]
        self.assertIn("pre_market_news", catchup_ids)

        # 09:25-09:30 的 call_auction 为 CRITICAL_EXPIRE，必须在安全废弃列表中，绝对不可追单
        skipped_ids = [s.step_id for s in safe_skipped]
        self.assertIn("call_auction", skipped_ids)

        # 验证账本中的状态已被置为 SKIPPED_EXPIRED 并且记录了详细原因
        entries = scheduler_ledger.get_daily_ledger("2026-10-21")
        entry_map = {e["step_id"]: e for e in entries}
        call_entry = entry_map["call_auction"]
        self.assertEqual(call_entry["status"], StepStatus.SKIPPED_EXPIRED.value)
        self.assertIn("严禁盘中追高", call_entry["skip_reason"])


class TestVirtualTimeAndDebugStep(unittest.TestCase):
    """测试虚拟时钟 (Mock Clock) 与单步演练 (Dry-Run)"""

    def tearDown(self):
        ai_scheduler.clear_mock_time()

    def test_mock_time_injection_and_clearing(self):
        self.assertIsNone(ai_scheduler._mock_time)
        ai_scheduler.set_mock_time("2026-10-20 09:28:15")
        curr = ai_scheduler.now()
        self.assertEqual(curr.year, 2026)
        self.assertEqual(curr.month, 10)
        self.assertEqual(curr.day, 20)
        self.assertEqual(curr.hour, 9)
        self.assertEqual(curr.minute, 28)

        # 清除虚拟时间
        ai_scheduler.clear_mock_time()
        self.assertIsNone(ai_scheduler._mock_time)
        self.assertEqual(ai_scheduler.now().year, datetime.now().year)

    def test_dry_run_step_execution(self):
        ai_scheduler.set_mock_time("2026-10-20 14:52:00")
        today = ai_scheduler.now().date().isoformat()
        conn = scheduler_ledger._get_db_conn()
        with scheduler_ledger._lock:
            conn.cursor().execute("DELETE FROM scheduler_execution_ledger WHERE date = ?", (today,))
            conn.commit()
        scheduler_ledger.init_daily_ledger(today)

        # 演练尾盘调仓 (Dry-Run)
        result = ai_scheduler.run_step_now("closing_rebalance", dry_run=True)
        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("step_id"), "closing_rebalance")

        # 验证账本记录为 SUCCESS
        entries = scheduler_ledger.get_daily_ledger(today)
        entry_map = {e["step_id"]: e for e in entries}
        self.assertEqual(entry_map["closing_rebalance"]["status"], StepStatus.SUCCESS.value)

    def test_get_status_contains_resilience_metadata(self):
        ai_scheduler.set_mock_time("2026-10-20 11:45:00")
        st = ai_scheduler.get_status()
        self.assertTrue(st["is_mock_time"])
        self.assertIn("current_window", st)
        self.assertEqual(st["current_window"]["window_id"], "W5_MIDDAY_RECESS")
        self.assertIn("ledger_summary", st)
        self.assertGreaterEqual(st["ledger_summary"]["total_steps"], 6)


if __name__ == "__main__":
    unittest.main()
