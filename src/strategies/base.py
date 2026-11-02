"""
策略基类定义
所有策略必须继承 BaseStrategy 并实现 analyze 方法
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
import json


class Signal(Enum):
    """交易信号"""
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    WEAK_BUY = "WEAK_BUY"
    HOLD = "HOLD"
    WEAK_SELL = "WEAK_SELL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


@dataclass
class StrategyResult:
    """策略分析结果"""
    signal: Signal
    score: float  # -1.0 ~ 1.0
    confidence: float  # 0.0 ~ 1.0
    reasons: List[str] = field(default_factory=list)
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    strategy_name: str = ""
    extra: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "signal": self.signal.value,
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 4),
            "reasons": self.reasons,
            "target_price": self.target_price,
            "stop_loss": self.stop_loss,
            "strategy_name": self.strategy_name,
            "extra": self.extra
        }


def check_capital_divergence(stock_data: Dict, sentiment_score: float = 50.0) -> Tuple[bool, float, str]:
    """
    通用主力资金背离校验 (Defect 10 逻辑去重)：
    当舆情情绪高涨 (>=75) 但主力大单净流出超过流通市值 0.8% 时，判定为高位诱多出货，触发一票否决。
    返回: (is_divergence, net_outflow_ratio, reject_reason)
    """
    net_outflow_ratio = float(stock_data.get("main_net_outflow_ratio", 0) or stock_data.get("large_order_net_outflow_pct", 0) or 0)
    if net_outflow_ratio <= 0 and stock_data.get("main_net_outflow") and stock_data.get("float_market_cap"):
        try:
            net_outflow_ratio = float(stock_data["main_net_outflow"]) / float(stock_data["float_market_cap"])
        except (KeyError, ValueError, TypeError, ZeroDivisionError):
            pass

    if sentiment_score >= 75 and net_outflow_ratio >= 0.008:
        reason = f"主力资金背离一票否决: 舆情分高达{sentiment_score:.0f}分，但主力大单净流出占流通市值{net_outflow_ratio*100:.2f}% (>=0.80%)，判定为高位诱多出货！"
        return True, net_outflow_ratio, reason
    return False, net_outflow_ratio, ""


class BaseStrategy:
    """
    策略基类
    所有策略必须继承此类并实现 analyze 方法
    """

    name: str = "base"
    description: str = ""  # 策略描述（给AI看的）
    category: str = "other"  # trend/momentum/reversal/volatility/volume/pattern/composite
    risk_level: str = "medium"  # low/medium/high
    params: Dict = field(default_factory=dict)  # 可调参数 {param_name: {desc, default, min, max}}

    def get_param(self, name: str, params: Optional[Dict] = None, default: Any = None) -> Any:
        """获取参数值：优先入参 params，其次 self.params[name]['default']，最后回退 default (Defect 3: 线程安全)"""
        if params and isinstance(params, dict) and name in params:
            return params[name]
        if name in self.params and isinstance(self.params[name], dict):
            return self.params[name].get("default", default)
        return default

    def _normalize_score(self, raw_score: float, min_val: float = -1.0, max_val: float = 1.0) -> float:
        """统一评分归一化并严格截断在 [-1.0, 1.0] 之间 (Defect 6)"""
        clamped = max(-1.0, min(1.0, raw_score))
        return round(clamped, 4)

    def analyze(
        self,
        stock_data: Dict,
        indicators: Dict,
        market_data: Dict = None,
        history: List[Dict] = None,
        **kwargs
    ) -> StrategyResult:
        """
        分析股票并返回交易信号

        Args:
            stock_data: 股票实时数据 {price, change_pct, volume, amount, turnover, open, high, low, pre_close}
            indicators: 技术指标 {ma5, ma10, ma20, ma60, rsi, macd, signal, histogram, bb_upper, bb_middle, bb_lower, kdj_k, kdj_d, kdj_j, atr, adx, dmi_plus, dmi_minus, obv, vwap}
            market_data: 市场数据 {sh_index, sz_index, sector, market_sentiment}
            history: 历史K线数据 [{date, open, close, high, low, volume}]
            **kwargs: 额外参数

        Returns:
            StrategyResult
        """
        raise NotImplementedError("子类必须实现 analyze 方法")

    def get_interface_doc(self) -> str:
        """返回策略接口文档（给AI读的）"""
        params_desc = ""
        for k, v in self.params.items():
            params_desc += f"    - {k}: {v.get('desc', '')} (默认: {v.get('default', '')}, 范围: {v.get('min', '')}~{v.get('max', '')})" + "\n"

        params_section = params_desc if params_desc else "    无"

        doc = (
            "策略名称: " + self.name + "\n"
            "策略类别: " + self.category + "\n"
            "风险等级: " + self.risk_level + "\n"
            "策略描述: " + self.description + "\n\n"
            "输入参数:\n"
            "    stock_data: dict - 股票实时数据 (price, change_pct, volume, amount, turnover, open, high, low, pre_close)\n"
            "    indicators: dict - 技术指标 (ma5, ma10, ma20, rsi, macd, signal, histogram, bb_upper, bb_middle, bb_lower, atr, adx)\n"
            "    market_data: dict - 市场数据 (可选)\n"
            "    history: list - 历史K线 (可选)\n\n"
            "可调参数:\n" + params_section + "\n\n"
            "返回: StrategyResult(signal, score, confidence, reasons, target_price, stop_loss)\n"
            "    signal: STRONG_BUY/BUY/WEAK_BUY/HOLD/WEAK_SELL/SELL/STRONG_SELL\n"
            "    score: -1.0~1.0 (负=看空, 正=看多)\n"
            "    confidence: 0.0~1.0 (置信度)\n"
        )
        return doc

    def _calc_technical_score(self, indicators: Dict) -> tuple:
        """通用技术指标评分（子类可复用）"""
        score = 0.0
        reasons = []

        # 均线
        close = indicators.get("close", 0)
        ma5 = indicators.get("ma5")
        ma10 = indicators.get("ma10")
        ma20 = indicators.get("ma20")

        if ma5 and ma20 and close:
            if ma5 > ma20:
                score += 0.15
                reasons.append("MA5 > MA20 短期趋势向上")
            else:
                score -= 0.15
                reasons.append("MA5 < MA20 短期趋势向下")

            if close > ma5:
                score += 0.1
                reasons.append("价格在MA5上方")
            elif close < ma20:
                score -= 0.1
                reasons.append("价格跌破MA20")

        # RSI
        rsi = indicators.get("rsi")
        if rsi is not None:
            if rsi > 70:
                score -= 0.2
                reasons.append(f"RSI超买({rsi:.1f})")
            elif rsi < 30:
                score += 0.2
                reasons.append(f"RSI超卖({rsi:.1f})")
            elif rsi > 60:
                score += 0.05
            elif rsi < 40:
                score -= 0.05

        # MACD
        macd = indicators.get("macd")
        signal_line = indicators.get("signal")
        histogram = indicators.get("histogram")
        if macd is not None and signal_line is not None:
            if histogram and histogram > 0:
                score += 0.1
                reasons.append("MACD柱状图为正")
            else:
                score -= 0.1
                reasons.append("MACD柱状图为负")

        # 布林带
        bb_upper = indicators.get("bb_upper")
        bb_lower = indicators.get("bb_lower")
        if bb_upper and bb_lower and close:
            if close > bb_upper:
                score -= 0.1
                reasons.append("价格突破布林带上轨")
            elif close < bb_lower:
                score += 0.1
                reasons.append("价格跌破布林带下轨")

        return score, reasons

    def _score_to_signal(self, score: float) -> Signal:
        """通用得分转交易信号转换器"""
        if score >= 0.5:
            return Signal.STRONG_BUY
        elif score >= 0.2:
            return Signal.BUY
        elif score >= 0.05:
            return Signal.WEAK_BUY
        elif score <= -0.5:
            return Signal.STRONG_SELL
        elif score <= -0.2:
            return Signal.SELL
        elif score <= -0.05:
            return Signal.WEAK_SELL
        return Signal.HOLD
