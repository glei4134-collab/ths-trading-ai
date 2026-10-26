"""
自动交易引擎
根据策略信号自动执行交易
"""

import json
import logging
import threading
import time as _time
from typing import Dict, List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

# 配置目录
from paths import CONFIG_DIR
TRADE_CONFIG_FILE = CONFIG_DIR / "trade_config.json"

# 导入数据存储模块
from data_store import data_store
from competition import competition_manager


class OrderType(Enum):
    """订单类型"""
    MARKET = "market"  # 市价单
    LIMIT = "limit"    # 限价单


class OrderStatus(Enum):
    """订单状态"""
    PENDING = "pending"      # 待执行
    FILLED = "filled"        # 已成交
    PARTIAL = "partial"      # 部分成交
    CANCELLED = "cancelled"  # 已取消
    FAILED = "failed"        # 失败


@dataclass
class TradeOrder:
    """交易订单"""
    order_id: str
    symbol: str
    action: str  # buy / sell
    amount: int
    price: float
    order_type: OrderType = OrderType.LIMIT
    status: OrderStatus = OrderStatus.PENDING
    created_at: str = ""
    filled_at: str = ""
    filled_price: float = 0
    filled_amount: int = 0
    commission: float = 0
    notes: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> Dict:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "action": self.action,
            "amount": self.amount,
            "price": self.price,
            "order_type": self.order_type.value,
            "status": self.status.value,
            "created_at": self.created_at,
            "filled_at": self.filled_at,
            "filled_price": self.filled_price,
            "filled_amount": self.filled_amount,
            "commission": self.commission,
            "notes": self.notes
        }


@dataclass
class Position:
    """持仓"""
    symbol: str
    name: str
    amount: int
    avg_price: float
    current_price: float = 0
    market_value: float = 0
    profit: float = 0
    profit_rate: float = 0
    stop_loss: float = 0
    take_profit: float = 0
    highest_price: float = 0  # 持仓期间最高价（用于移动止损）
    buy_date: str = ""

    def update_price(self, price: float):
        """更新价格"""
        self.current_price = price
        self.market_value = price * self.amount
        self.profit = (price - self.avg_price) * self.amount
        self.profit_rate = (price / self.avg_price - 1) * 100 if self.avg_price > 0 else 0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "amount": self.amount,
            "avg_price": round(self.avg_price, 2),
            "current_price": round(self.current_price, 2),
            "market_value": round(self.market_value, 2),
            "profit": round(self.profit, 2),
            "profit_rate": round(self.profit_rate, 2),
            "stop_loss": round(self.stop_loss, 2),
            "take_profit": round(self.take_profit, 2),
            "buy_date": self.buy_date
        }


