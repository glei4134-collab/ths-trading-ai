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
     * 触及硬止损线 (-3.5%) -> 紧急止损（EMERGENCY_STOP_LOSS）。

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
                                     industry: str = ""):
        """登记或更新未买入重点关注标的"""
        now_ts = time.time()
        if symbol not in self.watchlist:
            self.watchlist[symbol] = {
                "symbol": symbol,
                "name": name,
                "score": score,
                "reason": reason,
                "target_price": target_price,
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

    def update_quote(self, symbol: str, current_price: float, change_pct: float, 
                     high: float = 0.0, low: float = 0.0, volume: float = 0.0) -> Dict[str, Any]:
        """更新个股即时行情并计算时序低吸信号"""
        if symbol not in self.watchlist:
            return {}

        item = self.watchlist[symbol]
        now_ts = time.time()
        
        # 动态更新高低点与移动均线
        eff_high = max(high, item["high_price"], current_price)
        eff_low = min(low if low > 0 else current_price, item["low_price"], current_price)
        item["high_price"] = eff_high
        item["low_price"] = eff_low
        item["last_price"] = current_price
        item["last_change_pct"] = change_pct

        # 维护有界双端队列 (RingBuffer)
        item["ticks"].append((now_ts, current_price, volume))

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
        # 1. 股票具有强动量基础 (score >= 70)
        # 2. 早盘冲高后发生健康回踩，回踩幅度在 1.5% ~ 3.5% 之间
        # 3. 价格未跌破日内分时均线 (price >= vwap * 0.995)，表明有大资金托底
        # 4. 处于日内低吸黄金窗口
        dip_score = 0.0
        signal = "WATCHING"
        suggested_action = "CONTINUE_OBSERVE"

        if item["score"] >= 70:
            if 1.5 <= pullback_pct <= 3.8 and current_price >= item["vwap"] * 0.995:
                # 触发优质低吸黄金点
                dip_score = round(item["score"] * 0.5 + 45.0 + (3.8 - pullback_pct) * 5.0, 1)
                signal = "TIMING_DIP_BUY"
                suggested_action = "EXECUTE_DIP_BUY"
            elif pullback_pct > 4.5:
                # 回调过深，可能演变为瀑布跳水，降低评分
                dip_score = 40.0
                signal = "AVOID_DEEP_FALL"
                suggested_action = "WAIT_FOR_STABILIZE"
            elif pullback_pct < 1.0 and change_pct > 5.0:
                # 依然处于冲高顶部，追高风险大
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
            "dip_score": dip_score,
            "signal": signal,
            "suggested_action": suggested_action
        }

    def get_actionable_dip_candidates(self, min_dip_score: float = 75.0) -> List[Dict[str, Any]]:
        """提取已达到时序低吸买入标准的标的列表"""
        actionable = []
        for symbol, item in self.watchlist.items():
            if item.get("dip_score", 0) >= min_dip_score and item.get("status") == "TIMING_DIP_BUY":
                actionable.append(dict(item))
        # 按照低吸评分由高到低排序
        actionable.sort(key=lambda x: x.get("dip_score", 0), reverse=True)
        return actionable

    def get_summary_list(self) -> List[Dict[str, Any]]:
        """获取所有未买入追踪标的详细结构（供前端与大屏展示）"""
        res = []
        now_ts = time.time()
        for symbol, item in self.watchlist.items():
            res.append({
                "symbol": symbol,
                "name": item.get("name", ""),
                "score": item.get("score", 0),
                "industry": item.get("industry", ""),
                "reason": item.get("reason", "等待低吸"),
                "tracked_minutes": round((now_ts - item.get("registered_at", now_ts)) / 60.0, 1),
                "last_price": item.get("last_price", 0.0),
                "high_price": item.get("high_price", 0.0),
                "vwap": item.get("vwap", 0.0),
                "pullback_pct": item.get("pullback_pct", 0.0),
                "dip_score": item.get("dip_score", 0.0),
                "status": item.get("status", "WATCHING"),
                "suggested_action": item.get("suggested_action", "OBSERVE"),
                "utilization_weight": 0.25
            })
        return res


