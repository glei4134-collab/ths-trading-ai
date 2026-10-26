"""
综合策略
2个策略：多因子评分、AI增强策略
"""

from .base import BaseStrategy, StrategyResult, Signal
from typing import Dict, List, Optional


class MultiFactorStrategy(BaseStrategy):
    """多因子评分策略 - 综合多个维度打分"""
    name = "multi_factor"
    description = "多因子评分策略：综合技术面、量能、动量、波动率四个维度评分，加权得出最终信号"
    category = "composite"
    risk_level = "medium"
    params = {
        "technical_weight": {"desc": "技术面权重", "default": 0.35, "min": 0.1, "max": 0.5},
        "volume_weight": {"desc": "量能权重", "default": 0.25, "min": 0.1, "max": 0.4},
        "momentum_weight": {"desc": "动量权重", "default": 0.25, "min": 0.1, "max": 0.4},
        "volatility_weight": {"desc": "波动率权重", "default": 0.15, "min": 0.05, "max": 0.3}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        close = indicators.get("close", 0)
        score = 0.0
        reasons = []
        dimension_scores = {}

        # 1. 技术面评分
        tech_score, tech_reasons = self._calc_technical_score(indicators)
        dimension_scores["technical"] = tech_score
        reasons.extend([f"[技术]{r}" for r in tech_reasons])

        # 2. 量能评分
        vol_score = 0.0
        turnover = stock_data.get("turnover", 0)
        change_pct = stock_data.get("change_pct", 0)
        if turnover > 1.5 and change_pct > 0:
            vol_score = 0.3
            reasons.append(f"[量能]放量上涨(换手率{turnover:.2f}%)")
        elif turnover < 0.5 and change_pct < -1:
            vol_score = 0.1
            reasons.append(f"[量能]缩量下跌，抛压减轻")
        elif turnover > 2 and change_pct < -1:
            vol_score = -0.3
            reasons.append(f"[量能]放量下跌，恐慌情绪")
        dimension_scores["volume"] = vol_score

        # 3. 动量评分
        mom_score = 0.0
        if change_pct > 3:
            mom_score = 0.3
            reasons.append(f"[动量]强势上涨({change_pct:.2f}%)")
        elif change_pct > 1:
            mom_score = 0.15
            reasons.append(f"[动量]温和上涨({change_pct:.2f}%)")
        elif change_pct < -3:
            mom_score = -0.3
            reasons.append(f"[动量]强势下跌({change_pct:.2f}%)")
        elif change_pct < -1:
            mom_score = -0.15
            reasons.append(f"[动量]温和下跌({change_pct:.2f}%)")
        dimension_scores["momentum"] = mom_score

        # 4. 波动率评分
        vol_range = stock_data.get("high", 0) - stock_data.get("low", 0) if stock_data.get("high") else 0
        vol_pct = vol_range / close * 100 if close > 0 else 0
        vol_adj = 0.0
        if vol_pct > 5:
            vol_adj = -0.1
            reasons.append(f"[波动]日内波动较大({vol_pct:.2f}%)")
        elif vol_pct < 1:
            vol_adj = 0.05
            reasons.append(f"[波动]波动较小({vol_pct:.2f}%)")
        dimension_scores["volatility"] = vol_adj

        # 加权综合
        w = self.params
        total = (
            tech_score * w["technical_weight"]["default"] +
            vol_score * w["volume_weight"]["default"] +
            mom_score * w["momentum_weight"]["default"] +
            vol_adj * w["volatility_weight"]["default"]
        )

        signal = self._score_to_signal(total)
        confidence = min(abs(total) * 1.2, 1.0)

        return StrategyResult(
            signal, total, confidence, reasons,
            close * (1.08 if total > 0 else 0.92),
            close * (0.95 if total > 0 else 1.05),
            self.name,
            extra={"dimension_scores": dimension_scores}
        )

    def _score_to_signal(self, score):
        if score >= 0.25: return Signal.STRONG_BUY
        if score >= 0.12: return Signal.BUY
        if score >= 0.04: return Signal.WEAK_BUY
        if score <= -0.25: return Signal.STRONG_SELL
        if score <= -0.12: return Signal.SELL
        if score <= -0.04: return Signal.WEAK_SELL
        return Signal.HOLD


class AIEnhancedStrategy(BaseStrategy):
    """AI增强策略 - 结合多个策略结果，用AI权重调整"""
    name = "ai_enhanced"
    description = "AI增强策略：综合多个策略信号，根据AI判断动态调整权重"
    category = "composite"
    risk_level = "medium"
    params = {
        "strategy_count": {"desc": "使用的策略数量", "default": 5, "min": 3, "max": 10},
        "ai_weight": {"desc": "AI调整权重", "default": 0.3, "min": 0.1, "max": 0.5}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        """
        AI增强策略：接收其他策略的信号列表，加权综合
        kwargs 中应传入 sub_signals: List[StrategyResult]
        """
        sub_signals = kwargs.get("sub_signals", [])

        if not sub_signals:
            return StrategyResult(Signal.HOLD, 0, 0, ["未提供子策略信号"], strategy_name=self.name)

        score = 0.0
        reasons = []
        total_confidence = 0

        for sig in sub_signals:
            weight = sig.confidence  # 用置信度作为权重
            score += sig.score * weight
            total_confidence += weight
            reasons.append(f"[{sig.strategy_name}] {sig.signal.value} (分数{sig.score:.3f}, 置信度{sig.confidence:.2f})")

        if total_confidence > 0:
            score /= total_confidence

        signal = self._score_to_signal(score)
        avg_confidence = total_confidence / len(sub_signals) if sub_signals else 0

        return StrategyResult(
            signal, score, avg_confidence, reasons,
            strategy_name=self.name,
            extra={"sub_strategy_count": len(sub_signals)}
        )

    def _score_to_signal(self, score):
        if score >= 0.3: return Signal.STRONG_BUY
        if score >= 0.15: return Signal.BUY
        if score >= 0.05: return Signal.WEAK_BUY
        if score <= -0.3: return Signal.STRONG_SELL
        if score <= -0.15: return Signal.SELL
        if score <= -0.05: return Signal.WEAK_SELL
        return Signal.HOLD
