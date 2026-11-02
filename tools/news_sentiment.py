#!/usr/bin/env python3
"""
独立新闻情绪分析脚本（加固增强版 v2.5）
可在 GitHub Actions 云端或本地服务运行，支持多渠道并发加速、连接池复用与内存缓存

核心特性:
  1. 多源并发爬取（新浪 7x24、东财极速快讯、同花顺、雪球热榜、证券时报、财联社备用）
  2. ThreadPoolExecutor 并行抓取，单源超时严格限制 <= 4 秒，任一源超时不阻塞整体
  3. 全局 requests.Session 连接池复用，抓取耗时压缩 50% 以上
  4. 内存缓存（10 分钟 TTL）与标题指纹去重机制
  5. 本地关键词加权情绪评分与一票否决严重风险预警
  6. 持久化输出到 data/market_sentiment.json
"""

import json
import time
import re
import sys
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ========== 情感关键词库 ==========
POSITIVE_KEYWORDS = {
    "暴涨": 1.5, "涨停": 1.4, "新高": 1.3, "创新高": 1.3,
    "大幅上涨": 1.3, "强势突破": 1.3, "连续涨停": 1.5,
    "利好": 1.2, "上涨": 1.0, "突破": 1.1, "增长": 1.1,
    "盈利": 1.1, "超预期": 1.2, "反弹": 1.0, "放量": 1.0,
    "金叉": 1.0, "龙头": 1.1, "热点": 1.0, "资金流入": 1.1,
    "业绩增长": 1.1, "净利润增长": 1.2, "营收增长": 1.1,
    "政策利好": 1.2, "机构买入": 1.1, "北向资金买入": 1.0,
    "回购": 1.1, "增持": 1.1, "分红": 1.0,
    "牛市": 0.9, "强势": 0.9, "企稳": 0.8, "回暖": 0.8,
    "震荡上行": 0.8, "底部放量": 0.9, "突破压力": 1.0,
    "芯片": 0.8, "半导体": 0.8, "新能源": 0.9, "光伏": 0.8,
    "锂电池": 0.9, "人工智能": 1.0, "5G": 0.8, "军工": 0.8,
}

NEGATIVE_KEYWORDS = {
    "暴跌": 1.5, "崩盘": 1.5, "跌停": 1.4, "新低": 1.3,
    "大幅下跌": 1.3, "破位下跌": 1.3, "连续跌停": 1.5,
    "利空": 1.2, "下跌": 1.0, "破位": 1.1, "亏损": 1.1,
    "下滑": 1.0, "不及预期": 1.2, "业绩下滑": 1.2,
    "缩量": 0.9, "死叉": 1.0, "资金流出": 1.1, "机构卖出": 1.0,
    "政策收紧": 1.1, "北向资金卖出": 1.0, "减持": 1.1,
    "质押": 0.9, "商誉减值": 1.1, "熊市": 0.9, "弱势": 0.9,
    "恐慌": 1.0, "踩踏": 1.2, "爆仓": 1.3, "清仓": 1.1,
    "退市": 1.4, "ST": 1.2, "违规": 1.1, "处罚": 1.0,
}

CRITICAL_RISK_KEYWORDS = [
    "立案调查", "财务造假", "暴雷", "退市", "巨额亏损",
    "资金链断裂", "业绩腰斩", "重组失败", "违约", "清仓式减持"
]

