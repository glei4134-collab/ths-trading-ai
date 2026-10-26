"""
策略健康监控模块
检测策略衰减、自动降权、市场环境适配
"""

import logging
import math
from typing import Dict, List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import timedelta

logger = logging.getLogger(__name__)


class StrategyHealthMonitor:
    """策略健康监控器"""

    # 衰减检测阈值
    CONSECUTIVE_LOSS_LIMIT = 5      # 连续亏损次数
    SHORT_TERM_WIN_RATE = 0.30      # 近7天最低胜率
    SHARPE_FLOOR = 0.0              # 夏普比率下限
    MAX_DRAWDOWN_LIMIT = 0.15       # 最大回撤限制 15%

    def __init__(self):
        self._health_cache: Dict[str, Dict] = {}
        self._cache_time = 0
        self._cache_ttl = 300  # 5分钟缓存

    def check_health(self, strategy_name: str, days: int = 30) -> Dict:
        """
        检查策略健康状态

        Returns: {
            status: healthy/declining/dead,
            win_rate: float,
            trend: improving/stable/declining,
            consecutive_losses: int,
            max_drawdown: float,
            sharpe_ratio: float,
            total_signals: int,
            alert: str or None
        }
        """
        import time
        now = time.time()

        # 检查缓存
        cache_key = f"{strategy_name}_{days}"
        if cache_key in self._health_cache and now - self._cache_time < self._cache_ttl:
            return self._health_cache[cache_key]

        from strategy_tracker import strategy_tracker
        perf = strategy_tracker.get_performance(strategy_name, days)

        if perf.get("total_signals", 0) == 0:
            result = {
                "status": "no_data",
                "win_rate": 0,
                "trend": "unknown",
                "consecutive_losses": 0,
                "max_drawdown": 0,
                "sharpe_ratio": 0,
                "total_signals": 0,
                "alert": "无历史信号数据"
            }
            self._health_cache[cache_key] = result
            return result

        # 获取详细记录
        records = strategy_tracker.records
        strategy_records = [
            r for r in records
            if r.get("strategy_name") == strategy_name
            and r.get("timestamp", "") >= (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=days)).strftime("%Y-%m-%d")
        ]

        # 计算指标
        completed = [r for r in strategy_records if r.get("result")]
        win_rate = perf.get("win_rate", 0) / 100
        consecutive_losses = self._count_consecutive_losses(strategy_records)
        max_dd = self._calc_max_drawdown(completed)
        sharpe = self._calc_sharpe(completed)
        trend = self._detect_trend(strategy_records)

        # 衰减检测
        status, alert = self._detect_decay(
            win_rate, consecutive_losses, sharpe, max_dd, len(completed)
        )

        result = {
            "status": status,
            "win_rate": round(win_rate * 100, 2),
            "trend": trend,
            "consecutive_losses": consecutive_losses,
            "max_drawdown": round(max_dd * 100, 2),
            "sharpe_ratio": round(sharpe, 2),
            "total_signals": len(strategy_records),
            "completed_evaluations": len(completed),
            "avg_profit": perf.get("avg_profit_pct", 0),
            "alert": alert
        }

        self._health_cache[cache_key] = result
        return result

    def get_all_health(self) -> Dict[str, Dict]:
        """获取所有策略的健康状态"""
        from strategy_tracker import strategy_tracker
        from strategy_registry import registry

        all_health = {}
        for name in registry.list_names():
            all_health[name] = self.check_health(name)

        return all_health

    def detect_regime_fit(self, strategy_name: str, regime: str) -> Dict:
        """
        检测策略是否适合当前市场环境

        Args:
            strategy_name: 策略名称
            regime: 市场环境 (bull/bear/sideways/volatile)

        Returns: {fit_score, recommendation, reason}
        """
        from strategy_registry import registry

        strategy = registry.get(strategy_name)
        if not strategy:
            return {"fit_score": 0, "recommendation": "unknown", "reason": "策略不存在"}

        category = strategy.category
        risk_level = strategy.risk_level

        # 策略类别与市场环境的适配矩阵
        fit_matrix = {
            "bull": {
                "trend": 0.9, "momentum": 0.8, "volume": 0.7,
                "pattern": 0.6, "reversal": 0.3, "volatility": 0.4,
                "composite": 0.7, "shortterm": 0.6, "competition": 0.8
            },
            "bear": {
                "trend": 0.2, "momentum": 0.2, "volume": 0.3,
                "pattern": 0.5, "reversal": 0.8, "volatility": 0.7,
                "composite": 0.4, "shortterm": 0.5, "competition": 0.3
            },
            "sideways": {
                "trend": 0.4, "momentum": 0.5, "volume": 0.6,
                "pattern": 0.7, "reversal": 0.7, "volatility": 0.5,
                "composite": 0.6, "shortterm": 0.7, "competition": 0.5
            },
            "volatile": {
                "trend": 0.3, "momentum": 0.4, "volume": 0.7,
                "pattern": 0.4, "reversal": 0.6, "volatility": 0.9,
                "composite": 0.5, "shortterm": 0.6, "competition": 0.4
            }
        }

        fit_score = fit_matrix.get(regime, {}).get(category, 0.5)

        # 风险等级调整
        if regime == "bear" and risk_level == "high":
            fit_score *= 0.5
        elif regime == "bull" and risk_level == "low":
            fit_score *= 0.8

        # 生成建议
        if fit_score >= 0.7:
            recommendation = "推荐使用"
            reason = f"{category}策略在{regime}市场中表现优异"
        elif fit_score >= 0.5:
            recommendation = "可以使用"
            reason = f"{category}策略在{regime}市场中表现一般"
        else:
            recommendation = "不建议使用"
            reason = f"{category}策略在{regime}市场中表现较差"

        return {
            "fit_score": round(fit_score, 2),
            "recommendation": recommendation,
            "reason": reason,
            "strategy": strategy_name,
            "regime": regime,
            "category": category,
            "risk_level": risk_level
        }

    def suggest_rebalance(self, current_weights: Dict[str, float]) -> Dict:
        """
        基于健康状态建议重新平衡权重

        Returns: {new_weights, changes, reasons}
        """
        new_weights = {}
        changes = []
        reasons = []

        for name, weight in current_weights.items():
            health = self.check_health(name)
            status = health.get("status", "healthy")

            if status == "dead":
                new_weight = weight * 0.1  # 衰减到 10%
                reasons.append(f"{name}: 已失效，权重降至 {new_weight:.3f}")
            elif status == "declining":
                new_weight = weight * 0.5  # 衰减到 50%
                reasons.append(f"{name}: 衰减中，权重降至 {new_weight:.3f}")
            elif health.get("trend") == "improving":
                new_weight = min(weight * 1.3, 0.3)  # 加权但不超过 30%
                reasons.append(f"{name}: 改善中，权重升至 {new_weight:.3f}")
            else:
                new_weight = weight

            new_weights[name] = round(new_weight, 4)
            if abs(new_weight - weight) > 0.001:
                changes.append({
                    "strategy": name,
                    "old_weight": round(weight, 4),
                    "new_weight": round(new_weight, 4),
                    "reason": health.get("status", "unknown")
                })

        # 归一化
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {k: round(v / total, 4) for k, v in new_weights.items()}

        return {
            "new_weights": new_weights,
            "changes": changes,
            "reasons": reasons
        }

    def auto_adjust_weights(self) -> Dict[str, float]:
        """
        自动调整策略权重：衰减的降权，健康的加权
        """
        from strategy_tracker import strategy_tracker

        current_weights = strategy_tracker.get_strategy_weights()
        if not current_weights:
            return {}

        result = self.suggest_rebalance(current_weights)
        return result.get("new_weights", current_weights)

    # ========== 内部方法 ==========

    def _detect_decay(self, win_rate: float, consecutive_losses: int,
                      sharpe: float, max_dd: float, total_completed: int) -> tuple:
        """
        衰减检测

        Returns: (status, alert)
        """
        alerts = []

        # 规则1: 连续5次亏损
        if consecutive_losses >= self.CONSECUTIVE_LOSS_LIMIT:
            alerts.append(f"连续{consecutive_losses}次亏损")

        # 规则2: 近期胜率过低
        if total_completed >= 5 and win_rate < self.SHORT_TERM_WIN_RATE:
            alerts.append(f"胜率仅{win_rate*100:.1f}%")

        # 规则3: 夏普比率为负
        if total_completed >= 5 and sharpe < self.SHARPE_FLOOR:
            alerts.append(f"夏普比率{sharpe:.2f}为负")

        # 规则4: 最大回撤过大
        if max_dd > self.MAX_DRAWDOWN_LIMIT:
            alerts.append(f"最大回撤{max_dd*100:.1f}%")

        # 判断状态
        if len(alerts) >= 3:
            return "dead", "；".join(alerts)
        elif len(alerts) >= 1:
            return "declining", "；".join(alerts)
        else:
            return "healthy", None

    def _count_consecutive_losses(self, records: List[Dict]) -> int:
        """计算连续亏损次数（从最近开始）"""
        count = 0
        for r in reversed(records):
            result = r.get("result")
            if result:
                profit = result.get("profit_pct", 0)
                if profit < 0:
                    count += 1
                else:
                    break
            # 无结果的记录跳过
        return count

    def _calc_max_drawdown(self, completed_records: List[Dict]) -> float:
        """计算最大回撤"""
        if not completed_records:
            return 0

        equity = 1000000  # 起始资金
        peak = equity
        max_dd = 0

        for r in completed_records:
            profit_pct = r.get("result", {}).get("profit_pct", 0)
            equity *= (1 + profit_pct / 100)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        return max_dd

    def _calc_sharpe(self, completed_records: List[Dict]) -> float:
        """计算简化夏普比率"""
        if len(completed_records) < 2:
            return 0

        returns = [r.get("result", {}).get("profit_pct", 0) for r in completed_records]
        avg = sum(returns) / len(returns)
        variance = sum((r - avg) ** 2 for r in returns) / len(returns)
        std = math.sqrt(variance) if variance > 0 else 1

        return (avg / std) if std > 0 else 0

    def _detect_trend(self, records: List[Dict]) -> str:
        """检测趋势：improving/stable/declining"""
        if len(records) < 6:
            return "unknown"

        # 分成前半和后半
        mid = len(records) // 2
        first_half = records[:mid]
        second_half = records[mid:]

        def avg_profit(recs):
            completed = [r for r in recs if r.get("result")]
            if not completed:
                return 0
            return sum(r["result"].get("profit_pct", 0) for r in completed) / len(completed)

        first_avg = avg_profit(first_half)
        second_avg = avg_profit(second_half)

        diff = second_avg - first_avg
        if diff > 2:
            return "improving"
        elif diff < -2:
            return "declining"
        else:
            return "stable"


# 全局实例
strategy_health_monitor = StrategyHealthMonitor()
