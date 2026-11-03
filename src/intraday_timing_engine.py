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
import threading
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


# ===== 模块级通用函数 (方案 7: 统一时间秒数解析) =====
def _parse_time_sec(t_str: str) -> int:
    """
    健壮的秒数解析函数 (方案 7: 统一时间秒数解析，彻底杜绝缺少前导零导致的字典序误判)
    支持 'HH:MM:SS' 与 'HH:MM' 格式
    """
    try:
        parts = [int(p) for p in str(t_str).strip().split(":")]
        if len(parts) == 2:
            parts.append(0)
        if len(parts) == 3 and all(0 <= p < 60 for p in parts[1:]) and 0 <= parts[0] < 24:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    except (ValueError, TypeError):
        pass
    return 0


class TimingDipBuyer:
    """未买入候选股票重点追踪与时序低吸模块 (方案 1: 读写锁与细粒度锁优化, 方案 5: 治理哨兵值)"""

    def __init__(self):
        # 记录未买入重点关注股票字典：symbol -> {info, first_seen, ticks, target_price, reason}
        self.watchlist: Dict[str, Dict[str, Any]] = {}
        # 限制每只标的保留最近 120 个快照（约 2-5 分钟分时微结构）
        self.max_ticks = 120
        # 方案 1: 引入并发重入读写锁
        self._lock = threading.RLock()

    def register_unbought_candidate(self, symbol: str, name: str, score: float, 
                                     reason: str = "资金受限/等待回踩", 
                                     target_price: float = 0.0,
                                     industry: str = "",
                                     initial_price: float = 0.0,
                                     now: Optional[datetime] = None):
        """登记或更新未买入重点关注标的（支持 M8: now 回测时钟注入，方案1: 锁保护，方案5: 自适应真实价格初始化）"""
        now_ts = now.timestamp() if now else time.time()
        init_p = float(initial_price or target_price or 0.0)
        with self._lock:
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
                    "high_price": init_p,
                    "low_price": init_p,
                    "vwap": init_p,
                    "last_price": init_p,
                    "last_change_pct": 0.0,
                    "dip_score": 0.0,
                    "status": "WATCHING"
                }
            else:
                target = self.watchlist[symbol]
                target["score"] = max(target["score"], score)
                target["reason"] = reason
                if target_price > 0:
                    target["target_price"] = target_price
                if init_p > 0 and target.get("initial_price", 0.0) <= 0:
                    target["initial_price"] = init_p
                if init_p > 0:
                    prev_h = float(target.get("high_price", 0.0) or 0.0)
                    prev_l = float(target.get("low_price", 0.0) or 0.0)
                    target["high_price"] = max(init_p, prev_h) if prev_h > 0 else init_p
                    target["low_price"] = min(init_p, prev_l) if prev_l > 0 else init_p
        _publish_and_persist_intraday_event("register_unbought", {"symbol": symbol, "score": score})

    def _ensure_metrics_ready(self, symbol: str, current_price: float, change_pct: float,
                              high: float = 0.0, low: float = 0.0, volume: float = 0.0,
                              quote: Optional[Dict[str, Any]] = None,
                              item_snapshot: Optional[Dict[str, Any]] = None,
                              now_ts: float = 0.0, **kwargs) -> Dict[str, Any]:
        """
        耗时的指标平滑与列表运算在锁外执行 (方案 1: 核心锁粒度原则)
        方案 5: 自适应动态更新 real high / low，彻底废除 999999.0 哨兵值
        """
        item = item_snapshot or {}
        cur_p = current_price

        # 方案 5: 真实价格初始化与动态更新，杜绝 999999.0
        cur_high = max(cur_p, high) if high > 0 else cur_p
        cur_low = min(cur_p, low) if low > 0 else cur_p

        prev_high = float(item.get("high_price") or 0.0)
        prev_low = float(item.get("low_price") or 0.0)

        eff_high = max(cur_high, prev_high) if prev_high > 0 else cur_high
        eff_low = min(cur_low, prev_low) if prev_low > 0 else cur_low

        init_p = float(item.get("initial_price", 0.0) or item.get("target_price", 0.0) or cur_p)

        # 订单簿不平衡度 (OBI)、五档深度与跨帧订单流失衡 (VOI) 计算
        q = quote or {}
        bid_vols = q.get("bid_vols") or []
        ask_vols = q.get("ask_vols") or []
        bid1_vol = float(q.get("bid1_vol") or (bid_vols[0] if bid_vols else 0.0) or kwargs.get("bid1_vol", 0.0) or 0.0)
        ask1_vol = float(q.get("ask1_vol") or (ask_vols[0] if ask_vols else 0.0) or kwargs.get("ask1_vol", 0.0) or 0.0)
        bid1_p = float(q.get("bid1_price") or kwargs.get("bid1_price", 0.0) or round(cur_p - 0.01, 2))
        ask1_p = float(q.get("ask1_price") or kwargs.get("ask1_price", 0.0) or round(cur_p + 0.01, 2))
        tot_b_depth = float(sum(bid_vols)) if bid_vols else bid1_vol
        tot_a_depth = float(sum(ask_vols)) if ask_vols else ask1_vol
        tot_depth = tot_b_depth + tot_a_depth
        obi = round((tot_b_depth - tot_a_depth) / tot_depth, 4) if tot_depth > 0 else 0.0

        prev_q = item.get("_prev_quote") or {}
        if prev_q and (bid1_vol > 0 or ask1_vol > 0):
            voi_res = calculate_volume_order_imbalance(
                bid_p=bid1_p,
                bid_v=bid1_vol,
                prev_bid_p=float(prev_q.get("bid1_price") or bid1_p),
                prev_bid_v=float(prev_q.get("bid1_vol") or bid1_vol),
                ask_p=ask1_p,
                ask_v=ask1_vol,
                prev_ask_p=float(prev_q.get("ask1_price") or ask1_p),
                prev_ask_v=float(prev_q.get("ask1_vol") or ask1_vol),
            )
            voi_val = voi_res["voi"]
            norm_voi = voi_res["normalized_voi"]
        else:
            voi_val = 0.0
            norm_voi = 0.0

        vol_ratio = float(q.get("volume_ratio") or kwargs.get("volume_ratio", 1.0) or 1.0)
        turnover_ratio = float(q.get("turnover_ratio") or kwargs.get("turnover_ratio", 0.0) or 0.0)

        # 统计错过的最大涨幅 (Missed Gain %)
        missed_gain_pct = 0.0
        if init_p > 0 and eff_high > init_p:
            missed_gain_pct = round((eff_high - init_p) / init_p * 100.0, 2)

        # 计算日内分时均价 (VWAP) - 在锁外遍历快照 ticks 列表
        existing_ticks = item.get("ticks") or []
        prices = [t[1] for t in existing_ticks] + [cur_p]
        if len(prices) > 0:
            vwap = round(sum(prices) / len(prices), 2)
        else:
            vwap = cur_p

        # 计算自日内高点回踩幅度 (Pullback %)
        pullback_pct = 0.0
        if eff_high > 0:
            pullback_pct = round((eff_high - cur_p) / eff_high * 100.0, 2)

        # 核心算法：时序低吸买入评分 (Dip Buy Score)
        dip_score = 0.0
        signal = "WATCHING"
        suggested_action = "CONTINUE_OBSERVE"

        base_score = float(item.get("score", 0.0))
        if base_score >= DIP_SCORE_THRESHOLD:
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
            elif PULLBACK_MIN <= pullback_pct <= PULLBACK_MAX and cur_p >= vwap * 0.995:
                # 仅在 -7.0% <= change_pct <= 5.0% 且满足回踩与支撑条件时才触发买入
                obi_bonus = 5.0 if obi > OBI_BULLISH else (0.0 if obi >= OBI_BEARISH else -5.0)
                voi_bonus = round(norm_voi * 4.0, 1)
                dip_score = round(base_score * 0.5 + 45.0 + (PULLBACK_MAX - pullback_pct) * 5.0 + obi_bonus + voi_bonus, 1)
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

        return {
            "initial_price": init_p,
            "high_price": eff_high,
            "low_price": eff_low,
            "last_price": cur_p,
            "last_change_pct": change_pct,
            "obi": obi,
            "voi": voi_val,
            "normalized_voi": norm_voi,
            "volume_ratio": vol_ratio,
            "turnover_ratio": turnover_ratio,
            "missed_gain_pct": missed_gain_pct,
            "vwap": vwap,
            "pullback_pct": pullback_pct,
            "dip_score": dip_score,
            "status": signal,
            "suggested_action": suggested_action,
            "_prev_quote": {
                "bid1_price": bid1_p,
                "bid1_vol": bid1_vol,
                "ask1_price": ask1_p,
                "ask1_vol": ask1_vol,
                "volume": volume,
            },
            "_new_tick": (now_ts, cur_p, volume, obi)
        }

    def update_quote(self, symbol: str, current_price: float, change_pct: float, 
                     high: float = 0.0, low: float = 0.0, volume: float = 0.0,
                     quote: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None, **kwargs) -> Dict[str, Any]:
        """更新个股即时行情并计算时序微观低吸信号 (T9: 涨跌幅安全区间校验, 方案1: 锁外计算指标, 方案5: 治理哨兵值)"""
        try:
            now_ts = now.timestamp() if now else time.time()
            
            # 1. 锁内快速提取快照 (极短持锁)
            with self._lock:
                if symbol not in self.watchlist:
                    return {}
                raw_item = self.watchlist[symbol]
                item_snap = {
                    "name": raw_item.get("name", symbol),
                    "score": raw_item.get("score", 0.0),
                    "target_price": raw_item.get("target_price", 0.0),
                    "initial_price": raw_item.get("initial_price", 0.0),
                    "high_price": raw_item.get("high_price", 0.0),
                    "low_price": raw_item.get("low_price", 0.0),
                    "_prev_quote": dict(raw_item.get("_prev_quote") or {}),
                    "ticks": list(raw_item.get("ticks", []))
                }

            # 2. 耗时的列表运算与平滑指标在锁外完成 (核心锁粒度原则)
            metrics = self._ensure_metrics_ready(
                symbol=symbol,
                current_price=current_price,
                change_pct=change_pct,
                high=high,
                low=low,
                volume=volume,
                quote=quote,
                item_snapshot=item_snap,
                now_ts=now_ts,
                **kwargs
            )
            new_tick = metrics.pop("_new_tick", None)

            # 3. 仅在读写/修改 self.watchlist 字典时短时间持锁 (核心锁粒度原则)
            with self._lock:
                if symbol in self.watchlist:
                    target = self.watchlist[symbol]
                    target.update(metrics)
                    if new_tick:
                        target["ticks"].append(new_tick)
                    item_name = target.get("name", symbol)
                else:
                    item_name = item_snap.get("name", symbol)

            # 4. 返回标准结果字典
            return {
                "symbol": symbol,
                "name": item_name,
                "current_price": current_price,
                "high_price": metrics["high_price"],
                "vwap": metrics["vwap"],
                "pullback_pct": metrics["pullback_pct"],
                "initial_price": metrics["initial_price"],
                "missed_gain_pct": metrics["missed_gain_pct"],
                "obi": metrics["obi"],
                "voi": metrics.get("voi", 0.0),
                "normalized_voi": metrics.get("normalized_voi", 0.0),
                "volume_ratio": metrics["volume_ratio"],
                "turnover_ratio": metrics["turnover_ratio"],
                "dip_score": metrics["dip_score"],
                "signal": metrics["status"],
                "suggested_action": metrics["suggested_action"]
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

    def get_actionable_dip_candidates(self, min_dip_score: float = 75.0) -> List[Dict[str, Any]]:
        """提取已达到时序低吸买入标准的标的列表 (锁内快照，锁外过滤排序)"""
        with self._lock:
            items_snapshot = [dict(item) for item in list(self.watchlist.values())]

        actionable = [
            item for item in items_snapshot
            if item.get("dip_score", 0) >= min_dip_score and item.get("status") == "TIMING_DIP_BUY"
        ]
        # 按照低吸评分由高到低排序 (在锁外完成)
        actionable.sort(key=lambda x: x.get("dip_score", 0), reverse=True)
        return actionable

    def get_summary_list(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """获取所有未买入追踪标的详细结构（支持 M8: now 回测时钟注入，锁内快照，锁外聚合）"""
        now_ts = now.timestamp() if now else time.time()
        with self._lock:
            items_snapshot = [dict(item) for item in list(self.watchlist.values())]

        res = []
        for item in items_snapshot:
            symbol = item.get("symbol", "")
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
                "voi": item.get("voi", 0.0),
                "normalized_voi": item.get("normalized_voi", 0.0),
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
    """未卖出预期时间合理调控模块 (Unsold Position Controller) - 支持多账户与订单维度 (account_id, symbol, order_id) 复合键严格隔离、全量锁保护与防覆盖"""

    def __init__(self):
        # 跟踪预期卖出指令：(account_id, symbol, order_id) -> {expected_sell_time, target_price, reason, order_id, register_time}
        self.pending_sells: Dict[Any, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _parse_time_to_seconds(t_str: Any) -> Optional[int]:
        """安全解析时钟字符串为日内秒数 (统一委托至方案 7 _parse_time_sec)"""
        sec = _parse_time_sec(t_str)
        return sec if sec > 0 else None

    @classmethod
    def _check_is_overdue(cls, now_t: Any, exp_t: Any) -> bool:
        """健壮判断当前时间是否已经超时，统一使用 _parse_time_sec 比对 (方案 7)"""
        if not now_t or not exp_t:
            return False
        now_sec = _parse_time_sec(now_t)
        exp_sec = _parse_time_sec(exp_t)
        if exp_sec > 0:
            return now_sec > exp_sec
        try:
            s_now = str(now_t).strip()
            s_exp = str(exp_t).strip()
            return (s_now > s_exp) if (s_now and s_exp) else False
        except Exception:
            return False

    def register_expected_sell(self, symbol: str, name: str, expected_sell_time_str: str, 
                               target_price: float, reason: str = "时序计划卖出", order_id: str = "", account_id: int = 1,
                               now: Optional[datetime] = None):
        """登记一笔有预期时间的卖出计划（采用 (account_id, symbol, order_id) 复合键隔离，全量锁保护，支持 now 回测时钟注入）"""
        with self._lock:
            acc_id = int(account_id) if account_id is not None else 1
            sym = str(symbol).strip()
            ord_id = str(order_id).strip() if order_id is not None else ""
            if not ord_id:
                count = sum(1 for k in list(self.pending_sells.keys()) if (isinstance(k, tuple) and len(k) >= 2 and k[0] == acc_id and k[1] == sym))
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
        _publish_and_persist_intraday_event("register_expected_sell", {"account_id": acc_id, "symbol": sym, "order_id": ord_id})
        return comp_key

    def cancel_expected_sell(self, symbol: str, account_id: Optional[int] = None, order_id: Optional[str] = None) -> bool:
        """撤销预期卖出计划（支持指定 order_id、account_id 或全量匹配标的撤销，全量锁保护）"""
        with self._lock:
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

        if removed:
            _publish_and_persist_intraday_event("cancel_expected_sell", {"symbol": sym, "account_id": account_id})
        return removed

    def evaluate_unsold_status(self, symbol: str, current_price: float, change_pct: float,
                               now_time_str: Optional[str] = None, account_id: Optional[int] = None,
                               order_id: Optional[str] = None,
                               quote: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None, **kwargs) -> Optional[Dict[str, Any]]:
        """
        对已超预期时间却未卖出的标的进行智能调控 (记录滑点损失率与买一支撑占比，全量锁保护)
        调控逻辑：
        1. 若价格反而拉升至涨停 -> 调控：撤销卖出计划，转为继续持有；
        2. 若已经超时且价格开始滞涨/跳水 -> 调控：立刻降价至即时买一或市价急卖，坚决止盈/避险；
        3. 若尚未到达预期时间 -> 调控：维持挂单等待时机。
        """
        try:
            with self._lock:
                sym = str(symbol).strip()
                target_ord = str(order_id).strip() if order_id is not None and str(order_id).strip() != "" else kwargs.get("order_id")
                target_item = None

                # 1. 精确匹配复合键 (account_id, symbol, order_id)
                if account_id is not None and target_ord:
                    exact_key = (int(account_id), sym, str(target_ord))
                    target_item = self.pending_sells.get(exact_key)
                    if not target_item:
                        target_item = self.pending_sells.get(f"{account_id}:{sym}:{target_ord}")

                # 2. 遍历查找匹配项 (快照遍历，防止并发修改)
                if not target_item:
                    for k, val in list(self.pending_sells.items()):
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

                # 健壮时间解析比较：支持缺少前导零 (如 '9:30:00')、异常格式与 None 容错
                is_overdue = self._check_is_overdue(now_time_str, item.get("expected_sell_time", ""))
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
        """获取未卖出超时调控清单（支持按 account_id 过滤，全量锁保护与快照遍历）"""
        with self._lock:
            res = []
            for key, item in list(self.pending_sells.items()):
                if account_id is not None:
                    item_acc = item.get("account_id")
                    if item_acc is not None and int(item_acc) != int(account_id):
                        continue
                res.append(dict(item))
            return res


from pathlib import Path as _Path
try:
    from src.reliability.crash_recovery import LockFreeRingBuffer, AtomicSnapshotRecovery
except ModuleNotFoundError:
    from reliability.crash_recovery import LockFreeRingBuffer, AtomicSnapshotRecovery

_INTRADAY_SNAPSHOT_PATH = _Path(__file__).resolve().parent.parent / "data" / "intraday_timing_snapshot.json"
_intraday_ring_buffer = LockFreeRingBuffer(capacity=512)
_intraday_snapshot_mgr = AtomicSnapshotRecovery(_INTRADAY_SNAPSHOT_PATH)

# 全局单例引擎实例
intraday_timing_engine = {
    "dip_buyer": TimingDipBuyer(),
    "retention_decider": PositionRetentionDecider(),
    "unsold_controller": UnsoldPositionController(),
    "ring_buffer": _intraday_ring_buffer,
    "snapshot_recovery": _intraday_snapshot_mgr,
    "weights": {
        "ensemble_rule_weight": WEIGHT_RETAIL,      # 多策略量化加权表决基础权重
        "timing_dip_weight": WEIGHT_TECH,           # 时序低吸买入权重 (未买入起伏关注)
        "news_sentiment_weight": WEIGHT_SENTIMENT,  # 新闻舆情与突发事件权重
        "retention_control_weight": WEIGHT_TIMING   # 持仓去留与超时调控权重
    }
}


def save_intraday_state_snapshot() -> bool:
    """将当前 dip_buyer 与 unsold_controller 内存状态原子持久化到磁盘快照"""
    try:
        dip_buyer: TimingDipBuyer = intraday_timing_engine["dip_buyer"]
        unsold_ctrl: UnsoldPositionController = intraday_timing_engine["unsold_controller"]
        with dip_buyer._lock:
            wl_copy = {}
            for sym, val in dip_buyer.watchlist.items():
                d = dict(val)
                d["ticks"] = list(val.get("ticks", []))
                wl_copy[sym] = d
        with unsold_ctrl._lock:
            ps_copy = {}
            for k, val in unsold_ctrl.pending_sells.items():
                d = dict(val)
                if "composite_key" in d and isinstance(d["composite_key"], tuple):
                    d["composite_key"] = list(d["composite_key"])
                str_k = f"{k[0]}:{k[1]}:{k[2]}" if isinstance(k, tuple) and len(k) == 3 else str(k)
                ps_copy[str_k] = d
        state = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "watchlist": wl_copy,
            "pending_sells": ps_copy,
        }
        return _intraday_snapshot_mgr.save_snapshot(state)
    except Exception as e:
        logger.debug(f"保存盘中状态快照忽略: {e}")
        return False


def restore_intraday_state_snapshot() -> bool:
    """进程启动时从磁盘原子快照自愈恢复当日未买/未卖追踪队列"""
    try:
        loaded = _intraday_snapshot_mgr.load_snapshot()
        state = loaded[0] if isinstance(loaded, tuple) else loaded
        if not state or state.get("date") != datetime.now().strftime("%Y-%m-%d"):
            return False
        dip_buyer: TimingDipBuyer = intraday_timing_engine["dip_buyer"]
        unsold_ctrl: UnsoldPositionController = intraday_timing_engine["unsold_controller"]
        with dip_buyer._lock:
            for sym, val in (state.get("watchlist") or {}).items():
                d = dict(val)
                raw_ticks = d.get("ticks") or []
                d["ticks"] = deque([tuple(t) for t in raw_ticks], maxlen=dip_buyer.max_ticks)
                dip_buyer.watchlist[sym] = d
        with unsold_ctrl._lock:
            for str_k, val in (state.get("pending_sells") or {}).items():
                d = dict(val)
                ck = d.get("composite_key")
                if isinstance(ck, list) and len(ck) == 3:
                    comp_key = (int(ck[0]), str(ck[1]), str(ck[2]))
                    d["composite_key"] = comp_key
                else:
                    comp_key = str_k
                unsold_ctrl.pending_sells[comp_key] = d
        return True
    except Exception as e:
        logger.debug(f"恢复盘中状态快照忽略: {e}")
        return False


def _publish_and_persist_intraday_event(event_type: str, payload: Dict[str, Any]) -> None:
    try:
        _intraday_ring_buffer.publish({"event": event_type, "ts": time.time(), **payload})
        save_intraday_state_snapshot()
    except Exception:
        pass


# 模块加载时自动尝试恢复今日盘中快照
restore_intraday_state_snapshot()


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


# ===== Stage 2: 微观盘口动力学算子 (Microstructure Latency Guard) =====

def calculate_volume_order_imbalance(
    bid_p: float,
    bid_v: float,
    prev_bid_p: float,
    prev_bid_v: float,
    ask_p: float,
    ask_v: float,
    prev_ask_p: float,
    prev_ask_v: float
) -> Dict[str, float]:
    """
    Cont, Kukanov & Stoikov (2014) 订单流失衡模型 (Volume Order Imbalance, VOI):
    量化盘口买一/卖一量价瞬时变化揭示的主动订单流净冲击力。
    """
    if prev_bid_p <= 0 or prev_ask_p <= 0:
        return {"voi": 0.0, "delta_bid_vol": 0.0, "delta_ask_vol": 0.0, "normalized_voi": 0.0}

    # 买方订单流增量 Delta V_b
    if bid_p < prev_bid_p - 1e-6:
        delta_vb = 0.0
    elif abs(bid_p - prev_bid_p) <= 1e-6:
        delta_vb = float(bid_v) - float(prev_bid_v)
    else:
        delta_vb = float(bid_v)

    # 卖方订单流增量 Delta V_a
    if ask_p < prev_ask_p - 1e-6:
        delta_va = float(ask_v)
    elif abs(ask_p - prev_ask_p) <= 1e-6:
        delta_va = float(ask_v) - float(prev_ask_v)
    else:
        delta_va = 0.0

    voi = delta_vb - delta_va
    denom = max(float(bid_v) + float(ask_v), float(prev_bid_v) + float(prev_ask_v), 1.0)
    norm_voi = max(-1.0, min(1.0, voi / denom))

    return {
        "voi": round(float(voi), 2),
        "delta_bid_vol": round(float(delta_vb), 2),
        "delta_ask_vol": round(float(delta_va), 2),
        "normalized_voi": round(float(norm_voi), 4)
    }


def estimate_l2_queue_position(
    order_shares: int,
    ahead_queue_volume: float,
    trade_flow_shares_per_sec: float,
    cancel_rate: float = 0.25,
    horizon_sec: float = 60.0
) -> Dict[str, Any]:
    """
    Level-2 逐笔排单队列深度位置与成交概率估计器 (Queue Position & Fill Probability Model):
        V_effective = max(0, V_ahead * (1 - cancel_rate)) + order_shares
        E[T_wait] = V_effective / max(trade_flow_per_sec, 1.0)
        P(Fill <= horizon) = 1 - exp(-horizon / E[T_wait])
    """
    import math
    ahead = max(0.0, float(ahead_queue_volume or 0.0))
    c_rate = max(0.0, min(0.80, float(cancel_rate or 0.25)))
    eff_ahead = ahead * (1.0 - c_rate)
    total_to_clear = eff_ahead + max(0, int(order_shares or 0))

    flow = max(float(trade_flow_shares_per_sec or 0.0), 1e-3)
    expected_wait_sec = total_to_clear / flow
    if expected_wait_sec <= 0.1:
        fill_prob = 1.0
    else:
        fill_prob = 1.0 - math.exp(-max(1.0, float(horizon_sec)) / expected_wait_sec)

    recommend_reprice = bool(fill_prob < 0.35 and ahead > max(order_shares * 3, 5000))
    return {
        "ahead_raw_volume": round(ahead, 1),
        "effective_queue_ahead": round(eff_ahead, 1),
        "expected_wait_sec": round(min(expected_wait_sec, 3600.0), 2),
        "fill_probability_60s": round(float(fill_prob), 4),
        "recommend_reprice": recommend_reprice
    }


def detect_spoofing_and_phantom_liquidity(
    bid1_vol: float,
    ask1_vol: float,
    prev_bid1_vol: float,
    prev_ask1_vol: float,
    traded_delta_vol: float = 0.0,
    min_large_order_threshold: float = 10000.0
) -> Dict[str, Any]:
    """
    虚假挂单与流动性幻影识别器 (Spoofing & Phantom Liquidity Detector):
    当上一帧买一出现巨量托单 (prev_bid1_vol >= threshold 且远大于卖一)，
    但当前帧买一量骤减超过 65%，且期间真实成交量 (traded_delta_vol) 占减少量不足 25% 时，
    判定为主力“买一虚假托单高频撤单诱多 (Bull Trap Spoofing)”。
    反之判定为“卖一虚假压单撤单诱空 (Bear Trap Spoofing)”。
    """
    b_cur = max(0.0, float(bid1_vol or 0.0))
    a_cur = max(0.0, float(ask1_vol or 0.0))
    b_prev = max(0.0, float(prev_bid1_vol or 0.0))
    a_prev = max(0.0, float(prev_ask1_vol or 0.0))
    traded = max(0.0, float(traded_delta_vol or 0.0))

    bid_drop = max(0.0, b_prev - b_cur)
    ask_drop = max(0.0, a_prev - a_cur)

    bull_spoofing = False
    bear_spoofing = False
    cancel_ratio_bid = 0.0
    cancel_ratio_ask = 0.0

    if b_prev >= min_large_order_threshold and bid_drop >= b_prev * 0.65:
        unexplained_drop = max(0.0, bid_drop - traded)
        cancel_ratio_bid = unexplained_drop / bid_drop if bid_drop > 0 else 0.0
        if cancel_ratio_bid >= 0.75:
            bull_spoofing = True

    if a_prev >= min_large_order_threshold and ask_drop >= a_prev * 0.65:
        unexplained_drop_a = max(0.0, ask_drop - traded)
        cancel_ratio_ask = unexplained_drop_a / ask_drop if ask_drop > 0 else 0.0
        if cancel_ratio_ask >= 0.75:
            bear_spoofing = True

    spoofing_type = "NONE"
    if bull_spoofing:
        spoofing_type = "BULL_TRAP_SPOOFING"
    elif bear_spoofing:
        spoofing_type = "BEAR_TRAP_SPOOFING"

    return {
        "is_spoofing": bool(bull_spoofing or bear_spoofing),
        "bull_trap_spoofing": bull_spoofing,
        "bear_trap_spoofing": bear_spoofing,
        "spoofing_type": spoofing_type,
        "bid_cancel_ratio": round(float(cancel_ratio_bid), 4),
        "ask_cancel_ratio": round(float(cancel_ratio_ask), 4)
    }
