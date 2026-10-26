"""
比赛制胜策略
5个策略：强势回调、放量突破、连板追踪、缩量企稳、趋势加速
专为收益率排名比赛设计，侧重高胜率+高赔率
"""

from .base import BaseStrategy, StrategyResult, Signal
from typing import Dict, List


class StrongPullbackStrategy(BaseStrategy):
    """强势回调买入 — 强者恒强，回调是加仓机会"""
    name = "strong_pullback"
    description = "强势回调策略：近期涨幅>8%的强势股，回调到MA10附近缩量企稳时买入"
    category = "competition"
    risk_level = "high"
    params = {
        "min_5d_gain": {"desc": "近5日最低涨幅(%)", "default": 8, "min": 5, "max": 15},
        "pullback_range": {"desc": "回调幅度范围(%)", "default": 3, "min": 1, "max": 5},
        "volume_shrink": {"desc": "缩量比例", "default": 0.7, "min": 0.4, "max": 0.9},
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        if not history or len(history) < 10:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足，需要至少10日K线"],
                                  strategy_name=self.name)

        score = 0.0
        reasons = []
        close = indicators.get("close", stock_data.get("price", 0))
        ma10 = indicators.get("ma10")
        ma20 = indicators.get("ma20")

        if not close or not ma10:
            return StrategyResult(Signal.HOLD, 0, 0, ["关键指标不足"],
                                  strategy_name=self.name)

        # 1. 计算近5日累计涨幅
        if len(history) >= 6:
            price_5d_ago = history[-6].get("close", 0)
            if price_5d_ago > 0:
                gain_5d = (close / price_5d_ago - 1) * 100
            else:
                gain_5d = 0
        else:
            gain_5d = 0

        # 2. 当日涨跌幅
        change_pct = stock_data.get("change_pct", 0)

        # 3. 成交量缩放
        volume = stock_data.get("volume", 0)
        vol_list = [h.get("volume", 0) for h in history[-5:]]
        avg_vol_5d = sum(vol_list) / len(vol_list) if vol_list else 1
        vol_ratio = volume / avg_vol_5d if avg_vol_5d > 0 else 1

        # --- 核心条件 ---
        # 强势: 近5日涨幅 > 8%
        if gain_5d > self.params["min_5d_gain"]["default"]:
            score += 0.3
            reasons.append(f"近5日涨幅 {gain_5d:.1f}%，强势股")
        else:
            reasons.append(f"近5日涨幅 {gain_5d:.1f}%，未达强势标准")
            return StrategyResult(Signal.HOLD, 0, 0.1, reasons, strategy_name=self.name)

        # 回调: 当日下跌 1-3%
        pullback = self.params["pullback_range"]["default"]
        if -pullback <= change_pct <= -0.5:
            score += 0.25
            reasons.append(f"回调 {change_pct:.2f}%，接近支撑")
        elif change_pct > 0:
            reasons.append("未回调，等待更好入场点")
            return StrategyResult(Signal.HOLD, 0, 0.1, reasons, strategy_name=self.name)

        # MA10支撑: 价格在MA10附近(±2%)
        ma10_distance = abs(close - ma10) / ma10 * 100 if ma10 > 0 else 999
        if ma10_distance < 2:
            score += 0.2
            reasons.append(f"价格距MA10仅 {ma10_distance:.1f}%，支撑有效")
        elif ma20 and abs(close - ma20) / ma20 * 100 < 3:
            score += 0.15
            reasons.append("价格在MA20附近，次级支撑")

        # 缩量确认
        shrink = self.params["volume_shrink"]["default"]
        if vol_ratio < shrink:
            score += 0.15
            reasons.append(f"缩量回调(量比{vol_ratio:.2f})，抛压减轻")
        else:
            reasons.append(f"成交量偏大(量比{vol_ratio:.2f})，回调未充分")

        signal = self._score_to_signal(score)
        confidence = min(0.3 + abs(score) * 0.8, 0.95)
        return StrategyResult(
            signal, score, confidence, reasons,
            target_price=close * 1.10,
            stop_loss=close * 0.94,
            strategy_name=self.name
        )


