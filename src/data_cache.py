"""
数据缓存层
内存缓存 + TTL 过期
底层使用 fast_data（腾讯/东方财富直连API），速度比 akshare 快 5-10 倍
"""

import time
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import timedelta

logger = logging.getLogger(__name__)

# TTL 常量（秒）
TTL_REALTIME = 30       # 实时行情：30秒
TTL_KLINE = 300         # K线数据：5分钟
TTL_INDICATORS = 300    # 技术指标：5分钟
TTL_INTRADAY = 30       # 分时数据：30秒
TTL_WEEKLY = 600        # 周线数据：10分钟


class DataCache:
    """内存缓存 + TTL 过期"""

    def __init__(self):
        self._store: Dict[str, tuple] = {}  # key -> (value, expire_time)

    def get(self, key: str) -> Optional[Any]:
        """获取缓存，过期返回 None"""
        if key in self._store:
            value, expire = self._store[key]
            if time.time() < expire:
                return value
            del self._store[key]
        return None

    def set(self, key: str, value: Any, ttl: int = 30):
        """设置缓存，ttl 秒后过期"""
        self._store[key] = (value, time.time() + ttl)

    def invalidate(self, pattern: str = ""):
        """按前缀清除缓存"""
        keys_to_delete = [k for k in self._store if k.startswith(pattern)]
        for k in keys_to_delete:
            del self._store[k]

    def clear(self):
        """清除所有缓存"""
        self._store.clear()

    def status(self) -> Dict:
        """获取缓存状态"""
        now = time.time()
        active = sum(1 for _, (_, exp) in self._store.items() if exp > now)
        expired = len(self._store) - active
        return {
            "total_keys": len(self._store),
            "active": active,
            "expired": expired
        }


# 全局缓存实例
cache = DataCache()


# ==================== 缓存化的数据函数 ====================

def get_realtime_quote(symbol: str = None) -> Any:
    """
    获取实时行情（带缓存）
    symbol: 股票代码，None 时返回全市场
    底层: 腾讯API（单只）/ 东方财富API（全市场）
    """
    if symbol is None:
        # 全市场：东方财富API
        cache_key = "realtime:all"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            from fast_data import fetch_all_stocks_df
            df = fetch_all_stocks_df()
            if df is None or df.empty:
                return None
            cache.set(cache_key, df, TTL_REALTIME)
            return df
        except Exception as e:
            logger.error(f"获取全市场行情失败: {e}")
            return None
    else:
        # 单只：腾讯API（更快）
        cache_key = f"realtime:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            from fast_data import fetch_realtime_single
            data = fetch_realtime_single(symbol)
            if data is None:
                return None
            cache.set(cache_key, data, TTL_REALTIME)
            return data
        except Exception as e:
            logger.error(f"获取实时行情失败 {symbol}: {e}")
            return None


def get_realtime_quotes_batch(symbols: List[str]) -> Dict[str, Dict]:
    """
    批量获取实时行情（带缓存）
    底层: 腾讯API（一次HTTP请求，批量返回）
    """
    cache_key = "realtime:batch"
    cached = cache.get(cache_key)
    if cached is not None:
        result = {}
        for sym in symbols:
            if sym in cached:
                result[sym] = cached[sym]
        return result

    try:
        from fast_data import fetch_tencent_realtime
        indexed = fetch_tencent_realtime(symbols)
        if not indexed:
            return {}

        cache.set(cache_key, indexed, TTL_REALTIME)

        result = {}
        for sym in symbols:
            if sym in indexed:
                result[sym] = indexed[sym]
        return result
    except Exception as e:
        logger.error(f"批量获取实时行情失败: {e}")
        return {}


