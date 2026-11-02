"""
时序低吸高买、持仓去留动态评估与未卖出超时调控引擎 (Intraday Timing & Position Retention Engine)
========================================================================================
核心职责：
1. 【未买入股票重点追踪与时序低吸 (Timing Dip Buyer)】
   - 对进入候选冲击池但因资金/涨幅暂未买入的股票进行分时微观起伏实时追踪；
   - 计算日内高点、回踩幅度 (Pullback)、分时均价 (VWAP) 及量价企稳信号；
   - 在优质标的冲高回踩 1.5%~3.5% 且企稳时触发低吸买入信号，防止追高被套，实现收益最大化。

2. 【持仓去留动态决策 (Position Retention Decider)】
   - 动态判定现有持仓去留（去/留/减/换）：
     * 强力封死涨停板 -> 坚决留仓（KEEP_LIMIT_UP），享受次日高开溢价；
     * 运行在分时均线上方且多头动能良好 -> 顺势留仓（KEEP_RIDE_TREND）；
     * 冲高乏力并出现分时顶背离 -> 部分减仓锁定利润（TRIM_PROTECT_PROFIT）；
     * 跌破日内分时均线 (VWAP) 超过 1.5% -> 坚决去仓换股（SELL_WEAK_EXIT）；
     * 触及硬止损线 (-4.5%) -> 紧急止损（EMERGENCY_STOP_LOSS）。

3. 【未卖出预期时间合理调控 (Unsold Position Controller)】
   - 对计划卖出但超过预期时间仍未卖出的标的进行智能调控：
     * 若因股价超预期继续爆拉至涨停 -> 顺势取消卖单，转为持仓享受主升；
     * 若因挂单过高导致未成交且盘口卖压增大 -> 立即降价至买一或市价追单成交，杜绝利润回吐；
     * 若因流动性不足挂单挂死 -> 动态调控为分批出局。

4. 【多维度详细交易数据与多模块跨权值融合体系】
   - 暴露未买入与未卖出的详细时序指标数据供前端大屏和调度器决策消费；
   - 权重矩阵：多策略表决 (0.35) + 时序低吸 (0.25) + 新闻舆情 (0.20) + 持仓去留调控 (0.20)。
"""

import time
import logging
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime
from collections import deque

logger = logging.getLogger("IntradayTimingEngine")


# ===== 模块级阈值常量 (M1: 魔法数字配置化) =====
DIP_SCORE_THRESHOLD = 70.0        # 时序低吸最低动量评分门槛
PULLBACK_MIN = 1.5                # 健康回踩幅度下限 (百分点, 与 pullback_pct 同量纲)
PULLBACK_MAX = 3.8                # 健康回踩幅度上限 (百分点)
OBI_BULLISH = 0.1                 # 订单簿不平衡看多阈值
OBI_BEARISH = -0.2                # 订单簿不平衡看空阈值
LIMIT_UP_PCT = 9.8                # 涨停判定涨幅 (%)
# 盘中硬止损阈值: 与 risk_manager 主板 min_fixed_pct(0.045) 对齐
# TODO: 接入 risk_manager.calculate_stop_loss 动态阈值（按主板/双创差异化 + ATR 自适应）
HARD_STOP_LOSS = -0.045
WEIGHT_RETAIL = 0.35              # 多策略量化加权表决权重
WEIGHT_TECH = 0.25                # 时序低吸买入权重
WEIGHT_SENTIMENT = 0.20           # 新闻舆情与突发事件权重
WEIGHT_TIMING = 0.20              # 持仓去留与超时调控权重