class AutoTrader:
    """
    自动交易引擎
    根据策略信号自动执行交易
    支持绑定到特定账户（通过 data_store 参数）
    """

    def __init__(self, ds=None):
        self.enabled = False  # 自动交易开关
        self.positions: Dict[str, Position] = {}
        self.orders: List[TradeOrder] = []
        self.config = self.load_config()
        self.trade_history = []
        self._monitor_thread = None
        self._monitor_running = False
        self._monitor_callbacks = []  # 监控回调函数列表
        self._last_snapshot_hour = -1  # 上次快照的小时数，避免重复保存
        # 绑定数据存储
        self._ds = ds or data_store
        # 从持久化存储加载余额和持仓
        self._load_from_data_store()

    def load_config(self) -> Dict:
        """加载配置"""
        default_config = {
            "max_position_ratio": 0.15,      # 单只股票最大仓位比例 (从30%降到15%)
            "max_total_position": 0.6,       # 最大总仓位比例 (从80%降到60%)
            "stop_loss_ratio": 0.05,         # 止损比例 (从8%降到5%)
            "take_profit_ratio": 0.20,       # 止盈比例 (从15%升到20%)
            "min_trade_amount": 100,         # 最小交易数量
            "trade_amount_step": 100,        # 交易数量步长
            "commission_rate": 0.0003,       # 佣金费率
            "min_commission": 5,             # 最低佣金
            "stamp_tax_rate": 0.001,         # 印花税率（卖出时）
            "auto_stop_loss": True,          # 自动止损
            "auto_take_profit": True,        # 自动止盈
            "trailing_stop_pct": 0.03,       # 移动止损回撤比例 (从5%降到3%)
            "monitor_interval": 30,          # 监控检查间隔（秒）
            "max_daily_trades": 10,          # 每日最大交易次数
            "trading_hours_only": True,      # 仅在交易时间执行
        }

        try:
            if TRADE_CONFIG_FILE.exists():
                with open(TRADE_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    saved_config = json.load(f)
                    default_config.update(saved_config)
        except Exception as e:
            logger.error(f"加载配置失败: {e}")

        return default_config

    def save_config(self):
        """保存配置"""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(TRADE_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存配置失败: {e}")

    def _load_from_data_store(self):
        """从持久化存储加载数据"""
        self.balance = self._ds.account.get("balance", 1000000)
        # 加载持仓到 Position 对象
        for symbol, pos_data in self._ds.positions.items():
            self.positions[symbol] = Position(
                symbol=symbol,
                name=pos_data.get("name", symbol),
                amount=pos_data.get("amount", 0),
                avg_price=pos_data.get("avg_price", 0),
                current_price=pos_data.get("current_price", pos_data.get("avg_price", 0)),
                stop_loss=pos_data.get("stop_loss", 0),
                take_profit=pos_data.get("take_profit", 0),
                highest_price=pos_data.get("highest_price", pos_data.get("avg_price", 0)),
                buy_date=pos_data.get("buy_date", "")
            )
            self.positions[symbol].update_price(self.positions[symbol].current_price)
        # 加载交易历史
        self.trade_history = self._ds.get_trades(limit=500)
        logger.info(f"从持久化存储加载: 余额 {self.balance}, 持仓 {len(self.positions)} 只")

    def sync_from_ths(self):
        """从THS同步真实持仓（启动时调用，覆盖本地数据）"""
        try:
            from ths_connector import get_active_connector
            conn, conn_type = get_active_connector()
            if not conn:
                logger.info("THS未连接，跳过持仓同步")
                return False

            result = conn.get_positions()
            if not result.get("success"):
                logger.warning(f"THS持仓查询失败: {result.get('message')}")
                return False

            data = result.get("data", {})
            positions = data.get("positions", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            fund = data.get("fund", {}) if isinstance(data, dict) else {}
            balance = fund.get("available", data.get("balance", self.balance)) if isinstance(data, dict) else self.balance

            # 用THS真实数据更新
            self.balance = balance
            old_count = len(self.positions)
            self.positions.clear()

            for p in positions:
                symbol = p.get("symbol", "")
                if not symbol:
                    continue
                self.positions[symbol] = Position(
                    symbol=symbol,
                    name=p.get("name", symbol),
                    amount=p.get("amount", 0),
                    avg_price=p.get("cost_price", 0),
                    current_price=p.get("market_value", 0) / max(p.get("amount", 1), 1),
                    buy_date=""
                )

            # 持久化
            self._ds.update_balance(self.balance)
            for sym, pos in self.positions.items():
                self._ds.update_position(sym, {
                    "name": pos.name, "amount": pos.amount,
                    "avg_price": pos.avg_price, "current_price": pos.current_price,
                })

            logger.info(f"✅ THS持仓同步完成: 余额={self.balance}, 持仓={len(self.positions)}只 (原{old_count}只)")
            return True
        except Exception as e:
            logger.error(f"THS持仓同步异常: {e}")
            return False

    def update_config(self, **kwargs):
        """更新配置"""
        for key, value in kwargs.items():
            if key in self.config:
                self.config[key] = value
        self.save_config()

    def get_config(self) -> Dict:
        """获取配置"""
        return self.config

    def calculate_commission(self, price: float, amount: int, action: str) -> float:
        """计算手续费"""
        trade_amount = price * amount
        commission = max(trade_amount * self.config["commission_rate"], self.config["min_commission"])

        # 卖出时收取印花税
        if action == "sell":
            commission += trade_amount * self.config["stamp_tax_rate"]

        return round(commission, 2)

    def calculate_position_size(self, price: float, signal_strength: float = 1.0) -> int:
        """计算仓位大小（接入比赛阶段动态调整）"""
        # 获取比赛阶段基础仓位
        try:
            from risk_manager import risk_manager
            days = risk_manager.get_competition_days_elapsed()
            total = risk_manager.get_total_competition_days()
            phase = risk_manager.get_phase_config(days, total)
            base_ratio = phase["base_ratio"]
        except Exception:
            base_ratio = 0.15

        # 可用资金
        available = self.balance

        # 单只股票最大投入 = 基础仓位 × 信号强度
        max_invest = available * base_ratio * signal_strength

        # 计算可买数量（100股整数倍）
        shares = int(max_invest / price / 100) * 100

        return max(shares, 0)

    def check_risk(self, symbol: str, action: str, price: float, amount: int) -> Dict:
        """风控检查（完整比赛规则）"""
        warnings = []
        allowed = True

        # 获取比赛阶段动态配置
        try:
            from risk_manager import risk_manager
            days = risk_manager.get_competition_days_elapsed()
            total = risk_manager.get_total_competition_days()
            phase = risk_manager.get_phase_config(days, total)
            max_single = phase["max_single_position"]
            max_total = phase["max_total_position"]
        except Exception:
            max_single = self.config["max_position_ratio"]
            max_total = self.config["max_total_position"]

        # 1. 检查交易时间
        if self.config["trading_hours_only"]:
            if not competition_manager.is_trading_hours():
                market = competition_manager.get_market_status()
                warnings.append(f"当前非交易时间 ({market['status_text']})")

        # 2. 检查整手（100股整数倍）
        if amount % 100 != 0:
            warnings.append(f"买入数量必须是100的整数倍，当前{amount}股")
            allowed = False

        # 3. 检查涨跌停
        try:
            from fast_data import fetch_realtime_single
            quote = fetch_realtime_single(symbol)
            if quote:
                current_price = float(quote.get("price", 0))
                pre_close = float(quote.get("pre_close", 0))
                if pre_close > 0:
                    change_pct = (current_price / pre_close - 1) * 100
                    if action == "buy" and change_pct >= 9.9:
                        warnings.append(f"{symbol} 涨停中(+{change_pct:.1f}%)，无法买入")
                        allowed = False
                    elif action == "sell" and change_pct <= -9.9:
                        warnings.append(f"{symbol} 跌停中({change_pct:.1f}%)，无法卖出")
                        allowed = False
        except Exception:
            pass

        # 4. 检查流动性（成交量过低警告）
        try:
            from fast_data import fetch_realtime_single
            quote = fetch_realtime_single(symbol)
            if quote:
                volume = int(quote.get("volume", 0))
                if volume < 10000:
                    warnings.append(f"{symbol} 成交量较低({volume})，可能无法成交")
        except Exception:
            pass

        # 5. 检查比赛范围
        try:
            if not competition_manager.is_tradable(symbol):
                warnings.append(f"{symbol} 不在比赛允许的交易范围内")
                allowed = False
        except Exception:
            pass

        # 买入检查
        if action == "buy":
            trade_amount = price * amount
            commission = self.calculate_commission(price, amount, action)
            total_cost = trade_amount + commission

            # 检查余额
            if total_cost > self.balance:
                warnings.append(f"余额不足: 需要 {total_cost:.2f}，可用 {self.balance:.2f}")
                allowed = False

            # 检查单只股票仓位
            if symbol in self.positions:
                current_value = self.positions[symbol].current_price * self.positions[symbol].amount
                new_value = current_value + trade_amount
                total_assets = self.balance + sum(p.market_value for p in self.positions.values())
                if total_assets > 0 and new_value / total_assets > max_single:
                    warnings.append(f"单只股票仓位将超过 {max_single*100:.0f}%（比赛阶段限制）")
                    allowed = False

            # 检查总仓位
            total_position_value = sum(p.market_value for p in self.positions.values())
            total_assets = self.balance + total_position_value
            if total_assets > 0:
                new_position_ratio = (total_position_value + trade_amount) / total_assets
                if new_position_ratio > max_total:
                    warnings.append(f"总仓位将超过 {max_total*100:.0f}%（比赛阶段限制）")
                    allowed = False

        # 卖出检查
        elif action == "sell":
            if symbol not in self.positions:
                warnings.append(f"未持有 {symbol}")
                allowed = False
            elif amount > self.positions[symbol].amount:
                warnings.append(f"持仓不足: 持有 {self.positions[symbol].amount}，卖出 {amount}")
                allowed = False
            # T+1 检查
            elif self.positions[symbol].buy_date == datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d"):
                warnings.append(f"{symbol} 今日买入，T+1限制无法卖出")
                allowed = False

        return {
            "allowed": allowed,
            "warnings": warnings
        }

    def create_order(self, symbol: str, action: str, amount: int, price: float,
                    order_type: OrderType = OrderType.LIMIT) -> TradeOrder:
        """创建订单"""
        import uuid

        order = TradeOrder(
            order_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            action=action,
            amount=amount,
            price=price,
            order_type=order_type
        )

        self.orders.append(order)
        return order

    def execute_order(self, order: TradeOrder, stock_data: Dict = None) -> Dict:
        """执行订单 — 先尝试真实THS连接器，不可用时退回内存模拟"""
        # 风控检查
        risk_check = self.check_risk(order.symbol, order.action, order.price, order.amount)
        if not risk_check["allowed"]:
            order.status = OrderStatus.FAILED
            order.notes = "; ".join(risk_check["warnings"])
            return {
                "success": False,
                "message": order.notes
            }

        # ★ 尝试通过真实THS连接器执行交易
        real_executed = False
        try:
            from ths_connector import get_active_connector
            conn, conn_type = get_active_connector()
            if conn and getattr(conn, 'connected', False):
                if order.action == "buy":
                    real_result = conn.buy(order.symbol, order.price, order.amount)
                else:
                    real_result = conn.sell(order.symbol, order.price, order.amount)

                if real_result.get("success"):
                    real_executed = True
                    order.notes = f"通过{conn_type}连接器执行"
                    logger.info(f"✅ 真实交易执行成功: {order.action} {order.symbol} {order.amount}股 via {conn_type}")
                else:
                    order.status = OrderStatus.FAILED
                    order.notes = f"THS交易失败: {real_result.get('message', '未知错误')}"
                    logger.warning(f"❌ 真实交易失败: {order.symbol} - {order.notes}")
                    return {"success": False, "message": order.notes}
            else:
                logger.info(f"THS连接器不可用，使用内存模拟执行: {order.symbol}")
        except Exception as e:
            logger.warning(f"THS连接器调用异常，退回内存模拟: {e}")

        # 计算手续费
        commission = self.calculate_commission(order.price, order.amount, order.action)

        # 执行买入
        if order.action == "buy":
            trade_amount = order.price * order.amount
            total_cost = trade_amount + commission

            # 安全检查：余额不足时阻止
            if total_cost > self.balance:
                order.status = OrderStatus.FAILED
                order.notes = f"余额不足: 需要{total_cost:.2f}，可用{self.balance:.2f}"
                return {"success": False, "message": order.notes}

            # 扣除资金
            self.balance -= total_cost

            # 更新持仓
            if order.symbol in self.positions:
                pos = self.positions[order.symbol]
                pos.amount += order.amount
                pos.avg_price = (pos.avg_price * (pos.amount - order.amount) + order.price * order.amount) / pos.amount
            else:
                name = stock_data.get("name", order.symbol) if stock_data else order.symbol
                self.positions[order.symbol] = Position(
                    symbol=order.symbol,
                    name=name,
                    amount=order.amount,
                    avg_price=order.price,
                    current_price=order.price,
                    market_value=order.price * order.amount,
                    buy_date=datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d")
                )

            # 设置止损止盈
            if self.config["auto_stop_loss"]:
                self.positions[order.symbol].stop_loss = order.price * (1 - self.config["stop_loss_ratio"])
            if self.config["auto_take_profit"]:
                self.positions[order.symbol].take_profit = order.price * (1 + self.config["take_profit_ratio"])
            self.positions[order.symbol].highest_price = order.price

        # 执行卖出
        elif order.action == "sell":
            # 安全检查：持仓不足时阻止
            if order.symbol not in self.positions or order.amount > self.positions[order.symbol].amount:
                order.status = OrderStatus.FAILED
                order.notes = f"持仓不足: 无法卖出{order.amount}股"
                return {"success": False, "message": order.notes}

            trade_amount = order.price * order.amount
            net_income = trade_amount - commission

            # 增加资金
            self.balance += net_income

            # 更新持仓
            if order.symbol in self.positions:
                self.positions[order.symbol].amount -= order.amount
                if self.positions[order.symbol].amount == 0:
                    del self.positions[order.symbol]

        # 更新订单状态
        order.status = OrderStatus.FILLED
        order.filled_at = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        order.filled_price = order.price
        order.filled_amount = order.amount
        order.commission = commission

        # 记录交易
        trade_record = {
            "time": order.filled_at,
            "order_id": order.order_id,
            "symbol": order.symbol,
            "name": stock_data.get("name", order.symbol) if stock_data else order.symbol,
            "action": order.action,
            "amount": order.amount,
            "price": order.price,
            "total": order.price * order.amount,
            "commission": commission,
            "balance": round(self.balance, 2),
            "mode": "real" if real_executed else "simulated"
        }
        self.trade_history.append(trade_record)

        # ===== 持久化到 data_store =====
        # 更新余额
        self._ds.update_balance(self.balance)

        # 更新持仓
        if order.action == "buy":
            pos = self.positions[order.symbol]
            self._ds.update_position(order.symbol, {
                "symbol": order.symbol,
                "name": trade_record["name"],
                "amount": pos.amount,
                "avg_price": pos.avg_price,
                "current_price": pos.current_price,
                "total_cost": pos.avg_price * pos.amount,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "buy_date": pos.buy_date
            })
        elif order.action == "sell":
            if order.symbol in self.positions:
                pos = self.positions[order.symbol]
                self._ds.update_position(order.symbol, {
                    "symbol": order.symbol,
                    "name": trade_record["name"],
                    "amount": pos.amount,
                    "avg_price": pos.avg_price,
                    "current_price": pos.current_price,
                    "total_cost": pos.avg_price * pos.amount,
                    "stop_loss": pos.stop_loss,
                    "take_profit": pos.take_profit,
                    "buy_date": pos.buy_date
                })
            else:
                # 清仓
                self._ds.update_position(order.symbol, {"amount": 0})

        # 保存交易记录
        self._ds.add_trade(trade_record)

        logger.info(f"订单执行成功: {order.action} {order.symbol} x{order.amount} @ {order.price}")

        # 发送桌面通知
        try:
            from notification import notification_manager
            action_cn = "买入" if order.action == "buy" else "卖出"
            notification_manager.add({
                "type": "trade",
                "level": "success",
                "title": f"{'📈' if order.action == 'buy' else '📉'} {action_cn}成功",
                "message": f"{trade_record['name']} {order.symbol}\n"
                          f"{action_cn}{order.amount}股 @ ¥{order.price}\n"
                          f"金额: ¥{order.price * order.amount:,.0f}",
            })
        except Exception:
            pass

        return {
            "success": True,
            "message": f"{'买入' if order.action == 'buy' else '卖出'}成功",
            "data": trade_record
        }

    def check_stop_loss_take_profit(self, stock_prices: Dict[str, float]) -> List[Dict]:
        """检查止损止盈（含移动止损）"""
        signals = []

        for symbol, pos in list(self.positions.items()):
            if symbol in stock_prices:
                current_price = stock_prices[symbol]
                pos.update_price(current_price)

                # 计算盈亏比例
                profit_pct = (current_price / pos.avg_price - 1) * 100 if pos.avg_price > 0 else 0

                # 移动止损：盈利超过5%时启动
                if profit_pct > 5:
                    # 盈利5%后，止损上移到成本价+2%
                    new_stop = pos.avg_price * 1.02
                    if new_stop > pos.stop_loss:
                        pos.stop_loss = new_stop
                        logger.info(f"{symbol} 移动止损更新: {pos.stop_loss:.2f} (盈利{profit_pct:.1f}%)")

                # 检查止损
                if self.config["auto_stop_loss"] and pos.stop_loss > 0:
                    if current_price <= pos.stop_loss:
                        reason = "触发移动止损" if profit_pct > 0 else "触发止损"
                        signals.append({
                            "symbol": symbol,
                            "action": "sell",
                            "reason": f"{reason}: 当前价 {current_price} <= 止损价 {pos.stop_loss:.2f}",
                            "amount": pos.amount,
                            "price": current_price
                        })

                # 检查止盈
                if self.config["auto_take_profit"] and pos.take_profit > 0:
                    if current_price >= pos.take_profit:
                        signals.append({
                            "symbol": symbol,
                            "action": "sell",
                            "reason": f"触发止盈: 当前价 {current_price} >= 止盈价 {pos.take_profit}",
                            "amount": pos.amount,
                            "price": current_price
                        })

        return signals

    def get_positions(self) -> List[Dict]:
        """获取所有持仓"""
        return [pos.to_dict() for pos in self.positions.values()]

    def get_account_summary(self) -> Dict:
        """获取账户摘要"""
        total_market_value = sum(pos.market_value for pos in self.positions.values())
        total_profit = sum(pos.profit for pos in self.positions.values())
        total_assets = self.balance + total_market_value

        return {
            "balance": round(self.balance, 2),
            "market_value": round(total_market_value, 2),
            "total_assets": round(total_assets, 2),
            "total_profit": round(total_profit, 2),
            "total_profit_rate": round((total_profit / (total_assets - total_profit)) * 100, 2) if total_assets > total_profit else 0,
            "position_count": len(self.positions),
            "trade_count": len(self.trade_history)
        }

    def get_trade_history(self, limit: int = 50) -> List[Dict]:
        """获取交易历史"""
        return self.trade_history[-limit:]

    def decide_and_trade(self, symbol: str, stock_data: Dict, indicators: Dict,
                         account_info: Dict = None) -> Dict:
        """
        AI 大脑决策并自动执行交易

        流程:
        1. 检查交易时间和风控
        2. 调用 ai_brain 做决策
        3. 根据决策结果自动下单
        4. 记录策略信号用于后续评估
        """
        from ai_brain import ai_brain
        from strategy_tracker import strategy_tracker

        # 1. 交易时间检查（复用 competition_manager）
        if not competition_manager.is_trading_hours():
            market = competition_manager.get_market_status()
            return {
                "success": False,
                "message": f"非交易时间 ({market['status_text']})，下次开盘: {market['next_open']}"
            }

        # 2. 获取账户信息
        if not account_info:
            account_info = {
                "balance": self.balance,
                "total_assets": self.balance + sum(p.market_value for p in self.positions.values()),
                "positions": {s: p.to_dict() for s, p in self.positions.items()}
            }

        # 3. AI 大脑决策
        if not account_info.get("balance"):
            account_info["balance"] = self.balance

        try:
            decision = ai_brain.decide(symbol, stock_data, indicators, account_info)
        except Exception as e:
            logger.error(f"AI决策失败: {e}")
            return {"success": False, "message": f"AI决策失败: {e}"}

        # 4. 记录策略信号（用于策略追踪闭环）
        price = stock_data.get("price", 0)
        for strat_name in decision.strategies_used:
            strategy_tracker.record_signal(
                strat_name, symbol, decision.signal,
                0, decision.confidence, price
            )

        # 5. 执行交易
        result = {"success": False, "decision": decision.to_dict(), "trade": None}

        if decision.action == "hold":
            result["success"] = True
            result["message"] = "AI建议持有，不执行交易"
            return result

        if decision.action == "buy":
            quantity = decision.position_size
            if quantity <= 0:
                # 根据风控计算仓位
                pos_info = self.check_risk(symbol, "buy", price, 100)
                quantity = self.calculate_position_size(price, decision.confidence)

            if quantity > 0:
                order = self.create_order(symbol, "buy", quantity, price)
                trade_result = self.execute_order(order, stock_data)
                result["trade"] = trade_result
                result["success"] = trade_result["success"]
                result["message"] = trade_result["message"]

        elif decision.action == "sell":
            if symbol in self.positions:
                quantity = self.positions[symbol].amount
                order = self.create_order(symbol, "sell", quantity, price)
                trade_result = self.execute_order(order, stock_data)
                result["trade"] = trade_result
                result["success"] = trade_result["success"]
                result["message"] = trade_result["message"]
            else:
                result["message"] = f"未持有 {symbol}，无法卖出"

        # 活动日志: AI自动交易
        if decision.action in ("buy", "sell") and result.get("success"):
            try:
                from activity_log import activity_log as _alog
                _alog.log(
                    action=f"auto_{decision.action}",
                    actor="ai",
                    symbol=symbol,
                    name=stock_data.get("name", symbol) if stock_data else symbol,
                    details={
                        "price": price,
                        "amount": decision.position_size,
                        "confidence": decision.confidence,
                        "strategies": decision.strategies_used,
                        "signal": decision.signal,
                        "reasons": decision.reasons[:3],
                    },
                )
            except Exception:
                pass

        return result

    def evaluate_signals(self, stock_prices: Dict[str, float]):
        """
        评估历史信号的准确性（策略追踪闭环）

        扫描所有未评估的信号，用当前价格计算盈亏
        """
        from strategy_tracker import strategy_tracker

        for record in strategy_tracker.records:
            if record.get("result"):
                continue  # 已评估，跳过

            symbol = record.get("symbol")
            signal = record.get("signal", "")
            price_at_signal = record.get("price_at_signal", 0)

            if symbol not in stock_prices or price_at_signal <= 0:
                continue

            current_price = stock_prices[symbol]
            profit_pct = (current_price - price_at_signal) / price_at_signal * 100

            # 买入信号：涨了算赢，跌了算输
            # 卖出信号：跌了算赢（正确避险），涨了算输
            if signal in ("BUY", "STRONG_BUY", "WEAK_BUY"):
                is_win = profit_pct > 0
            elif signal in ("SELL", "STRONG_SELL", "WEAK_SELL"):
                is_win = profit_pct < 0
            else:
                continue

            # 查找记录索引并更新
            idx = strategy_tracker.records.index(record)
            strategy_tracker.update_result(idx, {
                "price_after": current_price,
                "profit_pct": round(profit_pct, 2),
                "is_win": is_win,
                "evaluated_at": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
            })

        logger.info("策略信号评估完成")

    # ===== 止损止盈监控 =====

    def start_monitor(self, callback=None):
        """
        启动止损止盈监控（后台线程）

        Args:
            callback: 可选回调函数，当触发止损/止盈时调用 callback(signals)
        """
        if self._monitor_running:
            logger.warning("监控已在运行中")
            return

        if callback:
            self._monitor_callbacks.append(callback)

        self._monitor_running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info("止损止盈监控已启动")

    def stop_monitor(self):
        """停止监控"""
        self._monitor_running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
            self._monitor_thread = None
        logger.info("止损止盈监控已停止")

    def _monitor_loop(self):
        """监控主循环"""
        while self._monitor_running:
            try:
                if competition_manager.is_trading_hours():
                    # 每小时保存一次账户快照（用于收益曲线）
                    current_hour = datetime.now(ZoneInfo('Asia/Shanghai')).hour
                    if current_hour != self._last_snapshot_hour and self._last_snapshot_hour != -1:
                        self._save_hourly_snapshot()
                    self._last_snapshot_hour = current_hour

                    if self.positions:
                        prices = self._fetch_position_prices()
                        if prices:
                            # 更新移动止损
                            for symbol, price in prices.items():
                                self.update_trailing_stop(symbol, price)

                            # 检查止损止盈
                            signals = self.check_stop_loss_take_profit(prices)
                            if signals:
                                logger.info(f"触发 {len(signals)} 个止损/止盈信号")
                                for sig in signals:
                                    self._execute_stop_signal(sig)

                                # 触发回调
                                for cb in self._monitor_callbacks:
                                    try:
                                        cb(signals)
                                    except Exception as e:
                                        logger.error(f"监控回调执行失败: {e}")
            except Exception as e:
                logger.error(f"监控循环异常: {e}")

            interval = self.config.get("monitor_interval", 30)
            _time.sleep(interval)

    def _fetch_position_prices(self) -> Dict[str, float]:
        """获取所有持仓股票的当前价格（带缓存）"""
        prices = {}
        try:
            from data_cache import get_realtime_quotes_batch
            symbols = list(self.positions.keys())
            quotes = get_realtime_quotes_batch(symbols)
            for symbol, data in quotes.items():
                if data and "price" in data:
                    prices[symbol] = data["price"]
        except Exception as e:
            logger.error(f"获取持仓价格失败: {e}")
        return prices

    def _execute_stop_signal(self, signal: Dict):
        """执行止损/止盈信号"""
        symbol = signal["symbol"]
        action = signal["action"]
        amount = signal["amount"]
        price = signal["price"]
        reason = signal["reason"]

        logger.info(f"执行止损/止盈: {symbol} {action} x{amount} @ {price} - {reason}")

        # 创建并执行订单
        order = self.create_order(symbol, action, amount, price)
        stock_data = {"name": self.positions[symbol].name if symbol in self.positions else symbol}
        result = self.execute_order(order, stock_data)

        if result["success"]:
            # 记录到交易历史，标记为自动止损/止盈
            if self.trade_history:
                self.trade_history[-1]["notes"] = reason
                self.trade_history[-1]["auto_trade"] = True

            # 活动日志: 止损/止盈
            try:
                from activity_log import activity_log as _alog
                # 根据 reason 判断具体类型
                action_type = "stop_loss"
                if "止盈" in reason or "take_profit" in reason.lower():
                    action_type = "take_profit"
                elif "移动止损" in reason or "trailing" in reason.lower():
                    action_type = "trailing_stop"
                _alog.log(
                    action=action_type,
                    actor="auto_trader",
                    symbol=symbol,
                    name=self.positions[symbol].name if symbol in self.positions else symbol,
                    details={
                        "price": price,
                        "amount": amount,
                        "reason": reason,
                    },
                )
            except Exception:
                pass

            # 推送止损/止盈通知到前端
            try:
                from notification import notification_manager
                is_tp = "止盈" in reason or "take_profit" in reason.lower()
                notification_manager.add({
                    "type": "trade",
                    "level": "success",
                    "title": f"{'🎯 止盈' if is_tp else '🛡️ 止损'}自动执行",
                    "message": f"{symbol} {self.positions[symbol].name if symbol in self.positions else symbol} "
                               f"卖出{amount}股 @ ¥{price}\n{reason}",
                })
            except Exception:
                pass

            # OPT-7+8: 止损止盈后清除调度器冷却 + 通知调度器持仓已变
            try:
                from ai_scheduler import ai_scheduler
                ai_scheduler._trade_cooldown.pop(symbol, None)
            except Exception:
                pass

        return result

    def update_trailing_stop(self, symbol: str, current_price: float):
        """
        更新移动止损 — 价格创新高时止损位跟随上移

        规则：
        - 止损位只能上移，不能下移
        - 止损位 = 最高价 × (1 - trailing_stop_pct)
        """
        pos = self.positions.get(symbol)
        if not pos or pos.stop_loss <= 0:
            return

        trailing_pct = self.config.get("trailing_stop_pct", 0.05)

        # 更新最高价
        if current_price > pos.highest_price:
            pos.highest_price = current_price

            # 计算新的止损位
            new_stop = current_price * (1 - trailing_pct)
            if new_stop > pos.stop_loss:
                old_stop = pos.stop_loss
                pos.stop_loss = round(new_stop, 2)
                logger.info(f"{symbol} 移动止损更新: {old_stop:.2f} -> {pos.stop_loss:.2f} (最高价: {current_price:.2f})")

                # 持久化
                self._ds.update_position(symbol, {
                    "stop_loss": pos.stop_loss,
                    "take_profit": pos.take_profit,
                    "highest_price": pos.highest_price
                })

    def _save_hourly_snapshot(self):
        """每小时保存一次账户快照（用于绘制日内收益曲线）"""
        try:
            total_mv = sum(pos.market_value for pos in self.positions.values())
            total_assets = self.balance + total_mv
            initial = self._ds.account.get("initial_balance", 1000000)
            total_profit = total_assets - initial
            self._db_store().save_snapshot(
                balance=self.balance,
                market_value=total_mv,
                total_assets=total_assets,
                total_profit=total_profit,
                position_count=len(self.positions),
                account_id=self._ds.account_id
            )
        except Exception as e:
            logger.debug(f"小时快照保存失败: {e}")

    def _db_store(self):
        return self._ds._db

    def get_monitor_status(self) -> Dict:
        """获取监控状态"""
        return {
            "running": self._monitor_running,
            "positions": {
                symbol: {
                    "stop_loss": pos.stop_loss,
                    "take_profit": pos.take_profit,
                    "highest_price": pos.highest_price,
                    "current_price": pos.current_price,
                    "profit_rate": pos.profit_rate
                }
                for symbol, pos in self.positions.items()
            },
            "config": {
                "trailing_stop_pct": self.config.get("trailing_stop_pct", 0.05),
                "auto_stop_loss": self.config.get("auto_stop_loss", True),
                "auto_take_profit": self.config.get("auto_take_profit", True),
                "monitor_interval": self.config.get("monitor_interval", 30)
            }
        }

    def reset(self, initial_balance: float = 1000000):
        """重置账户"""
        self.balance = initial_balance
        self.positions.clear()
        self.orders.clear()
        self.trade_history.clear()
        # 重置持久化存储
        self._ds.reset(initial_balance)


# 全局自动交易实例（绑定默认账户）
auto_trader = AutoTrader()

# 多账户自动交易管理
class MultiAccountTrader:
    """多账户自动交易管理器"""

    def __init__(self):
        self.traders: Dict[int, AutoTrader] = {}

    def get_trader(self, account_id: int) -> AutoTrader:
        """获取指定账户的交易器"""
        if account_id not in self.traders:
            from account_manager import account_manager
            ds = account_manager.get(account_id)
            self.traders[account_id] = AutoTrader(ds=ds)
        return self.traders[account_id]

    def start_all(self):
        """启动所有账户的自动交易"""
        for acc_id, trader in self.traders.items():
            if trader.enabled:
                trader.start_monitor()
                logger.info(f"启动账户 {acc_id} 的自动交易监控")

    def stop_all(self):
        """停止所有"""
        for trader in self.traders.values():
            trader.stop_monitor()

multi_account_trader = MultiAccountTrader()