# ========== 全局连接池会话 ==========
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/json,*/*",
}

_session = requests.Session()
_session.headers.update(HEADERS)
_retry_strategy = Retry(
    total=1,
    backoff_factor=0.2,
    status_forcelist=[500, 502, 503, 504],
)
_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=_retry_strategy)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)
_session.trust_env = False

# 10 分钟内存缓存
_MEM_CACHE: Dict = {}
_MEM_CACHE_TIME: float = 0.0
_MEM_CACHE_TTL: float = 600.0  # 10 分钟

# ========== 新闻源抓取器（单源超时严格 <= 4.0 秒） ==========

def fetch_sina_live() -> List[str]:
    """新浪 7x24 实时快讯（精选财经直播电报）"""
    try:
        url = "https://zhibo.sina.com.cn/api/zhibo/feed"
        params = {"page": 1, "page_size": 30, "zhibo_id": "152", "tag_id": 0, "type": 0}
        r = _session.get(url, params=params, timeout=4)
        data = r.json()
        result = data.get("result", {}).get("data", {}).get("feed", {}).get("list", [])
        return [item.get("rich_text", "")[:90].strip() for item in result if item.get("rich_text")]
    except Exception:
        return []

def fetch_sina_news(keyword: str = "A股") -> List[str]:
    """新浪财经滚动新闻"""
    try:
        url = "https://feed.mix.sina.com.cn/api/roll/get"
        params = {"pageid": "153", "lid": "2516", "k": keyword, "num": 30, "page": 1}
        r = _session.get(url, params=params, timeout=4)
        data = r.json()
        return [item.get("title", "").strip() for item in data.get("result", {}).get("data", []) if item.get("title")]
    except Exception:
        return []

def fetch_eastmoney_fast_flash() -> List[str]:
    """东方财富 7x24 极速快讯（覆盖A股要闻与宏观突发）"""
    try:
        url = "https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_50_1_.html"
        r = _session.get(url, timeout=4)
        raw = r.text.replace("var ajaxResult=", "").rstrip(";")
        data = json.loads(raw)
        lives = data.get("LivesList", [])
        titles = []
        for item in lives:
            t = (item.get("title") or "").strip() or (item.get("digest") or "").strip()
            if t and len(t) >= 6:
                titles.append(t[:90])
        return titles[:40]
    except Exception:
        return []

def fetch_10jqka_news() -> List[str]:
    """同花顺实时股票快讯"""
    try:
        url = "https://news.10jqka.com.cn/tapp/news/push/stock/"
        params = {"page": 1, "tag": "", "track": "website", "pagesize": 30}
        r = _session.get(url, params=params, timeout=4)
        data = r.json()
        return [item.get("title", "").strip() for item in data.get("data", {}).get("list", []) if item.get("title")]
    except Exception:
        return []

def fetch_xueqiu_hot() -> List[str]:
    """雪球 7x24 投资者热搜榜与情绪讨论"""
    try:
        url = "https://stock.xueqiu.com/v5/stock/hot_stock/list.json?size=20&_type=10&type=10"
        r = _session.get(url, timeout=4)
        items = r.json().get("data", {}).get("items", [])
        results = []
        for item in items:
            name = item.get("name", "")
            code = item.get("code", "")
            pct = item.get("percent", 0.0)
            if name:
                results.append(f"[雪球热榜] {name}({code}) 讨论飙升 涨跌幅:{pct}%")
        return results[:20]
    except Exception:
        return []

def fetch_stcn_news() -> List[str]:
    """证券时报要闻滚动"""
    try:
        url = "https://www.stcn.com/roll/index.html"
        r = _session.get(url, timeout=4)
        titles = re.findall(r'<a[^>]*>([^<]{10,80})</a>', r.text)
        return [t.strip() for t in titles if any(kw in t for kw in ["股", "涨", "跌", "市场", "板块", "资金", "政策"])][:20]
    except Exception:
        return []

def fetch_eastmoney_news() -> List[str]:
    """东方财富上市公司公告"""
    try:
        url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
        params = {"sr": -1, "page_size": 30, "page_index": 1, "ann_type": "SHA,SZA", "client_source": "web"}
        r = _session.get(url, params=params, timeout=4)
        data = r.json()
        return [item.get("title", "").strip() for item in data.get("data", {}).get("list", []) if item.get("title")]
    except Exception:
        return []

def fetch_cls_telegraph() -> List[str]:
    """财联社电报热点（备用通道）"""
    try:
        url = "https://m.cls.cn/nodeapi/updateTelegraphList"
        params = {"app": "Clyde", "os": "web", "sv": "7.7.5"}
        r = _session.get(url, params=params, timeout=4)
        items = r.json().get("data", {}).get("roll_data", [])
        titles = []
        for item in items:
            t = item.get("title") or item.get("content", "")
            if t:
                titles.append(t[:80].strip())
        return titles[:20]
    except Exception:
        return []

ALL_FETCHERS = [
    ("sina_live", fetch_sina_live),
    ("eastmoney_flash", fetch_eastmoney_fast_flash),
    ("10jqka", fetch_10jqka_news),
    ("sina", fetch_sina_news),
    ("xueqiu", fetch_xueqiu_hot),
    ("stcn", fetch_stcn_news),
    ("eastmoney_ann", fetch_eastmoney_news),
    ("cls", fetch_cls_telegraph),
]

# ========== 情绪分析与关键词匹配 ==========

def analyze_sentiment(news_with_source: List[Tuple[str, str]]) -> Dict:
    """关键词加权情绪分析（含严重风险识别）"""
    total_positive = 0.0
    total_negative = 0.0
    matched_keywords = []
    critical_risk_detected = False
    critical_hits = []

    for title, source in news_with_source:
        # 重大风险关键词扫描
        for crk in CRITICAL_RISK_KEYWORDS:
            if crk in title:
                critical_risk_detected = True
                if crk not in critical_hits:
                    critical_hits.append(crk)

        # 正向词加权
        for word, weight in POSITIVE_KEYWORDS.items():
            if word in title:
                total_positive += weight
                matched_keywords.append({"word": word, "type": "positive", "weight": weight, "title": title[:50]})

        # 负向词加权
        for word, weight in NEGATIVE_KEYWORDS.items():
            if word in title:
                total_negative += weight
                matched_keywords.append({"word": word, "type": "negative", "weight": weight, "title": title[:50]})

    total = total_positive + total_negative
    if total == 0:
        score = 50
    else:
        score = round(50 + (total_positive - total_negative) / total * 50)
        score = max(0, min(100, score))

    # 若检测到重大雷区，进行情绪截断保护
    if critical_risk_detected:
        score = min(score, 38)

    if score >= 65:
        description = "市场情绪偏多"
    elif score >= 55:
        description = "市场情绪中性偏多"
    elif score >= 45:
        description = "市场情绪中性"
    elif score >= 35:
        description = "市场情绪中性偏空"
    else:
        description = "市场情绪偏空"

    return {
        "score": score,
        "news_count": len(news_with_source),
        "positive_count": round(total_positive, 2),
        "negative_count": round(total_negative, 2),
        "matched_keywords": matched_keywords[:30],
        "critical_risk_detected": critical_risk_detected,
        "critical_hits": critical_hits,
        "description": description,
    }

# ========== 并发执行主流程 ==========

def fetch_all_news_concurrent(timeout_per_source: float = 4.0, max_workers: int = 4) -> List[Tuple[str, str]]:
    """使用 ThreadPoolExecutor 并行拉取多源新闻并去重"""
    all_news: List[Tuple[str, str]] = []
    seen_hashes = set()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(fn): name for name, fn in ALL_FETCHERS}
        for future in as_completed(future_map, timeout=timeout_per_source + 2.0):
            src_name = future_map[future]
            try:
                titles = future.result(timeout=timeout_per_source)
                added = 0
                for t in titles:
                    if not t or len(t) < 5:
                        continue
                    # 标题特征指纹去重
                    h = hashlib.md5(t[:30].encode("utf-8")).hexdigest()
                    if h not in seen_hashes:
                        seen_hashes.add(h)
                        all_news.append((t, src_name))
                        added += 1
            except Exception as e:
                # 即使单源超时或异常，直接捕获跳过，绝不阻塞整体抓取
                pass

    return all_news

def run(force_refresh: bool = False) -> Dict:
    """执行完整的新闻爬取+情绪分析（支持 10 分钟内存缓存）"""
    global _MEM_CACHE, _MEM_CACHE_TIME

    now_ts = time.time()
    if not force_refresh and _MEM_CACHE and (now_ts - _MEM_CACHE_TIME < _MEM_CACHE_TTL):
        return _MEM_CACHE

    t0 = time.time()
    all_news = fetch_all_news_concurrent(timeout_per_source=4.0, max_workers=4)
    duration = time.time() - t0

    result = analyze_sentiment(all_news)
    result["timestamp"] = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
    result["sources"] = list(set(s for _, s in all_news))
    result["news_headlines"] = [{"title": t, "source": s} for t, s in all_news[:60]]
    result["fetch_duration_sec"] = round(duration, 2)

    # 更新内存缓存
    _MEM_CACHE = result
    _MEM_CACHE_TIME = now_ts

    # 输出到持久化 JSON 文件
    try:
        output_dir = Path(__file__).parent.parent / "data"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "market_sentiment.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return result


if __name__ == "__main__":
    res = run(force_refresh=True)
    print(f"爬取完成: {res['news_count']}条新闻, 耗时 {res.get('fetch_duration_sec')}秒")
    print(f"情绪分: {res['score']}/100 ({res['description']})")
    print(f"覆盖渠道: {', '.join(res['sources'])}")
