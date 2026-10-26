"""
同花顺模拟比赛AI交易MCP Server
提供股票行情查询、交易执行、策略分析等功能
"""

from mcp.server.fastmcp import FastMCP
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import timedelta
import json
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 创建MCP Server实例
mcp = FastMCP("ths-trading-server")

# 全局变量存储交易状态
trading_state = {
    "positions": {},
    "balance": 1000000,  # 初始资金100万
    "trades": [],
    "connected": False
}


@mcp.tool()
async def get_stock_realtime(symbol: str) -> str:
    """
    获取股票实时行情

    Args:
        symbol: 股票代码，如 "000001"

    Returns:
        股票实时价格、涨跌幅等信息
    """
    try:
        df = ak.stock_zh_a_spot_em()
        stock = df[df['代码'] == symbol]

        if stock.empty:
            return f"未找到股票 {symbol}"

        stock_info = stock.iloc[0]

        result = {
            "股票代码": stock_info['代码'],
            "股票名称": stock_info['名称'],
            "最新价": float(stock_info['最新价']),
            "涨跌幅": f"{float(stock_info['涨跌幅']):.2f}%",
            "涨跌额": float(stock_info['涨跌额']),
            "成交量": int(stock_info['成交量']),
            "成交额": float(stock_info['成交额']),
            "振幅": f"{float(stock_info['振幅']):.2f}%",
            "最高": float(stock_info['最高']),
            "最低": float(stock_info['最低']),
            "今开": float(stock_info['今开']),
            "昨收": float(stock_info['昨收']),
            "换手率": f"{float(stock_info['换手率']):.2f}%"
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"获取股票行情失败: {e}")
        return f"获取行情失败: {str(e)}"


@mcp.tool()
async def get_stock_history(symbol: str, period: str = "daily", days: int = 30) -> str:
    """
    获取股票历史行情

    Args:
        symbol: 股票代码
        period: 周期 ("daily", "weekly", "monthly")
        days: 获取天数

    Returns:
        历史K线数据
    """
    try:
        end_date = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y%m%d")
        start_date = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=days)).strftime("%Y%m%d")

        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust="qfq"
        )

        if df.empty:
            return f"未找到股票 {symbol} 的历史数据"

        result = []
        for _, row in df.iterrows():
            result.append({
                "日期": row['日期'].strftime("%Y-%m-%d") if hasattr(row['日期'], 'strftime') else str(row['日期']),
                "开盘": float(row['开盘']),
                "收盘": float(row['收盘']),
                "最高": float(row['最高']),
                "最低": float(row['最低']),
                "成交量": int(row['成交量']),
                "成交额": float(row['成交额']),
                "振幅": f"{float(row['振幅']):.2f}%",
                "涨跌幅": f"{float(row['涨跌幅']):.2f}%",
                "涨跌额": float(row['涨跌额']),
                "换手率": f"{float(row['换手率']):.2f}%"
            })

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"获取历史行情失败: {e}")
        return f"获取历史行情失败: {str(e)}"


