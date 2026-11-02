"""
高阶算法交易与最优清算执行模块 (Algorithmic Execution & Optimal Liquidation Engine)
由【高阶算法执行与最优清算专家 (algorithmic-execution-specialist)】与
【微观盘口动力学与排单专家 (microstructure-latency-guard)】联合打造。

核心模型：
1. Almgren-Chriss (2001) 最优清算轨迹模型 (Optimal Execution Trajectory)：
   - 在市场冲击成本 (Permanent Impact gamma & Temporary Impact eta) 与持仓价格波动风险 (Inventory Variance sigma^2) 之间求解欧拉-拉格朗日帕累托最优路径。
   - 导出双曲正弦轨迹 x_j = sinh(kappa * (T - t_j)) / sinh(kappa * T) * X_0。
2. 日内双峰 U 型成交量分布下的动态残差 VWAP 与自适应 TWAP 拆单器：
   - 结合 A 股 240 分钟典型双峰 U 型成交量先验，根据实时市场成交量偏离残差动态调节切片大小，严格将参与率 (Participation Rate) 锁定在 [5%, 15%] 安全走廊内。
3. Kyle's Lambda 平方根定律瞬时市场冲击滑点模型 (Square-Root Law of Market Impact)：
   - Delta P / P = Y * sigma_daily * sqrt(Q / V_ADV)
4. 被动 Maker 冰山挂单规划器 (Pegged Iceberg Order Planner)。
"""

import math
import logging
from typing import Dict, List, Optional, Tuple, Union
import numpy as np

logger = logging.getLogger(__name__)

LOT_SIZE = 100


