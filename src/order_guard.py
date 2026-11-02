"""
统一下单闸门与穿透式极端风控引擎 (Order Guard & Extreme Risk Controller)
由【极端风控与穿透式防御专家 (extreme-risk-controller)】负责维护。

硬约束（拒绝则不得记成交）：
- A 股整手 100 股
- 委托时段含集合竞价 09:15-11:30 / 13:00-15:00
- 主板 10% / 创业板与科创板 20% / 北交所 30% 涨跌停
- 团队赛单票仓位硬顶 35%
- 申万一级单一行业总敞口硬顶 40% (35% 触发黄色预警)
- 卖出不超过可卖股数（T+1）

高阶机构级动态风控算子：
1. 时变半凯利 (Fractional Half-Kelly) + ATR_14 波动率双轨头寸动态缩放
2. 候选股票池高相关性剪枝 (Pairwise Return Correlation Pruning, r > 0.80)
3. Clayton Copula 下尾极值共振相关性监控 (Lower-Tail Dependence lambda_L = 2^(-1/theta))
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo
import numpy as np

logger = logging.getLogger(__name__)

TEAM_MAX_SINGLE_PCT = 0.35
SECTOR_MAX_HARD_CAP = 0.40
SECTOR_WARN_THRESHOLD = 0.35
CORRELATION_PRUNE_THRESHOLD = 0.80
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


def calculate_dynamic_position_size(
    total_equity: float,
    price: float,
    atr_14: Optional[float] = None,
    win_rate: float = 0.55,
    payoff_ratio: float = 1.5,
    risk_per_trade_pct: float = 0.015,
    max_single_pct: float = TEAM_MAX_SINGLE_PCT,
    min_single_pct: float = 0.05
) -> Dict[str, Union[int, float, str]]:
    """
    时变半凯利 (Fractional Half-Kelly) + ATR_14 波动率双轨动态头寸计算器。
    
    数学公式：
    1. 完整凯利比例: f_kelly = p - (1 - p) / b
    2. 半凯利比例:   f_half = clamp(0.5 * f_kelly, min_single_pct, max_single_pct)
    3. ATR 风险平价股数: Shares_ATR = floor( (Equity * RiskPct) / (2 * ATR_14 * 100) ) * 100
    4. 最终推荐股数: min(Shares_ATR, Shares_Kelly)
    """
    if total_equity <= 0 or price <= 0:
        return {
            "recommended_shares": 0,
            "recommended_value": 0.0,
            "position_pct": 0.0,
            "half_kelly_pct": 0.0,
            "atr_shares": 0,
            "kelly_shares": 0,
            "binding_constraint": "INVALID_INPUT"
        }

    p = max(0.01, min(0.99, float(win_rate)))
    b = max(0.1, float(payoff_ratio))

    # 1. 计算完整凯利与保守半凯利
    full_kelly = p - (1.0 - p) / b
    if full_kelly <= 0:
        half_kelly = float(min_single_pct)
    else:
        half_kelly = max(float(min_single_pct), min(0.5 * full_kelly, float(max_single_pct)))

    kelly_budget = total_equity * half_kelly
    kelly_shares = round_lot(int(kelly_budget / price))

    # 2. 计算 2*ATR 止损距离下的波动率风险预算股数
    eff_atr = float(atr_14) if (atr_14 is not None and atr_14 > 0) else (price * 0.025)
    stop_distance = max(2.0 * eff_atr, price * 0.01)
    risk_budget = total_equity * max(0.002, min(0.05, float(risk_per_trade_pct)))
    raw_atr_shares = int(risk_budget / stop_distance)
    # 同时受单票硬上限约束
    hard_cap_shares = round_lot(int((total_equity * float(max_single_pct)) / price))
    atr_shares = min(round_lot(raw_atr_shares), hard_cap_shares)

    # 3. 双轨取安全下界
    if atr_shares <= kelly_shares:
        rec_shares = atr_shares
        binding = "ATR_VOLATILITY_BUDGET"
    else:
        rec_shares = kelly_shares
        binding = "HALF_KELLY_CRITERION"

    rec_val = float(rec_shares) * float(price)
    pos_pct = rec_val / float(total_equity) if total_equity > 0 else 0.0

    return {
        "recommended_shares": int(rec_shares),
        "recommended_value": round(rec_val, 2),
        "position_pct": round(pos_pct, 4),
        "half_kelly_pct": round(half_kelly, 4),
        "atr_shares": int(atr_shares),
        "kelly_shares": int(kelly_shares),
        "binding_constraint": binding
    }


def prune_correlated_candidates(
    candidates: List[Dict],
    returns_map: Dict[str, Union[List[float], np.ndarray]],
    max_corr: float = CORRELATION_PRUNE_THRESHOLD
) -> Tuple[List[Dict], List[Dict]]:
    """
    候选池两两收益率高相关性剪枝 (Pairwise Correlation Pruning)。
    按综合评分 (score) 降序排列，若某候选股与已入选高分标的近30日收益率相关系数 r > max_corr (默认0.80)，
    则剔除低分同质化标的，保留高分龙头。
    
    Returns:
        (retained_candidates, pruned_records)
    """
    if not candidates:
        return [], []

    # 按 score 降序排列
    sorted_cands = sorted(
        candidates,
        key=lambda x: float(x.get("score", x.get("composite_score", 0.0))),
        reverse=True
    )

    retained: List[Dict] = []
    pruned: List[Dict] = []

    for cand in sorted_cands:
        sym = str(cand.get("symbol") or cand.get("code") or "").strip()
        if not sym or sym not in returns_map:
            retained.append(cand)
            continue

        r_cand = np.asarray(returns_map[sym], dtype=float)
        if len(r_cand) < 5:
            retained.append(cand)
            continue

        is_redundant = False
        for kept in retained:
            kept_sym = str(kept.get("symbol") or kept.get("code") or "").strip()
            if not kept_sym or kept_sym not in returns_map:
                continue
            r_kept = np.asarray(returns_map[kept_sym], dtype=float)
            min_len = min(len(r_cand), len(r_kept))
            if min_len < 5:
                continue

            a = r_cand[-min_len:]
            b = r_kept[-min_len:]
            std_a = float(np.std(a))
            std_b = float(np.std(b))
            if std_a > 1e-9 and std_b > 1e-9:
                corr_val = float(np.corrcoef(a, b)[0, 1])
                if not math.isnan(corr_val) and corr_val > max_corr:
                    is_redundant = True
                    pruned.append({
                        "pruned_symbol": sym,
                        "kept_symbol": kept_sym,
                        "correlation": round(corr_val, 4),
                        "reason": f"与更高评分标的 {kept_sym} 收益率相关性过高 (r={corr_val:.2f} > {max_corr:.2f})"
                    })
                    break

        if not is_redundant:
            retained.append(cand)

    return retained, pruned


def estimate_clayton_lower_tail_dependence(
    returns_matrix: np.ndarray
) -> Dict[str, Union[float, bool, str]]:
    """
    基于 Clayton Copula 估计投资组合的下尾极值共振相关系数 (Lower-Tail Dependence):
        theta = 2 * tau_Kendall / (1 - tau_Kendall)
        lambda_L = 2^(-1 / theta)
    当全市场暴跌引发流动性挤兑时，若 lambda_L > 0.65，触发系统级去杠杆预警。
    """
    if returns_matrix is None or returns_matrix.ndim != 2 or returns_matrix.shape[1] < 2:
        return {
            "lambda_lower_tail": 0.0,
            "clayton_theta": 0.0,
            "avg_kendall_tau": 0.0,
            "tail_Contagion_alert": False,
            "leverage_scale_factor": 1.0
        }

    T, N = returns_matrix.shape
    if T < 5:
        return {
            "lambda_lower_tail": 0.0,
            "clayton_theta": 0.0,
            "avg_kendall_tau": 0.0,
            "tail_Contagion_alert": False,
            "leverage_scale_factor": 1.0
        }

    # 计算两两资产的秩相关近似 Kendall's tau = (2 / pi) * arcsin(rho_Spearman)
    ranks = np.argsort(np.argsort(returns_matrix, axis=0), axis=0).astype(float)
    spearman_corr = np.corrcoef(ranks, rowvar=False)
    off_diag_vals = []
    for i in range(N):
        for j in range(i + 1, N):
            val = spearman_corr[i, j]
            if not math.isnan(val):
                off_diag_vals.append(float(np.clip(val, -0.99, 0.99)))

    if not off_diag_vals:
        avg_rho = 0.0
    else:
        avg_rho = float(np.mean(off_diag_vals))

    kendall_tau = max(0.0, (2.0 / math.pi) * math.asin(max(-1.0, min(1.0, avg_rho))))
    if kendall_tau <= 1e-4:
        theta = 0.0
        lambda_l = 0.0
    else:
        theta = (2.0 * kendall_tau) / max(1.0 - kendall_tau, 0.02)
        lambda_l = math.pow(2.0, -1.0 / theta)

    alert = bool(lambda_l > 0.65)
    scale_factor = round(max(0.4, 1.0 - (lambda_l - 0.50) * 1.2), 2) if lambda_l > 0.50 else 1.0

    return {
        "lambda_lower_tail": round(float(lambda_l), 4),
        "clayton_theta": round(float(theta), 4),
        "avg_kendall_tau": round(float(kendall_tau), 4),
        "tail_contagion_alert": alert,
        "leverage_scale_factor": scale_factor
    }


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
    sector: Optional[str] = None,
    existing_sector_value: float = 0.0,
    max_sector_pct: float = SECTOR_MAX_HARD_CAP,
) -> GuardResult:
    """
    下单前硬校验：
    1. 买入在触达单票 35% 时自动下修到合法手数；无法买满 1 手则拒绝 (POSITION_CAP)。
    2. 买入在触达申万一级行业 40% 硬顶时自动下修或拒绝 (SECTOR_CAP)；超 35% 附加预警。
    3. 卖出严守 T+1 可卖股数限制。
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
    sector_cap = min(float(max_sector_pct or SECTOR_MAX_HARD_CAP), SECTOR_MAX_HARD_CAP)
    warn_suffix = ""

    if action == "buy":
        cost = price * amount
        if available_cash > 0 and cost > available_cash * 0.995:
            afford = round_lot(int((available_cash * 0.95) / price))
            if afford < LOT_SIZE:
                return _reject("NO_CASH", f"可用资金不足 1 手（现金 {available_cash:.2f}）")
            amount = min(amount, afford)

        if total_assets > 0:
            # 1. 单票 35% 仓位硬顶检查
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

            # 2. 申万一级行业 40% 敞口硬顶检查 (35% 预警)
            if sector or existing_sector_value > 0:
                sec_name = sector or "当前行业"
                sec_room = sector_cap * total_assets - float(existing_sector_value or 0)
                if sec_room < price * LOT_SIZE:
                    return _reject(
                        "SECTOR_CAP",
                        f"{symbol} 所属行业[{sec_name}]敞口已达 {sector_cap:.0%} 硬截断红线，禁止加仓",
                    )
                max_sec_shares = round_lot(int(sec_room / price))
                if amount > max_sec_shares:
                    amount = max_sec_shares
                if amount < LOT_SIZE:
                    return _reject(
                        "SECTOR_CAP",
                        f"{symbol} 加仓后所属行业[{sec_name}]将超过 {sector_cap:.0%} 红线，已无整手空间",
                    )
                post_sec_pct = (float(existing_sector_value or 0) + amount * price) / total_assets
                if post_sec_pct >= SECTOR_WARN_THRESHOLD:
                    warn_suffix = f" [SECTOR_WARN: 行业[{sec_name}]敞口达 {post_sec_pct:.1%}，已超35%预警线]"

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

    return GuardResult(True, amount, f"通过{warn_suffix}", "OK")


def fetch_quote_silent(symbol: str) -> Dict:
    try:
        from fast_data import fetch_realtime_single
        return fetch_realtime_single(symbol) or {}
    except Exception:
        return {}
