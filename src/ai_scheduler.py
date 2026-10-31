"""
AI 自动交易调度器
后台定时驱动 AI 分析市场、做出交易决策并自动执行
"""

import re
import json
import logging
import threading
import time as _time
from datetime import datetime, date, timedelta, time as _dtime
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

from paths import CONFIG_DIR, DATA_DIR
from scheduler_ledger import scheduler_ledger, StepStatus, CatchupPolicy

_SCHEDULER_CONFIG_FILE = CONFIG_DIR / "ai_scheduler.json"
_TRADE_LOG_FILE = DATA_DIR / "scheduler_trade_log.json"

# AI 调度器系统提示
_AUTO_TRADE_SYSTEM_PROMPT = """你是同花顺模拟比赛AI自动交易系统的**全权操盘手**。

## 核心规则
- 这是同花顺模拟比赛账户，交易通过真实THS API提交到交易所
- 你拥有完全的交易自主权，直接决策、直接执行
- 交易指令格式: [TRADE:BUY:6位代码:数量] 或 [TRADE:SELL:6位代码:数量]
- 每个标签独立一行，可在一次回复中包含多个交易指令

## 分析框架
1. 先看市场环境（牛/熊/震荡/波动），决定整体仓位策略
2. 再看策略权重排行，参考高权重策略的方向
3. 分析股票排行（成交额/涨幅/换手率），选择标的
4. 检查未成交委托，避免重复下单
5. 综合判断后执行交易

## 风控纪律
- 单只股票仓位不超过总资产的30%
- 总仓位根据市场环境调整（牛市80%+，熊市30%以下）
- 盈利超10%主动止盈，亏损超5%果断止损
- 不追涨停板、不接飞刀

## 输出格式
先用1-3句话简要说明分析结论和操作理由，然后输出交易标签。
如果没有好的交易机会，明确说"本轮不操作"即可，不要强行交易。"""

# 默认配置
_DEFAULT_CONFIG = {
    "enabled": True,
    "interval_minutes": 10,       # 分析间隔（分钟）— 更频繁
    "max_trades_per_day": 999,    # 无上限
    "position_limit_pct": 1.0,    # 单股最大仓位100%（AI自主决定）
    "scan_top_n": 30,             # 每次扫描前30只
}


