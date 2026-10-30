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
        """获取股票所属板块（简化版：按代码前缀判断）"""
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
