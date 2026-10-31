"""
团队赛多策略集成表决器 (Team Strategy Ensemble Voter - 优化增强版 v2.1)
专为 2026 年第十三届全国证券投资模拟实训大赛（团队赛段）设计。

核心机制:
1. 组合集成: 聚合强势回调(StrongPullback)、放量突破(VolumeBreakout)、趋势加速(TrendAcceleration)、缩量企稳(ShrinkStabilize)等多个子策略。
2. 市场状态与时序自适应: 
   - 集合竞价时段 (09:25): 重点加权放量突破(35%)、趋势加速(30%)与连板追踪(20%)，启用竞价专用等效量比；
   - 盘中时段: 联动 MarketRegime，牛市加大突破权重，弱势/震荡加大回调与企稳反转权重。
3. 团队投票决策: 必须至少有 2 个子策略同时发出 BUY 信号且综合置信度达标，或触发极高确信度单兵突破，才会开仓。
4. 团队风控内置约束: 单票建议仓位控制在 25%~30%（严格低于 35% 红线），止损线设为 -4.5%。
"""

from typing import Dict, List, Optional, Any
from .base import BaseStrategy, StrategyResult, Signal
from .competition_strategies import (
    StrongPullbackStrategy,
    VolumeBreakoutCompetition,
    LimitUpContinuationStrategy,
    VolumeShrinkReversal,
    TrendAccelerationStrategy
)


