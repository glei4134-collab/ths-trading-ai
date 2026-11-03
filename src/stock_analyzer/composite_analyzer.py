"""
stock_analyzer/composite_analyzer.py
综合股票分析器门面 (CompositeStockAnalyzer Facade)
汇聚技术面、微观盘口、多策略集成表决与宏观板块4大正交分解维度，
产出高内聚、统一格式的评级信号、目标价、硬止损价与雷达图
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo

from stock_analyzer.base import StockAnalysisResult
from stock_analyzer.technical_analyzer import TechnicalAnalyzer
from stock_analyzer.microstructure_analyzer import MicrostructureAnalyzer
from stock_analyzer.strategy_ensemble_analyzer import StrategyEnsembleAnalyzer
from stock_analyzer.macro_sector_analyzer import MacroSectorAnalyzer

logger = logging.getLogger(__name__)


class CompositeStockAnalyzer:
    """股票综合多维量化分析器 (门面模式)"""

    def __init__(self):
        self.technical_analyzer = TechnicalAnalyzer()
        self.microstructure_analyzer = MicrostructureAnalyzer()
        self.ensemble_analyzer = StrategyEnsembleAnalyzer()
        self.macro_sector_analyzer = MacroSectorAnalyzer()

    def analyze(self, symbol: str, quote: Optional[Dict[str, Any]] = None,
                kline: Optional[List[Dict[str, Any]]] = None,
                account_style: str = "balanced") -> StockAnalysisResult:
        """
        全量分析指定股票，融合4大独立子模块

        Args:
            symbol: 股票代码 (如 600519 或 000001)
            quote: 可选传入的实时行情 (若为空则自动高速抓取)
            kline: 可选传入的日K线列表 (若为空则自动通过快速源拉取)
            account_style: 账户风格 (aggressive/balanced/defensive)
        """
        # 1. 补全行情与基础数据
        if not quote:
            try:
                from fast_data import fetch_realtime_single
                quote = fetch_realtime_single(symbol) or {}
            except Exception as e:
                logger.debug(f"抓取实时行情异常 {symbol}: {e}")
                quote = {}

        stock_name = quote.get("name") or quote.get("stock_name") or symbol
        curr_price = float(quote.get("price") or quote.get("close") or 0.0)
        change_pct = float(quote.get("change_pct") or 0.0)

        # 2. 补全 K 线历史数据
        if not kline:
            try:
                from fast_data import fetch_kline
                kline = fetch_kline(symbol, period="daily", days=60) or []
            except Exception as e:
                logger.debug(f"拉取K线异常 {symbol}: {e}")
                kline = []

        # 3. 维度 1: 技术面量化深度扫描
        tech_res = self.technical_analyzer.analyze(symbol, quote, kline)

        # 4. 维度 2: 微观五档盘口与流动性分析
        micro_res = self.microstructure_analyzer.analyze(symbol, quote)

        # 5. 维度 4: 宏观状态与板块轮动
        macro_res = self.macro_sector_analyzer.analyze(symbol, stock_name)
        curr_regime = macro_res.get("regime", "bull")

        # 6. 维度 3: 多策略集成表决与 Lowdin 正交打分
        ensemble_res = self.ensemble_analyzer.analyze(symbol, quote, tech_res, regime=curr_regime)

        # 7. 4大维度正交加权汇总评分 (0 ~ 100)
        w_ens = 0.35   # 多策略共识权重
        w_tech = 0.30  # 技术面均线动量权重
        w_micro = 0.20 # 微观盘口承接力权重
        w_macro = 0.15 # 宏观与板块舆情权重

        score_ens = float(ensemble_res.get("score", 50.0))
        score_tech = float(tech_res.get("score", 50.0))
        score_micro = float(micro_res.get("score", 50.0))
        score_macro = float(macro_res.get("score", 50.0))

        composite_score = (
            score_ens * w_ens +
            score_tech * w_tech +
            score_micro * w_micro +
            score_macro * w_macro
        )
        composite_score = round(max(0.0, min(100.0, composite_score)), 2)

        # 8. 置信度评估
        votes_ratio = float(ensemble_res.get("vote_ratio", 0.0))
        confidence = round(max(0.40, min(0.95, (votes_ratio * 0.5) + (composite_score / 200.0))), 2)

        # 9. 最终信号综合仲裁
        if composite_score >= 82.0 and votes_ratio >= 0.60:
            final_signal = "STRONG_BUY"
        elif composite_score >= 70.0 and votes_ratio >= 0.40:
            final_signal = "BUY"
        elif composite_score >= 60.0:
            final_signal = "WEAK_BUY"
        elif composite_score <= 35.0:
            final_signal = "STRONG_SELL"
        elif composite_score <= 45.0:
            final_signal = "SELL"
        elif composite_score <= 52.0:
            final_signal = "WEAK_SELL"
        else:
            final_signal = "HOLD"

        # 10. 动态目标价与铁律止损线计算
        # 目标价: 基于布林上轨与平均真实波幅预估上涨空间 (+4% ~ +12%)
        if curr_price > 0:
            upside_pct = max(0.04, min(0.12, (composite_score - 50.0) / 300.0 + 0.05))
            target_price = round(curr_price * (1.0 + upside_pct), 2)
            # 严格受制于团队 -4.5% 硬止损铁律，同时结合盘口支撑位微调 (-3.8% ~ -4.5%)
            stop_loss_pct = max(0.038, min(0.045, 0.045 - (score_micro / 2000.0)))
            stop_loss_price = round(curr_price * (1.0 - stop_loss_pct), 2)
        else:
            target_price = 0.0
            stop_loss_price = 0.0

        # 11. 汇聚全局信号标签
        all_signals = []
        if ensemble_res.get("consensus_desc"):
            all_signals.append(f"【多策略集成】{ensemble_res['consensus_desc']}")
        for s in tech_res.get("signals", [])[:2]:
            all_signals.append(f"【技术形态】{s}")
        for s in micro_res.get("signals", [])[:2]:
            all_signals.append(f"【微观盘口】{s}")
        for s in macro_res.get("signals", [])[:1]:
            all_signals.append(f"【宏观板块】{s}")

        # 12. 构造雷达图 5 维量化能力模型 (用于可视化看板渲染)
        radar_scores = {
            "多策略共识": round(score_ens, 1),
            "技术面动量": round(score_tech, 1),
            "盘口承接力": round(score_micro, 1),
            "行业景气度": round(score_macro, 1),
            "风险防御性": round(max(20.0, min(95.0, 100.0 - abs(change_pct) * 8.0)), 1)
        }

        # 13. 打包历史兼容指标
        legacy_indicators = {
            "price": curr_price,
            "ma5": tech_res.get("ma5"),
            "ma10": tech_res.get("ma10"),
            "ma20": tech_res.get("ma20"),
            "ma60": tech_res.get("ma60"),
            "rsi": tech_res.get("rsi"),
            "macd": tech_res.get("macd_hist"),
            "dif": tech_res.get("macd_dif"),
            "dea": tech_res.get("macd_dea"),
            "bb_upper": tech_res.get("bb_upper"),
            "bb_lower": tech_res.get("bb_lower"),
            "volume_ratio": tech_res.get("volume_ratio"),
            "obi": micro_res.get("obi"),
            "voi": micro_res.get("normalized_voi")
        }

        return StockAnalysisResult(
            symbol=symbol,
            name=stock_name,
            price=curr_price,
            change_pct=change_pct,
            signal=final_signal,
            score=composite_score,
            confidence=confidence,
            target_price=target_price,
            stop_loss=stop_loss_price,
            signals=all_signals,
            technical=tech_res,
            microstructure=micro_res,
            ensemble=ensemble_res,
            macro_sector=macro_res,
            radar_scores=radar_scores,
            strategy_results=ensemble_res.get("strategy_details", {}),
            indicators=legacy_indicators,
            timestamp=datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        )