@mcp.tool()
async def calculate_indicators(symbol: str, days: int = 60) -> str:
    """
    计算股票技术指标

    Args:
        symbol: 股票代码
        days: 计算天数

    Returns:
        MA、RSI、MACD等技术指标
    """
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
            return f"未找到股票 {symbol} 的数据"

        # 计算移动平均线
        df['MA5'] = df['收盘'].rolling(window=5).mean()
        df['MA10'] = df['收盘'].rolling(window=10).mean()
        df['MA20'] = df['收盘'].rolling(window=20).mean()
        df['MA60'] = df['收盘'].rolling(window=60).mean()

        # 计算RSI
        delta = df['收盘'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # 计算MACD
        exp12 = df['收盘'].ewm(span=12, adjust=False).mean()
        exp26 = df['收盘'].ewm(span=26, adjust=False).mean()
        df['MACD'] = exp12 - exp26
        df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['Histogram'] = df['MACD'] - df['Signal']

        # 计算布林带
        df['BB_middle'] = df['收盘'].rolling(window=20).mean()
        df['BB_std'] = df['收盘'].rolling(window=20).std()
        df['BB_upper'] = df['BB_middle'] + 2 * df['BB_std']
        df['BB_lower'] = df['BB_middle'] - 2 * df['BB_std']

        latest = df.iloc[-1]

        result = {
            "股票代码": symbol,
            "当前价格": float(latest['收盘']),
            "移动平均线": {
                "MA5": round(float(latest['MA5']), 2) if pd.notna(latest['MA5']) else None,
                "MA10": round(float(latest['MA10']), 2) if pd.notna(latest['MA10']) else None,
                "MA20": round(float(latest['MA20']), 2) if pd.notna(latest['MA20']) else None,
                "MA60": round(float(latest['MA60']), 2) if pd.notna(latest['MA60']) else None
            },
            "RSI": round(float(latest['RSI']), 2) if pd.notna(latest['RSI']) else None,
            "MACD": {
                "DIF": round(float(latest['MACD']), 4) if pd.notna(latest['MACD']) else None,
                "DEA": round(float(latest['Signal']), 4) if pd.notna(latest['Signal']) else None,
                "柱状图": round(float(latest['Histogram']), 4) if pd.notna(latest['Histogram']) else None
            },
            "布林带": {
                "上轨": round(float(latest['BB_upper']), 2) if pd.notna(latest['BB_upper']) else None,
                "中轨": round(float(latest['BB_middle']), 2) if pd.notna(latest['BB_middle']) else None,
                "下轨": round(float(latest['BB_lower']), 2) if pd.notna(latest['BB_lower']) else None
            }
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"计算技术指标失败: {e}")
        return f"计算技术指标失败: {str(e)}"


@mcp.tool()
async def analyze_stock(symbol: str) -> str:
    """
    综合分析股票，生成交易建议

    Args:
        symbol: 股票代码

    Returns:
        综合分析报告和交易建议
    """
    try:
        end_date = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y%m%d")
        start_date = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=60)).strftime("%Y%m%d")

        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq"
        )

        if df.empty:
            return f"未找到股票 {symbol} 的数据"

        # 计算技术指标
        df['MA5'] = df['收盘'].rolling(window=5).mean()
        df['MA20'] = df['收盘'].rolling(window=20).mean()

        delta = df['收盘'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        exp12 = df['收盘'].ewm(span=12, adjust=False).mean()
        exp26 = df['收盘'].ewm(span=26, adjust=False).mean()
        df['MACD'] = exp12 - exp26
        df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        signals = []
        score = 0

        # MA金叉/死叉
        if latest['MA5'] > latest['MA20'] and prev['MA5'] <= prev['MA20']:
            signals.append("MA金叉（买入信号）")
            score += 2
        elif latest['MA5'] < latest['MA20'] and prev['MA5'] >= prev['MA20']:
            signals.append("MA死叉（卖出信号）")
            score -= 2

        # RSI分析
        rsi = latest['RSI']
        if pd.notna(rsi):
            if rsi > 70:
                signals.append(f"RSI超买（{rsi:.1f}）")
                score -= 1
            elif rsi < 30:
                signals.append(f"RSI超卖（{rsi:.1f}）")
                score += 1

        # MACD分析
        if pd.notna(latest['MACD']) and pd.notna(latest['Signal']):
            if latest['MACD'] > latest['Signal'] and prev['MACD'] <= prev['Signal']:
                signals.append("MACD金叉（买入信号）")
                score += 1
            elif latest['MACD'] < latest['Signal'] and prev['MACD'] >= prev['Signal']:
                signals.append("MACD死叉（卖出信号）")
                score -= 1

        # 价格与MA关系
        price = latest['收盘']
        if price > latest['MA5'] > latest['MA20']:
            signals.append("价格在均线上方（多头排列）")
            score += 1
        elif price < latest['MA5'] < latest['MA20']:
            signals.append("价格在均线下方（空头排列）")
            score -= 1

        if score >= 3:
            recommendation = "强烈建议买入"
        elif score >= 1:
            recommendation = "建议买入"
        elif score <= -3:
            recommendation = "强烈建议卖出"
        elif score <= -1:
            recommendation = "建议卖出"
        else:
            recommendation = "建议持有观望"

        result = {
            "股票代码": symbol,
            "当前价格": float(price),
            "技术指标": {
                "MA5": round(float(latest['MA5']), 2) if pd.notna(latest['MA5']) else None,
                "MA20": round(float(latest['MA20']), 2) if pd.notna(latest['MA20']) else None,
                "RSI": round(float(rsi), 2) if pd.notna(rsi) else None,
                "MACD": round(float(latest['MACD']), 4) if pd.notna(latest['MACD']) else None
            },
            "分析信号": signals,
            "综合评分": score,
            "交易建议": recommendation,
            "分析时间": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"分析股票失败: {e}")
        return f"分析股票失败: {str(e)}"


@mcp.tool()
async def get_market_overview() -> str:
    """
    获取市场整体概况

    Returns:
        大盘指数、涨跌统计等信息
    """
    try:
        sh_index = ak.stock_zh_index_daily(symbol="sh000001")
        sh_latest = sh_index.iloc[-1]

        sz_index = ak.stock_zh_index_daily(symbol="sz399001")
        sz_latest = sz_index.iloc[-1]

        cy_index = ak.stock_zh_index_daily(symbol="sz399006")
        cy_latest = cy_index.iloc[-1]

        df = ak.stock_zh_a_spot_em()

        up_count = len(df[df['涨跌幅'] > 0])
        down_count = len(df[df['涨跌幅'] < 0])
        flat_count = len(df[df['涨跌幅'] == 0])

        limit_up = len(df[df['涨跌幅'] >= 9.9])
        limit_down = len(df[df['涨跌幅'] <= -9.9])

        result = {
            "大盘指数": {
                "上证指数": {"点位": float(sh_latest['close']), "日期": str(sh_latest['date'])},
                "深证成指": {"点位": float(sz_latest['close']), "日期": str(sz_latest['date'])},
                "创业板指": {"点位": float(cy_latest['close']), "日期": str(cy_latest['date'])}
            },
            "涨跌统计": {
                "上涨家数": up_count,
                "下跌家数": down_count,
                "平盘家数": flat_count,
                "涨停家数": limit_up,
                "跌停家数": limit_down
            },
            "市场情绪": "乐观" if up_count > down_count else "悲观" if down_count > up_count else "震荡",
            "数据时间": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"获取市场概况失败: {e}")
        return f"获取市场概况失败: {str(e)}"


@mcp.tool()
async def search_stocks(keyword: str, limit: int = 10) -> str:
    """
    搜索股票

    Args:
        keyword: 搜索关键词（股票代码或名称）
        limit: 返回结果数量

    Returns:
        匹配的股票列表
    """
    try:
        df = ak.stock_zh_a_spot_em()
        mask = df['代码'].str.contains(keyword) | df['名称'].str.contains(keyword)
        result_df = df[mask].head(limit)

        if result_df.empty:
            return f"未找到匹配 '{keyword}' 的股票"

        result = []
        for _, row in result_df.iterrows():
            result.append({
                "代码": row['代码'],
                "名称": row['名称'],
                "最新价": float(row['最新价']),
                "涨跌幅": f"{float(row['涨跌幅']):.2f}%"
            })

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"搜索股票失败: {e}")
        return f"搜索股票失败: {str(e)}"