class PositionRetentionDecider:
    """当前持仓去留动态评估模块（去/留/减/换）"""

    def __init__(self):
        pass

    def evaluate_retention(self, position: Dict[str, Any], quote: Dict[str, Any]) -> Dict[str, Any]:
        """
        全要素评估现有持仓去留决策
        :param position: 当前账户持仓数据 (symbol, amount, cost_price, profit_rate, held_days, etc.)
        :param quote: 实时盘口数据 (price, change_pct, high, low, bid1_vol, ask1_vol, vwap)
        :return: 决策结果字典 (action: KEEP/TRIM/SELL/STOP, reason, confidence, urgency)
        """
        symbol = position.get("symbol", position.get("code", ""))
        name = position.get("name", symbol)
        cost_price = float(position.get("cost_price", 0.0) or 0.0)
        current_price = float(quote.get("price", 0.0) or cost_price)
        change_pct = float(quote.get("change_pct", 0.0) or 0.0)
        
        profit_rate = 0.0
        if cost_price > 0:
            profit_rate = (current_price - cost_price) / cost_price
        else:
            profit_rate = float(position.get("profit_rate", 0.0) or 0.0)

        high_price = float(quote.get("high", 0.0) or current_price)
        vwap = float(quote.get("vwap", 0.0) or current_price)
        ask1_vol = float(quote.get("ask1_vol", 0.0) or 0.0)

        # 1. 涨停板判定：封单充足，坚决【留】
        if change_pct >= 9.8 and ask1_vol == 0:
            return {
                "symbol": symbol,
                "name": name,
                "action": "KEEP_LIMIT_UP",
                "retention_decision": "留 (涨停封单)",
                "confidence": 0.95,
                "urgency": "LOW",
                "reason": f"股票强势封板 (+{change_pct:.2f}%)，卖一封死，坚决持仓赚取次日高开溢价",
                "profit_rate": profit_rate
            }

        # 2. 硬风控止损红线：触及 -3.5% 坚决【去】
        if profit_rate <= -0.035:
            return {
                "symbol": symbol,
                "name": name,
                "action": "EMERGENCY_STOP_LOSS",
                "retention_decision": "去 (硬止损)",
                "confidence": 0.99,
                "urgency": "HIGH",
                "reason": f"触及硬止损阈值 ({profit_rate*100:.2f}% <= -3.5%)，铁律清仓防大回撤",
                "profit_rate": profit_rate
            }

        # 3. 冲高受阻回落保利判定：曾经大赚但从日内高点回落过深，部分【减】
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
                "profit_rate": profit_rate
            }

        # 4. 破位与均线背离判定：跌破日内分时均线且翻绿，【去】仓换牛股
        if vwap > 0 and current_price < vwap * 0.982 and change_pct < 0:
            return {
                "symbol": symbol,
                "name": name,
                "action": "SELL_WEAK_EXIT",
                "retention_decision": "去 (破位换仓)",
                "confidence": 0.82,
                "urgency": "MEDIUM",
                "reason": f"价格跌破分时均线 1.8% 且处于弱势区间，建议出局腾挪仓位配置时序低吸标的",
                "profit_rate": profit_rate
            }

        # 5. 多头趋势良好，站在分时均线上方：顺势【留】
        if current_price >= vwap:
            return {
                "symbol": symbol,
                "name": name,
                "action": "KEEP_RIDE_TREND",
                "retention_decision": "留 (多头顺势)",
                "confidence": 0.78,
                "urgency": "LOW",
                "reason": f"运行于日内分时均线上方 (现价 {current_price:.2f} >= VWAP {vwap:.2f})，多头动能充沛，持股待涨",
                "profit_rate": profit_rate
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
            "profit_rate": profit_rate
        }