def _round_lot(shares: float, allow_odd_final: bool = False) -> int:
    qty = int(round(shares))
    if qty <= 0:
        return 0
    if allow_odd_final and qty < LOT_SIZE:
        return qty
    return (qty // LOT_SIZE) * LOT_SIZE


def estimate_kyle_lambda_slippage(
    order_shares: int,
    price: float,
    daily_volume_shares: float,
    daily_volatility: float = 0.025,
    kyle_coeff: float = 0.65
) -> Dict[str, Union[float, bool, str]]:
    """
    平方根定律瞬时市场冲击滑点模型 (Square-Root Law / Kyle's Lambda):
        Impact_pct = Y * sigma_daily * sqrt(Order_Shares / ADV_Shares)
    
    Args:
        order_shares: 拟委托股数 Q
        price: 当前价格 P
        daily_volume_shares: 日均成交股数 V_ADV (若传入成交额可换算，此处统一为股数)
        daily_volatility: 日收益率波动率 sigma_daily (默认 2.5%)
        kyle_coeff: 市场冲击常数 Y (A股实证通常在 0.5 ~ 0.8 之间，默认 0.65)
    Returns:
        {
            "slippage_pct": float,      # 预计冲击滑点百分比 (例如 0.0032 表示 0.32%)
            "slippage_bps": float,      # 预计冲击滑点基点数 (例如 32.0 bps)
            "impact_cost_rmb": float,   # 预计冲击损耗金额 (元)
            "participation_rate": float,# 占日均成交量比例
            "requires_algo_split": bool,# 是否超过单笔冲击阈值需启用算法拆单
            "warning": str
        }
    """
    if order_shares <= 0 or price <= 0:
        return {
            "slippage_pct": 0.0,
            "slippage_bps": 0.0,
            "impact_cost_rmb": 0.0,
            "participation_rate": 0.0,
            "requires_algo_split": False,
            "warning": ""
        }

    adv = max(float(daily_volume_shares or 0.0), float(order_shares * 2.0), 10000.0)
    sigma = max(float(daily_volatility or 0.025), 0.005)
    participation = float(order_shares) / adv

    # 平方根冲击公式
    slippage_pct = float(kyle_coeff * sigma * math.sqrt(participation))
    # 极端低流动性惩罚：若单笔占日成交量超过 10%，附加非线性凸性惩罚
    if participation > 0.10:
        slippage_pct *= (1.0 + 2.0 * (participation - 0.10))

    slippage_pct = min(slippage_pct, 0.10)  # 封顶涨跌停幅度
    slippage_bps = slippage_pct * 10000.0
    order_value = float(order_shares) * float(price)
    impact_cost = order_value * slippage_pct

    requires_split = bool(slippage_bps >= 35.0 or participation >= 0.02)
    warning = ""
    if slippage_bps >= 80.0:
        warning = f"严重流动性枯竭预警: 预计冲击滑点高达 {slippage_bps:.1f} bps (占ADV {participation*100:.2f}%)，严禁单笔市价直砸！"
    elif requires_split:
        warning = f"中等市场冲击 ({slippage_bps:.1f} bps)，建议启用 Almgren-Chriss 或残差 VWAP 拆单执行。"

    return {
        "slippage_pct": round(slippage_pct, 6),
        "slippage_bps": round(slippage_bps, 2),
        "impact_cost_rmb": round(impact_cost, 2),
        "participation_rate": round(participation, 5),
        "requires_algo_split": requires_split,
        "warning": warning
    }


class AlmgrenChrissExecutor:
    """
    Almgren & Chriss (2001) 最优执行与清算轨迹规划器
    
    最小化目标泛函:
        U(x) = E[Cost(x)] + lambda * Var[Cost(x)]
    曲率参数:
        kappa = sqrt(lambda * sigma^2 / eta)
    最优离散剩余头寸轨迹:
        x_j = X_0 * sinh(kappa * (T - t_j)) / sinh(kappa * T)
    """

    def __init__(
        self,
        risk_aversion: float = 1e-5,
        permanent_impact_gamma: float = 2.5e-7,
        temporary_impact_eta: float = 2.5e-6
    ):
        self.risk_aversion = max(float(risk_aversion), 1e-9)
        self.gamma = max(float(permanent_impact_gamma), 1e-10)
        self.eta = max(float(temporary_impact_eta), 1e-10)

    def plan_trajectory(
        self,
        total_shares: int,
        price: float,
        horizon_minutes: float = 30.0,
        n_slices: int = 6,
        daily_volatility: float = 0.025,
        action: str = "sell"
    ) -> Dict[str, Union[List[Dict], float, int, str]]:
        """
        生成 Almgren-Chriss 最优分步执行计划。
        Args:
            total_shares: 总计划交易股数 X_0
            price: 当前基准价格 S_0
            horizon_minutes: 总执行时间跨度 T (分钟)
            n_slices: 离散切片步数 N
            daily_volatility: 标的日波动率 (转化为分钟级绝对波动率)
            action: 'buy' 或 'sell'
        Returns:
            包含每步时间戳偏移、委托股数、剩余持仓、紧迫度曲率 kappa 及预期冲击成本与方差的完整轨迹字典
        """
        total_shares = max(0, int(total_shares))
        n_slices = max(1, int(n_slices))
        if total_shares == 0 or price <= 0:
            return {
                "total_shares": 0,
                "n_slices": 0,
                "kappa": 0.0,
                "expected_cost_rmb": 0.0,
                "variance_cost_rmb2": 0.0,
                "slices": []
            }

        # 如果总股数不足 2 手，无需分拆，一步执行
        if total_shares <= LOT_SIZE or n_slices == 1:
            return {
                "total_shares": total_shares,
                "n_slices": 1,
                "kappa": 0.0,
                "expected_cost_rmb": round(0.5 * self.gamma * (total_shares ** 2) + self.eta * (total_shares ** 2), 2),
                "variance_cost_rmb2": 0.0,
                "slices": [{
                    "step": 1,
                    "time_offset_min": 0.0,
                    "trade_shares": total_shares,
                    "remaining_shares": 0,
                    "weight_pct": 100.0
                }]
            }

        T = max(float(horizon_minutes), 1.0)
        tau = T / float(n_slices)

        # 将日百分比波动率转换为每分钟绝对价格波动率 sigma_abs
        sigma_min_abs = float(price) * float(daily_volatility) / math.sqrt(240.0)

        # 有效临时冲击系数 eta_tilde = eta - 0.5 * gamma * tau
        eta_tilde = max(self.eta - 0.5 * self.gamma * tau, self.eta * 0.2)

        # 计算曲率参数 kappa = sqrt(lambda * sigma^2 / eta_tilde)
        kappa = math.sqrt((self.risk_aversion * (sigma_min_abs ** 2)) / eta_tilde)
        # 限制 kappa * T 避免 sinh 数值溢出
        kappa = min(kappa, 15.0 / T)

        # 计算连续理论轨迹 x_j (j = 0, 1, ..., N)
        time_points = [j * tau for j in range(n_slices + 1)]
        if kappa * T < 1e-4:
            # 当风险厌恶趋于 0 时，退化为匀速线性清算 (TWAP)
            raw_x = [total_shares * (1.0 - t / T) for t in time_points]
        else:
            denom = math.sinh(kappa * T)
            raw_x = [
                total_shares * (math.sinh(kappa * (T - t)) / denom)
                for t in time_points
            ]
        raw_x[-1] = 0.0

        # 将连续轨迹转化为满足 A 股 100 股整手约束的离散交易切片 n_j
        slices = []
        remaining = total_shares
        for j in range(1, n_slices + 1):
            if j == n_slices:
                trade_qty = remaining
            else:
                target_rem = raw_x[j]
                ideal_trade = max(0.0, remaining - target_rem)
                trade_qty = _round_lot(ideal_trade)
                # 防止提前耗尽或过度留尾
                if trade_qty > remaining:
                    trade_qty = _round_lot(remaining)
            remaining -= trade_qty
            slices.append({
                "step": j,
                "time_offset_min": round(time_points[j - 1], 2),
                "trade_shares": int(trade_qty),
                "remaining_shares": int(remaining),
                "weight_pct": round(trade_qty / total_shares * 100.0, 2) if total_shares > 0 else 0.0
            })

        # 计算期望执行冲击成本与方差
        expected_cost = 0.5 * self.gamma * (total_shares ** 2)
        variance_cost = 0.0
        for s in slices:
            nj = float(s["trade_shares"])
            xj = float(s["remaining_shares"])
            expected_cost += eta_tilde * (nj ** 2) / tau
            variance_cost += (sigma_min_abs ** 2) * (xj ** 2) * tau

        return {
            "action": action,
            "total_shares": total_shares,
            "n_slices": len(slices),
            "horizon_minutes": T,
            "kappa": round(kappa, 6),
            "expected_cost_rmb": round(expected_cost, 2),
            "variance_cost_rmb2": round(variance_cost, 2),
            "slices": slices
        }


class DynamicResidualVWAP:
    """
    A 股日内双峰 U 型成交量分布下的动态残差 VWAP / 自适应 TWAP 拆单器
    """

    # A 股 240 分钟 8 个 30 分钟时段的典型经验成交量占比 (双峰 U 型分布，总和 = 1.0)
    INTRADAY_U_PROFILE = [
        ("09:30-10:00", 0.22),  # 早盘集合竞价后第1个半小时：交投最活跃峰值
        ("10:00-10:30", 0.13),  # 早盘次高峰
        ("10:30-11:00", 0.10),  # 盘中回落
        ("11:00-11:30", 0.09),  # 午盘前清淡期
        ("13:00-13:30", 0.10),  # 午后开盘脉冲
        ("13:30-14:00", 0.08),  # 午后低谷
        ("14:00-14:30", 0.10),  # 尾盘预热
        ("14:30-15:00", 0.18),  # 尾盘抢筹/调仓高峰
    ]

    def __init__(self, min_participation: float = 0.05, max_participation: float = 0.15):
        self.min_participation = min_participation
        self.max_participation = max_participation

    def generate_vwap_schedule(
        self,
        total_shares: int,
        start_bucket_idx: int = 0,
        end_bucket_idx: int = 7,
        observed_volume_ratio: float = 1.0
    ) -> Dict[str, Union[List[Dict], int, float]]:
        """
        生成结合实时成交量残差修正的动态 VWAP 拆单计划。
        Args:
            total_shares: 拟拆单总股数 (100股整数倍)
            start_bucket_idx: 起始半小时时段索引 (0~7)
            end_bucket_idx: 结束半小时时段索引 (0~7)
            observed_volume_ratio: 当前市场实际成交量相对历史同期的放量倍数 (残差修正因子)
        """
        total_shares = max(0, int(total_shares))
        start_idx = max(0, min(7, int(start_bucket_idx)))
        end_idx = max(start_idx, min(7, int(end_bucket_idx)))

        active_buckets = self.INTRADAY_U_PROFILE[start_idx:end_idx + 1]
        raw_weights = np.array([w for _, w in active_buckets], dtype=float)

        # 动态残差修正: 若当前放量 (observed_volume_ratio > 1.0)，适度前移权重趁流动性充沛加速成交
        vol_ratio = max(0.3, min(3.0, float(observed_volume_ratio)))
        if len(raw_weights) > 1 and abs(vol_ratio - 1.0) > 0.05:
            tilt = np.linspace(vol_ratio, 1.0 / vol_ratio, len(raw_weights))
            raw_weights = raw_weights * tilt

        norm_weights = raw_weights / np.sum(raw_weights)

        schedule = []
        allocated = 0
        for i, (label, _) in enumerate(active_buckets):
            if i == len(active_buckets) - 1:
                qty = max(0, total_shares - allocated)
            else:
                qty = _round_lot(total_shares * norm_weights[i])
                if allocated + qty > total_shares:
                    qty = max(0, total_shares - allocated)
            allocated += qty
            schedule.append({
                "bucket_index": start_idx + i,
                "time_window": label,
                "target_weight": round(float(norm_weights[i]), 4),
                "order_shares": int(qty),
                "target_participation_rate": round(
                    min(self.max_participation, max(self.min_participation, 0.08 * vol_ratio)), 4
                )
            })

        return {
            "total_shares": total_shares,
            "volume_residual_factor": round(vol_ratio, 3),
            "schedule": schedule
        }


class PeggedIcebergPlanner:
    """
    被动 Maker 冰山挂单规划器 (Pegged Iceberg Order Planner)
    防止大单暴露交易意图引发盘口抢跑 (Front-Running)。
    """

    @staticmethod
    def plan_iceberg(
        total_shares: int,
        action: str,
        best_bid: float,
        best_ask: float,
        best_bid_vol: int,
        best_ask_vol: int,
        visible_ratio: float = 0.25
    ) -> Dict[str, Union[int, float, str]]:
        """
        计算冰山单的表观挂单量 (Tip)、隐藏储备量 (Hidden) 与最优被动 Maker 挂单价。
        """
        total_shares = max(0, int(total_shares))
        action = (action or "buy").lower()
        spread = max(0.0, float(best_ask) - float(best_bid))

        if action == "buy":
            opp_depth = max(LOT_SIZE, int(best_bid_vol or LOT_SIZE))
            # 被动挂在买一价（若点差 >= 0.02元，可加 0.01 元抢占买一队列头部 Queue Jumping）
            pegged_price = round(float(best_bid) + (0.01 if spread >= 0.02 else 0.0), 2)
        else:
            opp_depth = max(LOT_SIZE, int(best_ask_vol or LOT_SIZE))
            pegged_price = round(float(best_ask) - (0.01 if spread >= 0.02 else 0.0), 2)

        raw_tip = min(total_shares * visible_ratio, opp_depth * 0.30)
        visible_tip = max(LOT_SIZE, _round_lot(raw_tip)) if total_shares >= LOT_SIZE else total_shares
        visible_tip = min(visible_tip, total_shares)
        hidden_reserve = max(0, total_shares - visible_tip)
        n_batches = math.ceil(total_shares / visible_tip) if visible_tip > 0 else 1

        return {
            "action": action,
            "total_shares": total_shares,
            "visible_tip_shares": int(visible_tip),
            "hidden_reserve_shares": int(hidden_reserve),
            "estimated_batches": int(n_batches),
            "pegged_limit_price": pegged_price,
            "execution_style": "PASSIVE_MAKER_ICEBERG" if hidden_reserve > 0 else "DIRECT_LIMIT"
        }
