"""
通知管理模块
管理信号通知、系统通知，支持 SSE 实时推送
每日交易汇总 + 盘前提醒
"""

import json
import logging
import queue
import threading
import time as _time
from typing import Dict, List, Optional, Callable
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import date, time, timedelta

logger = logging.getLogger(__name__)


class NotificationManager:
    """通知管理器"""

    def __init__(self):
        self._notifications: List[Dict] = []
        self._callbacks: List[Callable] = []
        self._sse_queues: List[queue.Queue] = []
        self._max_notifications = 200

    def add(self, notification: Dict):
        """添加通知"""
        notification["id"] = len(self._notifications)
        notification["read"] = False
        notification["created_at"] = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        self._notifications.append(notification)

        # 保留最近 N 条
        if len(self._notifications) > self._max_notifications:
            self._notifications = self._notifications[-self._max_notifications:]

        # 触发回调
        for cb in self._callbacks:
            try:
                cb(notification)
            except Exception:
                pass

        # 推送到 SSE 队列
        self._push_sse(notification)

        logger.info(f"新通知: [{notification.get('level', 'info')}] {notification.get('title', '')}")

    def _push_sse(self, notification: Dict):
        """推送到所有 SSE 客户端"""
        dead_queues = []
        for q in self._sse_queues:
            try:
                q.put_nowait(notification)
            except queue.Full:
                dead_queues.append(q)
        for q in dead_queues:
            self._sse_queues.remove(q)

    def get_all(self, limit: int = 50) -> List[Dict]:
        """获取通知列表"""
        return list(reversed(self._notifications[-limit:]))

    def get_unread(self) -> List[Dict]:
        """获取未读通知"""
        return [n for n in self._notifications if not n["read"]]

    def get_unread_count(self) -> int:
        """获取未读数量"""
        return sum(1 for n in self._notifications if not n["read"])

    def mark_read(self, ids: List[int] = None):
        """标记已读，ids=None 标记全部"""
        if ids is None:
            for n in self._notifications:
                n["read"] = True
        else:
            for n in self._notifications:
                if n["id"] in ids:
                    n["read"] = True

    def add_callback(self, callback: Callable):
        """添加通知回调"""
        self._callbacks.append(callback)

    def register_sse(self) -> queue.Queue:
        """注册 SSE 客户端，返回一个 Queue"""
        q = queue.Queue(maxsize=100)
        self._sse_queues.append(q)
        return q

    def unregister_sse(self, q: queue.Queue):
        """注销 SSE 客户端"""
        if q in self._sse_queues:
            self._sse_queues.remove(q)

    def clear(self):
        """清除所有通知"""
        self._notifications.clear()

    def add_signal_notification(self, signal: Dict):
        """添加交易信号通知"""
        is_buy = signal.get("signal", "") in ("BUY", "STRONG_BUY", "WEAK_BUY")
        self.add({
            "type": "signal",
            "level": "success" if is_buy else "warning",
            "title": f"{'买入' if is_buy else '卖出'}信号",
            "message": f"{signal.get('name', '')}({signal.get('symbol', '')}) "
                       f"{signal.get('signal', '')} 评分:{signal.get('score', 0)}",
            "data": signal
        })

    def add_system_notification(self, title: str, message: str, level: str = "info"):
        """添加系统通知"""
        self.add({
            "type": "system",
            "level": level,
            "title": title,
            "message": message
        })

    def add_daily_summary(self, summary: Dict):
        """添加每日交易汇总通知"""
        date_str = summary.get("date", date.today().isoformat())
        total_profit = summary.get("total_profit", 0)
        trade_count = summary.get("trade_count", 0)
        buy_count = summary.get("buy_count", 0)
        sell_count = summary.get("sell_count", 0)
        total_assets = summary.get("total_assets", 0)
        initial = summary.get("initial_balance", 1000000)
        return_rate = summary.get("return_rate", 0)
        stop_loss_triggered = summary.get("stop_loss_count", 0)
        take_profit_triggered = summary.get("take_profit_count", 0)
        holdings = summary.get("holdings", [])

        # 盈亏 emoji
        pnl_icon = "📈" if total_profit >= 0 else "📉"
        pnl_sign = "+" if total_profit >= 0 else ""

        lines = [
            f"📅 {date_str} 收盘汇总",
            f"",
            f"💰 总资产: ¥{total_assets:,.0f} ({pnl_sign}{return_rate:.2f}%)",
            f"{pnl_icon} 当日盈亏: {pnl_sign}¥{total_profit:,.0f}",
            f"🔄 交易: {trade_count}笔 (买{buy_count}/卖{sell_count})",
        ]

        if stop_loss_triggered > 0 or take_profit_triggered > 0:
            lines.append(f"⚡ 触发: 止损{stop_loss_triggered}次 / 止盈{take_profit_triggered}次")

        if holdings:
            lines.append(f"")
            lines.append(f"📦 持仓 ({len(holdings)}只):")
            for h in holdings[:5]:
                pnl = h.get("profit_rate", 0)
                sign = "+" if pnl >= 0 else ""
                lines.append(f"  {h.get('name', h.get('symbol', '?'))} {sign}{pnl:.2f}%")
            if len(holdings) > 5:
                lines.append(f"  ...共{len(holdings)}只")

        self.add({
            "type": "daily_summary",
            "level": "success" if total_profit >= 0 else "warning",
            "title": f"📊 {date_str} 每日汇总",
            "message": "\n".join(lines),
            "data": summary
        })

    def add_market_open_reminder(self, reminder: Dict):
        """添加开盘前提醒"""
        watchlist = reminder.get("watchlist", [])
        signals = reminder.get("pending_signals", [])

        lines = [f"🔔 今日盘前提醒"]

        if watchlist:
            lines.append(f"")
            lines.append(f"👀 关注: {', '.join(watchlist[:8])}")

        if signals:
            lines.append(f"")
            lines.append(f"📡 待处理信号:")
            for sig in signals[:5]:
                action = "买" if "BUY" in sig.get("signal", "") else "卖"
                lines.append(f"  {sig.get('name', sig.get('symbol', '?'))} → {action}")

        lines.append(f"")
        lines.append(f"⏰ 交易时间: 09:30-11:30 / 13:00-15:00")

        self.add({
            "type": "market_reminder",
            "level": "info",
            "title": "🔔 盘前提醒",
            "message": "\n".join(lines),
            "data": reminder
        })


