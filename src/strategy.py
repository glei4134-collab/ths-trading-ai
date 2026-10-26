"""
AI策略引擎模块
提供多种交易策略和信号生成
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import timedelta
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class TechnicalStrategy:
    """技术分析策略"""

    def __init__(self):
        self.name = "技术分析策略"

    def calculate_ma(self, df: pd.DataFrame, periods: List[int] = [5, 10, 20, 60]) -> pd.DataFrame:
        """计算移动平均线"""
        for period in periods:
            df[f'MA{period}'] = df['收盘'].rolling(window=period).mean()
        return df

    def calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """计算RSI指标"""
        delta = df['收盘'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        return df

    def calculate_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算MACD指标"""
        exp12 = df['收盘'].ewm(span=12, adjust=False).mean()
        exp26 = df['收盘'].ewm(span=26, adjust=False).mean()
        df['MACD'] = exp12 - exp26
        df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['Histogram'] = df['MACD'] - df['Signal']
        return df

    def calculate_bollinger(self, df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        """计算布林带"""
        df['BB_middle'] = df['收盘'].rolling(window=period).mean()
        df['BB_std'] = df['收盘'].rolling(window=period).std()
        df['BB_upper'] = df['BB_middle'] + 2 * df['BB_std']
        df['BB_lower'] = df['BB_middle'] - 2 * df['BB_std']
        return df

    def generate_signal(self, symbol: str, days: int = 60) -> Dict:
        """
        生成交易信号

        Args:
            symbol: 股票代码
            days: 数据天数

        Returns:
            交易信号字典
        """
        try:
            # 获取历史数据
            end_date = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y%m%d")
            start_date = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=days)).strftime("%Y%m%d")

            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"
            )

            if df.empty:
                return {"signal": "HOLD", "reason": "无数据"}

            # 计算技术指标
            df = self.calculate_ma(df)
            df = self.calculate_rsi(df)
            df = self.calculate_macd(df)
            df = self.calculate_bollinger(df)

            # 获取最新数据
            latest = df.iloc[-1]
            prev = df.iloc[-2]

            signals = []
            score = 0

            # MA金叉/死叉
            if latest['MA5'] > latest['MA20'] and prev['MA5'] <= prev['MA20']:
                signals.append("MA5金叉MA20")
                score += 2
            elif latest['MA5'] < latest['MA20'] and prev['MA5'] >= prev['MA20']:
                signals.append("MA5死叉MA20")
                score -= 2

            # RSI分析
            rsi = latest['RSI']
            if pd.notna(rsi):
                if rsi > 70:
                    signals.append(f"RSI超买({rsi:.1f})")
                    score -= 1
                elif rsi < 30:
                    signals.append(f"RSI超卖({rsi:.1f})")
                    score += 1

            # MACD分析
            if pd.notna(latest['MACD']) and pd.notna(latest['Signal']):
                if latest['MACD'] > latest['Signal'] and prev['MACD'] <= prev['Signal']:
                    signals.append("MACD金叉")
                    score += 1
                elif latest['MACD'] < latest['Signal'] and prev['MACD'] >= prev['Signal']:
                    signals.append("MACD死叉")
                    score -= 1

            # 布林带分析
            price = latest['收盘']
            if pd.notna(latest['BB_upper']) and pd.notna(latest['BB_lower']):
                if price > latest['BB_upper']:
                    signals.append("价格突破布林带上轨")
                    score -= 1
                elif price < latest['BB_lower']:
                    signals.append("价格跌破布林带下轨")
                    score += 1

            # 生成最终信号
            if score >= 3:
                signal = "STRONG_BUY"
            elif score >= 1:
                signal = "BUY"
            elif score <= -3:
                signal = "STRONG_SELL"
            elif score <= -1:
                signal = "SELL"
            else:
                signal = "HOLD"

            return {
                "symbol": symbol,
                "signal": signal,
                "score": score,
                "signals": signals,
                "indicators": {
                    "price": float(price),
                    "ma5": round(float(latest['MA5']), 2) if pd.notna(latest['MA5']) else None,
                    "ma20": round(float(latest['MA20']), 2) if pd.notna(latest['MA20']) else None,
                    "rsi": round(float(rsi), 2) if pd.notna(rsi) else None,
                    "macd": round(float(latest['MACD']), 4) if pd.notna(latest['MACD']) else None
                },
                "timestamp": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
            }

        except Exception as e:
            logger.error(f"生成信号失败: {e}")
            return {"signal": "HOLD", "reason": str(e)}


