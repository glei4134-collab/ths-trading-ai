"""
反转策略
4个策略：RSI底背离、MACD底背离、双底形态、头肩底形态
"""

from .base import BaseStrategy, StrategyResult, Signal
from typing import Dict, List


class RSIDivergenceStrategy(BaseStrategy):
    """RSI背离策略 - 价格与RSI方向不一致"""
    name = "rsi_divergence"
    description = "RSI背离策略：价格创新低但RSI未创新低（底背离买入），价格创新高但RSI未创新高（顶背离卖出）"
    category = "reversal"
    risk_level = "high"
    params = {
        "lookback": {"desc": "回看周期", "default": 20, "min": 10, "max": 40}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        rsi = indicators.get("rsi")
        close = indicators.get("close", 0)
        macd = indicators.get("macd")
        histogram = indicators.get("histogram")

        if rsi is None or not history or len(history) < 10:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足，需要历史K线"], strategy_name=self.name)

        score = 0.0
        reasons = []

        # 简化版背离检测：比较近期价格趋势和RSI趋势
        recent_closes = [h.get("close", 0) for h in history[-10:]]
        price_trend = recent_closes[-1] - recent_closes[0] if recent_closes else 0

        # 底背离：价格下跌但RSI回升
        if price_trend < 0 and rsi > 40:
            score += 0.3
            reasons.append(f"疑似底背离：价格下跌但RSI({rsi:.1f})回升")
        # 顶背离：价格上涨但RSI下降
        elif price_trend > 0 and rsi < 60:
            score -= 0.3
            reasons.append(f"疑似顶背离：价格上涨但RSI({rsi:.1f})下降")

        # MACD 辅助确认
        if histogram:
            if histogram > 0 and score > 0:
                score += 0.1
                reasons.append("MACD柱状图转正，确认反转")
            elif histogram < 0 and score < 0:
                score -= 0.1
                reasons.append("MACD柱状图转负，确认反转")

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0) * 0.8  # 背离策略置信度略低

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.25: return Signal.BUY
        if score >= 0.1: return Signal.WEAK_BUY
        if score <= -0.25: return Signal.SELL
        if score <= -0.1: return Signal.WEAK_SELL
        return Signal.HOLD


class MACDDivergenceStrategy(BaseStrategy):
    """MACD背离策略"""
    name = "macd_divergence"
    description = "MACD背离策略：价格与MACD柱状图方向不一致时产生反转信号"
    category = "reversal"
    risk_level = "high"
    params = {
        "lookback": {"desc": "回看周期", "default": 20, "min": 10, "max": 40}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        macd = indicators.get("macd")
        histogram = indicators.get("histogram")

        if not history or len(history) < 15:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        # 价格趋势
        recent = [h.get("close", 0) for h in history[-15:]]
        price_trend = "up" if recent[-1] > recent[0] else "down"

        # MACD趋势（用柱状图）
        if histogram is not None:
            if price_trend == "down" and histogram > 0:
                score = 0.3
                reasons.append("底背离信号：价格下跌但MACD柱状图转正")
            elif price_trend == "up" and histogram < 0:
                score = -0.3
                reasons.append("顶背离信号：价格上涨但MACD柱状图转负")
            else:
                reasons.append("价格与MACD方向一致，无背离")

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0) * 0.8

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.25: return Signal.BUY
        if score >= 0.1: return Signal.WEAK_BUY
        if score <= -0.25: return Signal.SELL
        if score <= -0.1: return Signal.WEAK_SELL
        return Signal.HOLD


class DoubleBottomStrategy(BaseStrategy):
    """双底形态策略 - W底"""
    name = "double_bottom"
    description = "双底形态策略：价格形成W底形态，第二次探底不破前低时买入"
    category = "reversal"
    risk_level = "medium"
    params = {
        "min_gap_days": {"desc": "两个底之间最小间隔天数", "default": 5, "min": 3, "max": 20},
        "tolerance": {"desc": "两底价格容差(%)", "default": 3.0, "min": 1.0, "max": 5.0}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        if not history or len(history) < 20:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足，需要20日以上K线"], strategy_name=self.name)

        score = 0.0
        reasons = []

        closes = [h.get("close", 0) for h in history[-30:]]
        lows = [h.get("low", h.get("close", 0)) for h in history[-30:]]

        # 寻找两个低点
        min_idx = lows.index(min(lows)) if lows else -1
        if min_idx > 5:
            first_low = lows[min_idx]
            # 在第一个低点之前找更早的低点
            earlier_lows = lows[:min_idx]
            if earlier_lows:
                second_min = min(earlier_lows)
                if abs(first_low - second_min) / second_min < 0.03:
                    current_price = closes[-1]
                    neckline = max(closes[min_idx:])
                    if current_price > neckline:
                        score = 0.35
                        reasons.append(f"双底形态确认：突破颈线({neckline:.2f})")
                    elif current_price > first_low:
                        score = 0.15
                        reasons.append(f"疑似双底：当前价({current_price:.2f})在两底之上")

        if not reasons:
            reasons.append("未检测到双底形态")

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0)

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.3: return Signal.BUY
        if score >= 0.1: return Signal.WEAK_BUY
        return Signal.HOLD


class HeadShoulderStrategy(BaseStrategy):
    """头肩底形态策略"""
    name = "head_shoulder"
    description = "头肩底形态策略：价格形成头肩底形态，突破颈线时买入"
    category = "reversal"
    risk_level = "medium"
    params = {
        "lookback": {"desc": "回看周期", "default": 20, "min": 10, "max": 40}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        if not history or len(history) < 20:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        closes = [h.get("close", 0) for h in history[-30:]]
        if len(closes) < 20:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足"], strategy_name=self.name)

        # 简化检测：找三个低点，中间最低
        mid = len(closes) // 2
        left_min = min(closes[:mid-3]) if mid > 3 else closes[0]
        center_min = min(closes[mid-3:mid+3])
        right_min = min(closes[mid+3:]) if mid+3 < len(closes) else closes[-1]

        if center_min < left_min and center_min < right_min:
            neckline = max(left_min, right_min)
            current = closes[-1]
            if current > neckline:
                score = 0.3
                reasons.append(f"头肩底形态突破颈线({neckline:.2f})")
            elif current > center_min:
                score = 0.1
                reasons.append("疑似头肩底形成中")

        if not reasons:
            reasons.append("未检测到头肩底形态")

        signal = Signal.BUY if score > 0.2 else Signal.HOLD
        confidence = min(abs(score), 1.0) * 0.7

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)