def get_kline(symbol: str, period: str = "daily", days: int = 60,
              start_date: str = None, end_date: str = None) -> list:
    """
    获取K线数据（带缓存）
    底层: 东方财富直连API（跳过akshare封装）
    period: daily / weekly
    返回: [{date, open, close, high, low, volume, change_pct}, ...]
    """
    cache_key = f"kline:{symbol}:{period}:{days}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # 方案1: 腾讯K线API（最稳定，HTTP无代理问题）
    try:
        from fast_data import fetch_tencent_kline
        data = fetch_tencent_kline(symbol, period=period, days=days)
        if data:
            cache.set(cache_key, data, TTL_KLINE)
            return data
    except Exception as e:
        logger.warning(f"腾讯K线请求失败 {symbol}: {e}")

    # 方案2: 东方财富直连API
    try:
        from fast_data import fetch_eastmoney_kline
        data = fetch_eastmoney_kline(symbol, period=period, days=days,
                                     start_date=start_date, end_date=end_date)
        if data:
            cache.set(cache_key, data, TTL_KLINE)
            return data
    except Exception as e:
        logger.warning(f"东方财富K线请求失败 {symbol}: {e}")

    # 方案2: akshare 备用
    try:
        import akshare as ak
        if not start_date:
            end_date = end_date or datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y%m%d")
            start_date = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=days + 30)).strftime("%Y%m%d")

        df = ak.stock_zh_a_hist(
            symbol=symbol, period=period, adjust="qfq",
            start_date=start_date, end_date=end_date
        )
        if df is not None and not df.empty:
            data = []
            for _, row in df.iterrows():
                d = str(row['日期'])[:10]
                data.append({
                    "date": d,
                    "open": round(float(row['开盘']), 2),
                    "close": round(float(row['收盘']), 2),
                    "high": round(float(row['最高']), 2),
                    "low": round(float(row['最低']), 2),
                    "volume": int(row['成交量']),
                    "change_pct": round(float(row['涨跌幅']), 2)
                })
            cache.set(cache_key, data, TTL_KLINE)
            return data
    except Exception as e:
        logger.warning(f"akshare K线也失败 {symbol}: {e}")

    return []


def get_index_kline(symbol: str, period: str = "daily", days: int = 60) -> list:
    """
    获取指数K线数据（直接使用东方财富API，跳过腾讯——腾讯K线不支持指数代码）
    例如: get_index_kline("000300", days=90) 获取沪深300日K线
    """
    cache_key = f"kline_index:{symbol}:{period}:{days}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # 方案1: 东方财富直连API（指数需要 is_index=True）
    try:
        from fast_data import fetch_eastmoney_kline
        data = fetch_eastmoney_kline(symbol, period=period, days=days, is_index=True)
        if data:
            cache.set(cache_key, data, TTL_KLINE)
            return data
    except Exception as e:
        logger.warning(f"东方财富指数K线请求失败 {symbol}: {e}")

    # 方案2: akshare 备用
    try:
        import akshare as ak
        end_date = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y%m%d")
        start_date = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=days + 30)).strftime("%Y%m%d")
        df = ak.index_zh_a_hist(
            symbol=symbol, period=period if period != "daily" else "daily",
            start_date=start_date, end_date=end_date
        )
        if df is not None and not df.empty:
            data = []
            for _, row in df.iterrows():
                d = str(row.get('日期', ''))[:10]
                data.append({
                    "date": d,
                    "open": round(float(row.get('开盘', 0)), 2),
                    "close": round(float(row.get('收盘', 0)), 2),
                    "high": round(float(row.get('最高', 0)), 2),
                    "low": round(float(row.get('最低', 0)), 2),
                    "volume": int(row.get('成交量', 0)),
                    "change_pct": round(float(row.get('涨跌幅', 0)), 2)
                })
            cache.set(cache_key, data, TTL_KLINE)
            return data
    except Exception as e:
        logger.warning(f"akshare 指数K线也失败 {symbol}: {e}")

    return []


