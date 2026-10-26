"""
多时间框架分析模块
同时分析周线、日线、小时线，信号共振时加分
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class MultiTimeframeAnalyzer:
    """多时间框架分析器"""

    def get_weekly_indicators(self, symbol: str) -> Dict:
        """获取周线级别指标"""
        try:
            from data_cache import get_kline, compute_indicators
            kline = get_kline(symbol, period="weekly", days=120)
            if not kline or len(kline) < 20:
                return {}
            return compute_indicators(kline)
        except Exception as e:
            logger.debug(f"获取周线指标失败 {symbol}: {e}")
            return {}

    def get_hourly_indicators(self, symbol: str) -> Dict:
        """获取60分钟级别指标"""
        try:
            from data_cache import get_kline
            kline = get_kline(symbol, period="daily", days=10)
            if not kline or len(kline) < 10:
                return {}
            # 用日线数据近10天近似小时级别趋势
            # 真正的小时数据需要 akshare 分钟级接口
            import pandas as pd
            close = pd.Series([d['close'] for d in kline], dtype=float)
            indicators = {"close": round(close.iloc[-1], 2)}
            for p in [5, 10]:
                if len(close) >= p:
                    indicators[f"ma{p}"] = round(close.rolling(p).mean().iloc[-1], 2)
            if len(close) >= 15:
                delta = close.diff()
                gain = delta.where(delta > 0, 0).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rs = gain / loss
                indicators["rsi"] = round((100 - (100 / (1 + rs))).iloc[-1], 2)
            return indicators
        except Exception as e:
            logger.debug(f"获取小时指标失败 {symbol}: {e}")
            return {}

    def _judge_trend(self, indicators: Dict) -> str:
        """判断单个时间框架的趋势方向"""
        if not indicators:
            return "unknown"

        ma5 = indicators.get("ma5")
        ma10 = indicators.get("ma10")
        ma20 = indicators.get("ma20")
        close = indicators.get("close", 0)
        rsi = indicators.get("rsi")

        bull_signals = 0
        bear_signals = 0

        if ma5 and ma10:
            if ma5 > ma10:
                bull_signals += 1
            else:
                bear_signals += 1

        if ma10 and ma20:
            if ma10 > ma20:
                bull_signals += 1
            else:
                bear_signals += 1

        if close and ma5:
            if close > ma5:
                bull_signals += 1
            else:
                bear_signals += 1

        if rsi is not None:
            if rsi > 55:
                bull_signals += 1
            elif rsi < 45:
                bear_signals += 1

        if bull_signals >= 3:
            return "bull"
        elif bear_signals >= 3:
            return "bear"
        return "neutral"

    def analyze_alignment(self, daily: Dict, weekly: Dict, hourly: Dict) -> Dict:
        """
        分析多周期共振
        返回: {
            aligned_bull: bool,   # 三周期看多共振
            aligned_bear: bool,   # 三周期看空共振
            score: float,         # -1.0 ~ 1.0 综合评分
            timeframe_boost: float, # 共振加成分数
            daily_trend: str,
            weekly_trend: str,
            hourly_trend: str
        }
        """
        daily_trend = self._judge_trend(daily)
        weekly_trend = self._judge_trend(weekly)
        hourly_trend = self._judge_trend(hourly)

        trends = [daily_trend, weekly_trend, hourly_trend]
        bull_count = trends.count("bull")
        bear_count = trends.count("bear")

        aligned_bull = bull_count >= 2 and daily_trend == "bull"
        aligned_bear = bear_count >= 2 and daily_trend == "bear"

        # 综合评分
        trend_scores = {"bull": 1, "bear": -1, "neutral": 0, "unknown": 0}
        score = (trend_scores[daily_trend] * 0.5 +
                 trend_scores[weekly_trend] * 0.3 +
                 trend_scores[hourly_trend] * 0.2)

        # 共振加成
        timeframe_boost = 0.0
        if aligned_bull:
            timeframe_boost = 0.15 + (bull_count - 2) * 0.05
        elif aligned_bear:
            timeframe_boost = 0.15 + (bear_count - 2) * 0.05

        return {
            "aligned_bull": aligned_bull,
            "aligned_bear": aligned_bear,
            "score": round(score, 3),
            "timeframe_boost": round(timeframe_boost, 3),
            "daily_trend": daily_trend,
            "weekly_trend": weekly_trend,
            "hourly_trend": hourly_trend
        }


# 全局实例
multi_timeframe_analyzer = MultiTimeframeAnalyzer()
