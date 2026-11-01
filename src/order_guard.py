"""
统一下单闸门：所有买卖必须先过这里。

硬约束（拒绝则不得记成交）：
- A 股整手 100 股
- 委托时段含集合竞价 09:15-11:30 / 13:00-15:00
- 主板 10% / 创业板与科创板 20% 涨跌停
- 团队赛单票仓位硬顶 35%
- 卖出不超过可卖股数（T+1）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time
from typing import Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TEAM_MAX_SINGLE_PCT = 0.35
TEAM_INITIAL_BALANCE = 200000.0
LOT_SIZE = 100


def board_limit_pct(symbol: str) -> float:
    """返回该代码适用的涨跌幅上限（小数）。"""
    s = (symbol or "").strip()
    if s.startswith(("300", "301", "688", "689")):
        return 0.20
    if s.startswith(("8", "4")) and len(s) == 6:
        return 0.30
    return 0.10


def round_lot(amount: int, unit: int = LOT_SIZE) -> int:
    try:
        qty = int(amount)
    except (TypeError, ValueError):
        return 0
    if qty <= 0:
        return 0
    return (qty // unit) * unit


def is_order_session(now: Optional[datetime] = None) -> bool:
    """可报单时段：含 09:15 集合竞价，不含午休。"""
    from competition import competition_manager

    if now is None:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if not competition_manager.is_trading_day(now.date()):
        return False
    current = now.time()
    if getattr(current, "tzinfo", None) is not None:
        current = current.replace(tzinfo=None)
    morning = time(9, 15) <= current <= time(11, 30)
    afternoon = time(13, 0) <= current <= time(15, 0)
    return morning or afternoon


@dataclass
class GuardResult:
    allowed: bool
    amount: int
    message: str
    code: str = "OK"

    def to_dict(self) -> Dict:
        return {
            "allowed": self.allowed,
            "amount": self.amount,
            "message": self.message,
            "code": self.code,
        }


def _reject(code: str, message: str, amount: int = 0) -> GuardResult:
    return GuardResult(False, amount, message, code)


def validate_pre_trade(
    symbol: str,
    action: str,
    price: float,
    amount: int,
    *,
    total_assets: float = 0.0,
    available_cash: float = 0.0,
    existing_position_value: float = 0.0,
    existing_shares: int = 0,
    available_shares: Optional[int] = None,
    change_pct: Optional[float] = None,
    quote: Optional[Dict] = None,
    now: Optional[datetime] = None,
    skip_session_check: bool = False,
    max_single_pct: float = TEAM_MAX_SINGLE_PCT,
) -> GuardResult:
    """
    下单前硬校验。买入在触达 35% 时尽量下修到合法手数；无法买满 1 手则拒绝。
    """
    action = (action or "").lower().strip()
    symbol = (symbol or "").strip()
    if action not in ("buy", "sell"):
        return _reject("BAD_ACTION", f"不支持的动作: {action}")
    if not symbol or len(symbol) < 6:
        return _reject("BAD_SYMBOL", f"无效证券代码: {symbol}")
    try:
        price = float(price or 0)
        amount = int(amount or 0)
    except (TypeError, ValueError):
        return _reject("BAD_QTY", "价格或数量非法")
    if price <= 0:
        return _reject("BAD_PRICE", f"{symbol} 价格无效: {price}")

    if not skip_session_check and not is_order_session(now):
        return _reject("OUT_OF_SESSION", "当前不在可报单时段（09:15-11:30 / 13:00-15:00）")

    amount = round_lot(amount)
    if amount < LOT_SIZE:
        return _reject("LOT_SIZE", f"委托数量必须是 {LOT_SIZE} 股整数倍，当前不足 1 手")

    q = quote or {}
    if change_pct is None:
        try:
            change_pct = float(q.get("change_pct")) if q.get("change_pct") is not None else None
        except (TypeError, ValueError):
            change_pct = None
        if change_pct is None:
            try:
                last = float(q.get("price") or price)
                pre = float(q.get("pre_close") or 0)
                if pre > 0:
                    change_pct = (last / pre - 1.0) * 100.0
            except (TypeError, ValueError):
                change_pct = None

    limit_pct = board_limit_pct(symbol) * 100.0
    if change_pct is not None:
        if action == "buy" and change_pct >= (limit_pct - 0.05):
            return _reject("LIMIT_UP", f"{symbol} 涨停附近(+{change_pct:.2f}% / 板 {limit_pct:.0f}%)，禁止买入")
        if action == "sell" and change_pct <= -(limit_pct - 0.05):
            return _reject("LIMIT_DOWN", f"{symbol} 跌停附近({change_pct:.2f}% / 板 {limit_pct:.0f}%)，禁止卖出")

    volume = q.get("volume")
    try:
        if volume is not None and int(volume) == 0 and q.get("price"):
            return _reject("SUSPENDED", f"{symbol} 成交量为 0，可能停牌")
    except (TypeError, ValueError):
        pass

    hard_cap = min(float(max_single_pct or TEAM_MAX_SINGLE_PCT), TEAM_MAX_SINGLE_PCT)

    if action == "buy":
        cost = price * amount
        if available_cash > 0 and cost > available_cash * 0.995:
            afford = round_lot(int((available_cash * 0.95) / price))
            if afford < LOT_SIZE:
                return _reject("NO_CASH", f"可用资金不足 1 手（现金 {available_cash:.2f}）")
            amount = min(amount, afford)
        if total_assets > 0:
            room = hard_cap * total_assets - float(existing_position_value or 0)
            if room < price * LOT_SIZE:
                return _reject(
                    "POSITION_CAP",
                    f"{symbol} 单票仓位已达团队赛红线 {hard_cap:.0%}，禁止加仓",
                )
            max_shares = round_lot(int(room / price))
            if amount > max_shares:
                amount = max_shares
            if amount < LOT_SIZE:
                return _reject("POSITION_CAP", f"{symbol} 加仓后将超过单票 {hard_cap:.0%}，已无整手空间")

    elif action == "sell":
        hold = int(existing_shares or 0)
        sellable = hold if available_shares is None else int(available_shares)
        if sellable < LOT_SIZE and hold > 0 and available_shares is not None and available_shares == 0:
            return _reject("T_PLUS_1", f"{symbol} 今日买入或无可卖余额，T+1 限制无法卖出")
        if sellable <= 0:
            return _reject("NO_POSITION", f"未持有可卖 {symbol}")
        if amount > sellable:
            amount = round_lot(sellable)
        if amount < 1:
            return _reject("NO_POSITION", f"{symbol} 可卖数量不足")
        # 不足一手但有零股（持仓清理）允许按可卖数量卖出
        if amount < LOT_SIZE and sellable < LOT_SIZE:
            amount = sellable

    return GuardResult(True, amount, "通过", "OK")


def fetch_quote_silent(symbol: str) -> Dict:
    try:
        from fast_data import fetch_realtime_single
        return fetch_realtime_single(symbol) or {}
    except Exception:
        return {}
