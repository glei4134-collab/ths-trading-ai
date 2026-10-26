"""
市场环境识别模块
基于沪深300和上证指数判断市场状态：牛市/熊市/震荡/波动
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class MarketRegimeDetector:
    """市场环境识别器"""

    def __init__(self):
        self._last_regime = None
        self._last_detect_time = 0

    def detect(self) -> Dict:
        """
        判断当前市场环境
        返回: {regime, confidence, factors, description}
        """
        try:
            from data_cache import get_index_kline, get_market_basket_quote
            import time

            # 缓存5分钟
            now = time.time()
            if self._last_regime and now - self._last_detect_time < 300:
                return self._last_regime

            # 获取沪深300日K线（用 get_index_kline 直接走东方财富，跳过不支持指数的腾讯）
            kline = get_index_kline("000300", period="daily", days=90)
            if not kline or len(kline) < 60:
                # K线不可用时，尝试市场篮子实时报价作为降级方案
                basket = get_market_basket_quote()
                if basket:
                    avg_change = basket["avg_change_pct"]
                    factors = [f"市场篮子({basket['sample_size']}只)平均涨跌{avg_change:.2f}%"]
                    if avg_change > 1.0:
                        regime, confidence = "bull", 0.5
                        factors.append("篮子整体上涨")
                    elif avg_change < -1.0:
                        regime, confidence = "bear", 0.5
                        factors.append("篮子整体下跌")
                    else:
                        regime, confidence = "sideways", 0.5
                        factors.append("篮子整体震荡")
                    return {
                        "regime": regime,
                        "confidence": confidence,
                        "factors": factors,
                        "description": f"基于篮子行情近似（数据源有限）",
                        "ma20": 0, "ma60": 0, "volatility": 0, "change_20d": round(avg_change, 2)
                    }
                return self._default_result()

            close = [d['close'] for d in kline]
            volumes = [d['volume'] for d in kline]

            # 1. 均线趋势
            ma20 = sum(close[-20:]) / 20
            ma60 = sum(close[-60:]) / 60
            ma20_prev = sum(close[-21:-1]) / 20
            ma60_prev = sum(close[-61:-1]) / 60

            ma_trend = 0
            if ma20 > ma60:
                ma_trend += 1
            else:
                ma_trend -= 1
            if ma20 > ma20_prev:
                ma_trend += 0.5
            else:
                ma_trend -= 0.5

            # 2. 波动率（近20日）
            returns = [(close[i] / close[i-1] - 1) for i in range(-20, 0)]
            volatility = (sum(r**2 for r in returns) / 20) ** 0.5

            # 3. 近20日涨跌幅
            change_20d = (close[-1] / close[-20] - 1) * 100

            # 4. 成交量趋势
            vol_recent = sum(volumes[-5:]) / 5
            vol_prev = sum(volumes[-10:-5]) / 5
            vol_trend = vol_recent / vol_prev if vol_prev > 0 else 1.0

            # 5. 综合判断
            factors = []
            regime = "sideways"
            confidence = 0.5

            if ma_trend > 1 and change_20d > 3 and volatility < 0.02:
                regime = "bull"
                confidence = min(0.9, 0.5 + abs(ma_trend) * 0.1 + abs(change_20d) * 0.01)
                factors.append(f"均线多头排列(MA20={ma20:.2f}>MA60={ma60:.2f})")
                factors.append(f"20日涨幅{change_20d:.1f}%")
                factors.append(f"低波动率{volatility:.4f}")
            elif ma_trend < -1 and change_20d < -3:
                regime = "bear"
                confidence = min(0.9, 0.5 + abs(ma_trend) * 0.1 + abs(change_20d) * 0.01)
                factors.append(f"均线空头排列(MA20={ma20:.2f}<MA60={ma60:.2f})")
                factors.append(f"20日跌幅{change_20d:.1f}%")
            elif volatility > 0.025:
                regime = "volatile"
                confidence = min(0.85, 0.5 + volatility * 10)
                factors.append(f"高波动率{volatility:.4f}")
                factors.append(f"20日振幅{change_20d:.1f}%")
            else:
                regime = "sideways"
                confidence = 0.6
                factors.append(f"均线交织(MA20={ma20:.2f}, MA60={ma60:.2f})")
                factors.append(f"低波动{volatility:.4f}")

            if vol_trend > 1.3:
                factors.append(f"放量{vol_trend:.1f}倍")

            descriptions = {
                "bull": "牛市趋势 - 适合趋势跟踪和动量策略",
                "bear": "熊市下跌 - 适合反转和超短线策略",
                "sideways": "横盘震荡 - 适合量能和形态策略",
                "volatile": "大幅波动 - 适合短线和反转策略"
            }

            result = {
                "regime": regime,
                "confidence": round(confidence, 2),
                "factors": factors,
                "description": descriptions.get(regime, ""),
                "ma20": round(ma20, 2),
                "ma60": round(ma60, 2),
                "volatility": round(volatility, 4),
                "change_20d": round(change_20d, 2)
            }

            self._last_regime = result
            self._last_detect_time = now
            return result

        except Exception as e:
            logger.error(f"市场环境识别失败: {e}")
            return self._default_result()

    def _default_result(self) -> Dict:
        return {
            "regime": "unknown",
            "confidence": 0,
            "factors": ["数据不足，无法判断"],
            "description": "未知环境",
            "ma20": 0, "ma60": 0, "volatility": 0, "change_20d": 0
        }

    def get_strategy_adjustments(self, regime: str) -> Dict:
        """根据市场环境给出策略调整建议"""
        adjustments = {
            "bull": {
                "prefer_categories": ["trend", "momentum"],
                "avoid_categories": ["reversal"],
                "position_factor": 1.2,
                "stop_loss_factor": 1.0,
                "confidence_boost": 0.05
            },
            "bear": {
                "prefer_categories": ["reversal", "shortterm"],
                "avoid_categories": ["momentum"],
                "position_factor": 0.6,
                "stop_loss_factor": 0.8,
                "confidence_boost": -0.05
            },
            "sideways": {
                "prefer_categories": ["volume", "pattern", "volatility"],
                "avoid_categories": [],
                "position_factor": 0.8,
                "stop_loss_factor": 0.9,
                "confidence_boost": 0
            },
            "volatile": {
                "prefer_categories": ["shortterm", "reversal"],
                "avoid_categories": ["trend"],
                "position_factor": 0.5,
                "stop_loss_factor": 0.7,
                "confidence_boost": -0.1
            }
        }
        return adjustments.get(regime, adjustments["sideways"])


# 全局实例
market_regime_detector = MarketRegimeDetector()