class TimingDipBuyer:
    """未买入候选股票重点追踪与时序低吸模块"""

    def __init__(self):
        # 记录未买入重点关注股票字典：symbol -> {info, first_seen, ticks, target_price, reason}
        self.watchlist: Dict[str, Dict[str, Any]] = {}
        # 限制每只标的保留最近 120 个快照（约 2-5 分钟分时微结构）
        self.max_ticks = 120

    def register_unbought_candidate(self, symbol: str, name: str, score: float, 
                                     reason: str = "资金受限/等待回踩", 
                                     target_price: float = 0.0,
                                     industry: str = "",
                                     initial_price: float = 0.0,
                                     now: Optional[datetime] = None):
        """登记或更新未买入重点关注标的（支持 M8: now 回测时钟注入）"""
        now_ts = now.timestamp() if now else time.time()
        init_p = float(initial_price or target_price or 0.0)
        if symbol not in self.watchlist:
            self.watchlist[symbol] = {
                "symbol": symbol,
                "name": name,
                "score": score,
                "reason": reason,
                "target_price": target_price,
                "initial_price": init_p,
                "missed_gain_pct": 0.0,
                "obi": 0.0,
                "volume_ratio": 1.0,
                "turnover_ratio": 0.0,
                "industry": industry,
                "registered_at": now_ts,
                "ticks": deque(maxlen=self.max_ticks),
                "high_price": 0.0,
                "low_price": 999999.0,
                "vwap": 0.0,
                "last_price": 0.0,
                "last_change_pct": 0.0,
                "dip_score": 0.0,
                "status": "WATCHING"
            }
        else:
            self.watchlist[symbol]["score"] = max(self.watchlist[symbol]["score"], score)
            self.watchlist[symbol]["reason"] = reason
            if target_price > 0:
                self.watchlist[symbol]["target_price"] = target_price
            if init_p > 0 and self.watchlist[symbol].get("initial_price", 0.0) <= 0:
                self.watchlist[symbol]["initial_price"] = init_p

    def update_quote(self, symbol: str, current_price: float, change_pct: float, 
                     high: float = 0.0, low: float = 0.0, volume: float = 0.0,
                     quote: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None, **kwargs) -> Dict[str, Any]:
        """更新个股即时行情并计算时序微观低吸信号 (T9: 涨跌幅安全区间校验 [-7.0%, +5.0%], M8: now 回测时钟注入)"""
        try:
            if symbol not in self.watchlist:
                return {}

            item = self.watchlist[symbol]
            now_ts = now.timestamp() if now else time.time()
            
            # 补全初始登记价格
            if item.get("initial_price", 0.0) <= 0:
                item["initial_price"] = item.get("target_price") or current_price

            # 动态更新高低点与移动均线
            eff_high = max(high, item["high_price"], current_price)
            eff_low = min(low if low > 0 else current_price, item["low_price"], current_price)
            item["high_price"] = eff_high
            item["low_price"] = eff_low
            item["last_price"] = current_price
            item["last_change_pct"] = change_pct

            # 订单簿不平衡度 (Order Book Imbalance, OBI) 与量比换手计算
            q = quote or {}
            bid1_vol = float(q.get("bid1_vol") or kwargs.get("bid1_vol", 0.0) or 0.0)
            ask1_vol = float(q.get("ask1_vol") or kwargs.get("ask1_vol", 0.0) or 0.0)
            tot_depth = bid1_vol + ask1_vol
            obi = round((bid1_vol - ask1_vol) / tot_depth, 4) if tot_depth > 0 else 0.0
            item["obi"] = obi

            vol_ratio = float(q.get("volume_ratio") or kwargs.get("volume_ratio", 1.0) or 1.0)
            turnover_ratio = float(q.get("turnover_ratio") or kwargs.get("turnover_ratio", 0.0) or 0.0)
            item["volume_ratio"] = vol_ratio
            item["turnover_ratio"] = turnover_ratio

            # 统计错过的最大涨幅 (Missed Gain %)
            init_p = item.get("initial_price", 0.0)
            missed_gain_pct = 0.0
            if init_p > 0 and eff_high > init_p:
                missed_gain_pct = round((eff_high - init_p) / init_p * 100.0, 2)
            item["missed_gain_pct"] = missed_gain_pct

            # 维护有界双端队列 (RingBuffer)
            item["ticks"].append((now_ts, current_price, volume, obi))

            # 计算日内分时均价 (VWAP 估算)
            if len(item["ticks"]) > 0:
                prices = [t[1] for t in item["ticks"]]
                item["vwap"] = round(sum(prices) / len(prices), 2)
            else:
                item["vwap"] = current_price

            # 计算自日内高点回踩幅度 (Pullback %)
            pullback_pct = 0.0
            if eff_high > 0:
                pullback_pct = round((eff_high - current_price) / eff_high * 100.0, 2)

            # 核心算法：时序低吸买入评分 (Dip Buy Score)
            # 理想低吸模型：
            # 1. 股票具有强动量基础 (score >= DIP_SCORE_THRESHOLD)
            # 2. 早盘冲高后发生健康回踩，回踩幅度在 PULLBACK_MIN ~ PULLBACK_MAX 之间
            # 3. 价格未跌破日内分时均线 (price >= vwap * 0.995)，表明有大资金托底
            # 4. 买一支撑坚挺 (OBI > OBI_BEARISH)
            dip_score = 0.0
            signal = "WATCHING"
            suggested_action = "CONTINUE_OBSERVE"

            if item["score"] >= DIP_SCORE_THRESHOLD:
                # ===== T9 (低吸风控边界保护)：严格校验涨跌幅安全区间 [-7.0%, +5.0%] =====
                if change_pct > 5.0:
                    # 日内冲高幅度过大 (>5.0%)，判定为追高风险，拒绝低吸买入
                    dip_score = 45.0
                    signal = "OVERBOUGHT_HIGH"
                    suggested_action = "REJECT_CHASE_HIGH"
                elif change_pct < -7.0:
                    # 重挫破位形态 (<-7.0%)，判定为严重下行破位，拒绝盲目抄底接飞刀
                    dip_score = 30.0
                    signal = "AVOID_DEEP_FALL"
                    suggested_action = "REJECT_FALLING_KNIFE"
                elif PULLBACK_MIN <= pullback_pct <= PULLBACK_MAX and current_price >= item["vwap"] * 0.995:
                    # 仅在 -7.0% <= change_pct <= 5.0% 且满足回踩与支撑条件时才触发买入
                    obi_bonus = 5.0 if obi > OBI_BULLISH else (0.0 if obi >= OBI_BEARISH else -5.0)
                    dip_score = round(item["score"] * 0.5 + 45.0 + (PULLBACK_MAX - pullback_pct) * 5.0 + obi_bonus, 1)
                    signal = "TIMING_DIP_BUY"
                    suggested_action = "EXECUTE_DIP_BUY"
                elif pullback_pct > 4.5:
                    # 回调过深，可能演变为瀑布跳水，降低评分
                    dip_score = 40.0
                    signal = "AVOID_DEEP_FALL"
                    suggested_action = "WAIT_FOR_STABILIZE"
                elif pullback_pct < 1.0 and change_pct > 3.0:
                    # 处于冲高顶部，追高风险大
                    dip_score = 50.0
                    signal = "OVERBOUGHT_HIGH"
                    suggested_action = "WAIT_FOR_PULLBACK"
                else:
                    dip_score = 60.0
                    signal = "NORMAL_FLUCTUATION"
                    suggested_action = "OBSERVE"
            else:
                dip_score = 45.0

            item["dip_score"] = dip_score
            item["pullback_pct"] = pullback_pct
            item["status"] = signal
            item["suggested_action"] = suggested_action

            return {
                "symbol": symbol,
                "name": item["name"],
                "current_price": current_price,
                "high_price": eff_high,
                "vwap": item["vwap"],
                "pullback_pct": pullback_pct,
                "initial_price": init_p,
                "missed_gain_pct": missed_gain_pct,
                "obi": obi,
                "volume_ratio": vol_ratio,
                "turnover_ratio": turnover_ratio,
                "dip_score": dip_score,
                "signal": signal,
                "suggested_action": suggested_action
            }
        except (ValueError, TypeError, KeyError, ZeroDivisionError) as e:
            logger.warning(f"[update_quote] 标的 {symbol} 行情更新异常，跳过该标的: {e}")
            return {}

    def evaluate_dip_candidate(self, symbol: str, current_price: float, change_pct: float,
                               high: float = 0.0, low: float = 0.0, volume: float = 0.0,
                               quote: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None,
                               **kwargs) -> Dict[str, Any]:
        """
        评估低吸标的资格 (T9: 严格校验涨跌幅安全区间 [-7.0%, +5.0%], M8: 支持 now 注入)
        """
        return self.update_quote(symbol, current_price, change_pct, high=high, low=low, volume=volume, quote=quote, now=now, **kwargs)

    update_quote_and_check = update_quote

    def get_actionable_dip_candidates(self, min_dip_score: float = 75.0) -> List[Dict[str, Any]]:
        """提取已达到时序低吸买入标准的标的列表"""
        actionable = []
        for symbol, item in self.watchlist.items():
            if item.get("dip_score", 0) >= min_dip_score and item.get("status") == "TIMING_DIP_BUY":
                actionable.append(dict(item))
        # 按照低吸评分由高到低排序
        actionable.sort(key=lambda x: x.get("dip_score", 0), reverse=True)
        return actionable

    def get_summary_list(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """获取所有未买入追踪标的详细结构（支持 M8: now 回测时钟注入）"""
        res = []
        now_ts = now.timestamp() if now else time.time()
        for symbol, item in self.watchlist.items():
            res.append({
                "symbol": symbol,
                "name": item.get("name", ""),
                "score": item.get("score", 0),
                "industry": item.get("industry", ""),
                "reason": item.get("reason", "等待低吸"),
                "tracked_minutes": round((now_ts - item.get("registered_at", now_ts)) / 60.0, 1),
                "initial_price": item.get("initial_price", 0.0),
                "last_price": item.get("last_price", 0.0),
                "high_price": item.get("high_price", 0.0),
                "vwap": item.get("vwap", 0.0),
                "pullback_pct": item.get("pullback_pct", 0.0),
                "missed_gain_pct": item.get("missed_gain_pct", 0.0),
                "obi": item.get("obi", 0.0),
                "volume_ratio": item.get("volume_ratio", 1.0),
                "turnover_ratio": item.get("turnover_ratio", 0.0),
                "dip_score": item.get("dip_score", 0.0),
                "status": item.get("status", "WATCHING"),
                "suggested_action": item.get("suggested_action", "OBSERVE"),
                "utilization_weight": WEIGHT_TECH
            })
        return res


class PositionRetentionDecider:
    """当前持仓去留动态评估模块（去/留/减/换）"""

    def __init__(self):
        pass

    def evaluate_retention(self, position: Dict[str, Any], quote: Dict[str, Any], 
                           news_sentiment_score: float = 50.0, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        全要素评估现有持仓去留决策 (支持成本价三级兜底与新闻舆情权重修正)
        :param position: 当前账户持仓数据 (symbol, amount, cost_price, avg_price, total_cost, profit_rate, held_days, etc.)
        :param quote: 实时盘口数据 (price, change_pct, high, low, bid1_vol, ask1_vol, vwap)
        :param news_sentiment_score: 舆情情绪评分 (0~100，默认50中性)
        :return: 决策结果字典 (action: KEEP/TRIM/SELL/STOP, reason, confidence, urgency)
        """
        try:
            symbol = position.get("symbol", position.get("code", ""))
            name = position.get("name", symbol)
            
            # 成本价三级兜底计算：cost_price -> avg_price -> total_cost / amount，防止 profit_rate 被误算为 0.0
            amt = float(position.get("amount", 0) or position.get("total_amount", 0) or 0)
            total_cost = float(position.get("total_cost", 0.0) or 0.0)
            calc_cost = (total_cost / amt) if amt > 0 else 0.0
            cost_price = float(position.get("cost_price") or position.get("avg_price") or calc_cost or 0.0)

            current_price = float(quote.get("price", 0.0) or (cost_price if cost_price > 0 else 0.0))
            change_pct = float(quote.get("change_pct", 0.0) or 0.0)
            
            profit_rate = 0.0
            if cost_price > 0:
                profit_rate = (current_price - cost_price) / cost_price
            else:
                profit_rate = float(position.get("profit_rate", 0.0) or 0.0)

            high_price = float(quote.get("high", 0.0) or current_price)
            vwap = float(quote.get("vwap", 0.0) or current_price)
            ask1_vol = float(quote.get("ask1_vol", 0.0) or 0.0)
            sentiment = float(news_sentiment_score if news_sentiment_score is not None else 50.0)

            # 1. 涨停板判定：封单充足，坚决【留】
            if change_pct >= LIMIT_UP_PCT and ask1_vol == 0:
                return {
                    "symbol": symbol,
                    "name": name,
                    "action": "KEEP_LIMIT_UP",
                    "retention_decision": "留 (涨停封单)",
                    "confidence": 0.95,
                    "urgency": "LOW",
                    "reason": f"股票强势封板 (+{change_pct:.2f}%)，卖一封死，坚决持仓赚取次日高开溢价",
                    "profit_rate": profit_rate,
                    "cost_price": cost_price,
                    "news_sentiment_score": sentiment
                }

            # 2. 舆情恶化联动判定：情绪分低于30且持仓浮亏，下调止损容忍度，提前触发清仓避险
            if sentiment < 30.0 and profit_rate < 0:
                loss_pct = profit_rate * 100.0
                if profit_rate <= -0.018 or current_price < vwap * 0.99:
                    return {
                        "symbol": symbol,
                        "name": name,
                        "action": "SELL_WEAK_EXIT",
                        "retention_decision": "去 (舆情恶化避险)",
                        "confidence": 0.92,
                        "urgency": "HIGH",
                        "reason": f"舆情极度悲观({sentiment:.0f}<30)且持仓浮亏({loss_pct:.2f}%)，下调止损阈值触发清仓避险",
                        "profit_rate": profit_rate,
                        "cost_price": cost_price,
                        "news_sentiment_score": sentiment
                    }

            # 3. 硬风控止损红线：触及 HARD_STOP_LOSS 坚决【去】
            # TODO: 接入 risk_manager.calculate_stop_loss 动态阈值（主板 4.5%~6.5% / 双创 6.5%~8.5%）
            if profit_rate <= HARD_STOP_LOSS:
                return {
                    "symbol": symbol,
                    "name": name,
                    "action": "EMERGENCY_STOP_LOSS",
                    "retention_decision": "去 (硬止损)",
                    "confidence": 0.99,
                    "urgency": "HIGH",
                    "reason": f"触及硬止损阈值 ({profit_rate*100:.2f}% <= {HARD_STOP_LOSS*100:.1f}%)，铁律清仓防大回撤",
                    "profit_rate": profit_rate,
                    "cost_price": cost_price,
                    "news_sentiment_score": sentiment
                }

            # 4. 冲高受阻回落保利判定：曾经大赚但从日内高点回落过深，部分【减】
            pullback_from_high = 0.0
            if high_price > 0:
                pullback_from_high = (high_price - current_price) / high_price * 100.0

            if profit_rate >= 0.035 and pullback_from_high >= 2.5:
                return {
                    "symbol": symbol,
                    "name": name,
                    "action": "TRIM_PROTECT_PROFIT",
                    "retention_decision": "减 (冲高落袋)",
                    "confidence": 0.85,
                    "urgency": "MEDIUM",
                    "reason": f"日内从高位回落 {pullback_from_high:.1f}%，盈利丰厚 (+{profit_rate*100:.1f}%)，建议减仓锁定战果",
                    "profit_rate": profit_rate,
                    "cost_price": cost_price,
                    "news_sentiment_score": sentiment
                }

            # 5. 破位与均线背离判定：跌破日内分时均线且翻绿，【去】仓换牛股
            if vwap > 0 and current_price < vwap * 0.982 and change_pct < 0:
                return {
                    "symbol": symbol,
                    "name": name,
                    "action": "SELL_WEAK_EXIT",
                    "retention_decision": "去 (破位换仓)",
                    "confidence": 0.82,
                    "urgency": "MEDIUM",
                    "reason": f"价格跌破分时均线 1.8% 且处于弱势区间，建议出局腾挪仓位配置时序低吸标的",
                    "profit_rate": profit_rate,
                    "cost_price": cost_price,
                    "news_sentiment_score": sentiment
                }

            # 6. 多头趋势良好，站在分时均线上方：顺势【留】 (若舆情>70显著增强多头信心)
            if current_price >= vwap:
                boost_conf = 0.88 if sentiment > 70.0 else 0.78
                sentiment_note = f"，舆情积极({sentiment:.0f}>70)增强持股信心" if sentiment > 70.0 else ""
                return {
                    "symbol": symbol,
                    "name": name,
                    "action": "KEEP_RIDE_TREND",
                    "retention_decision": "留 (多头顺势)",
                    "confidence": boost_conf,
                    "urgency": "LOW",
                    "reason": f"运行于日内分时均线上方 (现价 {current_price:.2f} >= VWAP {vwap:.2f})，多头动能充沛{sentiment_note}，持股待涨",
                    "profit_rate": profit_rate,
                    "cost_price": cost_price,
                    "news_sentiment_score": sentiment
                }

            # 默认中性持有
            return {
                "symbol": symbol,
                "name": name,
                "action": "KEEP_OBSERVE",
                "retention_decision": "留 (震荡观察)",
                "confidence": 0.60,
                "urgency": "LOW",
                "reason": "处于正常分时震荡箱体内，未触发离场信号，继续持仓观察",
                "profit_rate": profit_rate,
                "cost_price": cost_price,
                "news_sentiment_score": sentiment
            }
        except (ValueError, TypeError, KeyError, ZeroDivisionError) as e:
            logger.warning(f"[evaluate_retention] 持仓去留评估数据异常，降级持有: {e}")
            return {"action": "HOLD", "reason": "数据异常降级持有"}


class UnsoldPositionController:
    """未卖出预期时间合理调控模块 (Unsold Position Controller) - 支持多账户与订单维度 (account_id, symbol, order_id) 复合键严格隔离与防覆盖"""

    def __init__(self):
        # 跟踪预期卖出指令：(account_id, symbol, order_id) -> {expected_sell_time, target_price, reason, order_id, register_time}
        self.pending_sells: Dict[Any, Dict[str, Any]] = {}

    def register_expected_sell(self, symbol: str, name: str, expected_sell_time_str: str, 
                               target_price: float, reason: str = "时序计划卖出", order_id: str = "", account_id: int = 1,
                               now: Optional[datetime] = None):
        """登记一笔有预期时间的卖出计划（采用 (account_id, symbol, order_id) 复合键隔离，防止多笔分批挂单相互覆盖，支持 now 回测时钟注入）"""
        acc_id = int(account_id) if account_id is not None else 1
        sym = str(symbol).strip()
        ord_id = str(order_id).strip() if order_id is not None else ""
        if not ord_id:
            count = sum(1 for k in self.pending_sells if (isinstance(k, tuple) and len(k) >= 2 and k[0] == acc_id and k[1] == sym))
            ord_id = f"auto_{count + 1}_{int(time.time()*1000)}"

        comp_key = (acc_id, sym, ord_id)
        now_ts = now.timestamp() if now else time.time()
        self.pending_sells[comp_key] = {
            "key": f"{acc_id}:{sym}:{ord_id}",
            "composite_key": comp_key,
            "account_id": acc_id,
            "symbol": sym,
            "name": name,
            "expected_sell_time": expected_sell_time_str,
            "target_price": target_price,
            "reason": reason,
            "order_id": ord_id,
            "registered_at": now_ts,
            "status": "AWAITING_TIME",
            "tuning_action": "NONE"
        }
        return comp_key

    def cancel_expected_sell(self, symbol: str, account_id: Optional[int] = None, order_id: Optional[str] = None) -> bool:
        """撤销预期卖出计划（支持指定 order_id、account_id 或全量匹配标的撤销）"""
        sym = str(symbol).strip()
        target_ord = str(order_id).strip() if order_id is not None and str(order_id).strip() != "" else None
        removed = False

        keys_to_del = []
        for k, item in list(self.pending_sells.items()):
            # 校验 symbol
            k_sym = item.get("symbol", "")
            if not k_sym:
                if isinstance(k, tuple) and len(k) >= 2:
                    k_sym = str(k[1])
                elif isinstance(k, str) and ":" in k:
                    parts = k.split(":")
                    k_sym = parts[1] if len(parts) > 1 else parts[0]
                else:
                    k_sym = str(k)
            if k_sym != sym:
                continue

            # 校验 account_id
            if account_id is not None:
                item_acc = item.get("account_id")
                if item_acc is None and isinstance(k, tuple) and len(k) >= 1:
                    item_acc = k[0]
                if item_acc is not None and int(item_acc) != int(account_id):
                    continue

            # 校验 order_id
            if target_ord is not None:
                item_ord = str(item.get("order_id", ""))
                k_ord = str(k[2]) if isinstance(k, tuple) and len(k) >= 3 else ""
                if item_ord != target_ord and k_ord != target_ord:
                    continue

            keys_to_del.append(k)

        for k in keys_to_del:
            if k in self.pending_sells:
                del self.pending_sells[k]
                removed = True

        return removed

    def evaluate_unsold_status(self, symbol: str, current_price: float, change_pct: float,
                               now_time_str: Optional[str] = None, account_id: Optional[int] = None,
                               order_id: Optional[str] = None,
                               quote: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None, **kwargs) -> Optional[Dict[str, Any]]:
        """
        对已超预期时间却未卖出的标的进行智能调控 (记录滑点损失率与买一支撑占比)
        调控逻辑：
        1. 若价格反而拉升至涨停 -> 调控：撤销卖出计划，转为继续持有；
        2. 若已经超时且价格开始滞涨/跳水 -> 调控：立刻降价至即时买一或市价急卖，坚决止盈/避险；
        3. 若尚未到达预期时间 -> 调控：维持挂单等待时机。
        """
        try:
            sym = str(symbol).strip()
            target_ord = str(order_id).strip() if order_id is not None and str(order_id).strip() != "" else kwargs.get("order_id")
            target_item = None

            # 1. 精确匹配复合键 (account_id, symbol, order_id)
            if account_id is not None and target_ord:
                exact_key = (int(account_id), sym, str(target_ord))
                target_item = self.pending_sells.get(exact_key)
                if not target_item:
                    target_item = self.pending_sells.get(f"{account_id}:{sym}:{target_ord}")

            # 2. 遍历查找匹配项
            if not target_item:
                for k, val in self.pending_sells.items():
                    k_sym = val.get("symbol") or (k[1] if isinstance(k, tuple) and len(k) >= 2 else str(k))
                    if k_sym != sym:
                        continue
                    if account_id is not None:
                        k_acc = val.get("account_id") or (k[0] if isinstance(k, tuple) and len(k) >= 1 else None)
                        if k_acc is not None and int(k_acc) != int(account_id):
                            continue
                    if target_ord:
                        k_ord = val.get("order_id") or (k[2] if isinstance(k, tuple) and len(k) >= 3 else "")
                        if str(k_ord) != str(target_ord):
                            continue
                    # 优先挑选尚未处理完成/处于等待或超时的单据
                    if val.get("status") not in ("COMPLETED", "EXECUTED"):
                        target_item = val
                        break
                    if not target_item:
                        target_item = val

            if not target_item:
                return {}

            item = target_item

            # 状态机门禁：若该条目已处于调价/市价卖出已下发状态，直接跳过，杜绝幽灵重复下单
            if item.get("status") in ("REPRICING_ORDERED", "COMPLETED", "EXECUTED"):
                return {
                    "account_id": item.get("account_id", account_id or 1),
                    "symbol": sym,
                    "name": item.get("name", sym),
                    "order_id": item.get("order_id", ""),
                    "status": item.get("status"),
                    "tuning_action": "ALREADY_ORDERED",
                    "tuning_reason": f"该标的已下发 {item.get('status')} 指令，跳过重复触发",
                    "skip": True
                }

            acc_id = item.get("account_id", account_id or 1)
            if not now_time_str:
                now_time_str = (now or datetime.now()).strftime("%H:%M:%S")

            # 滑点损失率计算 (Slippage %): 相对目标卖出价的滑点
            target_p = float(item.get("target_price", 0.0) or 0.0)
            slippage_pct = 0.0
            if target_p > 0 and current_price < target_p:
                slippage_pct = round((target_p - current_price) / target_p * 100.0, 2)
            item["slippage_pct"] = slippage_pct

            # 买一盘口支撑度计算 (Bid1 Support Ratio)
            q = quote or {}
            bid1_vol = float(q.get("bid1_vol") or kwargs.get("bid1_vol", 0.0) or 0.0)
            ask1_vol = float(q.get("ask1_vol") or kwargs.get("ask1_vol", 0.0) or 0.0)
            tot_depth = bid1_vol + ask1_vol
            bid1_support_ratio = round(bid1_vol / tot_depth, 4) if tot_depth > 0 else 0.5
            item["bid1_support_ratio"] = bid1_support_ratio

            is_overdue = now_time_str > item["expected_sell_time"]
            tuning_action = "KEEP_PENDING"
            tuning_reason = "尚未到达预期卖出时钟"
            status = "PENDING"

            if is_overdue:
                status = "OVERDUE_UNSOLD"
                # 检查市场表现
                if change_pct >= LIMIT_UP_PCT:
                    # 市场超预期强劲，调控为顺延持有
                    tuning_action = "CANCEL_AND_RIDE"
                    tuning_reason = f"[账户#{acc_id}] 已过预定卖出时间 {item['expected_sell_time']}，但股价封死涨停，调控为取消卖出继续享受涨幅"
                elif current_price < target_p * 0.985 or (bid1_support_ratio < 0.3 and slippage_pct > 1.0):
                    # 价格已跌破目标价 1.5% 或买一支撑崩塌，属于挂单过高未成交，必须紧急调价
                    tuning_action = "REPRICE_URGENT_SELL"
                    tuning_reason = f"[账户#{acc_id}] 已超时且价格跌破挂单价 ({current_price:.2f} < {target_p:.2f}, 滑点 {slippage_pct:.1f}%)，调控为按买一市价紧急卖出防滑坡"
                else:
                    # 正常超时，调整为分批现价卖出
                    tuning_action = "MARKET_SELL_NOW"
                    tuning_reason = f"[账户#{acc_id}] 已超过预定卖出窗口 {item['expected_sell_time']}，执行纪律性市价调仓"
            else:
                status = "AWAITING_TIME"
                tuning_action = "WAIT_SCHEDULED_TIME"
                tuning_reason = f"[账户#{acc_id}] 等待到达预期卖出时间 {item['expected_sell_time']}"

            item["tuning_action"] = tuning_action
            item["tuning_reason"] = tuning_reason

            # 状态机：一旦下发调价/市价卖出指令，立即标记为 REPRICING_ORDERED 并记录时间戳，
            # 后续 tick 命中状态门禁直接跳过，杜绝 30s 后幽灵重复下单
            if tuning_action in ("REPRICE_URGENT_SELL", "MARKET_SELL_NOW"):
                status = "REPRICING_ORDERED"
                item["ordered_time"] = time.time()

            item["status"] = status

            return {
                "account_id": acc_id,
                "symbol": sym,
                "name": item.get("name", sym),
                "expected_sell_time": item["expected_sell_time"],
                "target_price": target_p,
                "current_price": current_price,
                "slippage_pct": slippage_pct,
                "bid1_support_ratio": bid1_support_ratio,
                "is_overdue": is_overdue,
                "status": status,
                "tuning_action": tuning_action,
                "tuning_reason": tuning_reason,
                "utilization_weight": WEIGHT_TIMING
            }
        except (ValueError, TypeError, KeyError, ZeroDivisionError) as e:
            logger.warning(f"[evaluate_unsold_status] 未卖出调控评估异常，跳过: {e}")
            return None

    def get_unsold_summary_list(self, account_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """获取未卖出超时调控清单（支持按 account_id 过滤）"""
        res = []
        for key, item in self.pending_sells.items():
            if account_id is not None:
                item_acc = item.get("account_id")
                if item_acc is not None and int(item_acc) != int(account_id):
                    continue
            res.append(dict(item))
        return res


# 全局单例引擎实例
intraday_timing_engine = {
    "dip_buyer": TimingDipBuyer(),
    "retention_decider": PositionRetentionDecider(),
    "unsold_controller": UnsoldPositionController(),
    "weights": {
        "ensemble_rule_weight": WEIGHT_RETAIL,      # 多策略量化加权表决基础权重
        "timing_dip_weight": WEIGHT_TECH,           # 时序低吸买入权重 (未买入起伏关注)
        "news_sentiment_weight": WEIGHT_SENTIMENT,  # 新闻舆情与突发事件权重
        "retention_control_weight": WEIGHT_TIMING   # 持仓去留与超时调控权重
    }
}


def get_detailed_timing_metrics(account_id: Optional[int] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    全局时序微观量化指标汇聚输出 (支持 M8: now 回测时钟注入)
    涵盖未买入低吸监控、未卖出超时调控、订单簿不平衡(OBI)、错过涨幅及滑点统计
    """
    dip_buyer: TimingDipBuyer = intraday_timing_engine["dip_buyer"]
    unsold_ctrl: UnsoldPositionController = intraday_timing_engine["unsold_controller"]

    unbought_summary = dip_buyer.get_summary_list(now=now)
    actionable_dips = dip_buyer.get_actionable_dip_candidates()
    unsold_summary = unsold_ctrl.get_unsold_summary_list(account_id=account_id)

    total_unbought = len(unbought_summary)
    actionable_count = len(actionable_dips)
    total_unsold = len(unsold_summary)
    overdue_count = sum(1 for x in unsold_summary if x.get("status") == "OVERDUE_UNSOLD")

    avg_pullback = round(sum(x.get("pullback_pct", 0.0) for x in unbought_summary) / total_unbought, 2) if total_unbought > 0 else 0.0
    avg_missed = round(sum(x.get("missed_gain_pct", 0.0) for x in unbought_summary) / total_unbought, 2) if total_unbought > 0 else 0.0
    avg_slippage = round(sum(x.get("slippage_pct", 0.0) for x in unsold_summary) / total_unsold, 2) if total_unsold > 0 else 0.0

    return {
        "status": "success",
        "timestamp": (now or datetime.now()).isoformat(),
        "account_id": account_id,
        "statistics": {
            "total_tracked_unbought": total_unbought,
            "actionable_dip_count": actionable_count,
            "total_pending_sells": total_unsold,
            "overdue_unsold_count": overdue_count,
            "avg_pullback_pct": avg_pullback,
            "avg_missed_gain_pct": avg_missed,
            "avg_slippage_pct": avg_slippage,
        },
        "unbought_candidates": unbought_summary,
        "actionable_dips": actionable_dips,
        "unsold_positions": unsold_summary,
        "weights": intraday_timing_engine.get("weights", {})
    }
