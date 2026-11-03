"""
stock_analyzer/macro_sector_analyzer.py
维度 4: 股票宏观环境、申万行业板块轮动与舆情分析器
负责4象限宏观状态机判定、所属行业热度打分、新闻舆情加权与宏观风控增益
"""

import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class MacroSectorAnalyzer:
    """宏观环境与板块轮动分析器"""

    def analyze(self, symbol: str, stock_name: str = "") -> Dict[str, Any]:
        """
        分析股票所属行业板块热度、大盘宏观状态与舆情情绪

        Args:
            symbol: 股票代码
            stock_name: 股票简称
        """
        # 1. 宏观象限探测
        regime_desc = "震荡市"
        regime_id = "neutral"
        regime_confidence = 0.70
        try:
            from market_regime import market_regime_detector
            r_info = market_regime_detector.detect()
            regime_id = r_info.get("regime", "neutral")
            regime_desc = r_info.get("description", "大盘处于结构性震荡态")
            regime_confidence = float(r_info.get("confidence", 0.70))
        except Exception as e:
            logger.debug(f"宏观状态获取异常: {e}")

        # 2. 板块轮动与申万行业得分
        sector_name = "综合"
        sector_score = 50.0
        try:
            from sector_analysis import sector_analyzer
            s_val = sector_analyzer.get_sector_score(symbol)
            sector_score = float(s_val * 100.0) if s_val <= 1.0 else float(s_val)
            sector_name = sector_analyzer.get_stock_sector(symbol) if hasattr(sector_analyzer, "get_stock_sector") else "核心行业"
        except Exception:
            sector_score = 50.0

        # 3. 新闻舆情分
        news_score = 50.0
        news_desc = "中性偏稳"
        try:
            from sentiment_analyzer import sentiment_analyzer
            s_news = sentiment_analyzer.get_stock_sentiment(symbol, stock_name)
            news_score = float(s_news.get("score", 50))
            news_desc = s_news.get("description", "舆情平稳")
        except Exception:
            news_score = 50.0

        # 4. 宏观与板块综合得分生成
        macro_score = (sector_score * 0.5) + (news_score * 0.3)
        if regime_id == "bull":
            macro_score += 10.0
        elif regime_id == "bear":
            macro_score -= 10.0
        macro_score = max(0.0, min(100.0, macro_score))

        signals = [
            f"市场宏观环境: [{regime_id}] {regime_desc}",
            f"所属板块[{sector_name}]得分: {sector_score:.1f}",
            f"舆情情绪: {news_score:.0f}分 ({news_desc})"
        ]

        return {
            "score": round(macro_score, 1),
            "regime": regime_id,
            "regime_desc": regime_desc,
            "regime_confidence": round(regime_confidence, 2),
            "sector_name": sector_name,
            "sector_score": round(sector_score, 1),
            "news_score": round(news_score, 1),
            "news_desc": news_desc,
            "signals": signals
        }