# 全局实例
notification_manager = NotificationManager()


class DailySummaryScheduler:
    """
    每日交易汇总调度器

    - 14:55 收盘前: 生成当日交易汇总
    - 09:15 盘前: 推送关注股票和待处理信号
    """

    def __init__(self):
        self._thread = None
        self._running = False
        self._daily_sent = set()  # 已发送的日期集合，防重复
        self._morning_sent = set()

    def start(self):
        """启动调度器"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("每日汇总调度器已启动")

    def stop(self):
        """停止调度器"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self):
        """主循环: 每 60 秒检查一次是否到了推送时间"""
        while self._running:
            try:
                now = datetime.now(ZoneInfo('Asia/Shanghai'))
                today = now.date().isoformat()
                current_time = now.time()

                # 收盘汇总: 14:55 - 15:05 之间，每天只发一次
                if time(14, 55) <= current_time <= time(15, 5):
                    if today not in self._daily_sent:
                        self._send_daily_summary(today)
                        self._daily_sent.add(today)
                        # 清理旧日期
                        cutoff = (now - timedelta(days=3)).date().isoformat()
                        self._daily_sent = {d for d in self._daily_sent if d >= cutoff}

                # 盘前提醒: 09:15 - 09:25，每天只发一次
                elif time(9, 15) <= current_time <= time(9, 25):
                    if today not in self._morning_sent:
                        self._send_market_reminder(today)
                        self._morning_sent.add(today)
                        cutoff = (now - timedelta(days=3)).date().isoformat()
                        self._morning_sent = {d for d in self._morning_sent if d >= cutoff}

            except Exception as e:
                logger.error(f"每日汇总调度异常: {e}")

            _time.sleep(60)

    def _send_daily_summary(self, today: str):
        """生成并发送每日交易汇总"""
        try:
            from db_store import db_store
            from data_store import data_store

            account_id = data_store.account_id
            account = data_store.account

            # 获取今日交易记录
            trades = db_store.get_trades(limit=200, account_id=account_id)
            today_trades = [t for t in trades if t.get("time", "").startswith(today)]

            buy_count = sum(1 for t in today_trades if t.get("action") == "buy")
            sell_count = sum(1 for t in today_trades if t.get("action") == "sell")
            trade_count = len(today_trades)

            # 计算当日盈亏（通过持仓对比昨日快照）
            balance = account.get("balance", 1000000)
            initial = account.get("initial_balance", 1000000)

            # 获取持仓总市值
            positions = data_store.get_positions()
            market_value = sum(
                p.get("current_price", p.get("avg_price", 0)) * p.get("amount", 0)
                for p in positions
            )
            total_assets = balance + market_value
            total_profit = total_assets - initial
            return_rate = (total_profit / initial * 100) if initial > 0 else 0

            # 获取止损/止盈触发次数
            stop_loss_count = sum(1 for t in today_trades if "止损" in str(t.get("notes", "")))
            take_profit_count = sum(1 for t in today_trades if "止盈" in str(t.get("notes", "")))

            # 持仓信息
            holdings = []
            for p in positions:
                pnl = p.get("current_price", 0)
                avg = p.get("avg_price", 0)
                rate = ((pnl / avg - 1) * 100) if avg > 0 else 0
                holdings.append({
                    "symbol": p.get("symbol", ""),
                    "name": p.get("name", ""),
                    "profit_rate": round(rate, 2)
                })

            summary = {
                "date": today,
                "trade_count": trade_count,
                "buy_count": buy_count,
                "sell_count": sell_count,
                "total_profit": round(total_profit, 0),
                "total_assets": round(total_assets, 0),
                "initial_balance": initial,
                "return_rate": round(return_rate, 2),
                "stop_loss_count": stop_loss_count,
                "take_profit_count": take_profit_count,
                "holdings": holdings
            }

            # 保存账户快照
            db_store.save_snapshot(
                balance=balance,
                market_value=market_value,
                total_assets=total_assets,
                total_profit=total_profit,
                position_count=len(positions),
                account_id=account_id
            )

            notification_manager.add_daily_summary(summary)
            logger.info(f"每日汇总已推送: 盈亏 {total_profit:+.0f}, 交易 {trade_count}笔")

        except Exception as e:
            logger.error(f"生成每日汇总失败: {e}")

    def _send_market_reminder(self, today: str):
        """生成并发送盘前提醒"""
        try:
            from db_store import db_store
            from data_store import data_store

            account_id = data_store.account_id

            # 持仓关注列表
            positions = data_store.get_positions()
            watchlist = [p.get("name", p.get("symbol", "")) for p in positions[:8]]

            # 获取未处理的信号
            signals = db_store.conn.execute(
                "SELECT * FROM signals WHERE account_id = ? AND result IS NULL ORDER BY timestamp DESC LIMIT 5",
                (account_id,)
            ).fetchall()
            pending_signals = [dict(s) for s in signals]

            account = data_store.account
            balance = account.get("balance", 1000000)
            initial = account.get("initial_balance", 1000000)
            market_value = sum(
                p.get("current_price", p.get("avg_price", 0)) * p.get("amount", 0)
                for p in positions
            )
            total_assets = balance + market_value
            return_rate = ((total_assets / initial - 1) * 100) if initial > 0 else 0

            reminder = {
                "date": today,
                "watchlist": watchlist,
                "pending_signals": pending_signals,
                "total_assets": round(total_assets, 0),
                "return_rate": round(return_rate, 2),
                "position_count": len(positions)
            }

            notification_manager.add_market_open_reminder(reminder)
            logger.info(f"盘前提醒已推送: 持仓 {len(positions)} 只, 待处理信号 {len(pending_signals)} 个")

        except Exception as e:
            logger.error(f"生成盘前提醒失败: {e}")

    def force_daily_summary(self):
        """手动触发每日汇总（用于测试/补发）"""
        today = date.today().isoformat()
        self._daily_sent.discard(today)  # 允许重发
        self._send_daily_summary(today)

    def force_market_reminder(self):
        """手动触发盘前提醒"""
        today = date.today().isoformat()
        self._morning_sent.discard(today)
        self._send_market_reminder(today)


# 全局调度器实例
daily_scheduler = DailySummaryScheduler()