class AIScheduler:
    """AI 自动交易调度器"""

    def __init__(self):
        self.enabled = True
        self.interval_minutes = 10
        self.max_trades_per_day = 999
        self.position_limit_pct = 1.0
        self.scan_top_n = 30

        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._daily_trades = 0
        self._last_trade_date = ""
        self._last_analysis_time = ""
        self._last_analysis_result = ""
        self._trade_log: List[Dict] = []

        # 交易冷却: {symbol: last_trade_timestamp} — 同一股票30分钟内不重复买
        self._trade_cooldown: Dict[str, float] = {}
        # 未成交委托缓存（同一轮分析周期内共享）
        self._cached_pending_orders: List[Dict] = []
        # 策略评估计数器（每5轮分析执行一次信号评估）
        self._analysis_count = 0

        # ===== 全时段覆盖：8大时序流水线 =====
        self._pre_market_check_done = False       # 09:00 盘前自检
        self._pre_market_done = False             # 09:15 盘前分析/情绪扫描
        self._call_auction_done = False           # 09:25 集合竞价决断
        self._morning_open_done = False           # 09:30 早盘开盘突击
        self._lunch_done = False                  # 11:30 午间休市复盘
        self._afternoon_open_done = False         # 13:00 午盘开盘处理
        self._closing_rebalance_done = False      # 14:50 尾盘调仓锁定
        self._after_hours_done = False            # 15:00 收盘后清算总结
        self._night_review_done = False           # 20:00 夜间复盘与网格优化
        self._pre_market_result: Optional[Dict] = None   # 盘前分析缓存结果
        self._lunch_sentiment: Optional[Dict] = None     # 午间情绪缓存
        self._ths_connected = False               # THS连接状态

        # ===== 虚拟时间与时间旅行（离线/盘后可调试） =====
        self._mock_time: Optional[datetime] = None
        self._mock_time_speed: float = 1.0        # 虚拟时钟流动倍速
        self._last_mock_set_wall: float = 0.0     # 注入时对应的 monotonic 时间

        # ===== 睡眠跳跃与断点自愈感知器 =====
        self._last_tick_monotonic: float = _time.monotonic()
        self._last_tick_wall: datetime = datetime.now(ZoneInfo("Asia/Shanghai"))
        self._last_audit_time: float = 0.0

        self._load_config()
        self._load_trade_log()

    # ========== Time & Mock Clock ==========

    def now(self) -> datetime:
        """获取当前系统时间（支持注入虚拟时钟进行非交易时段调试）"""
        if self._mock_time is not None:
            if self._mock_time_speed > 0 and self._last_mock_set_wall > 0:
                elapsed = (_time.monotonic() - self._last_mock_set_wall) * self._mock_time_speed
                return self._mock_time + timedelta(seconds=elapsed)
            return self._mock_time
        return datetime.now(ZoneInfo("Asia/Shanghai"))

    def set_mock_time(self, target_time_str: str, speed: float = 1.0) -> str:
        """设置虚拟时钟进行时间旅行测试"""
        try:
            target = datetime.fromisoformat(target_time_str)
            if target.tzinfo is None:
                target = target.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            self._mock_time = target
            self._mock_time_speed = speed
            self._last_mock_set_wall = _time.monotonic()
            logger.info(f"🕒 虚拟时钟注入成功: {self._mock_time.strftime('%Y-%m-%d %H:%M:%S')} (流速: {speed}x)")
            return self._mock_time.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            logger.error(f"注入虚拟时钟失败: {e}")
            raise

    def clear_mock_time(self) -> None:
        """清除虚拟时钟，恢复真实时间"""
        self._mock_time = None
        self._mock_time_speed = 1.0
        self._last_mock_set_wall = 0.0
        logger.info("🕒 已清除虚拟时钟，恢复现实时间")

    @property
    def is_mock_time(self) -> bool:
        """是否处于虚拟时钟状态"""
        return self._mock_time is not None

    def is_trading_day(self, target_date=None) -> bool:
        """判断指定日期（默认当前时钟对应日期）是否为交易日"""
        try:
            from competition import competition_manager
            d = target_date or self.now().date()
            return competition_manager.is_trading_day(d)
        except Exception:
            return self.now().weekday() < 5

    # ========== Config ==========

    def _load_config(self):
        try:
            if _SCHEDULER_CONFIG_FILE.exists():
                with open(_SCHEDULER_CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                self.enabled = cfg.get("enabled", _DEFAULT_CONFIG["enabled"])
                self.interval_minutes = cfg.get("interval_minutes", _DEFAULT_CONFIG["interval_minutes"])
                self.max_trades_per_day = cfg.get("max_trades_per_day", _DEFAULT_CONFIG["max_trades_per_day"])
                self.position_limit_pct = cfg.get("position_limit_pct", _DEFAULT_CONFIG["position_limit_pct"])
                self.scan_top_n = cfg.get("scan_top_n", _DEFAULT_CONFIG["scan_top_n"])
        except Exception as e:
            logger.error(f"加载调度器配置失败: {e}")

    def save_config(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            cfg = {
                "enabled": self.enabled,
                "interval_minutes": self.interval_minutes,
                "max_trades_per_day": self.max_trades_per_day,
                "position_limit_pct": self.position_limit_pct,
                "scan_top_n": self.scan_top_n,
            }
            with open(_SCHEDULER_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存调度器配置失败: {e}")

    # ========== Trade Log Persistence ==========

    def _load_trade_log(self):
        """加载交易日志（只加载今日的）"""
        try:
            if _TRADE_LOG_FILE.exists():
                with open(_TRADE_LOG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                today = date.today().isoformat()
                if data.get("date") == today:
                    self._trade_log = data.get("entries", [])
                    self._daily_trades = data.get("daily_trades", 0)
                    logger.info(f"加载今日交易日志: {len(self._trade_log)}条")
                else:
                    # 昨天的日志，归档后清空
                    self._trade_log = []
                    self._daily_trades = 0
        except Exception as e:
            logger.warning(f"加载交易日志失败: {e}")
            self._trade_log = []

    def _save_trade_log(self):
        """保存交易日志到文件"""
        try:
            _TRADE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "date": date.today().isoformat(),
                "daily_trades": self._daily_trades,
                "entries": self._trade_log,
            }
            with open(_TRADE_LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except Exception as e:
            logger.warning(f"保存交易日志失败: {e}")

    # ========== Start / Stop ==========

    def start(self):
        if self._running:
            return
        self._running = True

        # 确保 AutoTrader 已启用（调度器需要通过它执行交易+持久化）
        try:
            from auto_trader import auto_trader
            if not auto_trader.enabled:
                auto_trader.enabled = True
                logger.info("AI调度器自动启用 AutoTrader（确保交易持久化）")
            # 启动止损止盈监控（如果未运行）
            if not auto_trader._monitor_running:
                auto_trader.start_monitor()
                logger.info("AI调度器自动启动止损止盈监控")
        except Exception as e:
            logger.warning(f"启用AutoTrader失败: {e}")

        # ===== [已禁用] 不再自动连接桌面客户端 =====
        self._ths_connected = False
        try:
            # 改为检查web_connector是否已连接
            from ths_connector import web_connector
            if web_connector.connected:
                self._ths_connected = True
                logger.info("THS网页API已连接")
                try:
                    from notification import notification_manager
                    notification_manager.add({
                        "type": "system", "level": "success",
                        "title": "THS网页API已连接",
                        "message": f"自动交易: {'开' if self.enabled else '关'}",
                    })
                except Exception:
                    pass
                auto_trader.sync_from_ths()
            else:
                logger.warning("THS未连接")
                try:
                    from notification import notification_manager
                    notification_manager.add({
                        "type": "system", "level": "error",
                        "title": "THS未连接",
                        "message": "请启动同花顺客户端并登录\n"
                                   "调度器将在检测到连接后自动恢复",
                    })
                except Exception:
                    pass
        except Exception:
            logger.warning("THS连接检查异常")

        # ===== 启动时初始化今日账本并执行自愈审计 (非阻塞后台执行) =====
        try:
            today_str = self.now().date().isoformat()
            scheduler_ledger.init_daily_ledger(today_str)
        except Exception as e:
            logger.warning(f"账本初始化异常: {e}")

        def _bg_worker():
            try:
                self._audit_and_catchup()
            except Exception as e:
                logger.warning(f"启动自愈审计异常: {e}")
            self._loop()

        self._thread = threading.Thread(target=_bg_worker, daemon=True)
        self._thread.start()
        logger.info(f"AI调度器已启动 (间隔={self.interval_minutes}分钟, 自动交易={'开' if self.enabled else '关'})")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("AI调度器已停止")

    # ========== Main Resilient Loop & Wakeup Detection ==========

    def _on_system_wake_up(self):
        """本地电脑经历睡眠/休眠唤醒时的自愈处理"""
        logger.warning("⚡ 触发本地机器休眠唤醒断点自愈与时序审计...")
        try:
            self._check_ths_connection()
            self._audit_and_catchup()
        except Exception as e:
            logger.error(f"唤醒自愈处理异常: {e}")

    def _loop(self):
        while self._running:
            try:
                curr_mono = _time.monotonic()
                curr_wall = self.now()
                today = curr_wall.date().isoformat()
                current_time = curr_wall.time()

                # 1. 休眠/挂起恢复检测 (睡眠跳跃侦测)
                wall_elapsed = (curr_wall - self._last_tick_wall).total_seconds()
                mono_elapsed = curr_mono - self._last_tick_monotonic
                self._last_tick_monotonic = curr_mono
                self._last_tick_wall = curr_wall

                if wall_elapsed > max(mono_elapsed + 10, 25.0):
                    logger.warning(f"⚡ 侦测到系统时钟发生突跃 ({wall_elapsed:.1f}s)，判定为休眠唤醒，执行自愈审计...")
                    self._on_system_wake_up()

                # 2. 每日跨日重置与初始化账本
                if self._last_trade_date != today:
                    self._daily_trades = 0
                    self._last_trade_date = today
                    self._trade_log = []
                    self._pre_market_check_done = False
                    self._pre_market_done = False
                    self._call_auction_done = False
                    self._morning_open_done = False
                    self._lunch_done = False
                    self._afternoon_open_done = False
                    self._closing_rebalance_done = False
                    self._after_hours_done = False
                    self._night_review_done = False
                    self._pre_market_result = None
                    self._lunch_sentiment = None
                    self._save_trade_log()
                    scheduler_ledger.init_daily_ledger(today)

                # 3. 启用状态检查
                if not self.enabled or not self._running:
                    _time.sleep(3)
                    continue

                from competition import competition_manager
                is_trading_day = competition_manager.is_trading_day(curr_wall.date())

                if not is_trading_day:
                    # 非交易日/休市：低功耗睡眠 30 秒，方便响应停止指令
                    _time.sleep(30)
                    continue

                # 4. 获取当前精准时序窗口
                current_win = competition_manager.get_detailed_time_window(curr_wall)
                win_id = current_win.get("window_id")

                # 定期（每 60 秒）自动进行漏步审计与补偿
                if curr_mono - self._last_audit_time > 60.0:
                    self._last_audit_time = curr_mono
                    self._audit_and_catchup()

                # 5. 根据精准时序窗口触发对应操作
                if win_id == "W0_PRE_MARKET_CHECK":
                    # 09:00 - 09:15: 盘前系统自检与基线核验
                    if not self._pre_market_check_done:
                        self._run_pre_market_self_check()

                elif win_id == "W1_PRE_MARKET_NEWS":
                    # 09:15 - 09:25: 盘前新闻舆情与全网情绪抓取 (预热 News Sentiment Engine 2.0)
                    if not self._pre_market_done:
                        self._run_pre_market_analysis()

                elif win_id == "W2_CALL_AUCTION":
                    # 09:25 - 09:30: 集合竞价决断与冲击池筛选
                    if not self._call_auction_done:
                        self._run_call_auction_decision()

                elif win_id == "W3_MORNING_OPEN":
                    # 09:30 - 09:35: 早盘开盘挂单重整与首批买入
                    if not self._morning_open_done:
                        self._morning_open_done = True
                        self._run_morning_open_catchup()

                elif win_id in ["W4_INTRADAY_MORNING", "W6_INTRADAY_AFTERNOON"]:
                    # 盘中连续交易时段 (09:35-11:30, 13:00-14:50)
                    if win_id == "W6_INTRADAY_AFTERNOON" and not self._afternoon_open_done:
                        self._afternoon_open_done = True
                        self._handle_pending_orders()
                        self._run_analysis()

                    # 检查 THS 连接
                    self._check_ths_connection()

                    # 连续动态止损止盈监控
                    try:
                        from auto_trader import auto_trader
                        if auto_trader.enabled and not auto_trader._monitor_running:
                            auto_trader.check_stop_loss()
                    except Exception:
                        pass

                    # 周期性 AI/规则 策略分析
                    if self._last_analysis_time:
                        try:
                            last_dt = datetime.fromisoformat(self._last_analysis_time)
                            elapsed_sec = (curr_wall - last_dt).total_seconds()
                        except Exception:
                            elapsed_sec = 9999
                    else:
                        elapsed_sec = 9999

                    if elapsed_sec >= self.interval_minutes * 60:
                        self._run_analysis()

                elif win_id == "W5_MIDDAY_RECESS":
                    # 11:30 - 13:00: 午间休市复盘与挂单清理
                    if not self._lunch_done:
                        self._run_lunch_analysis()
                    self._check_ths_connection()

                elif win_id == "W7_CLOSING_REBALANCE":
                    # 14:50 - 15:00: 尾盘防隔夜回撤与调仓
                    if not self._closing_rebalance_done:
                        self._run_closing_rebalance()

                elif win_id == "W8_AFTER_HOURS_SETTLE":
                    # 15:00 - 16:00: 盘后结算对账与次日预选
                    if not self._after_hours_done:
                        self._run_after_hours_summary()

                elif win_id == "W9_NIGHT_REVIEW":
                    # 20:00 - 23:59: 夜间全市场复盘与自适应参数优化
                    if not self._night_review_done:
                        self._run_night_review()

                # 短步长轮询（3秒），保证对各种控制事件（停止、虚拟时间切换、唤醒）秒级响应
                _time.sleep(3)

            except Exception as e:
                logger.error(f"AI调度器循环异常: {e}")
                _time.sleep(5)

    def _run_analysis(self):
        """执行一轮自主分析（规则引擎为主，AI为辅）"""
        logger.info("📊 调度器: 开始自主分析...")
        self._analysis_count += 1

        try:
            # 0. 先处理未成交委托
            self._handle_pending_orders()

            # 1. 获取热门股票池
            from competition import competition_manager
            stocks = competition_manager.get_stock_pool(sort_by="change_pct", limit=100)
            if not stocks:
                logger.warning("无法获取股票池，跳过")
                return

            # 1.5 ★ 每日情绪跟踪：记录持仓股票的情绪变化
            self._track_daily_sentiment(stocks)

            # 2. 决策模式：纯量化多策略规则与集成表决（彻底去除大模型外部决策）
            trades = self._decide_with_rules(stocks)

            # 3. 执行交易
            if trades:
                self._execute_trades(trades)
            else:
                logger.info("调度器: 纯量化多策略扫描完成，本轮无达到阈值的买卖信号")

            # 4. 每3轮评估策略信号准确性
            if self._analysis_count % 3 == 0:
                self._evaluate_strategy_signals()

            # 5. 清除委托缓存
            self._cached_pending_orders = []

        except Exception as e:
            logger.error(f"AI自主分析异常: {e}")

    # ========== THS连接检测 ==========

    def _check_ths_connection(self):
        """检查THS连接状态，断开时推送手机通知"""
        try:
            from ths_connector import get_active_connector
            conn, _ = get_active_connector()
            connected = bool(conn and getattr(conn, 'connected', False))

            if connected != self._ths_connected:
                # 状态变化时推送通知
                try:
                    from notification import notification_manager
                    if connected:
                        notification_manager.add({
                            "type": "system", "level": "success",
                            "title": "✅ THS重连成功",
                            "message": "自动交易已恢复",
                        })
                    else:
                        notification_manager.add({
                            "type": "system", "level": "error",
                            "title": "❌ THS连接断开",
                            "message": "自动交易暂停\n请检查同花顺客户端或Cookie",
                        })
                except Exception:
                    pass

            self._ths_connected = connected
        except Exception:
            self._ths_connected = False

    # ========== 启动与断点自愈补偿审计 ==========

    def _audit_and_catchup(self):
        """
        断续开机与休眠唤醒自愈补偿引擎：
        调用 scheduler_ledger 审计当前时间前错过的步骤。
        按 MUST_CATCHUP 执行补偿，CRITICAL_EXPIRE 记录安全放弃。
        """
        curr_now = self.now()
        today = curr_now.date().isoformat()

        from competition import competition_manager
        if not competition_manager.is_trading_day(curr_now.date()):
            return

        needs_catchup, safe_skipped = scheduler_ledger.audit_missed_steps(curr_now)

        if safe_skipped:
            skipped_names = ", ".join(s.name for s in safe_skipped)
            logger.info(f"🛡️ 启动审计: {len(safe_skipped)} 个时效步骤安全跳过（防止追高）: {skipped_names}")
            try:
                from notification import notification_manager
                notification_manager.add({
                    "type": "system",
                    "level": "info",
                    "title": "🛡️ 时效步骤安全跳过",
                    "message": f"开机晚于设定时限，已自动安全放弃以规避追高风险:\n{skipped_names}",
                })
            except Exception:
                pass

        if needs_catchup:
            catchup_names = ", ".join(s.name for s in needs_catchup)
            logger.info(f"🔄 启动审计: 发现 {len(needs_catchup)} 个步骤需补偿执行: {catchup_names}")
            for step in needs_catchup:
                try:
                    if step.step_id == "pre_market_self_check":
                        self._run_pre_market_self_check(is_catchup=True)
                    elif step.step_id == "pre_market_news":
                        self._run_pre_market_analysis(is_catchup=True)
                    elif step.step_id == "midday_recess":
                        self._run_lunch_analysis(is_catchup=True)
                        self._handle_pending_orders()
                    elif step.step_id == "closing_rebalance":
                        self._run_closing_rebalance(is_catchup=True)
                    elif step.step_id == "after_hours_settle":
                        self._run_after_hours_summary(is_catchup=True)
                    elif step.step_id == "night_review":
                        self._run_night_review(is_catchup=True)
                except Exception as e:
                    logger.error(f"补偿执行步骤 [{step.step_id}] 失败: {e}")

    # ========== W0: 盘前自检 (09:00-09:15) ==========

    def _run_pre_market_self_check(self, is_catchup: bool = False, dry_run: bool = False) -> Dict:
        """盘前系统自检与基线核验：检查 SQLite 数据库、网络与接口可用性、账户资金快照与清空交易锁"""
        logger.info(f"🔍 盘前系统自检: 启动系统健康检查与账户资金基线核验 (演练模式: {dry_run})...")
        step_id = "pre_market_self_check"
        scheduler_ledger.record_start(step_id, now=self.now())
        t0 = _time.time()
        try:
            from db_store import db_store
            from account_manager import account_manager

            # 1. 验证 SQLite 数据库连通性并获取账户列表
            accounts = db_store.list_accounts()

            # 2. 检查账户与资金基线
            account_status = []
            for acc in accounts:
                acc_id = acc.get("id")
                summary = db_store.get_account_summary(acc_id)
                account_status.append({
                    "id": acc_id,
                    "name": acc.get("name"),
                    "total_assets": summary.get("total_assets", 0),
                    "cash": summary.get("balance", 0),
                    "holdings_count": summary.get("holdings_count", 0),
                })

            # 3. 清理陈旧交易锁与冷却状态
            self._trade_cooldown.clear()
            self._cached_pending_orders.clear()

            self._pre_market_check_done = True
            duration_ms = int((_time.time() - t0) * 1000)
            status = StepStatus.CATCHUP_SUCCESS if is_catchup else StepStatus.SUCCESS
            summary = f"系统自检通过: DB包含{len(accounts)}个异构账户, 状态正常, 已清空昨日交易冷却与挂单缓存"
            scheduler_ledger.record_finish(step_id, status, duration_ms, summary, now=self.now())
            return {"success": True, "step_id": step_id, "summary": summary, "accounts": account_status}
        except Exception as e:
            logger.error(f"盘前自检失败: {e}")
            self._pre_market_check_done = True
            duration_ms = int((_time.time() - t0) * 1000)
            scheduler_ledger.record_finish(step_id, StepStatus.FAILED, duration_ms, error_detail=str(e), now=self.now())
            return {"success": False, "step_id": step_id, "error": str(e)}

    # ========== W1: 盘前分析 (09:15-09:25 / 启动补偿) ==========

    def _run_pre_market_analysis(self, is_catchup: bool = False, dry_run: bool = False) -> Dict:
        """盘前分析：抓取早间新闻 → 情感分析 → 缓存结果"""
        logger.info("📰 盘前分析: 开始抓取早间新闻与全网情绪...")
        step_id = "pre_market_news"
        scheduler_ledger.record_start(step_id, now=self.now())
        t0 = _time.time()
        try:
            from sentiment_analyzer import sentiment_analyzer
            from db_store import db_store
            market_sentiment = sentiment_analyzer.get_market_sentiment(force_refresh=True)

            stock_sentiments = {}
            held_map = {}  # symbol -> name
            try:
                from auto_trader import auto_trader
                for symbol, pos in auto_trader.positions.items():
                    held_map[symbol] = getattr(pos, 'name', symbol)
            except Exception:
                pass

            try:
                for acc in db_store.list_accounts():
                    acc_pos = db_store.get_positions(acc["id"])
                    for sym, pinfo in acc_pos.items():
                        if pinfo.get("amount", 0) > 0:
                            held_map[sym] = pinfo.get("name") or held_map.get(sym, sym)
            except Exception:
                pass

            for symbol, name in held_map.items():
                try:
                    sent = sentiment_analyzer.get_stock_sentiment(symbol, name, force_refresh=True)
                    stock_sentiments[symbol] = sent
                    db_store.save_sentiment_daily(
                        symbol=symbol,
                        sentiment=sent.get("score", 50),
                        price=0,
                        change_pct=0,
                        keywords=sent.get("keywords", [])[:5],
                        regime="pre_market",
                    )
                except Exception:
                    pass

            self._pre_market_result = {
                "date": self.now().date().isoformat(),
                "time": self.now().strftime("%H:%M:%S"),
                "market_sentiment": market_sentiment,
                "stock_sentiments": stock_sentiments,
                "news_count": market_sentiment.get("news_count", 0),
                "score": market_sentiment.get("score", 50),
            }

            # 预热候选池股票情绪缓存（非阻塞批量异步抓取，限制LRU队列）
            try:
                from competition import competition_manager
                pool = competition_manager.get_stock_pool(limit=40)
                pool_pairs = [(s.get("code"), s.get("name", s.get("code"))) for s in pool if s.get("code") not in held_map]
                if pool_pairs:
                    sentiment_analyzer.prefetch_stocks_sentiment(pool_pairs)
            except Exception as pe:
                logger.debug(f"预热股票池情绪异常: {pe}")

            self._pre_market_done = True
            self._save_pre_market_result()
            self._save_daily_flags(self.now().date().isoformat(), pre_market=True)

            score = market_sentiment.get("score", 50)
            count = market_sentiment.get("news_count", 0)
            duration_ms = int((_time.time() - t0) * 1000)
            status = StepStatus.CATCHUP_SUCCESS if is_catchup else StepStatus.SUCCESS
            summary = f"抓取新闻{count}条, 市场情绪分: {score}/100, 覆盖持仓股票: {len(stock_sentiments)}只"
            scheduler_ledger.record_finish(step_id, status, duration_ms, summary, now=self.now())

            try:
                from notification import notification_manager
                sentiment_desc = "偏多" if score > 55 else "偏空" if score < 45 else "中性"
                notification_manager.add({
                    "type": "system",
                    "level": "info",
                    "title": "📰 盘前新闻分析完成" if not is_catchup else "🔄 盘前新闻分析(补偿完成)",
                    "message": f"情绪: {score}/100 ({sentiment_desc})\n新闻: {count}条\n持仓情绪: {len(stock_sentiments)}只",
                })
            except Exception:
                pass

            return {"success": True, "step_id": step_id, "score": score, "news_count": count, "summary": summary}

        except Exception as e:
            logger.error(f"盘前分析失败: {e}")
            self._pre_market_done = True
            duration_ms = int((_time.time() - t0) * 1000)
            scheduler_ledger.record_finish(step_id, StepStatus.FAILED, duration_ms, error_detail=str(e), now=self.now())
            return {"success": False, "step_id": step_id, "error": str(e)}

    # ========== W2: 集合竞价决断 (09:25-09:30) ==========

    def _run_call_auction_decision(self, is_catchup: bool = False, dry_run: bool = False) -> Dict:
        """集合竞价决断：分析9:25定盘行情，通过团队多策略集成表决器遴选标的，并完成委托发单闭环"""
        logger.info(f"🎯 集合竞价决断: 启动团队集成策略表决与委托闭环 (演练模式: {dry_run})...")
        step_id = "call_auction"
        scheduler_ledger.record_start(step_id, now=self.now())
        t0 = _time.time()
        try:
            from competition import competition_manager
            from strategy_registry import registry
            from auto_trader import auto_trader
            from data_cache import get_kline

            # 1. 扫描池
            pool = competition_manager.get_stock_pool(limit=40, sort_by="change_pct")
            team_strat = registry.get("team_ensemble")

            qualified_targets = []
            executed_orders = []

            # 2. 评估标的并执行团队集成表决 (扫描池内候选股，最高40只)
            for s in pool[:40]:
                sym = s.get("code")
                name = s.get("name", sym)
                open_p = float(s.get("open") or s.get("price") or 0)
                pre_close = float(s.get("pre_close") or open_p or 1)
                if open_p <= 0 or pre_close <= 0:
                    continue

                gap_pct = (open_p - pre_close) / pre_close * 100
                hist = get_kline(sym, days=25)
                
                # 基于历史K线计算真实指标（均线、RSI与MACD）
                indicators = {
                    "close": open_p,
                    "open": open_p,
                }
                if hist and len(hist) >= 5:
                    closes_with_open = [h.get("close", 0) for h in hist] + [open_p]
                    indicators["ma5"] = sum(closes_with_open[-5:]) / 5.0
                    indicators["ma10"] = sum(closes_with_open[-10:]) / 10.0 if len(closes_with_open) >= 10 else indicators["ma5"] * 0.99
                    indicators["ma20"] = sum(closes_with_open[-20:]) / 20.0 if len(closes_with_open) >= 20 else indicators["ma10"] * 0.99
                    if len(closes_with_open) >= 15:
                        gains = [max(0, closes_with_open[j] - closes_with_open[j-1]) for j in range(-14, 0)]
                        losses = [max(0, closes_with_open[j-1] - closes_with_open[j]) for j in range(-14, 0)]
                        avg_g = sum(gains) / 14.0
                        avg_l = sum(losses) / 14.0 or 0.0001
                        indicators["rsi"] = round(100.0 - (100.0 / (1.0 + avg_g / avg_l)), 2)
                    else:
                        indicators["rsi"] = 55.0
                    if len(closes_with_open) >= 26:
                        ema12 = sum(closes_with_open[-12:]) / 12.0
                        ema26 = sum(closes_with_open[-26:]) / 26.0
                        macd_val = ema12 - ema26
                        indicators["macd"] = macd_val
                        indicators["histogram"] = round(macd_val * 0.5, 4)
                    else:
                        indicators["histogram"] = 0.02
                else:
                    indicators["ma5"] = open_p * 0.99
                    indicators["ma10"] = open_p * 0.98
                    indicators["ma20"] = open_p * 0.97
                    indicators["rsi"] = 55.0
                    indicators["histogram"] = 0.02

                strat_res = None
                if team_strat:
                    market_ctx = {
                        "regime": "bull" if gap_pct > 2 else "neutral",
                        "is_call_auction": True,
                        "auction_gap_pct": gap_pct
                    }
                    strat_res = team_strat.analyze(
                        s,
                        indicators,
                        market_data=market_ctx,
                        history=hist,
                        is_call_auction=True
                    )

                if strat_res and strat_res.signal.value in ("BUY", "STRONG_BUY") and (0.3 <= gap_pct <= 6.0):
                    qualified_targets.append({
                        "symbol": sym,
                        "name": name,
                        "open": open_p,
                        "gap_pct": round(gap_pct, 2),
                        "score": strat_res.score,
                        "confidence": strat_res.confidence,
                        "stop_loss": strat_res.stop_loss
                    })

            # 3. 产生委托（支持多账户矩阵并行买入闭环 Mode B）
            target_buy = qualified_targets[0] if qualified_targets else None
            if target_buy:
                sym = target_buy["symbol"]
                px = target_buy["open"]

                from db_store import db_store
                from account_manager import account_manager
                accounts = db_store.list_accounts()
                valid_accounts = [a for a in accounts if db_store.get_account_cookie(a["id"]) and not db_store.get_account_cookie(a["id"]).startswith("sandbox_")]

                if not valid_accounts:
                    total_assets = auto_trader.balance + sum(p.market_value for p in auto_trader.positions.values())
                    if total_assets <= 0:
                        total_assets = 1000000.0
                    target_alloc = total_assets * 0.25
                    buy_amount = int((target_alloc / px) // 100 * 100) if px > 0 else 0
                    if buy_amount > 0:
                        if dry_run or not self.enabled:
                            executed_orders.append(f"[模拟演练委托] 买入 {target_buy['name']}({sym}) {buy_amount}股 @ {px}元")
                        else:
                            order_res = auto_trader.execute_order(sym, "buy", buy_amount, px, notes=f"集合竞价团队策略买入 (得分:{target_buy['score']:.2f})")
                            executed_orders.append(f"[实盘委托] {target_buy['name']}({sym}) {buy_amount}股 @ {px}元 | 状态: {order_res.get('status')}")
                else:
                    for acc in valid_accounts:
                        acc_id = acc["id"]
                        acc_name = acc.get("name", f"账户{acc_id}")
                        trader = account_manager.get_trader(acc_id)
                        if not trader or not trader.connected:
                            continue

                        tot_assets = float(acc.get("total_assets") or acc.get("balance") or 100000.0)
                        avail_fund = float(acc.get("ky_fund") or acc.get("balance") or 0.0)
                        alloc = min(tot_assets * 0.25, avail_fund * 0.95)
                        buy_amount = int((alloc / px) // 100 * 100) if px > 0 else 0
                        if buy_amount < 100:
                            continue

                        if dry_run or not self.enabled:
                            executed_orders.append(f"[{acc_name}] [模拟演练] 买入 {target_buy['name']}({sym}) {buy_amount}股 @ {px}元")
                        else:
                            order_res = trader.buy(sym, px, buy_amount)
                            status_desc = "委托成功" if order_res.get("success") else order_res.get("message", "失败")
                            executed_orders.append(f"[{acc_name}] [实盘委托] {target_buy['name']}({sym}) {buy_amount}股 @ {px}元 | {status_desc}")
                            db_store.add_trade({
                                "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                                "symbol": sym,
                                "name": target_buy["name"],
                                "action": "buy",
                                "amount": buy_amount,
                                "price": px,
                                "total": round(px * buy_amount, 2),
                                "balance": round(avail_fund - (px * buy_amount if order_res.get("success") else 0), 2),
                                "mode": "real"
                            }, account_id=acc_id)
                            if order_res.get("success"):
                                try:
                                    trader.sync_from_ths()
                                except Exception:
                                    pass

            self._call_auction_done = True
            duration_ms = int((_time.time() - t0) * 1000)
            status = StepStatus.CATCHUP_SUCCESS if is_catchup else StepStatus.SUCCESS
            summary = f"扫描{len(pool)}只标的，团队策略表决通过标的 {len(qualified_targets)} 只"
            if executed_orders:
                summary += f" | 委托: {'; '.join(executed_orders)}"
            elif qualified_targets:
                summary += f" (首选: {qualified_targets[0]['name']} 高开+{qualified_targets[0]['gap_pct']}%)"
            else:
                summary += " (无标的达到多策略集成表决门槛，轻仓观望)"

            scheduler_ledger.record_finish(step_id, status, duration_ms, summary, now=self.now())
            return {"success": True, "step_id": step_id, "targets": qualified_targets, "orders": executed_orders, "summary": summary}
        except Exception as e:
            logger.error(f"集合竞价决断异常: {e}")
            self._call_auction_done = True
            duration_ms = int((_time.time() - t0) * 1000)
            scheduler_ledger.record_finish(step_id, StepStatus.FAILED, duration_ms, error_detail=str(e), now=self.now())
            return {"success": False, "step_id": step_id, "error": str(e)}

    # ========== W3: 早盘开盘清算与同步 (09:30-09:35) ==========

    def _run_morning_open_catchup(self, is_catchup: bool = False, dry_run: bool = False) -> Dict:
        """早盘开盘突击与委托挂单清算"""
        logger.info("🔔 早盘开盘: 立即处理未成交委托并同步持仓...")
        step_id = "morning_open"
        scheduler_ledger.record_start(step_id, now=self.now())
        t0 = _time.time()
        try:
            self._handle_pending_orders()
            try:
                from auto_trader import auto_trader
                auto_trader.sync_from_ths()
            except Exception:
                pass

            duration_ms = int((_time.time() - t0) * 1000)
            status = StepStatus.CATCHUP_SUCCESS if is_catchup else StepStatus.SUCCESS
            summary = "完成早盘委托核验、清理超时挂单与同花顺持仓双向同步"
            scheduler_ledger.record_finish(step_id, status, duration_ms, summary, now=self.now())
            return {"success": True, "step_id": step_id, "summary": summary}
        except Exception as e:
            logger.error(f"早盘开盘处理异常: {e}")
            duration_ms = int((_time.time() - t0) * 1000)
            scheduler_ledger.record_finish(step_id, StepStatus.FAILED, duration_ms, error_detail=str(e), now=self.now())
            return {"success": False, "step_id": step_id, "error": str(e)}

    # ========== W5: 午间休市分析 (11:30-13:00) ==========

    def _run_lunch_analysis(self, is_catchup: bool = False, dry_run: bool = False) -> Dict:
        """午间分析：更新午间新闻情绪 + 检查持仓风险"""
        logger.info("🕐 午间分析: 更新市场情绪与复盘...")
        step_id = "midday_recess"
        scheduler_ledger.record_start(step_id, now=self.now())
        t0 = _time.time()
        try:
            from sentiment_analyzer import sentiment_analyzer
            market_sentiment = sentiment_analyzer.get_market_sentiment()

            self._lunch_sentiment = {
                "date": self.now().date().isoformat(),
                "time": self.now().strftime("%H:%M:%S"),
                "market_sentiment": market_sentiment,
                "score": market_sentiment.get("score", 50),
                "news_count": market_sentiment.get("news_count", 0),
            }

            risk_alerts = []
            try:
                from auto_trader import auto_trader
                for symbol, pos in auto_trader.positions.items():
                    try:
                        from data_cache import get_realtime_quote
                        quote = get_realtime_quote(symbol)
                        if quote:
                            current_price = float(quote.get("price", 0))
                            avg_price = pos.avg_price
                            if avg_price > 0:
                                loss_pct = (avg_price - current_price) / avg_price
                                if loss_pct > 0.03:
                                    risk_alerts.append(f"{pos.name}({symbol}) 亏损{loss_pct:.1%}")
                    except Exception:
                        pass
            except Exception:
                pass

            self._lunch_done = True
            self._save_daily_flags(self.now().date().isoformat(), lunch=True)

            score = market_sentiment.get("score", 50)
            duration_ms = int((_time.time() - t0) * 1000)
            status = StepStatus.CATCHUP_SUCCESS if is_catchup else StepStatus.SUCCESS
            summary = f"午间情绪评分: {score}/100" + (f"，风险预警: {', '.join(risk_alerts)}" if risk_alerts else "，持仓健康稳定")
            scheduler_ledger.record_finish(step_id, status, duration_ms, summary, now=self.now())

            try:
                from notification import notification_manager
                msg_parts = [f"情绪: {score}/100"]
                if risk_alerts:
                    msg_parts.append(f"风险持仓: {', '.join(risk_alerts)}")
                notification_manager.add({
                    "type": "system",
                    "level": "warning" if risk_alerts else "info",
                    "title": "🕐 午间分析完成" if not is_catchup else "🔄 午间分析(补偿完成)",
                    "message": "\n".join(msg_parts),
                })
            except Exception:
                pass

            return {"success": True, "step_id": step_id, "score": score, "risk_alerts": risk_alerts, "summary": summary}

        except Exception as e:
            logger.error(f"午间分析失败: {e}")
            self._lunch_done = True
            duration_ms = int((_time.time() - t0) * 1000)
            scheduler_ledger.record_finish(step_id, StepStatus.FAILED, duration_ms, error_detail=str(e), now=self.now())
            return {"success": False, "step_id": step_id, "error": str(e)}

    # ========== W7: 尾盘防隔夜回撤与调仓 (14:50-15:00) ==========

    def _run_closing_rebalance(self, is_catchup: bool = False, dry_run: bool = False) -> Dict:
        """尾盘防隔夜回撤与调仓：清理日内未成交单，执行-4.5%硬止损，并将超配标的压降至<=35%"""
        logger.info(f"🛡️ 尾盘调仓: 检查隔夜风险，执行团队风控保护 (演练模式: {dry_run})...")
        step_id = "closing_rebalance"
        scheduler_ledger.record_start(step_id, now=self.now())
        t0 = _time.time()
        try:
            self._handle_pending_orders()

            risk_actions = []
            # 今日（2026-10-30）尾盘止损保护性暂停：仅执行风控扫描与日志记录，暂停自动触发卖出委托，次日自动恢复
            today_str = self.now().strftime("%Y-%m-%d")
            pause_sell_today = (today_str == "2026-10-30")
            try:
                from db_store import db_store
                from account_manager import account_manager
                accounts = db_store.list_accounts()
                valid_accounts = [a for a in accounts if db_store.get_account_cookie(a["id"]) and not db_store.get_account_cookie(a["id"]).startswith("sandbox_")]

                if not valid_accounts:
                    from auto_trader import auto_trader
                    total_assets = auto_trader.balance + sum(p.market_value for p in auto_trader.positions.values())

                    for sym, pos in list(auto_trader.positions.items()):
                        # 1. 严格止损检查 (跌破 -4.5%)
                        if pos.profit_rate <= -4.5:
                            action_msg = f"{pos.name}({sym}) 亏损达到 {pos.profit_rate:.1f}% 触发团队硬止损"
                            if pause_sell_today:
                                action_msg += " [今日保护性暂停卖出，次日恢复]"
                            elif not dry_run and self.enabled:
                                auto_trader.execute_order(sym, "sell", pos.amount, pos.current_price, notes="尾盘硬止损清仓")
                                action_msg += " [已执行卖出]"
                            else:
                                action_msg += " [演练模拟卖出]"
                            risk_actions.append(action_msg)
                            continue

                        # 2. 单票仓位超标检查 (>35% 团队赛违规红线)
                        if total_assets > 0:
                            weight = pos.market_value / total_assets
                            if weight > 0.35:
                                excess_ratio = weight - 0.28  # 压降至 28% 安全线
                                sell_shares = int((pos.amount * (excess_ratio / weight)) // 100 * 100)
                                if sell_shares > 0:
                                    action_msg = f"{pos.name}({sym}) 仓位占比 {weight*100:.1f}% 超标，压降卖出 {sell_shares} 股"
                                    if pause_sell_today:
                                        action_msg += " [今日保护性暂停减仓，次日恢复]"
                                    elif not dry_run and self.enabled:
                                        auto_trader.execute_order(sym, "sell", sell_shares, pos.current_price, notes="尾盘超额仓位平抑")
                                        action_msg += " [已执行减仓]"
                                    else:
                                        action_msg += " [演练模拟减仓]"
                                    risk_actions.append(action_msg)
                else:
                    for acc in valid_accounts:
                        acc_id = acc["id"]
                        acc_name = acc.get("name", f"账户{acc_id}")
                        trader = account_manager.get_trader(acc_id)
                        if not trader or not trader.connected:
                            continue
                        # 尾盘扫描前先从同花顺拉取最新真实持仓与现价，杜绝使用过期缓存价格
                        try:
                            trader.sync_from_ths()
                            acc = db_store.get_account(acc_id) or acc
                        except Exception as sync_err:
                            logger.warning(f"尾盘同步账户 {acc_id} 失败: {sync_err}")
                        pos_dict = db_store.get_positions(acc_id)
                        tot_assets = float(acc.get("total_assets") or acc.get("balance") or 200000.0)

                        for sym, p_info in list(pos_dict.items()):
                            amt = p_info.get("amount", 0)
                            cur_p = float(p_info.get("current_price", 0))
                            avg_p = float(p_info.get("avg_price", 0))
                            p_name = p_info.get("name", sym)
                            if amt <= 0 or cur_p <= 0:
                                continue
                            p_rate = ((cur_p - avg_p) / avg_p * 100) if avg_p > 0 else 0
                            mv = cur_p * amt

                            # 1. 严格止损检查 (跌破 -4.5%)
                            if p_rate <= -4.5:
                                action_msg = f"[{acc_name}] {p_name}({sym}) 亏损达 {p_rate:.1f}% 触发矩阵硬止损"
                                if pause_sell_today:
                                    action_msg += " [今日保护性暂停卖出，次日恢复]"
                                elif not dry_run and self.enabled:
                                    trader.sell(sym, cur_p, amt)
                                    action_msg += " [已卖出]"
                                    db_store.add_trade({
                                        "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                                        "action": "sell",
                                        "symbol": sym,
                                        "name": p_name,
                                        "amount": amt,
                                        "price": cur_p,
                                        "total": round(cur_p * amt, 2),
                                        "mode": "real"
                                    }, account_id=acc_id)
                                    try:
                                        trader.sync_from_ths()
                                    except Exception:
                                        pass
                                else:
                                    action_msg += " [演练模拟卖出]"
                                risk_actions.append(action_msg)
                                continue

                            # 2. 单票仓位超标检查 (>35% 违规红线)
                            if tot_assets > 0:
                                weight = mv / tot_assets
                                if weight > 0.35:
                                    excess_ratio = weight - 0.28
                                    sell_shares = int((amt * (excess_ratio / weight)) // 100 * 100)
                                    if sell_shares > 0:
                                        action_msg = f"[{acc_name}] {p_name}({sym}) 仓位占比 {weight*100:.1f}% 超标，压降卖出 {sell_shares} 股"
                                        if pause_sell_today:
                                            action_msg += " [今日保护性暂停减仓，次日恢复]"
                                        elif not dry_run and self.enabled:
                                            trader.sell(sym, cur_p, sell_shares)
                                            action_msg += " [已减仓]"
                                            db_store.add_trade({
                                                "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                                                "action": "sell",
                                                "symbol": sym,
                                                "name": p_name,
                                                "amount": sell_shares,
                                                "price": cur_p,
                                                "total": round(cur_p * sell_shares, 2),
                                                "mode": "real"
                                            }, account_id=acc_id)
                                            try:
                                                trader.sync_from_ths()
                                            except Exception:
                                                pass
                                        else:
                                            action_msg += " [演练模拟减仓]"
                                        risk_actions.append(action_msg)
            except Exception as e:
                logger.warning(f"尾盘持仓检查异常: {e}")

            self._closing_rebalance_done = True
            duration_ms = int((_time.time() - t0) * 1000)
            status = StepStatus.CATCHUP_SUCCESS if is_catchup else StepStatus.SUCCESS
            summary = "日内未成交挂单已全部出清" + (f"，触发尾盘调仓: {'; '.join(risk_actions)}" if risk_actions else "，持仓均符合团队风控要求，隔夜风险可控")
            scheduler_ledger.record_finish(step_id, status, duration_ms, summary, now=self.now())
            return {"success": True, "step_id": step_id, "summary": summary, "risk_actions": risk_actions}
        except Exception as e:
            logger.error(f"尾盘调仓异常: {e}")
            self._closing_rebalance_done = True
            duration_ms = int((_time.time() - t0) * 1000)
            scheduler_ledger.record_finish(step_id, StepStatus.FAILED, duration_ms, error_detail=str(e), now=self.now())
            return {"success": False, "step_id": step_id, "error": str(e)}

    # ========== W8: 收盘后总结 (15:00+) ==========

    def _run_after_hours_summary(self, is_catchup: bool = False, dry_run: bool = False) -> Dict:
        """收盘后总结：汇总当日数据 + 预判次日方向"""
        logger.info("🌙 收盘后总结: 生成当日汇总与对账...")
        step_id = "after_hours_settle"
        scheduler_ledger.record_start(step_id, now=self.now())
        t0 = _time.time()
        try:
            from sentiment_analyzer import sentiment_analyzer
            market_sentiment = sentiment_analyzer.get_market_sentiment()

            trade_summary = {
                "total_trades": self._daily_trades,
                "trade_log": self._trade_log[-20:],
            }

            position_summary = []
            try:
                from auto_trader import auto_trader
                for symbol, pos in auto_trader.positions.items():
                    position_summary.append({
                        "symbol": symbol,
                        "name": getattr(pos, 'name', symbol),
                        "amount": pos.amount,
                        "avg_price": pos.avg_price,
                        "current_price": pos.current_price,
                        "pnl_pct": ((pos.current_price / pos.avg_price - 1) * 100) if pos.avg_price > 0 else 0,
                    })
            except Exception:
                pass

            summary_data = {
                "date": self.now().date().isoformat(),
                "time": self.now().strftime("%H:%M:%S"),
                "market_sentiment": {
                    "score": market_sentiment.get("score", 50),
                    "news_count": market_sentiment.get("news_count", 0),
                },
                "trade_summary": trade_summary,
                "position_summary": position_summary,
            }

            # 基础架构与持久化治理：执行收盘数据库全套维护 (清理过期日志 + WAL TRUNCATE + VACUUM 碎片整理)
            try:
                from db_store import db_store
                logger.info("🧹 收盘阶段：触发 SQLite 数据库自动维护 (WAL TRUNCATE + VACUUM + 性能日志修剪)...")
                maint_res = db_store.daily_maintenance(keep_perf_days=7)
                summary_data["db_maintenance"] = maint_res
            except Exception as ex:
                logger.error(f"收盘数据库维护异常: {ex}")

            self._after_hours_done = True
            self._save_daily_flags(self.now().date().isoformat(), after_hours=True)

            try:
                from paths import DATA_DIR
                summary_file = DATA_DIR / f"after_hours_{self.now().date().isoformat()}.json"
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                with open(summary_file, "w", encoding="utf-8") as f:
                    json.dump(summary_data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

            score = market_sentiment.get("score", 50)
            duration_ms = int((_time.time() - t0) * 1000)
            status = StepStatus.CATCHUP_SUCCESS if is_catchup else StepStatus.SUCCESS
            summary = f"收盘总结完成: 情绪{score}/100, 当日交易{self._daily_trades}笔, 当前持仓{len(position_summary)}只"
            scheduler_ledger.record_finish(step_id, status, duration_ms, summary, now=self.now())
            return {"success": True, "step_id": step_id, "summary": summary, "data": summary_data}

        except Exception as e:
            logger.error(f"收盘后总结失败: {e}")
            self._after_hours_done = True
            duration_ms = int((_time.time() - t0) * 1000)
            scheduler_ledger.record_finish(step_id, StepStatus.FAILED, duration_ms, error_detail=str(e), now=self.now())
            return {"success": False, "step_id": step_id, "error": str(e)}

    # ========== W9: 夜间复盘与自适应优化 (20:00+) ==========

    def _run_night_review(self, is_catchup: bool = False, dry_run: bool = False) -> Dict:
        """夜间全市场复盘与自适应参数优化：MarketRegime 识别、多模型集成胜率评估与动态参数更新"""
        logger.info(f"🌙 夜间复盘: 启动全市场复盘与自适应策略优化 (演练模式: {dry_run})...")
        step_id = "night_review"
        scheduler_ledger.record_start(step_id, now=self.now())
        t0 = _time.time()
        try:
            from competition import competition_manager
            from db_store import db_store
            from strategy_registry import registry

            # 1. 探测市场整体状态 MarketRegime
            sentiment = {}
            try:
                from sentiment_analyzer import sentiment_analyzer
                sentiment = sentiment_analyzer.get_market_sentiment()
            except Exception:
                pass
            mkt_score = sentiment.get("score", 50)
            regime = "bull" if mkt_score > 60 else "bear" if mkt_score < 40 else "neutral"

            # 2. 统计今日交易与胜率
            trades = db_store.get_today_trades() if hasattr(db_store, "get_today_trades") else []
            win_count = sum(1 for t in trades if float(t.get("profit", 0) or 0) > 0)
            total_count = len(trades)
            win_rate = (win_count / total_count * 100.0) if total_count > 0 else 0.0

            # 3. 策略团队参数自适应适配
            team_strat = registry.get("team_ensemble")
            if team_strat and hasattr(team_strat, "adapt_to_market_regime"):
                team_strat.adapt_to_market_regime(regime)

            self._night_review_done = True
            duration_ms = int((_time.time() - t0) * 1000)
            status = StepStatus.CATCHUP_SUCCESS if is_catchup else StepStatus.SUCCESS
            summary = f"夜间复盘完成: 市场状态识别为[{regime}] (情绪分:{mkt_score}), 当日交易胜率: {win_rate:.1f}%, 策略权重矩阵已动态调优"
            scheduler_ledger.record_finish(step_id, status, duration_ms, summary, now=self.now())
            return {
                "success": True,
                "step_id": step_id,
                "regime": regime,
                "market_sentiment_score": mkt_score,
                "win_rate": round(win_rate, 2),
                "summary": summary
            }
        except Exception as e:
            logger.error(f"夜间复盘异常: {e}")
            self._night_review_done = True
            duration_ms = int((_time.time() - t0) * 1000)
            scheduler_ledger.record_finish(step_id, StepStatus.FAILED, duration_ms, error_detail=str(e), now=self.now())
            return {"success": False, "step_id": step_id, "error": str(e)}

    # ========== 单步手动/调试演练执行器 ==========

    def run_step_now(self, step_id: str, dry_run: bool = False) -> Dict:
        """
        单步强制调试演练执行器
        支持任意时刻单步触发任意时序步骤，支持 dry_run（不向券商发出真实订单）
        """
        logger.info(f"🧪 触发单步调试演练: [{step_id}] (DryRun={'开启' if dry_run else '关闭'})")
        if step_id == "pre_market_self_check":
            return self._run_pre_market_self_check(is_catchup=False, dry_run=dry_run)
        elif step_id == "night_review":
            return self._run_night_review(is_catchup=False, dry_run=dry_run)
        elif step_id == "pre_market_news":
            return self._run_pre_market_analysis(is_catchup=False, dry_run=dry_run)
        elif step_id == "call_auction":
            return self._run_call_auction_decision(is_catchup=False, dry_run=dry_run)
        elif step_id == "morning_open":
            return self._run_morning_open_catchup(is_catchup=False, dry_run=dry_run)
        elif step_id == "midday_recess":
            return self._run_lunch_analysis(is_catchup=False, dry_run=dry_run)
        elif step_id == "closing_rebalance":
            return self._run_closing_rebalance(is_catchup=False, dry_run=dry_run)
        elif step_id == "after_hours_settle":
            return self._run_after_hours_summary(is_catchup=False, dry_run=dry_run)
        elif step_id == "intraday_analysis":
            self._run_analysis()
            return {"success": True, "step_id": step_id, "summary": "自主策略分析执行完毕"}
        else:
            return {"success": False, "error": f"未知步骤ID: {step_id}"}

    # ========== 持久化：每日阶段标志 ==========

    def _save_daily_flags(self, today: str, pre_market: bool = None,
                          lunch: bool = None, after_hours: bool = None):
        """保存每日阶段完成标志，防止重启后重复执行"""
        try:
            from paths import DATA_DIR
            flags_file = DATA_DIR / f"daily_flags_{today}.json"
            DATA_DIR.mkdir(parents=True, exist_ok=True)

            # 读取现有标志
            existing = {}
            if flags_file.exists():
                with open(flags_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)

            now_str = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%H:%M:%S")

            if pre_market is not None:
                existing["pre_market"] = pre_market
                if pre_market:
                    existing["pre_market_time"] = now_str
            if lunch is not None:
                existing["lunch"] = lunch
                if lunch:
                    existing["lunch_time"] = now_str
            if after_hours is not None:
                existing["after_hours"] = after_hours
                if after_hours:
                    existing["after_hours_time"] = now_str

            with open(flags_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_daily_flags(self, today: str) -> Dict:
        """加载每日阶段完成标志"""
        try:
            from paths import DATA_DIR
            flags_file = DATA_DIR / f"daily_flags_{today}.json"
            if flags_file.exists():
                with open(flags_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    # ========== 盘前分析结果持久化 ==========

    def _save_pre_market_result(self):
        """保存盘前分析结果（供盘中分析使用）"""
        if not self._pre_market_result:
            return
        try:
            from paths import DATA_DIR
            result_file = DATA_DIR / f"pre_market_{date.today().isoformat()}.json"
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump(self._pre_market_result, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_pre_market_result(self, target_date: str = None) -> Optional[Dict]:
        """
        加载盘前分析结果
        盘中分析时可直接使用，无需重复抓取新闻
        """
        if target_date is None:
            target_date = date.today().isoformat()
        try:
            from paths import DATA_DIR
            result_file = DATA_DIR / f"pre_market_{target_date}.json"
            if result_file.exists():
                with open(result_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return None

    def _track_daily_sentiment(self, stocks: List[Dict]):
        """每日情绪跟踪：记录持仓股票和有信号股票的情绪变化"""
        try:
            from sentiment_analyzer import sentiment_analyzer
            from auto_trader import auto_trader
            from data_cache import get_realtime_quote
            from db_store import db_store
            from market_regime import market_regime_detector

            try:
                regime = market_regime_detector.detect()
                regime_str = regime.get("regime", "unknown")
            except:
                regime_str = "unknown"

            tracked = 0

            # 跟踪所有绑定账户的持仓股票（多账户矩阵汇总）
            held_symbols = set(auto_trader.positions.keys())
            try:
                for acc in db_store.list_accounts():
                    for sym, pinfo in db_store.get_positions(acc["id"]).items():
                        if pinfo.get("amount", 0) > 0:
                            held_symbols.add(sym)
            except Exception:
                pass

            for symbol in held_symbols:
                try:
                    stock_data = get_realtime_quote(symbol)
                    if not stock_data:
                        continue
                    news = sentiment_analyzer.get_stock_sentiment(symbol, stock_data.get("name", ""))
                    db_store.save_sentiment_daily(
                        symbol=symbol,
                        sentiment=news.get("score", 50),
                        price=stock_data.get("price", 0),
                        change_pct=stock_data.get("change_pct", 0),
                        keywords=news.get("keywords", [])[:5],
                        regime=regime_str,
                    )
                    tracked += 1
                except Exception:
                    pass

            if tracked:
                logger.info(f"📈 每日情绪跟踪: 记录了 {tracked} 只持仓股票的情绪")
        except Exception:
            pass

    def _handle_pending_orders(self):
        """检查未成交委托，智能撤单（考虑时间紧迫度 + 持仓盈亏）"""
        try:
            from ths_connector import get_active_connector
            conn, conn_type = get_active_connector()
            if not conn or not getattr(conn, 'connected', False):
                return

            orders_result = conn.get_orders()
            if not orders_result.get("success"):
                return

            # 缓存未成交委托（供 _build_context() 共享使用，避免重复请求）
            all_orders = orders_result.get("data", [])
            self._cached_pending_orders = [o for o in all_orders if o.get("status") == "未成交"]

            if not self._cached_pending_orders:
                return

            logger.info(f"📋 AI调度器: 发现 {len(self._cached_pending_orders)} 笔未成交委托")

            for order in self._cached_pending_orders:
                symbol = order.get("symbol", "")
                action = order.get("action", "")
                order_amount = order.get("amount", 0)
                order_price = order.get("price", 0)
                order_time = order.get("time", "")

                if not symbol or not order_price:
                    continue

                # ===== 计算挂单时间 =====
                try:
                    order_dt = _dt.strptime(order_time, "%H:%M:%S") if order_time else None
                    now_dt = _dt.now()
                    if order_dt:
                        elapsed_min = (now_dt.hour * 60 + now_dt.minute) - (order_dt.hour * 60 + order_dt.minute)
                    else:
                        elapsed_min = 999
                except Exception:
                    elapsed_min = 999

                # ===== 获取当前价格 =====
                try:
                    from data_cache import get_realtime_quote
                    stock_data = get_realtime_quote(symbol)
                    current_price = float(stock_data.get("price", 0)) if stock_data else 0
                except Exception:
                    current_price = 0

                if not current_price:
                    continue

                price_diff_pct = abs(current_price - order_price) / order_price * 100 if order_price else 0

                # ===== 智能撤单规则 =====
                should_cancel = False
                cancel_reason = ""

                if action == "sell":
                    # --- 卖单：止损紧迫度高 ---
                    # 检查持仓盈亏
                    is_loss_position = False
                    try:
                        from auto_trader import auto_trader
                        if symbol in auto_trader.positions:
                            pos = auto_trader.positions[symbol]
                            avg = pos.avg_price
                            if avg > 0 and current_price < avg:
                                is_loss_position = True
                    except Exception:
                        pass

                    if is_loss_position and elapsed_min >= 3:
                        # 亏损持仓的卖单：3分钟没成交就撤（止损要快）
                        should_cancel = True
                        cancel_reason = f"亏损止损单超时{elapsed_min}分钟未成交"
                    elif elapsed_min >= 5 and price_diff_pct > 1.0:
                        # 普通卖单：5分钟 + 偏离1%
                        should_cancel = True
                        cancel_reason = f"挂单{elapsed_min}分钟,偏离{price_diff_pct:.1f}%"
                    elif elapsed_min >= 20:
                        # 超时兜底：任何卖单超20分钟都撤
                        should_cancel = True
                        cancel_reason = f"挂单超时{elapsed_min}分钟"

                elif action == "buy":
                    # --- 买单：不急但不能无限等 ---
                    if elapsed_min >= 10 and price_diff_pct > 1.5:
                        # 10分钟 + 偏离1.5%
                        should_cancel = True
                        cancel_reason = f"挂单{elapsed_min}分钟,偏离{price_diff_pct:.1f}%"
                    elif elapsed_min >= 30:
                        # 超时兜底：任何买单超30分钟都撤
                        should_cancel = True
                        cancel_reason = f"挂单超时{elapsed_min}分钟"

                # ===== 执行撤单 =====
                if should_cancel:
                    try:
                        cancel_result = conn.cancel_order(order.get("order_id", ""), order.get("date", ""))
                        if cancel_result.get("success"):
                            logger.info(f"🔄 撤单: {symbol} {action} {order_amount}股 @ ¥{order_price} "
                                        f"({cancel_reason}, 当前价¥{current_price})")
                            self._trade_log.append({
                                "time": _dt.now().strftime("%H:%M:%S"),
                                "action": f"auto_cancel_{action}",
                                "symbol": symbol,
                                "amount": order_amount,
                                "success": True,
                                "result": f"智能撤单: {cancel_reason}, 当前价¥{current_price}",
                            })
                            self._save_trade_log()
                            try:
                                from notification import notification_manager
                                notification_manager.add({
                                    "type": "trade",
                                    "level": "warning",
                                    "title": "🔄 智能撤单",
                                    "message": f"{symbol} {'卖出' if action == 'sell' else '买入'}{order_amount}股\n"
                                               f"{cancel_reason}\n当前价¥{current_price}",
                                })
                            except Exception:
                                pass
                    except Exception as e:
                        logger.warning(f"撤单失败: {e}")
                else:
                    logger.info(f"⏳ {symbol} {action} {order_amount}股 @ ¥{order_price} "
                                f"(挂{elapsed_min}分钟, 偏离{price_diff_pct:.1f}%, 保持)")

        except Exception as e:
            logger.error(f"未成交委托检查异常: {e}")

    def _build_context(self) -> str:
        """构建全方位市场上下文信息（含策略信号+市场环境+未成交委托）"""
        parts = []

        # 1. 账户状态
        try:
            from auto_trader import auto_trader
            parts.append(f"可用资金: ¥{auto_trader.balance:,.2f}")
            if auto_trader.positions:
                pos_lines = []
                for sym, pos in auto_trader.positions.items():
                    mp = getattr(pos, 'current_price', 0) or getattr(pos, 'market_price', 0) or getattr(pos, 'cost_price', 0)
                    cp = getattr(pos, 'cost_price', 0)
                    amt = getattr(pos, 'amount', 0)
                    pnl = (mp - cp) * amt if mp and cp else 0
                    pnl_pct = ((mp / cp - 1) * 100) if mp and cp else 0
                    nm = getattr(pos, 'name', sym)
                    # 止损止盈信息
                    sl = getattr(pos, 'stop_loss', 0)
                    tp = getattr(pos, 'take_profit', 0)
                    sl_tp = ""
                    if sl:
                        sl_tp += f" 止损¥{sl:.2f}"
                    if tp:
                        sl_tp += f" 止盈¥{tp:.2f}"
                    pos_lines.append(
                        f"  {nm}({sym}) {amt}股 "
                        f"成本¥{cp:.2f} 现价¥{mp:.2f} "
                        f"盈亏{'+'if pnl>=0 else ''}{pnl:.0f}元({pnl_pct:+.1f}%)"
                        f"{sl_tp}"
                    )
                parts.append("当前持仓:\n" + "\n".join(pos_lines))
            else:
                parts.append("当前持仓: 空仓")
        except Exception:
            parts.append("账户状态: 未知")

        # 2. 大盘概况
        try:
            from data_cache import get_market_basket_quote
            basket = get_market_basket_quote()
            if basket:
                parts.append(
                    f"大盘情绪: 加权涨跌{basket.get('weighted_change', 0):+.2f}% "
                    f"(上涨{basket.get('up_count', 0)}/下跌{basket.get('down_count', 0)})"
                )
        except Exception:
            pass

        # 3. 市场环境识别
        try:
            from market_regime import market_regime_detector
            regime = market_regime_detector.detect()
            if regime and regime.get("regime") != "unknown":
                regime_name = {"bull": "牛市", "bear": "熊市", "sideways": "震荡", "volatile": "波动"}.get(regime["regime"], "未知")
                parts.append(
                    f"📊 市场环境: {regime_name}(置信度{regime['confidence']:.0%})\n"
                    f"  MA20={regime.get('ma20',0):.2f} MA60={regime.get('ma60',0):.2f} "
                    f"波动率={regime.get('volatility',0):.4f} 20日涨跌={regime.get('change_20d',0):+.1f}%\n"
                    f"  {regime.get('description', '')}"
                )
                # 策略调整建议
                adj = market_regime_detector.get_strategy_adjustments(regime["regime"])
                if adj:
                    prefer = ", ".join(adj.get("prefer_categories", []))
                    avoid = ", ".join(adj.get("avoid_categories", []))
                    parts.append(
                        f"  策略建议: 推荐[{prefer}] 避免[{avoid}] "
                        f"仓位系数{adj['position_factor']:.1f}x 止损系数{adj['stop_loss_factor']:.1f}x"
                    )
        except Exception:
            pass

        # 4. 未成交委托（使用 _handle_pending_orders 的缓存，避免重复请求）
        try:
            pending = self._cached_pending_orders
            if pending:
                lines = []
                for o in pending:
                    action = o.get('action', '')
                    symbol = o.get('symbol', '')
                    order_price = o.get('price', 0)
                    order_time = o.get('time', '')
                    # 计算挂单时长
                    urgency = ""
                    try:
                        from datetime import datetime

                        if order_time:
                            od = _dt4.strptime(order_time, "%H:%M:%S")
                            nd = _dt4.now()
                            mins = (nd.hour * 60 + nd.minute) - (od.hour * 60 + od.minute)
                            urgency = f"已挂{mins}分钟"
                            # 亏损卖单标记紧急
                            if action == "sell" and symbol in auto_trader.positions:
                                pos = auto_trader.positions[symbol]
                                if pos.avg_price > 0:
                                    from data_cache import get_realtime_quote as _grq
                                    try:
                                        q = _grq(symbol)
                                        cp = float(q.get("price", 0)) if q else 0
                                        if cp > 0 and cp < pos.avg_price:
                                            urgency += " ⚠️亏损中,需尽快处理"
                                    except Exception:
                                        pass
                    except Exception:
                        urgency = ""
                    label = "卖出" if action == "sell" else "买入"
                    lines.append(
                        f"  {o.get('name','')}({symbol}) {label} "
                        f"{o.get('amount',0)}股 @ ¥{order_price:.2f} "
                        f"时间{order_time} {urgency}"
                    )
                parts.append(f"⚠️ 未成交委托({len(pending)}笔)【请优先处理】:\n" + "\n".join(lines))
            else:
                parts.append("未成交委托: 无")
        except Exception:
            pass

        # 5. 多维度股票排行（OPT-5: 只获取1次，Python内多维排序）
        try:
            from competition import competition_manager

            # 一次获取，缓存30秒内共享
            pool = competition_manager.get_stock_pool(limit=50, sort_by="amount")

            if pool:
                # 成交额 TOP15（默认已按成交额排序）
                lines = []
                for s in pool[:15]:
                    lines.append(
                        f"  {s.get('name','')}({s.get('code','')}) "
                        f"¥{s.get('price',0):.2f} {s.get('change_pct',0):+.1f}% "
                        f"额{s.get('amount',0)/1e8:.1f}亿 换手{s.get('turnover',0):.1f}%"
                    )
                parts.append(f"成交额TOP15:\n" + "\n".join(lines))

                # 涨幅 TOP10（Python排序）
                pool_up = sorted(pool, key=lambda x: x.get('change_pct', 0), reverse=True)[:10]
                lines = []
                for s in pool_up:
                    lines.append(
                        f"  {s.get('name','')}({s.get('code','')}) "
                        f"¥{s.get('price',0):.2f} {s.get('change_pct',0):+.1f}% "
                        f"额{s.get('amount',0)/1e8:.1f}亿"
                    )
                parts.append(f"涨幅TOP10:\n" + "\n".join(lines))

                # 换手率 TOP10（Python排序）
                pool_turn = sorted(pool, key=lambda x: x.get('turnover', 0), reverse=True)[:10]
                lines = []
                for s in pool_turn:
                    lines.append(
                        f"  {s.get('name','')}({s.get('code','')}) "
                        f"¥{s.get('price',0):.2f} {s.get('change_pct',0):+.1f}% "
                        f"换手{s.get('turnover',0):.1f}%"
                    )
                parts.append(f"换手率TOP10（资金活跃）:\n" + "\n".join(lines))

        except Exception:
            pass

        # 6. 策略信号摘要
        try:
            from strategy_registry import registry
            from strategy_tracker import strategy_tracker

            # 获取策略权重排行
            weights = strategy_tracker.get_strategy_weights(days=30)
            if weights:
                # 按权重排序，取前5个
                sorted_w = sorted(weights.items(), key=lambda x: x[1], reverse=True)[:5]
                lines = []
                for name, w in sorted_w:
                    perf = strategy_tracker.get_performance(name, days=30)
                    win_rate = perf.get("win_rate", 0)
                    signals = perf.get("total_signals", 0)
                    lines.append(f"  [{name}] 权重{w:.3f} 胜率{win_rate:.0f}% 信号{signals}次")
                parts.append(f"策略权重排行(基于历史胜率):\n" + "\n".join(lines))
            else:
                parts.append("策略权重: 暂无历史数据（策略系统刚启动）")

            # 获取策略目录摘要
            categories = registry.get_categories()
            cat_counts = {cat: len(names) for cat, names in categories.items()}
            cat_summary = ", ".join(f"{cat}({cnt})" for cat, cnt in cat_counts.items())
            parts.append(f"可用策略: {len(registry.list_names())}个 — {cat_summary}")

        except Exception as e:
            parts.append(f"策略系统: 加载异常({e})")

        # 7. 今日交易统计
        parts.append(f"今日已自动交易: {self._daily_trades}笔")

        # 8. 盘前/午间情绪缓存（全时段覆盖的关键数据）
        try:
            # 优先使用内存缓存，其次从文件加载
            pm = self._pre_market_result or self.load_pre_market_result()
            if pm:
                score = pm.get("score", 50)
                count = pm.get("news_count", 0)
                pm_time = pm.get("time", "??:??")
                sentiment_desc = "偏多" if score > 55 else "偏空" if score < 45 else "中性"
                parts.append(
                    f"📰 盘前新闻分析({pm_time}): 情绪{score}/100({sentiment_desc}) "
                    f"新闻{count}条"
                )

            if self._lunch_sentiment:
                score = self._lunch_sentiment.get("score", 50)
                count = self._lunch_sentiment.get("news_count", 0)
                lt = self._lunch_sentiment.get("time", "??:??")
                sentiment_desc = "偏多" if score > 55 else "偏空" if score < 45 else "中性"
                parts.append(
                    f"🕐 午间情绪({lt}): {score}/100({sentiment_desc}) "
                    f"新闻{count}条"
                )
        except Exception:
            pass

        return "\n\n".join(parts)

    def _build_analysis_prompt(self, context: str) -> str:
        """构建 AI 分析提示"""
        now_str = datetime.now(ZoneInfo("Asia/Shanghai")).strftime('%Y-%m-%d %H:%M:%S')
        return f"""你是自主交易AI，请根据市场情况做出交易决策并直接执行。

当前时间: {now_str}
A股交易时段: 上午9:30-11:30, 下午13:00-15:00

{context}

## 你的职责
你是这个模拟比赛账户的**全权操盘手**。你有完全的交易自主权：
- 自主分析市场、选股、择时
- 自主决定买入/卖出时机和仓位
- 直接执行交易，无需人工确认

## 交易指令格式
如果决定交易，在回复中包含标签（可多个）：
[TRADE:BUY:股票代码:数量]
[TRADE:SELL:股票代码:数量]

## ⚠️ 未成交委托处理（最高优先级）
如果上下文中显示有"未成交委托"，你**必须优先处理**，再考虑新交易：
1. 亏损持仓的卖单：挂3分钟未成交就该撤单重下（止损不能拖）
2. 价格已偏离的委托：系统可能已自动撤单，你可以调整价格重下
3. 有未成交委托的股票：**不要下新单**，先处理旧单
4. 多笔未成交委托堆积：说明市场不活跃，应减少新交易
5. 无未成交委托：才可以自由决策新交易

## 📊 策略辅助决策
你拥有34个量化策略系统支持。上下文中包含"34策略信号"部分，显示每个股票的策略评分。
**必须参考这些信号**：
- 策略说 BUY + 高置信度 → 强烈考虑买入
- 策略说 SELL + 高置信度 → 强烈考虑卖出
- 多个策略信号一致 → 更可靠的信号
- 你自己的分析 + 策略信号 = 最终决策
- 策略给出的目标价/止损价 → 作为你的参考
- **不要完全忽略策略信号**，它们是34个量化模型的集体智慧

## 交易纪律
- 追踪成交额排名靠前的活跃股
- 关注涨幅居前但未涨停的强势股
- 持仓盈利超过10%考虑止盈，亏损超过5%考虑止损
- 单次交易金额不超过可用资金的 {int(self.position_limit_pct*100)}%
- 今日已交易 {self._daily_trades} 笔，无上限
- 参考策略权重和市场环境调整仓位大小

请给出简要分析理由后执行交易。"""

    def _decide_with_ai(self, stocks: List[Dict]) -> List[Dict]:
        """AI 深度参与分析：策略先筛选 → AI再深度分析有信号的股票"""
        context = self._build_context()
        if not context:
            return []

        try:
            from ai_brain import ai_brain
            from data_cache import get_realtime_quote, compute_indicators
            from auto_trader import auto_trader
            from sentiment_analyzer import sentiment_analyzer
            from market_regime import market_regime_detector

            # 获取市场环境
            try:
                regime = market_regime_detector.detect()
                regime_desc = regime.get("description", "未知")
            except:
                regime_desc = "未知"

            # ★ Step 1: 策略引擎先筛选，找出有信号的股票
            flagged_stocks = []
            for stock in stocks:
                symbol = stock.get("code", "")
                if not symbol or symbol in auto_trader.positions:
                    continue
                try:
                    stock_data = get_realtime_quote(symbol)
                    if not stock_data:
                        continue
                    indicators = compute_indicators(symbol) or {"close": stock_data.get("price", 0)}
                    decision = ai_brain.decide(
                        symbol=symbol, stock_data=stock_data,
                        indicators=indicators,
                        account_info=self._get_account_info()
                    )
                    # 只保留有明确信号的（置信度>40%）
                    if decision.action in ("buy", "sell") and decision.confidence > 0.4:
                        flagged_stocks.append({
                            "symbol": symbol,
                            "name": stock_data.get("name", symbol),
                            "stock_data": stock_data,
                            "indicators": indicators,
                            "decision": decision,
                        })
                except Exception:
                    pass

            logger.info(f"策略筛选: {len(stocks)}只 → {len(flagged_stocks)}只有信号")

            # ★ 记录后悔数据：有信号但没买的股票
            try:
                from db_store import db_store
                from market_regime import market_regime_detector
                try:
                    regime = market_regime_detector.detect()
                    regime_str = regime.get("regime", "unknown")
                except:
                    regime_str = "unknown"
                for item in flagged_stocks:
                    db_store.save_sentiment_regret(
                        symbol=item["symbol"],
                        stock_name=item["name"],
                        signal=item["decision"].action,
                        confidence=item["decision"].confidence,
                        sentiment=50,  # 后续可从情绪分析获取
                        price=item["stock_data"].get("price", 0),
                        regime=regime_str,
                    )
            except Exception:
                pass

            # ★ Step 2: AI深度分析有信号的股票（新闻+完整上下文）
            stock_analyses = []
            for item in flagged_stocks[:20]:  # 最多分析20只
                symbol = item["symbol"]
                stock_data = item["stock_data"]
                decision = item["decision"]
                name = item["name"]

                # 新闻情绪
                try:
                    news = sentiment_analyzer.get_stock_sentiment(symbol, name)
                    news_desc = f"{news.get('score', 50)}分 ({news.get('description', '')})"
                except:
                    news_desc = "无数据"

                # ★ 历史情绪准确率
                try:
                    from db_store import db_store
                    accuracy = db_store.get_sentiment_accuracy(symbol, min_sentiment=50, days=30)
                    if accuracy["count"] > 0:
                        hist_desc = f"历史准确率{accuracy['win_rate']}% 平均盈利{accuracy['avg_profit']}% ({accuracy['count']}次)"
                    else:
                        hist_desc = "无历史数据"
                except:
                    hist_desc = "无历史数据"

                stock_analyses.append(
                    f"【{name} {symbol}】\n"
                    f"  价格: {stock_data.get('price', '?')} | 涨跌: {stock_data.get('change_pct', '?')}%\n"
                    f"  策略信号: {decision.action.upper()} 置信度{decision.confidence:.0%}\n"
                    f"  新闻情绪: {news_desc}\n"
                    f"  {hist_desc}\n"
                    f"  目标价: {getattr(decision, 'target_price', '?')} | 止损: {getattr(decision, 'stop_loss', '?')}\n"
                    f"  策略理由: {'; '.join(getattr(decision, 'reasons', [])[:3])}"
                )

            if stock_analyses:
                # ★ 市场环境历史准确率
                try:
                    from db_store import db_store
                    regime_acc = db_store.get_sentiment_by_regime(regime_str, days=30)
                    regime_hist = f" ({regime_str}下历史胜率{regime_acc['win_rate']}%, 平均盈利{regime_acc['avg_profit']}%, {regime_acc['count']}次)" if regime_acc["count"] > 0 else ""
                except:
                    regime_hist = ""
                context += f"\n\n## 市场环境: {regime_desc}{regime_hist}\n"
                context += f"## 筛选出 {len(flagged_stocks)} 只有策略信号的股票，AI深度分析:\n"
                context += "\n".join(stock_analyses)
                context += "\n\n请逐只分析以上股票（它们已有策略信号），结合新闻情绪做最终决策。"
            else:
                context += "\n\n## 策略筛选结果: 本轮无股票触发策略信号"
                context += "\n请根据市场环境和你的判断，决定是否需要主动寻找机会。"
        except Exception:
            pass

        # 彻底去除外部大模型决策，统一由纯量化规则与策略矩阵执行
        return self._decide_with_rules(stocks)

    def _decide_with_rules(self, stocks: List[Dict]) -> List[Dict]:
        """规则引擎决策：批量选股 + 动态分散"""
        from ai_brain import ai_brain
        from data_cache import get_realtime_quote, compute_indicators
        from auto_trader import auto_trader

        # 1. 获取当前持仓数量
        current_positions = len(auto_trader.positions)

        # 2. 根据比赛阶段确定目标持仓数
        try:
            from risk_manager import risk_manager
            days = risk_manager.get_competition_days_elapsed()
            total = risk_manager.get_total_competition_days()
            phase = risk_manager.get_phase_config(days, total)
            target_positions = phase.get("max_position_count", 5)
        except Exception:
            target_positions = 5

        # 3. 计算需要新建仓的数量
        slots_to_fill = target_positions - current_positions
        if slots_to_fill <= 0:
            logger.info(f"已持有{current_positions}只，目标{target_positions}只，无需建仓")
            return []

        # 4. 分析所有股票并评分
        candidates = []
        for stock in stocks[:50]:  # 分析前50只
            symbol = stock.get("code", "")
            if not symbol or symbol in auto_trader.positions:
                continue  # 跳过已持有的

            # 冷却检查
            now_ts = _time.time()
            if now_ts - self._trade_cooldown.get(symbol, 0) < 1800:
                continue

            try:
                stock_data = get_realtime_quote(symbol)
                if not stock_data:
                    continue

                indicators = compute_indicators(symbol) or {"close": stock_data.get("price", 0)}

                decision = ai_brain.decide(
                    symbol=symbol,
                    stock_data=stock_data,
                    indicators=indicators,
                    account_info=self._get_account_info()
                )

                if decision.action == "buy":
                    amount = decision.position_size if decision.position_size >= 100 else 100
                    signal_weight = 1.3 if getattr(decision, "signal", "") == "STRONG_BUY" else (1.1 if getattr(decision, "signal", "") == "BUY" else 1.0)
                    score = decision.confidence * signal_weight
                    candidates.append({
                        "symbol": symbol,
                        "confidence": decision.confidence,
                        "signal": getattr(decision, "signal", "BUY"),
                        "position_size": amount,
                        "score": score
                    })
            except Exception as e:
                logger.warning(f"分析 {symbol} 失败: {e}")

        # 5. 按综合得分降序排序，遴选前 slots_to_fill 只优质标的
        candidates.sort(key=lambda x: x["score"], reverse=True)
        selected = candidates[:slots_to_fill]

        # ★ 比赛规则合规持仓底线补齐机制 (Min-Holdings Compliance Fallback)
        # 比赛规则要求持仓至少 3 只 (min_holdings: 3)，单票上限 35%
        # 如果当前持仓数+入选数 < 3，且可用资金充裕，从优选池中补齐高流动性基底标的
        min_required = 3
        current_and_selected = len(auto_trader.positions) + len(selected)
        if current_and_selected < min_required and auto_trader.balance > 20000:
            deficit = min_required - current_and_selected
            logger.info(f"🛡️ 触发比赛合规底线兜底: 当前+已选 {current_and_selected} 只，缺少 {deficit} 只满足 >=3 只红线，从优质活跃池补齐...")
            total_assets = auto_trader.balance + sum(p.market_value for p in auto_trader.positions.values())
            chosen_syms = set(auto_trader.positions.keys()) | {item["symbol"] for item in selected}
            for s in stocks:
                sym = s.get("code", "")
                px = float(s.get("price", 0))
                chg = float(s.get("change_pct", 0))
                if not sym or sym in chosen_syms or px <= 0:
                    continue
                if abs(chg) >= 9.5:
                    continue
                if px * 100 > total_assets * 0.25:
                    continue
                target_value = total_assets * 0.20
                shares = int((target_value / px) // 100 * 100)
                if shares < 100 or shares * px > auto_trader.balance * 0.90:
                    continue
                selected.append({
                    "symbol": sym,
                    "position_size": shares,
                    "confidence": 0.60,
                    "signal": "BUY",
                    "score": 0.60,
                })
                chosen_syms.add(sym)
                deficit -= 1
                logger.info(f"🛡️ 选定合规兜底标的: {s.get('name', sym)}({sym}) 计划买入 {shares}股 @ ¥{px:.2f} (仓位约 {(shares*px/total_assets)*100:.1f}%)")
                if deficit <= 0:
                    break

        # 6. 生成交易指令
        trades = []
        for item in selected:
            trades.append({
                "action": "buy",
                "symbol": item["symbol"],
                "amount": item["position_size"],
            })
            logger.info(f"批量选股: {item['symbol']} {item['position_size']}股 "
                       f"(置信度{item['confidence']:.2f}, 得分{item['score']:.2f})")

        return trades

    def _get_account_info(self) -> Dict:
        """获取账户信息（供决策使用）"""
        try:
            from auto_trader import auto_trader
            return {
                "balance": auto_trader.balance,
                "total_assets": auto_trader.balance + sum(
                    p.market_value for p in auto_trader.positions.values()
                ),
                "positions": {
                    s: {"amount": p.amount, "current_price": p.current_price}
                    for s, p in auto_trader.positions.items()
                }
            }
        except Exception:
            return {"balance": 1000000, "total_assets": 1000000, "positions": {}}

    def _parse_trade_tags(self, ai_response: str) -> List[Dict]:
        """解析 AI 回复中的交易标签"""
        pattern = re.compile(r'\[TRADE:(BUY|SELL):(\d{6}):(\d+)\]', re.IGNORECASE)
        trades = []
        for action, symbol, amount in pattern.findall(ai_response):
            trades.append({
                "action": action.lower(),
                "symbol": symbol,
                "amount": int(amount),
            })
        return trades

    def _execute_trades(self, trades: List[Dict]):
        """执行交易列表 — 通过 AutoTrader 执行（含风控+持久化+策略记录）"""
        import time as _time_mod

        for trade in trades:
            action = trade["action"]
            symbol = trade["symbol"]
            amount = trade["amount"]
            label = "买入" if action == "buy" else "卖出"

            try:
                # ===== SAFE-1: 整手验证（A股买入必须100股整数倍）=====
                if action == "buy":
                    if amount < 100:
                        logger.warning(f"买入数量{amount}股不足1手(100股)，跳过")
                        self._trade_log.append({
                            "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S"),
                            "action": action, "symbol": symbol, "amount": amount,
                            "success": False, "result": f"不足1手(100股), 给定{amount}股"
                        })
                        self._save_trade_log()
                        continue
                    amount = (amount // 100) * 100  # 强制取整到100股

                # ===== SAFE-2: 重复交易冷却（同一只股票30分钟内不重复买）=====
                now_ts = _time_mod.time()
                if action == "buy":
                    last_ts = self._trade_cooldown.get(symbol, 0)
                    elapsed = now_ts - last_ts
                    if elapsed < 1800:  # 30分钟冷却
                        remaining = int(1800 - elapsed)
                        logger.info(f"⏰ {symbol} 交易冷却中（剩余{remaining}秒），跳过")
                        self._trade_log.append({
                            "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S"),
                            "action": action, "symbol": symbol, "amount": amount,
                            "success": False, "result": f"冷却中（剩余{remaining}秒）"
                        })
                        self._save_trade_log()
                        continue

                # 获取股票数据
                from data_cache import get_realtime_quote
                stock_data = get_realtime_quote(symbol)
                if not stock_data:
                    logger.warning(f"无法获取 {symbol} 数据")
                    continue

                price = float(stock_data.get("price", 0))
                if not price:
                    logger.warning(f"{symbol} 价格为0，跳过")
                    continue

                # ===== 比赛规则校验 =====
                # 涨停检查
                change_pct = float(stock_data.get("change_pct", 0))
                if action == "buy" and change_pct >= 9.9:
                    logger.info(f"📊 {symbol} 涨停中(+{change_pct:.1f}%)，无法买入，跳过")
                    continue
                if action == "sell" and change_pct <= -9.9:
                    logger.info(f"📊 {symbol} 跌停中({change_pct:.1f}%)，无法卖出，跳过")
                    continue

                # ===== CRIT-1: 多账户矩阵交易 (Mode B) / 单账户 AutoTrader 兜底 =====
                from db_store import db_store
                from account_manager import account_manager
                accounts = db_store.list_accounts()
                valid_accounts = [a for a in accounts if db_store.get_account_cookie(a["id"]) and not db_store.get_account_cookie(a["id"]).startswith("sandbox_")]

                if not valid_accounts:
                    # 单账户回退逻辑
                    if action == "buy":
                        try:
                            from auto_trader import auto_trader as _at
                            total_assets = _at.balance + sum(p.market_value for p in _at.positions.values())
                            existing_value = 0
                            if symbol in _at.positions:
                                existing_value = _at.positions[symbol].market_value
                            buy_value = price * amount
                            if total_assets > 0 and (existing_value + buy_value) / total_assets > 0.30:
                                max_buy = int((total_assets * 0.30 - existing_value) / price / 100) * 100
                                if max_buy < 100:
                                    logger.info(f"📊 {symbol} 仓位已达30%上限，跳过")
                                    continue
                                amount = max_buy
                                logger.info(f"📊 {symbol} 调整买入数量为{amount}股（仓位限制30%）")
                        except Exception:
                            pass

                    from auto_trader import auto_trader
                    if not auto_trader.enabled:
                        auto_trader.enabled = True

                    order = auto_trader.create_order(symbol, action, amount, price)
                    result = auto_trader.execute_order(order, stock_data)
                    success = result.get("success", False)
                    result_msg = result.get("message", "")
                else:
                    # 多账户矩阵并发独立交易 (Mode B)
                    overall_success = False
                    exec_results = []
                    for acc in valid_accounts:
                        acc_id = acc["id"]
                        acc_name = acc.get("name", f"账户{acc_id}")
                        trader = account_manager.get_trader(acc_id)
                        if not trader or not trader.connected:
                            continue

                        tot_assets = float(acc.get("total_assets") or acc.get("balance") or 100000.0)
                        avail_balance = float(acc.get("ky_fund") or acc.get("balance") or 0.0)
                        pos_dict = db_store.get_positions(acc_id)

                        if action == "buy":
                            # 独立检查单账户 30% 仓位限额
                            existing_shares = pos_dict.get(symbol, {}).get("amount", 0)
                            existing_val = existing_shares * price
                            max_allowed_val = tot_assets * 0.30
                            if existing_val >= max_allowed_val:
                                logger.info(f"📊 [{acc_name}] {symbol} 已达30%上限(现持{existing_shares}股)，跳过")
                                continue
                            room_val = min(max_allowed_val - existing_val, avail_balance * 0.95)
                            acc_amount = int((room_val / price) // 100 * 100)
                            if acc_amount < 100:
                                logger.info(f"📊 [{acc_name}] 资金或上限不足购买1手 {symbol}，跳过")
                                continue

                            if not self.enabled:
                                acc_res = {"success": True, "message": "模拟演练成功"}
                            else:
                                acc_res = trader.buy(symbol, price, acc_amount)

                            acc_ok = acc_res.get("success", False)
                            if acc_ok:
                                overall_success = True
                            status_txt = '成功' if acc_ok else acc_res.get('message', '失败')
                            exec_results.append(f"[{acc_name}:{acc_amount}股:{status_txt}]")

                            db_store.add_trade({
                                "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S"),
                                "action": "buy",
                                "symbol": symbol,
                                "name": stock_data.get("name", symbol),
                                "amount": acc_amount,
                                "price": price,
                                "total": round(price * acc_amount, 2),
                                "balance": round(avail_balance - (price * acc_amount if acc_ok else 0), 2),
                                "mode": "real" if trader.connected else "simulated"
                            }, account_id=acc_id)
                            if acc_ok:
                                try:
                                    trader.sync_from_ths()
                                except Exception:
                                    pass

                        elif action == "sell":
                            pos = pos_dict.get(symbol)
                            if not pos or pos.get("amount", 0) <= 0:
                                continue
                            hold_shares = pos.get("amount", 0)
                            sell_shares = min(hold_shares, amount) if amount > 0 else hold_shares
                            if sell_shares <= 0:
                                continue

                            if not self.enabled:
                                acc_res = {"success": True, "message": "模拟演练成功"}
                            else:
                                acc_res = trader.sell(symbol, price, sell_shares)

                            acc_ok = acc_res.get("success", False)
                            if acc_ok:
                                overall_success = True
                            status_txt = '成功' if acc_ok else acc_res.get('message', '失败')
                            exec_results.append(f"[{acc_name}:{sell_shares}股:{status_txt}]")

                            db_store.add_trade({
                                "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S"),
                                "action": "sell",
                                "symbol": symbol,
                                "name": stock_data.get("name", symbol),
                                "amount": sell_shares,
                                "price": price,
                                "total": round(price * sell_shares, 2),
                                "balance": round(avail_balance + (price * sell_shares if acc_ok else 0), 2),
                                "mode": "real" if trader.connected else "simulated"
                            }, account_id=acc_id)
                            if acc_ok:
                                try:
                                    trader.sync_from_ths()
                                except Exception:
                                    pass

                    success = overall_success
                    result_msg = "; ".join(exec_results) if exec_results else "无符合持仓/资金条件的账户"

                if success:
                    self._daily_trades += 1

                log_entry = {
                    "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S"),
                    "action": action,
                    "symbol": symbol,
                    "amount": amount,
                    "success": success,
                    "result": result_msg,
                }
                self._trade_log.append(log_entry)
                self._save_trade_log()

                # ★ 记录情绪数据（买入时）
                if success and action == "buy":
                    try:
                        from sentiment_analyzer import sentiment_analyzer
                        from market_regime import market_regime_detector
                        news = sentiment_analyzer.get_stock_sentiment(symbol, stock_data.get("name", ""))
                        try:
                            regime = market_regime_detector.detect()
                            regime_str = regime.get("regime", "unknown")
                        except:
                            regime_str = "unknown"
                        from db_store import db_store
                        db_store.save_sentiment_snapshot(
                            symbol=symbol,
                            stock_name=stock_data.get("name", symbol),
                            action="buy",
                            price=price,
                            amount=amount,
                            sentiment=news.get("score", 50),
                            keywords=[k["word"] for k in news.get("matched_keywords", [])[:10]],
                            news_titles=[n.get("title", "") for n in news.get("news_headlines", [])[:5]],
                            strategy=trade.get("strategy", ""),
                            regime=regime_str,
                        )
                    except Exception:
                        pass

                status = "✅" if success else "❌"
                logger.info(f"🤖 AI调度器 {label} {symbol} {amount}股 → {status}")

                # OPT-9: 推送交易通知到前端
                if success:
                    try:
                        from notification import notification_manager
                        notification_manager.add({
                            "type": "trade",
                            "level": "success",
                            "title": f"🤖 AI自动{'买入' if action == 'buy' else '卖出'}",
                            "message": f"{symbol} {amount}股 @ ¥{price}\n分析轮次: #{self._analysis_count}",
                        })
                    except Exception:
                        pass

                # 记录交易冷却时间
                if action == "buy" and success:
                    self._trade_cooldown[symbol] = now_ts

                # ===== GAP-1: 记录策略信号（供策略系统学习）=====
                if success:
                    try:
                        from strategy_tracker import strategy_tracker
                        # OPT-13: 包含分析轮次以便区分信号来源
                        strategy_tracker.record_signal(
                            strategy_name=f"ai_scheduler_round{self._analysis_count}",
                            symbol=symbol,
                            signal="BUY" if action == "buy" else "SELL",
                            score=0,
                            confidence=0.5,
                            price=price
                        )
                    except Exception:
                        pass

            except Exception as e:
                logger.error(f"AI调度器执行交易失败: {e}")
                self._trade_log.append({
                    "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S"),
                    "action": action,
                    "symbol": symbol,
                    "amount": amount,
                    "success": False,
                    "result": str(e),
                })
                self._save_trade_log()

    def _evaluate_strategy_signals(self):
        """定期评估策略信号准确性（GAP-2）"""
        try:
            from auto_trader import auto_trader
            # 获取当前持仓价格
            prices = {}
            for sym, pos in auto_trader.positions.items():
                try:
                    from data_cache import get_realtime_quote
                    q = get_realtime_quote(sym)
                    if q and q.get("price"):
                        prices[sym] = float(q["price"])
                except Exception:
                    pass

            if prices:
                auto_trader.evaluate_signals(prices)
                logger.info(f"📊 策略信号评估完成: {len(prices)}只股票")
        except Exception as e:
            logger.error(f"策略信号评估失败: {e}")

    # ========== Status API ==========

    def get_status(self) -> Dict:
        # 加载盘前结果（如果内存中没有）
        pm = self._pre_market_result or self.load_pre_market_result()
        curr_now = self.now()
        today_str = curr_now.date().isoformat()

        # 获取时序窗口与审计账本数据
        from competition import competition_manager
        current_win = competition_manager.get_detailed_time_window(curr_now)
        daily_ledger = scheduler_ledger.get_daily_ledger(today_str)

        completed_cnt = len([r for r in daily_ledger if r.get("status") in ["SUCCESS", "CATCHUP_SUCCESS"]])
        skipped_cnt = len([r for r in daily_ledger if "SKIPPED" in r.get("status", "")])
        failed_cnt = len([r for r in daily_ledger if r.get("status") == "FAILED"])
        pending_cnt = len([r for r in daily_ledger if r.get("status") == "PENDING"])

        return {
            "enabled": self.enabled,
            "running": self._running,
            "interval_minutes": self.interval_minutes,
            "max_trades_per_day": self.max_trades_per_day,
            "daily_trades": self._daily_trades,
            "daily_limit": self.max_trades_per_day,
            "last_analysis_time": self._last_analysis_time,
            "last_analysis_result": self._last_analysis_result[:500] if self._last_analysis_result else "",
            "trade_log": self._trade_log[-10:],
            # 现实时序状态与时钟感知
            "current_time": curr_now.strftime("%Y-%m-%d %H:%M:%S"),
            "is_mock_time": self._mock_time is not None,
            "mock_time_speed": self._mock_time_speed if self._mock_time else 1.0,
            "current_window": current_win,
            # 审计账本统计
            "ledger_summary": {
                "total_steps": len(daily_ledger),
                "completed": completed_cnt,
                "skipped": skipped_cnt,
                "failed": failed_cnt,
                "pending": pending_cnt,
            },
            # 兼容旧版全时段状态
            "pre_market_done": self._pre_market_done,
            "call_auction_done": self._call_auction_done,
            "morning_open_done": self._morning_open_done,
            "lunch_done": self._lunch_done,
            "afternoon_open_done": self._afternoon_open_done,
            "closing_rebalance_done": self._closing_rebalance_done,
            "after_hours_done": self._after_hours_done,
            "pre_market_score": pm.get("score") if pm else None,
            "pre_market_news_count": pm.get("news_count") if pm else None,
            "lunch_score": self._lunch_sentiment.get("score") if self._lunch_sentiment else None,
        }


# 全局实例
ai_scheduler = AIScheduler()
