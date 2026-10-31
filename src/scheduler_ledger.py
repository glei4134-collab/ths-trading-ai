"""
调度执行审计账本模块 (Scheduler Execution Ledger)
专为本地非24小时连续运行设计，提供时序状态机持久化、步骤审计、断续开机与休眠唤醒补偿决策。
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, date, time
from zoneinfo import ZoneInfo
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from paths import DATA_DIR, CONFIG_DIR


class StepStatus(str, Enum):
    """步骤执行状态"""
    PENDING = "PENDING"                    # 待到达时间窗口
    RUNNING = "RUNNING"                    # 正在执行
    SUCCESS = "SUCCESS"                    # 按时执行成功
    FAILED = "FAILED"                      # 执行异常报错
    SKIPPED_EXPIRED = "SKIPPED_EXPIRED"    # 超出时效窗口，为资产安全主动废弃跳过
    SKIPPED_CONDITION = "SKIPPED_CONDITION"# 前置条件不满足跳过（如非交易日/资金不足）
    CATCHUP_SUCCESS = "CATCHUP_SUCCESS"    # 断续开机/唤醒后补偿执行成功


class CatchupPolicy(str, Enum):
    """断电/关机/休眠错过窗口后的补偿策略"""
    MUST_CATCHUP = "MUST_CATCHUP"          # 强补偿型：即使开机晚了也必须执行（如对账、同步持仓、撤无效单）
    CRITICAL_EXPIRE = "CRITICAL_EXPIRE"    # 时效废弃型：一旦超时严禁追单补做，直接标记安全跳过（如集合竞价）
    DEGRADE_ADAPT = "DEGRADE_ADAPT"        # 降级自适应型：降级为当下等价的盘中动态版本执行


class StepDefinition:
    """时序步骤定义"""
    def __init__(
        self,
        step_id: str,
        name: str,
        window_start: str,       # "HH:MM"
        window_end: str,         # "HH:MM"
        policy: CatchupPolicy,
        description: str,
        expire_tolerance_sec: int = 300, # 允许的最长延迟宽限秒数
    ):
        self.step_id = step_id
        self.name = name
        self.window_start = window_start
        self.window_end = window_end
        self.policy = policy
        self.description = description
        self.expire_tolerance_sec = expire_tolerance_sec

    def to_dict(self) -> Dict:
        return {
            "step_id": self.step_id,
            "name": self.name,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "window": f"{self.window_start}-{self.window_end}",
            "policy": self.policy.value if isinstance(self.policy, CatchupPolicy) else str(self.policy),
            "description": self.description,
            "expire_tolerance_sec": self.expire_tolerance_sec,
        }


# 默认系统标准预设时序步骤（8大时序流水线）
STANDARD_STEPS: List[StepDefinition] = [
    StepDefinition(
        step_id="pre_market_self_check",
        name="盘前系统自检与基线核验",
        window_start="09:00",
        window_end="09:15",
        policy=CatchupPolicy.MUST_CATCHUP,
        description="检查系统健康、SQLite与网络数据连通性、账户Cookie及资金基线核验",
        expire_tolerance_sec=1800,
    ),
    StepDefinition(
        step_id="pre_market_news",
        name="盘前新闻舆情与情绪扫描",
        window_start="09:15",
        window_end="09:25",
        policy=CatchupPolicy.MUST_CATCHUP,
        description="全网抓取最新财经宏观与个股新闻，预热 News Sentiment Engine 2.0 缓存，为全天交易定调",
        expire_tolerance_sec=1800,
    ),
    StepDefinition(
        step_id="call_auction",
        name="集合竞价决断与冲击池筛选",
        window_start="09:25",
        window_end="09:30",
        policy=CatchupPolicy.CRITICAL_EXPIRE,
        description="依据9:25定盘行情和等效量比换算选出早盘冲击标的。超9:30严禁追买，以防盘中盲目追高",
        expire_tolerance_sec=120,
    ),
    StepDefinition(
        step_id="morning_open",
        name="早盘开盘突击与委托挂单清算",
        window_start="09:30",
        window_end="09:35",
        policy=CatchupPolicy.CRITICAL_EXPIRE,
        description="开盘冲击波执行，清理前日或昨夜残留委托单。超9:40弃用",
        expire_tolerance_sec=300,
    ),
    StepDefinition(
        step_id="midday_recess",
        name="午间休市复盘与挂单清理",
        window_start="11:30",
        window_end="13:00",
        policy=CatchupPolicy.MUST_CATCHUP,
        description="撤销早盘未成交废单防下午突发低开被动套牢，重新抓取午间突发消息面",
        expire_tolerance_sec=3600,
    ),
    StepDefinition(
        step_id="closing_rebalance",
        name="尾盘防隔夜回撤与调仓锁定",
        window_start="14:50",
        window_end="15:00",
        policy=CatchupPolicy.MUST_CATCHUP,
        description="T+1机制下尾盘买入确定性最高，执行硬止损清仓，撤销不可隔夜限价单，锁定全天战果",
        expire_tolerance_sec=600,
    ),
    StepDefinition(
        step_id="after_hours_settle",
        name="盘后清算对账与策略复盘",
        window_start="15:00",
        window_end="16:00",
        policy=CatchupPolicy.MUST_CATCHUP,
        description="与同花顺真实账户核对持仓与收益率，更新策略权重，生成次日预选池与复盘简报，执行数据库维护",
        expire_tolerance_sec=7200,
    ),
    StepDefinition(
        step_id="night_review",
        name="夜间复盘与自适应参数优化",
        window_start="20:00",
        window_end="23:59",
        policy=CatchupPolicy.MUST_CATCHUP,
        description="夜间全市场复盘、MarketRegime市场状态探测及参数网格自适应优化",
        expire_tolerance_sec=14400,
    ),
]


class SchedulerLedger:
    """调度执行审计账本（双重持久化 + 线程安全）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._steps: Dict[str, StepDefinition] = {s.step_id: s for s in STANDARD_STEPS}

    def get_registered_steps(self) -> List[Dict]:
        """获取所有已注册的步骤定义"""
        with self._lock:
            return [s.to_dict() for s in self._steps.values()]

    def _get_db_conn(self):
        """获取数据库连接"""
        from db_store import db_store
        return db_store.conn

    def _json_ledger_path(self, target_date: str) -> Path:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return DATA_DIR / f"scheduler_ledger_{target_date}.json"

    def init_daily_ledger(self, target_date: str = None) -> List[Dict]:
        """初始化当天的账本条目（若不存在则生成初始 PENDING 记录）"""
        if not target_date:
            target_date = date.today().isoformat()

        with self._lock:
            conn = self._get_db_conn()
            c = conn.cursor()

            for step in self._steps.values():
                scheduled_time = f"{target_date} {step.window_start}:00"
                c.execute("""
                    INSERT OR IGNORE INTO scheduler_execution_ledger (
                        date, step_id, step_name, window, status, policy, scheduled_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    target_date,
                    step.step_id,
                    step.name,
                    f"{step.window_start}-{step.window_end}",
                    StepStatus.PENDING.value,
                    step.policy.value if isinstance(step.policy, CatchupPolicy) else str(step.policy),
                    scheduled_time,
                ))
            conn.commit()

        entries = self.get_daily_ledger(target_date)
        self._sync_to_json(target_date, entries)
        return entries

    def get_daily_ledger(self, target_date: str = None) -> List[Dict]:
        """查询指定日期的完整审计账本"""
        if not target_date:
            target_date = date.today().isoformat()

        with self._lock:
            conn = self._get_db_conn()
            c = conn.cursor()
            c.execute("""
                SELECT id, date, step_id, step_name, window, status, policy,
                       scheduled_time, executed_at, duration_ms, skip_reason,
                       output_summary, error_detail, created_at
                FROM scheduler_execution_ledger
                WHERE date = ?
                ORDER BY scheduled_time ASC, id ASC
            """, (target_date,))
            rows = c.fetchall()

            entries = []
            for r in rows:
                entries.append(dict(r))

            if not entries:
                # 尝试从 JSON 备份恢复
                json_p = self._json_ledger_path(target_date)
                if json_p.exists():
                    try:
                        with open(json_p, "r", encoding="utf-8") as f:
                            entries = json.load(f)
                    except Exception:
                        pass

            return entries

    def record_start(self, step_id: str, now: datetime = None) -> None:
        """记录步骤开始执行"""
        if now is None:
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
        today_str = now.date().isoformat()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        with self._lock:
            conn = self._get_db_conn()
            c = conn.cursor()
            c.execute("""
                UPDATE scheduler_execution_ledger
                SET status = ?, executed_at = ?
                WHERE date = ? AND step_id = ?
            """, (StepStatus.RUNNING.value, now_str, today_str, step_id))
            conn.commit()

        logger.info(f"⏱️ 审计账本: 步骤 [{step_id}] 开始执行 @ {now_str}")

    def record_finish(
        self,
        step_id: str,
        status: StepStatus,
        duration_ms: int = 0,
        summary: str = "",
        error_detail: str = "",
        now: datetime = None,
    ) -> None:
        """记录步骤完成（成功/失败/补偿成功）"""
        if now is None:
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
        today_str = now.date().isoformat()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        with self._lock:
            conn = self._get_db_conn()
            c = conn.cursor()
            c.execute("""
                UPDATE scheduler_execution_ledger
                SET status = ?, executed_at = ?, duration_ms = ?,
                    output_summary = ?, error_detail = ?
                WHERE date = ? AND step_id = ?
            """, (
                status.value if isinstance(status, StepStatus) else str(status),
                now_str,
                duration_ms,
                summary,
                error_detail,
                today_str,
                step_id,
            ))
            conn.commit()

        entries = self.get_daily_ledger(today_str)
        self._sync_to_json(today_str, entries)
        logger.info(f"✅ 审计账本: 步骤 [{step_id}] 结算 -> {status.value} (耗时: {duration_ms}ms) | {summary}")

    def record_skip(
        self,
        step_id: str,
        reason: str,
        policy: CatchupPolicy = None,
        now: datetime = None,
    ) -> None:
        """记录步骤被跳过及原因"""
        if now is None:
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
        today_str = now.date().isoformat()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        status = StepStatus.SKIPPED_EXPIRED if policy == CatchupPolicy.CRITICAL_EXPIRE else StepStatus.SKIPPED_CONDITION

        with self._lock:
            conn = self._get_db_conn()
            c = conn.cursor()
            c.execute("""
                UPDATE scheduler_execution_ledger
                SET status = ?, executed_at = ?, skip_reason = ?
                WHERE date = ? AND step_id = ?
            """, (status.value, now_str, reason, today_str, step_id))
            conn.commit()

        entries = self.get_daily_ledger(today_str)
        self._sync_to_json(today_str, entries)
        logger.warning(f"⚠️ 审计账本: 步骤 [{step_id}] 安全跳过 -> {status.value} | 原因: {reason}")

    def audit_missed_steps(self, now: datetime = None) -> Tuple[List[StepDefinition], List[StepDefinition]]:
        """
        断续开机或休眠唤醒审计器：
        检查截止到当前时间，哪些步骤尚未执行。
        返回: (needs_catchup_steps, safe_skipped_steps)
        - needs_catchup_steps: 按策略需要立即补做的步骤（MUST_CATCHUP）
        - safe_skipped_steps: 按策略不可补做、必须安全废弃的步骤（CRITICAL_EXPIRE，会被立即标记为 SKIPPED_EXPIRED）
        """
        if now is None:
            now = datetime.now(ZoneInfo("Asia/Shanghai"))

        today_str = now.date().isoformat()
        current_time = now.time()

        # 确保今日账本已初始化
        self.init_daily_ledger(today_str)
        daily_entries = {r["step_id"]: r for r in self.get_daily_ledger(today_str)}

        needs_catchup: List[StepDefinition] = []
        safe_skipped: List[StepDefinition] = []

        for step in self._steps.values():
            entry = daily_entries.get(step.step_id)
            if not entry:
                continue

            status = entry.get("status", StepStatus.PENDING.value)
            # 如果已经处于终态（已成功、已补偿、已安全跳过），无需再重复处理
            if status in [
                StepStatus.SUCCESS.value,
                StepStatus.CATCHUP_SUCCESS.value,
                StepStatus.SKIPPED_EXPIRED.value,
                StepStatus.SKIPPED_CONDITION.value,
            ]:
                continue

            end_t = time.fromisoformat(step.window_end)
            start_t = time.fromisoformat(step.window_start)

            # 已经超过窗口结束时间
            if current_time > end_t:
                if step.policy == CatchupPolicy.MUST_CATCHUP:
                    needs_catchup.append(step)
                elif step.policy == CatchupPolicy.CRITICAL_EXPIRE:
                    reason = (
                        f"本地机器在有效窗口 ({step.window_start}-{step.window_end}) 内未运行或休眠；"
                        f"当前时间 {now.strftime('%H:%M:%S')} 已超时。为资金安全主动安全放弃，严禁盘中追高补单。"
                    )
                    self.record_skip(step.step_id, reason, CatchupPolicy.CRITICAL_EXPIRE, now)
                    safe_skipped.append(step)
                elif step.policy == CatchupPolicy.DEGRADE_ADAPT:
                    needs_catchup.append(step)

        return needs_catchup, safe_skipped

    def _sync_to_json(self, target_date: str, entries: List[Dict]):
        """同步写入 JSON 本地镜像文件，方便无数据库直接排查"""
        try:
            json_p = self._json_ledger_path(target_date)
            with open(json_p, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"同步 JSON 审计账本镜像失败: {e}")


# 全局单例
scheduler_ledger = SchedulerLedger()
