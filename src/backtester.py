"""
回测模块
使用真实历史K线数据（腾讯/东方财富/akshare 三级降级） + 29个策略进行回测验证
"""

import logging
import random
from typing import Dict, List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import timedelta
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """回测交易记录"""
    date: str
    symbol: str
    action: str  # buy / sell
    price: float
    amount: int
    value: float
    commission: float
    profit: float = 0
    strategy: str = ""


@dataclass
class BacktestResult:
    """回测结果"""
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return: float
    annual_return: float
    max_drawdown: float
    max_drawdown_duration: int
    sharpe_ratio: float
    win_rate: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_profit: float
    avg_loss: float
    profit_factor: float
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[Dict] = field(default_factory=list)
    strategy_stats: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "initial_capital": round(self.initial_capital, 2),
            "final_capital": round(self.final_capital, 2),
            "total_return": round(self.total_return, 2),
            "annual_return": round(self.annual_return, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "max_drawdown_duration": self.max_drawdown_duration,
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "win_rate": round(self.win_rate, 2),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "avg_profit": round(self.avg_profit, 2),
            "avg_loss": round(self.avg_loss, 2),
            "profit_factor": round(self.profit_factor, 2),
            "trades_count": len(self.trades),
            "equity_curve": self.equity_curve[-50:],  # 最近50个数据点
            "strategy_stats": self.strategy_stats
        }


