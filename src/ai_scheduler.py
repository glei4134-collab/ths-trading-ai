"""
AI 自动交易调度器
后台定时驱动 AI 分析市场、做出交易决策并自动执行
"""

import re
import json
import logging
import threading
import time as _time
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import date
from typing import Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

from paths import CONFIG_DIR, DATA_DIR

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

        # ===== 全时段覆盖：盘前/午间/收盘后 =====
        self._pre_market_done = False             # 今日盘前分析是否已完成
        self._lunch_done = False                  # 今日午间分析是否已完成
        self._after_hours_done = False            # 今日收盘后总结是否已完成
        self._pre_market_result: Optional[Dict] = None   # 盘前分析缓存结果
        self._lunch_sentiment: Optional[Dict] = None     # 午间情绪缓存
        self._ths_connected = False               # THS连接状态
        self._morning_open_done = False           # 今日早盘开盘处理是否已完成
        self._afternoon_open_done = False         # 今日午盘开盘处理是否已完成

        self._load_config()
        self._load_trade_log()

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

        # ===== 启动补救：检查今天是否做过盘前分析，没做就立刻补做 =====
        self._check_startup_catchup()

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f"AI调度器已启动 (间隔={self.interval_minutes}分钟, 自动交易={'开' if self.enabled else '关'})")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("AI调度器已停止")

    # ========== Main Loop ==========

    def _loop(self):
        while self._running:
            try:
                today = date.today().isoformat()
                now = datetime.now(ZoneInfo("Asia/Shanghai"))
                current_time = now.time()

                # 重置每日计数 + 阶段标志
                if self._last_trade_date != today:
                    self._daily_trades = 0
                    self._last_trade_date = today
                    self._trade_log = []
                    self._pre_market_done = False
                    self._lunch_done = False
                    self._after_hours_done = False
                    self._pre_market_result = None
                    self._lunch_sentiment = None
                    self._morning_open_done = False
                    self._afternoon_open_done = False
                    self._save_trade_log()

                # OPT-1: enabled 检查移到最前面
                if not self.enabled or not self._running:
                    _time.sleep(10)
                    continue

                from competition import competition_manager
                is_trading_day = competition_manager.is_trading_day(now.date())

                # ===== 区分时段，分别处理 =====
                from datetime import time as _dt_time

                if is_trading_day and _dt_time(9, 20) <= current_time <= _dt_time(9, 30):
                    # ===== 开盘前: 抓一次新闻 =====
                    if not self._pre_market_done:
                        self._run_pre_market_analysis()
                    _time.sleep(60)
                    continue

                elif is_trading_day and _dt_time(11, 30) <= current_time <= _dt_time(13, 0):
                    # ===== 午间: 抓一次新闻 + THS连接检查 =====
                    if not self._lunch_done:
                        self._run_lunch_analysis()
                    # 午间检查THS连接（为下午交易做准备）
                    self._check_ths_connection()
                    market = competition_manager.get_market_status()
                    next_open = market.get("next_open_seconds", 0)
                    sleep_sec = min(next_open, 1800) if next_open > 60 else 300
                    _time.sleep(sleep_sec)
                    continue

                elif is_trading_day and competition_manager.is_trading_hours():
                    # ===== 盘中交易 (9:30-11:30, 13:00-15:00) =====
                    # 检查 THS 连接
                    self._check_ths_connection()
                    if not self._ths_connected:
                        _time.sleep(120)
                        continue

                    # ===== 每次开盘立即处理未成交委托（不等10分钟）=====
                    current_session = "morning" if current_time <= _dt_time(12, 0) else "afternoon"
                    if current_session == "morning" and not self._morning_open_done:
                        self._morning_open_done = True
                        logger.info("📋 早盘开盘: 立即处理未成交委托...")
                        self._handle_pending_orders()
                    elif current_session == "afternoon" and not self._afternoon_open_done:
                        self._afternoon_open_done = True
                        logger.info("📋 午盘开盘: 立即处理未成交委托...")
                        self._handle_pending_orders()

                    # 等待分析间隔
                    _time.sleep(self.interval_minutes * 60)

                    if not self.enabled or not self._running:
                        continue

                    # 执行一轮 AI 分析
                    self._run_analysis()

                elif is_trading_day and current_time > _dt_time(15, 0):
                    # ===== 收盘后 (15:00-次日6:30): 汇总+预判次日 =====
                    if not self._after_hours_done:
                        self._run_after_hours_summary()
                    market = competition_manager.get_market_status()
                    next_open = market.get("next_open_seconds", 0)
                    if next_open > 60:
                        sleep_sec = min(next_open, 1800)  # 最多睡30分钟
                    else:
                        sleep_sec = 300
                    _time.sleep(sleep_sec)
                    continue

                else:
                    # ===== 其他时间（周末/节假日/凌晨）: 长时间等待 =====
                    market = competition_manager.get_market_status()
                    next_open = market.get("next_open_seconds", 0)
                    if next_open > 60:
                        sleep_sec = min(next_open, 1800)  # 最多睡30分钟
                    else:
                        sleep_sec = 300
                    _time.sleep(sleep_sec)
                    continue

            except Exception as e:
                logger.error(f"AI调度器循环异常: {e}")
                _time.sleep(60)

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

            # 2. 决策模式：AI 或 规则引擎
            from ai_connector import ai_connector
            use_ai = ai_connector.connected

            if use_ai:
                trades = self._decide_with_ai(stocks)
            else:
                trades = self._decide_with_rules(stocks)

            # 3. 执行交易
            if trades:
                self._execute_trades(trades)
            else:
                logger.info(f"调度器: 分析完成，本轮无交易信号 (模式: {'AI' if use_ai else '规则'})")

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

    # ========== 启动补救 ==========

    def _check_startup_catchup(self):
        """
        启动补救：今天没做过的阶段，立刻补做
        """
        today = date.today().isoformat()
        now = datetime.now(ZoneInfo("Asia/Shanghai"))

        from competition import competition_manager
        if not competition_manager.is_trading_day(now.date()):
            return

        done = self._load_daily_flags(today)

        if not done.get("pre_market"):
            logger.info("🔄 启动补救: 补做盘前新闻分析...")
            self._run_pre_market_analysis()

        if not done.get("lunch") and now.time() > datetime.strptime("11:30", "%H:%M").time():
            logger.info("🔄 启动补救: 补做午间分析...")
            self._run_lunch_analysis()

        if not done.get("after_hours") and now.time() > datetime.strptime("15:00", "%H:%M").time():
            logger.info("🔄 启动补救: 补做收盘总结...")
            self._run_after_hours_summary()

    # ========== 盘前分析 (9:20-9:30 / 启动补救) ==========

    def _run_pre_market_analysis(self):
        """
        盘前分析：抓取早间新闻 → 情感分析 → 缓存结果
        盘中第一轮分析时直接使用缓存结果
        """
        logger.info("📰 盘前分析: 开始抓取早间新闻...")
        try:
            from sentiment_analyzer import sentiment_analyzer

            # 抓取全市场情绪
            market_sentiment = sentiment_analyzer.get_market_sentiment()

            # 尝试分析关注的持仓股票情绪
            stock_sentiments = {}
            try:
                from auto_trader import auto_trader
                for symbol in auto_trader.positions:
                    try:
                        pos = auto_trader.positions[symbol]
                        name = getattr(pos, 'name', symbol)
                        stock_sentiments[symbol] = sentiment_analyzer.get_stock_sentiment(symbol, name)
                    except Exception:
                        pass
            except Exception:
                pass

            self._pre_market_result = {
                "date": date.today().isoformat(),
                "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S"),
                "market_sentiment": market_sentiment,
                "stock_sentiments": stock_sentiments,
                "news_count": market_sentiment.get("news_count", 0),
                "score": market_sentiment.get("score", 50),
            }

            self._pre_market_done = True
            self._save_pre_market_result()
            self._save_daily_flags(date.today().isoformat(), pre_market=True)

            score = market_sentiment.get("score", 50)
            count = market_sentiment.get("news_count", 0)
            logger.info(f"📰 盘前分析完成: 情绪{score}/100, 新闻{count}条")

            # 推送盘前分析结果
            try:
                from notification import notification_manager
                sentiment_desc = "偏多" if score > 55 else "偏空" if score < 45 else "中性"
                notification_manager.add({
                    "type": "system",
                    "level": "info",
                    "title": "📰 盘前新闻分析",
                    "message": f"情绪: {score}/100 ({sentiment_desc})\n"
                               f"新闻: {count}条\n"
                               f"持仓情绪: {len(stock_sentiments)}只",
                })
            except Exception:
                pass

        except Exception as e:
            logger.error(f"盘前分析失败: {e}")
            self._pre_market_done = True  # 标记为已完成，避免反复重试
            self._save_daily_flags(date.today().isoformat(), pre_market=True)

    # ========== 午间分析 (11:30-13:00) ==========

    def _run_lunch_analysis(self):
        """
        午间分析：更新午间新闻情绪 + 检查持仓风险
        为下午交易提供最新情绪依据
        """
        logger.info("🕐 午间分析: 更新市场情绪...")
        try:
            from sentiment_analyzer import sentiment_analyzer

            # 重新抓取新闻（午间有新消息）
            market_sentiment = sentiment_analyzer.get_market_sentiment()

            self._lunch_sentiment = {
                "date": date.today().isoformat(),
                "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S"),
                "market_sentiment": market_sentiment,
                "score": market_sentiment.get("score", 50),
                "news_count": market_sentiment.get("news_count", 0),
            }

            # 检查持仓是否有风险
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
                                if loss_pct > 0.03:  # 亏损超3%提醒
                                    risk_alerts.append(
                                        f"{pos.name}({symbol}) 亏损{loss_pct:.1%}"
                                    )
                    except Exception:
                        pass
            except Exception:
                pass

            self._lunch_done = True
            self._save_daily_flags(date.today().isoformat(), lunch=True)

            score = market_sentiment.get("score", 50)
            logger.info(f"🕐 午间分析完成: 情绪{score}/100")

            # 推送午间分析结果
            try:
                from notification import notification_manager
                msg_parts = [f"情绪: {score}/100"]
                if risk_alerts:
                    msg_parts.append(f"风险持仓: {', '.join(risk_alerts)}")
                notification_manager.add({
                    "type": "system",
                    "level": "warning" if risk_alerts else "info",
                    "title": "🕐 午间分析",
                    "message": "\n".join(msg_parts),
                })
            except Exception:
                pass

        except Exception as e:
            logger.error(f"午间分析失败: {e}")
            self._lunch_done = True
            self._save_daily_flags(date.today().isoformat(), lunch=True)

    # ========== 收盘后总结 (15:00+) ==========

    def _run_after_hours_summary(self):
        """
        收盘后总结：汇总当日数据 + 预判次日方向
        为次日盘前分析做准备
        """
        logger.info("🌙 收盘后总结: 生成当日汇总...")
        try:
            # 获取收盘后新闻
            from sentiment_analyzer import sentiment_analyzer
            market_sentiment = sentiment_analyzer.get_market_sentiment()

            # 获取当日交易统计
            trade_summary = {
                "total_trades": self._daily_trades,
                "trade_log": self._trade_log[-20:],  # 最近20条
            }

            # 获取持仓状态
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

            summary = {
                "date": date.today().isoformat(),
                "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S"),
                "market_sentiment": {
                    "score": market_sentiment.get("score", 50),
                    "news_count": market_sentiment.get("news_count", 0),
                },
                "trade_summary": trade_summary,
                "position_summary": position_summary,
            }

            self._after_hours_done = True
            self._save_daily_flags(date.today().isoformat(), after_hours=True)

            # 保存收盘总结
            try:
                from paths import DATA_DIR
                summary_file = DATA_DIR / f"after_hours_{date.today().isoformat()}.json"
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                with open(summary_file, "w", encoding="utf-8") as f:
                    json.dump(summary, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

            score = market_sentiment.get("score", 50)
            logger.info(f"🌙 收盘后总结完成: 情绪{score}/100, 交易{self._daily_trades}笔, 持仓{len(position_summary)}只")

        except Exception as e:
            logger.error(f"收盘后总结失败: {e}")
            self._after_hours_done = True
            self._save_daily_flags(date.today().isoformat(), after_hours=True)

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

            # 跟踪持仓股票
            for symbol in auto_trader.positions:
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
                        keywords=[k["word"] for k in news.get("matched_keywords", [])[:5]],
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

        prompt = self._build_analysis_prompt(context)
        from ai_connector import ai_connector
        result = ai_connector.chat(prompt, system_prompt=_AUTO_TRADE_SYSTEM_PROMPT)

        if not result.get("success"):
            logger.error(f"AI分析失败: {result.get('message')}")
            return []

        ai_content = result["data"]["content"]
        self._last_analysis_time = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
        self._last_analysis_result = ai_content

        return self._parse_trade_tags(ai_content)

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

                if decision.action == "buy" and decision.confidence > 0.5:
                    candidates.append({
                        "symbol": symbol,
                        "confidence": decision.confidence,
                        "position_size": decision.position_size,
                        "score": decision.confidence * decision.position_size
                    })
            except Exception as e:
                logger.warning(f"分析 {symbol} 失败: {e}")

        # 5. 按综合得分排序，选前N只
        candidates.sort(key=lambda x: x["score"], reverse=True)
        selected = candidates[:slots_to_fill]

        # 6. 生成交易指令
        trades = []
        for item in selected:
            trades.append({
                "action": "buy",
                "symbol": item["symbol"],
                "amount": item["position_size"],
            })
            self._trade_cooldown[item["symbol"]] = now_ts
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

                # 仓位限制检查 (30%)
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

                # ===== CRIT-1: 始终通过 AutoTrader 执行（含风控+持久化）=====
                from auto_trader import auto_trader

                # 确保 AutoTrader 已启用
                if not auto_trader.enabled:
                    auto_trader.enabled = True

                order = auto_trader.create_order(symbol, action, amount, price)
                result = auto_trader.execute_order(order, stock_data)
                success = result.get("success", False)
                if success:
                    self._daily_trades += 1

                log_entry = {
                    "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S"),
                    "action": action,
                    "symbol": symbol,
                    "amount": amount,
                    "success": success,
                    "result": result.get("message", ""),
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
        return {
            "enabled": self.enabled,
            "running": self._running,
            "interval_minutes": self.interval_minutes,
            "max_trades_per_day": self.max_trades_per_day,
            "daily_trades": self._daily_trades,
            "daily_limit": self.max_trades_per_day,
            "last_analysis_time": self._last_analysis_time,
            "last_analysis_result": self._last_analysis_result[:500] if self._last_analysis_result else "",
            "trade_log": self._trade_log[-10:],  # 最近10条
            # 全时段覆盖状态
            "pre_market_done": self._pre_market_done,
            "lunch_done": self._lunch_done,
            "after_hours_done": self._after_hours_done,
            "pre_market_score": pm.get("score") if pm else None,
            "pre_market_news_count": pm.get("news_count") if pm else None,
            "lunch_score": self._lunch_sentiment.get("score") if self._lunch_sentiment else None,
        }


# 全局实例
ai_scheduler = AIScheduler()
