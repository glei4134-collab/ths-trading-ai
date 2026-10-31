"""
板块轮动分析模块
分析当前热点板块，为选股提供参考
"""

import logging
from typing import Dict, List
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import timedelta

logger = logging.getLogger(__name__)


class SectorAnalyzer:
    """板块轮动分析器"""

    # 板块代码映射
    SECTOR_MAP = {
        "银行": "BK0475", "证券": "BK0473", "保险": "BK0474",
        "房地产": "BK0451", "医药": "BK0465", "白酒": "BK0896",
        "新能源": "BK1033", "光伏": "BK1034", "锂电池": "BK1035",
        "芯片": "BK1036", "人工智能": "BK1131", "半导体": "BK0985",
        "汽车": "BK0481", "军工": "BK0480", "消费": "BK0438",
        "科技": "BK0501", "周期": "BK0502", "防御": "BK0503",
    }

    def __init__(self):
        self._cache = {}
        self._cache_time = 0

    def get_hot_sectors(self, limit: int = 10) -> List[Dict]:
        """
        获取当前热点板块

        Returns: [{name, code, change_pct, turnover, leading_stock}, ...]
        """
        try:
            import time
            now = time.time()

            # 缓存10分钟
            if self._cache and now - self._cache_time < 600:
                return self._cache

            # 优先从新浪极速行业网关获取，东方财富作为备用
            sectors = self._fetch_sina_sectors()
            if not sectors:
                sectors = self._fetch_eastmoney_sectors()

            if sectors:
                # 按涨幅排序
                sectors.sort(key=lambda x: x.get("change_pct", 0), reverse=True)
                self._cache = sectors[:limit]
                self._cache_time = now
                return self._cache

            return []
        except Exception as e:
            logger.warning(f"获取热点板块失败: {e}")
            return []

    def _fetch_sina_sectors(self) -> List[Dict]:
        """从新浪财经获取行业板块数据（毫秒级极速响应，免鉴权不被拦截）"""
        try:
            import requests
            import json
            import re

            url = "http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
            headers = {
                "Referer": "https://finance.sina.com.cn",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
            resp = requests.get(url, headers=headers, timeout=5)
            resp.encoding = "gbk"
            m = re.search(r"var\s+\w+\s*=\s*(\{.*\})", resp.text)
            if not m:
                return []
            data = json.loads(m.group(1))
            sectors = []
            for k, v in data.items():
                parts = v.split(",")
                if len(parts) >= 6:
                    sectors.append({
                        "code": parts[0],
                        "name": parts[1],
                        "count": int(parts[2]),
                        "price": float(parts[3] or 0),
                        "change": float(parts[4] or 0),
                        "change_pct": float(parts[5] or 0),
                    })
            return sectors
        except Exception as e:
            logger.debug(f"新浪板块API暂不可用: {e}")
            return []

    def _fetch_eastmoney_sectors(self) -> List[Dict]:
        """从东方财富获取板块数据（备用通道）"""
        try:
            import requests

            url = "https://push2.eastmoney.com/api/qt/clist/get"
            params = {
                "pn": 1, "pz": 50, "po": 1, "np": 1,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2, "invt": 2, "fid": "f3",
                "fs": "m:90+t:2",  # 板块指数
                "fields": "f2,f3,f4,f12,f14",
            }
            headers = {
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }

            resp = requests.get(url, params=params, headers=headers, timeout=5)
            data = resp.json()

            if not data or "data" not in data:
                return []

            items = data["data"].get("diff", [])
            sectors = []

            for item in items:
                try:
                    sectors.append({
                        "code": item.get("f12", ""),
                        "name": item.get("f14", ""),
                        "price": float(item.get("f2", 0) or 0),
                        "change_pct": float(item.get("f3", 0) or 0),
                    })
                except (ValueError, TypeError):
                    continue

            return sectors
        except Exception as e:
            logger.debug(f"东方财富板块备用通道跳过: {e}")
            return []

    def get_sector_for_stock(self, symbol: str) -> str:
        """获取股票所属板块/行业（优先词库行业，降级为概念，最后降级为板位）"""
        try:
            from stock_keyword_db import stock_keyword_db
            ind = stock_keyword_db.get_stock_industry(symbol)
            if ind:
                return ind
            concepts = stock_keyword_db.get_stock_concepts(symbol)
            if concepts:
                return concepts[0]
        except Exception:
            pass

        if symbol.startswith("600") or symbol.startswith("601"):
            return "上海主板"
        elif symbol.startswith("688"):
            return "科创板"
        elif symbol.startswith("300"):
            return "创业板"
        elif symbol.startswith("000"):
            return "深圳主板"
        elif symbol.startswith("002"):
            return "中小板"
        return "其他"

    def check_stock_sector_resonance(self, symbol: str, stock_data: Dict = None, top_n: int = 5) -> Dict:
        """
        三级共振校验（大盘 × 板块 × 个股）：
        早盘集合竞价时段仅允许处于当日涨幅前 top_n 名主线板块（或主力净流入为正）的个股通过表决。

        Returns:
            {
                "passed": bool,
                "sector": str,
                "rank": Optional[int],
                "change_pct": float,
                "net_inflow": float,
                "reason": str
            }
        """
        stock_data = stock_data or {}
        # 1. 尝试从多渠道提取股票归属板块/概念
        sectors_to_check = []
        if stock_data.get("sector"):
            sectors_to_check.append(str(stock_data["sector"]))
        if stock_data.get("industry"):
            sectors_to_check.append(str(stock_data["industry"]))

        try:
            from stock_keyword_db import stock_keyword_db
            ind = stock_keyword_db.get_stock_industry(symbol)
            if ind and ind not in sectors_to_check:
                sectors_to_check.append(ind)
            concepts = stock_keyword_db.get_stock_concepts(symbol)
            for c in concepts:
                if c not in sectors_to_check:
                    sectors_to_check.append(c)
        except Exception:
            pass

        if not sectors_to_check:
            sectors_to_check.append(self.get_sector_for_stock(symbol))

        # 2. 获取当前前 30 名热点板块
        hot_sectors = self.get_hot_sectors(limit=30)
        if not hot_sectors:
            # 数据源离线或不可用时降级放行
            return {
                "passed": True,
                "sector": sectors_to_check[0] if sectors_to_check else "全市场",
                "rank": None,
                "change_pct": 0.0,
                "net_inflow": 0.0,
                "reason": "板块数据源暂时离线，三级共振降级放行"
            }

        matched_sector = None
        matched_rank = None
        matched_change = 0.0
        matched_inflow = 0.0

        for sec in sectors_to_check:
            for idx, s in enumerate(hot_sectors):
                s_name = s.get("name", "")
                if sec in s_name or s_name in sec:
                    matched_sector = s_name
                    matched_rank = idx + 1
                    matched_change = float(s.get("change_pct", 0) or 0)
                    matched_inflow = float(s.get("net_inflow", 0) or s.get("inflow", 0) or 0)
                    break
            if matched_sector:
                break

        if not matched_sector:
            matched_sector = sectors_to_check[0] if sectors_to_check else "未分类"

        # 判断通过条件：处于涨幅前 top_n 名，或主力净流入为正（或涨幅>1.2%）
        is_in_top_n = (matched_rank is not None and matched_rank <= top_n)
        is_net_inflow_pos = (matched_inflow > 0 or (matched_rank is not None and matched_change > 1.2))

        if is_in_top_n or is_net_inflow_pos:
            reason = f"三级共振成立: 标的所属板块[{matched_sector}]排名第{matched_rank or '>30'}名(涨幅{matched_change:+.2f}%, 净流入{matched_inflow:+.1f}万)"
            return {
                "passed": True,
                "sector": matched_sector,
                "rank": matched_rank,
                "change_pct": matched_change,
                "net_inflow": matched_inflow,
                "reason": reason
            }
        else:
            reason = f"三级共振否决: 标的所属板块[{matched_sector}]未进入涨幅前{top_n}(排第{matched_rank or '>30'}名, 涨幅{matched_change:+.2f}%)且净流入非正"
            return {
                "passed": False,
                "sector": matched_sector,
                "rank": matched_rank,
                "change_pct": matched_change,
                "net_inflow": matched_inflow,
                "reason": reason
            }

    def is_sector_hot(self, sector_name: str) -> bool:
        """判断板块是否是热点"""
        hot_sectors = self.get_hot_sectors(limit=10)
        hot_names = [s["name"] for s in hot_sectors]
        return sector_name in hot_names

    def get_sector_score(self, symbol: str) -> float:
        """
        获取板块得分（0-1）
        热点板块得1分，非热点得0.5分
        """
        sector = self.get_sector_for_stock(symbol)
        hot_sectors = self.get_hot_sectors(limit=10)

        for s in hot_sectors:
            if s["name"] == sector or sector in s["name"]:
                # 板块涨幅越大，得分越高
                change = s.get("change_pct", 0)
                return min(1.0, 0.5 + change / 10)

        return 0.5  # 非热点板块基础分

    def get_market_sentiment(self) -> Dict:
        """
        获取市场情绪

        Returns: {score, hot_sectors, cold_sectors, description}
        """
        sectors = self.get_hot_sectors(limit=20)

        if not sectors:
            return {
                "score": 0,
                "hot_sectors": [],
                "cold_sectors": [],
                "description": "无法获取板块数据"
            }

        # 计算平均涨幅
        avg_change = sum(s.get("change_pct", 0) for s in sectors) / len(sectors)

        # 热点板块（涨幅>1%）
        hot = [s for s in sectors if s.get("change_pct", 0) > 1]

        # 冷门板块（跌幅>1%）
        cold = [s for s in sectors if s.get("change_pct", 0) < -1]

        # 情绪得分
        if avg_change > 1:
            score = 80
            desc = "市场情绪亢奋，板块普涨"
        elif avg_change > 0:
            score = 60
            desc = "市场情绪偏暖，部分板块活跃"
        elif avg_change > -1:
            score = 40
            desc = "市场情绪平淡，板块分化"
        else:
            score = 20
            desc = "市场情绪低迷，板块普跌"

        return {
            "score": score,
            "avg_change": round(avg_change, 2),
            "hot_sectors": [s["name"] for s in hot[:5]],
            "cold_sectors": [s["name"] for s in cold[:5]],
            "description": desc
        }


# 全局实例
sector_analyzer = SectorAnalyzer()
