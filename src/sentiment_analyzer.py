"""
市场情绪分析模块（增强版）
支持加权关键词、时间衰减、来源评分、技术面结合、情绪趋势、股票绑定
基于金融情感词库
"""

import logging
import re
import json
import time
from typing import Dict, List, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# 配置目录
from paths import CONFIG_DIR
LEXICON_FILE = CONFIG_DIR / "sentiment_lexicon.json"


class SentimentAnalyzer:
    """市场情绪分析器（增强版）"""

    # ========== 加权关键词库（100+个） ==========

    POSITIVE_KEYWORDS = {
        # 强正面 (1.3-1.5)
        "暴涨": 1.5, "涨停": 1.4, "新高": 1.3, "创新高": 1.3,
        "大幅上涨": 1.3, "强势突破": 1.3, "连续涨停": 1.5,

        # 中等正面 (1.0-1.2)
        "利好": 1.2, "上涨": 1.0, "突破": 1.1, "增长": 1.1,
        "盈利": 1.1, "超预期": 1.2, "反弹": 1.0, "放量": 1.0,
        "金叉": 1.0, "龙头": 1.1, "热点": 1.0, "资金流入": 1.1,
        "业绩增长": 1.1, "净利润增长": 1.2, "营收增长": 1.1,
        "政策利好": 1.2, "机构买入": 1.1, "北向资金买入": 1.0,
        "回购": 1.1, "增持": 1.1, "分红": 1.0,

        # 弱正面 (0.6-0.9)
        "牛市": 0.9, "强势": 0.9, "企稳": 0.8, "回暖": 0.8,
        "震荡上行": 0.8, "底部放量": 0.9, "突破压力": 1.0,
        "站上均线": 0.8, "多头排列": 0.9, "量价配合": 0.9,

        # 行业正面
        "芯片": 0.8, "半导体": 0.8, "新能源": 0.9, "光伏": 0.8,
        "锂电池": 0.9, "人工智能": 1.0, "5G": 0.8, "军工": 0.8,
    }

    NEGATIVE_KEYWORDS = {
        # 强负面 (1.3-1.5)
        "暴跌": 1.5, "崩盘": 1.5, "跌停": 1.4, "新低": 1.3,
        "大幅下跌": 1.3, "破位下跌": 1.3, "连续跌停": 1.5,

        # 中等负面 (1.0-1.2)
        "利空": 1.2, "下跌": 1.0, "破位": 1.1, "亏损": 1.1,
        "下滑": 1.0, "不及预期": 1.2, "业绩下滑": 1.2,
        "缩量": 0.9, "死叉": 1.0, "资金流出": 1.1, "机构卖出": 1.0,
        "政策收紧": 1.1, "北向资金卖出": 1.0, "减持": 1.1,
        "质押": 0.9, "商誉减值": 1.1, "应收账款": 0.9,

        # 弱负面 (0.6-0.9)
        "熊市": 0.9, "弱势": 0.9, "低迷": 0.8, "疲软": 0.8,
        "震荡下行": 0.8, "跌破支撑": 0.9, "空头排列": 0.9,
        "缩量下跌": 0.8, "阴跌": 0.8, "套牢": 0.9,

        # 行业负面
        "产能过剩": 0.9, "价格战": 0.9, "监管": 0.8, "处罚": 1.0,
        "退市": 1.3, "ST": 1.2, "违规": 1.1,
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
            "positive": ["芯片", "国产替代", "算力", "AI"],
            "negative": ["出口限制", "技术封锁"],
        },
    }

    # 来源权重
    SOURCE_WEIGHTS = {
        "ths_stockpage": 1.0,    # 同花顺个股页（最相关）
        "eastmoney_ann": 0.95,   # 东方财富公告
        "eastmoney_guba": 0.6,   # 东方财富股吧
        "sina": 0.85,            # 新浪财经
        "tencent": 0.8,          # 腾讯财经
        "baidu": 0.7,            # 百度搜索
    }

    def __init__(self):
        self._cache = {}
        self._cache_time = 0
        self._sentiment_history = {}
        self._load_lexicon()

    def _load_lexicon(self):
        """加载金融情感词库"""
        try:
            if LEXICON_FILE.exists():
                with open(LEXICON_FILE, "r", encoding="utf-8") as f:
                    lexicon = json.load(f)

                # 加载正面关键词
                for level, data in lexicon.get("positive", {}).items():
                    for word in data.get("words", []):
                        self.POSITIVE_KEYWORDS[word] = data.get("weight", 1.0)

                # 加载负面关键词
                for level, data in lexicon.get("negative", {}).items():
                    for word in data.get("words", []):
                        self.NEGATIVE_KEYWORDS[word] = data.get("weight", 1.0)

                # 加载股票特定关键词
                for code, kws in lexicon.get("stock_specific", {}).items():
                    self.STOCK_KEYWORDS[code] = kws

                logger.info(f"加载情感词库: {len(self.POSITIVE_KEYWORDS)} 正面, {len(self.NEGATIVE_KEYWORDS)} 负面")
        except Exception as e:
            logger.error(f"加载情感词库失败: {e}")

    # ========== 核心分析方法 ==========

    def _load_cloud_cache(self) -> Dict:
        """从GitHub仓库加载云端情绪缓存"""
        try:
            from paths import BASE_DIR
            cache_file = BASE_DIR / "data" / "market_sentiment.json"
            if cache_file.exists():
                import os
                mtime = os.path.getmtime(cache_file)
                age_hours = (time.time() - mtime) / 3600
                if age_hours < 4:  # 缓存4小时内有效
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if data.get("score") is not None:
                        data["source"] = "cloud_cache"
                        data["cache_age_hours"] = round(age_hours, 1)
                        logger.info(f"使用云端情绪缓存 (分数:{data['score']}, {age_hours:.1f}小时前)")
                        return data
        except Exception as e:
            logger.debug(f"加载云端缓存失败: {e}")
        return None

    def _sync_cloud_cache(self):
        """从GitHub拉取最新情绪数据"""
        try:
            import subprocess
            from paths import BASE_DIR
            cache_file = BASE_DIR / "data" / "market_sentiment.json"
            url = "https://raw.githubusercontent.com/glei4134-collab/ths-trading-ai/main/data/market_sentiment.json"

            import urllib.request
            urllib.request.urlretrieve(url, str(cache_file))
            logger.info("已从GitHub拉取最新情绪数据")
        except Exception as e:
            logger.debug(f"拉取云端数据失败: {e}")

    def get_market_sentiment(self) -> Dict:
        """获取市场整体情绪"""
        try:
            now = time.time()
            if self._cache and now - self._cache_time < 600:
                return self._cache

            # 优先使用云端缓存
            cached = self._load_cloud_cache()
            if cached:
                cached["trend"] = self._calculate_trend("market", cached["score"])
                self._cache = cached
                self._cache_time = now
                return cached

            # 缓存不存在或过期，尝试拉取
            self._sync_cloud_cache()
            cached = self._load_cloud_cache()
            if cached:
                cached["trend"] = self._calculate_trend("market", cached["score"])
                self._cache = cached
                self._cache_time = now
                return cached

            # 云端也没有，本地爬取（兜底）
            news_with_source = self._fetch_all_news()
            result = self._analyze_sentiment_with_weight(news_with_source)
            result["trend"] = self._calculate_trend("market", result["score"])
            result["source"] = "local_scrape"
            self._cache = result
            self._cache_time = now
            return result
        except Exception as e:
            logger.error(f"获取市场情绪失败: {e}")
            return {"score": 50, "description": "无法获取情绪数据"}

    def get_stock_sentiment(self, symbol: str, stock_name: str = "") -> Dict:
        """获取个股情绪（增强版）"""
        try:
            keyword = stock_name or symbol
            all_news_with_source = []

            # 获取新闻（多源）
            all_news_with_source.extend(self._fetch_ths_stockpage(symbol, stock_name))
            all_news_with_source.extend(self._fetch_eastmoney_ann(symbol))
            all_news_with_source.extend(self._fetch_stock_comment(symbol))
            sina_titles = self._fetch_sina_news(f"{keyword} {symbol}")
            all_news_with_source.extend([(t, "sina") for t in sina_titles])

            if not all_news_with_source:
                return {"score": 50, "news_count": 0, "weighted_score": 50,
                        "trend": "stable", "keywords": [], "description": f"{keyword} 无相关新闻"}

            result = self._analyze_sentiment_with_weight(all_news_with_source, symbol)
            result["trend"] = self._calculate_trend(symbol, result["score"])

            # 获取股票概念和行业信息
            try:
                from stock_keyword_db import stock_keyword_db
                result["concepts"] = stock_keyword_db.get_stock_concepts(symbol)
                result["industry"] = stock_keyword_db.get_stock_industry(symbol)
            except Exception:
                result["concepts"] = []
                result["industry"] = ""

            result["description"] = f"{keyword} {result['news_count']}条新闻，情绪{'偏多' if result['score'] > 55 else '偏空' if result['score'] < 45 else '中性'}"

            return result
        except Exception as e:
            logger.warning(f"获取个股情绪失败: {e}")
            return {"score": 50, "news_count": 0, "weighted_score": 50,
                    "trend": "stable", "keywords": [], "description": "无法获取情绪"}

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

    # ========== 内部方法 ==========

    def _analyze_sentiment_with_weight(self, news_with_source: List[Tuple[str, str]],
                                       symbol: str = None) -> Dict:
        """加权情绪分析（混合规则+NLP + 噪音过滤）"""
        if not news_with_source:
            return {"score": 50, "positive": 0, "negative": 0, "weighted_score": 50,
                    "news_count": 0, "keywords": []}

        # 噪音过滤：移除低质量新闻
        news_with_source = self._filter_noise(news_with_source)

        # 获取股票特定关键词
        stock_pos_kw = self.STOCK_KEYWORDS.get(symbol, {}).get("positive", [])
        stock_neg_kw = self.STOCK_KEYWORDS.get(symbol, {}).get("negative", [])

        total_weighted_positive = 0
        total_weighted_negative = 0
        positive_keywords = []
        negative_keywords = []

        for title, source in news_with_source:
            source_weight = self.SOURCE_WEIGHTS.get(source, 0.7)
            time_weight = self._get_time_weight(title)
            weight = source_weight * time_weight

            # 通用关键词
            for kw, kw_weight in self.POSITIVE_KEYWORDS.items():
                if kw in title:
                    total_weighted_positive += kw_weight * weight
                    if kw not in positive_keywords:
                        positive_keywords.append(kw)

            for kw, kw_weight in self.NEGATIVE_KEYWORDS.items():
                if kw in title:
                    total_weighted_negative += kw_weight * weight
                    if kw not in negative_keywords:
                        negative_keywords.append(kw)

            # 股票特定关键词（权重较低，仅作参考）
            for kw in stock_pos_kw:
                if kw in title and len(kw) > 2:  # 排除公司名等短词
                    total_weighted_positive += 0.5 * weight
                    if kw not in positive_keywords:
                        positive_keywords.append(f"{kw}(专属)")

            for kw in stock_neg_kw:
                if kw in title and len(kw) > 2:
                    total_weighted_negative += 0.5 * weight
                    if kw not in negative_keywords:
                        negative_keywords.append(f"{kw}(专属)")

        total_weight = total_weighted_positive + total_weighted_negative
        weighted_score = int((total_weighted_positive / total_weight) * 100) if total_weight > 0 else 50

        simple_positive = sum(1 for title, _ in news_with_source
                            if any(kw in title for kw in self.POSITIVE_KEYWORDS))
        simple_negative = sum(1 for title, _ in news_with_source
                            if any(kw in title for kw in self.NEGATIVE_KEYWORDS))
        simple_total = simple_positive + simple_negative
        simple_score = int((simple_positive / simple_total) * 100) if simple_total > 0 else 50

        # 混合NLP分析（权重20%）
        nlp_score = 50
        try:
            from nlp_sentiment import nlp_sentiment
            # 合并所有标题进行NLP分析
            all_text = " ".join([title for title, _ in news_with_source[:10]])
            nlp_result = nlp_sentiment.analyze(all_text)
            nlp_score = nlp_result.get("score", 50)
        except Exception:
            pass

        # 最终分数：规则80% + NLP 20%
        final_score = int(simple_score * 0.8 + nlp_score * 0.2)

        return {
            "score": final_score,
            "weighted_score": weighted_score,
            "rule_score": simple_score,
            "nlp_score": nlp_score,
            "positive": simple_positive,
            "negative": simple_negative,
            "weighted_positive": round(total_weighted_positive, 2),
            "weighted_negative": round(total_weighted_negative, 2),
            "news_count": len(news_with_source),
            "keywords": positive_keywords[:5] + negative_keywords[:5],
        }

    def _get_time_weight(self, title: str) -> float:
        """时间权重"""
        return 0.8

    def _filter_noise(self, news_with_source: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """噪音过滤：移除低质量新闻"""
        filtered = []

        # 噪音关键词（过滤包含这些词的新闻）
        noise_keywords = [
            "广告", "推广", "赞助", "点击查看", "了解更多", "立即注册",
            "免费领取", "限时优惠", "点击链接", "扫描二维码", "关注公众号",
            "免责声明", "风险提示", "过往业绩", "不代表未来", "投资需谨慎",
            "市场有风险", "入市需谨慎", "历史业绩", "不代表未来表现",
        ]

        for title, source in news_with_source:
            # 过滤太短的标题
            if len(title) < 8:
                continue

            # 过滤包含噪音关键词的标题
            if any(kw in title for kw in noise_keywords):
                continue

            # 过滤纯数字或符号的标题
            if re.match(r'^[\d\s\.\,\-\+]+$', title):
                continue

            # 过滤重复标题（简单去重）
            if any(title == t for t, _ in filtered):
                continue

            filtered.append((title, source))

        return filtered

    def _tokenize_chinese(self, text: str) -> List[str]:
        """中文分词（使用jieba）"""
        try:
            import jieba
            # 加载金融词典
            jieba.add_word('涨停', freq=10000)
            jieba.add_word('跌停', freq=10000)
            jieba.add_word('利好', freq=10000)
            jieba.add_word('利空', freq=10000)
            jieba.add_word('金叉', freq=10000)
            jieba.add_word('死叉', freq=10000)

            words = list(jieba.cut(text))
            return [w.strip() for w in words if w.strip()]
        except Exception:
            # 回退到简单分词
            return re.split(r'[，。！？、；：\s]+', text)

    def _calculate_trend(self, key: str, current_score: float) -> str:
        """计算情绪趋势"""
        now = datetime.now(ZoneInfo('Asia/Shanghai'))
        history = self._sentiment_history.get(key, [])
        history.append({"score": current_score, "time": now})
        self._sentiment_history[key] = history[-10:]

        if len(history) < 3:
            return "stable"

        recent = [h["score"] for h in history[-3:]]
        earlier = [h["score"] for h in history[:-3]] if len(history) > 3 else recent
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
            if rsi < 30: score += 15
            elif rsi < 40: score += 8
            elif rsi > 70: score -= 15
            elif rsi > 60: score -= 8

        macd = indicators.get("macd")
        macd_signal = indicators.get("signal")
        if macd is not None and macd_signal is not None:
            score += 10 if macd > macd_signal else -10

        close = indicators.get("close", 0)
        ma5 = indicators.get("ma5")
        ma20 = indicators.get("ma20")
        if close and ma5 and ma20:
            if close > ma5 > ma20: score += 10
            elif close < ma5 < ma20: score -= 10

        return max(0, min(100, score))

    # ========== 新闻获取方法 ==========

    def _fetch_all_news(self) -> List[Tuple[str, str]]:
        """获取所有新闻源的市场新闻（多源聚合，最大化数据量）"""
        all_news = []

        # 新浪财经（30条）
        for title in self._fetch_sina_news():
            all_news.append((title, "sina"))

        # 东方财富公告（30条）
        for title in self._fetch_eastmoney_market_news():
            all_news.append((title, "eastmoney_ann"))

        # 腾讯财经（30条）
        for title in self._fetch_tencent_news():
            all_news.append((title, "tencent"))

        # 东方财富快讯（30条）
        for title in self._fetch_eastmoney_news_flash():
            all_news.append((title, "eastmoney_flash"))

        # 证券时报（10条）
        for title in self._fetch_stcn_news():
            all_news.append((title, "stcn"))

        # 新浪7x24快讯（30条）
        for title in self._fetch_sina_live_news():
            all_news.append((title, "sina_live"))

        # 政策新闻（10条）
        for title in self._fetch_policy_news():
            all_news.append((title, "policy"))

        # 同花顺快讯（20条）
        for title in self._fetch_10jqka_news():
            all_news.append((title, "10jqka"))

        # Yahoo Finance（国际新闻）
        for title in self._fetch_yahoo_finance():
            all_news.append((title, "yahoo"))

        # CNBC（国际新闻）
        for title in self._fetch_cnbc():
            all_news.append((title, "cnbc"))

        # 雪球热帖（散户情绪）
        for title in self._fetch_xueqiu_hot():
            all_news.append((title, "xueqiu"))

        # 研报评级（机构观点）
        for title in self._fetch_research_reports():
            all_news.append((title, "research"))

        # 宏观政策新闻
        for title in self._fetch_macro_news():
            all_news.append((title, "macro"))

        return all_news

    def _fetch_ths_stockpage(self, symbol: str, stock_name: str = "") -> List[Tuple[str, str]]:
        """同花顺个股新闻页"""
        try:
            import requests
            url = f"https://stockpage.10jqka.com.cn/{symbol}/"
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            titles = re.findall(r'<span class="title">.*?<a[^>]*>(.*?)</a>', resp.text)
            return [(t.strip(), "ths_stockpage") for t in titles if t.strip()]
        except Exception:
            return []

    def _fetch_sina_news(self, keyword: str = "A股") -> List[str]:
        """新浪财经新闻（多页抓取）"""
        try:
            import requests
            all_titles = []
            for page in range(1, 4):  # 3页
                url = "https://feed.mix.sina.com.cn/api/roll/get"
                params = {"pageid": "153", "lid": "2516", "k": "", "num": "30", "page": str(page)}
                resp = requests.get(url, params=params, timeout=10)
                data = resp.json()
                if data and "result" in data and "data" in data["result"]:
                    titles = [item.get("title", "") for item in data["result"]["data"] if item.get("title")]
                    all_titles.extend(titles)
            return all_titles[:50]
        except Exception:
            return []

    def _fetch_eastmoney_market_news(self) -> List[str]:
        """东方财富市场新闻（多页抓取）"""
        try:
            import requests
            all_titles = []
            for page in range(1, 4):  # 3页
                url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
                params = {"cb": "", "sr": "-1", "page_size": "30", "page_index": str(page),
                          "ann_type": "SHA,SZA", "client_source": "web", "f_node": "0", "s_node": "0"}
                resp = requests.get(url, params=params, timeout=10)
                data = resp.json()
                if data and "data" in data and "list" in data["data"]:
                    titles = [item.get("title", "") for item in data["data"]["list"] if item.get("title")]
                    all_titles.extend(titles)
            return all_titles[:50]
        except Exception:
            return []

    def _fetch_tencent_news(self) -> List[str]:
        """腾讯财经新闻"""
        try:
            import requests
            # 腾讯新闻搜索
            url = "https://web.sqt.gtimg.cn/q=sh000001,sz399001"
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            # 改用腾讯财经新闻列表
            url2 = "https://finance.qq.com/a/20260504A00000"
            resp2 = requests.get(url2, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            titles = re.findall(r'<h2[^>]*>(.*?)</h2>', resp2.text)
            return [t.strip() for t in titles if t.strip() and len(t.strip()) > 5][:20]
        except Exception:
            return []

    def _fetch_eastmoney_ann(self, symbol: str) -> List[Tuple[str, str]]:
        """东方财富个股公告"""
        try:
            import requests
            ann_type = "SHA" if symbol.startswith("6") else "SZA"
            url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
            params = {"cb": "", "sr": "-1", "page_size": "20", "page_index": "1",
                      "ann_type": ann_type, "client_source": "web", "stock_list": symbol}
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            if data and "data" in data and "list" in data["data"]:
                return [(item.get("title", ""), "eastmoney_ann") for item in data["data"]["list"] if item.get("title")]
            return []
        except Exception:
            return []

    def _fetch_eastmoney_guba(self, symbol: str) -> List[Tuple[str, str]]:
        """东方财富股吧"""
        try:
            import requests
            url = f"https://guba.eastmoney.com/list,{symbol}.html"
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            titles = re.findall(r'>([^<]{8,80})<', resp.text)
            valid = [t.strip() for t in titles if t.strip() and len(t.strip()) > 5]
            return [(t, "eastmoney_guba") for t in valid[:15]]
        except Exception:
            return []

    def _fetch_eastmoney_news_flash(self) -> List[str]:
        """东方财富实时快讯"""
        try:
            import requests
            # 东方财富公告API
            url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
            params = {
                "cb": "", "sr": "-1", "page_size": "30", "page_index": "1",
                "ann_type": "SHA,SZA", "client_source": "web", "f_node": "0", "s_node": "0"
            }
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            if data and "data" in data and "list" in data["data"]:
                return [item.get("title", "") for item in data["data"]["list"] if item.get("title")]
            return []
        except Exception:
            return []

    def _fetch_stcn_news(self) -> List[str]:
        """证券时报快讯"""
        try:
            import requests
            url = "https://www.stcn.com/article/list/kx.html"
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            titles = re.findall(r'<h2><a[^>]*>(.*?)</a></h2>', resp.text)
            return [t.strip() for t in titles if t.strip()][:20]
        except Exception:
            return []

    def _fetch_sina_live_news(self) -> List[str]:
        """新浪7x24实时快讯（多页抓取）"""
        try:
            import requests
            all_titles = []
            for page in range(1, 4):  # 3页
                url = "https://zhibo.sina.com.cn/api/zhibo/feed"
                params = {"page": str(page), "page_size": "30", "zhibo_id": "152", "tag_id": "0", "type": "0"}
                resp = requests.get(url, params=params, timeout=10)
                data = resp.json()
                if data and "result" in data and "data" in data["result"]:
                    feed = data["result"]["data"].get("feed", {})
                    list_data = feed.get("list", [])
                    titles = [item.get("rich_text", "") for item in list_data if item.get("rich_text")]
                    all_titles.extend(titles)
            return all_titles[:60]
        except Exception:
            return []

    def _fetch_policy_news(self) -> List[str]:
        """政策新闻（央行、证监会）"""
        try:
            import requests
            # 央行政策新闻
            url = "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html"
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            titles = re.findall(r'<a[^>]*title="(.*?)"', resp.text)
            policy_news = [t.strip() for t in titles if t.strip() and len(t.strip()) > 5]
            return policy_news[:10]
        except Exception:
            return []

    def _fetch_stock_comment(self, symbol: str) -> List[Tuple[str, str]]:
        """个股评论/讨论（东方财富股吧热帖）"""
        try:
            import requests
            url = f"https://guba.eastmoney.com/list,{symbol},f.html"
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            # 提取帖子标题和内容摘要
            titles = re.findall(r'class="post_title"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
            if not titles:
                titles = re.findall(r'>([^<]{10,100})<', resp.text)
            valid = [t.strip() for t in titles if t.strip() and len(t.strip()) > 8]
            return [(t, "stock_comment") for t in valid[:20]]
        except Exception:
            return []

    def _fetch_10jqka_news(self) -> List[str]:
        """同花顺快讯（增加抓取量）"""
        try:
            import requests
            url = "https://news.10jqka.com.cn/tapp/news/push/stock/"
            params = {"page": "1", "tag": "", "track": "website", "pagesize": "30"}
            resp = requests.get(url, params=params, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            data = resp.json()
            if data and "data" in data and "list" in data["data"]:
                return [item.get("title", "") for item in data["data"]["list"] if item.get("title")]
            return []
        except Exception:
            return []

    def _fetch_yahoo_finance(self) -> List[str]:
        """Yahoo Finance 国际新闻（增加抓取量）"""
        try:
            import requests
            url = "https://finance.yahoo.com/topic/stock-market-news/"
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            titles = re.findall(r'<h3[^>]*>(.*?)</h3>', resp.text)
            clean_titles = [re.sub(r'<[^>]+>', '', t).strip() for t in titles]
            return [t for t in clean_titles if t and len(t) > 10][:25]
        except Exception:
            return []

    def _fetch_cnbc(self) -> List[str]:
        """CNBC 国际新闻（增加抓取量）"""
        try:
            import requests
            url = "https://www.cnbc.com/world-markets/"
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            titles = re.findall(r'<a[^>]*class=\"Card-title\"[^>]*>(.*?)</a>', resp.text)
            clean_titles = [re.sub(r'<[^>]+>', '', t).strip() for t in titles]
            return [t for t in clean_titles if t and len(t) > 10][:25]
        except Exception:
            return []

    def _fetch_xueqiu_hot(self) -> List[str]:
        """雪球热帖（散户情绪）"""
        try:
            import requests
            url = "https://xueqiu.com/statuses/hot/listV2.json"
            params = {"since_id": -1, "max_id": -1, "size": 20}
            headers = {"User-Agent": "Mozilla/5.0", "Cookie": "xq_a_token=test"}
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            data = resp.json()
            if "items" in data:
                return [item.get("title", "") for item in data["items"] if item.get("title")]
            return []
        except Exception:
            return []

    def _fetch_research_reports(self) -> List[str]:
        """研报评级（机构观点）"""
        try:
            import requests
            # 东方财富研报
            url = "https://data.eastmoney.com/report/info/AP,ART,20260504,0.html"
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            titles = re.findall(r'<a[^>]*title=\"(.*?)\"', resp.text)
            valid = [t.strip() for t in titles if t.strip() and len(t.strip()) > 10]
            return valid[:15]
        except Exception:
            return []

    def _fetch_macro_news(self) -> List[str]:
        """宏观政策新闻"""
        try:
            import requests
            # 央行政策
            url = "http://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html"
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            titles = re.findall(r'<a[^>]*title=\"(.*?)\"', resp.text)
            policy_news = [t.strip() for t in titles if t.strip() and len(t.strip()) > 5]

            # 证监会新闻
            url2 = "http://www.csrc.gov.cn/csrc/c100028/common_list.shtml"
            resp2 = requests.get(url2, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            titles2 = re.findall(r'<a[^>]*title=\"(.*?)\"', resp2.text)
            policy_news.extend([t.strip() for t in titles2 if t.strip() and len(t.strip()) > 5])

            return policy_news[:20]
        except Exception:
            return []


# 全局实例
sentiment_analyzer = SentimentAnalyzer()
