"""
stock_analyzer/__init__.py
股票多维正交量化分析模块对外入口

模块划分架构:
- TechnicalAnalyzer: 维度 1 - 技术面均线多头/MACD/RSI/量价形态深度扫描
- MicrostructureAnalyzer: 维度 2 - 五档微观盘口、订单簿不平衡度(OBI)、委托流(VOI)与流动性分析
- StrategyEnsembleAnalyzer: 维度 3 - 5大核心策略集成表决、共识打分与 Lowdin 正交化
- MacroSectorAnalyzer: 维度 4 - 4象限宏观状态机、申万一级行业轮动与新闻舆情
- CompositeStockAnalyzer: 综合分析门面 (Facade)，统一调度输出结构化报告
"""

from stock_analyzer.base import StockAnalysisResult
from stock_analyzer.technical_analyzer import TechnicalAnalyzer
from stock_analyzer.microstructure_analyzer import MicrostructureAnalyzer
from stock_analyzer.strategy_ensemble_analyzer import StrategyEnsembleAnalyzer
from stock_analyzer.macro_sector_analyzer import MacroSectorAnalyzer
from stock_analyzer.composite_analyzer import CompositeStockAnalyzer

# 单例实例，方便跨路由与策略直接调用
stock_analyzer = CompositeStockAnalyzer()

def analyze_stock(symbol: str, **kwargs) -> dict:
    """快捷分析函数，返回序列化字典结果"""
    res = stock_analyzer.analyze(symbol, **kwargs)
    return res.to_dict()

__all__ = [
    "StockAnalysisResult",
    "TechnicalAnalyzer",
    "MicrostructureAnalyzer",
    "StrategyEnsembleAnalyzer",
    "MacroSectorAnalyzer",
    "CompositeStockAnalyzer",
    "stock_analyzer",
    "analyze_stock",
]
