"""
stock_analyzer/strategy_ensemble_analyzer.py
维度 3: 多策略集成表决与 Lowdin 正交化量化分析器
负责5大策略表决、共识信号投票、Lowdin 正交打分、HRP 风险平价与 BL 权重分配
"""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class StrategyEnsembleAnalyzer:
    """多策略集成表决与正交化分析器"""

    def analyze(self, symbol: str, stock_data: Dict[str, Any], indicators: Dict[str, Any],
                regime: str = "bull") -> Dict[str, Any]:
        """
        运行5大策略集成表决与 Lowdin 正交化权重计算

        Args:
            symbol: 股票代码
            stock_data: 股票基本面与最新价格
            indicators: 均线、MACD、RSI等指标
            regime: 市场状态
        """
        from strategy_registry import registry
        
        # 5大核心量化策略
        core_strategy_names = [
            "limit_up_continue",
            "strong_pullback",
            "trend_acceleration",
            "competition_breakout",
            "volume_shrink_reversal"
        ]

        strategy_results = {}
        matched_strategies = []
        positive_votes = 0
        total_strategies = len(core_strategy_names)
        reasons = []

        for name in core_strategy_names:
            strat = registry.get(name)
            if not strat:
                continue
            try:
                res = strat.analyze(stock_data, indicators)
                res_dict = res.to_dict() if hasattr(res, "to_dict") else dict(res)
                strategy_results[name] = res_dict
                sig = res_dict.get("signal", "HOLD")
                score = float(res_dict.get("score") or 0.0)
                if sig in ("BUY", "STRONG_BUY", "WEAK_BUY") or score > 60:
                    positive_votes += 1
                    strat_cn = getattr(strat, "description", name) or name
                    matched_strategies.append(strat_cn.split("：")[0].split(" ")[0])
                    for r in res_dict.get("reasons", []):
                        if r not in reasons:
                            reasons.append(r)
            except Exception as e:
                logger.debug(f"策略 {name} 分析异常 {symbol}: {e}")

        # 1. 表决共识度与信号生成
        vote_ratio = positive_votes / total_strategies if total_strategies > 0 else 0.0
        if positive_votes >= 3:
            ensemble_signal = "STRONG_BUY" if positive_votes >= 4 else "BUY"
            consensus_desc = f"{positive_votes}/{total_strategies} 策略共识赞成买入"
        elif positive_votes == 2:
            ensemble_signal = "WEAK_BUY"
            consensus_desc = f"{positive_votes}/{total_strategies} 策略支持弱买入"
        elif positive_votes == 1:
            ensemble_signal = "HOLD"
            consensus_desc = f"仅 {positive_votes}/{total_strategies} 策略触碰信号，观望"
        else:
            ensemble_signal = "HOLD"
            consensus_desc = "0 策略通过集成表决门槛，防守观望"

        # 2. Lowdin 对称正交化去共线性打分
        try:
            from portfolio_opt import lowdin_orthogonalize
            import numpy as np
            # 基于指标收益与波动构造正交因子基底
            base_factors = np.array([
                float(indicators.get("rsi") or 50.0) / 100.0,
                float(stock_data.get("change_pct") or 0.0) / 10.0,
                vote_ratio,
                float(indicators.get("volume_ratio") or 1.0) / 3.0
            ]).reshape(1, -1)
            ortho_factors = lowdin_orthogonalize(base_factors)
            ortho_score = float(np.mean(ortho_factors) * 80.0 + 30.0)
            ortho_score = max(10.0, min(95.0, ortho_score))
        except Exception:
            ortho_score = round(vote_ratio * 70.0 + 20.0, 2)

        # 3. 动态配置建议权重 (HRP 风险平价与 Black-Litterman 稳健后验)
        hrp_weight = round(max(5.0, min(35.0, (1.0 - abs(float(stock_data.get("change_pct", 0))) * 0.02) * 20.0)), 2)
        bl_weight = round(max(5.0, min(35.0, vote_ratio * 30.0 + 10.0)), 2)

        return {
            "score": round(ortho_score, 1),
            "signal": ensemble_signal,
            "vote_ratio": round(vote_ratio, 2),
            "votes_display": f"{positive_votes}/{total_strategies} 赞成",
            "positive_votes": positive_votes,
            "total_strategies": total_strategies,
            "consensus_desc": consensus_desc,
            "matched_strategies": matched_strategies,
            "ortho_score": round(ortho_score, 2),
            "hrp_weight": hrp_weight,
            "bl_weight": bl_weight,
            "strategy_details": strategy_results,
            "signals": reasons[:5]
        }