@mcp.tool()
async def get_hot_stocks(limit: int = 20) -> str:
    """
    获取热门股票（按成交额排序）

    Args:
        limit: 返回数量

    Returns:
        热门股票列表
    """
    try:
        df = ak.stock_zh_a_spot_em()
        df_sorted = df.sort_values('成交额', ascending=False).head(limit)

        result = []
        for _, row in df_sorted.iterrows():
            result.append({
                "代码": row['代码'],
                "名称": row['名称'],
                "最新价": float(row['最新价']),
                "涨跌幅": f"{float(row['涨跌幅']):.2f}%",
                "成交额": f"{float(row['成交额'])/10000:.2f}万",
                "换手率": f"{float(row['换手率']):.2f}%"
            })

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"获取热门股票失败: {e}")
        return f"获取热门股票失败: {str(e)}"


@mcp.tool()
async def buy_stock(symbol: str, amount: int, price: float = None) -> str:
    """
    模拟买入股票

    Args:
        symbol: 股票代码
        amount: 买入数量（必须是100的整数倍）
        price: 买入价格（None表示市价）

    Returns:
        交易结果
    """
    try:
        if amount % 100 != 0:
            return "买入数量必须是100的整数倍"

        if price is None:
            df = ak.stock_zh_a_spot_em()
            stock = df[df['代码'] == symbol]
            if stock.empty:
                return f"未找到股票 {symbol}"
            price = float(stock.iloc[0]['最新价'])

        total_cost = price * amount

        if total_cost > trading_state['balance']:
            return f"余额不足，需要 {total_cost:.2f} 元，当前余额 {trading_state['balance']:.2f} 元"

        trading_state['balance'] -= total_cost

        if symbol in trading_state['positions']:
            trading_state['positions'][symbol]['amount'] += amount
            trading_state['positions'][symbol]['total_cost'] += total_cost
        else:
            trading_state['positions'][symbol] = {
                'amount': amount,
                'total_cost': total_cost,
                'avg_price': price
            }

        trade = {
            'time': datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S"),
            'symbol': symbol,
            'action': '买入',
            'amount': amount,
            'price': price,
            'total': total_cost
        }
        trading_state['trades'].append(trade)

        result = {
            "交易状态": "成功",
            "股票代码": symbol,
            "买入数量": amount,
            "买入价格": price,
            "交易金额": total_cost,
            "剩余余额": trading_state['balance'],
            "交易时间": trade['time']
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"买入股票失败: {e}")
        return f"买入股票失败: {str(e)}"


