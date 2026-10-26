"""
超短线策略
5个策略：跳空高开、均值回归、区间突破、短周期RSI、均线支撑反弹
持仓周期：1-5天
"""

from .base import BaseStrategy, StrategyResult, Signal
from typing import Dict, List


class GapUpStrategy(BaseStrategy):
    """跳空高开追涨策略 - 1天持仓"""
    name = "gap_up"
    description = "跳空高开策略：开盘价明显高于昨日收盘价，且放量配合，追涨买入"
    category = "shortterm"
    risk_level = "high"
    params = {
        "min_gap_pct": {"desc": "最小跳空幅度(%)", "default": 1.5, "min": 0.5, "max": 5.0},
        "volume_confirm": {"desc": "成交量放大倍数", "default": 1.5, "min": 1.2, "max": 3.0}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        if not history or len(history) < 5:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足，需要至少5日K线"], strategy_name=self.name)

        score = 0.0
        reasons = []

        open_price = stock_data.get("open", 0)
        close = stock_data.get("price", 0)
        prev_close = history[-1].get("close", 0) if history else 0
        volume = stock_data.get("volume", 0)

        # 计算跳空幅度
        if prev_close > 0:
            gap_pct = (open_price - prev_close) / prev_close * 100
        else:
            gap_pct = 0

        # 计算前5日平均成交量
        vol_list = [h.get("volume", 0) for h in history[-5:]]
        avg_vol = sum(vol_list) / len(vol_list) if vol_list else 1
        vol_ratio = volume / avg_vol if avg_vol > 0 else 1

        # 跳空高开
        if gap_pct > 1.5:
            score += 0.35
            reasons.append(f"跳空高开 {gap_pct:.2f}%")
            # 放量确认
            if vol_ratio > 1.5:
                score += 0.15
                reasons.append(f"成交量放大 {vol_ratio:.1f} 倍，确认强势")
            else:
                reasons.append("成交量未明显放大，追涨风险较高")
            # 开盘后继续上涨
            if close > open_price:
                score += 0.1
                reasons.append("高开高走，强势延续")
            elif close < open_price:
                score -= 0.15
                reasons.append("高开低走，抛压较重")
        elif gap_pct < -1.5:
            score -= 0.3
            reasons.append(f"跳空低开 {gap_pct:.2f}%，回避")
        else:
            reasons.append(f"开盘平稳（跳空 {gap_pct:.2f}%），无明显机会")

        # 目标价/止损价
        target_price = close * 1.03  # 3%止盈
        stop_loss = prev_close * 0.97  # 跳空缺口下沿止损

        signal = self._score_to_signal(score)
        confidence = min(abs(score) * 1.2, 1.0)

        return StrategyResult(signal, score, confidence, reasons,
                              target_price=target_price, stop_loss=stop_loss,
                              strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.4: return Signal.STRONG_BUY
        if score >= 0.2: return Signal.BUY
        if score >= 0.05: return Signal.WEAK_BUY
        if score <= -0.3: return Signal.STRONG_SELL
        if score <= -0.15: return Signal.SELL
        return Signal.HOLD


class MeanReversionStrategy(BaseStrategy):
    """均值回归策略 - 3天持仓"""
    name = "mean_reversion"
    description = "均值回归策略：价格偏离3日均线过远时反向操作，偏离越大回归概率越高"
    category = "shortterm"
    risk_level = "medium"
    params = {
        "ma_period": {"desc": "均线周期", "default": 3, "min": 2, "max": 5},
        "deviation_pct": {"desc": "偏离阈值(%)", "default": 2.0, "min": 1.0, "max": 5.0}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        if not history or len(history) < 5:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        close = stock_data.get("price", 0)
        closes = [h.get("close", 0) for h in history[-5:]]
        closes.append(close)

        # 3日均线
        ma3 = sum(closes[-3:]) / 3
        # 5日均线
        ma5 = sum(closes[-5:]) / 5

        # 计算偏离度
        deviation_3 = (close - ma3) / ma3 * 100 if ma3 > 0 else 0
        deviation_5 = (close - ma5) / ma5 * 100 if ma5 > 0 else 0

        # 价格严重偏离3日均线 → 回归信号
        if deviation_3 < -2.0:
            score += 0.35
            reasons.append(f"价格低于3日均线 {abs(deviation_3):.2f}%，超跌回归")
        elif deviation_3 > 2.0:
            score -= 0.3
            reasons.append(f"价格高于3日均线 {deviation_3:.2f}%，超涨回调")
        elif deviation_3 < -1.0:
            score += 0.15
            reasons.append(f"价格略低于3日均线 {abs(deviation_3):.2f}%")
        elif deviation_3 > 1.0:
            score -= 0.1
            reasons.append(f"价格略高于3日均线 {deviation_3:.2f}%")

        # 5日均线方向
        if ma5 > closes[-6] if len(closes) > 5 else ma5:
            score += 0.1
            reasons.append("5日均线趋势向上")
        else:
            score -= 0.05
            reasons.append("5日均线趋势向下")

        # RSI辅助
        rsi = indicators.get("rsi")
        if rsi:
            if rsi < 30 and score > 0:
                score += 0.1
                reasons.append(f"RSI({rsi:.1f}) 超卖，增强回归信号")
            elif rsi > 70 and score < 0:
                score -= 0.1
                reasons.append(f"RSI({rsi:.1f}) 超买，增强回调信号")

        target_price = ma3  # 目标回归均线
        stop_loss = close * 0.97  # 3%止损

        signal = self._score_to_signal(score)
        confidence = min(abs(score) * 1.1, 1.0)

        return StrategyResult(signal, score, confidence, reasons,
                              target_price=target_price, stop_loss=stop_loss,
                              strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.35: return Signal.STRONG_BUY
        if score >= 0.2: return Signal.BUY
        if score >= 0.05: return Signal.WEAK_BUY
        if score <= -0.3: return Signal.STRONG_SELL
        if score <= -0.15: return Signal.SELL
        return Signal.HOLD


class RangeBreakoutStrategy(BaseStrategy):
    """区间突破策略 - 3天持仓"""
    name = "range_breakout"
    description = "区间突破策略：价格突破近3日震荡区间上沿买入，突破下沿卖出"
    category = "shortterm"
    risk_level = "medium"
    params = {
        "range_days": {"desc": "震荡区间天数", "default": 3, "min": 2, "max": 5},
        "breakout_pct": {"desc": "突破确认幅度(%)", "default": 0.5, "min": 0.2, "max": 2.0}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        if not history or len(history) < 5:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足"], strategy_name=self.name)

        score = 0.0
        reasons = []

        close = stock_data.get("price", 0)
        recent = history[-3:]

        # 近3日高低点
        range_high = max(h.get("high", h.get("close", 0)) for h in recent)
        range_low = min(h.get("low", h.get("close", 0)) for h in recent)
        range_width = (range_high - range_low) / range_low * 100 if range_low > 0 else 0

        # 突破判断
        if close > range_high:
            breakout_pct = (close - range_high) / range_high * 100
            score += 0.35
            reasons.append(f"突破近3日高点({range_high:.2f})，突破幅度{breakout_pct:.2f}%")
            # 量能确认
            vol = stock_data.get("volume", 0)
            avg_vol = sum(h.get("volume", 0) for h in recent) / len(recent) if recent else 1
            if vol > avg_vol * 1.3:
                score += 0.1
                reasons.append("放量突破，有效性较高")
            else:
                reasons.append("缩量突破，需谨慎")
        elif close < range_low:
            breakdown_pct = (range_low - close) / range_low * 100
            score -= 0.35
            reasons.append(f"跌破近3日低点({range_low:.2f})，跌破幅度{breakdown_pct:.2f}%")
        else:
            reasons.append(f"价格在近3日区间内（{range_low:.2f} ~ {range_high:.2f}），等待突破")

        # 区间宽度
        if range_width < 2:
            reasons.append(f"震荡区间较窄({range_width:.1f}%)，突破后动能可能较大")

        target_price = close + (close - range_low)  # 突破目标=区间高度
        stop_loss = range_low * 0.99  # 区间下沿止损

        signal = self._score_to_signal(score)
        confidence = min(abs(score) * 1.1, 1.0)

        return StrategyResult(signal, score, confidence, reasons,
                              target_price=target_price, stop_loss=stop_loss,
                              strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.3: return Signal.BUY
        if score >= 0.15: return Signal.WEAK_BUY
        if score <= -0.3: return Signal.SELL
        if score <= -0.15: return Signal.WEAK_SELL
        return Signal.HOLD


class ShortTermRSIStrategy(BaseStrategy):
    """短周期RSI策略 - 1-2天持仓"""
    name = "short_rsi"
    description = "短周期RSI策略：使用5周期RSI捕捉超短线超买超卖，RSI(5)<15买入，RSI(5)>85卖出"
    category = "shortterm"
    risk_level = "high"
    params = {
        "rsi_period": {"desc": "RSI周期", "default": 5, "min": 3, "max": 7},
        "oversold": {"desc": "超卖阈值", "default": 15, "min": 10, "max": 25},
        "overbought": {"desc": "超买阈值", "default": 85, "min": 75, "max": 95}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        if not history or len(history) < 7:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足，需要至少7日K线"], strategy_name=self.name)

        score = 0.0
        reasons = []

        # 计算5周期RSI
        closes = [h.get("close", 0) for h in history[-6:]]
        close = stock_data.get("price", 0)
        closes.append(close)

        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        if not gains or not losses:
            return StrategyResult(Signal.HOLD, 0, 0, ["价格数据不足"], strategy_name=self.name)

        avg_gain = sum(gains[-5:]) / 5
        avg_loss = sum(losses[-5:]) / 5

        if avg_loss == 0:
            rsi5 = 100
        else:
            rs = avg_gain / avg_loss
            rsi5 = 100 - 100 / (1 + rs)

        # 标准RSI(14)作为参考
        rsi14 = indicators.get("rsi")

        reasons.append(f"5周期RSI = {rsi5:.1f}")
        if rsi14:
            reasons.append(f"14周期RSI = {rsi14:.1f}")

        # 极端RSI信号
        if rsi5 < 15:
            score = 0.4
            reasons.append("5周期RSI极度超卖，1-2天反弹概率高")
        elif rsi5 < 25:
            score = 0.2
            reasons.append("5周期RSI超卖，短线反弹机会")
        elif rsi5 > 85:
            score = -0.4
            reasons.append("5周期RSI极度超买，1-2天回调概率高")
        elif rsi5 > 75:
            score = -0.2
            reasons.append("5周期RSI超买，短线回调风险")
        # RSI从极值区回归
        elif 30 <= rsi5 <= 50 and rsi5 > rsi14 if rsi14 else False:
            score = 0.1
            reasons.append("RSI从低位回升，短期动能恢复")
        elif 50 <= rsi5 <= 70 and rsi5 < rsi14 if rsi14 else False:
            score = -0.1
            reasons.append("RSI从高位回落，短期动能减弱")

        # 目标价和止损
        atr = indicators.get("atr", 0)
        target_price = close + atr * 1.5 if atr else close * 1.02
        stop_loss = close - atr * 1.0 if atr else close * 0.98

        signal = self._score_to_signal(score)
        confidence = min(abs(score) * 1.3, 1.0)

        return StrategyResult(signal, score, confidence, reasons,
                              target_price=target_price, stop_loss=stop_loss,
                              strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.35: return Signal.STRONG_BUY
        if score >= 0.15: return Signal.BUY
        if score >= 0.05: return Signal.WEAK_BUY
        if score <= -0.35: return Signal.STRONG_SELL
        if score <= -0.15: return Signal.SELL
        return Signal.HOLD


class MABounceStrategy(BaseStrategy):
    """均线支撑反弹策略 - 3-5天持仓"""
    name = "ma_bounce"
    description = "均线支撑反弹策略：价格回踩3/5日均线获得支撑时买入，跌破均线止损"
    category = "shortterm"
    risk_level = "medium"
    params = {
        "ma_period": {"desc": "支撑均线周期", "default": 5, "min": 3, "max": 10},
        "bounce_threshold": {"desc": "反弹确认幅度(%)", "default": 0.3, "min": 0.1, "max": 1.0}
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        if not history or len(history) < 10:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足，需要至少10日K线"], strategy_name=self.name)

        score = 0.0
        reasons = []

        close = stock_data.get("price", 0)
        low = stock_data.get("low", close)
        closes = [h.get("close", 0) for h in history[-10:]]
        lows = [h.get("low", h.get("close", 0)) for h in history[-10:]]

        # 3日均线和5日均线
        ma3 = sum(closes[-3:]) / 3
        ma5 = sum(closes[-5:]) / 5

        # 当前价格与均线关系
        dist_ma3 = (close - ma3) / ma3 * 100 if ma3 > 0 else 0
        dist_ma5 = (close - ma5) / ma5 * 100 if ma5 > 0 else 0

        # 均线多头排列
        ma3_rising = ma3 > sum(closes[-4:-1]) / 3 if len(closes) >= 4 else False

        # 回踩MA5获得支撑
        if close >= ma5 * 0.99 and close <= ma5 * 1.01 and ma3_rising:
            score += 0.3
            reasons.append(f"回踩5日均线({ma5:.2f})获得支撑")
            # 检查是否有下影线（盘中跌破后收回）
            if low < ma5 and close > ma5:
                score += 0.1
                reasons.append("盘中跌破均线后收回，支撑有效")
        elif close >= ma3 * 0.99 and close <= ma3 * 1.01 and ma3_rising:
            score += 0.2
            reasons.append(f"回踩3日均线({ma3:.2f})获得支撑")
        elif close < ma5:
            score -= 0.2
            reasons.append(f"价格跌破5日均线({ma5:.2f})，短期偏弱")
            if close < ma3:
                score -= 0.1
                reasons.append("价格跌破3日均线，进一步走弱")

        # 量价配合
        change_pct = stock_data.get("change_pct", 0)
        if change_pct > 0 and score > 0:
            score += 0.1
            reasons.append("阳线确认反弹")
        elif change_pct < -1 and score > 0:
            score -= 0.1
            reasons.append("阴线较大，反弹力度不足")

        target_price = close * 1.03  # 3%目标
        stop_loss = ma5 * 0.98 if ma5 > 0 else close * 0.97  # 均线下方止损

        signal = self._score_to_signal(score)
        confidence = min(abs(score) * 1.1, 1.0)

        return StrategyResult(signal, score, confidence, reasons,
                              target_price=target_price, stop_loss=stop_loss,
                              strategy_name=self.name)

    def _score_to_signal(self, score):
        if score >= 0.3: return Signal.BUY
        if score >= 0.15: return Signal.WEAK_BUY
        if score <= -0.2: return Signal.SELL
        if score <= -0.1: return Signal.WEAK_SELL
        return Signal.HOLD
