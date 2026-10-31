"""
AI 大脑模块
AI 作为决策中心，选择策略、执行分析、评估表现、编写新策略
"""

import json
import logging
import random
from typing import Dict, List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

from strategies.base import StrategyResult, Signal
from strategy_registry import registry
from strategy_tracker import strategy_tracker
from ai_connector import ai_connector
from risk_manager import risk_manager

# 延迟导入避免循环
_stock_data_func = None


def set_stock_data_func(func):
    global _stock_data_func
    _stock_data_func = func


ACCOUNT_STYLE_CONFIG = {
    # 账户 1: 先锋进攻型 (09:25 竞价量比与连板突破)
    "aggressive": {
        "max_single_position": 0.33,
        "min_confidence": 0.52,
        "stop_loss_pct": 0.05,
        "desc": "先锋进攻型",
        "tactic": "pioneer_breakout",
        "prefer_strategies": ["competition_breakout", "limit_up_continue", "volume_breakout"],
    },
    # 账户 2: 趋势中军型 (主线板块核心标的缩量回调买点 StrongPullback)
    "balanced": {
        "max_single_position": 0.28,
        "min_confidence": 0.58,
        "stop_loss_pct": 0.045,
        "desc": "趋势中军型",
        "tactic": "trend_mainforce_pullback",
        "prefer_strategies": ["strong_pullback", "volume_shrink_reversal", "trend_acceleration"],
    },
    # 账户 3: 防守套利型 (14:50 尾盘低吸博取次日早盘溢价)
    "defensive": {
        "max_single_position": 0.20,
        "min_confidence": 0.62,
        "stop_loss_pct": 0.035,
        "desc": "防守套利型",
        "tactic": "closing_low_absorption",
        "prefer_strategies": ["closing_arbitrage", "volume_shrink_reversal", "reversal"],
    },
}


@dataclass
class BrainDecision:
    """AI大脑决策结果（支持多账户定制与独立风控状态）"""
    symbol: str
    action: str  # buy/sell/hold
    signal: str  # STRONG_BUY/BUY/WEAK_BUY/HOLD/WEAK_SELL/SELL/STRONG_SELL
    confidence: float
    strategies_used: List[str]
    reasons: List[str]
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    position_size: int = 0  # 建议买入/卖出股数
    ai_reasoning: str = ""  # AI的推理过程
    timestamp: str = ""
    account_id: Optional[int] = None
    account_name: str = ""
    status: str = "APPROVED"  # APPROVED / REJECTED_POSITION_LIMIT / REJECTED_INSUFFICIENT_FUNDS / FILTERED_NO_HOLDINGS / FILTERED_T1_LOCKED / REJECTED_CONFIDENCE

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "signal": self.signal,
            "confidence": round(self.confidence, 4),
            "strategies_used": self.strategies_used,
            "reasons": self.reasons,
            "target_price": self.target_price,
            "stop_loss": self.stop_loss,
            "position_size": self.position_size,
            "ai_reasoning": self.ai_reasoning,
            "timestamp": self.timestamp,
            "account_id": self.account_id,
            "account_name": self.account_name,
            "status": self.status
        }


