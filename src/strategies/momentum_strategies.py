"""
动量策略
4个策略：RSI超卖反弹、MACD金叉、KDJ金叉、量价齐升
"""

from .base import BaseStrategy, StrategyResult, Signal
from typing import Dict, List


class RSIOverboughtOversoldStrategy(BaseStrategy):
    """RSI超买超卖策略 - RSI从极端区域回归"""
    name = "rsi_extreme"
    description = "RSI超买超卖策略：RSI<30超卖时买入，RSI>70超买时卖出"
    category = "momentum"
    risk_level = "medium"
    params = {
        "oversold": {"desc": "超卖阈值", "default": 30, "min": 15, "max": 40},
        "overbought": {"desc": "超买阈值", "default": 70, "min": 60, "max": 85}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        rsi = indicators.get("rsi")
        if rsi is None:
            return StrategyResult(Signal.HOLD, 0, 0, ["RSI数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        if rsi < 20:
            score = 0.4
            reasons.append(f"RSI({rsi:.1f}) 极度超卖，强烈反弹信号")
        elif rsi < 30:
            score = 0.25
            reasons.append(f"RSI({rsi:.1f}) 超卖，可能反弹")
        elif rsi > 80:
            score = -0.4
            reasons.append(f"RSI({rsi:.1f}) 极度超买，强烈回调信号")
        elif rsi > 70:
            score = -0.25
            reasons.append(f"RSI({rsi:.1f}) 超买，可能回调")
        elif 40 <= rsi <= 60:
            reasons.append(f"RSI({rsi:.1f}) 中性区间")
        elif rsi < 40:
            score = 0.05
            reasons.append(f"RSI({rsi:.1f}) 偏弱")
        elif rsi > 60:
            score = -0.05
            reasons.append(f"RSI({rsi:.1f}) 偏强")

        signal = self._score_to_signal(score)
        confidence = min(abs(score) * 1.2, 1.0)

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.35: return Signal.STRONG_BUY
        if score >= 0.2: return Signal.BUY
        if score >= 0.05: return Signal.WEAK_BUY
        if score <= -0.35: return Signal.STRONG_SELL
        if score <= -0.2: return Signal.SELL
        if score <= -0.05: return Signal.WEAK_SELL
        return Signal.HOLD


class MACDCrossStrategy(BaseStrategy):
    """MACD金叉死叉策略"""
    name = "macd_cross"
    description = "MACD金叉死叉策略：MACD线上穿信号线为金叉买入，下穿为死叉卖出"
    category = "momentum"
    risk_level = "medium"
    params = {
        "require_zero_axis": {"desc": "金叉是否要求在零轴附近", "default": False}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        macd = indicators.get("macd")
        signal_line = indicators.get("signal")
        histogram = indicators.get("histogram")

        if macd is None or signal_line is None:
            return StrategyResult(Signal.HOLD, 0, 0, ["MACD数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        # 金叉判断
        if macd > signal_line:
            score += 0.3
            reasons.append("MACD金叉（MACD > Signal）")
            if histogram and histogram > 0:
                score += 0.1
                reasons.append("柱状图为正，动能增强")
        else:
            score -= 0.3
            reasons.append("MACD死叉（MACD < Signal）")
            if histogram and histogram < 0:
                score -= 0.1
                reasons.append("柱状图为负，动能减弱")

        # 零轴判断
        if macd > 0 and signal_line > 0:
            score += 0.1
            reasons.append("双线在零轴上方，中期趋势向好")
        elif macd < 0 and signal_line < 0:
            score -= 0.1
            reasons.append("双线在零轴下方，中期趋势偏弱")

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0)

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.3: return Signal.STRONG_BUY
        if score >= 0.15: return Signal.BUY
        if score <= -0.3: return Signal.STRONG_SELL
        if score <= -0.15: return Signal.SELL
        return Signal.HOLD


class KDJCrossStrategy(BaseStrategy):
    """KDJ金叉死叉策略"""
    name = "kdj_cross"
    description = "KDJ金叉死叉策略：K线上穿D线为金叉买入，下穿为死叉卖出"
    category = "momentum"
    risk_level = "high"
    params = {
        "overbought": {"desc": "超买区", "default": 80, "min": 70, "max": 90},
        "oversold": {"desc": "超卖区", "default": 20, "min": 10, "max": 30}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        k = indicators.get("kdj_k")
        d = indicators.get("kdj_d")
        j = indicators.get("kdj_j")

        if k is None or d is None:
            return StrategyResult(Signal.HOLD, 0, 0, ["KDJ数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        # K/D 交叉
        if k > d:
            score += 0.25
            reasons.append(f"K({k:.1f}) > D({d:.1f}) KDJ金叉")
            if j and j > 80:
                reasons.append(f"J({j:.1f}) 超买，注意风险")
        else:
            score -= 0.25
            reasons.append(f"K({k:.1f}) < D({d:.1f}) KDJ死叉")
            if j and j < 20:
                reasons.append(f"J({j:.1f}) 超卖，可能反弹")
                score += 0.1

        # 超买超卖区
        if k < 20 and d < 20:
            score += 0.15
            reasons.append("KDJ在超卖区，反弹概率大")
        elif k > 80 and d > 80:
            score -= 0.15
            reasons.append("KDJ在超买区，回调概率大")

        signal = self._score_to_signal(score)
        confidence = min(abs(score), 1.0)

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.3: return Signal.BUY
        if score >= 0.1: return Signal.WEAK_BUY
        if score <= -0.3: return Signal.SELL
        if score <= -0.1: return Signal.WEAK_SELL
        return Signal.HOLD


class VolumePriceMomentumStrategy(BaseStrategy):
    """量价齐升策略"""
    name = "vol_price_up"
    description = "量价齐升策略：价格上涨同时成交量放大，确认上涨动能"
    category = "momentum"
    risk_level = "medium"
    params = {
        "price_threshold": {"desc": "涨幅阈值(%)", "default": 1.0, "min": 0.5, "max": 5.0},
        "volume_ratio": {"desc": "放量倍数", "default": 1.5, "min": 1.2, "max": 3.0}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        change_pct = stock_data.get("change_pct", 0)
        turnover = stock_data.get("turnover", 0)
        volume = stock_data.get("volume", 0)

        score = 0.0
        reasons = []

        # 价涨量增
        if change_pct > 1 and turnover > 1.0:
            score += 0.35
            reasons.append(f"量价齐升：涨幅{change_pct:.2f}%，换手率{turnover:.2f}%")
        elif change_pct > 0.5 and turnover > 1.5:
            score += 0.25
            reasons.append(f"温和放量上涨：涨幅{change_pct:.2f}%")
        # 价跌量缩
        elif change_pct < -1 and turnover < 0.5:
            score += 0.1
            reasons.append(f"缩量下跌：跌幅{change_pct:.2f}%，抛压减轻")
        # 价涨量缩（背离）
        elif change_pct > 1 and turnover < 0.5:
            score -= 0.15
            reasons.append(f"量价背离：上涨但缩量，持续性存疑")
        # 价跌量增
        elif change_pct < -1 and turnover > 1.5:
            score -= 0.3
            reasons.append(f"放量下跌：跌幅{change_pct:.2f}%，恐慌抛售")

        signal = self._score_to_signal(score)
        confidence = min(abs(score) * 1.1, 1.0)

        return StrategyResult(signal, score, confidence, reasons, strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.3: return Signal.BUY
        if score >= 0.15: return Signal.WEAK_BUY
        if score <= -0.25: return Signal.SELL
        if score <= -0.1: return Signal.WEAK_SELL
        return Signal.HOLD
