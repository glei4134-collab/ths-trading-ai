"""
波动率策略
3个策略：布林带收口突破、ATR波动突破、Keltner通道
"""

from .base import BaseStrategy, StrategyResult, Signal
from typing import Dict, List


class BollingerSqueezeStrategy(BaseStrategy):
    """布林带收口突破策略 - 波动率压缩后爆发"""
    name = "bollinger_squeeze"
    description = "布林带收口突破策略：布林带收窄后突破，预示大行情启动"
    category = "volatility"
    risk_level = "high"
    params = {
        "squeeze_threshold": {"desc": "收口阈值(带宽/中轨)", "default": 0.05, "min": 0.03, "max": 0.08},
        "breakout_confirm": {"desc": "突破确认比例", "default": 0.5, "min": 0.3, "max": 0.8}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        close = indicators.get("close", 0)
        bb_upper = indicators.get("bb_upper")
        bb_lower = indicators.get("bb_lower")
        bb_middle = indicators.get("bb_middle")

        if not all([bb_upper, bb_lower, bb_middle, close]):
            return StrategyResult(Signal.HOLD, 0, 0, ["布林带数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        band_width = bb_upper - bb_lower
        relative_width = band_width / bb_middle if bb_middle > 0 else 0

        # 检测收口
        if relative_width < 0.05:
            reasons.append(f"布林带收窄(宽度{relative_width:.3f})，波动率压缩")
            # 突破方向
            if close > bb_upper:
                score = 0.4
                reasons.append("向上突破布林带上轨，大行情启动信号")
            elif close < bb_lower:
                score = -0.4
                reasons.append("向下突破布林带下轨，下跌行情信号")
            else:
                reasons.append("收口状态，等待突破方向")
        else:
            reasons.append(f"布林带宽度正常({relative_width:.3f})")

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0)

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.35: return Signal.STRONG_BUY
        if score >= 0.15: return Signal.BUY
        if score <= -0.35: return Signal.STRONG_SELL
        if score <= -0.15: return Signal.SELL
        return Signal.HOLD


class ATRBreakoutStrategy(BaseStrategy):
    """ATR波动突破策略"""
    name = "atr_breakout"
    description = "ATR波动突破策略：价格突破ATR通道时产生信号，适合趋势行情"
    category = "volatility"
    risk_level = "high"
    params = {
        "atr_multiplier": {"desc": "ATR倍数", "default": 2.0, "min": 1.0, "max": 3.0},
        "atr_period": {"desc": "ATR周期", "default": 14, "min": 7, "max": 28}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        close = indicators.get("close", 0)
        atr = indicators.get("atr")
        ma20 = indicators.get("ma20")

        if atr is None or not ma20:
            return StrategyResult(Signal.HOLD, 0, 0, ["ATR数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        upper_channel = ma20 + 2 * atr
        lower_channel = ma20 - 2 * atr

        if close > upper_channel:
            score = 0.35
            reasons.append(f"价格({close:.2f})突破ATR上轨({upper_channel:.2f})")
            reasons.append("强势突破，趋势延续概率高")
        elif close < lower_channel:
            score = -0.35
            reasons.append(f"价格({close:.2f})跌破ATR下轨({lower_channel:.2f})")
            reasons.append("弱势跌破，下跌延续概率高")
        else:
            # 通道内
            position = (close - lower_channel) / (upper_channel - lower_channel)
            if position > 0.7:
                score = 0.1
                reasons.append(f"价格接近ATR上轨(位置{position:.0%})")
            elif position < 0.3:
                score = -0.1
                reasons.append(f"价格接近ATR下轨(位置{position:.0%})")
            else:
                reasons.append(f"价格在ATR通道中部(位置{position:.0%})")

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0)

        return StrategyResult(signal, score, confidence, reasons, upper_channel, lower_channel, self.name)

    def _score_to_signal(self, score):
        if score >= 0.3: return Signal.BUY
        if score >= 0.1: return Signal.WEAK_BUY
        if score <= -0.3: return Signal.SELL
        if score <= -0.1: return Signal.WEAK_SELL
        return Signal.HOLD


class KeltnerChannelStrategy(BaseStrategy):
    """Keltner通道策略"""
    name = "keltner_channel"
    description = "Keltner通道策略：基于EMA和ATR的通道，突破上下轨产生信号"
    category = "volatility"
    risk_level = "medium"
    params = {
        "ema_period": {"desc": "EMA周期", "default": 20, "min": 10, "max": 30},
        "atr_multiplier": {"desc": "ATR倍数", "default": 1.5, "min": 1.0, "max": 2.5}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        close = indicators.get("close", 0)
        ma20 = indicators.get("ma20")
        atr = indicators.get("atr")

        if not ma20 or atr is None:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足"], strategy_name=self.name)

        upper = ma20 + 1.5 * atr
        lower = ma20 - 1.5 * atr

        score = 0.0
        reasons = []

        if close > upper:
            score = 0.3
            reasons.append(f"价格({close:.2f})突破Keltner上轨({upper:.2f})")
        elif close < lower:
            score = -0.3
            reasons.append(f"价格({close:.2f})跌破Keltner下轨({lower:.2f})")
        elif close > ma20:
            score = 0.05
            reasons.append(f"价格在Keltner中轨上方")
        else:
            score = -0.05
            reasons.append(f"价格在Keltner中轨下方")

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0)

        return StrategyResult(signal, score, confidence, reasons, upper, lower, self.name)

    def _score_to_signal(self, score):
        if score >= 0.25: return Signal.BUY
        if score <= -0.25: return Signal.SELL
        return Signal.HOLD
