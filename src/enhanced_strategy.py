"""
增强策略模块
结合AI分析的智能交易策略
"""

import json
import logging
from typing import Dict, List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """交易信号类型"""
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    WEAK_BUY = "WEAK_BUY"
    HOLD = "HOLD"
    WEAK_SELL = "WEAK_SELL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class RiskLevel(Enum):
    """风险等级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class TradingSignal:
    """交易信号"""
    symbol: str
    signal: SignalType
    score: float
    confidence: float
    reasons: List[str]
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "signal": self.signal.value,
            "score": self.score,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "target_price": self.target_price,
            "stop_loss": self.stop_loss,
            "timestamp": self.timestamp
        }


class EnhancedStrategy:
    """
    增强策略引擎
    结合技术分析、动量、AI分析的综合策略
    """

    def __init__(self):
        self.name = "增强智能策略"
        self.risk_level = RiskLevel.MEDIUM
        self.max_position_ratio = 0.3  # 单只股票最大仓位比例
        self.stop_loss_ratio = 0.08  # 止损比例
        self.take_profit_ratio = 0.15  # 止盈比例

    def set_risk_level(self, level: str):
        """设置风险等级"""
        self.risk_level = RiskLevel(level)
        if self.risk_level == RiskLevel.LOW:
            self.max_position_ratio = 0.2
            self.stop_loss_ratio = 0.05
            self.take_profit_ratio = 0.10
        elif self.risk_level == RiskLevel.HIGH:
            self.max_position_ratio = 0.4
            self.stop_loss_ratio = 0.10
            self.take_profit_ratio = 0.20
        else:
            self.max_position_ratio = 0.3
            self.stop_loss_ratio = 0.08
            self.take_profit_ratio = 0.15

    def analyze_technical(self, indicators: Dict) -> Dict:
        """
        分析技术指标

        Args:
            indicators: 技术指标数据

        Returns:
            技术分析结果
        """
        score = 0
        signals = []

        # 均线分析
        ma5 = indicators.get('ma5')
        ma10 = indicators.get('ma10')
        ma20 = indicators.get('ma20')
        close = indicators.get('close')

        if ma5 and ma20 and close:
            if ma5 > ma20:
                signals.append("MA5在MA20上方，短期趋势向上")
                score += 1
            else:
                signals.append("MA5在MA20下方，短期趋势向下")
                score -= 1

            if close > ma5:
                signals.append("价格在MA5上方，短期强势")
                score += 1
            elif close < ma20:
                signals.append("价格跌破MA20，中期弱势")
                score -= 1

        # RSI分析
        rsi = indicators.get('rsi')
        if rsi is not None:
            if rsi > 70:
                signals.append(f"RSI超买({rsi:.1f})，注意回调风险")
                score -= 2
            elif rsi > 60:
                signals.append(f"RSI偏强({rsi:.1f})")
                score += 1
            elif rsi < 30:
                signals.append(f"RSI超卖({rsi:.1f})，可能反弹")
                score += 2
            elif rsi < 40:
                signals.append(f"RSI偏弱({rsi:.1f})")
                score -= 1
            else:
                signals.append(f"RSI中性({rsi:.1f})")

        # MACD分析
        macd = indicators.get('macd')
        signal = indicators.get('signal')
        histogram = indicators.get('histogram')

        if macd is not None and signal is not None:
            if histogram and histogram > 0:
                signals.append("MACD柱状图为正，多头动能")
                score += 1
            else:
                signals.append("MACD柱状图为负，空头动能")
                score -= 1

            if macd > signal:
                signals.append("MACD在信号线上方")
                score += 1
            else:
                signals.append("MACD在信号线下方")
                score -= 1

        # 布林带分析
        bb_upper = indicators.get('bb_upper')
        bb_lower = indicators.get('bb_lower')
        bb_middle = indicators.get('bb_middle')

        if bb_upper and bb_lower and close:
            if close > bb_upper:
                signals.append("价格突破布林带上轨，超买")
                score -= 1
            elif close < bb_lower:
                signals.append("价格跌破布林带下轨，超卖")
                score += 1
            elif close > bb_middle:
                signals.append("价格在布林带中轨上方")
                score += 0.5
            else:
                signals.append("价格在布林带中轨下方")
                score -= 0.5

        return {
            "score": score,
            "signals": signals,
            "confidence": min(abs(score) / 5, 1.0)
        }

    def analyze_momentum(self, market_data: Dict) -> Dict:
        """
        分析动量

        Args:
            market_data: 市场数据

        Returns:
            动量分析结果
        """
        score = 0
        signals = []

        change_pct = market_data.get('change_pct', 0)
        volume = market_data.get('volume', 0)
        turnover = market_data.get('turnover', 0)

        # 价格动量
        if change_pct > 3:
            signals.append(f"强势上涨({change_pct:.2f}%)")
            score += 2
        elif change_pct > 1:
            signals.append(f"温和上涨({change_pct:.2f}%)")
            score += 1
        elif change_pct < -3:
            signals.append(f"强势下跌({change_pct:.2f}%)")
            score -= 2
        elif change_pct < -1:
            signals.append(f"温和下跌({change_pct:.2f}%)")
            score -= 1

        # 成交量分析
        if turnover > 1:
            signals.append(f"高换手率({turnover:.2f}%)，交投活跃")
            if change_pct > 0:
                score += 1
            else:
                score -= 1
        elif turnover < 0.3:
            signals.append(f"低换手率({turnover:.2f}%)，交投清淡")

        return {
            "score": score,
            "signals": signals,
            "confidence": min(abs(score) / 3, 1.0)
        }

    def generate_signal(self, symbol: str, stock_data: Dict, indicators: Dict = None,
                       ai_analysis: str = None) -> TradingSignal:
        """
        生成综合交易信号

        Args:
            symbol: 股票代码
            stock_data: 股票数据
            indicators: 技术指标
            ai_analysis: AI分析结果

        Returns:
            交易信号
        """
        total_score = 0
        all_reasons = []
        confidence_scores = []

        # 技术分析
        if indicators:
            tech_result = self.analyze_technical(indicators)
            total_score += tech_result["score"] * 0.5
            all_reasons.extend(tech_result["signals"])
            confidence_scores.append(tech_result["confidence"])

        # 动量分析
        momentum_result = self.analyze_momentum(stock_data)
        total_score += momentum_result["score"] * 0.3
        all_reasons.extend(momentum_result["signals"])
        confidence_scores.append(momentum_result["confidence"])

        # AI分析（如果有）
        if ai_analysis:
            ai_score = self._parse_ai_analysis(ai_analysis)
            total_score += ai_score * 0.2
            all_reasons.append("AI分析建议: " + ai_analysis[:100] + "...")
            confidence_scores.append(0.8)

        # 计算综合置信度
        avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.5

        # 生成信号
        if total_score >= 3:
            signal = SignalType.STRONG_BUY
        elif total_score >= 1.5:
            signal = SignalType.BUY
        elif total_score >= 0.5:
            signal = SignalType.WEAK_BUY
        elif total_score <= -3:
            signal = SignalType.STRONG_SELL
        elif total_score <= -1.5:
            signal = SignalType.SELL
        elif total_score <= -0.5:
            signal = SignalType.WEAK_SELL
        else:
            signal = SignalType.HOLD

        # 计算目标价和止损价
        current_price = stock_data.get('price', 0)
        if signal in [SignalType.BUY, SignalType.STRONG_BUY, SignalType.WEAK_BUY]:
            target_price = current_price * (1 + self.take_profit_ratio)
            stop_loss = current_price * (1 - self.stop_loss_ratio)
        elif signal in [SignalType.SELL, SignalType.STRONG_SELL, SignalType.WEAK_SELL]:
            target_price = current_price * (1 - self.take_profit_ratio)
            stop_loss = current_price * (1 + self.stop_loss_ratio)
        else:
            target_price = None
            stop_loss = None

        return TradingSignal(
            symbol=symbol,
            signal=signal,
            score=round(total_score, 2),
            confidence=round(avg_confidence, 2),
            reasons=all_reasons,
            target_price=round(target_price, 2) if target_price else None,
            stop_loss=round(stop_loss, 2) if stop_loss else None
        )

    def _parse_ai_analysis(self, analysis: str) -> float:
        """解析AI分析结果，提取评分"""
        analysis_lower = analysis.lower()
        score = 0

        # 关键词匹配
        buy_keywords = ['买入', '看多', '上涨', '突破', '强势', '推荐']
        sell_keywords = ['卖出', '看空', '下跌', '跌破', '弱势', '风险']

        for keyword in buy_keywords:
            if keyword in analysis_lower:
                score += 0.5

        for keyword in sell_keywords:
            if keyword in analysis_lower:
                score -= 0.5

        return max(-3, min(3, score))

    def calculate_position_size(self, signal: TradingSignal, balance: float,
                               current_price: float) -> int:
        """
        计算仓位大小

        Args:
            signal: 交易信号
            balance: 可用余额
            current_price: 当前价格

        Returns:
            建议买入数量（股）
        """
        if signal.signal in [SignalType.HOLD, SignalType.WEAK_SELL, SignalType.SELL, SignalType.STRONG_SELL]:
            return 0

        # 根据信号强度调整仓位比例
        position_ratio = self.max_position_ratio
        if signal.signal == SignalType.STRONG_BUY:
            position_ratio *= 1.2
        elif signal.signal == SignalType.WEAK_BUY:
            position_ratio *= 0.6

        # 根据置信度调整
        position_ratio *= signal.confidence

        # 计算最大投入金额
        max_amount = balance * position_ratio

        # 计算可买数量（100股整数倍）
        shares = int(max_amount / current_price / 100) * 100

        return max(0, shares)

    def format_signal_report(self, signal: TradingSignal, stock_data: Dict) -> str:
        """格式化信号报告"""
        signal_emoji = {
            SignalType.STRONG_BUY: "🔥 强烈买入",
            SignalType.BUY: "✅ 买入",
            SignalType.WEAK_BUY: "📈 轻仓买入",
            SignalType.HOLD: "⏸️ 持有观望",
            SignalType.WEAK_SELL: "📉 轻仓卖出",
            SignalType.SELL: "❌ 卖出",
            SignalType.STRONG_SELL: "🚨 强烈卖出"
        }

        report = f"""
📊 股票分析报告
{'='*40}
股票代码: {signal.symbol}
股票名称: {stock_data.get('name', 'N/A')}
当前价格: {stock_data.get('price', 'N/A')}

📈 交易信号: {signal_emoji.get(signal.signal, signal.signal.value)}
📊 综合评分: {signal.score}
🎯 置信度: {signal.confidence:.0%}
"""
        if signal.target_price:
            report += f"🎯 目标价: {signal.target_price}\n"
        if signal.stop_loss:
            report += f"🛑 止损价: {signal.stop_loss}\n"

        report += f"""
{'='*40}
📋 分析理由:
"""
        for i, reason in enumerate(signal.reasons[:5], 1):
            report += f"  {i}. {reason}\n"

        report += f"""
⏰ 分析时间: {signal.timestamp}
{'='*40}
"""
        return report


# 全局增强策略实例
enhanced_strategy = EnhancedStrategy()