class VolumeBreakoutCompetition(BaseStrategy):
    """放量突破平台 — 横盘整理后突破，趋势启动信号"""
    name = "competition_breakout"
    description = "放量突破策略：股价突破近10日横盘区间，伴随放量确认"
    category = "competition"
    risk_level = "high"
    params = {
        "consolidation_days": {"desc": "横盘天数", "default": 10, "min": 5, "max": 20},
        "max_range_pct": {"desc": "横盘最大振幅(%)", "default": 5, "min": 3, "max": 8},
        "volume_ratio_min": {"desc": "突破日最小量比", "default": 1.5, "min": 1.2, "max": 3.0},
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        days = self.params["consolidation_days"]["default"]
        if not history or len(history) < days + 5:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足"],
                                  strategy_name=self.name)

        score = 0.0
        reasons = []
        close = stock_data.get("price", 0)
        high = stock_data.get("high", 0)
        low = stock_data.get("low", 0)
        volume = stock_data.get("volume", 0)

        if not close:
            return StrategyResult(Signal.HOLD, 0, 0, ["价格数据不足"],
                                  strategy_name=self.name)

        # 1. 计算近N日横盘区间
        recent = history[-days:]
        period_high = max(h.get("high", 0) for h in recent)
        period_low = min(h.get("low", 0) for h in recent)
        if period_low <= 0:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据异常"],
                                  strategy_name=self.name)
        range_pct = (period_high - period_low) / period_low * 100

        # 2. 横盘判定
        max_range = self.params["max_range_pct"]["default"]
        if range_pct > max_range:
            reasons.append(f"近{days}日振幅 {range_pct:.1f}%，非横盘")
            return StrategyResult(Signal.HOLD, 0, 0.1, reasons, strategy_name=self.name)
        score += 0.2
        reasons.append(f"近{days}日振幅仅 {range_pct:.1f}%，横盘整理")

        # 3. 突破判定: 收盘价 > 横盘最高价
        if close > period_high:
            score += 0.35
            reasons.append(f"收盘价 {close:.2f} 突破平台高点 {period_high:.2f}")
        elif close > period_high * 0.98:
            score += 0.15
            reasons.append(f"接近突破(距高点{((close/period_high)-1)*100:.1f}%)")
        else:
            reasons.append("尚未突破")
            return StrategyResult(Signal.WEAK_BUY, score, 0.2, reasons,
                                  strategy_name=self.name)

        # 4. 放量确认
        vol_list = [h.get("volume", 0) for h in history[-5:]]
        avg_vol = sum(vol_list) / len(vol_list) if vol_list else 1
        vol_ratio = volume / avg_vol if avg_vol > 0 else 1
        min_vol_ratio = self.params["volume_ratio_min"]["default"]

        if vol_ratio >= min_vol_ratio:
            score += 0.25
            reasons.append(f"放量突破(量比{vol_ratio:.1f})")
        elif vol_ratio >= 1.2:
            score += 0.1
            reasons.append(f"温和放量(量比{vol_ratio:.1f})")
        else:
            reasons.append(f"量能不足(量比{vol_ratio:.1f})，假突破风险")

        signal = self._score_to_signal(score)
        confidence = min(0.3 + abs(score) * 0.7, 0.9)
        return StrategyResult(
            signal, score, confidence, reasons,
            target_price=close * 1.12,
            stop_loss=period_low * 0.98,
            strategy_name=self.name
        )


