"""
形态策略
3个策略：突破整理平台、缺口回补、支撑压力位
"""

from .base import BaseStrategy, StrategyResult, Signal
from typing import Dict, List


class PlatformBreakoutStrategy(BaseStrategy):
    """突破整理平台策略"""
    name = "platform_breakout"
    description = "突破整理平台策略：价格长时间横盘整理后向上突破，买入信号"
    category = "pattern"
    risk_level = "medium"
    params = {
        "consolidation_days": {"desc": "最小整理天数", "default": 10, "min": 5, "max": 30},
        "breakout_pct": {"desc": "突破幅度(%)", "default": 2.0, "min": 1.0, "max": 5.0}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        if not history or len(history) < 15:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        closes = [h.get("close", 0) for h in history[-20:]]
        if len(closes) < 15:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足"], strategy_name=self.name)

        # 计算整理区间
        recent = closes[-10:]
        platform_high = max(recent)
        platform_low = min(recent)
        platform_range = (platform_high - platform_low) / platform_low if platform_low > 0 else 0

        current = closes[-1]

        # 检测突破
        if platform_range < 0.05:  # 整理幅度<5%
            reasons.append(f"整理区间幅度{platform_range:.2%}，形态收敛")
            if current > platform_high * 1.01:
                score = 0.35
                reasons.append(f"向上突破整理平台({platform_high:.2f})")
            elif current < platform_low * 0.99:
                score = -0.3
                reasons.append(f"向下突破整理平台({platform_low:.2f})")
            else:
                reasons.append("整理中，等待突破")
        else:
            reasons.append(f"整理幅度较大({platform_range:.2%})，非典型平台")

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0)

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.3: return Signal.BUY
        if score >= 0.1: return Signal.WEAK_BUY
        if score <= -0.25: return Signal.SELL
        if score <= -0.1: return Signal.WEAK_SELL
        return Signal.HOLD


class GapFillStrategy(BaseStrategy):
    """缺口回补策略"""
    name = "gap_fill"
    description = "缺口回补策略：检测跳空缺口，预判缺口回补方向"
    category = "pattern"
    risk_level = "high"
    params = {
        "gap_threshold": {"desc": "缺口阈值(%)", "default": 1.0, "min": 0.5, "max": 3.0}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        if not history or len(history) < 5:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        # 检测最近的缺口
        for i in range(-3, 0):
            prev_high = history[i-1].get("high", history[i-1].get("close", 0))
            curr_low = history[i].get("low", history[i].get("close", 0))
            prev_low = history[i-1].get("low", history[i-1].get("close", 0))
            curr_high = history[i].get("high", history[i].get("close", 0))

            # 向上跳空
            if curr_low > prev_high:
                gap_pct = (curr_low - prev_high) / prev_high * 100
                if gap_pct > 1.0:
                    reasons.append(f"发现向上缺口({gap_pct:.2f}%)，可能回补")
                    score = -0.1  # 向上缺口可能回补
                    break
            # 向下跳空
            elif curr_high < prev_low:
                gap_pct = (prev_low - curr_high) / prev_low * 100
                if gap_pct > 1.0:
                    reasons.append(f"发现向下缺口({gap_pct:.2f}%)，可能回补")
                    score = 0.15  # 向下缺口回补概率高
                    break

        if not reasons:
            reasons.append("近期无明显缺口")

        signal = Signal.BUY if score > 0.1 else (Signal.SELL if score < -0.1 else Signal.HOLD)
        confidence = min(abs(score), 1.0) * 0.7

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)


class SupportResistanceStrategy(BaseStrategy):
    """支撑压力位策略"""
    name = "support_resistance"
    description = "支撑压力位策略：价格接近支撑位时买入，接近压力位时卖出"
    category = "pattern"
    risk_level = "low"
    params = {
        "touch_threshold": {"desc": "触及阈值(%)", "default": 2.0, "min": 1.0, "max": 3.0},
        "min_touches": {"desc": "最小触及次数", "default": 2, "min": 2, "max": 5}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        if not history or len(history) < 20:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        closes = [h.get("close", 0) for h in history[-30:]]
        highs = [h.get("high", h.get("close", 0)) for h in history[-30:]]
        lows = [h.get("low", h.get("close", 0)) for h in history[-30:]]
        current = closes[-1] if closes else 0

        # 寻找支撑位（多次触及的低点）
        support_levels = self._find_levels(lows, is_support=True)
        resistance_levels = self._find_levels(highs, is_support=False)

        # 判断当前位置
        near_support = False
        near_resistance = False

        for level in support_levels:
            if abs(current - level) / level < 0.02:
                near_support = True
                reasons.append(f"接近支撑位({level:.2f})")
                score += 0.2
                break

        for level in resistance_levels:
            if abs(current - level) / level < 0.02:
                near_resistance = True
                reasons.append(f"接近压力位({level:.2f})")
                score -= 0.2
                break

        if not reasons:
            reasons.append("未接近关键支撑压力位")

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0)

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)

    def _find_levels(self, prices, is_support=True, window=3):
        """寻找支撑/压力位"""
        levels = []
        for i in range(window, len(prices) - window):
            if is_support:
                if prices[i] == min(prices[i-window:i+window+1]):
                    levels.append(prices[i])
            else:
                if prices[i] == max(prices[i-window:i+window+1]):
                    levels.append(prices[i])
        return levels

    def _score_to_signal(self, score):
        if score >= 0.15: return Signal.BUY
        if score <= -0.15: return Signal.SELL
        return Signal.HOLD