class Backtester:
    """
    回测引擎
    使用akshare真实历史数据 + 策略插件进行回测
    """

    def __init__(self):
        self.commission_rate = 0.0003   # 佣金费率
        self.min_commission = 5         # 最低佣金
        self.stamp_tax_rate = 0.001     # 印花税率（卖出）
        self.slippage = 0.001           # 滑点 0.1%

    def fetch_real_data(self, symbol: str, days: int = 250) -> List[Dict]:
        """
        获取真实历史K线数据（通过 data_cache，自动走腾讯 → 东方财富 → akshare 三级降级）

        Args:
            symbol: 股票代码（如 000001）
            days: 回溯天数

        Returns:
            [{date, open, close, high, low, volume}, ...]
        """
        try:
            from data_cache import get_kline
            kline = get_kline(symbol, period="daily", days=days)
            if kline:
                data = kline[-days:] if len(kline) > days else kline
                logger.info(f"获取 {symbol} 真实数据 {len(data)} 条")
                return data

            # data_cache 内部已尝试腾讯 → 东方财富 → akshare，全部失败时降级到mock
            logger.warning(f"无法获取 {symbol} 的历史数据（三级数据源均失败），使用mock数据")
            return self.generate_mock_data(symbol, days)

        except Exception as e:
            logger.error(f"获取 {symbol} 真实数据失败: {e}，使用mock数据")
            return self.generate_mock_data(symbol, days)

    def generate_mock_data(self, symbol: str, days: int = 250) -> List[Dict]:
        """生成模拟历史数据（fallback）"""
        data = []
        base_price = random.uniform(10, 100)
        current_price = base_price

        for i in range(days):
            date = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=days - i)).strftime("%Y-%m-%d")
            change = random.uniform(-0.03, 0.03)
            current_price = current_price * (1 + change)
            current_price = max(current_price * 0.5, min(current_price * 1.5, current_price))

            open_price = current_price * (1 + random.uniform(-0.01, 0.01))
            high_price = max(open_price, current_price) * (1 + random.uniform(0, 0.02))
            low_price = min(open_price, current_price) * (1 - random.uniform(0, 0.02))
            volume = random.randint(1000000, 50000000)

            data.append({
                "date": date,
                "open": round(open_price, 2),
                "close": round(current_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "volume": volume
            })

        return data

    def calculate_indicators_for_data(self, data: List[Dict]) -> List[Dict]:
        """
        为历史数据逐日计算技术指标
        复用 api.py 的指标计算逻辑

        Returns:
            带有指标的数据列表
        """
        if not data or len(data) < 20:
            return data

        closes = [d["close"] for d in data]
        highs = [d["high"] for d in data]
        lows = [d["low"] for d in data]

        for i in range(len(data)):
            # MA
            if i >= 4:
                data[i]["ma5"] = sum(closes[i-4:i+1]) / 5
            if i >= 9:
                data[i]["ma10"] = sum(closes[i-9:i+1]) / 10
            if i >= 19:
                data[i]["ma20"] = sum(closes[i-19:i+1]) / 20

            # RSI (14)
            if i >= 14:
                gains = []
                losses = []
                for j in range(i-13, i+1):
                    change = closes[j] - closes[j-1]
                    if change > 0:
                        gains.append(change)
                    else:
                        losses.append(abs(change))
                avg_gain = sum(gains) / 14 if gains else 0
                avg_loss = sum(losses) / 14 if losses else 0.0001
                rs = avg_gain / avg_loss
                data[i]["rsi"] = round(100 - (100 / (1 + rs)), 2)

            # MACD (12, 26, 9) — 简化版
            if i >= 26:
                # 简化EMA计算
                ema12 = sum(closes[i-11:i+1]) / 12
                ema26 = sum(closes[i-25:i+1]) / 26
                macd_val = ema12 - ema26
                data[i]["macd"] = round(macd_val, 4)
                # signal line 近似
                if i >= 35:
                    prev_ema12 = sum(closes[i-12:i]) / 12
                    prev_ema26 = sum(closes[i-26:i]) / 26
                    prev_macd = prev_ema12 - prev_ema26
                    signal_val = (prev_macd + macd_val) / 2
                    data[i]["signal"] = round(signal_val, 4)
                    data[i]["histogram"] = round(macd_val - signal_val, 4)

            # ATR (14)
            if i >= 14:
                trs = []
                for j in range(i-13, i+1):
                    tr = max(
                        highs[j] - lows[j],
                        abs(highs[j] - closes[j-1]) if j > 0 else highs[j] - lows[j],
                        abs(lows[j] - closes[j-1]) if j > 0 else highs[j] - lows[j]
                    )
                    trs.append(tr)
                data[i]["atr"] = round(sum(trs) / 14, 2)

            # KDJ (9)
            if i >= 9:
                low_min = min(lows[i-8:i+1])
                high_max = max(highs[i-8:i+1])
                if high_max != low_min:
                    rsv = (closes[i] - low_min) / (high_max - low_min) * 100
                else:
                    rsv = 50
                k = rsv * 2/3 + (data[i-1].get("kdj_k", 50)) * 1/3 if i > 0 else rsv
                d = k * 2/3 + (data[i-1].get("kdj_d", 50)) * 1/3 if i > 0 else k
                j = 3 * k - 2 * d
                data[i]["kdj_k"] = round(k, 2)
                data[i]["kdj_d"] = round(d, 2)
                data[i]["kdj_j"] = round(j, 2)

            # OBV 简化
            if i > 0:
                prev_obv = data[i-1].get("obv", 0)
                if closes[i] > closes[i-1]:
                    data[i]["obv"] = prev_obv + data[i]["volume"]
                elif closes[i] < closes[i-1]:
                    data[i]["obv"] = prev_obv - data[i]["volume"]
                else:
                    data[i]["obv"] = prev_obv

        return data

    def _build_indicators(self, day_data: Dict) -> Dict:
        """将单日数据转为策略所需的 indicators dict"""
        return {
            "close": day_data.get("close", 0),
            "ma5": day_data.get("ma5"),
            "ma10": day_data.get("ma10"),
            "ma20": day_data.get("ma20"),
            "rsi": day_data.get("rsi"),
            "macd": day_data.get("macd"),
            "signal": day_data.get("signal"),
            "histogram": day_data.get("histogram"),
            "bb_upper": day_data.get("bb_upper"),
            "bb_middle": day_data.get("bb_middle"),
            "bb_lower": day_data.get("bb_lower"),
            "atr": day_data.get("atr"),
            "kdj_k": day_data.get("kdj_k"),
            "kdj_d": day_data.get("kdj_d"),
            "kdj_j": day_data.get("kdj_j"),
            "obv": day_data.get("obv"),
        }

    def run_strategy_backtest(
        self,
        symbol: str,
        strategy_names: List[str],
        initial_capital: float = 1000000,
        days: int = 60,
        position_size: float = 0.3,
        stock_name: str = ""
    ) -> BacktestResult:
        """
        用真实策略组合回测

        Args:
            symbol: 股票代码
            strategy_names: 要使用的策略名称列表
            initial_capital: 初始资金
            days: 回测天数
            position_size: 单次仓位比例
            stock_name: 股票名称

        Returns:
            BacktestResult
        """
        from strategy_registry import registry

        # 1. 获取真实数据
        data = self.fetch_real_data(symbol, days + 30)
        if not data:
            return self._empty_result(initial_capital, days)

        # 2. 计算技术指标
        data = self.calculate_indicators_for_data(data)

        # 3. 只取最近 days 天作为回测期
        if len(data) > days:
            data = data[-days:]

        if len(data) < 20:
            return self._empty_result(initial_capital, days)

        # 4. 初始化策略实例
        strategies = []
        for name in strategy_names:
            s = registry.get(name)
            if s:
                strategies.append(s)

        if not strategies:
            logger.warning("没有可用策略，使用默认策略")
            strategies = [registry.get("multi_factor")]

        # 5. 模拟交易
        capital = initial_capital
        position = 0
        buy_price = 0
        trades = []
        equity_curve = []
        max_capital = initial_capital
        max_drawdown = 0
        max_drawdown_duration = 0
        current_drawdown_duration = 0
        strategy_stats = {name: {"signals": 0, "wins": 0, "total_profit": 0} for name in strategy_names}

        for i in range(20, len(data)):  # 前20天用于计算指标
            day = data[i]
            price = day["close"]
            history = data[max(0, i-20):i]  # 前20天历史

            # 构建stock_data
            stock_data = {
                "price": price,
                "open": day["open"],
                "high": day["high"],
                "low": day["low"],
                "change_pct": ((price - data[i-1]["close"]) / data[i-1]["close"] * 100) if i > 0 else 0,
                "volume": day["volume"],
                "turnover": 0
            }

            indicators = self._build_indicators(day)

            # 记录权益
            total_value = capital + position * price
            equity_curve.append({
                "date": day["date"],
                "equity": round(total_value, 2)
            })

            # 更新最大回撤
            if total_value > max_capital:
                max_capital = total_value
                current_drawdown_duration = 0
            else:
                current_drawdown_duration += 1
                if current_drawdown_duration > max_drawdown_duration:
                    max_drawdown_duration = current_drawdown_duration

            drawdown = (max_capital - total_value) / max_capital
            if drawdown > max_drawdown:
                max_drawdown = drawdown

            # 6. 用策略分析
            if position == 0:
                # 无持仓，看是否买入
                buy_signals = 0
                total_score = 0
                for s in strategies:
                    try:
                        result = s.analyze(stock_data, indicators, history=history)
                        total_score += result.score * result.confidence
                        if result.signal.value in ("BUY", "STRONG_BUY", "WEAK_BUY"):
                            buy_signals += 1
                    except Exception:
                        pass

                # 多数策略看多时买入
                if buy_signals >= len(strategies) * 0.5 and total_score > 0:
                    # 计算可买数量
                    max_shares = int(capital * position_size / price / 100) * 100
                    if max_shares >= 100:
                        # 加滑点
                        exec_price = price * (1 + self.slippage)
                        commission = max(max_shares * exec_price * self.commission_rate, self.min_commission)
                        cost = max_shares * exec_price + commission
                        if cost <= capital:
                            capital -= cost
                            position = max_shares
                            buy_price = exec_price
                            trades.append(BacktestTrade(
                                date=day["date"],
                                symbol=symbol,
                                action="buy",
                                price=round(exec_price, 2),
                                amount=max_shares,
                                value=round(max_shares * exec_price, 2),
                                commission=round(commission, 2),
                                strategy=",".join(strategy_names)
                            ))

            elif position > 0:
                # 有持仓，看是否卖出
                sell_signals = 0
                for s in strategies:
                    try:
                        result = s.analyze(stock_data, indicators, history=history)
                        if result.signal.value in ("SELL", "STRONG_SELL", "WEAK_SELL"):
                            sell_signals += 1
                    except Exception:
                        pass

                # 止损检查（-8%）
                loss_pct = (price - buy_price) / buy_price
                if loss_pct < -0.08:
                    sell_signals = len(strategies)  # 强制卖出

                # 多数策略看空时卖出
                if sell_signals >= len(strategies) * 0.5:
                    exec_price = price * (1 - self.slippage)
                    revenue = position * exec_price
                    commission = max(revenue * self.commission_rate, self.min_commission)
                    tax = revenue * self.stamp_tax_rate
                    capital += revenue - commission - tax

                    profit = (exec_price - buy_price) * position - commission - tax
                    trades.append(BacktestTrade(
                        date=day["date"],
                        symbol=symbol,
                        action="sell",
                        price=round(exec_price, 2),
                        amount=position,
                        value=round(revenue, 2),
                        commission=round(commission + tax, 2),
                        profit=round(profit, 2),
                        strategy=",".join(strategy_names)
                    ))

                    # 更新策略统计
                    for name in strategy_names:
                        strategy_stats[name]["signals"] += 1
                        if profit > 0:
                            strategy_stats[name]["wins"] += 1
                            strategy_stats[name]["total_profit"] += profit / buy_price * 100

                    position = 0
                    buy_price = 0

        # 7. 计算最终资金
        final_capital = capital + position * data[-1]["close"]

        # 8. 计算统计指标
        total_return = (final_capital - initial_capital) / initial_capital * 100
        annual_return = total_return * 250 / days if days > 0 else 0

        winning_trades = sum(1 for t in trades if t.action == "sell" and t.profit > 0)
        losing_trades = sum(1 for t in trades if t.action == "sell" and t.profit <= 0)
        total_sell_trades = winning_trades + losing_trades
        win_rate = winning_trades / total_sell_trades * 100 if total_sell_trades > 0 else 0

        profits = [t.profit for t in trades if t.action == "sell" and t.profit > 0]
        losses = [t.profit for t in trades if t.action == "sell" and t.profit <= 0]
        avg_profit = sum(profits) / len(profits) if profits else 0
        avg_loss = sum(losses) / len(losses) if losses else 0

        profit_factor = abs(sum(profits) / sum(losses)) if losses else float('inf')

        # 夏普比率
        if len(equity_curve) > 1:
            returns = []
            for j in range(1, len(equity_curve)):
                ret = (equity_curve[j]["equity"] - equity_curve[j-1]["equity"]) / equity_curve[j-1]["equity"]
                returns.append(ret)
            avg_return = sum(returns) / len(returns) if returns else 0
            std_return = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5 if returns else 1
            sharpe_ratio = (avg_return * 250) / (std_return * (250 ** 0.5)) if std_return > 0 else 0
        else:
            sharpe_ratio = 0

        # 策略胜率统计
        for name in strategy_stats:
            s = strategy_stats[name]
            s["win_rate"] = round(s["wins"] / s["signals"] * 100, 1) if s["signals"] > 0 else 0
            s["avg_profit"] = round(s["total_profit"] / s["wins"], 2) if s["wins"] > 0 else 0

        return BacktestResult(
            start_date=data[20]["date"] if len(data) > 20 else data[0]["date"],
            end_date=data[-1]["date"],
            initial_capital=initial_capital,
            final_capital=final_capital,
            total_return=total_return,
            annual_return=annual_return,
            max_drawdown=max_drawdown * 100,
            max_drawdown_duration=max_drawdown_duration,
            sharpe_ratio=sharpe_ratio,
            win_rate=win_rate,
            total_trades=len(trades),
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            avg_profit=avg_profit,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            trades=trades,
            equity_curve=equity_curve,
            strategy_stats=strategy_stats
        )

    def run_portfolio_backtest(
        self,
        symbols: List[str],
        strategy_names: List[str],
        initial_capital: float = 1000000,
        days: int = 60,
        position_per_stock: float = 0.2
    ) -> BacktestResult:
        """
        多股票组合回测，模拟真实比赛场景

        Args:
            symbols: 股票代码列表
            strategy_names: 策略名称列表
            initial_capital: 初始资金
            days: 回测天数
            position_per_stock: 每只股票仓位比例

        Returns:
            组合回测结果
        """
        from strategy_registry import registry

        # 获取所有股票数据
        all_data = {}
        for symbol in symbols:
            data = self.fetch_real_data(symbol, days + 30)
            if data:
                data = self.calculate_indicators_for_data(data)
                if len(data) > days:
                    data = data[-days:]
                all_data[symbol] = data

        if not all_data:
            return self._empty_result(initial_capital, days)

        # 取最短数据长度
        min_len = min(len(d) for d in all_data.values())
        if min_len < 20:
            return self._empty_result(initial_capital, days)

        # 初始化策略
        strategies = []
        for name in strategy_names:
            s = registry.get(name)
            if s:
                strategies.append(s)

        if not strategies:
            return self._empty_result(initial_capital, days)

        # 模拟交易
        capital = initial_capital
        positions = {}  # symbol -> {shares, buy_price}
        trades = []
        equity_curve = []
        max_capital = initial_capital
        max_drawdown = 0

        for i in range(20, min_len):
            # 计算总权益
            total_position_value = 0
            for sym, pos in positions.items():
                if i < len(all_data[sym]):
                    price = all_data[sym][i]["close"]
                    total_position_value += pos["shares"] * price

            total_value = capital + total_position_value
            equity_curve.append({
                "date": list(all_data.values())[0][i]["date"],
                "equity": round(total_value, 2)
            })

            if total_value > max_capital:
                max_capital = total_value
            drawdown = (max_capital - total_value) / max_capital
            if drawdown > max_drawdown:
                max_drawdown = drawdown

            # 对每只股票检查信号
            for sym in symbols:
                if i >= len(all_data[sym]):
                    continue

                day = all_data[sym][i]
                price = day["close"]
                history = all_data[sym][max(0, i-20):i]

                stock_data = {
                    "price": price,
                    "open": day["open"],
                    "high": day["high"],
                    "low": day["low"],
                    "change_pct": ((price - all_data[sym][i-1]["close"]) / all_data[sym][i-1]["close"] * 100) if i > 0 else 0,
                    "volume": day["volume"],
                    "turnover": 0
                }
                indicators = self._build_indicators(day)

                if sym not in positions:
                    # 无持仓，看买入
                    buy_count = 0
                    for s in strategies:
                        try:
                            result = s.analyze(stock_data, indicators, history=history)
                            if result.signal.value in ("BUY", "STRONG_BUY", "WEAK_BUY"):
                                buy_count += 1
                        except Exception:
                            pass

                    if buy_count >= len(strategies) * 0.5:
                        invest = capital * position_per_stock
                        shares = int(invest / price / 100) * 100
                        if shares >= 100:
                            exec_price = price * (1 + self.slippage)
                            cost = shares * exec_price
                            commission = max(cost * self.commission_rate, self.min_commission)
                            total_cost = cost + commission
                            if total_cost <= capital:
                                capital -= total_cost
                                positions[sym] = {"shares": shares, "buy_price": exec_price}
                                trades.append(BacktestTrade(
                                    date=day["date"], symbol=sym, action="buy",
                                    price=round(exec_price, 2), amount=shares,
                                    value=round(cost, 2), commission=round(commission, 2),
                                    strategy=",".join(strategy_names)
                                ))

                else:
                    # 有持仓，看卖出
                    pos = positions[sym]
                    sell_count = 0
                    loss_pct = (price - pos["buy_price"]) / pos["buy_price"]

                    for s in strategies:
                        try:
                            result = s.analyze(stock_data, indicators, history=history)
                            if result.signal.value in ("SELL", "STRONG_SELL", "WEAK_SELL"):
                                sell_count += 1
                        except Exception:
                            pass

                    if sell_count >= len(strategies) * 0.5 or loss_pct < -0.08:
                        exec_price = price * (1 - self.slippage)
                        revenue = pos["shares"] * exec_price
                        commission = max(revenue * self.commission_rate, self.min_commission)
                        tax = revenue * self.stamp_tax_rate
                        capital += revenue - commission - tax

                        profit = (exec_price - pos["buy_price"]) * pos["shares"] - commission - tax
                        trades.append(BacktestTrade(
                            date=day["date"], symbol=sym, action="sell",
                            price=round(exec_price, 2), amount=pos["shares"],
                            value=round(revenue, 2), commission=round(commission + tax, 2),
                            profit=round(profit, 2), strategy=",".join(strategy_names)
                        ))
                        del positions[sym]

        # 最终资金
        final_position_value = sum(
            pos["shares"] * list(all_data.values())[0][-1]["close"]
            for pos in positions.values()
        )
        final_capital = capital + final_position_value

        # 统计
        total_return = (final_capital - initial_capital) / initial_capital * 100
        annual_return = total_return * 250 / days if days > 0 else 0

        winning = sum(1 for t in trades if t.action == "sell" and t.profit > 0)
        losing = sum(1 for t in trades if t.action == "sell" and t.profit <= 0)
        total_sell = winning + losing
        win_rate = winning / total_sell * 100 if total_sell > 0 else 0

        profits = [t.profit for t in trades if t.action == "sell" and t.profit > 0]
        losses_list = [t.profit for t in trades if t.action == "sell" and t.profit <= 0]
        avg_profit = sum(profits) / len(profits) if profits else 0
        avg_loss = sum(losses_list) / len(losses_list) if losses_list else 0
        profit_factor = abs(sum(profits) / sum(losses_list)) if losses_list else float('inf')

        return BacktestResult(
            start_date=equity_curve[0]["date"] if equity_curve else "",
            end_date=equity_curve[-1]["date"] if equity_curve else "",
            initial_capital=initial_capital,
            final_capital=final_capital,
            total_return=total_return,
            annual_return=annual_return,
            max_drawdown=max_drawdown * 100,
            max_drawdown_duration=0,
            sharpe_ratio=0,
            win_rate=win_rate,
            total_trades=len(trades),
            winning_trades=winning,
            losing_trades=losing,
            avg_profit=avg_profit,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            trades=trades,
            equity_curve=equity_curve,
            strategy_stats={}
        )

    def compare_strategies(self, symbol: str, strategy_groups: List[List[str]],
                           days: int = 60, initial_capital: float = 1000000) -> Dict:
        """
        比较不同策略组合的表现

        Args:
            symbol: 股票代码
            strategy_groups: 策略组合列表，如 [["ma_cross", "rsi_extreme"], ["multi_factor"]]
            days: 回测天数
            initial_capital: 初始资金

        Returns:
            比较结果
        """
        results = {}
        for group in strategy_groups:
            name = "+".join(group)
            result = self.run_strategy_backtest(symbol, group, initial_capital, days)
            results[name] = result.to_dict()

        best = max(results.items(), key=lambda x: x[1]["total_return"])
        return {
            "symbol": symbol,
            "strategies": results,
            "best_strategy": best[0],
            "best_return": best[1]["total_return"]
        }

    def _empty_result(self, initial_capital: float, days: int) -> BacktestResult:
        """返回空结果"""
        return BacktestResult(
            start_date="", end_date="",
            initial_capital=initial_capital, final_capital=initial_capital,
            total_return=0, annual_return=0, max_drawdown=0,
            max_drawdown_duration=0, sharpe_ratio=0, win_rate=0,
            total_trades=0, winning_trades=0, losing_trades=0,
            avg_profit=0, avg_loss=0, profit_factor=0
        )


# 全局回测实例
backtester = Backtester()
