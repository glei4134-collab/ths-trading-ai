"""
数据持久化模块（多账户版）
保存交易记录、账户数据，底层使用 SQLite
支持多账户隔离，每个账户独立的持仓和交易记录
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, List

logger = logging.getLogger(__name__)


class DataStore:
    """
    数据存储管理器（SQLite 后端，多账户）
    """

    def __init__(self, account_id: int = None):
        from db_store import db_store
        self._db = db_store
        # 未指定 account_id 时，加载活跃账户
        if account_id is None:
            self.account_id = self._db.get_active_account_id()
        else:
            self.account_id = account_id
        self._load()

    def _load(self):
        """加载当前账户数据"""
        self.account = self._db.get_account(self.account_id)
        if self.account is None:
            # 账户不存在，创建默认账户
            logger.warning(f"账户 {self.account_id} 不存在，创建默认账户")
            created = self._db.create_account("默认账户", balance=1000000)
            self.account_id = created["id"]
            self._db.set_active_account_id(self.account_id)
            self.account = self._db.get_account(self.account_id)
        positions_dict = self._db.get_positions(self.account_id)
        # 转换为旧格式: {symbol: pos_dict}
        self.positions = {}
        for key, pos in positions_dict.items():
            self.positions[pos["symbol"]] = pos
        self.trades = self._db.get_trades(limit=10000, account_id=self.account_id)

    def switch_account(self, account_id: int):
        """切换到另一个账户（同时持久化活跃状态）"""
        self.account_id = account_id
        self._db.set_active_account_id(account_id)
        self._load()
        logger.info(f"切换到账户 {account_id}: {self.account.get('name', '')}")

    def save_account(self):
        """保存账户数据"""
        self._db.update_balance(self.account.get("balance", 1000000), self.account_id)

    def save_trades(self):
        """保存交易记录（已实时写入 SQLite）"""
        pass

    def save_positions(self):
        """保存持仓数据"""
        self._db.save_positions(self.positions, self.account_id)

    def add_trade(self, trade: Dict):
        """添加交易记录"""
        trade["saved_at"] = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        self.trades.append(trade)
        self._db.add_trade(trade, self.account_id)

    def update_position(self, symbol: str, position: Dict):
        """更新持仓"""
        if position["amount"] <= 0:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = position
        self.save_positions()

    def update_balance(self, balance: float):
        """更新余额"""
        self.account["balance"] = balance
        self._db.update_balance(balance, self.account_id)

    def get_account_summary(self) -> Dict:
        """获取账户摘要"""
        return self._db.get_account_summary(self.account_id)

    def get_trades(self, limit: int = 50) -> List[Dict]:
        """获取交易记录"""
        return self._db.get_trades(limit=limit, account_id=self.account_id)

    def get_positions(self) -> List[Dict]:
        """获取持仓列表"""
        return list(self.positions.values())

    def export_data(self) -> Dict:
        """导出所有数据"""
        return self._db.export_all(self.account_id)

    def import_data(self, data: Dict):
        """导入数据"""
        if "account" in data:
            self.account = data["account"]
            self.save_account()
        if "trades" in data:
            self.trades = data["trades"]
            for t in data["trades"]:
                self._db.add_trade(t, self.account_id)
        if "positions" in data:
            self.positions = data["positions"]
            self.save_positions()

    def reset(self, initial_balance: float = 1000000):
        """重置账户"""
        self.account = {
            "balance": initial_balance,
            "initial_balance": initial_balance,
            "created_at": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        }
        self.trades = []
        self.positions = {}
        self._db.update_balance(initial_balance, self.account_id)
        self.save_positions()
        self._db.conn.execute("DELETE FROM trades WHERE account_id = ?", (self.account_id,))
        self._db.conn.commit()


# 默认全局实例（账户1）
data_store = DataStore()  # 自动加载活跃账户
