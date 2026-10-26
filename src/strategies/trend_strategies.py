"""
趋势跟踪策略
5个策略：MA交叉、布林带突破、MACD趋势、DMI趋势、ADX趋势
"""

from .base import BaseStrategy, StrategyResult, Signal
from typing import Dict, List


class MACrossStrategy(BaseStrategy):
    """均线交叉策略 - MA5/MA10/MA20 交叉产生买卖信号"""
    name = "ma_cross"
    description = "均线交叉策略：通过MA5/MA10/MA20的金叉死叉判断趋势方向"
    category = "trend"
    risk_level = "medium"
    params = {
        "short_period": {"desc": "短期均线周期", "default": 5, "min": 3, "max": 20},
        "long_period": {"desc": "长期均线周期", "default": 20, "min": 10, "max": 60},
        "confirm_volume": {"desc": "是否要求放量确认", "default": True}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        close = indicators.get("close", 0)
        ma5 = indicators.get("ma5")
        ma10 = indicators.get("ma10")
        ma20 = indicators.get("ma20")
        volume = stock_data.get("volume", 0)
        turnover = stock_data.get("turnover", 0)

        if not all([ma5, ma20, close]):
            return StrategyResult(Signal.HOLD, 0, 0, ["均线数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        # MA5/MA20 金叉
        if ma5 > ma20:
            score += 0.3
            reasons.append(f"MA5({ma5:.2f}) > MA20({ma20:.2f}) 短期趋势向上")
            if close > ma5:
                score += 0.15
                reasons.append("价格在MA5上方，趋势强劲")
        else:
            score -= 0.3
            reasons.append(f"MA5({ma5:.2f}) < MA20({ma20:.2f}) 短期趋势向下")
            if close < ma5:
                score -= 0.15
                reasons.append("价格在MA5下方，趋势疲弱")

        # MA10 方向
        if ma10:
            if ma5 > ma10 > ma20:
                score += 0.15
                reasons.append("均线多头排列 MA5>MA10>MA20")
            elif ma5 < ma10 < ma20:
                score -= 0.15
                reasons.append("均线空头排列 MA5<MA10<MA20")

        # 放量确认
        if turnover and turnover > 1.5:
            score *= 1.2
            reasons.append(f"放量确认(换手率{turnover:.2f}%)")

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0)
        target_price = close * (1.08 if score > 0 else 0.92)
        stop_loss = close * (0.95 if score > 0 else 1.05)

        return StrategyResult(signal, score, confidence, reasons, target_price, stop_loss, self.name)

    def _score_to_signal(self, score):
        if score >= 0.4: return Signal.STRONG_BUY
        if score >= 0.2: return Signal.BUY
        if score >= 0.05: return Signal.WEAK_BUY
        if score <= -0.4: return Signal.STRONG_SELL
        if score <= -0.2: return Signal.SELL
        if score <= -0.05: return Signal.WEAK_SELL
        return Signal.HOLD


class BollingerBreakoutStrategy(BaseStrategy):
    """布林带突破策略 - 价格突破布林带上轨/下轨"""
    name = "bollinger_breakout"
    description = "布林带突破策略：价格突破布林带上轨为买入信号，跌破下轨为卖出信号"
    category = "trend"
    risk_level = "high"
    params = {
        "period": {"desc": "布林带周期", "default": 20, "min": 10, "max": 30},
        "std_dev": {"desc": "标准差倍数", "default": 2, "min": 1.5, "max": 3}
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

        # 价格与布林带位置
        band_width = bb_upper - bb_lower
        position = (close - bb_lower) / band_width if band_width > 0 else 0.5

        if close > bb_upper:
            score = -0.25
            reasons.append(f"价格({close:.2f})突破布林带上轨({bb_upper:.2f})，可能超买")
        elif close < bb_lower:
            score = 0.25
            reasons.append(f"价格({close:.2f})跌破布林带下轨({bb_lower:.2f})，可能超卖")
        elif position > 0.8:
            score = -0.1
            reasons.append(f"价格接近布林带上轨(位置{position:.0%})")
        elif position < 0.2:
            score = 0.1
            reasons.append(f"价格接近布林带下轨(位置{position:.0%})")
        else:
            reasons.append(f"价格在布林带中部(位置{position:.0%})")

        # 布林带宽度变化（收口后突破更有意义）
        if band_width / bb_middle < 0.05:
            reasons.append("布林带收窄，可能即将变盘")

        signal = self._score_to_signal(score)
        confidence = min(abs(score) * 1.5, 1.0)

        return StrategyResult(signal, score, confidence, reasons, bb_upper * 1.02, bb_lower * 0.98, self.name)

    def _score_to_signal(self, score):
        if score >= 0.2: return Signal.BUY
        if score <= -0.2: return Signal.SELL
        return Signal.HOLD


class MACDTrendStrategy(BaseStrategy):
    """MACD趋势策略 - MACD柱状图方向和零轴位置"""
    name = "macd_trend"
    description = "MACD趋势策略：通过MACD柱状图方向、零轴位置和金叉死叉判断趋势"
    category = "trend"
    risk_level = "medium"
    params = {
        "fast_period": {"desc": "快线周期", "default": 12, "min": 8, "max": 20},
        "slow_period": {"desc": "慢线周期", "default": 26, "min": 20, "max": 40},
        "signal_period": {"desc": "信号线周期", "default": 9, "min": 5, "max": 15}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        macd = indicators.get("macd")
        signal_line = indicators.get("signal")
        histogram = indicators.get("histogram")

        if macd is None or signal_line is None:
            return StrategyResult(Signal.HOLD, 0, 0, ["MACD数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        # MACD金叉/死叉
        if macd > signal_line:
            score += 0.25
            reasons.append("MACD在信号线上方（多头）")
            if histogram and histogram > 0:
                score += 0.1
                reasons.append("MACD柱状图为正且扩大")
        else:
            score -= 0.25
            reasons.append("MACD在信号线下方（空头）")
            if histogram and histogram < 0:
                score -= 0.1
                reasons.append("MACD柱状图为负且扩大")

        # 零轴位置
        if macd > 0:
            score += 0.15
            reasons.append("MACD在零轴上方（中期趋势向上）")
        else:
            score -= 0.15
            reasons.append("MACD在零轴下方（中期趋势向下）")

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0)

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.3: return Signal.STRONG_BUY
        if score >= 0.15: return Signal.BUY
        if score >= 0.05: return Signal.WEAK_BUY
        if score <= -0.3: return Signal.STRONG_SELL
        if score <= -0.15: return Signal.SELL
        if score <= -0.05: return Signal.WEAK_SELL
        return Signal.HOLD


class DMITrendStrategy(BaseStrategy):
    """DMI趋势策略 - +DI/-DI方向和交叉"""
    name = "dmi_trend"
    description = "DMI趋势策略：通过+DI/-DI的交叉和方向判断多空力量对比"
    category = "trend"
    risk_level = "medium"
    params = {
        "period": {"desc": "DMI计算周期", "default": 14, "min": 7, "max": 28}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        dmi_plus = indicators.get("dmi_plus")
        dmi_minus = indicators.get("dmi_minus")
        adx = indicators.get("adx")

        if dmi_plus is None or dmi_minus is None:
            return StrategyResult(Signal.HOLD, 0, 0, ["DMI数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        # +DI vs -DI
        if dmi_plus > dmi_minus:
            score += 0.3
            reasons.append(f"+DI({dmi_plus:.1f}) > -DI({dmi_minus:.1f}) 多头占优")
        else:
            score -= 0.3
            reasons.append(f"+DI({dmi_plus:.1f}) < -DI({dmi_minus:.1f}) 空头占优")

        # ADX 趋势强度
        if adx is not None:
            if adx > 25:
                reasons.append(f"ADX({adx:.1f}) > 25 趋势明确")
                score *= 1.2
            elif adx < 20:
                reasons.append(f"ADX({adx:.1f}) < 20 趋势不明")
                score *= 0.5

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0)

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.3: return Signal.BUY
        if score >= 0.1: return Signal.WEAK_BUY
        if score <= -0.3: return Signal.SELL
        if score <= -0.1: return Signal.WEAK_SELL
        return Signal.HOLD


class ADXTrendStrengthStrategy(BaseStrategy):
    """ADX趋势强度策略 - 判断趋势是否足够强"""
    name = "adx_strength"
    description = "ADX趋势强度策略：ADX>25表示趋势明确，结合+DI/-DI判断方向"
    category = "trend"
    risk_level = "low"
    params = {
        "adx_threshold": {"desc": "ADX趋势阈值", "default": 25, "min": 15, "max": 40}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        adx = indicators.get("adx")
        dmi_plus = indicators.get("dmi_plus")
        dmi_minus = indicators.get("dmi_minus")

        if adx is None:
            return StrategyResult(Signal.HOLD, 0, 0, ["ADX数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        if adx < 20:
            reasons.append(f"ADX({adx:.1f}) < 20 无明显趋势，建议观望")
            return StrategyResult(Signal.HOLD, 0, 0.3, reasons, strategy_name=self.name)

        if adx > 25:
            reasons.append(f"ADX({adx:.1f}) > 25 趋势明确")
            if dmi_plus and dmi_minus:
                if dmi_plus > dmi_minus:
                    score = 0.35
                    reasons.append("多头趋势，建议买入")
                else:
                    score = -0.35
                    reasons.append("空头趋势，建议卖出")
        else:
            reasons.append(f"ADX({adx:.1f}) 趋势较弱")

        signal = Signal.BUY if score > 0.2 else (Signal.SELL if score < -0.2 else Signal.HOLD)
        confidence = min(abs(score), 1.0)

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)