class AIBrain:
    """AI 大脑 - 交易决策中心"""

    def __init__(self):
        self.context = {
            "market_state": "unknown",  # bull/bear/sideways/unknown
            "recent_decisions": [],
            "active_strategies": []
        }
        # 动态阈值: 记录最近 200 个决策分数，据此计算自适应阈值
        self._score_history: List[float] = []
        self._score_history_max = 200
        # ★ 用户策略偏移：每个用户首次生成随机种子，后续固定
        self._user_seed = self._load_or_create_seed()
        self._strategy_offsets = self._generate_offsets()

    def _load_or_create_seed(self) -> int:
        """加载或创建用户随机种子"""
        seed_file = Path(__file__).parent.parent / "config" / "user_seed.json"
        try:
            if seed_file.exists():
                data = json.loads(seed_file.read_text(encoding="utf-8"))
                return data.get("seed", 0)
        except Exception:
            pass
        # 生成新种子
        import random
        seed = random.randint(1, 999999)
        seed_file.parent.mkdir(parents=True, exist_ok=True)
        seed_file.write_text(json.dumps({"seed": seed}), encoding="utf-8")
        return seed

    def _generate_offsets(self) -> Dict[str, float]:
        """用种子为每个策略生成固定的随机偏移 (-0.15 ~ +0.15)"""
        import random
        random.seed(self._user_seed)
        offsets = {}
        from strategy_registry import registry
        for name in registry.list_names():
            offsets[name] = random.uniform(-0.15, 0.15)
        random.seed()  # 恢复随机性
        return offsets

    def get_strategy_catalog(self) -> str:
        """获取策略目录（给AI读的）"""
        return registry.get_catalog()

    def get_strategy_catalog_json(self) -> List[Dict]:
        """获取策略目录（JSON格式）"""
        return registry.get_catalog_json()

    def decide(self, symbol: str, stock_data: Dict, indicators: Dict,
               account_info: Dict = None, strategy_names: List[str] = None,
               history: Optional[List[Dict]] = None) -> BrainDecision:
        """
        AI 做出交易决策

        流程:
        0. 获取市场环境
        1. 用选定策略分析（传入市场环境）
        2. 综合评分 + 环境加成
        3. AI判断并给出最终决策
        """
        reasons = []
        sub_signals = []

        # 0. 获取市场环境
        try:
            from market_regime import market_regime_detector
            regime_info = market_regime_detector.detect()
            self.context["market_state"] = regime_info["regime"]
            reasons.append(f"市场环境: {regime_info['description']}")
        except Exception:
            regime_info = None

        # 0.5 获取板块轮动信息
        try:
            from sector_analysis import sector_analyzer
            sector_score = sector_analyzer.get_sector_score(symbol)
            market_sentiment = sector_analyzer.get_market_sentiment()
            reasons.append(f"板块得分: {sector_score:.2f}, 市场情绪: {market_sentiment['score']}")
        except Exception:
            sector_score = 0.5
            market_sentiment = None

        # 0.6 获取新闻情绪
        try:
            from sentiment_analyzer import sentiment_analyzer
            news_sentiment = sentiment_analyzer.get_stock_sentiment(symbol, stock_data.get("name", ""))
            reasons.append(f"新闻情绪: {news_sentiment['score']}分 ({news_sentiment['description']})")
        except Exception:
            news_sentiment = {"score": 50}

        # 0.7 提取账户风格与战术矩阵
        acc_style = account_info.get("style", "balanced") if account_info else "balanced"
        style_cfg = ACCOUNT_STYLE_CONFIG.get(acc_style, ACCOUNT_STYLE_CONFIG["balanced"])

        # 1. 选择要使用的策略（优先匹配账户专属战术矩阵）
        if not strategy_names:
            if style_cfg.get("prefer_strategies"):
                strategy_names = [s for s in style_cfg["prefer_strategies"] if registry.get(s)]
            if not strategy_names:
                strategy_names = self._auto_select_strategies(stock_data, indicators, regime_info)

        # 2. 执行每个策略分析（传入市场环境 + 优化参数）
        market_data = {"regime": regime_info} if regime_info else None
        from strategy_optimizer import strategy_optimizer
        for name in strategy_names:
            strategy = registry.get(name)
            if strategy:
                try:
                    # 注入优化后的参数
                    opt_params = strategy_optimizer.load_optimized_params(name)
                    original_params = {}
                    if opt_params:
                        for k, v in opt_params.items():
                            if k in strategy.params:
                                original_params[k] = strategy.params[k].get("default")
                                strategy.params[k]["default"] = v
                    try:
                        result = strategy.analyze(stock_data, indicators, market_data=market_data, history=history)
                    finally:
                        # 恢复原始参数
                        for k, v in original_params.items():
                            strategy.params[k]["default"] = v

                    # ★ 加入用户策略偏移（让不同用户有差异化信号）
                    offset = self._strategy_offsets.get(name, 0)
                    result.score = max(0, min(1, result.score + offset))

                    sub_signals.append(result)
                    param_note = " (优化参数)" if opt_params else ""
                    reasons.append(f"[{name}] {result.signal.value} 分数:{result.score:.3f}{param_note}")
                except Exception as e:
                    logger.error(f"策略 {name} 执行失败: {e}")

        # 3. 综合评分
        if not sub_signals:
            return BrainDecision(
                symbol=symbol, action="hold", signal="HOLD",
                confidence=0, strategies_used=[], reasons=["无可用策略"]
            )

        total_score = sum(s.score * s.confidence for s in sub_signals)
        total_conf = sum(s.confidence for s in sub_signals)
        avg_score = total_score / total_conf if total_conf > 0 else 0
        avg_confidence = total_conf / len(sub_signals)

        # 3.5 市场环境调整
        if regime_info:
            try:
                from market_regime import market_regime_detector
                adj = market_regime_detector.get_strategy_adjustments(regime_info["regime"])
                avg_confidence += adj.get("confidence_boost", 0)
                avg_confidence = max(0.1, min(0.95, avg_confidence))
                if adj["position_factor"] != 1.0:
                    reasons.append(f"仓位调整: {adj['position_factor']}x ({regime_info['regime']})")
            except Exception:
                pass

        # 3.6 板块轮动调整
        if sector_score > 0.7:
            avg_confidence += 0.1  # 热点板块加分
            reasons.append(f"板块加分: {sector_score:.2f} (热点板块)")
        elif sector_score < 0.3:
            avg_confidence -= 0.1  # 冷门板块减分
            reasons.append(f"板块减分: {sector_score:.2f} (冷门板块)")

        # 3.7 新闻情绪调整
        if news_sentiment:
            sentiment_score = news_sentiment.get("score", 50)
            if sentiment_score > 65:
                avg_confidence += 0.05  # 新闻偏多加分
                reasons.append(f"新闻加分: {sentiment_score}分 (偏多)")
            elif sentiment_score < 35:
                avg_confidence -= 0.05  # 新闻偏空减分
                reasons.append(f"新闻减分: {sentiment_score}分 (偏空)")

        # 3.8 多时间框架共振
        try:
            from multi_timeframe import multi_timeframe_analyzer
            weekly = multi_timeframe_analyzer.get_weekly_indicators(symbol)
            alignment = multi_timeframe_analyzer.analyze_alignment(indicators, weekly, {})
            if alignment["aligned_bull"]:
                avg_score += alignment["timeframe_boost"]
                reasons.append(f"多周期看多共振(+{alignment['timeframe_boost']:.2f})")
            elif alignment["aligned_bear"]:
                avg_score -= alignment["timeframe_boost"]
                reasons.append(f"多周期看空共振(-{alignment['timeframe_boost']:.2f})")
        except Exception:
            pass

        # 4. 决策
        action, signal = self._score_to_action(avg_score)

        # 5. 异构资产信号适配与每账户独立风控过滤
        price = stock_data.get("price", 0)
        position_size = 0
        target_price = None
        stop_loss = None
        status = "APPROVED"
        acc_id = account_info.get("account_id") if account_info else None
        acc_name = account_info.get("name", f"账户_{acc_id}" if acc_id else "默认账户") if account_info else "默认账户"
        acc_style = account_info.get("style", "balanced") if account_info else "balanced"
        style_cfg = ACCOUNT_STYLE_CONFIG.get(acc_style, ACCOUNT_STYLE_CONFIG["balanced"])
        max_single_pos = account_info.get("max_single_position", style_cfg["max_single_position"]) if account_info else 0.30
        min_conf_req = account_info.get("min_confidence", style_cfg["min_confidence"]) if account_info else 0.50

        # 规则0: 主力资金背离一票否决校验 (舆情分 >= 75 但主力大单净流出超过流通市值 0.8% 防诱多)
        sentiment_score = news_sentiment.get("score", 50) if news_sentiment else 50
        net_outflow_ratio = float(stock_data.get("main_net_outflow_ratio", 0) or stock_data.get("large_order_net_outflow_pct", 0) or 0)
        if net_outflow_ratio <= 0 and stock_data.get("main_net_outflow") and stock_data.get("float_market_cap"):
            try:
                net_outflow_ratio = float(stock_data["main_net_outflow"]) / float(stock_data["float_market_cap"])
            except Exception:
                pass

        if sentiment_score >= 75 and net_outflow_ratio >= 0.008:
            action, signal = "hold", "HOLD"
            status = "REJECTED_CAPITAL_DIVERGENCE"
            reasons.append(f"风控一票否决: 标的{symbol}舆情得分高涨({sentiment_score})，但主力大单净流出达流通市值{net_outflow_ratio*100:.2f}% (>=0.80%)，判定为机构诱多出货，禁止买入")
            position_size = 0

        elif action == "buy" and account_info and price > 0:
            balance = float(account_info.get("balance", 0))
            total_assets = float(account_info.get("total_assets", balance))
            if total_assets <= 0:
                total_assets = balance or 1000000.0
            positions = account_info.get("positions", {})
            existing_pos = positions.get(symbol, {})
            existing_amount = existing_pos.get("amount", 0)
            existing_mv = existing_amount * price

            # 战术矩阵校验: 3账户异构战术分流与风控过滤
            if style_cfg.get("tactic") == "pioneer_breakout" and not (
                account_info.get("is_call_auction")
                or (market_data and market_data.get("is_call_auction"))
                or any(s.strategy_name in ("competition_breakout", "limit_up_continue", "volume_breakout") and s.signal in (Signal.BUY, Signal.STRONG_BUY) for s in sub_signals)
                or stock_data.get("change_pct", 0) >= 3.0
            ):
                action, signal = "hold", "HOLD"
                status = "FILTERED_TACTIC_MISMATCH"
                reasons.append(f"战术矩阵过滤: 账户[{acc_name}]专职先锋进攻(09:25竞价量比与连板突破)，标的{symbol}非突破态势")
                position_size = 0

            elif style_cfg.get("tactic") == "trend_mainforce_pullback" and stock_data.get("change_pct", 0) > 7.0:
                action, signal = "hold", "HOLD"
                status = "FILTERED_TACTIC_CHASING_HIGH"
                reasons.append(f"战术矩阵过滤: 账户[{acc_name}]专职趋势中军缩量回调(StrongPullback)，拒绝追高(+{stock_data.get('change_pct',0):.1f}%)")
                position_size = 0

            elif style_cfg.get("tactic") == "closing_low_absorption" and not account_info.get("is_closing_session", False) and stock_data.get("change_pct", 0) > 3.0:
                action, signal = "hold", "HOLD"
                status = "FILTERED_TACTIC_NOT_CLOSING"
                reasons.append(f"战术矩阵过滤: 账户[{acc_name}]专职14:50尾盘低吸套利，早盘盘中冲高(+{stock_data.get('change_pct',0):.1f}%)不予建仓，锁定尾盘博弈")
                position_size = 0

            # 规则1: 账户风格置信度门槛一票否决
            elif avg_confidence < min_conf_req:
                action, signal = "hold", "HOLD"
                status = "REJECTED_CONFIDENCE"
                reasons.append(f"风控一票否决: 信号置信度({avg_confidence:.2f})未达账户[{acc_name}]门槛({min_conf_req:.2f})")
                position_size = 0

            # 规则2: 单票持仓上限一票否决 (持仓已接近或超过单票红线)
            elif total_assets > 0 and (existing_mv / total_assets) >= (max_single_pos - 0.005):
                action, signal = "hold", "HOLD"
                status = "REJECTED_POSITION_LIMIT"
                reasons.append(f"风控一票否决: 账户[{acc_name}]对标的{symbol}持仓已达单票上限({existing_mv/total_assets*100:.1f}% >= {max_single_pos*100:.1f}%)")
                position_size = 0

            # 规则3: 账户可用资金不足一票否决 (买不起1手)
            elif balance < price * 100 * 1.001:
                action, signal = "hold", "HOLD"
                status = "REJECTED_INSUFFICIENT_FUNDS"
                reasons.append(f"风控一票否决: 账户[{acc_name}]可用资金({balance:.2f}元)不足以买入1手(需{price*100:.2f}元)")
                position_size = 0

            else:
                # 规则4: 定制化可买仓位精算（在单票上限剩余额度与可用现金之间取最小值）
                remaining_mv_room = max(0.0, total_assets * max_single_pos - existing_mv)
                cash_available = balance * 0.95  # 预留5%手续费与滑点缓冲
                affordable_val = min(remaining_mv_room, cash_available)
                custom_shares = int(affordable_val / price / 100) * 100 if price > 0 else 0

                if custom_shares < 100:
                    action, signal = "hold", "HOLD"
                    status = "REJECTED_INSUFFICIENT_FUNDS"
                    reasons.append(f"风控拦截: 账户[{acc_name}]经单票上限与可用资金约束后可买数量不足1手")
                    position_size = 0
                else:
                    position_size = custom_shares
                    status = "APPROVED"
                    reasons.append(f"风控通过: 账户[{acc_name}]建议定制委托买入 {custom_shares}股 (预计占用{custom_shares*price:.2f}元, 占总资产{(existing_mv+custom_shares*price)/total_assets*100:.1f}%)")

            # 动态差异化 ATR 止损止盈
            volatility = indicators.get("atr", price * 0.02) / price if price else 0.02
            sl_info = risk_manager.calculate_stop_loss(price, volatility, symbol=symbol)
            tp_info = risk_manager.calculate_take_profit(price, volatility)
            stop_loss = sl_info["stop_loss"]
            target_price = tp_info["take_profit"]
            tp_info = risk_manager.calculate_take_profit(price, volatility)
            stop_loss = sl_info["stop_loss"]
            target_price = tp_info["take_profit"]

        elif action == "sell":
            if account_info:
                positions = account_info.get("positions", {})
                existing_pos = positions.get(symbol, {})
                existing_amount = existing_pos.get("amount", 0)
                available_to_sell = existing_pos.get("available", existing_amount)

                if existing_amount <= 0:
                    action, signal = "hold", "HOLD"
                    status = "FILTERED_NO_HOLDINGS"
                    reasons.append(f"风控过滤: 账户[{acc_name}]当前未持有{symbol}，忽略卖出信号")
                    position_size = 0
                elif available_to_sell <= 0:
                    action, signal = "hold", "HOLD"
                    status = "FILTERED_T1_LOCKED"
                    reasons.append(f"风控过滤: 账户[{acc_name}]持有{symbol}{existing_amount}股，但处于T+1锁定中，今日可用为0")
                    position_size = 0
                else:
                    status = "APPROVED"
                    position_size = available_to_sell
                    reasons.append(f"风控通过: 账户[{acc_name}]建议定制委托卖出 {available_to_sell}股")

            target_price = price * 0.92 if price else None
            stop_loss = price * 1.05 if price else None

        # 7. 记录信号
        for sig in sub_signals:
            strategy_tracker.record_signal(
                sig.strategy_name, symbol, sig.signal.value,
                sig.score, sig.confidence, price
            )

        used_strategies = [s.strategy_name for s in sub_signals]

        decision = BrainDecision(
            symbol=symbol,
            action=action,
            signal=signal,
            confidence=avg_confidence,
            strategies_used=used_strategies,
            reasons=reasons,
            target_price=round(target_price, 2) if target_price else None,
            stop_loss=round(stop_loss, 2) if stop_loss else None,
            position_size=position_size,
            account_id=acc_id,
            account_name=acc_name,
            status=status
        )

        self.context["recent_decisions"].append(decision.to_dict())
        if len(self.context["recent_decisions"]) > 50:
            self.context["recent_decisions"] = self.context["recent_decisions"][-50:]

        return decision

    def decide_with_ai(self, symbol: str, stock_data: Dict, indicators: Dict,
                       account_info: Dict = None) -> BrainDecision:
        """
        纯量化策略综合决策：彻底去除外部大模型调用，直接由多策略矩阵评估
        """
        return self.decide(symbol, stock_data, indicators, account_info)

    def _auto_select_strategies(self, stock_data: Dict, indicators: Dict,
                                 regime_info: Dict = None) -> List[str]:
        """
        根据市场条件 + 健康状态 + 优化参数选择策略

        逻辑:
        1. 检查每个策略的健康状态，排除已失效的
        2. 根据当前市场状态筛选匹配的策略类别
        3. 根据市场环境调整偏好
        4. 优先使用优化后的参数
        5. 按权重排序，取前6个
        """
        from strategy_tracker import strategy_tracker
        from strategy_health import strategy_health_monitor

        change_pct = stock_data.get("change_pct", 0)
        turnover = stock_data.get("turnover", 0)
        rsi = indicators.get("rsi")
        high = stock_data.get("high", 0)
        low = stock_data.get("low", 0)
        price = stock_data.get("price", 0)

        # 1. 获取策略权重
        weights = strategy_tracker.get_strategy_weights()

        # 2. 获取健康状态，排除已失效的策略
        health_status = {}
        for name in registry.list_names():
            health = strategy_health_monitor.check_health(name)
            health_status[name] = health["status"]

        # 3. 根据市场环境 + 比赛阶段获取偏好
        prefer_categories = []
        avoid_categories = []
        if regime_info:
            try:
                from market_regime import market_regime_detector
                adj = market_regime_detector.get_strategy_adjustments(regime_info["regime"])
                prefer_categories = adj.get("prefer_categories", [])
                avoid_categories = adj.get("avoid_categories", [])
            except Exception:
                pass

        # 比赛阶段: 根据进度调整策略偏好
        try:
            from risk_manager import risk_manager
            days = risk_manager.get_competition_days_elapsed()
            total = risk_manager.get_total_competition_days()
            phase = risk_manager.get_phase_config(days, total)
            phase_prefer = phase.get("prefer_categories", [])
            # 合并比赛阶段偏好（competition类别始终优先）
            for cat in phase_prefer:
                if cat not in prefer_categories:
                    prefer_categories.append(cat)
        except Exception:
            pass

        # 4. 根据市场条件筛选匹配的策略（排除已失效的）
        candidates = []
        for name, instance in registry.get_all().items():
            # 排除已失效的策略
            if health_status.get(name) == "dead":
                continue
            # 排除避免类别的策略
            if instance.category in avoid_categories:
                continue

            matched = False
            # 趋势明显 → 趋势/动量策略
            if abs(change_pct) > 2 and instance.category in ("trend", "momentum"):
                matched = True
            # RSI极端 → 反转/超短策略
            if rsi and (rsi > 70 or rsi < 30) and instance.category in ("reversal", "shortterm"):
                matched = True
            # 放量 → 量能策略
            if turnover > 1.5 and instance.category == "volume":
                matched = True
            # 高波动 → 波动率策略
            if price and high and low:
                daily_range = (high - low) / price * 100
                if daily_range > 4 and instance.category == "volatility":
                    matched = True
            # 市场环境偏好的类别优先加入
            if instance.category in prefer_categories:
                matched = True
            # 比赛制胜策略始终参与（高优先级）
            if instance.category == "competition":
                matched = True
            # 综合策略始终参与
            if instance.category == "composite":
                matched = True

            if matched:
                candidates.append(name)

        # 5. 加入高权重策略（top 3），确保不遗漏表现好的策略
        top_weighted = sorted(weights.items(), key=lambda x: x[1], reverse=True)[:3]
        for name, w in top_weighted:
            if name not in candidates and health_status.get(name) != "dead":
                candidates.append(name)

        # 去重
        candidates = list(set(candidates))

        # 6. 按权重排序，衰减中的策略降权
        def sort_key(n):
            w = weights.get(n, 0.02)
            if health_status.get(n) == "declining":
                w *= 0.5  # 衰减中的策略权重减半
            return w

        candidates.sort(key=sort_key, reverse=True)
        selected = candidates[:6]

        # 至少选1个
        if not selected:
            selected = ["multi_factor"]

        logger.info(f"策略选择: {selected} (健康状态: {[health_status.get(n, 'unknown') for n in selected]})")
        return selected

    def _parse_ai_strategy_selection(self, ai_content: str) -> List[str]:
        """从AI回复中解析选择的策略"""
        available = registry.list_names()
        selected = []
        content_lower = ai_content.lower()
        for name in available:
            if name.lower() in content_lower:
                selected.append(name)
        return selected[:6] if selected else None

    def _score_to_action(self, score):
        """
        分数转行动（动态阈值）

        根据最近 200 个决策分数的分布自适应调整:
        - 高波动市场 → 阈值上移，避免频繁交易
        - 低波动市场 → 阈值下移，捕捉弱信号

        保底阈值: weak≥0.03, normal≥0.10, strong≥0.25（防止噪声）
        """
        # 记录本次分数
        self._score_history.append(abs(score))
        if len(self._score_history) > self._score_history_max:
            self._score_history = self._score_history[-self._score_history_max:]

        # 计算动态阈值
        thresholds = self._calc_dynamic_thresholds()

        weak = thresholds["weak"]
        normal = thresholds["normal"]
        strong = thresholds["strong"]

        if score >= strong:
            return "buy", "STRONG_BUY"
        elif score >= normal:
            return "buy", "BUY"
        elif score >= weak:
            return "buy", "WEAK_BUY"
        elif score <= -strong:
            return "sell", "STRONG_SELL"
        elif score <= -normal:
            return "sell", "SELL"
        elif score <= -weak:
            return "sell", "WEAK_SELL"
        return "hold", "HOLD"

    def _calc_dynamic_thresholds(self) -> Dict:
        """
        基于历史分数分布计算动态阈值

        算法:
        1. 取最近分数的百分位数作为基准
        2. 高波动(分数离散大) → 阈值上移
        3. 低波动(分数集中) → 阈值下移
        4. 保底值防止过度交易
        """
        # 保底阈值
        FLOOR_WEAK = 0.03
        FLOOR_NORMAL = 0.10
        FLOOR_STRONG = 0.25

        # 默认值（历史不足 20 个样本时）
        if len(self._score_history) < 20:
            return {"weak": FLOOR_WEAK, "normal": FLOOR_NORMAL, "strong": FLOOR_STRONG}

        sorted_scores = sorted(self._score_history)
        n = len(sorted_scores)

        def percentile(p):
            """计算第 p 百分位数"""
            idx = int(n * p)
            return sorted_scores[min(idx, n - 1)]

        # 基于百分位的阈值
        # weak: 25th percentile (分数分布中等偏低)
        # normal: 50th percentile (中位数)
        # strong: 75th percentile (分数分布较高)
        weak = max(percentile(0.25), FLOOR_WEAK)
        normal = max(percentile(0.50), FLOOR_NORMAL)
        strong = max(percentile(0.75), FLOOR_STRONG)

        # 确保递增关系
        if normal <= weak:
            normal = weak + 0.03
        if strong <= normal:
            strong = normal + 0.05

        # 日内波动率调整: 标准差越大 → 阈值越高（更谨慎）
        import statistics
        if len(self._score_history) >= 10:
            std = statistics.stdev(self._score_history[-50:]) if len(self._score_history) >= 50 else statistics.stdev(self._score_history)
            # std > 0.15 说明市场波动大，阈值上移 20%
            if std > 0.15:
                boost = 1.2
                weak = max(weak * boost, FLOOR_WEAK)
                normal = max(normal * boost, FLOOR_NORMAL)
                strong = max(strong * boost, FLOOR_STRONG)

        return {"weak": round(weak, 4), "normal": round(normal, 4), "strong": round(strong, 4)}

    def get_dynamic_thresholds(self) -> Dict:
        """获取当前动态阈值（供前端展示）"""
        return {
            **self._calc_dynamic_thresholds(),
            "sample_count": len(self._score_history),
            "max_samples": self._score_history_max
        }

    def evaluate_and_adapt(self) -> Dict:
        """评估策略表现并给出调整建议"""
        rankings = strategy_tracker.rank_strategies(days=30)
        summary = strategy_tracker.get_strategy_summary()

        # 策略表现分析
        good_strategies = []
        bad_strategies = []
        for r in rankings:
            if r.get("completed_evaluations", 0) > 0:
                if r.get("win_rate", 0) > 50:
                    good_strategies.append(r["strategy"])
                elif r.get("win_rate", 0) < 30:
                    bad_strategies.append(r["strategy"])

        suggestions = []
        if good_strategies:
            suggestions.append(f"近期表现好的策略: {', '.join(good_strategies)}")
        if bad_strategies:
            suggestions.append(f"近期表现差的策略: {', '.join(bad_strategies)}，建议减少使用")
        if not rankings or all(r.get("total_signals", 0) == 0 for r in rankings):
            suggestions.append("尚无足够数据评估策略表现，继续积累信号记录")

        return {
            "rankings": rankings,
            "summary": summary,
            "good_strategies": good_strategies,
            "bad_strategies": bad_strategies,
            "suggestions": suggestions,
            "timestamp": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        }

    def write_strategy(self, spec: str) -> Dict:
        """
        AI 根据自然语言描述编写新策略

        Args:
            spec: 策略描述（自然语言）
        """
        if not ai_connector.connected:
            return {"success": False, "message": "AI未连接，无法编写策略"}

        # 获取现有策略基类文档
        base_doc = """
策略基类接口:
class BaseStrategy:
    name: str           # 策略唯一名称
    description: str    # 策略描述
    category: str       # trend/momentum/reversal/volatility/volume/pattern/composite
    risk_level: str     # low/medium/high
    params: dict        # 可调参数

    def analyze(self, stock_data, indicators, market_data=None, history=None, **kwargs) -> StrategyResult:
        # stock_data: {price, change_pct, volume, amount, turnover, open, high, low, pre_close}
        # indicators: {close, ma5, ma10, ma20, rsi, macd, signal, histogram, bb_upper, bb_middle, bb_lower, atr, adx, dmi_plus, dmi_minus, kdj_k, kdj_d, kdj_j}
        # 返回: StrategyResult(signal, score, confidence, reasons, target_price, stop_loss)

from strategies.base import BaseStrategy, StrategyResult, Signal
"""

        system_prompt = f"""你是一个Python量化交易策略开发者。请根据用户的描述编写交易策略。

{base_doc}

要求:
1. 继承 BaseStrategy
2. 设置 name, description, category, risk_level, params
3. 实现 analyze 方法，返回 StrategyResult
4. 代码要完整可运行
5. 只返回Python代码，不要其他内容"""

        result = ai_connector.chat(spec, system_prompt)
        if result.get("success"):
            code = result["data"]["content"]
            # 提取代码块
            if "```python" in code:
                code = code.split("```python")[1].split("```")[0]
            elif "```" in code:
                code = code.split("```")[1].split("```")[0]

            try:
                # 验证代码语法
                compile(code, "<strategy>", "exec")
                return {"success": True, "code": code, "message": "策略编写成功"}
            except SyntaxError as e:
                return {"success": False, "message": f"代码语法错误: {e}", "code": code}

        return {"success": False, "message": result.get("message", "AI请求失败")}

    def install_strategy(self, name: str, code: str) -> Dict:
        """安装自定义策略"""
        try:
            registry.save_custom_strategy(name, code)
            # 动态加载
            import importlib.util
            filepath = Path(__file__).parent.parent / "config" / "custom_strategies" / f"{name}.py"
            spec = importlib.util.spec_from_file_location(name, filepath)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "STRATEGY_CLASS"):
                registry.register(module.STRATEGY_CLASS)
                return {"success": True, "message": f"策略 {name} 安装成功"}
            else:
                return {"success": False, "message": "策略代码中未定义 STRATEGY_CLASS"}
        except Exception as e:
            return {"success": False, "message": f"安装失败: {e}"}

    # ==================== 多账户决策 ====================

    def decide_for_account(self, account_id: int, symbol: str, stock_data: Dict,
                           indicators: Dict, strategy_names: List[str] = None) -> BrainDecision:
        """为指定账户做交易决策（异构资产与独立风控过滤）"""
        from account_manager import account_manager
        from db_store import db_store

        acc = db_store.get_account(account_id) or {}
        acc_name = acc.get("name", f"账户_{account_id}")

        # 风格智能映射（账户1进攻、账户2均衡、账户3高股息防御）
        style = "balanced"
        if account_id == 1 or "进攻" in acc_name:
            style = "aggressive"
        elif account_id == 3 or "防御" in acc_name or "高股息" in acc_name:
            style = "defensive"

        account_info = db_store.get_account_summary(account_id) or {}
        trader = account_manager.get_trader(account_id)
        if trader and trader._positions:
            account_info["positions"] = {s: p for s, p in trader._positions.items()}
        elif not account_info.get("positions"):
            account_info["positions"] = db_store.get_positions(account_id) or {}

        account_info["account_id"] = account_id
        account_info["name"] = acc_name
        account_info["style"] = style
        return self.decide(symbol, stock_data, indicators, account_info, strategy_names)

    def decide_for_all(self, symbol: str, stock_data: Dict, indicators: Dict,
                       strategy_names: List[str] = None) -> Dict[int, BrainDecision]:
        """为所有账户做交易决策，返回各账户定制化委托建议 {account_id: BrainDecision}"""
        from account_manager import account_manager
        from db_store import db_store

        accounts = db_store.list_accounts()
        acc_ids = [a["id"] for a in accounts] if accounts else list(account_manager._stores.keys())
        if not acc_ids:
            acc_ids = [1, 2, 3]

        results = {}
        for acc_id in acc_ids:
            try:
                results[acc_id] = self.decide_for_account(acc_id, symbol, stock_data, indicators, strategy_names)
            except Exception as e:
                logger.error(f"账户 {acc_id} 决策失败: {e}")
        return results


