"""
量能策略
3个策略：放量突破、缩量回调、量价背离
"""

from .base import BaseStrategy, StrategyResult, Signal
from typing import Dict, List


class VolumeBreakoutStrategy(BaseStrategy):
    """放量突破策略"""
    name = "volume_breakout"
    description = "放量突破策略：成交量突然放大伴随价格突破关键位，确认突破有效性"
    category = "volume"
    risk_level = "medium"
    params = {
        "volume_ratio": {"desc": "放量倍数(相对20日均量)", "default": 2.0, "min": 1.5, "max": 5.0},
        "price_change_min": {"desc": "最小涨幅(%)", "default": 1.0, "min": 0.5, "max": 3.0}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        change_pct = stock_data.get("change_pct", 0)
        turnover = stock_data.get("turnover", 0)
        volume = stock_data.get("volume", 0)
        close = indicators.get("close", 0)
        ma20 = indicators.get("ma20")

        if not history or len(history) < 20:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        # 计算20日平均成交量
        avg_vol = sum(h.get("volume", 0) for h in history[-20:]) / 20 if history else 0
        vol_ratio = volume / avg_vol if avg_vol > 0 else 1

        is_high_volume = vol_ratio > 2.0
        is_price_up = change_pct > 1.0
        is_price_down = change_pct < -1.0

        if is_high_volume and is_price_up:
            score = 0.4
            reasons.append(f"放量上涨：量比{vol_ratio:.1f}倍，涨幅{change_pct:.2f}%")
            if ma20 and close > ma20:
                score += 0.1
                reasons.append("突破MA20，趋势确认")
        elif is_high_volume and is_price_down:
            score = -0.4
            reasons.append(f"放量下跌：量比{vol_ratio:.1f}倍，跌幅{change_pct:.2f}%")
        elif vol_ratio > 1.5 and change_pct > 0.5:
            score = 0.15
            reasons.append(f"温和放量上涨(量比{vol_ratio:.1f})")
        elif vol_ratio < 0.5 and change_pct < -0.5:
            score = 0.05
            reasons.append(f"缩量下跌(量比{vol_ratio:.1f})，抛压减轻")

        if not reasons:
            reasons.append(f"量能正常(量比{vol_ratio:.1f})")

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0)

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.35: return Signal.STRONG_BUY
        if score >= 0.15: return Signal.BUY
        if score <= -0.35: return Signal.STRONG_SELL
        if score <= -0.15: return Signal.SELL
        return Signal.HOLD


class VolumeRetracementStrategy(BaseStrategy):
    """缩量回调策略 - 上涨趋势中的回调买入"""
    name = "volume_retracement"
    description = "缩量回调策略：在上涨趋势中，缩量回调是较好的买入时机"
    category = "volume"
    risk_level = "low"
    params = {
        "retrace_ratio": {"desc": "回调幅度阈值(%)", "default": 3.0, "min": 1.0, "max": 5.0},
        "volume_shrink": {"desc": "缩量比例", "default": 0.5, "min": 0.3, "max": 0.7}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        change_pct = stock_data.get("change_pct", 0)
        turnover = stock_data.get("turnover", 0)
        ma5 = indicators.get("ma5")
        ma20 = indicators.get("ma20")
        close = indicators.get("close", 0)

        score = 0.0
        reasons = []

        # 检查是否在上涨趋势中
        in_uptrend = ma5 and ma20 and ma5 > ma20

        if in_uptrend:
            # 缩量回调
            if change_pct < -1 and turnover < 0.8:
                score = 0.3
                reasons.append(f"上涨趋势中缩量回调：跌幅{change_pct:.2f}%，换手率{turnover:.2f}%")
                reasons.append("回调幅度有限，抛压减轻，买入机会")
            elif change_pct < -0.5 and turnover < 0.5:
                score = 0.2
                reasons.append("温和缩量回调，可能接近支撑位")
        else:
            reasons.append("当前不在上涨趋势中，此策略不适用")

        if not reasons:
            reasons.append("无明显缩量回调信号")

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0) * 0.9

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.25: return Signal.BUY
        if score >= 0.1: return Signal.WEAK_BUY
        return Signal.HOLD


class VolumePriceDivergenceStrategy(BaseStrategy):
    """量价背离策略"""
    name = "vol_price_divergence"
    description = "量价背离策略：价格上涨但成交量萎缩（顶背离），或价格下跌但成交量萎缩（底背离）"
    category = "volume"
    risk_level = "medium"
    params = {
        "lookback": {"desc": "回看天数", "default": 5, "min": 3, "max": 10}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        if not history or len(history) < 10:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        recent = history[-5:]
        prev = history[-10:-5]

        price_up = recent[-1].get("close", 0) > prev[0].get("close", 0)
        vol_up = sum(h.get("volume", 0) for h in recent) > sum(h.get("volume", 0) for h in prev)

        if price_up and not vol_up:
            score = -0.25
            reasons.append("顶背离：价格上涨但成交量萎缩")
            reasons.append("上涨动能不足，注意回调风险")
        elif not price_up and vol_up:
            score = 0.2
            reasons.append("底背离：价格下跌但成交量放大")
            reasons.append("可能有资金介入，关注反弹")
        elif price_up and vol_up:
            score = 0.15
            reasons.append("量价齐升，健康上涨")
        else:
            reasons.append("量价同缩，无明显背离")

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0) * 0.8

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.2: return Signal.BUY
        if score <= -0.2: return Signal.SELL
        return Signal.HOLD
