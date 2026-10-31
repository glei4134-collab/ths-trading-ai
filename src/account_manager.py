"""
多账户管理器
每个账户 = 一个同花顺比赛账号（Cookie绑定）
AI统一管理所有账户，账户间数据完全隔离
"""

import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

def extract_account_identity(cookie: str, gdzh: str = "") -> dict:
    ident = {
        "gdzh": (gdzh or "").strip(),
        "u_name": "",
        "user": "",
        "userid": ""
    }
    for part in (cookie or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k in ("u_name", "user", "userid"):
                ident[k] = v
    return ident


class THSAccountTrader:
    """单个同花顺账户的交易管理"""

    def __init__(self, account_id: int):
        self.account_id = account_id
        self.cookie = ""
        self.connected = False
        self._positions = {}  # 从THS同步的持仓
        self._fund_data = {}  # 资金数据
        self._order_lock = threading.RLock()  # 单账户细粒度互斥锁：防止并发报单产生超买或限频
        from ths_connector import THSWebConnector
        self.connector = THSWebConnector(account_id=account_id)

    def connect(self, cookie: str) -> Dict:
        """用Cookie连接同花顺并验证"""
        self.cookie = cookie
        self.connector.cookie = cookie
        connector = self.connector

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
            "gdzh": connector.gdzh or account_info.get("gdzh", ""),
            "account_info": account_info
        }

    def _reload_cookie_if_updated(self) -> bool:
        """检查并无感热重载后端自愈续期的最新 Cookie"""
        try:
            from db_store import db_store
            db_cookie = db_store.get_account_cookie(self.account_id)
            if db_cookie and db_cookie != self.cookie and not db_cookie.startswith("sandbox_"):
                self.cookie = db_cookie
                self.connector.cookie = db_cookie
                self.connected = True
                logger.info(f"🔄 [SELF-HEALING] 账户 #{self.account_id} 检测到后端自愈新凭据，透明热重载生效")
                return True
        except Exception as e:
            logger.debug(f"检查自愈凭据忽略: {e}")
        return False

    def get_fund(self) -> Dict:
        """获取资金信息（复用长连接Session + 自愈热重载）"""
        if not self.cookie:
            self._reload_cookie_if_updated()
        if not self.cookie:
            return {"success": False, "message": "未连接"}
        self.connector.cookie = self.cookie
        res = self.connector.get_fund()
        if not res.get("success") and res.get("error_code") == "COOKIE_EXPIRED":
            if self._reload_cookie_if_updated():
                res = self.connector.get_fund()
        return res

    def get_positions(self) -> Dict:
        """获取THS持仓（复用长连接Session + 自愈热重载）"""
        if not self.cookie:
            self._reload_cookie_if_updated()
        if not self.cookie:
            return {"success": False, "message": "未连接"}
        self.connector.cookie = self.cookie
        res = self.connector.get_positions()
        if not res.get("success") and res.get("error_code") == "COOKIE_EXPIRED":
            if self._reload_cookie_if_updated():
                res = self.connector.get_positions()
        return res

    def buy(self, symbol: str, price: float, amount: int) -> Dict:
        """买入（复用长连接Session + 防死锁超时锁 + 零中断自愈透明重试）"""
        if not self._order_lock.acquire(timeout=6.0):
            logger.error(f"🚨 [DEADLOCK-PROTECTION] 账户 #{self.account_id} 报单获取锁超时(>6s)，触发防死锁熔断")
            return {"success": False, "message": "交易网关锁竞争超时，系统已自动保护阻断以避免死锁"}
        try:
            if not self.cookie:
                self._reload_cookie_if_updated()
            if not self.cookie:
                return {"success": False, "message": "未连接"}
            self.connector.cookie = self.cookie
            res = self.connector.buy(symbol, price, amount)
            # 若遇 Cookie 失效，立即尝试自愈凭据热重载并透明重试一次
            if not res.get("success") and res.get("error_code") == "COOKIE_EXPIRED":
                logger.warning(f"⚠️ 账户 #{self.account_id} 报单遭遇凭据失效，协同自愈尝试透明重试...")
                if self._reload_cookie_if_updated():
                    res = self.connector.buy(symbol, price, amount)
                    if res.get("success"):
                        logger.info(f"✅ 账户 #{self.account_id} 自愈协同重载后重试买入成功！")
            return res
        finally:
            self._order_lock.release()

    def sell(self, symbol: str, price: float, amount: int) -> Dict:
        """卖出（复用长连接Session + 防死锁超时锁 + 零中断自愈透明重试）"""
        if not self._order_lock.acquire(timeout=6.0):
            logger.error(f"🚨 [DEADLOCK-PROTECTION] 账户 #{self.account_id} 卖出获取锁超时(>6s)，触发防死锁熔断")
            return {"success": False, "message": "交易网关锁竞争超时，系统已自动保护阻断以避免死锁"}
        try:
            if not self.cookie:
                self._reload_cookie_if_updated()
            if not self.cookie:
                return {"success": False, "message": "未连接"}
            self.connector.cookie = self.cookie
            pos_result = self.connector.get_positions()
            mkcode = ""
            gdzh = ""
            if pos_result.get("success"):
                for p in pos_result["data"].get("positions", []):
                    if p["symbol"] == symbol:
                        mkcode = p.get("mkcode", "")
                        gdzh = p.get("gdzh", "")
                        break
            res = self.connector.sell(symbol, price, amount, mkcode=mkcode, gdzh=gdzh)
            # 若遇 Cookie 失效，透明自愈重试
            if not res.get("success") and res.get("error_code") == "COOKIE_EXPIRED":
                logger.warning(f"⚠️ 账户 #{self.account_id} 卖出遭遇凭据失效，协同自愈尝试透明重试...")
                if self._reload_cookie_if_updated():
                    res = self.connector.sell(symbol, price, amount, mkcode=mkcode, gdzh=gdzh)
                    if res.get("success"):
                        logger.info(f"✅ 账户 #{self.account_id} 自愈协同重载后重试卖出成功！")
            return res
        finally:
            self._order_lock.release()

    def cancel_order(self, order_id: str, date: str) -> Dict:
        """撤销委托（复用长连接Session + 防死锁超时锁）"""
        if not self._order_lock.acquire(timeout=5.0):
            return {"success": False, "message": "撤单获取锁超时"}
        try:
            if not self.cookie:
                self._reload_cookie_if_updated()
            if not self.cookie:
                return {"success": False, "message": "未连接"}
            self.connector.cookie = self.cookie
            res = self.connector.cancel_order(order_id, date)
            if not res.get("success") and res.get("error_code") == "COOKIE_EXPIRED":
                if self._reload_cookie_if_updated():
                    res = self.connector.cancel_order(order_id, date)
            return res
        finally:
            self._order_lock.release()

    def cancel_all_orders(self) -> Dict:
        """撤销所有未成交委托（复用长连接Session + 防死锁超时锁）"""
        if not self._order_lock.acquire(timeout=5.0):
            return {"success": False, "message": "撤单获取锁超时"}
        try:
            if not self.cookie:
                self._reload_cookie_if_updated()
            if not self.cookie:
                return {"success": False, "message": "未连接"}
            self.connector.cookie = self.cookie
            res = self.connector.cancel_all_orders()
            if not res.get("success") and res.get("error_code") == "COOKIE_EXPIRED":
                if self._reload_cookie_if_updated():
                    res = self.connector.cancel_all_orders()
            return res
        finally:
            self._order_lock.release()

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

            # 同步持仓到本地（账户隔离，兼容 avg_price / cost_price 与真实 current_price）
            from db_store import db_store as dbs
            pos_to_save = {}
            for p in positions:
                sym = p.get("symbol", "")
                if not sym:
                    continue
                amt = int(p.get("amount") or 0)
                avg_p = float(p.get("avg_price") or p.get("cost_price") or 0)
                cur_p = float(p.get("current_price") or avg_p or 0)
                pos_to_save[sym] = {
                    "symbol": sym,
                    "name": p.get("name", sym),
                    "amount": amt,
                    "avg_price": avg_p,
                    "current_price": cur_p,
                    "total_cost": round(avg_p * amt, 2),
                }
            dbs.save_positions(pos_to_save, self.account_id)

            # 为当前同步账户补充资产快照（距上次快照超过30分钟或当日尚无快照时写入，确保所有账户具备独立曲线与昨收基线）
            try:
                if total_assets > 0:
                    acc_row = dbs.get_account(self.account_id) or {}
                    init_b = float(acc_row.get("initial_balance") or 200000.0)
                    if init_b < 150000:
                        init_b = 200000.0
                    c = dbs.conn.cursor()
                    c.execute(
                        "SELECT timestamp FROM account_snapshots WHERE account_id = ? ORDER BY timestamp DESC LIMIT 1",
                        (self.account_id,)
                    )
                    last_row = c.fetchone()
                    should_snap = True
                    if last_row and last_row["timestamp"]:
                        try:
                            last_dt = datetime.strptime(last_row["timestamp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                            now_dt = datetime.now(ZoneInfo("Asia/Shanghai"))
                            if (now_dt - last_dt).total_seconds() < 1800 and last_dt.date() == now_dt.date():
                                should_snap = False
                        except Exception:
                            pass
                    if should_snap:
                        dbs.save_snapshot(
                            balance=ky_fund,
                            market_value=market_value,
                            total_assets=total_assets,
                            total_profit=round(total_assets - init_b, 2),
                            position_count=len(pos_to_save),
                            account_id=self.account_id
                        )
            except Exception as snap_err:
                logger.debug(f"账户 {self.account_id} 快照写入忽略: {snap_err}")

            self._positions = {p["symbol"]: p for p in positions}
            logger.info(f"账户 {self.account_id} 同步完成: 余额={ky_fund}, 市值={market_value}, 总资产={total_assets}, 持仓={len(positions)}只")

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
        添加或续签账户
        流程: 验证Cookie -> 提取权威身份(gdzh / userid / u_name / user) -> 全局查重唯一性匹配 -> 原地续签 或 新增
        保证对同一个同花顺账号确实唯一 ID，绝不创建多余的重复交易账号。
        """
        from db_store import db_store

        # 1. 验证Cookie
        if "userid=ths_gu_" in cookie:
            return {
                "success": False,
                "message": "检测到当前同花顺处于未登录访客状态 (ths_gu_)！请先在同花顺网页登录您的正式账号，并点击【同花顺交易区】进入交易台后再提取。"
            }

        test_trader = THSAccountTrader(0)
        conn_result = test_trader.connect(cookie)
        if not conn_result["success"] and ("资金账号不存在" in str(conn_result.get("message")) or "未登录" in str(conn_result.get("message"))):
            from ths_connector import activate_mncg_trade_session
            act_res = activate_mncg_trade_session(cookie, session=test_trader.connector.session)
            if act_res.get("success"):
                cookie = act_res["cookie"]
                conn_result = test_trader.connect(cookie)
            elif "访客" in act_res.get("message", "") or "未登录" in act_res.get("message", ""):
                return {"success": False, "message": act_res["message"]}

        if not conn_result["success"]:
            return {"success": False, "message": conn_result["message"]}

        gdzh = (conn_result.get("gdzh") or test_trader.connector.gdzh or "").strip()
        ky_fund = conn_result.get("ky_fund", 0)
        new_ident = extract_account_identity(cookie, gdzh)

        # 2. 严谨全局查重：对同一个账号确定唯一ID，不创建重复交易账号
        existing = db_store.list_accounts()
        matched_acc = None

        for acc in existing:
            old_cookie = db_store.get_account_cookie(acc["id"]) or ""
            acc_gdzh = (acc.get("ths_gdzh") or "").strip()
            old_ident = extract_account_identity(old_cookie, acc_gdzh)

            # 匹配规则（任一命中均视为同一同花顺账号）:
            # 1. 资金账号/股东号 gdzh 一致
            if gdzh and acc_gdzh and gdzh == acc_gdzh:
                matched_acc = acc
                break
            # 2. 同花顺用户唯一标识 userid (纯数字且非访客) 一致
            if new_ident["userid"] and old_ident["userid"] and not new_ident["userid"].startswith("ths_gu_") and new_ident["userid"] == old_ident["userid"]:
                matched_acc = acc
                break
            # 3. 同花顺加密通行证签名 user 字段完全一致
            if new_ident["user"] and old_ident["user"] and new_ident["user"] == old_ident["user"]:
                matched_acc = acc
                break
            # 4. 同花顺用户昵称 u_name 一致
            if new_ident["u_name"] and old_ident["u_name"] and new_ident["u_name"] == old_ident["u_name"]:
                matched_acc = acc
                break
            # 5. Cookie 字符串相同
            if old_cookie and old_cookie == cookie:
                matched_acc = acc
                break

        if matched_acc:
            acc_id = matched_acc["id"]
            acc_name = matched_acc.get("name", str(acc_id))
            db_store.update_account_cookie(acc_id, cookie)
            # 若数据库之前缺少 ths_gdzh，本次自动补齐更新
            if gdzh and not matched_acc.get("ths_gdzh"):
                try:
                    with db_store._get_conn() as conn:
                        conn.execute("UPDATE accounts SET ths_gdzh = ? WHERE id = ?", (gdzh, acc_id))
                        conn.commit()
                except Exception:
                    pass

            db_store.set_active_account_id(acc_id)
            from ths_connector import web_connector
            web_connector.cookie = cookie
            web_connector.connected = True
            web_connector.gdzh = gdzh
            trader = THSAccountTrader(acc_id)
            trader.cookie = cookie
            trader.connected = True
            self.traders[acc_id] = trader
            sync_result = trader.sync_from_ths()
            logger.info(f"安全识别并原地更新已绑定账号 Cookie: {acc_name} (ID={acc_id}, gdzh={gdzh}, 资金={ky_fund})")
            return {
                "success": True,
                "renewed": True,
                "data": db_store.get_account(acc_id),
                "sync": sync_result,
                "message": f"账号 '{acc_name}' 识别成功，已完成安全续签与数据同步（未创建重复账号）",
            }

        # 3. 只有全新的账号，才按存入顺序自动命名为 1, 2, 3...
        seq_name = str(len(existing) + 1)
        acc = db_store.create_account(
            name=seq_name, balance=ky_fund, cookie=cookie,
            ths_gdzh=gdzh, ths_competition_id="", initial_balance=200000.0
        )
        name = acc.get("name", seq_name)
        acc_id = acc["id"]
        db_store.set_active_account_id(acc_id)
        trader = THSAccountTrader(acc_id)
        trader.cookie = cookie
        trader.connected = True
        self.traders[acc_id] = trader
        sync_result = trader.sync_from_ths()
        try:
            if sync_result.get("success"):
                mv = float(sync_result.get("market_value", 0) or 0)
                tot_assets = float(sync_result.get("total_assets", 0) or 0) or (ky_fund + mv)
                db_store.save_snapshot(
                    balance=ky_fund, market_value=mv,
                    total_assets=tot_assets,
                    total_profit=round(tot_assets - acc.get("initial_balance", 200000.0), 2),
                    position_count=int(sync_result.get("position_count", 0) or 0),
                    account_id=acc["id"]
                )
        except Exception as e:
            logger.warning(f"新账户基线快照写入失败: {e}")

        logger.info(f"新增独立同花顺账户: {name} (ID={acc_id}, gdzh={gdzh}, 资金={ky_fund})")
        return {
            "success": True,
            "renewed": False,
            "data": acc,
            "sync": sync_result,
            "message": f"成功绑定新账号 '{name}' (初始本金锁定 ¥200,000.00)",
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
        """并发同步所有账户（非阻塞线程池）"""
        start_t = time.perf_counter()
        results = {}
        from db_store import db_store
        accounts = db_store.list_accounts()
        connected_tasks = []
        for acc in accounts:
            acc_id = acc["id"]
            trader = self.get_trader(acc_id)
            if trader and trader.connected:
                connected_tasks.append((acc_id, trader))

        if not connected_tasks:
            return results

        max_workers = min(len(connected_tasks), 10)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_acc = {
                executor.submit(trader.sync_from_ths): acc_id
                for acc_id, trader in connected_tasks
            }
            for future in as_completed(future_to_acc):
                acc_id = future_to_acc[future]
                try:
                    results[acc_id] = future.result()
                except Exception as e:
                    results[acc_id] = {"success": False, "message": f"并发同步异常: {e}"}

        total_cost_ms = round((time.perf_counter() - start_t) * 1000, 2)
        logger.info(f"🔄 [CONCURRENT-SYNC] 并发完成 {len(results)} 个账户数据同步，耗时: {total_cost_ms}ms")
        return results

    def sync_one(self, account_id: int) -> Dict:
        """同步单个账户"""
        trader = self.get_trader(account_id)
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

    def get_account_lock(self, account_id: int):
        """获取指定账户的交易互斥锁"""
        trader = self.get_trader(account_id)
        return trader._order_lock if trader else None

    def execute_for_account(self, account_id: int, symbol: str, action: str,
                            price: float, amount: int) -> Dict:
        """为指定账户执行交易（经过单账户细粒度锁保护）"""
        trader = self.get_trader(account_id)
        if not trader:
            return {"success": False, "message": "账户未连接"}
        if action == "buy":
            return trader.buy(symbol, price, amount)
        elif action == "sell":
            return trader.sell(symbol, price, amount)
        elif action in ("cancel", "cancel_all"):
            return trader.cancel_all_orders()
        else:
            return {"success": False, "message": f"不支持的交易指令: {action}"}

    def calculate_dynamic_amount(self, account_id: int, symbol: str, action: str,
                                 price: float, position_ratio: float = 0.20,
                                 amount_per_account: Optional[int] = None) -> int:
        """
        动态头寸计算器：
        根据指定账户当前可用资金（ky_fund）、总资产及持仓限制，动态计算合规的委托股数（100股整数倍）
        """
        trader = self.get_trader(account_id)
        if not trader or price <= 0:
            return 0

        if action == "buy":
            # 1. 获取最新资金
            fund_res = trader.get_fund()
            ky = float(fund_res.get("data", trader.ky_fund or 0.0)) if fund_res.get("success") else float(trader._fund_data.get("ky_fund", 0.0) or trader.connector.ky_fund or 0.0)
            if ky < price * 100:
                logger.warning(f"账户 #{account_id} 可用资金 ({ky:.2f}元) 不足以买入 100 股 {symbol} (单价 {price}元)")
                return 0

            # 2. 如果调用方显式指定了自定义非默认固定手数且未启用动态比例，校验资金充裕度后使用
            if amount_per_account and amount_per_account > 0 and amount_per_account != 100 and position_ratio <= 0.0:
                max_affordable = int((ky * 0.98) / price / 100) * 100
                return max(0, min(amount_per_account, max_affordable))

            # 3. 动态头寸计算：默认按可用资金的 position_ratio（默认 20%）建仓
            effective_ratio = position_ratio if position_ratio > 0.0 else 0.20
            target_cash = ky * effective_ratio
            shares = int(target_cash / price / 100) * 100

            # 至少买一手 100 股
            if shares < 100 and ky >= price * 100 * 1.002:
                shares = 100

            # 上限限制：不能超过可用资金的 95%（保留必要手续费缓冲）
            max_shares = int((ky * 0.95) / price / 100) * 100
            shares = min(shares, max_shares)

            return max(0, shares)

        elif action == "sell":
            # 卖出：按该账户持仓实际可卖股数计算
            pos_res = trader.get_positions()
            available_qty = 0
            if pos_res.get("success"):
                for p in pos_res["data"].get("positions", []):
                    if p.get("symbol") == symbol:
                        available_qty = int(float(p.get("available", 0)))
                        break

            if amount_per_account and amount_per_account > 0 and amount_per_account != 100:
                sell_shares = min(available_qty, amount_per_account)
            else:
                sell_shares = available_qty

            return int(sell_shares / 100) * 100

        return 0

    def execute_for_all(self, symbol: str, action: str, price: float,
                        amount_per_account: Optional[int] = None,
                        position_ratio: float = 0.20) -> Dict:
        """
        并发为所有账户下发委托订单（毫秒级多路并发派发 + 单账户RLock独立隔离 + 动态头寸计算）
        """
        start_t = time.perf_counter()
        results = {}
        acc_ids = list(self.traders.keys())
        if not acc_ids:
            return results

        def _calc_and_exec(acc_id: int):
            trader = self.get_trader(acc_id)
            if not trader or not trader.connected:
                return {"success": False, "message": "账户未连接或已离线", "shares": 0}

            order_amount = self.calculate_dynamic_amount(
                account_id=acc_id,
                symbol=symbol,
                action=action,
                price=price,
                position_ratio=position_ratio,
                amount_per_account=amount_per_account
            )
            if order_amount <= 0:
                if action == "buy":
                    return {"success": False, "message": f"账户 #{acc_id} 可用资金不足一手，跳过买入", "shares": 0}
                else:
                    return {"success": False, "message": f"账户 #{acc_id} 无 {symbol} 可卖持仓，跳过卖出", "shares": 0}

            res = self.execute_for_account(acc_id, symbol, action, price, order_amount)
            res["shares"] = order_amount
            return res

        max_workers = min(len(acc_ids), 10)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_acc = {
                executor.submit(_calc_and_exec, acc_id): acc_id
                for acc_id in acc_ids
            }
            for future in as_completed(future_to_acc):
                acc_id = future_to_acc[future]
                try:
                    results[acc_id] = future.result()
                except Exception as e:
                    results[acc_id] = {"success": False, "message": f"并发下单异常: {e}", "shares": 0}

        total_cost_ms = round((time.perf_counter() - start_t) * 1000, 2)
        logger.info(f"🚀 [CONCURRENT-ORDER] {action} {symbol} 并发多账户下单完成: {len(results)}个账户, 总耗时: {total_cost_ms}ms")
        return results


# 全局实例
account_manager = AccountManager()
account_manager._load_traders()