class LimitUpContinuationStrategy(BaseStrategy):
    """连板追踪 — A股特色，涨停板效应强者恒强"""
    name = "limit_up_continue"
    description = "连板追踪策略：前一日涨停，次日高开时追入，利用涨停板惯性"
    category = "competition"
    risk_level = "high"
    params = {
        "min_gap_pct": {"desc": "最小高开幅度(%)", "default": 2.0, "min": 0.5, "max": 5.0},
        "max_board_days": {"desc": "最大连板天数", "default": 3, "min": 1, "max": 5},
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        if not history or len(history) < 5:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足"],
                                  strategy_name=self.name)

        score = 0.0
        reasons = []
        open_price = stock_data.get("open", 0)
        close = stock_data.get("price", 0)
        pre_close = history[-1].get("close", 0)
        high = stock_data.get("high", 0)
        low = stock_data.get("low", 0)

        if not all([open_price, close, pre_close]):
            return StrategyResult(Signal.HOLD, 0, 0, ["价格数据不足"],
                                  strategy_name=self.name)

        # 1. 前一日是否涨停
        prev_change = history[-1].get("change_pct", 0)
        if prev_change is None:
            prev_change = 0
        # 也检查前一日K线: 涨停判断
        prev_high = history[-1].get("high", 0)
        prev_close_inner = history[-1].get("close", 0)
        prev_pre_close = history[-2].get("close", 0) if len(history) >= 2 else 0

        is_limit_up = False
        if prev_pre_close > 0:
            prev_chg = (prev_close_inner / prev_pre_close - 1) * 100
            is_limit_up = prev_chg >= 9.8

        if not is_limit_up:
            reasons.append("前一日未涨停")
            return StrategyResult(Signal.HOLD, 0, 0, reasons, strategy_name=self.name)

        score += 0.3
        reasons.append("前一日涨停板")

        # 2. 检查连板天数
        board_count = 1
        for i in range(len(history) - 1, 0, -1):
            if i < 1:
                break
            h_close = history[i].get("close", 0)
            h_pre = history[i - 1].get("close", 0)
            if h_pre > 0 and (h_close / h_pre - 1) * 100 >= 9.8:
                board_count += 1
            else:
                break

        max_board = self.params["max_board_days"]["default"]
        if board_count > max_board:
            reasons.append(f"已连板{board_count}天，超过上限{max_board}")
            return StrategyResult(Signal.WEAK_SELL, -0.1, 0.3, reasons,
                                  strategy_name=self.name)

        if board_count >= 2:
            score += 0.15
            reasons.append(f"已连板{board_count}天，强势延续")

        # 3. 当日高开
        gap_pct = (open_price / pre_close - 1) * 100 if pre_close > 0 else 0
        min_gap = self.params["min_gap_pct"]["default"]

        if gap_pct >= min_gap:
            score += 0.25
            reasons.append(f"高开 {gap_pct:.1f}%")
        elif gap_pct >= 0.5:
            score += 0.1
            reasons.append(f"小幅高开 {gap_pct:.1f}%")
        elif gap_pct < -1:
            score -= 0.3
            reasons.append(f"低开 {gap_pct:.1f}%，涨停板效应消失")
        else:
            reasons.append(f"平开 {gap_pct:.1f}%")

        # 4. 非一字板（有交易空间）
        if high > low and high != low:
            score += 0.1
            reasons.append("非一字板，有交易空间")
        else:
            reasons.append("一字板，无法买入")
            return StrategyResult(Signal.HOLD, 0, 0, reasons, strategy_name=self.name)

        # 5. 开盘后走势
        if close > open_price:
            score += 0.1
            reasons.append("高开高走，强势")
        elif close < open_price * 0.98:
            score -= 0.2
            reasons.append("高开低走，封板失败风险")

        signal = self._score_to_signal(score)
        confidence = min(0.4 + abs(score) * 0.6, 0.85)
        return StrategyResult(
            signal, score, confidence, reasons,
            target_price=close * 1.10,
            stop_loss=pre_close * 0.95,
            strategy_name=self.name
        )