class MomentumStrategy:
    """动量策略"""

    def __init__(self):
        self.name = "动量策略"

    def calculate_momentum(self, df: pd.DataFrame, period: int = 10) -> pd.DataFrame:
        """计算动量指标"""
        df['Momentum'] = df['收盘'] / df['收盘'].shift(period) - 1
        return df

    def calculate_volume_momentum(self, df: pd.DataFrame, period: int = 10) -> pd.DataFrame:
        """计算成交量动量"""
        df['Volume_MA'] = df['成交量'].rolling(window=period).mean()
        df['Volume_Momentum'] = df['成交量'] / df['Volume_MA']
        return df

    def generate_signal(self, symbol: str, days: int = 60) -> Dict:
        """生成动量交易信号"""
        try:
            end_date = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y%m%d")
            start_date = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=days)).strftime("%Y%m%d")

            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"
            )

            if df.empty:
                return {"signal": "HOLD", "reason": "无数据"}

            # 计算动量指标
            df = self.calculate_momentum(df)
            df = self.calculate_volume_momentum(df)

            latest = df.iloc[-1]
            prev = df.iloc[-2]

            signals = []
            score = 0

            # 价格动量
            momentum = latest['Momentum']
            if pd.notna(momentum):
                if momentum > 0.05:  # 5%以上涨幅
                    signals.append(f"强势上涨动量({momentum:.1%})")
                    score += 2
                elif momentum > 0:
                    signals.append(f"温和上涨动量({momentum:.1%})")
                    score += 1
                elif momentum < -0.05:
                    signals.append(f"强势下跌动量({momentum:.1%})")
                    score -= 2
                elif momentum < 0:
                    signals.append(f"温和下跌动量({momentum:.1%})")
                    score -= 1

            # 成交量动量
            vol_momentum = latest['Volume_Momentum']
            if pd.notna(vol_momentum):
                if vol_momentum > 1.5:
                    signals.append(f"成交量放大({vol_momentum:.1f}倍)")
                    if score > 0:
                        score += 1  # 放量上涨
                    else:
                        score -= 1  # 放量下跌
                elif vol_momentum < 0.5:
                    signals.append(f"成交量萎缩({vol_momentum:.1f}倍)")

            # 生成信号
            if score >= 3:
                signal = "STRONG_BUY"
            elif score >= 1:
                signal = "BUY"
            elif score <= -3:
                signal = "STRONG_SELL"
            elif score <= -1:
                signal = "SELL"
            else:
                signal = "HOLD"

            return {
                "symbol": symbol,
                "signal": signal,
                "score": score,
                "signals": signals,
                "indicators": {
                    "price": float(latest['收盘']),
                    "momentum": round(float(momentum), 4) if pd.notna(momentum) else None,
                    "volume_momentum": round(float(vol_momentum), 2) if pd.notna(vol_momentum) else None
                },
                "timestamp": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
            }

        except Exception as e:
            logger.error(f"生成动量信号失败: {e}")
            return {"signal": "HOLD", "reason": str(e)}


