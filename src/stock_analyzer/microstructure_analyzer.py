"""
stock_analyzer/microstructure_analyzer.py
维度 2: 股票微观盘口结构与订单簿深度分析器
负责五档买卖盘深度、订单簿不平衡度(OBI)、委托流不平衡(VOI)、买卖价差与流动性冲击评估
"""

import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class MicrostructureAnalyzer:
    """微观盘口量价与流动性分析器"""

    def analyze(self, symbol: str, quote: Dict[str, Any]) -> Dict[str, Any]:
        """
        分析股票盘口微观结构与挂单深度

        Args:
            symbol: 股票代码
            quote: 实时行情
        """
        try:
            from execution_optimizer import fetch_orderbook_depth
            depth = fetch_orderbook_depth(symbol)
        except Exception as e:
            logger.debug(f"获取五档盘口深度异常 {symbol}: {e}")
            depth = None

        curr_price = float(quote.get("price") or 0.0)
        signals = []

        if not depth:
            return {
                "score": 50.0,
                "obi": 0.0,
                "obi_pct": "0.0%",
                "normalized_voi": 0.0,
                "spread": 0.01,
                "bid_total_vol": 0,
                "ask_total_vol": 0,
                "liquidity_state": "NORMAL",
                "signals": ["盘口五档不可用或休市中"]
            }

        if isinstance(depth, dict):
            bid_vols = depth.get("bid_volumes", [0] * 5)
            ask_vols = depth.get("ask_volumes", [0] * 5)
            bid_prices = depth.get("bid_prices", [0.0] * 5)
            ask_prices = depth.get("ask_prices", [0.0] * 5)
            total_bid_vol = depth.get("total_bid_vol", sum(bid_vols[:5]))
            total_ask_vol = depth.get("total_ask_vol", sum(ask_vols[:5]))
            obi = depth.get("obi", None)
            bid_1 = bid_prices[0] if len(bid_prices) > 0 and bid_prices[0] > 0 else curr_price
            ask_1 = ask_prices[0] if len(ask_prices) > 0 and ask_prices[0] > 0 else curr_price
            voi = depth.get("voi", 0.0)
            norm_voi = depth.get("normalized_voi", 0.0)
            is_limit_up = depth.get("is_limit_up", False)
            is_limit_down = depth.get("is_limit_down", False)
        else:
            # OrderBookDepth dataclass
            bids = getattr(depth, "bids", [])
            asks = getattr(depth, "asks", [])
            bid_vols = [b[1] for b in bids] if bids else [0] * 5
            ask_vols = [a[1] for a in asks] if asks else [0] * 5
            total_bid_vol = getattr(depth, "total_bid_vol", sum(bid_vols[:5]))
            total_ask_vol = getattr(depth, "total_ask_vol", sum(ask_vols[:5]))
            obi = getattr(depth, "obi", None)
            bid_1 = getattr(depth, "bid1_price", 0.0) or (bids[0][0] if bids else curr_price)
            ask_1 = getattr(depth, "ask1_price", 0.0) or (asks[0][0] if asks else curr_price)
            voi = getattr(depth, "voi", 0.0)
            norm_voi = getattr(depth, "normalized_voi", 0.0)
            is_limit_up = getattr(depth, "is_limit_up", False)
            is_limit_down = getattr(depth, "is_limit_down", False)

        # 1. 订单簿不平衡度 OBI: [-1.0, 1.0]
        denom = total_bid_vol + total_ask_vol
        if obi is None:
            obi = (total_bid_vol - total_ask_vol) / denom if denom > 0 else 0.0

        # 2. 买一卖一买卖价差 (Bid-Ask Spread)
        spread = round(max(0.001, ask_1 - bid_1), 3)

        # 3. 规范化 VOI (Volume Order Imbalance)
        # 委托动量因子: 买方积极挂单推升价格的冲量
        if norm_voi == 0.0 and denom > 0 and voi != 0.0:
            norm_voi = round(voi / (denom + 1e-5), 4)

        # 4. 微观盘口评分生成
        micro_score = 50.0 + (obi * 35.0)  # OBI 贡献 -35 ~ +35
        if norm_voi > 0.005:
            micro_score += 15.0
            signals.append(f"买盘委托冲量强劲(VOI=+{norm_voi:.4f})")
        elif norm_voi < -0.005:
            micro_score -= 15.0
            signals.append(f"卖盘委托压制显著(VOI={norm_voi:.4f})")

        if obi > 0.35:
            signals.append(f"买盘挂单承接丰厚(OBI={obi*100:+.1f}%)")
        elif obi < -0.35:
            signals.append(f"卖盘重单密集压顶(OBI={obi*100:+.1f}%)")

        if spread <= 0.01:
            signals.append("买卖点差极小(高流动性)")
        elif spread > 0.05:
            signals.append(f"买卖点差偏大({spread:.2f}元，存在冲击滑点)")

        micro_score = max(0.0, min(100.0, micro_score))

        return {
            "score": round(micro_score, 1),
            "obi": round(obi, 4),
            "obi_pct": f"{obi*100:+.1f}%",
            "normalized_voi": round(norm_voi, 4),
            "spread": spread,
            "bid_total_vol": int(total_bid_vol),
            "ask_total_vol": int(total_ask_vol),
            "bid_depth_1_5": bid_vols[:5],
            "ask_depth_1_5": ask_vols[:5],
            "liquidity_state": "HIGH" if denom > 20000 else "MEDIUM" if denom > 5000 else "LOW",
            "signals": signals
        }