@mcp.tool()
async def sell_stock(symbol: str, amount: int, price: float = None) -> str:
    """
    模拟卖出股票

    Args:
        symbol: 股票代码
        amount: 卖出数量
        price: 卖出价格（None表示市价）

    Returns:
        交易结果
    """
    try:
        if symbol not in trading_state['positions']:
            return f"未持有股票 {symbol}"

        position = trading_state['positions'][symbol]

        if amount > position['amount']:
            return f"持仓不足，当前持有 {position['amount']} 股"

        if price is None:
            df = ak.stock_zh_a_spot_em()
            stock = df[df['代码'] == symbol]
            if stock.empty:
                return f"未找到股票 {symbol}"
            price = float(stock.iloc[0]['最新价'])

        total_income = price * amount
        trading_state['balance'] += total_income

        position['amount'] -= amount
        if position['amount'] == 0:
            del trading_state['positions'][symbol]
        else:
            position['total_cost'] -= position['avg_price'] * amount

        profit = (price - position['avg_price']) * amount

        trade = {
            'time': datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S"),
            'symbol': symbol,
            'action': '卖出',
            'amount': amount,
            'price': price,
            'total': total_income,
            'profit': profit
        }
        trading_state['trades'].append(trade)

        result = {
            "交易状态": "成功",
            "股票代码": symbol,
            "卖出数量": amount,
            "卖出价格": price,
            "交易金额": total_income,
            "盈亏": profit,
            "剩余余额": trading_state['balance'],
            "交易时间": trade['time']
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"卖出股票失败: {e}")
        return f"卖出股票失败: {str(e)}"


@mcp.tool()
async def get_positions() -> str:
    """
    查询当前持仓

    Returns:
        持仓列表和账户信息
    """
    try:
        positions = []
        total_market_value = 0

        for symbol, pos in trading_state['positions'].items():
            df = ak.stock_zh_a_spot_em()
            stock = df[df['代码'] == symbol]

            if not stock.empty:
                current_price = float(stock.iloc[0]['最新价'])
                market_value = current_price * pos['amount']
                profit = (current_price - pos['avg_price']) * pos['amount']
                profit_rate = (current_price / pos['avg_price'] - 1) * 100
            else:
                current_price = pos['avg_price']
                market_value = pos['total_cost']
                profit = 0
                profit_rate = 0

            total_market_value += market_value

            positions.append({
                "股票代码": symbol,
                "持有数量": pos['amount'],
                "成本价": round(pos['avg_price'], 2),
                "当前价": round(current_price, 2),
                "市值": round(market_value, 2),
                "盈亏": round(profit, 2),
                "盈亏比例": f"{profit_rate:.2f}%"
            })

        result = {
            "账户信息": {
                "可用余额": round(trading_state['balance'], 2),
                "持仓市值": round(total_market_value, 2),
                "总资产": round(trading_state['balance'] + total_market_value, 2)
            },
            "持仓列表": positions,
            "查询时间": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"查询持仓失败: {e}")
        return f"查询持仓失败: {str(e)}"


@mcp.tool()
async def get_balance() -> str:
    """
    查询账户余额

    Returns:
        账户余额信息
    """
    try:
        total_market_value = 0
        for symbol, pos in trading_state['positions'].items():
            df = ak.stock_zh_a_spot_em()
            stock = df[df['代码'] == symbol]
            if not stock.empty:
                current_price = float(stock.iloc[0]['最新价'])
                total_market_value += current_price * pos['amount']

        result = {
            "可用余额": round(trading_state['balance'], 2),
            "持仓市值": round(total_market_value, 2),
            "总资产": round(trading_state['balance'] + total_market_value, 2),
            "持仓股票数": len(trading_state['positions']),
            "查询时间": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"查询余额失败: {e}")
        return f"查询余额失败: {str(e)}"


@mcp.tool()
async def get_trade_history(limit: int = 20) -> str:
    """
    查询交易历史

    Args:
        limit: 返回记录数量

    Returns:
        交易记录列表
    """
    try:
        trades = trading_state['trades'][-limit:]

        result = {
            "交易记录": trades,
            "总交易次数": len(trading_state['trades']),
            "查询时间": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"查询交易历史失败: {e}")
        return f"查询交易历史失败: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
