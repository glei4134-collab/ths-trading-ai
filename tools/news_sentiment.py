#!/usr/bin/env python3
"""
独立新闻情绪分析脚本
可在GitHub Actions云端运行，不依赖本地服务器

功能:
  1. 爬取10+新闻源（新浪/东财/同花顺/雪球等）
  2. 本地关键词情绪分析
  3. 输出JSON到 data/market_sentiment.json

用法:
  # 本地测试
  python tools/news_sentiment.py

  # GitHub Actions 自动运行
  # （workflow 会自动调用此脚本）
"""

import json
import time
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, List, Tuple

import requests

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

# ========== 新闻源 ==========

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def fetch_sina_news(keyword: str = "A股") -> List[str]:
    """新浪财经"""
    try:
        url = "https://feed.mix.sina.com.cn/api/roll/get"
        params = {"pageid": "153", "lid": "2516", "k": keyword, "num": 30, "page": 1}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        return [item.get("title", "") for item in data.get("result", {}).get("data", []) if item.get("title")]
    except Exception:
        return []

def fetch_eastmoney_news() -> List[str]:
    """东方财富快讯"""
    try:
        url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
        params = {"sr": -1, "page_size": 30, "page_index": 1, "ann_type": "SHA", "client_source": "web"}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        return [item.get("title", "") for item in data.get("data", {}).get("list", []) if item.get("title")]
    except Exception:
        return []

def fetch_10jqka_news() -> List[str]:
    """同花顺快讯"""
    try:
        url = "https://news.10jqka.com.cn/tapp/news/push/stock/"
        params = {"page": 1, "tag": "", "track": "website", "pagesize": 20}
        r = requests.get(url, params=params, timeout=10, headers=HEADERS)
        data = r.json()
        return [item.get("title", "") for item in data.get("data", {}).get("list", []) if item.get("title")]
    except Exception:
        return []

def fetch_stcn_news() -> List[str]:
    """证券时报"""
    try:
        url = "https://www.stcn.com/roll/index.html"
        r = requests.get(url, timeout=10, headers=HEADERS)
        titles = re.findall(r'<a[^>]*>([^<]{10,80})</a>', r.text)
        return [t.strip() for t in titles if any(kw in t for kw in ["股", "涨", "跌", "市场", "板块", "资金", "政策"])][:15]
    except Exception:
        return []

def fetch_sina_live() -> List[str]:
    """新浪7x24直播"""
    try:
        url = "https://zhibo.sina.com.cn/api/zhibo/feed"
        params = {"page": 1, "page_size": 30, "zhibo_id": "152", "tag_id": 0, "type": 0}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        result = data.get("result", {}).get("data", {}).get("feed", {}).get("list", [])
        return [item.get("rich_text", "")[:80] for item in result if item.get("rich_text")]
    except Exception:
        return []

def fetch_xueqiu_hot() -> List[str]:
    """雪球热帖"""
    try:
        url = "https://xueqiu.com/statuses/hot/listV2.json"
        params = {"since_id": -1, "max_id": -1, "size": 20}
        headers = {**HEADERS, "Cookie": "xq_a_token=xxx"}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        data = r.json()
        return [item.get("title", "") for item in data.get("items", []) if item.get("title")]
    except Exception:
        return []

def fetch_tencent_news() -> List[str]:
    """腾讯财经"""
    try:
        url = "https://stock.finance.qq.com/a/a20260505001.htm"
        r = requests.get(url, timeout=10, headers=HEADERS)
        titles = re.findall(r'<a[^>]*>([^<]{10,80})</a>', r.text)
        return [t.strip() for t in titles if len(t) > 10][:15]
    except Exception:
        return []

ALL_FETCHERS = [
    ("sina", fetch_sina_news),
    ("eastmoney", fetch_eastmoney_news),
    ("10jqka", fetch_10jqka_news),
    ("stcn", fetch_stcn_news),
    ("sina_live", fetch_sina_live),
    ("xueqiu", fetch_xueqiu_hot),
    ("tencent", fetch_tencent_news),
]

# ========== 情绪分析 ==========

def analyze_sentiment(news_with_source: List[Tuple[str, str]]) -> Dict:
    """关键词加权情绪分析"""
    total_positive = 0
    total_negative = 0
    matched_keywords = []

    for title, source in news_with_source:
        for word, weight in POSITIVE_KEYWORDS.items():
            if word in title:
                total_positive += weight
                matched_keywords.append({"word": word, "type": "positive", "weight": weight, "title": title[:50]})
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
        "positive_count": total_positive,
        "negative_count": total_negative,
        "matched_keywords": matched_keywords[:20],
        "description": description,
    }

# ========== 主流程 ==========

def run():
    """执行完整的新闻爬取+情绪分析"""
    print(f"[{datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S')}] 开始爬取新闻...")

    all_news = []
    for name, fetcher in ALL_FETCHERS:
        try:
            titles = fetcher()
            all_news.extend([(t, name) for t in titles if t])
            print(f"  {name}: {len(titles)}条")
        except Exception as e:
            print(f"  {name}: 失败 - {e}")

    print(f"\n总计: {len(all_news)}条新闻")

    result = analyze_sentiment(all_news)
    result["timestamp"] = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
    result["sources"] = list(set(s for _, s in all_news))
    result["news_headlines"] = [{"title": t, "source": s} for t, s in all_news[:50]]

    # 输出到文件
    output_dir = Path(__file__).parent.parent / "data"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "market_sentiment.json"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n情绪分数: {result['score']}/100 ({result['description']})")
    print(f"匹配关键词: {len(result['matched_keywords'])}个")
    print(f"输出文件: {output_file}")

    return result


if __name__ == "__main__":
    run()