class ValueStrategy:
    """价值投资策略"""

    def __init__(self):
        self.name = "价值投资策略"

    def analyze_fundamentals(self, symbol: str) -> Dict:
        """分析基本面"""
        try:
            # 获取股票基本面数据
            df = ak.stock_individual_info_em(symbol=symbol)

            if df.empty:
                return {"score": 0, "reason": "无数据"}

            # 解析基本面数据
            info = {}
            for _, row in df.iterrows():
                info[row['item']] = row['value']

            score = 0
            signals = []

            # 市盈率分析
            if '市盈率(动态)' in info:
                pe = float(info['市盈率(动态)'])
                if pe < 0:
                    signals.append(f"市盈率为负({pe})")
                    score -= 1
                elif pe < 20:
                    signals.append(f"市盈率较低({pe})，估值合理")
                    score += 2
                elif pe < 50:
                    signals.append(f"市盈率适中({pe})")
                    score += 1
                else:
                    signals.append(f"市盈率较高({pe})，估值偏高")
                    score -= 1

            # 市净率分析
            if '市净率' in info:
                pb = float(info['市净率'])
                if pb < 1:
                    signals.append(f"市净率低于1({pb})，可能被低估")
                    score += 2
                elif pb < 3:
                    signals.append(f"市净率适中({pb})")
                    score += 1
                else:
                    signals.append(f"市净率较高({pb})")
                    score -= 1

            return {
                "symbol": symbol,
                "score": score,
                "signals": signals,
                "info": info
            }

        except Exception as e:
            logger.error(f"分析基本面失败: {e}")
            return {"score": 0, "reason": str(e)}


class TradingEngine:
    """交易引擎"""

    def __init__(self):
        self.strategies = {
            "technical": TechnicalStrategy(),
            "momentum": MomentumStrategy(),
            "value": ValueStrategy()
        }
        self.weights = {
            "technical": 0.5,
            "momentum": 0.3,
            "value": 0.2
        }

    def analyze_stock(self, symbol: str, strategies: List[str] = None) -> Dict:
        """
        综合分析股票

        Args:
            symbol: 股票代码
            strategies: 使用的策略列表

        Returns:
            综合分析结果
        """
        if strategies is None:
            strategies = ["technical", "momentum"]

        results = {}
        total_score = 0
        all_signals = []

        for strategy_name in strategies:
            if strategy_name in self.strategies:
                strategy = self.strategies[strategy_name]

                if strategy_name == "value":
                    result = strategy.analyze_fundamentals(symbol)
                else:
                    result = strategy.generate_signal(symbol)

                results[strategy_name] = result
                weight = self.weights.get(strategy_name, 0.33)
                total_score += result.get("score", 0) * weight
                all_signals.extend(result.get("signals", []))

        # 生成综合信号
        if total_score >= 2:
            signal = "BUY"
        elif total_score >= 1:
            signal = "WEAK_BUY"
        elif total_score <= -2:
            signal = "SELL"
        elif total_score <= -1:
            signal = "WEAK_SELL"
        else:
            signal = "HOLD"

        return {
            "symbol": symbol,
            "signal": signal,
            "score": round(total_score, 2),
            "signals": all_signals,
            "strategy_results": results,
            "timestamp": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        }

    def find_opportunities(self, symbols: List[str] = None, limit: int = 10) -> List[Dict]:
        """
        寻找投资机会

        Args:
            symbols: 股票列表（None表示自动筛选）
            limit: 返回数量

        Returns:
            投资机会列表
        """
        if symbols is None:
            # 获取热门股票
            try:
                df = ak.stock_zh_a_spot_em()
                # 筛选涨幅适中的股票
                df_filtered = df[
                    (df['涨跌幅'] > -3) &
                    (df['涨跌幅'] < 5) &
                    (df['成交额'] > 10000000)  # 成交额大于1000万
                ]
                symbols = df_filtered.head(20)['代码'].tolist()
            except Exception as e:
                logger.error(f"获取股票列表失败: {e}")
                return []

        opportunities = []

        for symbol in symbols:
            try:
                result = self.analyze_stock(symbol)
                if result["signal"] in ["BUY", "STRONG_BUY", "WEAK_BUY"]:
                    opportunities.append(result)
            except Exception as e:
                logger.error(f"分析股票 {symbol} 失败: {e}")

        # 按评分排序
        opportunities.sort(key=lambda x: x["score"], reverse=True)

        return opportunities[:limit]


# 全局交易引擎实例
trading_engine = TradingEngine()