def get_market_basket_quote() -> Dict:
    """
    获取市场篮子股票的实时报价（作为指数替代方案）
    选取各行业龙头股的加权平均涨跌幅作为市场代理指标
    """
    # 各行业龙头 + 权重（近似指数权重）
    basket = {
        "600519": 0.08,   # 贵州茅台（白酒）
        "000858": 0.04,   # 五粮液（白酒）
        "601318": 0.06,   # 中国平安（金融）
        "600036": 0.05,   # 招商银行（银行）
        "000001": 0.03,   # 平安银行（银行）
        "601888": 0.04,   # 中国中免（旅游）
        "002714": 0.03,   # 牧原股份（农业）
        "600900": 0.04,   # 长江电力（电力）
        "601899": 0.03,   # 紫金矿业（有色）
        "002475": 0.05,   # 立讯精密（电子）
        "300750": 0.05,   # 宁德时代（新能源）
        "000333": 0.03,   # 美的集团（家电）
        "600276": 0.04,   # 恒瑞医药（医药）
        "601012": 0.03,   # 隆基绿能（光伏）
        "603259": 0.03,   # 药明康德（CXO）
        "600031": 0.03,   # 三一重工（机械）
        "601166": 0.03,   # 兴业银行（银行）
        "002352": 0.03,   # 顺丰控股（物流）
        "600809": 0.03,   # 山西汾酒（白酒）
        "000568": 0.03,   # 泸州老窖（白酒）
    }
    try:
        from fast_data import fetch_tencent_realtime
        quotes = fetch_tencent_realtime(list(basket.keys()))
        if not quotes:
            return None

        weighted_change = 0.0
        total_weight = 0.0
        matched = 0
        for code, weight in basket.items():
            if code in quotes and quotes[code].get("change_pct"):
                weighted_change += quotes[code]["change_pct"] * weight
                total_weight += weight
                matched += 1

        if total_weight > 0 and matched >= 10:
            avg_change = weighted_change / total_weight
            return {
                "avg_change_pct": round(avg_change, 2),
                "sample_size": matched,
                "source": "market_basket"
            }
    except Exception as e:
        logger.warning(f"市场篮子报价获取失败: {e}")

    return None


def get_intraday(symbol: str) -> list:
    """
    获取分时数据（带缓存）
    底层: 东方财富直连API
    返回: [{time, price, volume, avg}, ...]
    """
    cache_key = f"intraday:{symbol}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # 方案1: 腾讯分时API（最稳定，HTTP无代理问题）
    try:
        from fast_data import fetch_tencent_intraday
        data = fetch_tencent_intraday(symbol)
        if data:
            cache.set(cache_key, data, TTL_INTRADAY)
            return data
    except Exception as e:
        logger.warning(f"腾讯分时请求失败 {symbol}: {e}")

    # 方案2: 东方财富直连API
    try:
        from fast_data import fetch_eastmoney_intraday
        data = fetch_eastmoney_intraday(symbol)
        if data:
            cache.set(cache_key, data, TTL_INTRADAY)
            return data
    except Exception as e:
        logger.warning(f"东方财富分时请求失败 {symbol}: {e}")

    # 方案2: akshare 备用
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist_min_em(symbol=symbol, period="1", adjust="qfq")
        if df is None or df.empty:
            return []

        data = []
        for _, row in df.iterrows():
            time_str = str(row.get('时间', ''))[-5:]
            data.append({
                "time": time_str,
                "price": round(float(row.get('收盘', 0)), 2),
                "volume": int(row.get('成交量', 0)),
                "avg": round(float(row.get('均价', 0)), 2) if '均价' in row else 0
            })

        cache.set(cache_key, data, TTL_INTRADAY)
        return data
    except Exception as e:
        logger.error(f"akshare 分时也失败 {symbol}: {e}")
        return []


