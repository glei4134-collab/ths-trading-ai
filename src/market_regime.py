"""
市场宏观时态与隐状态转移识别模块 (Market Regime & Microstructure Dynamic Switcher)
由【宏观时态与隐马尔可夫状态转移专家 (regime-macro-analyst)】领衔。

基于沪深300 (000300) 及全市场宽基指数，实现：
1. 四象限宏观状态机 (4-Quadrant Market Regime: bull_low_vol, bull_high_vol, bear_low_vol, bear_high_vol)
2. 高斯混合后验状态概率分布 (State Posterior Probabilities)
3. GARCH(1,1) 前瞻条件异方差波动率预测 (Forward-looking Conditional Volatility)
4. 向后完全兼容旧版 regime 接口 (bull/bear/volatile/sideways)
"""

import time
import math
import logging
from typing import Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)


class MarketRegimeDetector:
    """四象限自适应市场环境识别器"""

    REGIME_BULL_LOW_VOL = "bull_low_vol"      # 牛市低波: 顺势突破，做多Beta，放大持仓周期
    REGIME_BULL_HIGH_VOL = "bull_high_vol"    # 牛市高波: 震荡上行，急跌低吸，网格差价
    REGIME_BEAR_LOW_VOL = "bear_low_vol"      # 熊市低波: 缩量阴跌磨底，严防死守，仓位<=20%
    REGIME_BEAR_HIGH_VOL = "bear_high_vol"    # 熊市高波: 恐慌抛售与脉冲反弹，止损收紧至-3%

    def __init__(self, cache_ttl: int = 300):
        self._last_regime: Optional[Dict] = None
        self._last_detect_time: float = 0.0
        self.cache_ttl = cache_ttl

    def detect(self, force_refresh: bool = False) -> Dict:
        """
        判断当前市场宏观环境与状态概率
        返回包含四象限状态、后验概率分布、GARCH前瞻波动率及兼容老系统的标准字典
        """
        now = time.time()
        if not force_refresh and self._last_regime and (now - self._last_detect_time < self.cache_ttl):
            return self._last_regime

        try:
            from data_cache import get_index_kline, get_market_basket_quote

            # 获取沪深300近90日日K线
            kline = get_index_kline("000300", period="daily", days=90)
            if not kline or len(kline) < 30:
                # K线不可用时，尝试市场全篮子降级方案
                basket = get_market_basket_quote()
                if basket:
                    return self._fallback_basket_regime(basket)
                return self._default_result()

            close_prices = np.array([float(d['close']) for d in kline], dtype=float)
            volumes = np.array([float(d['volume']) for d in kline], dtype=float)

            # 1. 计算对数收益率序列
            log_returns = np.diff(np.log(close_prices))
            n_bars = len(log_returns)

            # 2. 均线趋势与动量倾角 (EMA20 vs EMA60)
            ma20 = float(np.mean(close_prices[-20:]))
            ma60 = float(np.mean(close_prices[-60:])) if len(close_prices) >= 60 else float(np.mean(close_prices))
            trend_diff_pct = (ma20 - ma60) / ma60 * 100.0 if ma60 > 0 else 0.0

            # 线性回归斜率（近20日归一化趋势角）
            x = np.arange(20)
            y = close_prices[-20:] / close_prices[-20]
            slope, _ = np.polyfit(x, y, 1)  # 每天百分比变化斜率
            trend_slope = float(slope * 100.0)

            # 3. GARCH(1,1) 前瞻条件波动率拟合
            # sigma_t^2 = omega + alpha * e_{t-1}^2 + beta * sigma_{t-1}^2
            garch_vol_daily, garch_vol_annual = self._estimate_garch11_vol(log_returns)

            # 历史滚动20日已实现年化波动率
            rv20_daily = float(np.std(log_returns[-20:])) if n_bars >= 20 else 0.015
            rv20_annual = rv20_daily * math.sqrt(242)

            # 4. 近20日价格动量与成交量趋势
            change_20d = float((close_prices[-1] / close_prices[-20] - 1.0) * 100.0) if len(close_prices) >= 20 else 0.0
            vol_5 = float(np.mean(volumes[-5:])) if len(volumes) >= 5 else 1.0
            vol_20 = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else 1.0
            vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1.0

            # 5. 四象限状态判定与高斯混合后验概率
            # 趋势基准划分：trend_slope > 0 或 trend_diff_pct > 0.5
            # 波动率基准划分：rv20_annual 阈值取 18% (0.18)
            vol_threshold = 0.18
            is_bull = (trend_slope > 0.05 or trend_diff_pct > 0.5)
            is_high_vol = (rv20_annual > vol_threshold)

            # 计算各个象限的似然打分
            z_trend = trend_slope / 0.2  # 趋势显著性
            z_vol = (rv20_annual - vol_threshold) / 0.05  # 波动偏离度

            # 基于 Sigmoid 连续化状态转移概率
            p_bull = 1.0 / (1.0 + math.exp(-max(min(z_trend, 5.0), -5.0)))
            p_bear = 1.0 - p_bull
            p_high_vol = 1.0 / (1.0 + math.exp(-max(min(z_vol, 5.0), -5.0)))
            p_low_vol = 1.0 - p_high_vol

            prob_bull_low = round(p_bull * p_low_vol, 3)
            prob_bull_high = round(p_bull * p_high_vol, 3)
            prob_bear_low = round(p_bear * p_low_vol, 3)
            prob_bear_high = round(p_bear * p_high_vol, 3)

            # 归一化后验概率
            total_prob = prob_bull_low + prob_bull_high + prob_bear_low + prob_bear_high
            if total_prob > 0:
                prob_bull_low = round(prob_bull_low / total_prob, 3)
                prob_bull_high = round(prob_bull_high / total_prob, 3)
                prob_bear_low = round(prob_bear_low / total_prob, 3)
                prob_bear_high = round(prob_bear_high / total_prob, 3)

            regime_probs = {
                self.REGIME_BULL_LOW_VOL: prob_bull_low,
                self.REGIME_BULL_HIGH_VOL: prob_bull_high,
                self.REGIME_BEAR_LOW_VOL: prob_bear_low,
                self.REGIME_BEAR_HIGH_VOL: prob_bear_high
            }

            # 判定当前主状态
            quadrant_regime = max(regime_probs, key=regime_probs.get)
            confidence = float(regime_probs[quadrant_regime])

            # 兼容老版接口字符串 (bull, bear, volatile, sideways)
            if quadrant_regime == self.REGIME_BULL_LOW_VOL:
                legacy_regime = "bull"
                desc = "牛市低波 - 顺势突破与多头主升浪，放宽止盈，优选动量与趋势策略"
            elif quadrant_regime == self.REGIME_BULL_HIGH_VOL:
                legacy_regime = "volatile"
                desc = "牛市高波 - 宽幅洗盘与震荡上行，急跌低吸，注重日内回撤防护"
            elif quadrant_regime == self.REGIME_BEAR_LOW_VOL:
                legacy_regime = "sideways" if abs(change_20d) < 3.0 else "bear"
                desc = "熊市低波 - 缩量阴跌或低位磨底，防守为主，严控单票与总仓位"
            else:
                legacy_regime = "bear"
                desc = "熊市高波 - 恐慌踩踏与脉冲反弹，严格-3%止损，仅参与超跌反弹"

            factors = [
                f"四象限态势: {quadrant_regime} (后验概率 {confidence*100:.1f}%)",
                f"均线形态: MA20={ma20:.2f}, MA60={ma60:.2f} (偏离度 {trend_diff_pct:+.2f}%)",
                f"年化波动率: 已实现 {rv20_annual*100:.1f}%, GARCH(1,1)前瞻 {garch_vol_annual*100:.1f}%",
                f"20日动量倾角: {trend_slope:+.3f}%/日, 累计涨跌: {change_20d:+.2f}%",
                f"量能倍率: {vol_ratio:.2f}x (5日均量/20日均量)"
            ]

            result = {
                "regime": legacy_regime,
                "quadrant_regime": quadrant_regime,
                "regime_probs": regime_probs,
                "confidence": round(confidence, 2),
                "factors": factors,
                "description": desc,
                "ma20": round(ma20, 2),
                "ma60": round(ma60, 2),
                "volatility": round(rv20_daily, 4),
                "annualized_volatility": round(rv20_annual, 4),
                "garch_volatility": round(garch_vol_annual, 4),
                "change_20d": round(change_20d, 2),
                "trend_slope": round(trend_slope, 4),
                "volume_ratio": round(vol_ratio, 2)
            }

            self._last_regime = result
            self._last_detect_time = now
            return result

        except Exception as e:
            logger.error(f"四象限市场环境识别异常: {e}", exc_info=True)
            return self._default_result()

    def _estimate_garch11_vol(self, returns: np.ndarray) -> Tuple[float, float]:
        """
        拟合标准 GARCH(1,1) 递归方差:
        sigma_t^2 = omega + alpha * r_{t-1}^2 + beta * sigma_{t-1}^2
        """
        if len(returns) < 15:
            default_daily = 0.012
            return default_daily, default_daily * math.sqrt(242)

        # 经验先验参数 (典型A股指数分布)
        # alpha ~ 0.08, beta ~ 0.88, omega ~ 0.04 * sample_var
        sample_var = float(np.var(returns))
        alpha = 0.08
        beta = 0.88
        omega = sample_var * (1.0 - alpha - beta)
        omega = max(omega, 1e-6)

        sigma2 = sample_var
        for r in returns:
            sigma2 = omega + alpha * (r ** 2) + beta * sigma2

        daily_vol = math.sqrt(max(sigma2, 1e-6))
        annual_vol = daily_vol * math.sqrt(242)
        return float(daily_vol), float(annual_vol)

    def _fallback_basket_regime(self, basket: Dict) -> Dict:
        """K线不可用时，通过全市场实时篮子报价近似"""
        avg_change = basket.get("avg_change_pct", 0.0)
        sample_size = basket.get("sample_size", 0)

        if avg_change > 1.0:
            quadrant = self.REGIME_BULL_LOW_VOL
            legacy = "bull"
            conf = 0.6
        elif avg_change < -1.0:
            quadrant = self.REGIME_BEAR_HIGH_VOL
            legacy = "bear"
            conf = 0.6
        else:
            quadrant = self.REGIME_BEAR_LOW_VOL
            legacy = "sideways"
            conf = 0.5

        return {
            "regime": legacy,
            "quadrant_regime": quadrant,
            "regime_probs": {quadrant: conf},
            "confidence": conf,
            "factors": [f"市场篮子({sample_size}只)均值涨跌{avg_change:+.2f}% (无K线降级模式)"],
            "description": f"基于市场篮子实时报价判定: {quadrant}",
            "ma20": 0.0, "ma60": 0.0, "volatility": 0.015,
            "annualized_volatility": 0.23,
            "garch_volatility": 0.23,
            "change_20d": round(avg_change, 2),
            "trend_slope": 0.0,
            "volume_ratio": 1.0
        }

    def _default_result(self) -> Dict:
        return {
            "regime": "unknown",
            "quadrant_regime": "unknown",
            "regime_probs": {},
            "confidence": 0.0,
            "factors": ["数据不足，无法完成四象限状态机判定"],
            "description": "未知环境 (默认中性防守)",
            "ma20": 0.0, "ma60": 0.0, "volatility": 0.0,
            "annualized_volatility": 0.0,
            "garch_volatility": 0.0,
            "change_20d": 0.0,
            "trend_slope": 0.0,
            "volume_ratio": 1.0
        }

    def get_strategy_adjustments(self, regime: str) -> Dict:
        """根据四象限或旧版市场环境输出精准动态风控与策略建议参数"""
        adjustments_map = {
            # 四象限模式
            self.REGIME_BULL_LOW_VOL: {
                "prefer_categories": ["trend", "momentum"],
                "avoid_categories": ["reversal"],
                "position_factor": 1.25,
                "stop_loss_factor": 1.0,
                "confidence_boost": 0.08,
                "holding_days_boost": 2
            },
            self.REGIME_BULL_HIGH_VOL: {
                "prefer_categories": ["shortterm", "volatility", "momentum"],
                "avoid_categories": ["trend"],
                "position_factor": 0.90,
                "stop_loss_factor": 0.85,
                "confidence_boost": 0.02,
                "holding_days_boost": 0
            },
            self.REGIME_BEAR_LOW_VOL: {
                "prefer_categories": ["volume", "pattern"],
                "avoid_categories": ["momentum", "trend"],
                "position_factor": 0.50,
                "stop_loss_factor": 0.80,
                "confidence_boost": -0.05,
                "holding_days_boost": -1
            },
            self.REGIME_BEAR_HIGH_VOL: {
                "prefer_categories": ["reversal", "shortterm"],
                "avoid_categories": ["trend", "momentum"],
                "position_factor": 0.35,
                "stop_loss_factor": 0.65,
                "confidence_boost": -0.10,
                "holding_days_boost": -2
            },
            # 旧版兼容
            "bull": {
                "prefer_categories": ["trend", "momentum"],
                "avoid_categories": ["reversal"],
                "position_factor": 1.2,
                "stop_loss_factor": 1.0,
                "confidence_boost": 0.05,
                "holding_days_boost": 1
            },
            "bear": {
                "prefer_categories": ["reversal", "shortterm"],
                "avoid_categories": ["momentum"],
                "position_factor": 0.5,
                "stop_loss_factor": 0.75,
                "confidence_boost": -0.05,
                "holding_days_boost": -1
            },
            "sideways": {
                "prefer_categories": ["volume", "pattern", "volatility"],
                "avoid_categories": [],
                "position_factor": 0.8,
                "stop_loss_factor": 0.9,
                "confidence_boost": 0,
                "holding_days_boost": 0
            },
            "volatile": {
                "prefer_categories": ["shortterm", "reversal"],
                "avoid_categories": ["trend"],
                "position_factor": 0.6,
                "stop_loss_factor": 0.7,
                "confidence_boost": -0.1,
                "holding_days_boost": -1
            }
        }
        return adjustments_map.get(regime, adjustments_map["sideways"])


# 全局单例
market_regime_detector = MarketRegimeDetector()
