"""
风险管理模块
提供仓位管理、止损止盈、风险评估等功能
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import timedelta
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RiskAlert:
    """风险警报"""
    level: str  # low / medium / high / critical
    type: str   # stop_loss / take_profit / position_limit / drawdown / etc
    symbol: str
    message: str
    value: float
    threshold: float
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> Dict:
        return {
            "level": self.level,
            "type": self.type,
            "symbol": self.symbol,
            "message": self.message,
            "value": self.value,
            "threshold": self.threshold,
            "timestamp": self.timestamp
        }


class RiskManager:
    """
    风险管理器
    提供全面的风险控制功能
    """

    def __init__(self):
        self.max_drawdown = 0.20      # 最大回撤限制 20%
        self.max_single_loss = 0.05   # 单笔最大亏损 5%
        self.max_daily_loss = 0.10    # 每日最大亏损 10%
        self.max_position_count = 10  # 最大持仓数量
        self.alerts: List[RiskAlert] = []

    def check_position_limit(self, positions: Dict, total_assets: float) -> List[RiskAlert]:
        """检查仓位限制"""
        alerts = []

        # 检查持仓数量
        if len(positions) > self.max_position_count:
            alerts.append(RiskAlert(
                level="medium",
                type="position_count",
                symbol="ALL",
                message=f"持仓数量 {len(positions)} 超过限制 {self.max_position_count}",
                value=len(positions),
                threshold=self.max_position_count
            ))

        # 检查单只股票仓位
        for symbol, pos in positions.items():
            position_ratio = pos.get("market_value", 0) / total_assets if total_assets > 0 else 0
            if position_ratio > 0.3:
                alerts.append(RiskAlert(
                    level="high" if position_ratio > 0.4 else "medium",
                    type="single_position",
                    symbol=symbol,
                    message=f"{symbol} 仓位占比 {position_ratio:.1%} 过高",
                    value=position_ratio,
                    threshold=0.3
                ))

        return alerts

    def check_drawdown(self, initial_assets: float, current_assets: float) -> List[RiskAlert]:
        """检查回撤"""
        alerts = []

        if initial_assets > 0:
            drawdown = (initial_assets - current_assets) / initial_assets
            if drawdown > self.max_drawdown:
                alerts.append(RiskAlert(
                    level="critical",
                    type="drawdown",
                    symbol="PORTFOLIO",
                    message=f"组合回撤 {drawdown:.1%} 超过限制 {self.max_drawdown:.1%}",
                    value=drawdown,
                    threshold=self.max_drawdown
                ))
            elif drawdown > self.max_drawdown * 0.8:
                alerts.append(RiskAlert(
                    level="high",
                    type="drawdown_warning",
                    symbol="PORTFOLIO",
                    message=f"组合回撤 {drawdown:.1%} 接近限制",
                    value=drawdown,
                    threshold=self.max_drawdown
                ))

        return alerts

    def check_single_stock_loss(self, positions: Dict, stock_prices: Dict) -> List[RiskAlert]:
        """检查单只股票亏损"""
        alerts = []

        for symbol, pos in positions.items():
            if symbol in stock_prices:
                current_price = stock_prices[symbol]
                avg_price = pos.get("avg_price", 0)
                if avg_price > 0:
                    loss_rate = (avg_price - current_price) / avg_price
                    if loss_rate > self.max_single_loss:
                        alerts.append(RiskAlert(
                            level="high",
                            type="single_loss",
                            symbol=symbol,
                            message=f"{symbol} 亏损 {loss_rate:.1%} 超过限制 {self.max_single_loss:.1%}",
                            value=loss_rate,
                            threshold=self.max_single_loss
                        ))

        return alerts

    def calculate_optimal_position(self, signal_strength: float, volatility: float,
                                  account_value: float, current_positions: int) -> Dict:
        """
        计算最优仓位（分散化策略）

        Args:
            signal_strength: 信号强度 (0-1)
            volatility: 波动率
            account_value: 账户总值
            current_positions: 当前持仓数量

        Returns:
            仓位建议
        """
        # 基础仓位比例（分散化：每只股票最多10%）
        base_ratio = 0.08

        # 根据信号强度调整
        signal_adjusted = base_ratio * signal_strength

        # 根据波动率调整（波动率越高，仓位越小）
        volatility_adjusted = signal_adjusted * (1 / (1 + volatility * 0.5))

        # 根据当前持仓数量调整（持仓越多，单只仓位越小）
        position_adjusted = volatility_adjusted * (1 - current_positions * 0.08)

        # 限制范围：最小2%，最大15%（分散化）
        final_ratio = max(0.02, min(0.15, position_adjusted))

        # 计算金额和股数
        max_amount = account_value * final_ratio

        return {
            "ratio": round(final_ratio, 4),
            "amount": round(max_amount, 2),
            "shares": int(max_amount / 100) * 100,  # 100股整数倍
            "reason": {
                "base": base_ratio,
                "signal": signal_strength,
                "volatility": volatility,
                "current_positions": current_positions
            }
        }

    def calculate_stop_loss(self, entry_price: float, volatility: float,
                           support_level: float = None) -> Dict:
        """
        计算止损价（动态ATR止损）

        Args:
            entry_price: 入场价格
            volatility: 波动率 (ATR/价格)
            support_level: 支撑位

        Returns:
            止损建议
        """
        # 方法1: ATR止损（2倍波动率）
        atr_stop = entry_price * (1 - 2 * volatility)

        # 方法2: 固定比例止损（最低5%，最高8%）
        fixed_pct = 0.05 + min(volatility * 2, 0.03)  # 根据波动率调整
        fixed_stop = entry_price * (1 - fixed_pct)

        # 方法3: 支撑位止损
        support_stop = support_level if support_level else atr_stop

        # 取最保守的（最高的）止损价
        optimal_stop = max(atr_stop, fixed_stop, support_stop)

        return {
            "stop_loss": round(optimal_stop, 2),
            "stop_loss_pct": round((entry_price - optimal_stop) / entry_price * 100, 2),
            "methods": {
                "atr": round(atr_stop, 2),
                "fixed": round(fixed_stop, 2),
                "support": round(support_stop, 2)
            }
        }

    def calculate_take_profit(self, entry_price: float, volatility: float,
                             resistance_level: float = None, risk_reward_ratio: float = 2.0) -> Dict:
        """
        计算止盈价

        Args:
            entry_price: 入场价格
            volatility: 波动率
            resistance_level: 阻力位
            risk_reward_ratio: 风险回报比

        Returns:
            止盈建议
        """
        # 方法1: 固定比例止盈
        fixed_tp = entry_price * 1.15  # 15%止盈

        # 方法2: 风险回报比止盈
        risk = entry_price * 0.08  # 假设8%止损
        rr_tp = entry_price + risk * risk_reward_ratio

        # 方法3: 阻力位止盈
        resistance_tp = resistance_level if resistance_level else fixed_tp

        # 取最保守的（最低的）止盈价
        optimal_tp = min(fixed_tp, rr_tp, resistance_tp)

        return {
            "take_profit": round(optimal_tp, 2),
            "take_profit_pct": round((optimal_tp - entry_price) / entry_price * 100, 2),
            "methods": {
                "fixed": round(fixed_tp, 2),
                "risk_reward": round(rr_tp, 2),
                "resistance": round(resistance_tp, 2)
            }
        }

    def assess_portfolio_risk(self, positions: Dict, stock_data: Dict) -> Dict:
        """
        评估组合风险

        Args:
            positions: 持仓数据
            stock_data: 股票数据

        Returns:
            风险评估结果
        """
        total_value = sum(p.get("market_value", 0) for p in positions.values())
        total_profit = sum(p.get("profit", 0) for p in positions.values())

        # 计算各项风险指标
        risk_score = 0
        risk_factors = []

        # 1. 集中度风险
        if positions:
            max_position = max(p.get("market_value", 0) for p in positions.values())
            concentration = max_position / total_value if total_value > 0 else 0
            if concentration > 0.3:
                risk_score += 20
                risk_factors.append(f"持仓集中度过高: {concentration:.1%}")

        # 2. 数量风险
        if len(positions) > 5:
            risk_score += 10
            risk_factors.append(f"持仓数量较多: {len(positions)}只")

        # 3. 波动率风险
        high_volatility_count = 0
        for symbol in positions:
            if symbol in stock_data:
                change_pct = abs(stock_data[symbol].get("change_pct", 0))
                if change_pct > 3:
                    high_volatility_count += 1
        if high_volatility_count > 2:
            risk_score += 15
            risk_factors.append(f"高波动股票较多: {high_volatility_count}只")

        # 4. 盈亏风险
        if total_profit < 0:
            loss_ratio = abs(total_profit) / total_value if total_value > 0 else 0
            if loss_ratio > 0.05:
                risk_score += 25
                risk_factors.append(f"组合亏损较大: {loss_ratio:.1%}")

        # 确定风险等级
        if risk_score >= 50:
            risk_level = "high"
        elif risk_score >= 30:
            risk_level = "medium"
        else:
            risk_level = "low"

        return {
            "risk_score": risk_score,
            "risk_level": risk_level,
            "risk_factors": risk_factors,
            "total_value": round(total_value, 2),
            "total_profit": round(total_profit, 2),
            "position_count": len(positions),
            "suggestions": self._generate_suggestions(risk_level, risk_factors)
        }

    def _generate_suggestions(self, risk_level: str, risk_factors: List[str]) -> List[str]:
        """生成风险建议"""
        suggestions = []

        if risk_level == "high":
            suggestions.append("建议立即减仓，降低风险敞口")
            suggestions.append("优先卖出亏损较大的股票")

        if risk_level in ["high", "medium"]:
            suggestions.append("设置更严格的止损位")
            suggestions.append("避免追涨杀跌")

        for factor in risk_factors:
            if "集中度" in factor:
                suggestions.append("分散投资，降低单只股票仓位")
            if "数量" in factor:
                suggestions.append("精简持仓，保留优质标的")
            if "波动" in factor:
                suggestions.append("考虑配置低波动股票或债券")

        return suggestions

    # ============ 比赛制胜风控 ============

    def get_phase_config(self, days_elapsed: int, total_days: int = 55) -> Dict:
        """
        根据比赛阶段动态调整风控参数

        - 前1/3 (early): 激进建仓，集中持仓，追求高收益
        - 中1/3 (mid): 正常交易，攻守兼备
        - 后1/3 (late): 保守防守，保住排名
        """
        progress = days_elapsed / total_days if total_days > 0 else 0

        if progress < 0.33:
            # 激进期: 集中火力，快速建仓
            return {
                "phase": "early",
                "phase_name": "激进建仓期",
                "max_single_position": 0.30,  # 单股30%（从50%降到30%）
                "max_total_position": 0.90,   # 总仓90%
                "max_position_count": 4,       # 最多4只（集中）
                "stop_loss": 0.06,             # 止损6%
                "take_profit": 0.20,           # 止盈20%
                "base_ratio": 0.20,            # 基础仓位20%
                "prefer_categories": ["competition", "momentum", "trend"],
            }
        elif progress < 0.66:
            # 均衡期: 攻守兼备
            return {
                "phase": "mid",
                "phase_name": "攻守均衡期",
                "max_single_position": 0.25,  # 单股25%
                "max_total_position": 0.75,   # 总仓75%
                "max_position_count": 5,
                "stop_loss": 0.05,
                "take_profit": 0.15,
                "base_ratio": 0.15,
                "prefer_categories": ["competition", "trend", "volume"],
            }
        else:
            # 防守期: 保住收益
            return {
                "phase": "late",
                "phase_name": "防守保本期",
                "max_single_position": 0.25,
                "max_total_position": 0.70,
                "max_position_count": 6,
                "stop_loss": 0.04,
                "take_profit": 0.10,
                "base_ratio": 0.10,
                "prefer_categories": ["reversal", "volume", "pattern"],
            }

    def adjust_for_ranking(self, current_return: float, target_return: float = 0.10) -> Dict:
        """
        根据当前收益 vs 目标收益调整仓位

        Args:
            current_return: 当前收益率 (如 0.05 = 5%)
            target_return: 目标收益率 (默认10%)

        Returns:
            调整参数: position_multiplier, stop_loss_adjustment
        """
        gap = target_return - current_return

        if gap > 0.10:
            # 严重落后: 大幅加仓
            return {
                "position_multiplier": 1.5,
                "stop_loss_adjustment": 0.02,  # 放宽止损2%
                "reason": f"落后目标{gap:.1%}，加大风险敞口"
            }
        elif gap > 0.05:
            # 落后: 适度加仓
            return {
                "position_multiplier": 1.3,
                "stop_loss_adjustment": 0.01,
                "reason": f"落后目标{gap:.1%}，适度加仓"
            }
        elif gap < -0.05:
            # 大幅超额: 保守防御
            return {
                "position_multiplier": 0.6,
                "stop_loss_adjustment": -0.02,  # 收紧止损
                "reason": f"超额完成{abs(gap):.1%}，锁定利润"
            }
        elif gap < -0.02:
            # 小幅超额: 适度防御
            return {
                "position_multiplier": 0.8,
                "stop_loss_adjustment": -0.01,
                "reason": f"超额{abs(gap):.1%}，适度收缩"
            }
        else:
            return {
                "position_multiplier": 1.0,
                "stop_loss_adjustment": 0,
                "reason": "收益正常"
            }

    def get_competition_days_elapsed(self) -> int:
        """计算比赛已过的天数"""
        from datetime import date
        start = date.fromisoformat(self._get_competition_start())
        today = date.today()
        if today < start:
            return 0
        return (today - start).days

    def get_total_competition_days(self) -> int:
        """比赛总天数"""
        from datetime import date
        start = date.fromisoformat(self._get_competition_start())
        end = date.fromisoformat(self._get_competition_end())
        return (end - start).days

    @staticmethod
    def _get_competition_start() -> str:
        try:
            rules_file = Path(__file__).parent.parent / "config" / "competition_rules.json"
            rules = json.loads(rules_file.read_text(encoding="utf-8"))
            return rules.get("period", {}).get("start", "2026-05-06")
        except Exception:
            return "2026-05-06"

    @staticmethod
    def _get_competition_end() -> str:
        try:
            rules_file = Path(__file__).parent.parent / "config" / "competition_rules.json"
            rules = json.loads(rules_file.read_text(encoding="utf-8"))
            return rules.get("period", {}).get("end", "2026-06-30")
        except Exception:
            return "2026-06-30"


# 全局风控实例
risk_manager = RiskManager()