def get_kline_with_indicators(symbol: str, days: int = 60) -> list:
    """
    获取K线+技术指标（带缓存，5分钟TTL）
    返回: [{date, open, close, high, low, volume, ma5, ma10, ma20, rsi, macd, signal, histogram}, ...]
    """
    cache_key = f"chart:{symbol}:{days}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    kline = get_kline(symbol, period="daily", days=days)
    if not kline:
        return []

    import pandas as pd
    df = pd.DataFrame(kline)
    close = df['close'].astype(float)

    # 均线
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()

    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line

    start_idx = max(0, len(df) - days)
    data = []
    for i in range(start_idx, len(df)):
        data.append({
            "date": df['date'].iloc[i],
            "open": round(float(df['open'].iloc[i]), 2),
            "close": round(float(df['close'].iloc[i]), 2),
            "high": round(float(df['high'].iloc[i]), 2),
            "low": round(float(df['low'].iloc[i]), 2),
            "volume": int(df['volume'].iloc[i]),
            "ma5": round(float(ma5.iloc[i]), 2) if pd.notna(ma5.iloc[i]) else None,
            "ma10": round(float(ma10.iloc[i]), 2) if pd.notna(ma10.iloc[i]) else None,
            "ma20": round(float(ma20.iloc[i]), 2) if pd.notna(ma20.iloc[i]) else None,
            "rsi": round(float(rsi.iloc[i]), 2) if pd.notna(rsi.iloc[i]) else None,
            "macd": round(float(macd_line.iloc[i]), 4) if pd.notna(macd_line.iloc[i]) else None,
            "signal": round(float(signal_line.iloc[i]), 4) if pd.notna(signal_line.iloc[i]) else None,
            "histogram": round(float(histogram.iloc[i]), 4) if pd.notna(histogram.iloc[i]) else None,
        })

    cache.set(cache_key, data, TTL_INDICATORS)
    return data


def compute_indicators(kline_data: list) -> Dict:
    """
    从K线数据计算技术指标（带缓存）
    kline_data: get_kline() 返回的列表
    返回: {ma5, ma10, ma20, rsi, macd, signal, histogram, bb_upper, bb_middle, bb_lower, atr, adx, kdj_*}
    """
    if not kline_data or len(kline_data) < 20:
        return {}

    import pandas as pd

    close = pd.Series([d['close'] for d in kline_data], dtype=float)
    high = pd.Series([d['high'] for d in kline_data], dtype=float)
    low = pd.Series([d['low'] for d in kline_data], dtype=float)

    indicators = {"close": round(close.iloc[-1], 2)}

    # 均线
    for p in [5, 10, 20, 60]:
        if len(close) >= p:
            indicators[f"ma{p}"] = round(close.rolling(p).mean().iloc[-1], 2)

    # RSI
    if len(close) >= 15:
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        indicators["rsi"] = round((100 - (100 / (1 + rs))).iloc[-1], 2)

    # MACD
    if len(close) >= 26:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        histogram = macd_line - signal_line
        indicators["macd"] = round(macd_line.iloc[-1], 4)
        indicators["signal"] = round(signal_line.iloc[-1], 4)
        indicators["histogram"] = round(histogram.iloc[-1], 4)

    # 布林带
    if len(close) >= 20:
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        indicators["bb_upper"] = round((sma20 + 2 * std20).iloc[-1], 2)
        indicators["bb_middle"] = round(sma20.iloc[-1], 2)
        indicators["bb_lower"] = round((sma20 - 2 * std20).iloc[-1], 2)

    # ATR
    if len(close) >= 15:
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs()
        ], axis=1).max(axis=1)
        indicators["atr"] = round(tr.rolling(14).mean().iloc[-1], 2)

    # ADX
    if len(close) >= 28:
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs()
        ], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        plus_di = 100 * (plus_dm.rolling(14).mean() / atr14)
        minus_di = 100 * (minus_dm.rolling(14).mean() / atr14)
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
        adx = dx.rolling(14).mean()
        indicators["adx"] = round(adx.iloc[-1], 2)

    # KDJ
    if len(close) >= 9:
        low_min = low.rolling(9).min()
        high_max = high.rolling(9).max()
        rsv = (close - low_min) / (high_max - low_min) * 100
        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        j = 3 * k - 2 * d
        indicators["kdj_k"] = round(k.iloc[-1], 2)
        indicators["kdj_d"] = round(d.iloc[-1], 2)
        indicators["kdj_j"] = round(j.iloc[-1], 2)

    return indicators
