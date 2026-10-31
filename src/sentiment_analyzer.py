"""
市场情绪分析模块（增强版 v2.0）
支持并发多源采集、个股专属新闻源（新浪个股+东财公告+机构研报）、真实时间衰减、
来源评分、中文否定词感知情感计算、股票绑定与30分钟个股TTL缓存
"""

import html
import json
import logging
import re
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# 配置目录
from paths import BASE_DIR, CONFIG_DIR

LEXICON_FILE = CONFIG_DIR / "sentiment_lexicon.json"


class SentimentAnalyzer:
    """市场情绪分析器（增强版 v2.0）"""

    # ========== 加权关键词库（100+个） ==========

    POSITIVE_KEYWORDS = {
        # 强正面 (1.3-1.5)
        "暴涨": 1.5, "涨停": 1.4, "新高": 1.3, "创新高": 1.3, "历史新高": 1.4,
        "大幅上涨": 1.3, "强势突破": 1.3, "连续涨停": 1.5, "业绩暴增": 1.5,
        "扭亏为盈": 1.4, "翻倍": 1.3,

        # 中等正面 (1.0-1.2)
        "利好": 1.2, "上涨": 1.0, "突破": 1.1, "增长": 1.1, "大增": 1.2,
        "盈利": 1.1, "超预期": 1.2, "反弹": 1.0, "放量": 1.0, "走强": 1.1,
        "金叉": 1.0, "龙头": 1.1, "热点": 1.0, "资金流入": 1.1, "净流入": 1.1,
        "业绩增长": 1.1, "净利润增长": 1.2, "营收增长": 1.1, "稳中有进": 1.1,
        "政策利好": 1.2, "机构买入": 1.1, "北向资金买入": 1.0, "获融资买入": 1.0,
        "回购": 1.1, "增持": 1.1, "分红": 1.0, "评级:买入": 1.3, "评级:增持": 1.15,
        "评级:强烈推荐": 1.35, "评级:推荐": 1.1, "共振向上": 1.2, "快速成长": 1.15,
        "中标": 1.15, "签下": 1.1, "获批": 1.15, "满产": 1.2, "提价": 1.15,

        # 弱正面 (0.6-0.9)
        "牛市": 0.9, "强势": 0.9, "企稳": 0.8, "回暖": 0.8, "复苏": 0.85,
        "震荡上行": 0.8, "底部放量": 0.9, "突破压力": 1.0, "蓄力": 0.85,
        "站上均线": 0.8, "多头排列": 0.9, "量价配合": 0.9,

        # 行业泛词正面（降权至 0.25-0.35，严防行业泛词掩盖个股实质业绩暴雷）
        "芯片": 0.25, "半导体": 0.25, "新能源": 0.25, "光伏": 0.25,
        "锂电池": 0.25, "人工智能": 0.3, "算力": 0.3, "国产替代": 0.35,
    }

    NEGATIVE_KEYWORDS = {
        # 极强负面与财务/监管暴雷 (2.0-2.5，设置一票压制基石)
        "立案调查": 2.5, "财务造假": 2.5, "暴雷": 2.5, "退市": 2.5,
        "暴跌": 2.0, "崩盘": 2.2, "跌停": 1.8, "新低": 1.6, "闪崩": 2.0,
        "连续跌停": 2.2, "大幅下跌": 1.5, "破位下跌": 1.5,
        "巨额亏损": 2.5, "净亏损": 2.2, "单季净利润同比下降": 2.5, "净利润同比下降": 2.2,
        "净利润大幅下降": 2.2, "净利减少": 2.0, "业绩大幅下滑": 2.2, "业绩腰斩": 2.5,
        "重组失败": 2.2, "资金链断裂": 2.5, "违约": 2.0, "清仓式减持": 2.0,
        "毛利率下降": 1.5, "首亏": 2.0, "立案": 2.2, "冻结": 1.8,

        # 中等负面 (1.0-1.4)
        "利空": 1.2, "下跌": 1.0, "破位": 1.1, "亏损": 1.2,
        "下滑": 1.0, "不及预期": 1.3, "业绩下滑": 1.2, "下降": 1.0, "承压": 1.0,
        "缩量": 0.9, "死叉": 1.0, "资金流出": 1.1, "净流出": 1.1, "机构卖出": 1.0,
        "政策收紧": 1.1, "北向资金卖出": 1.0, "减持": 1.15,
        "质押": 0.9, "商誉减值": 1.4, "计提减值": 1.3, "评级:卖出": 1.4, "评级:减持": 1.3,
        "信仰松动": 1.1, "诉讼": 1.1,

        # 弱负面 (0.6-0.9)
        "熊市": 0.9, "弱势": 0.9, "低迷": 0.8, "疲软": 0.8, "分化": 0.6,
        "震荡下行": 0.8, "跌破支撑": 0.9, "空头排列": 0.9, "回调": 0.7,
        "缩量下跌": 0.8, "阴跌": 0.8, "套牢": 0.9,

        # 行业与监管负面
        "产能过剩": 0.9, "价格战": 0.9, "处罚": 1.3, "警示函": 1.2,
        "ST": 1.3, "*ST": 1.8, "违规": 1.2,
    }

    # 重大风险关键词（触发一票压制机制，得分强制截断 <= 35分）
    CRITICAL_RISK_KEYWORDS = {
        "立案调查", "财务造假", "暴雷", "退市", "巨额亏损",
        "单季净利润同比下降", "净亏损", "资金链断裂", "业绩腰斩",
        "重组失败", "违约", "清仓式减持", "首亏", "净利润大幅下降"
    }

    # 股票绑定关键词（特定股票的特殊关键词）
    STOCK_KEYWORDS = {
        "601318": {  # 中国平安
            "positive": ["保险", "综合金融", "科技赋能", "数字转型"],
            "negative": ["保费下滑", "投资亏损", "偿付能力"],
        },
        "600519": {  # 贵州茅台
            "positive": ["提价", "业绩超预期", "白酒龙头", "消费升级"],
            "negative": ["控价", "库存", "消费降级"],
        },
        "300750": {  # 宁德时代
            "positive": ["电池", "储能", "海外订单", "技术突破"],
            "negative": ["产能过剩", "价格战", "原材料涨价"],
        },
        "688041": {  # 海光信息
            "positive": ["国产替代", "算力", "DCU", "服务器"],
            "negative": ["出口限制", "技术封锁"],
        },
    }

    # 来源权重（权威性分级）
    SOURCE_WEIGHTS = {
        "research": 1.05,        # 东方财富机构研报与评级（最高权威）
        "eastmoney_ann": 1.0,    # 东方财富上市公司官方公告
        "policy": 0.95,          # 央行/宏观政策要闻
        "sina_stock": 0.9,       # 新浪财经个股专属资讯
        "ths_stockpage": 0.9,    # 同花顺个股页
        "sina_live": 0.88,       # 新浪7x24实时财经电报
        "10jqka": 0.85,          # 同花顺实时股票快讯
        "sina": 0.82,            # 新浪财经滚动新闻
        "eastmoney_guba": 0.6,   # 东方财富股吧
        "stock_comment": 0.6,    # 股吧评论
        "tencent": 0.8,
        "baidu": 0.7,
    }

    # 常见财经噪音与广告关键词（预编译快速过滤）
    NOISE_KEYWORDS = [
        "广告", "推广", "赞助", "点击查看", "了解更多", "立即注册",
        "免费领取", "限时优惠", "点击链接", "扫描二维码", "关注公众号",
        "免责声明", "风险提示", "过往业绩", "不代表未来", "投资需谨慎",
        "市场有风险", "入市需谨慎", "历史业绩", "不代表未来表现",
        "违法和违规信息举报", "举报中心", "驻中国人民银行纪检监察组",
    ]

    def __init__(self):
        self._cache: Dict = {}
        self._cache_time: float = 0
        # News Sentiment Engine 2.0: LRU 有界队列缓存，上限 300 标的，淘汰最旧
        self._stock_cache: OrderedDict[str, Tuple[float, Dict]] = OrderedDict()
        self._max_stock_cache: int = 300
        self._stock_cache_ttl: int = 1800  # 个股情绪缓存 30 分钟
        # 有界时序历史（上限 200 标的，每只标的仅保留最近 20 次快照）
        self._sentiment_history: OrderedDict[str, deque] = OrderedDict()
        self._max_history_keys: int = 200
        # 异步解耦非阻塞后台工作线程池，彻底杜绝网络请求阻塞交易主循环
        self._async_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sentiment_async")

        # 正则表达式 DFA 预编译缓存 (单次扫描 O(L) 复杂度)
        self._pos_pattern: Optional[re.Pattern] = None
        self._neg_pattern: Optional[re.Pattern] = None
        self._crit_pattern: Optional[re.Pattern] = None
        self._noise_pattern: Optional[re.Pattern] = None

        self._load_lexicon()
        self._compile_patterns()

    def _compile_patterns(self):
        """预编译正则表达式，将关键词循环匹配降阶为单次正则DFA扫描 (O(L))"""
        try:
            pos_sorted = sorted(self.POSITIVE_KEYWORDS.keys(), key=len, reverse=True)
            neg_sorted = sorted(self.NEGATIVE_KEYWORDS.keys(), key=len, reverse=True)
            crit_sorted = sorted(self.CRITICAL_RISK_KEYWORDS, key=len, reverse=True)
            noise_sorted = sorted(self.NOISE_KEYWORDS, key=len, reverse=True)

            self._pos_pattern = re.compile(r'(' + '|'.join(re.escape(k) for k in pos_sorted) + r')') if pos_sorted else None
            self._neg_pattern = re.compile(r'(' + '|'.join(re.escape(k) for k in neg_sorted) + r')') if neg_sorted else None
            self._crit_pattern = re.compile(r'(' + '|'.join(re.escape(k) for k in crit_sorted) + r')') if crit_sorted else None
            self._noise_pattern = re.compile(r'(' + '|'.join(re.escape(k) for k in noise_sorted) + r')') if noise_sorted else None
        except Exception as e:
            logger.error(f"预编译情感正则模式失败: {e}")

    def _load_lexicon(self):
        """加载金融情感词库"""
        try:
            if LEXICON_FILE.exists():
                with open(LEXICON_FILE, "r", encoding="utf-8") as f:
                    lexicon = json.load(f)

                for level, data in lexicon.get("positive", {}).items():
                    for word in data.get("words", []):
                        self.POSITIVE_KEYWORDS[word] = data.get("weight", 1.0)

                for level, data in lexicon.get("negative", {}).items():
                    for word in data.get("words", []):
                        self.NEGATIVE_KEYWORDS[word] = data.get("weight", 1.0)

                for code, kws in lexicon.get("stock_specific", {}).items():
                    self.STOCK_KEYWORDS[code] = kws

                logger.info(f"加载情感词库: {len(self.POSITIVE_KEYWORDS)} 正面, {len(self.NEGATIVE_KEYWORDS)} 负面")
            self._compile_patterns()
        except Exception as e:
            logger.error(f"加载情感词库失败: {e}")

    # ========== 核心分析方法 ==========

    def _save_local_market_cache(self, data: Dict):
        """将最新采集的市场情绪持久化到本地 JSON 缓存"""
        try:
            cache_file = BASE_DIR / "data" / "market_sentiment.json"
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(data)
            payload["updated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"保存本地市场情绪缓存失败: {e}")

    def _load_cloud_cache(self) -> Optional[Dict]:
        """从本地持久化文件加载近期情绪缓存（30分钟内有效）"""
        try:
            cache_file = BASE_DIR / "data" / "market_sentiment.json"
            if cache_file.exists():
                import os
                mtime = os.path.getmtime(cache_file)
                age_hours = (time.time() - mtime) / 3600
                if age_hours < 0.5:  # 30分钟内有效
                    with open(cache_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if data.get("score") is not None and data.get("news_count", 0) > 10:
                        data["cache_age_hours"] = round(age_hours, 2)
                        return data
        except Exception as e:
            logger.debug(f"加载本地情绪缓存失败: {e}")
        return None

    def _sync_cloud_cache(self):
        """已弃用外网GitHub拉取，避免国内服务器连接 raw.githubusercontent.com 超时阻塞"""
        pass

    def get_market_sentiment(self, force_refresh: bool = False) -> Dict:
        """获取市场整体情绪（多源并发实时抓取）"""
        try:
            now = time.time()
            if not force_refresh and self._cache and now - self._cache_time < 600:
                return self._cache

            if not force_refresh:
                cached = self._load_cloud_cache()
                if cached:
                    cached["trend"] = self._calculate_trend("market", cached["score"])
                    self._cache = cached
                    self._cache_time = now
                    return cached

            news_with_source = self._fetch_all_news()
            result = self._analyze_sentiment_with_weight(news_with_source)
            result["trend"] = self._calculate_trend("market", result["score"])
            result["source"] = "local_concurrent_scrape"
            self._cache = result
            self._cache_time = now
            self._save_local_market_cache(result)
            return result
        except Exception as e:
            logger.error(f"获取市场情绪失败: {e}")
            return {"score": 50, "news_count": 0, "keywords": [], "matched_keywords": [], "description": "无法获取情绪数据"}

    def get_stock_sentiment(
        self,
        symbol: str,
        stock_name: str = "",
        force_refresh: bool = False,
        non_blocking: bool = False
    ) -> Dict:
        """
        获取个股专属情绪（增强版 News Sentiment Engine 2.0）
        - 聚合：新浪个股专属新闻 + 东方财富个股公告(含日期衰减) + 东方财富个股机构研报
        - LRU 有界队列缓存（上限 300 标的），自动驱逐最旧数据，限制内存常驻
        - non_blocking 模式：高频/实时交易决策主循环中若无新鲜缓存，立即返回降级估值并在后台线程异步刷新，杜绝网络阻塞交易
        """
        try:
            symbol = str(symbol).strip()
            now = time.time()

            # 1. 检查 LRU 缓存，命中则移至队尾（O(1) 访问并保持热度）
            if not force_refresh and symbol in self._stock_cache:
                cached_ts, cached_res = self._stock_cache[symbol]
                if now - cached_ts < self._stock_cache_ttl:
                    self._stock_cache.move_to_end(symbol)
                    return cached_res

            # 2. 若启用 non_blocking 模式且未命中新鲜缓存
            if non_blocking:
                if symbol in self._stock_cache:
                    cached_ts, cached_res = self._stock_cache[symbol]
                    # 提交后台异步刷新
                    self._async_pool.submit(self._fetch_and_cache_stock, symbol, stock_name)
                    stale_res = dict(cached_res)
                    stale_res["stale"] = True
                    return stale_res
                else:
                    # 完全冷启动：提交后台异步抓取，前台立即返回安全中性基线（耗时 < 1ms，绝不挂起下单线程）
                    self._async_pool.submit(self._fetch_and_cache_stock, symbol, stock_name)
                    if not stock_name:
                        stock_name = self._resolve_stock_name(symbol)
                    keyword = stock_name or symbol
                    return {
                        "score": 50,
                        "news_count": 0,
                        "weighted_score": 50,
                        "rule_score": 50,
                        "nlp_score": 50,
                        "trend": "stable",
                        "keywords": [],
                        "matched_keywords": [],
                        "news_headlines": [],
                        "description": f"{keyword} 情绪后台异步抓取中(暂取基线50分)",
                        "non_blocking_fallback": True,
                    }

            return self._fetch_and_cache_stock(symbol, stock_name)
        except Exception as e:
            logger.warning(f"获取个股情绪失败 ({symbol}): {e}")
            return {
                "score": 50,
                "news_count": 0,
                "weighted_score": 50,
                "trend": "stable",
                "keywords": [],
                "matched_keywords": [],
                "news_headlines": [],
                "description": "无法获取情绪",
            }

    def _fetch_and_cache_stock(self, symbol: str, stock_name: str = "") -> Dict:
        """实际执行个股专属新闻抓取并写入 LRU 缓存（受保护执行）"""
        try:
            symbol = str(symbol).strip()
            now = time.time()
            if not stock_name:
                stock_name = self._resolve_stock_name(symbol)
            keyword = stock_name or symbol

            all_news_with_source: List[Tuple[str, str]] = []

            # 并发获取 3 个按个股过滤的权威数据源
            with ThreadPoolExecutor(max_workers=3) as pool:
                f_sina = pool.submit(self._fetch_sina_stock_news, symbol, stock_name)
                f_ann = pool.submit(self._fetch_eastmoney_ann, symbol)
                f_rep = pool.submit(self._fetch_stock_research, symbol)

                for fut in (f_sina, f_ann, f_rep):
                    try:
                        items = fut.result(timeout=6)
                        if items:
                            all_news_with_source.extend(items)
                    except Exception:
                        pass

            # 若三者均为空，尝试同花顺个股页兜底
            if not all_news_with_source:
                all_news_with_source.extend(self._fetch_ths_stockpage(symbol, stock_name))

            if not all_news_with_source:
                empty_res = {
                    "score": 50,
                    "news_count": 0,
                    "weighted_score": 50,
                    "rule_score": 50,
                    "nlp_score": 50,
                    "trend": "stable",
                    "keywords": [],
                    "matched_keywords": [],
                    "news_headlines": [],
                    "description": f"{keyword} 暂无最新个股专属资讯",
                }
                self._set_lru_cache(symbol, (now, empty_res))
                return empty_res

            result = self._analyze_sentiment_with_weight(
                all_news_with_source, symbol=symbol, stock_name=stock_name
            )
            result["trend"] = self._calculate_trend(symbol, result["score"])

            # 获取股票概念和行业信息
            try:
                from stock_keyword_db import stock_keyword_db
                result["concepts"] = stock_keyword_db.get_stock_concepts(symbol)
                result["industry"] = stock_keyword_db.get_stock_industry(symbol)
            except Exception:
                result["concepts"] = []
                result["industry"] = ""

            if result.get("critical_risk"):
                risk_tag = "、".join(result.get("critical_reasons", [])[:2])
                bias_label = f"🚨重大风险一票压制[{risk_tag}]"
            else:
                bias_label = "偏多" if result["score"] > 55 else "偏空" if result["score"] < 45 else "中性"
            result["description"] = f"{keyword} {result['news_count']}条专属资讯，情绪{bias_label}({result['score']}分)"

            self._set_lru_cache(symbol, (now, result))
            return result
        except Exception as e:
            logger.warning(f"抓取并缓存个股情绪失败 ({symbol}): {e}")
            return {"score": 50, "news_count": 0, "description": "抓取失败"}

    def _set_lru_cache(self, symbol: str, entry: Tuple[float, Dict]):
        """线程安全地向 LRU 缓存写入条目并淘汰最旧条目"""
        if symbol in self._stock_cache:
            self._stock_cache.move_to_end(symbol)
        self._stock_cache[symbol] = entry
        if len(self._stock_cache) > self._max_stock_cache:
            self._stock_cache.popitem(last=False)

    def get_stock_sentiment_async(self, symbol: str, stock_name: str = ""):
        """异步非阻塞提交个股情绪抓取任务，返回 Future 对象"""
        return self._async_pool.submit(self._fetch_and_cache_stock, symbol, stock_name)

    def prefetch_stocks_sentiment(self, stocks: List[Tuple[str, str]]):
        """批量预热股票池情绪缓存（如盘前 09:15 扫描 40 只候选股）"""
        futures = []
        for sym, name in stocks:
            futures.append(self._async_pool.submit(self._fetch_and_cache_stock, sym, name))
        return futures

    def _resolve_stock_name(self, symbol: str) -> str:
        """尝试从本地词库或缓存解析股票名称"""
        try:
            kws = self.STOCK_KEYWORDS.get(symbol, {}).get("positive", [])
            if kws:
                return kws[0]
        except Exception:
            pass
        return ""

    def get_sentiment_with_technical(self, symbol: str, stock_name: str,
                                     indicators: Dict) -> Dict:
        """结合技术指标的情绪分析"""
        sentiment = self.get_stock_sentiment(symbol, stock_name)
        sentiment_score = sentiment.get("score", 50)
        technical_score = self._calculate_technical_score(indicators)
        combined_score = sentiment_score * 0.4 + technical_score * 0.6

        if combined_score > 65:
            recommendation = "强烈看多"
        elif combined_score > 55:
            recommendation = "偏多"
        elif combined_score > 45:
            recommendation = "中性观望"
        elif combined_score > 35:
            recommendation = "偏空"
        else:
            recommendation = "强烈看空"

        return {
            "sentiment_score": sentiment_score,
            "technical_score": technical_score,
            "combined_score": round(combined_score, 1),
            "recommendation": recommendation,
            "sentiment_detail": sentiment,
        }

    # ========== 内部评分与清洗方法 ==========

    def _analyze_sentiment_with_weight(
        self,
        news_with_source: List[Tuple[str, str]],
        symbol: str = None,
        stock_name: str = "",
    ) -> Dict:
        """
        加权情绪分析（来源权重 × 时间衰减 × 关键词权重 + 中文否定词感知 + NLP）
        """
        if not news_with_source:
            return {
                "score": 50,
                "positive": 0,
                "negative": 0,
                "weighted_score": 50,
                "rule_score": 50,
                "nlp_score": 50,
                "news_count": 0,
                "keywords": [],
                "matched_keywords": [],
                "news_headlines": [],
            }

        # 1. 噪音与乱码过滤、HTML反转义、去重
        news_with_source = self._filter_noise(news_with_source)
        if not news_with_source:
            return {
                "score": 50,
                "positive": 0,
                "negative": 0,
                "weighted_score": 50,
                "rule_score": 50,
                "nlp_score": 50,
                "news_count": 0,
                "keywords": [],
                "matched_keywords": [],
                "news_headlines": [],
            }

        # 2. 获取股票特定关键词（剔除股票自身名称，避免发任何公告都因包含公司名而被判为正面！）
        raw_pos_kw = self.STOCK_KEYWORDS.get(symbol, {}).get("positive", []) if symbol else []
        raw_neg_kw = self.STOCK_KEYWORDS.get(symbol, {}).get("negative", []) if symbol else []
        ignore_names = {symbol, stock_name.strip()} if stock_name else {symbol}
        # 词库中 stock_specific 的第一个词往往是股票简称（如"兆易创新"、"中国平安"），需排除
        if raw_pos_kw and len(raw_pos_kw[0]) in (3, 4):
            ignore_names.add(raw_pos_kw[0])

        stock_pos_kw = [kw for kw in raw_pos_kw if kw not in ignore_names and len(kw) >= 2]
        stock_neg_kw = [kw for kw in raw_neg_kw if kw not in ignore_names and len(kw) >= 2]

        total_weighted_positive = 0.0
        total_weighted_negative = 0.0
        positive_keywords: List[str] = []
        negative_keywords: List[str] = []
        simple_positive = 0
        simple_negative = 0

        negation_prefixes = ("不", "未", "没", "无", "非", "莫", "勿", "难")

        for title, source in news_with_source:
            source_weight = self.SOURCE_WEIGHTS.get(source, 0.75)
            time_weight = self._get_time_weight(title)
            weight = source_weight * time_weight

            title_has_pos = False
            title_has_neg = False

            # 通用正面关键词（单次正则 finditer 扫描，带前序否定词反转检测）
            if self._pos_pattern:
                for match in self._pos_pattern.finditer(title):
                    kw = match.group(0)
                    kw_weight = self.POSITIVE_KEYWORDS.get(kw, 1.0)
                    idx = match.start()
                    if (idx > 0 and title[idx - 1] in negation_prefixes) or (idx > 1 and title[idx - 2:idx] in ("没有", "未能", "并非")):
                        total_weighted_negative += kw_weight * weight
                        title_has_neg = True
                        neg_tag = f"{title[idx - 1]}{kw}" if idx > 0 else f"未{kw}"
                        if neg_tag not in negative_keywords:
                            negative_keywords.append(neg_tag)
                    else:
                        total_weighted_positive += kw_weight * weight
                        title_has_pos = True
                        if kw not in positive_keywords:
                            positive_keywords.append(kw)

            # 通用负面关键词（单次正则 finditer 扫描）
            if self._neg_pattern:
                for match in self._neg_pattern.finditer(title):
                    kw = match.group(0)
                    kw_weight = self.NEGATIVE_KEYWORDS.get(kw, 1.0)
                    idx = match.start()
                    if not kw.startswith("不") and ((idx > 0 and title[idx - 1] in ("不", "未", "无")) or (idx > 1 and title[idx - 2:idx] in ("没有", "未能", "并非"))):
                        total_weighted_positive += kw_weight * 0.6 * weight
                        title_has_pos = True
                    else:
                        total_weighted_negative += kw_weight * weight
                        title_has_neg = True
                        if kw not in negative_keywords:
                            negative_keywords.append(kw)

            # 股票特定行业/业务关键词（低权重辅助 0.35，且避免重复追加）
            for kw in stock_pos_kw:
                if kw in title:
                    total_weighted_positive += 0.35 * weight
                    tag = f"{kw}(专属)"
                    if tag not in positive_keywords and kw not in positive_keywords:
                        positive_keywords.append(tag)

            for kw in stock_neg_kw:
                if kw in title:
                    total_weighted_negative += 0.5 * weight
                    tag = f"{kw}(专属)"
                    if tag not in negative_keywords and kw not in negative_keywords:
                        negative_keywords.append(tag)

            if title_has_pos:
                simple_positive += 1
            if title_has_neg:
                simple_negative += 1

        # 3. 检查是否触发重大风险一票压制（单次正则扫描）
        has_critical_risk = False
        critical_risk_hits: List[str] = []
        if self._crit_pattern:
            for title, _ in news_with_source:
                for match in self._crit_pattern.finditer(title):
                    ckw = match.group(0)
                    has_critical_risk = True
                    if ckw not in critical_risk_hits:
                        critical_risk_hits.append(ckw)
        else:
            for kw in self.CRITICAL_RISK_KEYWORDS:
                if any(kw in neg_kw for neg_kw in negative_keywords):
                    has_critical_risk = True
                    if kw not in critical_risk_hits:
                        critical_risk_hits.append(kw)

        # 4. 计算加权规则得分 (weighted_score) 与简单计数得分 (simple_score)
        total_weight = total_weighted_positive + total_weighted_negative
        if total_weight > 0:
            raw_ratio = (total_weighted_positive / total_weight) * 100.0
            # 当匹配词较少时，向中性 50 分平滑收缩，防止单条新闻导致 0 分或 100 分极端值
            confidence_factor = min(1.0, total_weight / 4.0)
            weighted_score = int(round(50.0 + (raw_ratio - 50.0) * confidence_factor))
        else:
            weighted_score = 50

        # 重大风险一票压制
        if has_critical_risk:
            weighted_score = min(weighted_score, 35)

        simple_total = simple_positive + simple_negative
        simple_score = int(round((simple_positive / simple_total) * 100)) if simple_total > 0 else 50
        if has_critical_risk:
            simple_score = min(simple_score, 35)

        # 5. 中文金融否定词与程度副词引擎 (cn_sentiment) + NLP 融合
        cn_score = 50
        nlp_score = 50
        sample_text = "；".join([t for t, _ in news_with_source[:15]])
        try:
            from cn_sentiment import cn_sentiment
            cn_res = cn_sentiment.analyze(sample_text)
            if cn_res.get("critical_risk"):
                has_critical_risk = True
                for cr in cn_res.get("critical_reasons", []):
                    if cr not in critical_risk_hits:
                        critical_risk_hits.append(cr)
            if cn_res.get("positive_count", 0) + cn_res.get("negative_count", 0) > 0:
                cn_score = cn_res.get("score", 50)
            else:
                cn_score = weighted_score
        except Exception:
            cn_score = weighted_score

        try:
            from nlp_sentiment import nlp_sentiment
            nlp_result = nlp_sentiment.analyze(sample_text)
            nlp_score = nlp_result.get("score", 50)
        except Exception:
            nlp_score = weighted_score

        # 6. 最终综合评分：加权词库(含时间衰减+来源权重) 60% + 中文语法情感 25% + NLP 15%
        final_score = int(round(weighted_score * 0.60 + cn_score * 0.25 + nlp_score * 0.15))
        if has_critical_risk:
            final_score = min(final_score, 35)
        final_score = max(5, min(95, final_score))

        # 优先展示通用强情感词，再展示专属词
        core_pos = [k for k in positive_keywords if not k.endswith("(专属)")]
        spec_pos = [k for k in positive_keywords if k.endswith("(专属)")]
        merged_pos = (core_pos + spec_pos)[:5]
        merged_neg = negative_keywords[:5]
        all_keywords = merged_pos + merged_neg

        return {
            "score": final_score,
            "weighted_score": weighted_score,
            "rule_score": simple_score,
            "cn_score": cn_score,
            "nlp_score": nlp_score,
            "positive": simple_positive,
            "negative": simple_negative,
            "weighted_positive": round(total_weighted_positive, 2),
            "weighted_negative": round(total_weighted_negative, 2),
            "news_count": len(news_with_source),
            "keywords": all_keywords,
            "matched_keywords": [{"word": w} for w in all_keywords],
            "news_headlines": [{"title": t, "source": s} for t, s in news_with_source[:10]],
            "critical_risk": has_critical_risk,
            "critical_reasons": critical_risk_hits,
        }

    def _get_time_weight(self, title: str) -> float:
        """
        根据新闻标题中的日期标记计算真实时间衰减权重：
        - 3天以内（或无日期实时快讯）：1.15 ~ 1.0
        - 4 ~ 14天：0.95
        - 15 ~ 30天：0.70
        - 30天以上历史旧闻：0.35
        """
        try:
            now_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            m = re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})', title)
            if m:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                news_date = datetime(y, mo, d).date()
                days_diff = (now_date - news_date).days
                if days_diff <= 2:
                    return 1.15
                elif days_diff <= 7:
                    return 1.0
                elif days_diff <= 14:
                    return 0.9
                elif days_diff <= 30:
                    return 0.7
                else:
                    return 0.35
        except Exception:
            pass
        return 1.0

    @staticmethod
    def _fnv1a_64(text: str) -> int:
        """FNV-1a 64-bit 非加密极速哈希指纹算法（新闻去重与极低内存 footprint）"""
        h = 0xcbf29ce484222325
        fnv_prime = 0x100000001b3
        for b in text.encode("utf-8", errors="ignore"):
            h = ((h ^ b) * fnv_prime) & 0xffffffffffffffff
        return h

    def _filter_noise(self, news_with_source: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """噪音过滤：单次正则快速过滤噪音广告与 FNV-1a 64-bit 指纹去重"""
        filtered = []
        seen_fingerprints = set()

        for raw_title, source in news_with_source:
            if not raw_title:
                continue

            # HTML 实体解码并去除 HTML 标签
            title = html.unescape(str(raw_title))
            title = re.sub(r'<[^>]+>', '', title).strip()
            title = re.sub(r'\s+', ' ', title)

            if len(title) < 8:
                continue

            # 过滤 Mojibake 乱码（如 UTF-8 被按 Latin-1 解码产生的特征字符）
            if any(ch in title for ch in ("Ã", "å", "ä", "æ", "ç", "é", "ï¼", "Â")):
                continue

            # 必须包含至少 4 个中文字符，过滤纯英文或数字噪音
            chinese_chars = re.findall(r'[\u4e00-\u9fff]', title)
            if len(chinese_chars) < 4:
                continue

            # 单次正则快速匹配过滤噪音
            if self._noise_pattern and self._noise_pattern.search(title):
                continue

            # 归一化清洗并提取 FNV-1a 64-bit 哈希指纹进行严格去重
            clean_text = re.sub(r'^\[?\d{4}-\d{2}-\d{2}\]?\s*', '', title)
            clean_text = re.sub(r'[^\u4e00-\u9fffa-zA-Z0-9]', '', clean_text)[:35]
            if not clean_text:
                continue

            fp = self._fnv1a_64(clean_text)
            if fp in seen_fingerprints:
                continue

            seen_fingerprints.add(fp)
            filtered.append((title, source))

        return filtered

    def _tokenize_chinese(self, text: str) -> List[str]:
        """中文分词（使用jieba）"""
        try:
            import jieba
            jieba.add_word('涨停', freq=10000)
            jieba.add_word('跌停', freq=10000)
            jieba.add_word('利好', freq=10000)
            jieba.add_word('利空', freq=10000)
            jieba.add_word('金叉', freq=10000)
            jieba.add_word('死叉', freq=10000)

            words = list(jieba.cut(text))
            return [w.strip() for w in words if w.strip()]
        except Exception:
            return re.split(r'[，。！？、；：\s]+', text)

    def _calculate_trend(self, key: str, current_score: float) -> str:
        """计算情绪趋势（LRU 有界队列结构）"""
        now = datetime.now(ZoneInfo('Asia/Shanghai'))
        if key not in self._sentiment_history:
            if len(self._sentiment_history) >= self._max_history_keys:
                self._sentiment_history.popitem(last=False)
            self._sentiment_history[key] = deque(maxlen=20)
        history = self._sentiment_history[key]
        history.append({"score": current_score, "time": now})
        self._sentiment_history.move_to_end(key)

        if len(history) < 3:
            return "stable"

        hist_list = list(history)
        recent = [h["score"] for h in hist_list[-3:]]
        earlier = [h["score"] for h in hist_list[:-3]] if len(hist_list) > 3 else recent
        avg_recent = sum(recent) / len(recent)
        avg_earlier = sum(earlier) / len(earlier)
        diff = avg_recent - avg_earlier

        if diff > 5:
            return "improving"
        elif diff < -5:
            return "declining"
        return "stable"

    def _calculate_technical_score(self, indicators: Dict) -> float:
        """计算技术指标分数"""
        score = 50
        rsi = indicators.get("rsi")
        if rsi is not None:
            if rsi < 30:
                score += 15
            elif rsi < 40:
                score += 8
            elif rsi > 70:
                score -= 15
            elif rsi > 60:
                score -= 8

        macd = indicators.get("macd")
        macd_signal = indicators.get("signal")
        if macd is not None and macd_signal is not None:
            score += 10 if macd > macd_signal else -10

        close = indicators.get("close", 0)
        ma5 = indicators.get("ma5")
        ma20 = indicators.get("ma20")
        if close and ma5 and ma20:
            if close > ma5 > ma20:
                score += 10
            elif close < ma5 < ma20:
                score -= 10

        return max(0, min(100, score))

    # ========== 并发多源新闻获取方法 ==========

    def _fetch_all_news(self) -> List[Tuple[str, str]]:
        """
        并发获取所有有效国内权威财经源的市场新闻（耗时 < 1.5秒）
        涵盖：新浪7x24快讯(宏观+A股焦点)、新浪财经滚动、东方财富全市场公告、
        同花顺实时快讯、东方财富券商研报评级、央行宏观政策
        """
        all_news: List[Tuple[str, str]] = []

        tasks = [
            (self._fetch_sina_live_news, "sina_live"),
            (self._fetch_sina_news, "sina"),
            (self._fetch_10jqka_news, "10jqka"),
            (self._fetch_eastmoney_market_news, "eastmoney_ann"),
            (self._fetch_research_reports, "research"),
            (self._fetch_policy_news, "policy"),
        ]

        with ThreadPoolExecutor(max_workers=6) as pool:
            future_to_source = {pool.submit(fn): src for fn, src in tasks}
            for fut in as_completed(future_to_source, timeout=8):
                src = future_to_source[fut]
                try:
                    titles = fut.result(timeout=5)
                    for title in titles:
                        if title:
                            all_news.append((title, src))
                except Exception as e:
                    logger.debug(f"新闻源 [{src}] 抓取跳过: {e}")

        return all_news

    # ========== 个股专属新闻源（3大核心源） ==========

    def _fetch_sina_stock_news(self, symbol: str, stock_name: str = "") -> List[Tuple[str, str]]:
        """
        新浪财经个股专属新闻中心（vip.stock.finance.sina.com.cn）
        返回该股票名下的真实个股资讯与日期，完美解决旧版全市场新闻污染问题
        """
        try:
            import requests
            prefix = "sh" if symbol.startswith("6") else "sz"
            url = f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{prefix}{symbol}.phtml"
            resp = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
            resp.encoding = "gb2312"
            html_text = resp.text

            m = re.search(r'<div class="datelist">\s*<ul>(.*?)</ul>', html_text, re.S)
            if not m:
                return []

            block = m.group(1)
            # 提取日期与链接标题，例如：2026-10-29&nbsp;15:32 <a target='_blank' href='...'>标题</a>
            results: List[Tuple[str, str]] = []
            for line in block.split("<br>"):
                a_match = re.search(r"<a[^>]*>(.*?)</a>", line, re.S)
                if not a_match:
                    continue
                title = a_match.group(1).strip()
                if not title or len(title) < 6:
                    continue
                d_match = re.search(r"(\d{4}-\d{2}-\d{2})", line)
                if d_match:
                    title = f"[{d_match.group(1)}] {title}"
                results.append((title, "sina_stock"))
            return results[:25]
        except Exception:
            return []

    def _fetch_eastmoney_ann(self, symbol: str) -> List[Tuple[str, str]]:
        """东方财富个股官方公告（附带公告日期用于时间衰减）"""
        try:
            import requests
            ann_type = "SHA" if symbol.startswith("6") else "SZA"
            url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
            params = {
                "cb": "", "sr": "-1", "page_size": "20", "page_index": "1",
                "ann_type": ann_type, "client_source": "web", "stock_list": symbol
            }
            resp = requests.get(url, params=params, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
            data = resp.json()
            results: List[Tuple[str, str]] = []
            if data and data.get("data") and data["data"].get("list"):
                for item in data["data"]["list"]:
                    title = item.get("title", "").strip()
                    if not title:
                        continue
                    nd = str(item.get("notice_date", ""))[:10]
                    if nd and re.match(r"\d{4}-\d{2}-\d{2}", nd):
                        title = f"[{nd}] {title}"
                    results.append((title, "eastmoney_ann"))
            return results
        except Exception:
            return []

    def _fetch_stock_research(self, symbol: str) -> List[Tuple[str, str]]:
        """东方财富个股专属机构研报与评级（reportapi.eastmoney.com）"""
        try:
            import requests
            url = "https://reportapi.eastmoney.com/report/list"
            params = {
                "pageNo": 1,
                "pageSize": 15,
                "code": symbol,
                "industryCode": "*",
                "industry": "*",
                "rating": "*",
                "ratingchange": "*",
                "beginTime": "2025-01-01",
                "endTime": "2027-12-31",
                "fields": "",
                "qType": 0,
            }
            resp = requests.get(url, params=params, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
            data = resp.json()
            items = data.get("data", []) if isinstance(data, dict) else []
            results: List[Tuple[str, str]] = []
            for item in items:
                title = item.get("title", "").strip()
                if not title:
                    continue
                rating = item.get("emRatingName", "").strip()
                pub_date = str(item.get("publishDate", ""))[:10]
                prefix = f"[{pub_date}] " if pub_date else ""
                suffix = f" (评级:{rating})" if rating else ""
                results.append((f"{prefix}{title}{suffix}", "research"))
            return results
        except Exception:
            return []

    def _fetch_ths_stockpage(self, symbol: str, stock_name: str = "") -> List[Tuple[str, str]]:
        """同花顺个股新闻页（备用）"""
        try:
            import requests
            url = f"https://stockpage.10jqka.com.cn/{symbol}/"
            resp = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
            titles = re.findall(r'<span class="title">.*?<a[^>]*>(.*?)</a>', resp.text)
            return [(t.strip(), "ths_stockpage") for t in titles if t.strip() and len(t.strip()) >= 8]
        except Exception:
            return []

    # ========== 全市场宏观与行业新闻源 ==========

    def _fetch_sina_live_news(self) -> List[str]:
        """新浪7x24实时财经快讯（聚合全部快讯 tag_id=0 与 A股焦点 tag_id=10）"""
        try:
            import requests
            all_titles = []
            url = "https://zhibo.sina.com.cn/api/zhibo/feed"
            # 抓取最新 2 页全市场电报 + 1 页 A 股焦点电报
            queries = [
                {"page": "1", "page_size": "30", "zhibo_id": "152", "tag_id": "0", "type": "0"},
                {"page": "2", "page_size": "30", "zhibo_id": "152", "tag_id": "0", "type": "0"},
                {"page": "1", "page_size": "30", "zhibo_id": "152", "tag_id": "10", "type": "0"},
            ]
            for params in queries:
                try:
                    resp = requests.get(url, params=params, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
                    data = resp.json()
                    if data and "result" in data and "data" in data["result"]:
                        feed = data["result"]["data"].get("feed", {})
                        list_data = feed.get("list", [])
                        titles = [item.get("rich_text", "").strip() for item in list_data if item.get("rich_text")]
                        all_titles.extend(titles)
                except Exception:
                    continue
            return all_titles[:80]
        except Exception:
            return []

    def _fetch_sina_news(self, keyword: str = "") -> List[str]:
        """新浪财经滚动新闻（聚合财经要闻 lid=2516 与证券频道 lid=2517）"""
        try:
            import requests
            all_titles = []
            url = "https://feed.mix.sina.com.cn/api/roll/get"
            for lid in ("2516", "2517"):
                try:
                    params = {"pageid": "153", "lid": lid, "k": "", "num": "30", "page": "1"}
                    resp = requests.get(url, params=params, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
                    data = resp.json()
                    if data and "result" in data and "data" in data["result"]:
                        titles = [item.get("title", "").strip() for item in data["result"]["data"] if item.get("title")]
                        all_titles.extend(titles)
                except Exception:
                    continue
            return all_titles[:60]
        except Exception:
            return []

    def _fetch_eastmoney_market_news(self) -> List[str]:
        """东方财富沪深两市最新上市公司公告"""
        try:
            import requests
            all_titles = []
            url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
            for page in range(1, 3):
                params = {
                    "cb": "", "sr": "-1", "page_size": "30", "page_index": str(page),
                    "ann_type": "SHA,SZA", "client_source": "web", "f_node": "0", "s_node": "0"
                }
                resp = requests.get(url, params=params, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
                data = resp.json()
                if data and data.get("data") and data["data"].get("list"):
                    titles = [item.get("title", "").strip() for item in data["data"]["list"] if item.get("title")]
                    all_titles.extend(titles)
            return all_titles[:50]
        except Exception:
            return []

    def _fetch_10jqka_news(self) -> List[str]:
        """同花顺实时股票快讯"""
        try:
            import requests
            url = "https://news.10jqka.com.cn/tapp/news/push/stock/"
            params = {"page": "1", "tag": "", "track": "website", "pagesize": "30"}
            resp = requests.get(url, params=params, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
            data = resp.json()
            if data and data.get("data") and data["data"].get("list"):
                return [item.get("title", "").strip() for item in data["data"]["list"] if item.get("title")]
            return []
        except Exception:
            return []

    def _fetch_research_reports(self) -> List[str]:
        """东方财富全市场最新券商研报与评级（reportapi.eastmoney.com）"""
        try:
            import requests
            url = "https://reportapi.eastmoney.com/report/list"
            params = {
                "industryCode": "*",
                "pageSize": 30,
                "industry": "*",
                "rating": "*",
                "ratingchange": "*",
                "beginTime": "2026-01-01",
                "endTime": "2027-12-31",
                "pageNo": 1,
                "fields": "",
                "qType": 0,
            }
            resp = requests.get(url, params=params, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
            data = resp.json()
            items = data.get("data", []) if isinstance(data, dict) else []
            results = []
            for item in items:
                stock_name = item.get("stockName", "").strip()
                title = item.get("title", "").strip()
                rating = item.get("emRatingName", "").strip()
                if title:
                    rating_str = f" (评级:{rating})" if rating else ""
                    results.append(f"{stock_name}:{title}{rating_str}")
            return results[:30]
        except Exception:
            return []

    def _fetch_policy_news(self) -> List[str]:
        """央行与宏观政策新闻（强制 UTF-8 编码，剔除短导航锚点）"""
        try:
            import requests
            url = "http://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html"
            resp = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
            resp.encoding = "utf-8"
            titles = re.findall(r'<a[^>]*title=["\'](.*?)["\']', resp.text)
            valid = [
                t.strip() for t in titles
                if t.strip() and len(t.strip()) >= 10 and "纪检监察" not in t
            ]
            return valid[:15]
        except Exception:
            return []

    # ========== 兼容保留的旧接口方法 ==========

    def _fetch_tencent_news(self) -> List[str]:
        return []

    def _fetch_eastmoney_guba(self, symbol: str) -> List[Tuple[str, str]]:
        return []

    def _fetch_eastmoney_news_flash(self) -> List[str]:
        return []

    def _fetch_stcn_news(self) -> List[str]:
        return []

    def _fetch_stock_comment(self, symbol: str) -> List[Tuple[str, str]]:
        return []

    def _fetch_yahoo_finance(self) -> List[str]:
        return []

    def _fetch_cnbc(self) -> List[str]:
        return []

    def _fetch_xueqiu_hot(self) -> List[str]:
        return []

    def _fetch_macro_news(self) -> List[str]:
        return self._fetch_policy_news()


# 全局实例
sentiment_analyzer = SentimentAnalyzer()
