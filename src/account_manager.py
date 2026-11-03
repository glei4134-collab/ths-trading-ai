"""
多账户管理器
每个账户 = 一个同花顺比赛账号（Cookie绑定）
AI统一管理所有账户，账户间数据完全隔离
"""

import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple, Any

# ==================== 本地撤单防频发与幂等缓存 (T12) ====================
_cancelling_orders: Dict[str, Tuple[float, Dict]] = {}
_cancelling_lock = threading.Lock()
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

    def cancel_order(self, order_id: str, date: str = "") -> Dict:
        """撤销委托（复用长连接Session + 防死锁超时锁 + 5秒本地撤单幂等防频发）"""
        order_key = str(order_id).strip()
        now = time.time()
        with _cancelling_lock:
            # 清理超过 30 秒的过期记录
            expired = [k for k, (t, _) in _cancelling_orders.items() if now - t > 30.0]
            for k in expired:
                del _cancelling_orders[k]

            if order_key in _cancelling_orders:
                last_t, last_res = _cancelling_orders[order_key]
                if now - last_t < 5.0:
                    logger.info(f"⏳ [撤单防频发] 账户 #{self.account_id} order_id={order_key} 在 5 秒内已触发撤单 ({now - last_t:.1f}s前)，直接返回缓存结果")
                    cached = dict(last_res) if isinstance(last_res, dict) else {"success": True, "message": "撤单请求已在处理中(5秒防频发)"}
                    cached["dedup"] = True
                    return cached

            _cancelling_orders[order_key] = (now, {"success": True, "message": "撤单已受理中(防频发)", "order_id": order_key})

        if not self._order_lock.acquire(timeout=5.0):
            err_res = {"success": False, "message": "撤单获取锁超时"}
            with _cancelling_lock:
                _cancelling_orders[order_key] = (time.time(), err_res)
            return err_res
        try:
            if not self.cookie:
                self._reload_cookie_if_updated()
            if not self.cookie:
                err_res = {"success": False, "message": "未连接"}
                with _cancelling_lock:
                    _cancelling_orders[order_key] = (time.time(), err_res)
                return err_res
            self.connector.cookie = self.cookie
            res = self.connector.cancel_order(order_id, date)
            if not res.get("success") and res.get("error_code") == "COOKIE_EXPIRED":
                if self._reload_cookie_if_updated():
                    res = self.connector.cancel_order(order_id, date)

            with _cancelling_lock:
                _cancelling_orders[order_key] = (time.time(), res)

            if not res.get("success"):
                logger.warning(f"⚠️ 账户 #{self.account_id} 撤单失败: order_id={order_id} 原因={res.get('message')}")
            return res
        except Exception as e:
            err_res = {"success": False, "message": f"撤单异常: {e}"}
            with _cancelling_lock:
                _cancelling_orders[order_key] = (time.time(), err_res)
            logger.error(f"❌ 账户 #{self.account_id} 撤单抛出异常: order_id={order_id}, 错误={e}")
            return err_res
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

    def get_orders(self) -> Dict:
        """查询当前账户委托订单（复用长连接Session + 防死锁超时锁 + 透明自愈）"""
        if not self._order_lock.acquire(timeout=5.0):
            return {"success": False, "message": "查询委托获取锁超时"}
        try:
            if not self.cookie:
                self._reload_cookie_if_updated()
            if not self.cookie:
                return {"success": False, "message": "未连接"}
            self.connector.cookie = self.cookie
            res = self.connector.get_orders()
            if not res.get("success") and res.get("error_code") == "COOKIE_EXPIRED":
                if self._reload_cookie_if_updated():
                    res = self.connector.get_orders()
            return res
        finally:
            self._order_lock.release()

    def sync_from_ths(self) -> Dict:
        """从同花顺同步账户数据，返回同步结果"""
        try:
            fund_result = self.get_fund()
            pos_result = self.get_positions()

            if not fund_result["success"]:
                msg = f"获取资金失败: {fund_result['message']}"
                logger.warning(f"⚠️ 账户 #{self.account_id} 从同花顺同步资金失败: {msg}")
                return {"success": False, "message": msg}

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
                        except Exception as e:
                            logger.debug(f"解析快照时间戳失败: {e}")
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
                logger.warning(f"⚠️ 账户 #{self.account_id} 补充资产快照异常: {snap_err}")

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
        self._traders_lock = threading.Lock()  # P5: traders 字典并发读写互斥锁

    def _load_traders(self):
        """加载已有账户的trader"""
        from db_store import db_store
        accounts = db_store.list_accounts()
        with self._traders_lock:
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
                except Exception as e:
                    logger.debug(f"补齐 ths_gdzh 失败: {e}")

            db_store.set_active_account_id(acc_id)
            from ths_connector import web_connector
            web_connector.cookie = cookie
            web_connector.connected = True
            web_connector.gdzh = gdzh
            trader = THSAccountTrader(acc_id)
            trader.cookie = cookie
            trader.connected = True
            with self._traders_lock:
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
        with self._traders_lock:
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
        if ok:
            with self._traders_lock:
                if account_id in self.traders:
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
        """获取指定账户的交易器（自动加载未缓存的账户，确保100%穿透对齐）"""
        with self._traders_lock:
            if account_id in self.traders:
                return self.traders[account_id]
        # 自动加载: 新创建或未缓存的账户，只要数据库存在即创建专属 Trader
        try:
            from db_store import db_store
            account = db_store.get_account(account_id)
            if account:
                cookie = db_store.get_account_cookie(account_id) or ""
                trader = THSAccountTrader(account_id)
                trader.cookie = cookie
                trader.connected = bool(cookie)
                with self._traders_lock:
                    self.traders[account_id] = trader
                logger.info(f"自动加载账户 {account_id} 的 trader (connected={trader.connected})")
                return trader
        except Exception as e:
            logger.error(f"自动加载 trader 失败: {e}")
        return None

    def get_orders(self, account_id: Optional[int] = None) -> Dict:
        """查询指定账户委托（未指定时取当前活跃主账户，100%隔离防串号）"""
        target_id = account_id
        if target_id is None:
            from db_store import db_store
            target_id = db_store.get_active_account_id() or 1
        trader = self.get_trader(target_id)
        if not trader:
            return {"success": False, "message": f"未找到账户 #{target_id}", "data": []}
        res = trader.get_orders()
        if isinstance(res, dict):
            res["account_id"] = target_id
        return res

    def find_account_by_order_id(self, order_id: str) -> Optional[int]:
        """根据委托合同编号精确定位所属账户（防跨账户串号撤单）"""
        if not order_id:
            return None
        # T3/T7: 优先用 SQL 精确查询 trades.order_id，不再遍历所有账户
        try:
            from db_store import db_store
            with db_store._get_conn() as conn:
                row = conn.execute(
                    "SELECT account_id FROM trades WHERE order_id = ? ORDER BY id DESC LIMIT 1",
                    (str(order_id),)
                ).fetchone()
                if row and row["account_id"]:
                    return int(row["account_id"])
        except Exception as e:
            logger.warning(f"⚠️ 通过数据库查找 order_id 所属账户失败: {e}")
        return None

    def cancel_order(self, account_id: Optional[int], order_id: str, date: str = "") -> Dict:
        """专属通道撤单（自动寻址账户通道，严防跨账户串号）"""
        target_id = account_id
        if target_id is None:
            target_id = self.find_account_by_order_id(order_id)
        if target_id is None:
            from db_store import db_store
            target_id = db_store.get_active_account_id() or 1

        trader = self.get_trader(target_id)
        if not trader:
            return {"success": False, "message": f"未找到对应交易账户 #{target_id}", "account_id": target_id}

        res = trader.cancel_order(order_id, date)
        if isinstance(res, dict):
            res["account_id"] = target_id
            if not res.get("success"):
                logger.warning(f"⚠️ [专属通道撤单失败] 账户#{target_id} order_id={order_id} 错误: {res.get('message')}")
        return res

    def cancel_all_orders(self, account_id: Optional[int] = None, all_accounts: bool = False) -> Dict:
        """撤销未成交委托（单账户专属通道或全账户并发撤单）"""
        if all_accounts:
            results = {}
            with self._traders_lock:
                connected_pairs = [(acc_id, trader) for acc_id, trader in self.traders.items() if trader and trader.connected]
            with ThreadPoolExecutor(max_workers=min(len(connected_pairs) or 1, 8), thread_name_prefix="cancel-all") as executor:
                future_map = {
                    executor.submit(trader.cancel_all_orders): acc_id
                    for acc_id, trader in connected_pairs
                }
                for future in as_completed(future_map):
                    acc_id = future_map[future]
                    try:
                        res = future.result()
                        results[acc_id] = dict(res) if isinstance(res, dict) else {"success": True, "message": str(res)}
                    except Exception as e:
                        results[acc_id] = {"success": False, "message": str(e)}
            return {"success": True, "results": results, "message": f"并发撤销 {len(results)} 个账户的未成交委托"}

        target_id = account_id
        if target_id is None:
            from db_store import db_store
            target_id = db_store.get_active_account_id() or 1
        trader = self.get_trader(target_id)
        if not trader:
            return {"success": False, "message": f"未找到对应交易账户 #{target_id}", "account_id": target_id}

        res = trader.cancel_all_orders()
        if isinstance(res, dict):
            res["account_id"] = target_id
        return res

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
        with self._traders_lock:
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
        with self._traders_lock:
            acc_ids = list(self.traders.keys())
        for acc_id in acc_ids:
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

    def place_order(self, account_id: int, symbol: str, action: str,
                    price: float, amount: int, name: str = "",
                    dry_run: bool = False, persist: bool = True,
                    skip_optimizer: bool = False, notes: str = "", reason: str = "", **kwargs) -> Dict:
        """
        账户下单唯一入口：先过 order_guard，成功后才写 trades。
        失败委托绝不落成交账。
        """
        from order_guard import fetch_quote_silent, validate_pre_trade
        from db_store import db_store

        trader = self.get_trader(account_id)
        if not trader and not dry_run:
            return {"success": False, "message": "账户未连接", "recorded": False}

        acc = db_store.get_account(account_id) or {}
        pos_dict = db_store.get_positions(account_id) or {}
        tot_assets = float(acc.get("total_assets") or acc.get("balance") or 0)
        cash = float(acc.get("ky_fund") or acc.get("balance") or 0)
        pos = pos_dict.get(symbol) or {}
        existing_shares = int(pos.get("amount") or 0)
        existing_val = existing_shares * float(price or 0)
        available_shares = None
        if action == "sell" and trader:
            try:
                pos_res = trader.get_positions()
                if pos_res.get("success"):
                    for p in pos_res.get("data", {}).get("positions", []):
                        if p.get("symbol") == symbol:
                            available_shares = int(float(p.get("available") or 0))
                            existing_shares = int(float(p.get("amount") or existing_shares))
                            break
            except Exception as e:
                logger.warning(f"⚠️ 账户 #{account_id} 获取持仓可卖数量异常: {e}")

        quote = fetch_quote_silent(symbol)
        guard = validate_pre_trade(
            symbol, action, price, amount,
            total_assets=tot_assets,
            available_cash=cash,
            existing_position_value=existing_val,
            existing_shares=existing_shares,
            available_shares=available_shares,
            quote=quote,
            skip_session_check=dry_run,
        )
        if not guard.allowed:
            logger.info(f"下单闸门拒绝 账户#{account_id} {action} {symbol}: {guard.message}")
            return {"success": False, "message": guard.message, "code": guard.code, "recorded": False, "amount": 0}

        amount = guard.amount

        # ===== T2: 总仓位统一前置风控门禁（仅对买入生效）=====
        if action == "buy" and tot_assets > 0:
            cur_market_value = float(acc.get("market_value") or 0.0)
            buy_amount = float(price) * amount
            # 总仓位上限：买入后总仓位市值 / 总资产 > 80% 则拒绝
            if (cur_market_value + buy_amount) / tot_assets > 0.80:
                msg = (f"总仓位超过 80% 限制 (当前市值 {cur_market_value:.2f} + 本次买入 "
                       f"{buy_amount:.2f}) / 总资产 {tot_assets:.2f} > 80%)")
                logger.info(f"下单闸门拒绝 账户#{account_id} 买入 {symbol}: {msg}")
                return {"success": False, "message": msg, "code": "TOTAL_POSITION_CAP", "recorded": False, "amount": 0}
            # 单票上限：买入后该票市值 / 总资产 > 25%（低吸极值）则拒绝（35% 硬顶由 order_guard 保证）
            if (existing_val + buy_amount) / tot_assets > 0.25:
                msg = (f"单票仓位超过 25% 低吸极值 (当前市值 {existing_val:.2f} + 本次买入 "
                       f"{buy_amount:.2f}) / 总资产 {tot_assets:.2f} > 25%)")
                logger.info(f"下单闸门拒绝 账户#{account_id} 买入 {symbol}: {msg}")
                return {"success": False, "message": msg, "code": "SINGLE_POSITION_CAP", "recorded": False, "amount": 0}

        # ===== T4: 盘中 place_order 应用滑点（与回测口径一致）=====
        try:
            from auto_trader import auto_trader
            slippage_rate = float(getattr(auto_trader, "slippage_rate", None) or
                                  (auto_trader.config.get("slippage_rate", 0.001) if hasattr(auto_trader, "config") else 0.001))
        except Exception:
            slippage_rate = 0.001
        original_price = price
        if action == "buy":
            price = round(price * (1 + slippage_rate), 2)
        elif action == "sell":
            price = round(price * (1 - slippage_rate), 2)
        if price != original_price:
            logger.debug(f"[SLIPPAGE] 账户#{account_id} {action} {symbol}: 原价 {original_price:.2f} -> 滑点后 {price:.2f} (rate={slippage_rate})")

        if dry_run:
            audit_hash = ""
            try:
                from telemetry_audit import global_audit_chain
                audit_entry = global_audit_chain.append_event(
                    event_type="ORDER_DRY_RUN",
                    symbol=symbol,
                    action=action,
                    details={
                        "account_id": account_id,
                        "price": price,
                        "amount": amount,
                    },
                )
                audit_hash = audit_entry.get("entry_hash", "")
            except Exception:
                pass
            return {
                "success": True, "message": "演练通过闸门，未向券商发单",
                "amount": amount, "recorded": False, "dry_run": True,
                "audit_hash": audit_hash,
            }

        if skip_optimizer:
            if action == "buy":
                result = trader.buy(symbol, price, amount)
            elif action == "sell":
                result = trader.sell(symbol, price, amount)
            else:
                result = {"success": False, "message": f"不支持的动作 {action}"}
        else:
            result = self.execute_for_account(account_id, symbol, action, price, amount)

        result = dict(result or {})
        result["amount"] = amount

        # ===== T3/T7: 从同花顺返回结果提取委托号 order_id，供撤单路由精确定位 =====
        order_id = ""
        if result.get("success"):
            order_id = self._extract_order_id(result.get("data"))
            if order_id:
                result["order_id"] = order_id

        if result.get("success") and persist:
            now_s = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
            trade_payload = {
                "time": now_s,
                "action": action,
                "symbol": symbol,
                "name": name or symbol,
                "amount": amount,
                "price": price,
                "total": round(float(price) * amount, 2),
                "balance": round(cash - (price * amount if action == "buy" else -price * amount), 2),
                "mode": "real" if trader and trader.connected else "simulated",
                "order_id": order_id,
            }
            try:
                from telemetry_audit import global_audit_chain
                audit_entry = global_audit_chain.append_event(
                    event_type="ORDER_EXECUTED",
                    symbol=symbol,
                    action=action,
                    details={
                        "account_id": account_id,
                        **trade_payload,
                    },
                )
                result["audit_hash"] = audit_entry.get("entry_hash", "")
            except Exception as _aud_err:
                logger.debug(f"写入防篡改哈希审计链忽略: {_aud_err}")
            db_store.add_trade(trade_payload, account_id=account_id)
            result["recorded"] = True
            try:
                trader.sync_from_ths()
            except Exception as e:
                logger.warning(f"⚠️ 账户 #{account_id} 下单后同步同花顺账户失败: {e}")
        else:
            result["recorded"] = False
            if not dry_run and not result.get("success"):
                logger.error(f"❌ 账户 #{account_id} 下单被拒绝/失败: {action} {symbol} 价格={price} 数量={amount} 错误={result.get('message')}")
        return result

    @staticmethod
    def _extract_order_id(data) -> str:
        """从同花顺 buy/sell 返回的 data 中稳健提取委托合同编号 (order_id/entrust_no)"""
        if not data:
            return ""
        # data 可能是 dict、str、list 或其他结构
        if isinstance(data, dict):
            for key in ("order_id", "entrust_no", "htbh", "d_2135", "wtbh", "contract_no"):
                val = data.get(key)
                if val:
                    return str(val).strip()
            # 嵌套一层 result/data
            for nested_key in ("result", "data"):
                nested = data.get(nested_key)
                if isinstance(nested, dict):
                    for key in ("order_id", "entrust_no", "htbh", "d_2135", "wtbh", "contract_no"):
                        val = nested.get(key)
                        if val:
                            return str(val).strip()
        elif isinstance(data, str):
            return data.strip()
        elif isinstance(data, (list, tuple)) and data:
            first = data[0]
            if isinstance(first, dict):
                for key in ("order_id", "entrust_no", "htbh", "d_2135", "wtbh", "contract_no"):
                    val = first.get(key)
                    if val:
                        return str(val).strip()
        return ""

    def execute_for_account(self, account_id: int, symbol: str, action: str,
                            price: float, amount: int) -> Dict:
        """为指定账户执行交易（经过单账户细粒度锁保护 + OBI/涨停跨档抢筹优化 + TWAP微批次）"""
        from order_guard import fetch_quote_silent, validate_pre_trade
        from db_store import db_store

        trader = self.get_trader(account_id)
        if not trader:
            return {"success": False, "message": "账户未连接"}

        acc = db_store.get_account(account_id) or {}
        pos_dict = db_store.get_positions(account_id) or {}
        tot_assets = float(acc.get("total_assets") or acc.get("balance") or 0)
        cash = float(acc.get("ky_fund") or acc.get("balance") or 0)
        pos = pos_dict.get(symbol) or {}
        existing_shares = int(pos.get("amount") or 0)
        q_now = fetch_quote_silent(symbol)
        sector_name = str(pos.get("industry") or pos.get("sector") or (q_now or {}).get("industry") or "")
        guard = validate_pre_trade(
            symbol, action, price, amount,
            total_assets=tot_assets,
            available_cash=cash,
            existing_position_value=existing_shares * float(price or 0),
            existing_shares=existing_shares,
            quote=q_now,
            sector=sector_name,
            available_shares=int(pos.get("available") or existing_shares),
            positions=pos_dict,
        )
        if not guard.allowed:
            return {"success": False, "message": guard.message, "code": guard.code}
        amount = guard.amount

        if action == "buy":
            # 盘口深度与 OBI / 涨停抢筹评估
            try:
                from execution_optimizer import fetch_orderbook_depth, evaluate_aggressive_pegging, plan_twap_micro_batches
                depth = fetch_orderbook_depth(symbol)
                opt_price, is_pegged, peg_notes = evaluate_aggressive_pegging(symbol, action, price, depth)
                if is_pegged:
                    price = opt_price
                    logger.info(f"账户 #{account_id} 买入 {symbol}: {peg_notes}")
                
                # 检查单账户 TWAP 拆单 (当单笔委托量 > 1.2 * ask1_vol 且 ask1_vol > 0)
                is_twap, batches, twap_notes = plan_twap_micro_batches({account_id: amount}, depth.ask1_vol, batch_interval=1.5)
                if is_twap and len(batches) > 1:
                    logger.info(f"账户 #{account_id} {twap_notes}")
                    batch_res_list = []
                    for idx, b in enumerate(batches):
                        sub_amt = b.get(account_id, 0)
                        if sub_amt > 0:
                            sub_res = trader.buy(symbol, price, sub_amt)
                            batch_res_list.append(sub_res)
                            if idx < len(batches) - 1:
                                time.sleep(1.5)
                    all_success = any(r.get("success") for r in batch_res_list)
                    return {
                        "success": all_success,
                        "message": f"TWAP微批次完成 ({len(batch_res_list)}批次)",
                        "batches": batch_res_list,
                        "pegged": is_pegged,
                        "notes": peg_notes
                    }
            except Exception as opt_err:
                logger.warning(f"账户 #{account_id} 执行优化器调用忽略: {opt_err}")

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

            # 结合 Fractional Half-Kelly + ATR 波动率头寸上限保护
            try:
                from order_guard import calculate_dynamic_position_size, fetch_quote_silent
                q_vol = fetch_quote_silent(symbol) or {}
                hi_p = float(q_vol.get("high") or price * 1.02)
                lo_p = float(q_vol.get("low") or price * 0.98)
                est_atr = max(price * 0.015, hi_p - lo_p)
                kelly_atr_shares = calculate_dynamic_position_size(
                    total_assets=ky / max(effective_ratio, 0.20),
                    available_cash=ky,
                    price=price,
                    atr=est_atr,
                    win_rate=0.55,
                    payoff_ratio=1.5,
                    max_single_pct=max(effective_ratio, 0.30),
                )
                if kelly_atr_shares >= 100:
                    shares = min(shares, kelly_atr_shares)
            except Exception:
                pass

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
        并发为所有账户下发委托订单（毫秒级多路并发派发 + 单账户RLock独立隔离 + 动态头寸计算 + OBI涨停抢筹 + TWAP微批次拆单）
        """
        start_t = time.perf_counter()
        results = {}
        with self._traders_lock:
            acc_ids = list(self.traders.keys())
        if not acc_ids:
            return results

        depth = None
        effective_price = price
        is_pegged = False
        peg_notes = ""

        # 1. 深度盘口分析与涨停抢筹判定
        if action == "buy":
            try:
                from execution_optimizer import fetch_orderbook_depth, evaluate_aggressive_pegging
                depth = fetch_orderbook_depth(symbol)
                effective_price, is_pegged, peg_notes = evaluate_aggressive_pegging(symbol, action, price, depth)
                if is_pegged:
                    logger.info(f"多账户统一买入 {symbol}: {peg_notes}")
            except Exception as e:
                logger.warning(f"多账户盘口解析忽略: {e}")

        # 2. 为每个账户计算合规委托股数
        account_amounts = {}
        for acc_id in acc_ids:
            trader = self.get_trader(acc_id)
            if not trader or not trader.connected:
                results[acc_id] = {"success": False, "message": "账户未连接或已离线", "shares": 0}
                continue
            amt = self.calculate_dynamic_amount(
                account_id=acc_id,
                symbol=symbol,
                action=action,
                price=effective_price,
                position_ratio=position_ratio,
                amount_per_account=amount_per_account
            )
            if amt <= 0:
                if action == "buy":
                    results[acc_id] = {"success": False, "message": f"账户 #{acc_id} 可用资金不足一手，跳过买入", "shares": 0}
                else:
                    results[acc_id] = {"success": False, "message": f"账户 #{acc_id} 无 {symbol} 可卖持仓，跳过卖出", "shares": 0}
            else:
                account_amounts[acc_id] = amt

        if not account_amounts:
            total_cost_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.info(f"多账户 {action} {symbol} 无有效报单生成，耗时: {total_cost_ms}ms")
            return results

        # 3. TWAP 微批次拆单判定
        batches = [account_amounts]
        is_twap = False
        twap_notes = ""
        if action == "buy" and depth:
            try:
                from execution_optimizer import plan_twap_micro_batches
                is_twap, batches, twap_notes = plan_twap_micro_batches(account_amounts, depth.ask1_vol, batch_interval=1.5)
            except Exception as twap_err:
                logger.warning(f"TWAP拆单规划异常: {twap_err}")

        # 4. 执行调度 (单批直接并发，多批按 1.5s 步进派发)
        max_workers = min(len(account_amounts), 10)

        for b_idx, batch_dict in enumerate(batches):
            if b_idx > 0:
                logger.info(f"⏳ [TWAP-INTERVAL] 微批次 #{b_idx+1}/{len(batches)} 间隔等待 1.5s...")
                time.sleep(1.5)

            def _exec_single(acc_id: int, sub_amt: int):
                trader = self.get_trader(acc_id)
                if not trader or not trader.connected:
                    return {"success": False, "message": "账户已离线", "shares": 0}
                if action in ("buy", "sell"):
                    res = self.place_order(
                        acc_id, symbol, action, effective_price, sub_amt,
                        skip_optimizer=True, persist=True,
                    )
                else:
                    res = trader.cancel_all_orders()
                res["shares"] = res.get("amount", sub_amt)
                if is_pegged:
                    res["pegged"] = True
                    res["peg_notes"] = peg_notes
                return res

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(_exec_single, acc_id, qty): acc_id
                    for acc_id, qty in batch_dict.items() if qty > 0
                }
                for future in as_completed(future_map):
                    acc_id = future_map[future]
                    try:
                        res = future.result()
                        if acc_id not in results:
                            results[acc_id] = res
                            if is_twap:
                                results[acc_id]["batches"] = [res]
                        else:
                            # 累加多批次结果
                            prev = results[acc_id]
                            prev["shares"] = prev.get("shares", 0) + res.get("shares", 0)
                            if "batches" not in prev:
                                prev["batches"] = []
                            prev["batches"].append(res)
                            prev["success"] = prev.get("success", False) or res.get("success", False)
                    except Exception as e:
                        results[acc_id] = {"success": False, "message": f"并发下单异常: {e}", "shares": 0}

        total_cost_ms = round((time.perf_counter() - start_t) * 1000, 2)
        logger.info(f"🚀 [CONCURRENT-ORDER] {action} {symbol} 完成: {len(results)}个账户, 批次数: {len(batches)}, 总耗时: {total_cost_ms}ms")
        return results


# 全局实例
account_manager = AccountManager()
account_manager._load_traders()