# 全局AI大脑实例
ai_brain = AIBrain()


# ==================== 策略定时扫描器 ====================

import threading
import time as _time


class StrategyScanner:
    """
    策略定时扫描器
    交易时段每5分钟扫描股票池，用策略分析并记录信号
    发现强信号时生成提醒
    """

    def __init__(self):
        self._thread = None
        self._running = False
        self._scan_interval = 300  # 5分钟（默认）
        self._stocks = []  # 扫描的股票列表
        self._strategy_names = []  # 使用的策略
        self._last_signals = []  # 最近一次扫描的信号
        self._scan_count = 0
        self._consecutive_errors = 0
        self._last_error = ""
        self._callbacks = []
        # 动态参数
        self._buy_threshold = 0.3  # 买入信号阈值
        self._max_per_round = 20  # 每轮最多扫描数
        # 自动执行配置
        self._auto_execute = False
        self._auto_execute_threshold = 0.5  # 自动执行的最低分数
        self._max_daily_auto_trades = 5
        self._daily_trade_count = 0
        self._last_trade_date = ""
        # 市场环境缓存
        self._last_regime = "unknown"

    def configure(self, stocks: List[str] = None, strategy_names: List[str] = None,
                  interval: int = 300, max_per_round: int = None,
                  buy_threshold: float = None, auto_execute: bool = None):
        """配置扫描参数"""
        if stocks is not None:
            self._stocks = stocks
        if strategy_names is not None:
            self._strategy_names = strategy_names
        if interval:
            self._scan_interval = max(60, interval)  # 最少60秒
        if max_per_round is not None:
            self._max_per_round = max(max_per_round, 5)
        if buy_threshold is not None:
            self._buy_threshold = max(0.1, min(buy_threshold, 0.8))
        if auto_execute is not None:
            self._auto_execute = auto_execute

    def start(self):
        """启动扫描"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._thread.start()
        logger.info(f"策略扫描器已启动，间隔 {self._scan_interval}s，{len(self._stocks)} 只股票")

    def stop(self):
        """停止扫描"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("策略扫描器已停止")

    def add_callback(self, callback):
        """添加信号回调"""
        self._callbacks.append(callback)

    def _scan_loop(self):
        """扫描主循环（市场环境自适应间隔）"""
        # 各环境下的扫描间隔（秒）
        regime_intervals = {
            "bull": 300,       # 牛市: 5分钟（机会多）
            "bear": 600,       # 熊市: 10分钟（减少噪声）
            "volatile": 180,   # 波动市: 3分钟（信号来得快）
            "sideways": 300,   # 震荡: 5分钟（默认）
        }
        while self._running:
            try:
                from competition import competition_manager
                if competition_manager.is_trading_hours() and self._stocks:
                    try:
                        from market_regime import market_regime_detector
                        regime_result = market_regime_detector.detect()
                        self._last_regime = regime_result.get("regime", "sideways")
                    except Exception:
                        self._last_regime = "sideways"

                    current_interval = regime_intervals.get(self._last_regime, self._scan_interval)
                    self._do_scan()
                    self._consecutive_errors = 0
                    self._last_error = ""
                else:
                    current_interval = self._scan_interval
            except Exception as e:
                self._consecutive_errors += 1
                self._last_error = str(e)
                logger.error(f"扫描异常 (连续第{self._consecutive_errors}次): {e}")
                current_interval = min(self._scan_interval * (1 + self._consecutive_errors), 1800)
            _time.sleep(current_interval)

    def _do_scan(self):
        """执行一次扫描（市场环境自适应 + 策略分类匹配）"""
        self._scan_count += 1
        signals_found = []

        # 1. 获取市场环境
        try:
            from market_regime import market_regime_detector
            regime_result = market_regime_detector.detect()
            regime = regime_result.get("regime", "sideways")
            adjustments = market_regime_detector.get_strategy_adjustments(regime)
        except Exception:
            regime = "sideways"
            adjustments = {"position_factor": 1.0, "confidence_boost": 0,
                           "prefer_categories": [], "avoid_categories": []}

        # 2. 根据环境调整扫描参数
        position_factor = adjustments.get("position_factor", 1.0)
        confidence_boost = adjustments.get("confidence_boost", 0)
        adjusted_max = max(5, int(self._max_per_round * position_factor))
        adjusted_threshold = self._buy_threshold - confidence_boost  # bear时阈值升高更保守

        # 3. 获取策略（环境感知的分类匹配）
        weights = strategy_tracker.get_strategy_weights()
        strategies = []

        if self._strategy_names:
            # 用户指定了策略
            for name in self._strategy_names:
                s = registry.get(name)
                if s:
                    strategies.append(s)
        else:
            # 根据市场环境选择策略（优先匹配 prefer_categories）
            prefer = adjustments.get("prefer_categories", [])
            avoid = adjustments.get("avoid_categories", [])
            scored_strategies = []
            for name in registry.list_names():
                s = registry.get(name)
                if not s:
                    continue
                w = weights.get(name, 0.02)
                # 分类匹配加权
                if s.category in prefer:
                    w *= 1.5
                elif s.category in avoid:
                    w *= 0.3
                scored_strategies.append((name, w, s))
            # 取加权后最高的6个
            scored_strategies.sort(key=lambda x: x[1], reverse=True)
            strategies = [s for _, _, s in scored_strategies[:6]]

        logger.info(
            f"第 {self._scan_count} 轮扫描: {len(self._stocks)} 只股票, "
            f"{len(strategies)} 个策略, regime={regime}, "
            f"阈值={adjusted_threshold:.2f}, max={adjusted_max}"
        )

        for symbol in self._stocks[:adjusted_max]:
            try:
                # 获取实时数据
                stock_data = self._get_stock_data(symbol)
                if not stock_data:
                    continue

                # 计算指标
                indicators = self._get_indicators(symbol)
                history = self._get_history(symbol)

                # 运行策略
                buy_score = 0
                sell_score = 0
                reasons = []
                for s in strategies:
                    try:
                        result = s.analyze(stock_data, indicators, history=history)
                        w = weights.get(s.name, 0.05)
                        if result.signal.value in ("BUY", "STRONG_BUY", "WEAK_BUY"):
                            buy_score += result.score * w
                            reasons.append(f"[{s.name}] {result.signal.value}")
                        elif result.signal.value in ("SELL", "STRONG_SELL", "WEAK_SELL"):
                            sell_score += abs(result.score) * w
                            reasons.append(f"[{s.name}] {result.signal.value}")

                        # 记录信号
                        strategy_tracker.record_signal(
                            s.name, symbol, result.signal.value,
                            result.score, result.confidence, stock_data.get("price", 0)
                        )
                    except Exception:
                        pass

                # 强信号判定（使用动态阈值）
                if buy_score > adjusted_threshold:
                    signals_found.append({
                        "symbol": symbol,
                        "name": stock_data.get("name", symbol),
                        "price": stock_data.get("price", 0),
                        "change_pct": stock_data.get("change_pct", 0),
                        "signal": "BUY",
                        "score": round(buy_score, 3),
                        "reasons": reasons,
                        "time": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%H:%M:%S")
                    })
                elif sell_score > adjusted_threshold:
                    signals_found.append({
                        "symbol": symbol,
                        "name": stock_data.get("name", symbol),
                        "price": stock_data.get("price", 0),
                        "change_pct": stock_data.get("change_pct", 0),
                        "signal": "SELL",
                        "score": round(sell_score, 3),
                        "reasons": reasons,
                        "time": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%H:%M:%S")
                    })

            except Exception as e:
                logger.debug(f"扫描 {symbol} 失败: {e}")

        self._last_signals = signals_found
        if signals_found:
            logger.info(f"发现 {len(signals_found)} 个信号: {', '.join(s['symbol'] + s['signal'] for s in signals_found)}")
            for cb in self._callbacks:
                try:
                    cb(signals_found)
                except Exception:
                    pass
            # 发送通知
            try:
                from notification import notification_manager
                for sig in signals_found:
                    notification_manager.add_signal_notification(sig)
            except Exception:
                pass

        # 自动执行强信号（可选，需 auto_execute=True）
        if self._auto_execute and signals_found:
            self._auto_execute_signals(signals_found)

    def _auto_execute_signals(self, signals: List[Dict]):
        """自动执行强信号（通过 AutoTrader）"""
        from datetime import date as _date
        today = _date.today().isoformat()
        if self._last_trade_date != today:
            self._daily_trade_count = 0
            self._last_trade_date = today

        if self._daily_trade_count >= self._max_daily_auto_trades:
            logger.warning(f"今日自动交易已达上限 ({self._max_daily_auto_trades}次)")
            return

        try:
            from auto_trader import auto_trader
            if not auto_trader.enabled:
                logger.info("AutoTrader 未启用，跳过自动执行")
                return
        except Exception as e:
            logger.error(f"获取 AutoTrader 失败: {e}")
            return

        for sig in signals:
            if self._daily_trade_count >= self._max_daily_auto_trades:
                break
            # 只有高分信号才自动执行
            if sig.get("score", 0) < self._auto_execute_threshold:
                continue
            symbol = sig["symbol"]
            try:
                stock_data = self._get_stock_data(symbol)
                if not stock_data:
                    continue
                indicators = self._get_indicators(symbol)
                result = auto_trader.decide_and_trade(symbol, stock_data, indicators)
                if result.get("trade", {}).get("success"):
                    self._daily_trade_count += 1
                    logger.info(
                        f"自动执行: {sig['signal']} {symbol} "
                        f"(score={sig['score']}, daily={self._daily_trade_count})"
                    )
            except Exception as e:
                logger.debug(f"自动执行 {symbol} 失败: {e}")

    def _get_stock_data(self, symbol: str) -> Dict:
        """获取股票实时数据（带缓存）

        get_realtime_quote(symbol) 返回 dict，键为英文:
        code, name, price, open, high, low, pre_close, change, change_pct, volume, amount, turnover, pe
        """
        try:
            from data_cache import get_realtime_quote
            row = get_realtime_quote(symbol)
            if row is None:
                return None
            # 兼容两种键名格式（英文 dict / 中文 DataFrame row）
            def _get(d, *keys):
                for k in keys:
                    if k in d:
                        return d[k]
                return 0

            return {
                "price": float(_get(row, 'price', '最新价')),
                "change_pct": float(_get(row, 'change_pct', '涨跌幅')),
                "volume": int(_get(row, 'volume', '成交量')),
                "amount": float(_get(row, 'amount', '成交额')),
                "turnover": float(_get(row, 'turnover', '换手率')),
                "open": float(_get(row, 'open', '今开')),
                "high": float(_get(row, 'high', '最高')),
                "low": float(_get(row, 'low', '最低')),
                "pre_close": float(_get(row, 'pre_close', '昨收')),
                "name": _get(row, 'name', '名称', symbol)
            }
        except Exception as e:
            logger.debug(f"获取 {symbol} 实时数据失败: {e}")
            return None

    def _get_indicators(self, symbol: str) -> Dict:
        """获取技术指标（带缓存）"""
        try:
            from data_cache import get_kline, compute_indicators
            kline = get_kline(symbol, period="daily", days=60)
            if not kline or len(kline) < 20:
                return {}
            return compute_indicators(kline)
        except Exception:
            return {}

    def _get_history(self, symbol: str) -> List[Dict]:
        """获取历史K线（带缓存）"""
        try:
            from data_cache import get_kline
            kline = get_kline(symbol, period="daily", days=30)
            return [{"date": d["date"], "close": d["close"], "high": d["high"],
                     "low": d["low"], "volume": d["volume"]} for d in kline]
        except Exception:
            return []

    def get_status(self) -> Dict:
        """获取扫描器状态"""
        return {
            "running": self._running,
            "scan_count": self._scan_count,
            "interval": self._scan_interval,
            "stock_count": len(self._stocks),
            "strategy_count": len(self._strategy_names),
            "last_signals": self._last_signals,
            "regime": self._last_regime,
            "buy_threshold": self._buy_threshold,
            "max_per_round": self._max_per_round,
            "auto_execute": self._auto_execute,
            "auto_execute_threshold": self._auto_execute_threshold,
            "daily_trade_count": self._daily_trade_count,
            "next_scan": f"每 {self._scan_interval // 60} 分钟",
            "consecutive_errors": getattr(self, "_consecutive_errors", 0),
            "last_error": getattr(self, "_last_error", ""),
        }


# 全局扫描器实例
strategy_scanner = StrategyScanner()
