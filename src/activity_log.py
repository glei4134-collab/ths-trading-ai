"""
统一活动日志模块
记录所有交易操作、策略变更、系统事件，带时间戳和操作者标识。

操作者 (actor) 枚举:
  user         — 用户手动操作
  ai           — AI 大脑决策 (decide / decide_with_ai)
  strategy:<名> — 某个策略触发
  scanner      — 策略扫描器自动执行
  auto_trader  — 自动交易引擎 (止损/止盈/移动止损)
  system       — 系统级事件 (服务启停、账户切换等)
"""

import json
import logging
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# ---- 行为类型枚举 ----

class ActionType:
    # 交易执行
    BUY = "buy"
    SELL = "sell"
    AUTO_BUY = "auto_buy"
    AUTO_SELL = "auto_sell"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"
    CANCEL_ORDER = "cancel_order"
    CANCEL_ALL_ORDERS = "cancel_all_orders"

    # 策略管理
    STRATEGY_WRITE = "strategy_write"
    STRATEGY_INSTALL = "strategy_install"
    STRATEGY_ACTIVATE = "strategy_activate"
    STRATEGY_DEACTIVATE = "strategy_deactivate"
    STRATEGY_REPLACE = "strategy_replace"
    STRATEGY_SIGNAL = "strategy_signal"

    # 扫描器
    SCANNER_START = "scanner_start"
    SCANNER_STOP = "scanner_stop"
    SCANNER_CONFIGURE = "scanner_configure"
    SCANNER_SCAN_ROUND = "scanner_scan_round"

    # 系统
    ACCOUNT_SWITCH = "account_switch"
    SERVICES_START = "services_start"
    SERVICES_STOP = "services_stop"
    CONFIG_CHANGE = "config_change"
    AI_CHAT = "ai_chat"
    AI_ANALYZE = "ai_analyze"

    # 显示名映射
    LABELS = {
        "buy": "手动买入",
        "sell": "手动卖出",
        "auto_buy": "AI买入",
        "auto_sell": "AI卖出",
        "stop_loss": "自动止损",
        "take_profit": "自动止盈",
        "trailing_stop": "移动止损",
        "cancel_order": "撤单",
        "cancel_all_orders": "全部撤单",
        "strategy_write": "AI编写策略",
        "strategy_install": "安装策略",
        "strategy_activate": "启用策略",
        "strategy_deactivate": "停用策略",
        "strategy_replace": "替换策略",
        "strategy_signal": "策略信号",
        "scanner_start": "启动扫描器",
        "scanner_stop": "停止扫描器",
        "scanner_configure": "配置扫描器",
        "scanner_scan_round": "扫描轮次",
        "account_switch": "切换账户",
        "services_start": "启动服务",
        "services_stop": "停止服务",
        "config_change": "配置变更",
        "ai_chat": "AI对话",
        "ai_analyze": "AI分析",
    }

    # 颜色标签 (前端用)
    COLORS = {
        "buy": "green",
        "sell": "red",
        "auto_buy": "blue",
        "auto_sell": "orange",
        "stop_loss": "red",
        "take_profit": "green",
        "trailing_stop": "orange",
        "cancel_order": "gray",
        "cancel_all_orders": "gray",
        "strategy_write": "purple",
        "strategy_install": "purple",
        "strategy_activate": "green",
        "strategy_deactivate": "gray",
        "strategy_replace": "purple",
        "strategy_signal": "blue",
        "scanner_start": "blue",
        "scanner_stop": "gray",
        "scanner_configure": "blue",
        "scanner_scan_round": "blue",
        "account_switch": "gray",
        "services_start": "green",
        "services_stop": "red",
        "config_change": "gray",
        "ai_chat": "purple",
        "ai_analyze": "purple",
    }


