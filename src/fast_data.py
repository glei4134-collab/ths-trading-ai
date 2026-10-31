"""
高速数据源模块
用腾讯行情API + 东方财富直连API 替代 akshare，速度提升 5-10 倍

数据源:
  - 腾讯行情 (qt.gtimg.cn): 实时报价，最简单最快
  - 东方财富 (push2/push2his): K线数据、全市场行情、分时数据
"""

import re
import json
import time
import logging
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# ==================== 请求会话（连接池复用） ====================
_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/json,*/*",
})

# 国内行情API（东方财富/腾讯）不走代理，避免被本地代理拦截
# 用户环境可能设置了 HTTP_PROXY=127.0.0.1:7897，会导致 ProxyError
_session.trust_env = False


# ==================== 代码格式转换 ====================

def symbol_to_tencent(symbol: str, is_index: bool = False) -> str:
    """'000001' → 'sz000001', '600519' → 'sh600519', '000300'(index) → 'sh000300'"""
    if symbol.startswith("6") or is_index:
        return f"sh{symbol}"
    return f"sz{symbol}"


def symbol_to_eastmoney(symbol: str, is_index: bool = False) -> str:
    """'000001' → '0.000001', '600519' → '1.600519', '000300'(index) → '1.000300'"""
    if symbol.startswith("6") or is_index:
        return f"1.{symbol}"
    return f"0.{symbol}"


def symbol_prefix(symbol: str) -> str:
    """返回 'sh' 或 'sz'"""
    return "sh" if symbol.startswith("6") else "sz"


# ==================== 仿真/虚拟时钟行情提供器 ====================

class MockMarketDataProvider:
    """虚拟时钟/离线仿真行情提供者（解决虚拟时钟模式下无实时行情脱节痛点）"""

    @staticmethod
    def is_mock_active() -> bool:
        try:
            from ai_scheduler import ai_scheduler
            return ai_scheduler.is_mock_time
        except Exception:
            return False

    @staticmethod
    def generate_mock_realtime(symbols: List[str]) -> Dict[str, Dict]:
        """为给定股票代码列表生成逼真的快照报价"""
        try:
            from ai_scheduler import ai_scheduler
            now_dt = ai_scheduler.now()
        except Exception:
            now_dt = datetime.now()

        date_seed = int(now_dt.strftime("%Y%m%d"))
        quotes = {}
        for s in symbols:
            code_int = sum(ord(c) for c in s)
            base_p = 10.0 + (code_int % 80)
            day_pct = ((code_int + date_seed) % 11 - 4.5)  # -4.5% ~ +5.5%
            pre_close = round(base_p, 2)
            curr_price = round(pre_close * (1 + day_pct / 100), 2)
            open_p = round(pre_close * (1 + (day_pct * 0.4) / 100), 2)
            high_p = round(max(curr_price, open_p, pre_close) * 1.01, 2)
            low_p = round(min(curr_price, open_p, pre_close) * 0.99, 2)
            vol = 50000 + (code_int * 137 % 200000)
            amount = round(curr_price * vol * 100 / 10000, 2)

            l_up = round(pre_close * 1.10, 2)
            l_down = round(pre_close * 0.90, 2)
            b_vol1 = 5000 + (code_int * 17 % 10000)
            a_vol1 = 3000 + (code_int * 23 % 8000)
            tot_b = b_vol1 * 3
            tot_a = a_vol1 * 3
            mock_obi = round((tot_b - tot_a) / (tot_b + tot_a), 4)

            quotes[s] = {
                "code": s,
                "name": f"仿真标的_{s[-4:]}",
                "price": curr_price,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "pre_close": pre_close,
                "change": round(curr_price - pre_close, 2),
                "change_pct": round(day_pct, 2),
                "volume": vol,
                "amount": amount,
                "turnover": round(vol * 100 / 50000000 * 100, 2),
                "pe": 22.5,
                "is_mock": True,
                "limit_up": l_up,
                "limit_down": l_down,
                "obi": mock_obi,
                "total_bid_vol": tot_b,
                "total_ask_vol": tot_a,
                "ask1_price": round(curr_price + 0.01, 2),
                "ask1_vol": a_vol1,
                "bid1_price": round(curr_price - 0.01, 2),
                "bid1_vol": b_vol1,
            }
        return quotes

    @staticmethod
    def generate_mock_kline(symbol: str, days: int = 60) -> list:
        """生成 30-60 日的连续仿真 K 线"""
        try:
            from ai_scheduler import ai_scheduler
            now_dt = ai_scheduler.now()
        except Exception:
            now_dt = datetime.now()

        code_int = sum(ord(c) for c in symbol)
        p = 15.0 + (code_int % 40)
        kline = []
        for d in range(days, 0, -1):
            dt = now_dt - timedelta(days=d)
            if dt.weekday() >= 5:
                continue
            delta = ((code_int + d * 17) % 9 - 4) * 0.3
            p = max(5.0, round(p + delta, 2))
            kline.append({
                "date": dt.strftime("%Y-%m-%d"),
                "open": round(p * 0.995, 2),
                "close": p,
                "high": round(p * 1.02, 2),
                "low": round(p * 0.98, 2),
                "volume": 20000 + (code_int * d % 30000),
                "amount": round(p * 20000 * 100, 2),
                "change_pct": round(delta / p * 100, 2),
            })
        return kline


# ==================== 腾讯行情 API ====================

def fetch_tencent_realtime(symbols: List[str]) -> Dict[str, Dict]:
    """
    腾讯行情API批量获取实时报价
    速度: ~100-200ms（一次HTTP请求，返回多只股票）

    返回: {symbol: {code, name, price, open, high, low, pre_close, volume, amount, change, change_pct, turnover, pe, ...}}
    """
    if not symbols:
        return {}

    # 若处于虚拟时钟演练状态，自动由 Mock 提供器生成高仿真行情
    if MockMarketDataProvider.is_mock_active():
        return MockMarketDataProvider.generate_mock_realtime(symbols)

    # 构建腾讯格式代码
    tc_symbols = [symbol_to_tencent(s) for s in symbols]
    url = f"http://qt.gtimg.cn/q={','.join(tc_symbols)}"

    try:
        resp = _session.get(url, timeout=5)
        resp.encoding = "gbk"
        text = resp.text
    except Exception as e:
        logger.error(f"腾讯行情API请求失败: {e}")
        return {}

    result = {}
    # 每行格式: v_sh600519="1~贵州茅台~600519~1749.00~..."
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue

        # 提取代码和数据
        match = re.match(r'v_(s?_?\w+)="(.+)"', line)
        if not match:
            continue

        data_str = match.group(2)
        fields = data_str.split("~")
        if len(fields) < 50:
            continue

        try:
            code = fields[2]
            price = float(fields[3]) if fields[3] else 0
            pre_close = float(fields[4]) if fields[4] else 0
            open_price = float(fields[5]) if fields[5] else 0
            volume = int(fields[6]) if fields[6] else 0  # 手
            change = float(fields[31]) if fields[31] else 0
            change_pct = float(fields[32]) if fields[32] else 0
            high = float(fields[33]) if fields[33] else 0
            low = float(fields[34]) if fields[34] else 0
            amount = float(fields[37]) if fields[37] else 0  # 万元
            turnover = float(fields[38]) if fields[38] else 0  # 换手率
            pe = float(fields[39]) if fields[39] else 0

            # 解析买卖五档挂单与OBI
            b_vols = [int(fields[10 + i * 2]) * 100 if fields[10 + i * 2] else 0 for i in range(5)]
            a_vols = [int(fields[20 + i * 2]) * 100 if fields[20 + i * 2] else 0 for i in range(5)]
            tot_b = sum(b_vols)
            tot_a = sum(a_vols)
            obi = round((tot_b - tot_a) / (tot_b + tot_a), 4) if (tot_b + tot_a) > 0 else 0.0
            l_up = float(fields[47]) if len(fields) > 47 and fields[47] else round(pre_close * 1.10, 2)
            l_down = float(fields[48]) if len(fields) > 48 and fields[48] else round(pre_close * 0.90, 2)
            ask1_p = float(fields[19]) if fields[19] else 0.0
            bid1_p = float(fields[9]) if fields[9] else 0.0

            result[code] = {
                "code": code,
                "name": fields[1],
                "price": price,
                "open": open_price,
                "high": high,
                "low": low,
                "pre_close": pre_close,
                "change": change,
                "change_pct": change_pct,
                "volume": volume * 100,  # 手 → 股
                "amount": amount * 10000,  # 万元 → 元
                "turnover": turnover,
                "pe": pe,
                "limit_up": l_up,
                "limit_down": l_down,
                "obi": obi,
                "total_bid_vol": tot_b,
                "total_ask_vol": tot_a,
                "ask1_price": ask1_p,
                "ask1_vol": a_vols[0],
                "bid1_price": bid1_p,
                "bid1_vol": b_vols[0],
            }
        except (ValueError, IndexError) as e:
            logger.warning(f"解析腾讯行情数据失败 {fields[2] if len(fields) > 2 else '?'}: {e}")
            continue

    return result


# ==================== 东方财富 全市场行情 ====================

def fetch_eastmoney_all_stocks(max_pages: int = 60) -> Optional[Any]:
    """
    东方财富 push2 API 获取全市场A股行情（分页获取）
    速度: ~3-5s（60页 × 100只/页）

    返回: 原始 DataFrame 格式兼容 akshare，或 None
    """
    import pandas as pd

    url = "https://push2.eastmoney.com/api/qt/clist/get"
    all_items = []

    for page in range(1, max_pages + 1):
        params = {
            "pn": page,
            "pz": 100,  # API 限制最多100只/页
            "po": 1,
            "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "invt": 2,
            "fid": "f12",  # 按代码排序，确保获取全部股票
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21",
        }

        try:
            resp = _session.get(url, params=params, timeout=10)
            data = resp.json()
            if not data or "data" not in data or not data["data"]:
                break

            items = data["data"].get("diff", [])
            if not items:
                break

            all_items.extend(items)

            # 如果返回不足100只，说明已到最后一页
            if len(items) < 100:
                break
        except Exception:
            break

    if not all_items:
        return None

    items = all_items

    # 构建 DataFrame，保持与 akshare 兼容的列名
    records = []
    for item in items:
        try:
            records.append({
                "代码": str(item.get("f12", "")),
                "名称": item.get("f14", ""),
                "最新价": float(item.get("f2", 0) or 0),
                "涨跌幅": float(item.get("f3", 0) or 0),
                "涨跌额": float(item.get("f4", 0) or 0),
                "成交量": int(item.get("f5", 0) or 0),
                "成交额": float(item.get("f6", 0) or 0),
                "振幅": float(item.get("f7", 0) or 0),
                "换手率": float(item.get("f8", 0) or 0),
                "市盈率": float(item.get("f9", 0) or 0),
                "量比": float(item.get("f10", 0) or 0),
                "今开": float(item.get("f17", 0) or 0),
                "最高": float(item.get("f15", 0) or 0),
                "最低": float(item.get("f16", 0) or 0),
                "昨收": float(item.get("f18", 0) or 0),
                "总市值": float(item.get("f20", 0) or 0),
                "流通市值": float(item.get("f21", 0) or 0),
            })
        except (ValueError, TypeError):
            continue

    df = pd.DataFrame(records)
    return df


# ==================== 东方财富 K线数据 ====================

def fetch_eastmoney_kline(symbol: str, period: str = "daily", days: int = 60,
                          start_date: str = None, end_date: str = None,
                          is_index: bool = False) -> list:
    """
    东方财富 push2his API 获取K线数据
    速度: ~200-400ms（直接返回JSON，无akshare封装开销）

    period: daily / weekly / 5min / 15min / 30min / 60min / 1min
    is_index: 指数代码（如000300沪深300）需要传True
    返回: [{date, open, close, high, low, volume, change_pct}, ...]
    """
    if MockMarketDataProvider.is_mock_active():
        return MockMarketDataProvider.generate_mock_kline(symbol, days)

    secid = symbol_to_eastmoney(symbol, is_index=is_index)

    # K线周期映射
    klt_map = {
        "daily": 101, "weekly": 102, "monthly": 103,
        "5min": 5, "15min": 15, "30min": 30, "60min": 60, "1min": 1,
    }
    klt = klt_map.get(period, 101)

    if not start_date:
        end_date_val = end_date or datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y%m%d")
        # 多取一些天数以应对非交易日
        start_date_val = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=days + 30)).strftime("%Y%m%d")
    else:
        start_date_val = start_date
        end_date_val = end_date or "20500101"

    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": klt,
        "fqt": 1,  # 前复权
        "beg": start_date_val,
        "end": end_date_val,
        "lmt": 1000000,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }

    try:
        resp = _session.get(url, params=params, timeout=10)
        data = resp.json()

        if not data or "data" not in data or not data["data"]:
            return []

        klines = data["data"].get("klines", [])
        if not klines:
            return []

        result = []
        for kline in klines:
            parts = kline.split(",")
            if len(parts) < 7:
                continue
            try:
                open_p = round(float(parts[1]), 2)
                close_p = round(float(parts[2]), 2)
                high_p = round(float(parts[3]), 2)
                low_p = round(float(parts[4]), 2)

                # 数据验证：过滤明显错误的K线
                if open_p <= 0 or close_p <= 0:
                    continue
                if high_p < low_p:  # 最高 < 最低
                    continue
                if high_p < open_p or high_p < close_p:  # 最高 < 开盘或收盘
                    continue
                if low_p > open_p or low_p > close_p:  # 最低 > 开盘或收盘
                    continue

                result.append({
                    "date": parts[0],
                    "open": open_p,
                    "close": close_p,
                    "high": high_p,
                    "low": low_p,
                    "volume": int(float(parts[5])),
                    "change_pct": round(float(parts[8]), 2) if len(parts) > 8 else 0,
                })
            except (ValueError, IndexError):
                continue

        # 只返回指定天数
        return result[-days:] if len(result) > days else result

    except Exception as e:
        logger.error(f"东方财富K线请求失败 {symbol}: {e}")
        return []


# ==================== 东方财富 分时数据 ====================

def fetch_eastmoney_intraday(symbol: str) -> list:
    """
    东方财富 push2 API 获取当日分时数据（1分钟级）
    速度: ~200-400ms

    返回: [{time, price, volume, avg}, ...]
    """
    secid = symbol_to_eastmoney(symbol)
    today = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y%m%d")

    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": 1,  # 1分钟
        "fqt": 1,
        "beg": today,
        "end": "20500101",
        "lmt": 1000000,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }

    try:
        resp = _session.get(url, params=params, timeout=10)
        data = resp.json()

        if not data or "data" not in data or not data["data"]:
            return []

        klines = data["data"].get("klines", [])
        if not klines:
            return []

        result = []
        total_volume = 0
        total_amount = 0.0
        for kline in klines:
            parts = kline.split(",")
            if len(parts) < 7:
                continue
            try:
                time_str = parts[0][-5:]  # "2024-01-15 09:31" → "09:31"
                price = float(parts[2])
                volume = int(float(parts[5]))  # 手（1手=100股）
                amount = float(parts[6])  # 元
                total_volume += volume
                total_amount += amount
                # amount单位是元，volume单位是手(×100)才是股数
                avg = round(total_amount / (total_volume * 100), 2) if total_volume > 0 else price

                result.append({
                    "time": time_str,
                    "price": round(price, 2),
                    "volume": volume,
                    "avg": avg,
                })
            except (ValueError, IndexError):
                continue

        return result

    except Exception as e:
        logger.error(f"东方财富分时请求失败 {symbol}: {e}")
        return []


# ==================== 腾讯 K线数据 ====================

def fetch_tencent_kline(symbol: str, period: str = "daily", days: int = 60,
                         is_index: bool = False) -> list:
    """
    腾讯 K线 API
    速度: ~200-400ms
    周期: day / week / month / 5min / 15min / 30min / 60min
    注意: 腾讯K线对指数代码支持有限，建议指数用东方财富

    返回: [{date, open, close, high, low, volume, change_pct}, ...]
    """
    tc = symbol_to_tencent(symbol, is_index=is_index)
    end_date = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d")
    start_date = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=days + 30)).strftime("%Y-%m-%d")

    period_map = {
        "daily": "day", "weekly": "week", "monthly": "month",
        "5min": "5min", "15min": "15min", "30min": "30min", "60min": "60min",
    }
    tc_period = period_map.get(period, "day")

    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {
        "param": f"{tc},{tc_period},{start_date},{end_date},{days + 30},qfq"
    }

    try:
        resp = _session.get(url, params=params, timeout=10)
        data = resp.json()

        stock_data = data.get("data", {}).get(tc, {})
        # 腾讯返回 qfqday 或 day
        klines = stock_data.get("qfqday") or stock_data.get("day") or \
                 stock_data.get(f"qfq{tc_period}") or stock_data.get(tc_period) or []

        if not klines:
            return []

        result = []
        prev_close = None
        for kline in klines:
            if len(kline) < 6:
                continue
            try:
                close = float(kline[2])
                change_pct = round((close / prev_close - 1) * 100, 2) if prev_close else 0
                result.append({
                    "date": kline[0],
                    "open": round(float(kline[1]), 2),
                    "close": close,
                    "high": round(float(kline[3]), 2),
                    "low": round(float(kline[4]), 2),
                    "volume": int(float(kline[5])),
                    "change_pct": change_pct,
                })
                prev_close = close
            except (ValueError, IndexError):
                continue

        return result[-days:] if len(result) > days else result

    except Exception as e:
        logger.error(f"腾讯K线请求失败 {symbol}: {e}")
        return []


# ==================== 腾讯 分时数据 ====================

def fetch_tencent_intraday(symbol: str) -> list:
    """
    腾讯 分时 API
    速度: ~100-200ms

    返回: [{time, price, volume, avg}, ...]
    """
    tc = symbol_to_tencent(symbol)
    url = f"http://web.ifzq.gtimg.cn/appstock/app/minute/query?code={tc}"

    try:
        resp = _session.get(url, timeout=10)
        data = resp.json()

        mins = data.get("data", {}).get(tc, {}).get("data", {}).get("data", [])
        if not mins:
            return []

        result = []
        total_volume = 0
        total_amount = 0.0
        for m in mins:
            # 格式: "HHMM price volume total_amount"
            parts = m.split()
            if len(parts) < 4:
                continue
            try:
                time_str = parts[0]  # "0930"
                # 格式化为 "09:30"
                if len(time_str) == 4:
                    time_str = f"{time_str[:2]}:{time_str[2:]}"
                price = float(parts[1])
                volume = int(parts[2])  # 手（1手=100股）
                amount = float(parts[3])  # 元
                total_volume += volume
                total_amount += amount
                # amount单位是元，volume单位是手(×100)才是股数
                avg = round(total_amount / (total_volume * 100), 2) if total_volume > 0 else price

                result.append({
                    "time": time_str,
                    "price": round(price, 2),
                    "volume": volume,
                    "avg": avg,
                })
            except (ValueError, IndexError):
                continue

        return result

    except Exception as e:
        logger.error(f"腾讯分时请求失败 {symbol}: {e}")
        return []


# ==================== 快速单只股票实时报价 ====================

def fetch_realtime_single(symbol: str) -> Optional[Dict]:
    """
    获取单只股票实时行情（腾讯API，最快）
    速度: ~50-100ms
    """
    result = fetch_tencent_realtime([symbol])
    return result.get(symbol)


# ==================== 新浪全市场行情（备用） ====================

def _fetch_sina_page(page: int) -> list:
    """获取新浪行情单页数据（使用独立 session，线程安全）"""
    url = (
        f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"Market_Center.getHQNodeData?page={page}&num=100"
        f"&sort=changepercent&asc=0&node=hs_a&symbol=&_s_r_a=page"
    )
    try:
        # 每个线程用独立 session，避免线程安全问题
        s = requests.Session()
        s.trust_env = False
        resp = s.get(url, headers={"Referer": "http://finance.sina.com.cn"}, timeout=10)
        if resp.status_code != 200:
            return []
        items = resp.json()
        if not items or not isinstance(items, list):
            return []
        return items
    except Exception as e:
        logger.warning(f"新浪行情第{page}页请求失败: {e}")
        return []


def fetch_sina_all_stocks(max_pages: int = 55) -> Optional[Any]:
    """
    新浪财经 API 获取全市场A股行情（东方财富被封时的备用方案）
    速度: ~3-8s（并发分页获取，10线程 × 55页）

    返回: 与 fetch_eastmoney_all_stocks 相同格式的 DataFrame
    """
    import pandas as pd

    # 先获取第1页确定总页数
    first_page = _fetch_sina_page(1)
    if not first_page:
        return None

    all_items = list(first_page)
    actual_max = max_pages

    # 如果第1页不足100条，只有1页
    if len(first_page) < 100:
        actual_max = 1
    else:
        # 估算总页数（~5000只A股 / 100 = 50页）
        actual_max = min(max_pages, 55)

    # 并发获取剩余页
    if actual_max > 1:
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(_fetch_sina_page, p): p
                       for p in range(2, actual_max + 1)}
            for future in as_completed(futures):
                page_data = future.result()
                if page_data:
                    all_items.extend(page_data)
                # 如果某页返回不足100条，后面大概率也没了
                if len(page_data) < 100:
                    # 取消剩余未完成的
                    for f in futures:
                        f.cancel()
                    break

    # 转换为 DataFrame
    all_records = []
    for item in all_items:
        try:
            code = item.get("code", "")
            # 只要沪深A股 (6/0/3开头)，排除北交所(4/8/9开头)
            if not code or code[0] not in ("0", "3", "6"):
                continue
            all_records.append({
                "代码": code,
                "名称": item.get("name", ""),
                "最新价": float(item.get("trade", 0) or 0),
                "涨跌幅": float(item.get("changepercent", 0) or 0),
                "涨跌额": float(item.get("pricechange", 0) or 0),
                "成交量": int(item.get("volume", 0) or 0),
                "成交额": float(item.get("amount", 0) or 0),
                "振幅": 0,  # 新浪API不返回振幅
                "换手率": float(item.get("turnoverratio", 0) or 0),
                "市盈率": float(item.get("per", 0) or 0),
                "量比": 0,
                "今开": float(item.get("open", 0) or 0),
                "最高": float(item.get("high", 0) or 0),
                "最低": float(item.get("low", 0) or 0),
                "昨收": float(item.get("settlement", 0) or 0),
                "总市值": float(item.get("mktcap", 0) or 0) * 10000,  # 万元→元
                "流通市值": float(item.get("nmc", 0) or 0) * 10000,
            })
        except (ValueError, TypeError):
            continue

    if not all_records:
        return None

    logger.info(f"新浪API获取全市场行情: {len(all_records)} 只")
    return pd.DataFrame(all_records)


# ==================== 东方财富全市场行情（akshare兼容） ====================

def fetch_all_stocks_df():
    """
    获取全市场A股行情 DataFrame（兼容 akshare stock_zh_a_spot_em 返回格式）
    速度: ~300-500ms (东方财富) / ~2-5s (新浪备用)

    优先使用东方财富，失败后自动切换新浪
    """
    # 方案1: 东方财富API（最快，单次请求）
    df = fetch_eastmoney_all_stocks()
    if df is not None and not df.empty:
        return df

    # 方案2: 新浪API（备用，分页获取）
    logger.info("东方财富API失败，切换新浪API")
    df = fetch_sina_all_stocks()
    if df is not None and not df.empty:
        return df

    # 方案3: akshare（最后备用）
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            return df
    except Exception as e:
        logger.warning(f"akshare获取全市场行情也失败: {e}")

    return None


# ==================== 性能对比测试 ====================

def benchmark():
    """性能对比: fast_data vs akshare"""
    import time

    print("=" * 60)
    print("数据源性能对比测试")
    print("=" * 60)

    symbol = "000001"

    # 测试1: 单只股票实时报价
    print(f"\n[测试1] 单只股票实时报价 ({symbol})")
    t0 = time.time()
    r1 = fetch_realtime_single(symbol)
    t_fast = time.time() - t0
    print(f"  fast_data (腾讯API): {t_fast*1000:.0f}ms → price={r1.get('price') if r1 else 'N/A'}")

    try:
        import akshare as ak
        t0 = time.time()
        df = ak.stock_zh_a_spot_em()
        row = df[df['代码'] == symbol]
        t_ak = time.time() - t0
        price = float(row.iloc[0]['最新价']) if not row.empty else 'N/A'
        print(f"  akshare:              {t_ak*1000:.0f}ms → price={price}")
        print(f"  加速比: {t_ak/t_fast:.1f}x")
    except Exception as e:
        print(f"  akshare 失败: {e}")

    # 测试2: K线数据
    print(f"\n[测试2] 日K线数据 ({symbol}, 60天)")
    t0 = time.time()
    r2 = fetch_eastmoney_kline(symbol, "daily", 60)
    t_fast = time.time() - t0
    print(f"  fast_data (东方财富): {t_fast*1000:.0f}ms → {len(r2)}条")

    try:
        t0 = time.time()
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq",
                                start_date=(datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=90)).strftime("%Y%m%d"),
                                end_date=datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y%m%d"))
        t_ak = time.time() - t0
        print(f"  akshare:              {t_ak*1000:.0f}ms → {len(df) if df is not None else 0}条")
        print(f"  加速比: {t_ak/t_fast:.1f}x")
    except Exception as e:
        print(f"  akshare 失败: {e}")

    # 测试3: 分时数据
    print(f"\n[测试3] 分时数据 ({symbol})")
    t0 = time.time()
    r3 = fetch_eastmoney_intraday(symbol)
    t_fast = time.time() - t0
    print(f"  fast_data (东方财富): {t_fast*1000:.0f}ms → {len(r3)}条")

    try:
        t0 = time.time()
        df = ak.stock_zh_a_hist_min_em(symbol=symbol, period="1", adjust="qfq")
        t_ak = time.time() - t0
        print(f"  akshare:              {t_ak*1000:.0f}ms → {len(df) if df is not None else 0}条")
        print(f"  加速比: {t_ak/t_fast:.1f}x")
    except Exception as e:
        print(f"  akshare 失败: {e}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    benchmark()