class UnsoldPositionController:
    """未卖出预期时间合理调控模块 (Unsold Position Controller) - 支持多账户 (account_id, symbol) 复合键严格隔离"""

    def __init__(self):
        # 跟踪预期卖出指令：(account_id, symbol) -> {expected_sell_time, target_price, reason, order_id, register_time}
        self.pending_sells: Dict[Tuple[int, str], Dict[str, Any]] = {}

    def register_expected_sell(self, symbol: str, name: str, expected_sell_time_str: str, 
                               target_price: float, reason: str = "时序计划卖出", order_id: str = "", account_id: int = 1):
        """登记一笔有预期时间的卖出计划（采用 (account_id, symbol) 复合键严格隔离）"""
        acc_id = int(account_id) if account_id is not None else 1
        sym = str(symbol).strip()
        comp_key = (acc_id, sym)
        self.pending_sells[comp_key] = {
            "key": f"{acc_id}:{sym}",
            "composite_key": comp_key,
            "account_id": acc_id,
            "symbol": sym,
            "name": name,
            "expected_sell_time": expected_sell_time_str,
            "target_price": target_price,
            "reason": reason,
            "order_id": order_id,
            "registered_at": time.time(),
            "status": "AWAITING_TIME",
            "tuning_action": "NONE"
        }

    def cancel_expected_sell(self, symbol: str, account_id: Optional[int] = None) -> bool:
        """撤销预期卖出计划（支持指定 account_id 或全量匹配标的撤销）"""
        sym = str(symbol).strip()
        removed = False
        if account_id is not None:
            key = (int(account_id), sym)
            if key in self.pending_sells:
                del self.pending_sells[key]
                removed = True
            str_key = f"{account_id}:{sym}"
            if str_key in self.pending_sells:
                del self.pending_sells[str_key]
                removed = True
        else:
            keys_to_del = [
                k for k in list(self.pending_sells.keys())
                if (isinstance(k, tuple) and len(k) == 2 and k[1] == sym) or k == sym or (isinstance(k, str) and k.endswith(f":{sym}"))
            ]
            for k in keys_to_del:
                del self.pending_sells[k]
                removed = True
        return removed

    def evaluate_unsold_status(self, symbol: str, current_price: float, change_pct: float,
                               now_time_str: Optional[str] = None, account_id: Optional[int] = None) -> Dict[str, Any]:
        """
        对已超预期时间却未卖出的标的进行智能调控
        调控逻辑：
        1. 若价格反而拉升至涨停 -> 调控：撤销卖出计划，转为继续持有；
        2. 若已经超时且价格开始滞涨/跳水 -> 调控：立刻降价至即时买一或市价急卖，坚决止盈/避险；
        3. 若尚未到达预期时间 -> 调控：维持挂单等待时机。
        """
        sym = str(symbol).strip()
        target_item = None
        if account_id is not None:
            key = (int(account_id), sym)
            target_item = self.pending_sells.get(key)
            if not target_item:
                target_item = self.pending_sells.get(f"{account_id}:{sym}") or self.pending_sells.get(sym)
        else:
            # 若未传入 account_id，遍历寻找对应 symbol 的首个匹配记录
            for k, val in self.pending_sells.items():
                if (isinstance(k, tuple) and len(k) == 2 and k[1] == sym) or (isinstance(k, str) and (k == sym or k.endswith(f":{sym}"))):
                    target_item = val
                    break

        if not target_item:
            return {}

        item = target_item
        acc_id = item.get("account_id", account_id or 1)
        if not now_time_str:
            now_time_str = datetime.now().strftime("%H:%M:%S")

        is_overdue = now_time_str > item["expected_sell_time"]
        tuning_action = "KEEP_PENDING"
        tuning_reason = "尚未到达预期卖出时钟"
        status = "PENDING"

        if is_overdue:
            status = "OVERDUE_UNSOLD"
            # 检查市场表现
            if change_pct >= 9.8:
                # 市场超预期强劲，调控为顺延持有
                tuning_action = "CANCEL_AND_RIDE"
                tuning_reason = f"[账户#{acc_id}] 已过预定卖出时间 {item['expected_sell_time']}，但股价封死涨停，调控为取消卖出继续享受涨幅"
            elif current_price < item["target_price"] * 0.985:
                # 价格已跌破目标价 1.5%，属于挂单过高未成交，必须紧急调价
                tuning_action = "REPRICE_URGENT_SELL"
                tuning_reason = f"[账户#{acc_id}] 已超时且价格跌破挂单价 ({current_price:.2f} < {item['target_price']:.2f})，调控为按买一市价紧急卖出防滑坡"
            else:
                # 正常超时，调整为分批现价卖出
                tuning_action = "MARKET_SELL_NOW"
                tuning_reason = f"[账户#{acc_id}] 已超过预定卖出窗口 {item['expected_sell_time']}，执行纪律性市价调仓"
        else:
            status = "AWAITING_TIME"
            tuning_action = "WAIT_SCHEDULED_TIME"
            tuning_reason = f"[账户#{acc_id}] 等待到达预期卖出时间 {item['expected_sell_time']}"

        item["status"] = status
        item["tuning_action"] = tuning_action
        item["tuning_reason"] = tuning_reason

        return {
            "account_id": acc_id,
            "symbol": sym,
            "name": item.get("name", sym),
            "expected_sell_time": item["expected_sell_time"],
            "target_price": item["target_price"],
            "current_price": current_price,
            "is_overdue": is_overdue,
            "status": status,
            "tuning_action": tuning_action,
            "tuning_reason": tuning_reason,
            "utilization_weight": 0.20
        }

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
        "ensemble_rule_weight": 0.35,      # 多策略量化加权表决基础权重
        "timing_dip_weight": 0.25,         # 时序低吸买入权重 (未买入起伏关注)
        "news_sentiment_weight": 0.20,     # 新闻舆情与突发事件权重
        "retention_control_weight": 0.20   # 持仓去留与超时调控权重
    }
}