class VolumeShrinkReversal(BaseStrategy):
    """缩量企稳反转 — 恐慌释放完毕，筹码锁定"""
    name = "volume_shrink_reversal"
    description = "缩量企稳策略：下跌后成交量极度萎缩，价格企稳收阳，反转信号"
    category = "competition"
    risk_level = "medium"
    params = {
        "min_decline_pct": {"desc": "近5日最小跌幅(%)", "default": -5, "min": -10, "max": -2},
        "shrink_ratio": {"desc": "缩量比例(越小越缩)", "default": 0.5, "min": 0.2, "max": 0.8},
        "ma_proximity": {"desc": "距MA20最大距离(%)", "default": 3, "min": 1, "max": 5},
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        if not history or len(history) < 15:
            return StrategyResult(Signal.HOLD, 0, 0, ["数据不足，需要至少15日K线"],
                                  strategy_name=self.name)

        score = 0.0
        reasons = []
        close = stock_data.get("price", 0)
        open_price = stock_data.get("open", 0)
        volume = stock_data.get("volume", 0)
        ma20 = indicators.get("ma20")

        if not close or not open_price:
            return StrategyResult(Signal.HOLD, 0, 0, ["价格数据不足"],
                                  strategy_name=self.name)

        # 1. 近5日累计跌幅
        if len(history) >= 6:
            price_5d_ago = history[-6].get("close", 0)
            decline_5d = ((close / price_5d_ago) - 1) * 100 if price_5d_ago > 0 else 0
        else:
            decline_5d = 0

        min_decline = self.params["min_decline_pct"]["default"]
        if decline_5d > min_decline:
            reasons.append(f"近5日跌幅 {decline_5d:.1f}%，未达下跌标准")
            return StrategyResult(Signal.HOLD, 0, 0.1, reasons, strategy_name=self.name)

        score += 0.25
        reasons.append(f"近5日下跌 {decline_5d:.1f}%，存在反弹需求")

        # 2. 缩量确认
        vol_list_10d = [h.get("volume", 0) for h in history[-10:]]
        avg_vol_10d = sum(vol_list_10d) / len(vol_list_10d) if vol_list_10d else 1
        vol_ratio = volume / avg_vol_10d if avg_vol_10d > 0 else 1
        shrink = self.params["shrink_ratio"]["default"]

        if vol_ratio < shrink:
            score += 0.25
            reasons.append(f"极度缩量(量比{vol_ratio:.2f})，抛压枯竭")
        elif vol_ratio < 0.8:
            score += 0.1
            reasons.append(f"缩量(量比{vol_ratio:.2f})")
        else:
            reasons.append(f"量能未萎缩(量比{vol_ratio:.2f})")
            return StrategyResult(Signal.WEAK_BUY, score, 0.2, reasons,
                                  strategy_name=self.name)

        # 3. 收阳线（企稳信号）
        if close > open_price:
            score += 0.2
            reasons.append("收阳线，止跌企稳")
        else:
            reasons.append("仍收阴线，尚未企稳")
            return StrategyResult(Signal.WEAK_BUY, score, 0.15, reasons,
                                  strategy_name=self.name)

        # 4. MA20支撑
        if ma20 and ma20 > 0:
            distance = abs(close - ma20) / ma20 * 100
            proximity = self.params["ma_proximity"]["default"]
            if distance < proximity:
                score += 0.15
                reasons.append(f"价格在MA20附近({distance:.1f}%)，有支撑")

        signal = self._score_to_signal(score)
        confidence = min(0.3 + abs(score) * 0.7, 0.85)
        return StrategyResult(
            signal, score, confidence, reasons,
            target_price=close * 1.08,
            stop_loss=close * 0.95,
            strategy_name=self.name
        )


class TrendAccelerationStrategy(BaseStrategy):
    """趋势加速 — 多头排列+MACD放大，强趋势最佳入场"""
    name = "trend_acceleration"
    description = "趋势加速策略：MA多头排列，MACD柱状图放大，RSI强势区间，趋势加速启动"
    category = "competition"
    risk_level = "high"
    params = {
        "rsi_min": {"desc": "RSI最低要求", "default": 50, "min": 40, "max": 60},
        "rsi_max": {"desc": "RSI最高限制(避免超买)", "default": 72, "min": 65, "max": 80},
    }

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs):
        score = 0.0
        reasons = []

        close = indicators.get("close", stock_data.get("price", 0))
        ma5 = indicators.get("ma5")
        ma10 = indicators.get("ma10")
        ma20 = indicators.get("ma20")
        rsi = indicators.get("rsi")
        histogram = indicators.get("histogram")

        if not all([close, ma5, ma10, ma20]):
            return StrategyResult(Signal.HOLD, 0, 0, ["均线数据不足"],
                                  strategy_name=self.name)

        # 1. 多头排列: MA5 > MA10 > MA20
        if ma5 > ma10 > ma20:
            score += 0.3
            reasons.append(f"均线多头排列(MA5={ma5:.2f}>MA10={ma10:.2f}>MA20={ma20:.2f})")
        else:
            reasons.append("均线未形成多头排列")
            return StrategyResult(Signal.HOLD, 0, 0.1, reasons, strategy_name=self.name)

        # 2. 价格在MA5上方（强势）
        if close > ma5:
            score += 0.15
            reasons.append("价格在MA5上方，趋势强劲")
        elif close > ma10:
            score += 0.05
            reasons.append("价格在MA5下方但仍在MA10上方")

        # 3. MACD histogram > 0 且放大
        if histogram is not None:
            if histogram > 0:
                score += 0.15
                reasons.append(f"MACD柱状图为正({histogram:.4f})")
                # 检查是否放大（需要历史数据）
                if history and len(history) >= 2:
                    # 用indicators中的历史近似判断
                    prev_histogram = kwargs.get("prev_histogram")
                    if prev_histogram is not None and histogram > prev_histogram:
                        score += 0.15
                        reasons.append("MACD柱状图放大，趋势加速")
            else:
                score -= 0.1
                reasons.append(f"MACD柱状图为负({histogram:.4f})")

        # 4. RSI在强势区间
        rsi_min = self.params["rsi_min"]["default"]
        rsi_max = self.params["rsi_max"]["default"]
        if rsi is not None:
            if rsi_min <= rsi <= rsi_max:
                score += 0.15
                reasons.append(f"RSI={rsi:.1f}，强势但未超买")
            elif rsi > rsi_max:
                score -= 0.1
                reasons.append(f"RSI={rsi:.1f}，超买风险")
            elif rsi < 40:
                reasons.append(f"RSI={rsi:.1f}，偏弱")
            else:
                reasons.append(f"RSI={rsi:.1f}")

        # 5. 放量配合
        volume = stock_data.get("volume", 0)
        if history and len(history) >= 5:
            avg_vol = sum(h.get("volume", 0) for h in history[-5:]) / 5
            if avg_vol > 0:
                vol_ratio = volume / avg_vol
                if vol_ratio > 1.3:
                    score += 0.1
                    reasons.append(f"放量(量比{vol_ratio:.1f})")

        signal = self._score_to_signal(score)
        confidence = min(0.3 + abs(score) * 0.7, 0.9)
        return StrategyResult(
            signal, score, confidence, reasons,
            target_price=close * 1.12,
            stop_loss=ma20 * 0.97,
            strategy_name=self.name
        )