class TeamEnsembleStrategy(BaseStrategy):
    """
    团队赛多策略加权集成表决策略
    """
    name = "team_ensemble"
    description = "团队赛多策略集成表决器：聚合多策略投票，结合市场环境动态赋权，实现高胜率稳健开仓与严格团队风控"
    category = "competition"
    risk_level = "medium"
    params = {
        "min_votes": {"desc": "最小赞成投票数", "default": 2, "min": 1, "max": 4},
        "min_confidence": {"desc": "最小综合置信度", "default": 0.60, "min": 0.40, "max": 0.85},
        "target_position_pct": {"desc": "单票目标仓位(%)", "default": 28.0, "min": 10.0, "max": 33.0},
        "stop_loss_pct": {"desc": "团队止损比例(%)", "default": 4.5, "min": 2.0, "max": 5.0},
    }

    def __init__(self):
        super().__init__()
        self.sub_strategies = [
            StrongPullbackStrategy(),
            VolumeBreakoutCompetition(),
            LimitUpContinuationStrategy(),
            VolumeShrinkReversal(),
            TrendAccelerationStrategy(),
        ]

    def _get_regime_weights(self, market_data: Optional[Dict], is_auction: bool = False) -> Dict[str, float]:
        """根据大盘市场状态及是否集合竞价返回子策略精确权重"""
        if is_auction:
            # 集合竞价时段：重点聚焦高开突破与趋势惯性延续
            return {
                "competition_breakout": 0.35,
                "trend_acceleration": 0.30,
                "limit_up_continue": 0.20,
                "strong_pullback": 0.10,
                "volume_shrink_reversal": 0.05,
            }

        # 默认中性/震荡权重（修复策略类实际名称映射）
        weights = {
            "strong_pullback": 0.25,
            "competition_breakout": 0.25,
            "limit_up_continue": 0.15,
            "volume_shrink_reversal": 0.15,
            "trend_acceleration": 0.20,
        }
        if not market_data:
            return weights

        regime = market_data.get("regime", "neutral")  # bull / bear / volatile / neutral
        if regime == "bull":
            # 牛市/强趋势：加大突破与连板、趋势加速权重
            weights["competition_breakout"] = 0.35
            weights["trend_acceleration"] = 0.25
            weights["limit_up_continue"] = 0.20
            weights["strong_pullback"] = 0.10
            weights["volume_shrink_reversal"] = 0.10
        elif regime == "bear":
            # 弱势/防守：加大缩量企稳与强势回调权重，压低追高
            weights["volume_shrink_reversal"] = 0.35
            weights["strong_pullback"] = 0.35
            weights["competition_breakout"] = 0.10
            weights["trend_acceleration"] = 0.10
            weights["limit_up_continue"] = 0.10
        return weights

    def analyze(
        self,
        stock_data: Dict,
        indicators: Dict,
        market_data: Optional[Dict] = None,
        history: Optional[List[Dict]] = None,
        **kwargs
    ) -> StrategyResult:
        if not history or len(history) < 10:
            return StrategyResult(
                Signal.HOLD, 0, 0, ["数据不足，需要至少10日K线"],
                strategy_name=self.name
            )

        close = indicators.get("close") or stock_data.get("price", 0) or stock_data.get("open", 0)
        if not close or close <= 0:
            return StrategyResult(
                Signal.HOLD, 0, 0, ["价格指标缺失"],
                strategy_name=self.name
            )

        is_auction = kwargs.get("is_call_auction", False)
        if market_data and market_data.get("is_call_auction"):
            is_auction = True

        weights = self._get_regime_weights(market_data, is_auction=is_auction)

        votes_buy = 0
        total_weighted_score = 0.0
        total_weighted_conf = 0.0
        total_weight = sum(weights.values())
        sub_reasons = []

        # 遍历子策略执行打分并传递竞价上下文
        sub_kwargs = dict(kwargs)
        sub_kwargs.pop("is_call_auction", None)
        for strat in self.sub_strategies:
            w = weights.get(strat.name, 0.20)
            try:
                sub_res = strat.analyze(
                    stock_data=stock_data,
                    indicators=indicators,
                    market_data=market_data,
                    history=history,
                    is_call_auction=is_auction,
                    **sub_kwargs
                )
                if sub_res.signal in (Signal.BUY, Signal.STRONG_BUY):
                    votes_buy += 1
                    sub_reasons.append(f"[{strat.name}:赞成({sub_res.score:+.2f})]")
                else:
                    sub_reasons.append(f"[{strat.name}:中立或否决]")

                total_weighted_score += sub_res.score * w
                total_weighted_conf += sub_res.confidence * w
            except Exception as e:
                sub_reasons.append(f"[{strat.name}:执行异常({e})]")

        final_score = total_weighted_score / total_weight if total_weight > 0 else 0
        final_conf = total_weighted_conf / total_weight if total_weight > 0 else 0

        min_votes = self.params["min_votes"]["default"]
        min_conf = self.params["min_confidence"]["default"]
        stop_pct = self.params["stop_loss_pct"]["default"]

        mode_desc = "集合竞价定盘模式" if is_auction else "盘中K线常规模式"
        reasons = [
            f"团队策略投票({mode_desc}): {votes_buy}/{len(self.sub_strategies)} 赞成",
            f"综合评分: {final_score:+.2f}, 置信度: {final_conf:.2f}",
            "子策略明细: " + " ".join(sub_reasons)
        ]

        # 判定表决通过门槛
        vote_passed = False
        if is_auction:
            # 集合竞价时段：若 >=2 票赞成且置信度>=0.55且评分>0.20；或 >=1 票且置信度>=0.75与评分>0.38 (高确定性突破加速)
            if votes_buy >= 2 and final_conf >= 0.55 and final_score > 0.20:
                vote_passed = True
                reasons.append("⚡ 集合竞价多策略集成赞成表决通过")
            elif votes_buy >= 1 and final_conf >= 0.72 and final_score >= 0.35:
                vote_passed = True
                reasons.append("⚡ 集合竞价高确定性放量突破/趋势加速表决通过")
        else:
            if votes_buy >= min_votes and final_conf >= min_conf and final_score > 0.25:
                vote_passed = True

        if vote_passed:
            sig = Signal.STRONG_BUY if (votes_buy >= 3 or (is_auction and final_conf >= 0.70)) else Signal.BUY
            reasons.append("✅ 达到团队集成买入标准，建议仓位 25%~28% (严防单票35%红线)")
            return StrategyResult(
                signal=sig,
                score=final_score,
                confidence=final_conf,
                reasons=reasons,
                target_price=round(close * 1.12, 2),
                stop_loss=round(close * (1 - stop_pct / 100), 2),
                strategy_name=self.name,
                extra={
                    "votes_buy": votes_buy,
                    "target_position_pct": self.params["target_position_pct"]["default"],
                    "is_call_auction": is_auction
                }
            )
        else:
            return StrategyResult(
                signal=Signal.HOLD,
                score=final_score,
                confidence=final_conf,
                reasons=reasons,
                target_price=None,
                stop_loss=round(close * (1 - stop_pct / 100), 2),
                strategy_name=self.name,
                extra={"votes_buy": votes_buy, "is_call_auction": is_auction}
            )
