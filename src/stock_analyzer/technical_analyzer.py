"""
stock_analyzer/technical_analyzer.py
维度 1: 股票技术面多指标量化分析器
负责趋势追踪、均线排列、动量震荡(MACD/RSI/KDJ)、布林通道与量价形态分析
"""

import logging
from typing import Dict, Any, List, Optional
import numpy as np

logger = logging.getLogger(__name__)


class TechnicalAnalyzer:
    """技术面量化分析器"""

    def analyze(self, symbol: str, quote: Dict[str, Any], kline: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """
        执行技术面多指标深度扫描

        Args:
            symbol: 股票代码
            quote: 当前实时快照
            kline: 历史日K线列表
        """
        curr_price = float(quote.get("price") or quote.get("close") or 0.0)
        pre_close = float(quote.get("pre_close") or curr_price or 1.0)
        
        if not kline or len(kline) < 5:
            return self._fallback_technical(curr_price, pre_close)

        closes = np.array([float(d.get("close") or 0) for d in kline], dtype=float)
        highs = np.array([float(d.get("high") or d.get("close") or 0) for d in kline], dtype=float)
        lows = np.array([float(d.get("low") or d.get("close") or 0) for d in kline], dtype=float)
        volumes = np.array([float(d.get("volume") or 0) for d in kline], dtype=float)

        # 1. 均线系统与多头排列
        n = len(closes)
        ma5 = float(np.mean(closes[-5:])) if n >= 5 else curr_price
        ma10 = float(np.mean(closes[-10:])) if n >= 10 else ma5
        ma20 = float(np.mean(closes[-20:])) if n >= 20 else ma10
        ma60 = float(np.mean(closes[-60:])) if n >= 60 else ma20

        is_bull_alignment = (ma5 > ma10 > ma20)
        is_above_ma20 = (curr_price >= ma20)

        # 2. RSI 相对强弱指标 (Wilder 14)
        rsi = self._calculate_rsi(closes, period=14)

        # 3. MACD 指标 (12, 26, 9)
        dif, dea, macd_hist = self._calculate_macd(closes)

        # 4. 布林带 (20, 2)
        bb_mid = ma20
        std20 = float(np.std(closes[-20:])) if n >= 20 else (curr_price * 0.02)
        bb_upper = bb_mid + 2.0 * std20
        bb_lower = bb_mid - 2.0 * std20

        # 5. 量价分析 (近5日平均成交量 vs 最新成交量)
        recent_vol = volumes[-1] if len(volumes) > 0 else 0
        avg_vol_5 = float(np.mean(volumes[-6:-1])) if len(volumes) >= 6 else (recent_vol or 1.0)
        vol_ratio = (recent_vol / avg_vol_5) if avg_vol_5 > 0 else 1.0

        # 6. 技术面评分生成与信号提炼
        tech_score = 50.0  # 基准中性 50
        signals = []

        if is_bull_alignment:
            tech_score += 18.0
            signals.append("MA多头排列(MA5>MA10>MA20)")
        elif ma5 < ma10 < ma20:
            tech_score -= 15.0
            signals.append("MA空头排列")

        if is_above_ma20:
            tech_score += 10.0
            signals.append("站稳20日生命线")
        else:
            tech_score -= 10.0
            signals.append("跌破20日均线")

        if dif > dea and macd_hist > 0:
            tech_score += 12.0
            signals.append("MACD金叉多头放量")
        elif dif < dea and macd_hist < 0:
            tech_score -= 12.0
            signals.append("MACD死叉空头运行")

        if rsi < 30:
            tech_score += 12.0
            signals.append(f"RSI超卖反弹区({rsi:.1f})")
        elif rsi > 75:
            tech_score -= 10.0
            signals.append(f"RSI超买高危区({rsi:.1f})")

        if vol_ratio > 1.8 and curr_price > pre_close:
            tech_score += 10.0
            signals.append(f"放量上攻(量比{vol_ratio:.1f})")
        elif vol_ratio < 0.6 and abs(curr_price - pre_close) / pre_close < 0.015:
            signals.append("缩量蓄势整理")

        tech_score = max(0.0, min(100.0, tech_score))

        return {
            "score": round(tech_score, 1),
            "ma5": round(ma5, 2),
            "ma10": round(ma10, 2),
            "ma20": round(ma20, 2),
            "ma60": round(ma60, 2),
            "rsi": round(rsi, 1),
            "macd_dif": round(dif, 3),
            "macd_dea": round(dea, 3),
            "macd_hist": round(macd_hist, 3),
            "bb_upper": round(bb_upper, 2),
            "bb_middle": round(bb_mid, 2),
            "bb_lower": round(bb_lower, 2),
            "volume_ratio": round(vol_ratio, 2),
            "is_bull_alignment": is_bull_alignment,
            "signals": signals
        }

    def _calculate_rsi(self, closes: np.ndarray, period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        diff = np.diff(closes)
        gains = np.maximum(diff, 0)
        losses = np.maximum(-diff, 0)
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _calculate_macd(self, closes: np.ndarray):
        if len(closes) < 26:
            return 0.0, 0.0, 0.0
        ema12 = self._calc_ema(closes, 12)
        ema26 = self._calc_ema(closes, 26)
        dif = ema12 - ema26
        dea = self._calc_ema(dif, 9)
        macd_hist = (dif[-1] - dea[-1]) * 2.0
        return float(dif[-1]), float(dea[-1]), float(macd_hist)

    def _calc_ema(self, series: np.ndarray, period: int) -> np.ndarray:
        alpha = 2.0 / (period + 1.0)
        res = np.empty_like(series, dtype=float)
        res[0] = series[0]
        for i in range(1, len(series)):
            res[i] = alpha * series[i] + (1.0 - alpha) * res[i - 1]
        return res

    def _fallback_technical(self, price: float, pre_close: float) -> Dict[str, Any]:
        return {
            "score": 50.0,
            "ma5": price,
            "ma10": price,
            "ma20": price,
            "ma60": price,
            "rsi": 50.0,
            "macd_dif": 0.0,
            "macd_dea": 0.0,
            "macd_hist": 0.0,
            "bb_upper": price * 1.05,
            "bb_middle": price,
            "bb_lower": price * 0.95,
            "volume_ratio": 1.0,
            "is_bull_alignment": False,
            "signals": ["K线样本不足，采用快照技术面平滑估计"]
        }
