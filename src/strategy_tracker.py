"""
策略性能追踪模块
记录每个策略的历史信号和实际结果，供AI评估策略好坏
"""

import json
import logging
import os
from typing import Dict, List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import timedelta
from pathlib import Path
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

from paths import DATA_DIR
TRACKER_FILE = DATA_DIR / "strategy_performance.json"


@dataclass
class SignalRecord:
    """信号记录"""
    strategy_name: str
    symbol: str
    signal: str  # BUY/SELL/HOLD等
    score: float
    confidence: float
    price_at_signal: float
    timestamp: str = ""
    result: Optional[Dict] = None  # 后续结果 {price_after_1d, price_after_5d, profit_pct}

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")


class StrategyTracker:
    """策略性能追踪器"""

    def __init__(self):
        self.records: List[Dict] = []
        self._load()

    def _load(self):
        """加载历史记录"""
        try:
            if TRACKER_FILE.exists():
                with open(TRACKER_FILE, "r", encoding="utf-8") as f:
                    self.records = json.load(f)
        except Exception as e:
            logger.error(f"加载策略记录失败: {e}")
            self.records = []

    def _save(self):
        """保存记录"""
        if os.environ.get('SKIP_TRACKER_SAVE'):
            return  # 模拟测试时跳过保存
        try:
            TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(TRACKER_FILE, "w", encoding="utf-8") as f:
                json.dump(self.records, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存策略记录失败: {e}")

    def record_signal(self, strategy_name: str, symbol: str, signal: str,
                      score: float, confidence: float, price: float):
        """记录策略信号"""
        record = SignalRecord(
            strategy_name=strategy_name,
            symbol=symbol,
            signal=signal,
            score=score,
            confidence=confidence,
            price_at_signal=price
        )
        self.records.append(asdict(record))
        self._save()

    def update_result(self, index: int, result: Dict):
        """更新信号的后续结果"""
        if 0 <= index < len(self.records):
            self.records[index]["result"] = result
            self._save()

    def get_performance(self, strategy_name: str, days: int = 30) -> Dict:
        """获取策略表现"""
        cutoff = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=days)).strftime("%Y-%m-%d")
        relevant = [
            r for r in self.records
            if r["strategy_name"] == strategy_name and r["timestamp"] >= cutoff
        ]

        if not relevant:
            return {
                "strategy": strategy_name,
                "total_signals": 0,
                "message": f"最近{days}天无信号记录"
            }

        total = len(relevant)
        buy_signals = [r for r in relevant if r["signal"] in ("BUY", "STRONG_BUY", "WEAK_BUY")]
        sell_signals = [r for r in relevant if r["signal"] in ("SELL", "STRONG_SELL", "WEAK_SELL")]

        # 计算胜率（有结果的信号）
        completed = [r for r in relevant if r.get("result")]
        wins = 0
        total_profit = 0
        for r in completed:
            result = r["result"]
            if result.get("profit_pct", 0) > 0:
                wins += 1
            total_profit += result.get("profit_pct", 0)

        win_rate = wins / len(completed) if completed else 0
        avg_profit = total_profit / len(completed) if completed else 0

        return {
            "strategy": strategy_name,
            "period_days": days,
            "total_signals": total,
            "buy_signals": len(buy_signals),
            "sell_signals": len(sell_signals),
            "hold_signals": total - len(buy_signals) - len(sell_signals),
            "completed_evaluations": len(completed),
            "win_rate": round(win_rate * 100, 2),
            "avg_profit_pct": round(avg_profit, 2),
            "total_profit_pct": round(total_profit, 2),
            "avg_confidence": round(
                sum(r["confidence"] for r in relevant) / total, 4
            )
        }

    def rank_strategies(self, days: int = 30) -> List[Dict]:
        """按表现排名所有策略"""
        strategies = set(r["strategy_name"] for r in self.records)
        rankings = []
        for name in strategies:
            perf = self.get_performance(name, days)
            rankings.append(perf)

        # 按胜率排序（有结果的优先）
        rankings.sort(key=lambda x: (x.get("completed_evaluations", 0) > 0, x.get("win_rate", 0)), reverse=True)
        return rankings

    def get_strategy_summary(self) -> Dict:
        """获取所有策略的摘要"""
        strategies = set(r["strategy_name"] for r in self.records)
        summary = {}
        for name in strategies:
            records = [r for r in self.records if r["strategy_name"] == name]
            summary[name] = {
                "total_signals": len(records),
                "latest_signal": records[-1] if records else None,
                "avg_confidence": round(
                    sum(r["confidence"] for r in records) / len(records), 4
                ) if records else 0
            }
        return summary

    def get_recent_signals(self, limit: int = 20) -> List[Dict]:
        """获取最近的信号"""
        return self.records[-limit:]

    def get_strategy_weights(self, days: int = 30) -> Dict[str, float]:
        """
        根据历史表现计算策略动态权重

        权重公式:
        - win_rate × 0.6（胜率权重最高）
        - avg_profit × 0.3（平均收益）
        - confidence × 0.1（信号置信度）
        - 数据不足3次的策略给默认低权重

        Returns:
            {strategy_name: weight} 归一化后总和 = 1.0
        """
        rankings = self.rank_strategies(days)
        all_strategies = set(r["strategy_name"] for r in self.records)
        weights = {}

        for name in all_strategies:
            # 找到该策略的排名数据
            perf = None
            for r in rankings:
                if r["strategy"] == name:
                    perf = r
                    break

            if not perf or perf.get("completed_evaluations", 0) < 2:
                # OPT-15: 降低门槛从3次→2次，默认权重从0.02→0.05加速收敛
                weights[name] = 0.05
                continue

            win_rate = perf.get("win_rate", 50) / 100
            avg_profit = max(0, perf.get("avg_profit_pct", 0)) / 10  # 归一化到 0~1
            confidence = perf.get("avg_confidence", 0.5)

            score = win_rate * 0.6 + avg_profit * 0.3 + confidence * 0.1
            weights[name] = max(0.01, score)

        # 归一化
        total = sum(weights.values())
        if total > 0:
            weights = {k: round(v / total, 4) for k, v in weights.items()}

        return weights

    def get_weighted_performance(self, days: int = 30) -> List[Dict]:
        """
        获取加权后的策略排行榜
        近7天的信号权重 × 2，近30天的权重 × 1
        """
        cutoff_7d = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=7)).strftime("%Y-%m-%d")
        cutoff_30d = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=days)).strftime("%Y-%m-%d")

        strategies = set(r["strategy_name"] for r in self.records)
        results = []

        for name in strategies:
            recent = [r for r in self.records
                      if r["strategy_name"] == name and r["timestamp"] >= cutoff_7d]
            older = [r for r in self.records
                     if r["strategy_name"] == name and cutoff_7d > r["timestamp"] >= cutoff_30d]

            # 近7天加权
            recent_completed = [r for r in recent if r.get("result")]
            recent_wins = sum(1 for r in recent_completed if r["result"].get("is_win"))
            recent_win_rate = recent_wins / len(recent_completed) * 100 if recent_completed else 50

            # 30天整体
            all_completed = [r for r in recent + older if r.get("result")]
            all_wins = sum(1 for r in all_completed if r["result"].get("is_win"))
            all_win_rate = all_wins / len(all_completed) * 100 if all_completed else 50

            # 加权胜率 = 近7天 × 0.7 + 30天 × 0.3
            weighted_win_rate = recent_win_rate * 0.7 + all_win_rate * 0.3

            results.append({
                "strategy": name,
                "total_signals": len(recent + older),
                "recent_signals": len(recent),
                "completed_evaluations": len(all_completed),
                "recent_win_rate": round(recent_win_rate, 1),
                "overall_win_rate": round(all_win_rate, 1),
                "weighted_win_rate": round(weighted_win_rate, 1)
            })

        results.sort(key=lambda x: x["weighted_win_rate"], reverse=True)
        return results


# 全局实例
strategy_tracker = StrategyTracker()