class ActivityLog:
    """
    活动日志管理器

    用法:
        from activity_log import activity_log
        activity_log.log(
            action=ActionType.BUY,
            actor="user",
            symbol="000001",
            details={"price": 10.5, "amount": 500, "total": 5250},
            result="success",
            account_id=1,
        )
    """

    def __init__(self, db_path: str = None):
        if db_path is None:
            from paths import DATA_DIR
            db_path = str(DATA_DIR / "trading.db")
        self._db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """创建 activity_log 表"""
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER DEFAULT 1,
                    timestamp TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT 'system',
                    symbol TEXT DEFAULT '',
                    name TEXT DEFAULT '',
                    details TEXT DEFAULT '{}',
                    result TEXT DEFAULT 'success',
                    label TEXT DEFAULT '',
                    color TEXT DEFAULT 'gray'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_al_time ON activity_log(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_al_action ON activity_log(action)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_al_account ON activity_log(account_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_al_symbol ON activity_log(symbol)")
            conn.commit()
        finally:
            conn.close()

    def log(
        self,
        action: str,
        actor: str = "system",
        symbol: str = "",
        name: str = "",
        details: Dict = None,
        result: str = "success",
        account_id: int = None,
    ) -> Dict:
        """
        记录一条活动日志

        Args:
            action:   ActionType 常量 (buy / sell / auto_buy / strategy_write / ...)
            actor:    操作者 ("user" / "ai" / "strategy:MACD" / "scanner" / "auto_trader" / "system")
            symbol:   股票代码 (可选)
            name:     股票名称 / 策略名 (可选)
            details:  附加数据 (JSON 可序列化)
            result:   结果 ("success" / "failed" / "skipped")
            account_id: 账户ID (不传则用活跃账户)

        Returns:
            {"success": True, "id": <log_id>}
        """
        if account_id is None:
            try:
                from db_store import db_store
                account_id = db_store.get_active_account_id()
            except Exception:
                account_id = 1

        now = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        label = ActionType.LABELS.get(action, action)
        color = ActionType.COLORS.get(action, "gray")
        details_json = json.dumps(details or {}, ensure_ascii=False, default=str)

        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """INSERT INTO activity_log
                   (account_id, timestamp, action, actor, symbol, name, details, result, label, color)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (account_id, now, action, actor, symbol, name, details_json, result, label, color)
            )
            conn.commit()
            log_id = cursor.lastrowid
            logger.debug(f"[ActivityLog] {label} | {actor} | {symbol} | {result}")
            return {"success": True, "id": log_id}
        except Exception as e:
            logger.error(f"ActivityLog写入失败: {e}")
            return {"success": False, "message": str(e)}
        finally:
            conn.close()

    def get_logs(
        self,
        account_id: int = None,
        action: str = None,
        actor: str = None,
        symbol: str = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict]:
        """查询活动日志"""
        conn = self._get_conn()
        try:
            query = "SELECT * FROM activity_log WHERE 1=1"
            params = []

            if account_id is not None:
                query += " AND account_id = ?"
                params.append(account_id)
            if action:
                query += " AND action = ?"
                params.append(action)
            if actor:
                query += " AND actor = ?"
                params.append(actor)
            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)

            query += " ORDER BY id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            results = []
            for row in rows:
                d = dict(row)
                d["details"] = json.loads(d.get("details", "{}"))
                results.append(d)
            return results
        finally:
            conn.close()

    def get_stats(self, account_id: int = None) -> Dict:
        """获取活动统计"""
        conn = self._get_conn()
        try:
            where = "WHERE 1=1"
            params = []
            if account_id is not None:
                where += " AND account_id = ?"
                params.append(account_id)

            # 总数
            total = conn.execute(f"SELECT COUNT(*) as c FROM activity_log {where}", params).fetchone()["c"]

            # 按 action 分组
            rows = conn.execute(
                f"SELECT action, label, color, COUNT(*) as count FROM activity_log {where} GROUP BY action ORDER BY count DESC",
                params
            ).fetchall()
            by_action = [dict(r) for r in rows]

            # 按 actor 分组
            rows2 = conn.execute(
                f"SELECT actor, COUNT(*) as count FROM activity_log {where} GROUP BY actor ORDER BY count DESC",
                params
            ).fetchall()
            by_actor = [dict(r) for r in rows2]

            # 今天
            today = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d")
            today_count = conn.execute(
                f"SELECT COUNT(*) as c FROM activity_log {where} AND timestamp LIKE ?",
                params + [f"{today}%"]
            ).fetchone()["c"]

            return {
                "total": total,
                "today": today_count,
                "by_action": by_action,
                "by_actor": by_actor,
            }
        finally:
            conn.close()

    def clear(self, account_id: int = None):
        """清空日志"""
        conn = self._get_conn()
        try:
            if account_id is not None:
                conn.execute("DELETE FROM activity_log WHERE account_id = ?", (account_id,))
            else:
                conn.execute("DELETE FROM activity_log")
            conn.commit()
        finally:
            conn.close()


# 全局实例
activity_log = ActivityLog()
