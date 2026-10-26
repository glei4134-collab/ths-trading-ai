"""
同花顺客户端集成模块
使用easytrader连接同花顺客户端进行实盘交易
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

try:
    import easytrader
    EASYTRADER_AVAILABLE = True
except ImportError:
    EASYTRADER_AVAILABLE = False
    logger.warning("easytrader未安装，将使用模拟交易模式")


class THSBroker:
    """同花顺券商接口"""

    def __init__(self, mode: str = "simulation"):
        """
        初始化券商接口

        Args:
            mode: 交易模式 ("real" 实盘, "simulation" 模拟)
        """
        self.mode = mode
        self.user = None
        self.connected = False
        self.positions = {}
        self.balance = 1000000  # 模拟初始资金

    def connect(self, exe_path: str = None, user: str = None, password: str = None) -> bool:
        """
        连接同花顺客户端

        Args:
            exe_path: 同花顺下单程序路径
            user: 用户名
            password: 密码

        Returns:
            连接是否成功
        """
        if self.mode == "simulation":
            logger.info("使用模拟交易模式")
            self.connected = True
            return True

        if not EASYTRADER_AVAILABLE:
            logger.error("easytrader未安装，无法进行实盘交易")
            return False

        try:
            self.user = easytrader.use('ths')
            self.user.connect(exe_path)
            self.user.prepare(user=user, password=password)
            self.connected = True
            logger.info("成功连接同花顺客户端")
            return True
        except Exception as e:
            logger.error(f"连接同花顺客户端失败: {e}")
            return False

    def buy(self, symbol: str, price: float, amount: int) -> Dict:
        """
        买入股票

        Args:
            symbol: 股票代码
            price: 买入价格
            amount: 买入数量

        Returns:
            交易结果
        """
        try:
            if self.mode == "simulation":
                return self._simulate_buy(symbol, price, amount)

            if not self.connected or not self.user:
                return {"success": False, "message": "未连接"}

            # 调用easytrader买入
            result = self.user.buy(symbol, price=price, amount=amount)
            logger.info(f"买入成功: {symbol} {amount}股 @ {price}元")

            return {
                "success": True,
                "message": "买入成功",
                "symbol": symbol,
                "amount": amount,
                "price": price,
                "total": price * amount,
                "time": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
            }

        except Exception as e:
            logger.error(f"买入失败: {e}")
            return {"success": False, "message": str(e)}

    def sell(self, symbol: str, price: float, amount: int) -> Dict:
        """
        卖出股票

        Args:
            symbol: 股票代码
            price: 卖出价格
            amount: 卖出数量

        Returns:
            交易结果
        """
        try:
            if self.mode == "simulation":
                return self._simulate_sell(symbol, price, amount)

            if not self.connected or not self.user:
                return {"success": False, "message": "未连接"}

            # 调用easytrader卖出
            result = self.user.sell(symbol, price=price, amount=amount)
            logger.info(f"卖出成功: {symbol} {amount}股 @ {price}元")

            return {
                "success": True,
                "message": "卖出成功",
                "symbol": symbol,
                "amount": amount,
                "price": price,
                "total": price * amount,
                "time": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
            }

        except Exception as e:
            logger.error(f"卖出失败: {e}")
            return {"success": False, "message": str(e)}

    def get_positions(self) -> List[Dict]:
        """
        查询持仓

        Returns:
            持仓列表
        """
        try:
            if self.mode == "simulation":
                return self._simulate_get_positions()

            if not self.connected or not self.user:
                return []

            positions = self.user.position
            return positions

        except Exception as e:
            logger.error(f"查询持仓失败: {e}")
            return []

    def get_balance(self) -> Dict:
        """
        查询余额

        Returns:
            余额信息
        """
        try:
            if self.mode == "simulation":
                return self._simulate_get_balance()

            if not self.connected or not self.user:
                return {}

            balance = self.user.balance
            return balance

        except Exception as e:
            logger.error(f"查询余额失败: {e}")
            return {}

    def cancel_order(self, order_id: str) -> bool:
        """
        撤单

        Args:
            order_id: 订单ID

        Returns:
            撤单是否成功
        """
        try:
            if self.mode == "simulation":
                logger.info(f"模拟撤单: {order_id}")
                return True

            if not self.connected or not self.user:
                return False

            self.user.cancel_entrust(order_id)
            logger.info(f"撤单成功: {order_id}")
            return True

        except Exception as e:
            logger.error(f"撤单失败: {e}")
            return False

    def get_today_trades(self) -> List[Dict]:
        """
        查询今日成交

        Returns:
            成交记录列表
        """
        try:
            if self.mode == "simulation":
                return []

            if not self.connected or not self.user:
                return []

            trades = self.user.today_trades
            return trades

        except Exception as e:
            logger.error(f"查询今日成交失败: {e}")
            return []

    def get_today_entrusts(self) -> List[Dict]:
        """
        查询今日委托

        Returns:
            委托记录列表
        """
        try:
            if self.mode == "simulation":
                return []

            if not self.connected or not self.user:
                return []

            entrusts = self.user.today_entrusts
            return entrusts

        except Exception as e:
            logger.error(f"查询今日委托失败: {e}")
            return []

    # 模拟交易方法
    def _simulate_buy(self, symbol: str, price: float, amount: int) -> Dict:
        """模拟买入"""
        total_cost = price * amount

        if total_cost > self.balance:
            return {
                "success": False,
                "message": f"余额不足，需要 {total_cost:.2f} 元"
            }

        self.balance -= total_cost

        if symbol in self.positions:
            self.positions[symbol]['amount'] += amount
            self.positions[symbol]['total_cost'] += total_cost
        else:
            self.positions[symbol] = {
                'amount': amount,
                'total_cost': total_cost,
                'avg_price': price
            }

        logger.info(f"模拟买入: {symbol} {amount}股 @ {price}元")

        return {
            "success": True,
            "message": "模拟买入成功",
            "symbol": symbol,
            "amount": amount,
            "price": price,
            "total": total_cost,
            "time": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        }

    def _simulate_sell(self, symbol: str, price: float, amount: int) -> Dict:
        """模拟卖出"""
        if symbol not in self.positions:
            return {
                "success": False,
                "message": f"未持有股票 {symbol}"
            }

        position = self.positions[symbol]
        if amount > position['amount']:
            return {
                "success": False,
                "message": f"持仓不足，当前持有 {position['amount']} 股"
            }

        total_income = price * amount
        profit = (price - position['avg_price']) * amount

        self.balance += total_income
        position['amount'] -= amount

        if position['amount'] == 0:
            del self.positions[symbol]

        logger.info(f"模拟卖出: {symbol} {amount}股 @ {price}元")

        return {
            "success": True,
            "message": "模拟卖出成功",
            "symbol": symbol,
            "amount": amount,
            "price": price,
            "total": total_income,
            "profit": profit,
            "time": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        }

    def _simulate_get_positions(self) -> List[Dict]:
        """模拟查询持仓"""
        return [
            {
                "证券代码": symbol,
                "股票名称": symbol,
                "持仓数量": pos['amount'],
                "可用数量": pos['amount'],
                "成本价": pos['avg_price'],
                "当前价": pos['avg_price'],  # 简化处理
                "市值": pos['total_cost']
            }
            for symbol, pos in self.positions.items()
        ]

    def _simulate_get_balance(self) -> Dict:
        """模拟查询余额"""
        return {
            "资产金额": self.balance,
            "可用金额": self.balance,
            "冻结金额": 0,
            "市值": 0
        }


class AutoTrader:
    """自动交易器"""

    def __init__(self, broker: THSBroker):
        self.broker = broker
        self.trading_log = []

    def execute_signal(self, signal: Dict) -> Dict:
        """
        执行交易信号

        Args:
            signal: 交易信号

        Returns:
            执行结果
        """
        symbol = signal.get("symbol")
        action = signal.get("signal")

        if not symbol or not action:
            return {"success": False, "message": "无效信号"}

        # 根据信号执行交易
        if action in ["BUY", "STRONG_BUY"]:
            return self._execute_buy(symbol, signal)
        elif action in ["SELL", "STRONG_SELL"]:
            return self._execute_sell(symbol, signal)
        else:
            return {"success": True, "message": "持有观望", "action": "HOLD"}

    def _execute_buy(self, symbol: str, signal: Dict) -> Dict:
        """执行买入"""
        # 获取当前价格
        price = signal.get("indicators", {}).get("price", 0)
        if price <= 0:
            return {"success": False, "message": "无法获取价格"}

        # 计算买入数量（使用10%资金）
        balance = self.broker.get_balance()
        available = balance.get("可用金额", 0)
        buy_amount = int(available * 0.1 / price / 100) * 100  # 100股整数倍

        if buy_amount < 100:
            return {"success": False, "message": "资金不足"}

        # 执行买入
        result = self.broker.buy(symbol, price, buy_amount)

        # 记录日志
        log_entry = {
            "time": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S"),
            "action": "BUY",
            "symbol": symbol,
            "price": price,
            "amount": buy_amount,
            "signal": signal.get("signal"),
            "result": result
        }
        self.trading_log.append(log_entry)

        return result

    def _execute_sell(self, symbol: str, signal: Dict) -> Dict:
        """执行卖出"""
        # 获取持仓
        positions = self.broker.get_positions()

        # 查找该股票的持仓
        for pos in positions:
            if pos.get("证券代码") == symbol:
                amount = pos.get("持仓数量", 0)
                if amount > 0:
                    price = signal.get("indicators", {}).get("price", 0)
                    if price <= 0:
                        return {"success": False, "message": "无法获取价格"}

                    # 执行卖出
                    result = self.broker.sell(symbol, price, amount)

                    # 记录日志
                    log_entry = {
                        "time": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S"),
                        "action": "SELL",
                        "symbol": symbol,
                        "price": price,
                        "amount": amount,
                        "signal": signal.get("signal"),
                        "result": result
                    }
                    self.trading_log.append(log_entry)

                    return result

        return {"success": False, "message": f"未持有股票 {symbol}"}

    def get_trading_log(self) -> List[Dict]:
        """获取交易日志"""
        return self.trading_log
