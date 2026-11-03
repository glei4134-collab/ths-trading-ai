"""
stock_analyzer/base.py
股票多维量化分析基础数据结构与接口契约
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass
class StockAnalysisResult:
    """股票全维度综合分析结果模型"""
    symbol: str
    name: str = ""
    price: float = 0.0
    change_pct: float = 0.0
    signal: str = "HOLD"           # STRONG_BUY / BUY / WEAK_BUY / HOLD / WEAK_SELL / SELL / STRONG_SELL
    score: float = 0.0             # 综合评分 (-100 ~ +100 或 0 ~ 100 归一化)
    confidence: float = 0.5        # 置信度 (0.0 ~ 1.0)
    target_price: float = 0.0      # 建议目标价
    stop_loss: float = 0.0         # 建议止损价
    signals: List[str] = field(default_factory=list)      # 触发信号摘要列表
    
    # 4大正交分解维度明细
    technical: Dict[str, Any] = field(default_factory=dict)       # 维度1: 技术面分析
    microstructure: Dict[str, Any] = field(default_factory=dict)  # 维度2: 微观盘口与深度
    ensemble: Dict[str, Any] = field(default_factory=dict)        # 维度3: 多策略集成表决与正交打分
    macro_sector: Dict[str, Any] = field(default_factory=dict)    # 维度4: 宏观状态与板块轮动
    
    # 雷达图多维评分 (0 ~ 100)
    radar_scores: Dict[str, float] = field(default_factory=dict)
    
    # 兼容历史前端字段
    strategy_results: Dict[str, Any] = field(default_factory=dict)
    indicators: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 JSON 兼容字典，100% 保持旧接口字段与新增维度"""
        return {
            "symbol": self.symbol,
            "name": self.name,
            "price": self.price,
            "change_pct": self.change_pct,
            "signal": self.signal,
            "score": round(self.score, 2),
            "confidence": round(self.confidence, 2),
            "target_price": round(self.target_price, 2) if self.target_price > 0 else None,
            "stop_loss": round(self.stop_loss, 2) if self.stop_loss > 0 else None,
            "signals": self.signals,
            "technical": self.technical,
            "microstructure": self.microstructure,
            "ensemble": self.ensemble,
            "macro_sector": self.macro_sector,
            "radar_scores": self.radar_scores,
            "strategy_results": self.strategy_results,
            "indicators": self.indicators,
            "timestamp": self.timestamp or datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        }
