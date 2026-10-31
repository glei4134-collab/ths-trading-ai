"""
交易执行优化与五档深度 (OBI) 分析引擎
负责:
1. 五档盘口深度 (OBI) 解析与订单簿失衡度计算:
   OBI = (Sum(BidVol_1..5) - Sum(AskVol_1..5)) / (Sum(BidVol_1..5) + Sum(AskVol_1..5))
2. 涨停抢筹与智能防滑点 (跨档挂单):
   当 OBI > 0.45 且股价触及/逼近涨停时，自动跨档挂涨停价，保障优先排队与零滑点拒单。
3. 微批次 TWAP 拆单:
   当多账户总委托量 > 1.2 * 卖一量 (AskVol1) 时，自动拆解为 2~3 个微批次 (间隔 1.5s) 平滑派发，防盘口打穿与市场冲击。
"""

import time
import re
import logging
import requests
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 会话复用
_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/json,*/*",
})
_session.trust_env = False  # 绕过系统代理直接请求国内行情


@dataclass
class OrderBookDepth:
    """五档盘口深度结构体"""
    symbol: str
    price: float
    pre_close: float
    limit_up: float
    limit_down: float
    bids: List[Tuple[float, int]] = field(default_factory=list)  # [(price, shares), ...] 1~5
    asks: List[Tuple[float, int]] = field(default_factory=list)  # [(price, shares), ...] 1~5
    total_bid_vol: int = 0                                       # 买一~买五总委托股数
    total_ask_vol: int = 0                                       # 卖一~卖五总委托股数
    obi: float = 0.0                                             # 订单簿失衡度 [-1.0, 1.0]
    ask1_price: float = 0.0
    ask1_vol: int = 0                                            # 卖一量 (股数)
    bid1_price: float = 0.0
    bid1_vol: int = 0                                            # 买一量 (股数)
    is_limit_up: bool = False                                    # 是否已涨停
    is_limit_down: bool = False                                  # 是否已跌停
    is_touching_limit_up: bool = False                           # 是否触及或逼近涨停 (涨幅>=9.5%或距涨停<0.5%)

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "pre_close": self.pre_close,
            "limit_up": self.limit_up,
            "limit_down": self.limit_down,
            "bids": self.bids,
            "asks": self.asks,
            "total_bid_vol": self.total_bid_vol,
            "total_ask_vol": self.total_ask_vol,
            "obi": self.obi,
            "ask1_price": self.ask1_price,
            "ask1_vol": self.ask1_vol,
            "bid1_price": self.bid1_price,
            "bid1_vol": self.bid1_vol,
            "is_limit_up": self.is_limit_up,
            "is_limit_down": self.is_limit_down,
            "is_touching_limit_up": self.is_touching_limit_up
        }


def calculate_obi(bid_vols: List[int], ask_vols: List[int]) -> float:
    """
    计算五档订单簿失衡度 Order Book Imbalance (OBI)
    公式: OBI = (Sum(BidVol_i) - Sum(AskVol_i)) / (Sum(BidVol_i) + Sum(AskVol_i))
    范围: [-1.0, 1.0]。
    > 0.45 表示盘口买压极度强势，买方主导；< -0.45 表示抛压沉重。
    """
    total_b = sum(bid_vols)
    total_a = sum(ask_vols)
    denom = total_b + total_a
    if denom == 0:
        return 0.0
    return round((total_b - total_a) / denom, 4)


def fetch_orderbook_depth(symbol: str) -> OrderBookDepth:
    """
    获取单只股票的五档盘口深度与 OBI
    支持腾讯行情源高速解析 + 仿真虚拟时钟回退
    """
    symbol = symbol.strip()
    # 1. 检查是否处于虚拟时钟演练状态
    try:
        from fast_data import MockMarketDataProvider
        if MockMarketDataProvider.is_mock_active():
            mock_quotes = MockMarketDataProvider.generate_mock_realtime([symbol])
            mq = mock_quotes.get(symbol, {})
            curr_p = float(mq.get("price", 10.0))
            pre_c = float(mq.get("pre_close", curr_p))
            l_up = round(pre_c * 1.10, 2)
            l_down = round(pre_c * 0.90, 2)
            # 生成高仿真买卖五档
            bids = [(round(curr_p - i * 0.01, 2), 5000 + i * 1000) for i in range(5)]
            asks = [(round(curr_p + (i + 1) * 0.01, 2), 3000 + i * 800) for i in range(5)]
            b_vols = [b[1] for b in bids]
            a_vols = [a[1] for a in asks]
            obi = calculate_obi(b_vols, a_vols)
            return OrderBookDepth(
                symbol=symbol,
                price=curr_p,
                pre_close=pre_c,
                limit_up=l_up,
                limit_down=l_down,
                bids=bids,
                asks=asks,
                total_bid_vol=sum(b_vols),
                total_ask_vol=sum(a_vols),
                obi=obi,
                ask1_price=asks[0][0],
                ask1_vol=asks[0][1],
                bid1_price=bids[0][0],
                bid1_vol=bids[0][1],
                is_limit_up=curr_p >= l_up,
                is_limit_down=curr_p <= l_down,
                is_touching_limit_up=curr_p >= round(l_up * 0.995, 2)
            )
    except Exception as e:
        logger.debug(f"检查虚拟时钟行情跳过: {e}")

    # 2. 腾讯行情API获取完整五档
    prefix = "sh" if symbol.startswith("6") else "sz"
    tc_code = f"{prefix}{symbol}"
    url = f"http://qt.gtimg.cn/q={tc_code}"

    try:
        resp = _session.get(url, timeout=3)
        resp.encoding = "gbk"
        text = resp.text.strip()
    except Exception as e:
        logger.error(f"获取五档盘口失败 {symbol}: {e}")
        return _fallback_orderbook(symbol)

    if "=" not in text:
        return _fallback_orderbook(symbol)

    data_part = text.split("=")[1].strip('"\n; ')
    fields = data_part.split("~")
    if len(fields) < 30:
        return _fallback_orderbook(symbol)

    try:
        curr_price = float(fields[3]) if fields[3] else 0.0
        pre_close = float(fields[4]) if fields[4] else 0.0

        # 五档买单 (9-18)
        bids = []
        bid_vols = []
        for i in range(5):
            p_idx = 9 + i * 2
            v_idx = 10 + i * 2
            p = float(fields[p_idx]) if fields[p_idx] else 0.0
            v = int(fields[v_idx]) * 100 if fields[v_idx] else 0  # 手 -> 股
            bids.append((p, v))
            bid_vols.append(v)

        # 五档卖单 (19-28)
        asks = []
        ask_vols = []
        for i in range(5):
            p_idx = 19 + i * 2
            v_idx = 20 + i * 2
            p = float(fields[p_idx]) if fields[p_idx] else 0.0
            v = int(fields[v_idx]) * 100 if fields[v_idx] else 0  # 手 -> 股
            asks.append((p, v))
            ask_vols.append(v)

        # 涨跌停价 (47, 48)
        limit_ratio = 0.20 if symbol.startswith(("688", "300")) else 0.10
        limit_up = float(fields[47]) if len(fields) > 47 and fields[47] else round(pre_close * (1 + limit_ratio), 2)
        limit_down = float(fields[48]) if len(fields) > 48 and fields[48] else round(pre_close * (1 - limit_ratio), 2)

        total_bid = sum(bid_vols)
        total_ask = sum(ask_vols)
        obi = calculate_obi(bid_vols, ask_vols)

        # 判定是否触及或逼近涨停
        # 1. 当前价达到或大于等于涨停价
        # 2. 卖一价达到涨停价，或卖盘完全被扫空(ask1_vol==0 且当前价>=涨停价*0.995)
        # 3. 价格在距涨停价0.5%以内 (如主板涨幅>=9.5% 或 创业板>=19.5%)
        is_limit_up = curr_price >= limit_up if limit_up > 0 else False
        is_touching_limit_up = (
            is_limit_up or
            (limit_up > 0 and curr_price >= round(limit_up * 0.995, 2)) or
            (asks[0][0] >= limit_up if limit_up > 0 and asks[0][0] > 0 else False) or
            (asks[0][1] == 0 and total_ask == 0 and curr_price >= round(limit_up * 0.99, 2))
        )
        is_limit_down = curr_price <= limit_down if limit_down > 0 else False

        return OrderBookDepth(
            symbol=symbol,
            price=curr_price,
            pre_close=pre_close,
            limit_up=limit_up,
            limit_down=limit_down,
            bids=bids,
            asks=asks,
            total_bid_vol=total_bid,
            total_ask_vol=total_ask,
            obi=obi,
            ask1_price=asks[0][0],
            ask1_vol=asks[0][1],
            bid1_price=bids[0][0],
            bid1_vol=bids[0][1],
            is_limit_up=is_limit_up,
            is_limit_down=is_limit_down,
            is_touching_limit_up=is_touching_limit_up
        )

    except Exception as e:
        logger.error(f"解析五档行情异常 {symbol}: {e}")
        return _fallback_orderbook(symbol)


def _fallback_orderbook(symbol: str) -> OrderBookDepth:
    """无法获取盘口时的安全兜底"""
    return OrderBookDepth(
        symbol=symbol,
        price=0.0,
        pre_close=0.0,
        limit_up=0.0,
        limit_down=0.0,
        bids=[(0.0, 0)] * 5,
        asks=[(0.0, 0)] * 5,
        total_bid_vol=0,
        total_ask_vol=0,
        obi=0.0,
        ask1_price=0.0,
        ask1_vol=0,
        bid1_price=0.0,
        bid1_vol=0,
        is_limit_up=False,
        is_limit_down=False,
        is_touching_limit_up=False
    )


def evaluate_aggressive_pegging(symbol: str, action: str, requested_price: float,
                                depth: Optional[OrderBookDepth] = None) -> Tuple[float, bool, str]:
    """
    涨停抢筹与智能防滑点 (跨档挂单优化):
    当 action == "buy" 时:
      若 OBI > 0.45 且股价触及/逼近涨停 (touching_limit_up):
        自动跨档挂单至涨停价 (limit_up)，确保在交易所撮合队列中优先成交，避免因滑点拒单。
      若非涨停但卖一价高于当前申请价 (且在合理范围内):
        跨档挂在卖一价保证瞬间吃单。
    返回: (优化后的价格, 是否触发抢筹, 策略说明)
    """
    if action != "buy":
        return requested_price, False, "非买入指令"

    if depth is None:
        depth = fetch_orderbook_depth(symbol)

    if depth.limit_up <= 0:
        return requested_price, False, "无有效涨跌停价"

    # 条件1: OBI > 0.45 且触及/逼近涨停 -> 跨档挂涨停价抢筹
    if depth.obi > 0.45 and depth.is_touching_limit_up:
        peg_price = depth.limit_up
        reason = (f"🔥 [涨停抢筹] OBI={depth.obi:.3f}(>0.45) 且触及涨停，"
                  f"跨档直接挂涨停价 ¥{peg_price:.2f} 确保抢筹优先排队与零滑点成交")
        logger.info(f"{symbol}: {reason}")
        return peg_price, True, reason

    # 条件2: 正常买入时，若申请价略低于卖一价，且OBI买方优势明显(>0.30)，跨档挂卖一价确保直接撮合
    if depth.ask1_price > 0 and 0 < requested_price < depth.ask1_price:
        diff_pct = (depth.ask1_price - requested_price) / requested_price
        if diff_pct <= 0.005 and depth.obi > 0.30:  # 价差在0.5%以内且买方主导
            peg_price = depth.ask1_price
            reason = (f"⚡ [智能跨档] OBI={depth.obi:.3f}(>0.30)，"
                      f"申请价¥{requested_price:.2f}微调跨档挂卖一价 ¥{peg_price:.2f} 瞬间吃单防滑点")
            logger.info(f"{symbol}: {reason}")
            return peg_price, True, reason

    return requested_price, False, "正常执行价"


def plan_twap_micro_batches(account_shares: Dict[int, int], ask1_vol: int,
                            batch_interval: float = 1.5) -> Tuple[bool, List[Dict[int, int]], str]:
    """
    微批次 TWAP 拆单规划器:
    当所有账户总买入股数 > 1.2 * 卖一量 (AskVol1) 时，自动拆解为 2~3 个微批次。
    每批次严格向下/向上规整为 100 股整数倍，总和完全守恒。
    
    返回: (是否触发拆单, 微批次列表 [ {acc_id: shares}, ... ], 拆单说明)
    """
    total_shares = sum(account_shares.values())
    if total_shares <= 0:
        return False, [account_shares], "无委托量"

    # 如果卖一量为0 (如已一字封板)，或总委托量 <= 1.2 * 卖一量，无需拆单
    if ask1_vol <= 0 or total_shares <= int(1.2 * ask1_vol):
        return False, [account_shares], f"总委托量 {total_shares}股 <= 1.2*卖一量({ask1_vol}股)，直接单批并发执行"

    # 判定微批次数量：超过2.5倍拆3批，否则拆2批
    batch_count = 3 if total_shares > int(2.5 * ask1_vol) else 2
    ratios = [0.40, 0.30, 0.30] if batch_count == 3 else [0.50, 0.50]

    batches = [{} for _ in range(batch_count)]

    for acc_id, qty in account_shares.items():
        if qty <= 0:
            continue
        if qty < 200:
            # 资金较小仅有一手(100股)，不可再拆，直接并入第一微批次
            batches[0][acc_id] = qty
            continue

        if batch_count == 2:
            b0 = int(round(qty * 0.50 / 100)) * 100
            b0 = max(100, min(qty - 100, b0))
            b1 = qty - b0
            batches[0][acc_id] = b0
            batches[1][acc_id] = b1
        else:
            b0 = int(round(qty * 0.40 / 100)) * 100
            b0 = max(100, min(qty - 200, b0))
            rem = qty - b0
            b1 = int(round(rem * 0.50 / 100)) * 100
            b1 = max(100, min(rem - 100, b1))
            b2 = rem - b1
            batches[0][acc_id] = b0
            batches[1][acc_id] = b1
            batches[2][acc_id] = b2

    # 过滤掉全为0的空批次
    active_batches = [b for b in batches if sum(b.values()) > 0]
    reason = (f"🌊 [TWAP-拆单] 总委托量 {total_shares}股 > 1.2*卖一量({ask1_vol}股)，"
              f"自动拆分为 {len(active_batches)} 个微批次 (间隔 {batch_interval}s) 平滑派发，防盘口打穿")
    logger.info(reason)
    return True, active_batches, reason
