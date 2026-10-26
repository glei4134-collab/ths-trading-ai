"""
多因子股票筛选器
替代简单成交额排序，通过三阶段筛选提升信号质量:
  Stage 1: 硬过滤（排除不可交易股票）
  Stage 2: 质量评分（多因子打分）
  Stage 3: 分层取样（行业分散化）
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class StockScreener:
    """多因子股票筛选器"""

    def __init__(self):
        # 各因子权重（可根据回测结果调整）
        self.factor_weights = {
            "turnover": 0.25,     # 换手率
            "volume_ratio": 0.25, # 量比
            "amplitude": 0.20,    # 振幅
            "room": 0.20,         # 距涨跌停空间
            "change_momentum": 0.10,  # 涨跌动量
        }
        # 最小流动性要求（成交额，单位：元）
        self.min_amount = 5_000_000   # 500万
        # 最低股价
        self.min_price = 2.0
        # 每个行业/板块最多取几只
        self.max_per_prefix = 5  # 用代码前缀粗略代替行业分类

    def screen(self, market_data, regime: str = "sideways",
               max_count: int = 50) -> List[Dict]:
        """
        三阶段筛选主入口

        Args:
            market_data: DataFrame（东方财富全市场行情）或 list of dict
            regime: 市场环境 "bull"/"bear"/"sideways"/"volatile"
            max_count: 最终返回数量

        Returns:
            [{"code", "name", "price", "score", ...}, ...] 按综合评分降序
        """
        import pandas as pd

        # 统一转为 DataFrame
        if isinstance(market_data, list):
            if not market_data:
                return []
            df = pd.DataFrame(market_data)
            # 兼容中英文字段名
            col_map = {
                "code": "代码", "name": "名称", "price": "最新价",
                "change_pct": "涨跌幅", "open": "今开", "high": "最高",
                "low": "最低", "pre_close": "昨收", "volume": "成交量",
                "amount": "成交额", "turnover": "换手率",
            }
            for en, cn in col_map.items():
                if en in df.columns and cn not in df.columns:
                    df[cn] = df[en]
        elif hasattr(market_data, "empty"):
            df = market_data.copy()
        else:
            return []

        if df.empty:
            return []

        original_count = len(df)

        # Stage 1: 硬过滤
        df = self._stage1_hard_filter(df)
        filtered_count = len(df)

        if df.empty:
            logger.info("选股器: 所有股票均被过滤")
            return []

        # Stage 2: 质量评分
        df = self._stage2_quality_score(df, regime)

        # Stage 3: 分层取样
        result = self._stage3_diversify(df, max_count)

        logger.info(
            f"选股器: {original_count} -> {filtered_count} (过滤) "
            f"-> {len(result)} (最终), regime={regime}"
        )
        return result

    # ==================== Stage 1: 硬过滤 ====================

    def _stage1_hard_filter(self, df):
        """排除不可交易的股票"""
        import pandas as pd
        before = len(df)

        # 1. 排除停牌和无效数据
        df = df[df["成交量"] > 0]
        df = df[df["最新价"] > 0]

        # 2. 排除低价股
        df = df[df["最新价"] >= self.min_price]

        # 3. 排除成交额不足（流动性差）
        df = df[df["成交额"] >= self.min_amount]

        # 4. 排除封死涨停（买入无法成交）
        #    条件: 涨跌幅 >= 9.8% 且 最高价 == 最新价（封板）
        if "涨跌幅" in df.columns and "最高" in df.columns:
            is_limit_up = (df["涨跌幅"] >= 9.8) & (df["最高"] == df["最新价"])
            df = df[~is_limit_up]

        # 5. 排除封死跌停（卖出无法成交，且无法买入）
        if "涨跌幅" in df.columns and "最低" in df.columns:
            is_limit_down = (df["涨跌幅"] <= -9.8) & (df["最低"] == df["最新价"])
            df = df[~is_limit_down]

        # 6. 排除一字板（开盘即涨停/跌停，全天无法交易）
        if all(c in df.columns for c in ["今开", "最高", "最低", "最新价", "涨跌幅"]):
            is_one_word = (
                (df["今开"] == df["最高"]) &
                (df["最高"] == df["最低"]) &
                (df["涨跌幅"].abs() > 0.5)
            )
            df = df[~is_one_word]

        after = len(df)
        if before != after:
            logger.debug(f"Stage 1 硬过滤: {before} -> {after} (排除 {before - after} 只)")
        return df

    # ==================== Stage 2: 质量评分 ====================

    def _stage2_quality_score(self, df, regime: str):
        """多因子质量评分"""
        import pandas as pd
        import numpy as np

        # 各因子评分（0~1 归一化）
        scores = pd.DataFrame(index=df.index)

        # --- 换手率评分 ---
        # 最佳区间: 3-8%（活跃但不过度投机）
        if "换手率" in df.columns:
            turnover = df["换手率"].clip(0, 50)  # 防止极端值
            # 钟形分布: 5% 为峰值，偏离越远分越低
            scores["turnover"] = np.exp(-0.5 * ((turnover - 5) / 3) ** 2)
        else:
            scores["turnover"] = 0.5

        # --- 量比评分 ---
        # 量比 = 成交量 / 前5日平均成交量（近似用 成交额/换手率 估算）
        # 简化: 用 成交额 作为活跃度指标，归一化
        if "成交额" in df.columns:
            amount = df["成交额"]
            # 对数归一化（成交额差异巨大）
            log_amount = np.log10(amount.clip(lower=1))
            scores["volume_ratio"] = (log_amount - log_amount.min()) / (log_amount.max() - log_amount.min() + 1e-10)
        else:
            scores["volume_ratio"] = 0.5

        # --- 振幅评分 ---
        # 最佳振幅: 2-5%（有波动但不极端）
        if all(c in df.columns for c in ["最高", "最低", "昨收"]):
            amplitude = ((df["最高"] - df["最低"]) / df["昨收"].clip(lower=0.01) * 100).clip(0, 20)
            scores["amplitude"] = np.exp(-0.5 * ((amplitude - 3.5) / 2.5) ** 2)
        else:
            scores["amplitude"] = 0.5

        # --- 距涨跌停空间评分 ---
        # 空间越大越好（还有操作余地）
        if all(c in df.columns for c in ["最新价", "昨收"]):
            pre_close = df["昨收"].clip(lower=0.01)
            room_up = (pre_close * 1.1 - df["最新价"]) / pre_close * 100  # 距涨停%
            room_down = (df["最新价"] - pre_close * 0.9) / pre_close * 100  # 距跌停%
            # 取两者较小值作为操作空间
            room = pd.concat([room_up, room_down], axis=1).min(axis=1).clip(0, 10)
            scores["room"] = room / 10  # 归一化
        else:
            scores["room"] = 0.5

        # --- 涨跌动量评分 ---
        # bull: 正动量加分, bear: 负动量加分（逆向）
        if "涨跌幅" in df.columns:
            change = df["涨跌幅"].clip(-10, 10)
            if regime == "bull":
                # 牛市偏好: 正动量加分
                scores["change_momentum"] = (change + 10) / 20  # -10→0, +10→1
            elif regime == "bear":
                # 熊市偏好: 超跌反弹机会
                scores["change_momentum"] = (-change + 10) / 20  # -10→1, +10→0
            elif regime == "volatile":
                # 波动市偏好: 两极（大涨大跌都好）
                scores["change_momentum"] = 1 - abs(change) / 10  # 0%→1, ±10%→0
            else:
                # 震荡市: 偏好小幅正动量
                scores["change_momentum"] = np.exp(-0.5 * (change / 3) ** 2)
        else:
            scores["change_momentum"] = 0.5

        # 加权综合评分
        total_score = pd.Series(0.0, index=df.index)
        for factor, weight in self.factor_weights.items():
            if factor in scores.columns:
                total_score += scores[factor] * weight

        df = df.copy()
        df["score"] = total_score.round(4)

        # 按评分降序
        df = df.sort_values("score", ascending=False)
        return df

    # ==================== Stage 3: 分层取样 ====================

    def _stage3_diversify(self, df, max_count: int):
        """
        分层取样: 确保行业分散

        由于没有行业分类数据，用代码前缀做粗略分组:
        - 600xxx: 沪市主板
        - 601xxx: 沪市大盘蓝筹
        - 603xxx: 沪市中小板
        - 688xxx: 科创板
        - 000xxx: 深市主板
        - 001xxx: 深市主板
        - 002xxx: 深市中小板
        - 300xxx: 创业板
        """
        if df.empty:
            return []

        # 按代码前缀分组
        if "代码" in df.columns:
            df = df.copy()
            df["_prefix"] = df["代码"].astype(str).str[:3]
        else:
            # 无法分组时直接截断
            result = []
            for _, row in df.head(max_count).iterrows():
                result.append(self._row_to_dict(row))
            return result

        result = []
        prefix_counts = {}

        for _, row in df.iterrows():
            prefix = row.get("_prefix", "000")
            count = prefix_counts.get(prefix, 0)
            if count >= self.max_per_prefix:
                continue
            prefix_counts[prefix] = count + 1
            result.append(self._row_to_dict(row))
            if len(result) >= max_count:
                break

        return result

    def _row_to_dict(self, row) -> Dict:
        """DataFrame 行转为标准字典"""
        def _get(row, *keys):
            for k in keys:
                if k in row.index:
                    v = row[k]
                    if v is not None and str(v) != "nan":
                        return v
            return 0

        return {
            "code": str(_get(row, "代码", "code")),
            "name": str(_get(row, "名称", "name", "")),
            "price": float(_get(row, "最新价", "price", 0)),
            "change_pct": float(_get(row, "涨跌幅", "change_pct", 0)),
            "change": float(_get(row, "涨跌额", "change", 0)),
            "open": float(_get(row, "今开", "open", 0)),
            "high": float(_get(row, "最高", "high", 0)),
            "low": float(_get(row, "最低", "low", 0)),
            "pre_close": float(_get(row, "昨收", "pre_close", 0)),
            "volume": int(_get(row, "成交量", "volume", 0)),
            "amount": float(_get(row, "成交额", "amount", 0)),
            "turnover": float(_get(row, "换手率", "turnover", 0)),
            "score": float(round(_get(row, "score", 0), 4)),
        }


# 全局实例
stock_screener = StockScreener()
