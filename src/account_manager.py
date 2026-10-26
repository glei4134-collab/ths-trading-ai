"""
多账户管理器
每个账户 = 一个同花顺比赛账号（Cookie绑定）
AI统一管理所有账户，账户间数据完全隔离
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class THSAccountTrader:
    """单个同花顺账户的交易管理"""

    def __init__(self, account_id: int):
        self.account_id = account_id
        self.cookie = ""
        self.connected = False
        self._positions = {}  # 从THS同步的持仓
        self._fund_data = {}  # 资金数据

    def connect(self, cookie: str) -> Dict:
        """用Cookie连接同花顺并验证"""
        self.cookie = cookie
        from ths_connector import THSWebConnector
        connector = THSWebConnector()
        connector.cookie = cookie

        # 验证Cookie
        fund_result = connector.get_fund()
        if not fund_result["success"]:
            return {"success": False, "message": f"Cookie验证失败: {fund_result['message']}"}

        # 获取完整账户信息
        summary = connector.get_account_summary()
        account_info = {}
        if summary["success"]:
            account_info = summary["data"]

        self.connected = True
        self._fund_data = {"ky_fund": fund_result.get("data", 0), **account_info}

        return {
            "success": True,
            "ky_fund": fund_result.get("data", 0),
            "gdzh": account_info.get("gdzh", ""),
            "account_info": account_info
        }

    def get_fund(self) -> Dict:
        """获取资金信息"""
        if not self.cookie:
            return {"success": False, "message": "未连接"}
        from ths_connector import THSWebConnector
        connector = THSWebConnector()
        connector.cookie = self.cookie
        return connector.get_fund()

    def get_positions(self) -> Dict:
        """获取THS持仓"""
        if not self.cookie:
            return {"success": False, "message": "未连接"}
        from ths_connector import THSWebConnector
        connector = THSWebConnector()
        connector.cookie = self.cookie
        return connector.get_positions()

    def buy(self, symbol: str, price: float, amount: int) -> Dict:
        """买入"""
        if not self.cookie:
            return {"success": False, "message": "未连接"}
        from ths_connector import THSWebConnector
        connector = THSWebConnector()
        connector.cookie = self.cookie
        return connector.buy(symbol, price, amount)

    def sell(self, symbol: str, price: float, amount: int) -> Dict:
        """卖出"""
        if not self.cookie:
            return {"success": False, "message": "未连接"}
        from ths_connector import THSWebConnector
        connector = THSWebConnector()
        connector.cookie = self.cookie
        # 获取mkcode和gdzh
        pos_result = connector.get_positions()
        mkcode = ""
        gdzh = ""
        if pos_result["success"]:
            for p in pos_result["data"].get("positions", []):
                if p["symbol"] == symbol:
                    mkcode = p.get("mkcode", "")
                    gdzh = p.get("gdzh", "")
                    break
        return connector.sell(symbol, price, amount, mkcode=mkcode, gdzh=gdzh)

    def sync_from_ths(self) -> Dict:
        """从同花顺同步账户数据，返回同步结果"""
        try:
            fund_result = self.get_fund()
            pos_result = self.get_positions()

            if not fund_result["success"]:
                return {"success": False, "message": f"获取资金失败: {fund_result['message']}"}

            ky_fund = fund_result.get("data", 0)
            positions = pos_result.get("data", {}).get("positions", []) if pos_result["success"] else []

            # 从API获取总资产（含冻结金额），不自己算
            fund_info = pos_result.get("data", {}).get("fund", {}) if pos_result["success"] else {}
            total_assets = fund_info.get("total_assets", 0)
            frozen = fund_info.get("frozen", 0)
            market_value = fund_info.get("market_value", 0)
            # 如果API没返回total_assets，用 fallback 计算
            if not total_assets:
                total_assets = ky_fund + market_value

            # 更新数据库
            from db_store import db_store
            db_store.update_account_sync(self.account_id, total_assets, ky_fund, market_value, total_assets)

            # 同步持仓到本地（账户隔离）
            from db_store import db_store as dbs
            dbs.save_positions({p["symbol"]: {
                "symbol": p["symbol"],
                "name": p.get("name", p["symbol"]),
                "amount": p.get("amount", 0),
                "avg_price": p.get("avg_price", 0),
                "current_price": p.get("current_price", 0),
                "total_cost": p.get("avg_price", 0) * p.get("amount", 0),
            } for p in positions}, self.account_id)

            self._positions = {p["symbol"]: p for p in positions}
            logger.info(f"账户 {self.account_id} 同步完成: 余额={ky_fund}, 持仓={len(positions)}只")

            return {
                "success": True,
                "ky_fund": ky_fund,
                "market_value": market_value,
                "total_assets": total_assets,
                "position_count": len(positions)
            }

        except Exception as e:
            logger.error(f"账户 {self.account_id} 同步失败: {e}")
            return {"success": False, "message": str(e)}


class AccountManager:
    """多账户管理器（同花顺Cookie绑定）"""

    def __init__(self):
        self.traders: Dict[int, THSAccountTrader] = {}  # account_id -> trader

    def _load_traders(self):
        """加载已有账户的trader"""
        from db_store import db_store
        accounts = db_store.list_accounts()
        for acc in accounts:
            cookie = db_store.get_account_cookie(acc["id"])
            if cookie:
                trader = THSAccountTrader(acc["id"])
                trader.cookie = cookie
                trader.connected = True
                self.traders[acc["id"]] = trader

    def add_account(self, name: str, cookie: str) -> Dict:
        """
        添加同花顺比赛账号
        流程: 验证Cookie → 获取账户信息 → 检测是否重复 → 创建
        """
        from db_store import db_store

        # 1. 验证Cookie
        test_trader = THSAccountTrader(0)
        conn_result = test_trader.connect(cookie)
        if not conn_result["success"]:
            return {"success": False, "message": conn_result["message"]}

        gdzh = conn_result.get("gdzh", "")
        ky_fund = conn_result.get("ky_fund", 0)

        # 2. 检测是否已绑定过同一账户（通过gdzh查重）
        existing = db_store.list_accounts()
        for acc in existing:
            if acc.get("ths_gdzh") and gdzh and acc["ths_gdzh"] == gdzh:
                return {
                    "success": False,
                    "message": f"该账户已绑定过（{acc['name']}，账号{gdzh}），请勿重复绑定"
                }
        # 也检查cookie是否相同
        for acc in existing:
            old_cookie = db_store.get_account_cookie(acc["id"])
            if old_cookie and old_cookie == cookie:
                return {
                    "success": False,
                    "message": f"该Cookie已绑定过（{acc['name']}），请勿重复绑定"
                }

        # 3. 创建账户
        acc = db_store.create_account(
            name=name, balance=ky_fund, cookie=cookie,
            ths_gdzh=gdzh, ths_competition_id=""
        )

        # 3.5 设为活跃账户
        db_store.set_active_account_id(acc["id"])

        # 4. 创建trader并同步
        trader = THSAccountTrader(acc["id"])
        trader.cookie = cookie
        trader.connected = True
        self.traders[acc["id"]] = trader

        # 5. 首次同步
        sync_result = trader.sync_from_ths()

        logger.info(f"绑定同花顺账号: {name} (ID={acc['id']}, 资金={ky_fund})")
        return {
            "success": True,
            "data": acc,
            "sync": sync_result,
            "message": f"账号 '{name}' 绑定成功，可用资金: {ky_fund}",
            "cloud_hint": "Cookie有效期30天，配置GitHub Actions云端续签可永不过期。详见 tools/setup_cookie_keeper.md"
        }

    def remove_account(self, account_id: int) -> bool:
        """删除账户"""
        from db_store import db_store
        ok = db_store.delete_account(account_id)
        if ok and account_id in self.traders:
            del self.traders[account_id]
        return ok

    def rename(self, account_id: int, name: str):
        """重命名账户"""
        from db_store import db_store
        db_store.rename_account(account_id, name)

    def list_all(self) -> List[Dict]:
        """列出所有账户摘要"""
        from db_store import db_store
        return db_store.get_all_accounts_summary()

    def get_global_summary(self) -> Dict:
        """获取全局汇总"""
        from db_store import db_store
        return db_store.get_global_summary()

    def sync_all(self) -> Dict:
        """同步所有账户"""
        results = {}
        for acc_id, trader in self.traders.items():
            results[acc_id] = trader.sync_from_ths()
        return results

    def sync_one(self, account_id: int) -> Dict:
        """同步单个账户"""
        trader = self.traders.get(account_id)
        if not trader:
            return {"success": False, "message": "账户未连接"}
        return trader.sync_from_ths()

    def get_trader(self, account_id: int) -> Optional[THSAccountTrader]:
        """获取指定账户的交易器（自动加载未缓存的账户）"""
        if account_id in self.traders:
            return self.traders[account_id]
        # 自动加载: 新创建的账户可能没在 _load_traders 中注册
        try:
            from db_store import db_store
            cookie = db_store.get_account_cookie(account_id)
            if cookie:
                trader = THSAccountTrader(account_id)
                trader.cookie = cookie
                trader.connected = True
                self.traders[account_id] = trader
                logger.info(f"自动加载账户 {account_id} 的 trader")
                return trader
        except Exception as e:
            logger.error(f"自动加载 trader 失败: {e}")
        return None

    def decide_for_account(self, account_id: int, symbol: str, stock_data: Dict,
                           indicators: Dict) -> Dict:
        """
        为指定账户做AI决策（隔离）
        每个账户独立计算仓位、止损止盈
        """
        from ai_brain import ai_brain
        from db_store import db_store

        # 获取该账户独立的资金和持仓信息
        summary = db_store.get_account_summary(account_id)
        trader = self.traders.get(account_id)
        if trader:
            summary["positions"] = {s: p for s, p in trader._positions.items()}
        else:
            summary["positions"] = {}

        # AI决策（传入该账户独立的account_info）
        decision = ai_brain.decide(symbol, stock_data, indicators, summary)
        return decision.to_dict()

    def decide_for_all(self, symbol: str, stock_data: Dict, indicators: Dict) -> Dict:
        """AI为所有账户做决策（每个账户独立决策）"""
        results = {}
        for acc_id in self.traders:
            try:
                results[acc_id] = self.decide_for_account(acc_id, symbol, stock_data, indicators)
            except Exception as e:
                logger.error(f"账户 {acc_id} 决策失败: {e}")
                results[acc_id] = {"error": str(e)}
        return results

    def execute_for_account(self, account_id: int, symbol: str, action: str,
                            price: float, amount: int) -> Dict:
        """为指定账户执行交易"""
        trader = self.traders.get(account_id)
        if not trader:
            return {"success": False, "message": "账户未连接"}
        if action == "buy":
            return trader.buy(symbol, price, amount)
        else:
            return trader.sell(symbol, price, amount)

    def execute_for_all(self, symbol: str, action: str, price: float,
                        amount_per_account: int = 100) -> Dict:
        """为所有账户执行交易"""
        results = {}
        for acc_id in self.traders:
            results[acc_id] = self.execute_for_account(acc_id, symbol, action, price, amount_per_account)
        return results


# 全局实例
account_manager = AccountManager()
account_manager._load_traders()
