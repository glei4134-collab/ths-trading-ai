"""
同花顺一键扫码/自动登录与会话保活服务 (Auto Login & Session Keep-Alive)
- 自动适配 Chrome / Edge 浏览器，弹出窗口扫码。
- 内置 Win32 强力前台置顶，解决全屏软件遮挡问题。
- 扫码后全自动侦测、提取 Cookie、验证资金并自动入库。
- 内置 10 分钟心跳保活守护，保持 30 天 Cookie 长久有效。
"""

import time
import json
import logging
import threading
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Optional

logger = logging.getLogger("auto_login")

LOGIN_URL = "https://mncg.10jqka.com.cn/cgiwt/index/index"


class THSAutoLoginService:
    def __init__(self):
        self._lock = threading.Lock()
        self._login_thread = None
        self._keep_alive_thread = None
        self._running_keep_alive = False
        self.state = {
            "status": "idle",       # idle | launching | waiting_login | success | failed | timeout | canceled
            "message": "待机中",
            "balance": None,
            "updated_at": None,
        }

    def get_status(self) -> Dict:
        """获取当前自动登录进度状态"""
        with self._lock:
            return dict(self.state)

    def cancel_login(self):
        """取消登录"""
        with self._lock:
            if self.state["status"] in ("launching", "waiting_login"):
                self.state["status"] = "canceled"
                self.state["message"] = "用户已取消自动登录"

    def start_auto_login(self, account_id: Optional[int] = None, timeout_sec: int = 180) -> Dict:
        """启动一键扫码登录工作流（可视化窗口）"""
        with self._lock:
            if (self.state["status"] in ("launching", "waiting_login")
                    and self._login_thread and self._login_thread.is_alive()):
                return {"success": False, "message": "已有登录窗口在运行中，请在弹出的浏览器中扫码", "status": self.state["status"]}

            self.state = {
                "status": "launching",
                "message": "正在唤起浏览器登录页面...",
                "balance": None,
                "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S"),
            }

        self._login_thread = threading.Thread(
            target=self._run_browser_login,
            args=(account_id, timeout_sec),
            daemon=True,
            name="ths-auto-login"
        )
        self._login_thread.start()
        return {"success": True, "message": "登录服务已启动，请在弹出的浏览器中扫码登录"}

    def _force_window_to_front(self):
        """将同花顺登录窗口强力置顶到最前台，防止被其他全屏软件遮挡"""
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

            def enum_callback(hwnd, extra):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        title = buff.value
                        if "同花顺" in title or "10jqka" in title or "模拟交易" in title:
                            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                            user32.SetForegroundWindow(hwnd)
                return True

            user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
        except Exception as e:
            logger.debug(f"置顶窗口异常: {e}")

    def _save_cookie_success(self, verified_cookie: str, verified_balance: float, account_id: Optional[int] = None):
        """统一持久化 Cookie 并同步本地数据"""
        from db_store import db_store
        from ths_connector import web_connector, encrypt_cookie, COOKIE_FILE

        target_id = account_id or db_store.get_active_account_id()
        accs = db_store.list_accounts()
        if not accs:
            new_acc = db_store.create_account(name="模拟比赛账户", balance=verified_balance, cookie=verified_cookie)
            target_id = new_acc["id"]
        else:
            if not target_id:
                target_id = accs[0]["id"]
            db_store.update_account_cookie(target_id, verified_cookie)

        db_store.set_active_account_id(target_id)
        db_store.update_balance(verified_balance, target_id)

        # 更新全局 web_connector
        web_connector.cookie = verified_cookie
        web_connector.connected = True

        # 同步 account_manager
        try:
            from account_manager import account_manager
            trader = account_manager.get_trader(target_id)
            if trader:
                trader.cookie = verified_cookie
                trader.connected = True
                trader.sync_from_ths()
        except Exception as e:
            logger.warning(f"同步 account_manager 异常: {e}")

        # 加密持久化到文件
        try:
            COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "cookie": encrypt_cookie(verified_cookie),
                    "save_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }, f, indent=2)
        except Exception as e:
            logger.warning(f"写入 ths_cookie.json 异常: {e}")

        # 推送系统通知
        try:
            from notification import notification_manager
            notification_manager.add({
                "type": "connection",
                "level": "success",
                "title": "🎉 同花顺登录成功",
                "message": f"Cookie已自动截获并更新，可用资金: ¥{verified_balance:,.2f}",
            })
        except Exception:
            pass

    def _extract_ths_cookie(self, cookies) -> str:
        """从 Playwright cookies 列表提取同花顺完整规范 Cookie 字符串"""
        ths_parts = []
        for c in cookies:
            domain = c.get("domain", "")
            if "10jqka" in domain or "ths" in domain or c["name"] in ("v_c", "user", "escapename", "ticket", "PHPSESSID", "u_name"):
                ths_parts.append(f"{c['name']}={c['value']}")
        return "; ".join(ths_parts)

    def _run_browser_login(self, account_id: Optional[int], timeout_sec: int):
        """前台扫码登录流程（自动适配已安装的 Chrome 或 Edge）"""
        playwright_instance = None
        browser = None
        context = None
        try:
            from playwright.sync_api import sync_playwright
            playwright_instance = sync_playwright().start()

            launch_args = [
                "--window-size=1024,768",
                "--window-position=120,80",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check"
            ]

            # 优先使用用户的 Chrome 浏览器，若失败则使用 Edge
            try:
                browser = playwright_instance.chromium.launch(
                    channel="chrome",
                    headless=False,
                    args=launch_args
                )
                logger.info("已成功通过 Chrome 启动登录窗口")
            except Exception as e1:
                logger.warning(f"Chrome 启动失败 ({e1})，尝试使用 Edge 启动...")
                try:
                    browser = playwright_instance.chromium.launch(
                        channel="msedge",
                        headless=False,
                        args=launch_args
                    )
                    logger.info("已成功通过 Edge 启动登录窗口")
                except Exception as e2:
                    browser = playwright_instance.chromium.launch(
                        headless=False,
                        args=launch_args
                    )

            context = browser.new_context(
                viewport={"width": 1000, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            with self._lock:
                self.state["status"] = "waiting_login"
                self.state["message"] = "同花顺登录窗口已弹出，请使用手机App扫码或账号登录"
                self.state["updated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S")

            logger.info("🚀 正在打开同花顺模拟比赛交易主页...")
            page.goto(LOGIN_URL, timeout=45000)

            # 尝试把窗口激活到前台
            try:
                page.bring_to_front()
            except Exception:
                pass
            self._force_window_to_front()

            start_t = time.time()
            login_success = False
            verified_cookie = None
            verified_balance = 0.0

            while time.time() - start_t < timeout_sec:
                with self._lock:
                    if self.state["status"] == "canceled":
                        break

                cookies = context.cookies()
                cookie_map = {c["name"]: c["value"] for c in cookies}

                # 判定登录成功标识
                has_vc = bool(cookie_map.get("v_c"))
                has_user = bool(cookie_map.get("user") or cookie_map.get("escapename") or cookie_map.get("u_name"))

                if has_vc or has_user:
                    candidate_cookie = self._extract_ths_cookie(cookies)
                    if len(candidate_cookie) > 30:
                        from ths_connector import web_connector
                        old_cookie = web_connector.cookie
                        web_connector.cookie = candidate_cookie
                        fund_res = web_connector.get_fund()
                        if fund_res.get("success"):
                            login_success = True
                            verified_cookie = candidate_cookie
                            verified_balance = float(fund_res.get("data", 0))
                            logger.info(f"🎉 验证同花顺 Cookie 成功！可用资金: ¥{verified_balance}")
                            break
                        else:
                            web_connector.cookie = old_cookie

                time.sleep(1.2)

            if login_success and verified_cookie:
                self._save_cookie_success(verified_cookie, verified_balance, account_id)
                with self._lock:
                    self.state["status"] = "success"
                    self.state["message"] = f"登录成功！可用资金: ¥{verified_balance:,.2f}，Cookie已自动同步！"
                    self.state["balance"] = verified_balance
                    self.state["updated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S")

            elif self.state["status"] != "canceled":
                with self._lock:
                    self.state["status"] = "timeout"
                    self.state["message"] = f"登录超时（{timeout_sec}秒未完成登录），请重试"
                    self.state["updated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S")

        except Exception as e:
            logger.error(f"自动化登录过程异常: {e}", exc_info=True)
            with self._lock:
                self.state["status"] = "failed"
                self.state["message"] = f"启动或登录异常: {str(e)}"
                self.state["updated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S")

        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
            if playwright_instance:
                try:
                    playwright_instance.stop()
                except Exception:
                    pass

    # ==================== 会话心跳保活守护 (Keep-Alive Daemon) ====================

    def silent_refresh(self, account_id: Optional[int] = None) -> Dict:
        """会话心跳刷新：直接调用资金与持仓 API 续期 Session（支持单账户或全矩阵账户）"""
        try:
            from db_store import db_store
            from account_manager import account_manager
            from ths_connector import web_connector

            accounts = db_store.list_accounts()
            if not accounts:
                if web_connector.cookie and not web_connector.cookie.startswith("sandbox_"):
                    res = web_connector.get_fund()
                    if res.get("success"):
                        bal = float(res.get("data", 0))
                        return {"success": True, "balance": bal, "message": "Cookie心跳续签成功"}
                return {"success": False, "message": "当前未配置任何有效账户"}

            target_accounts = [a for a in accounts if a["id"] == account_id] if account_id else accounts
            if not target_accounts:
                return {"success": False, "message": f"未找到账户 ID: {account_id}"}

            results = {}
            success_count = 0
            for acc in target_accounts:
                acc_id = acc["id"]
                acc_name = acc.get("name", f"账户{acc_id}")
                cookie = db_store.get_account_cookie(acc_id)
                if not cookie or cookie.startswith("sandbox_"):
                    results[acc_id] = {"success": False, "name": acc_name, "message": "未绑定真实Cookie或为沙盒模式"}
                    continue

                trader = account_manager.get_trader(acc_id)
                if trader:
                    sync_res = trader.sync_from_ths()
                    if sync_res.get("success"):
                        success_count += 1
                        results[acc_id] = {
                            "success": True,
                            "name": acc_name,
                            "ky_fund": sync_res.get("ky_fund", 0),
                            "total_assets": sync_res.get("total_assets", 0),
                            "message": "续签同步成功"
                        }
                    else:
                        results[acc_id] = {
                            "success": False,
                            "name": acc_name,
                            "message": sync_res.get("message", "接口请求失败")
                        }

            active_id = db_store.get_active_account_id()
            active_cookie = db_store.get_account_cookie(active_id)
            if active_cookie and not active_cookie.startswith("sandbox_"):
                web_connector.cookie = active_cookie
                web_connector.connected = True

            return {
                "success": success_count > 0,
                "refreshed_count": success_count,
                "total_count": len(target_accounts),
                "details": results,
                "message": f"多账户矩阵续签完成: {success_count}/{len(target_accounts)} 个账户活跃有效"
            }
        except Exception as e:
            logger.error(f"静默续签异常: {e}", exc_info=True)
            return {"success": False, "message": str(e)}

    def start_keep_alive(self, interval_sec: int = 600):
        """启动后台自动心跳保活线程，默认每10分钟唤醒保活一次所有已绑定同花顺账户"""
        if self._running_keep_alive:
            return

        self._running_keep_alive = True

        def _worker():
            logger.info(f"💓 同花顺多账户全自动矩阵保活守护线程已启动 (心跳周期: {interval_sec}秒)")
            while self._running_keep_alive:
                time.sleep(interval_sec)
                try:
                    from db_store import db_store
                    from account_manager import account_manager
                    from ths_connector import web_connector

                    accounts = db_store.list_accounts()
                    if not accounts:
                        if web_connector.cookie and not web_connector.cookie.startswith("sandbox_"):
                            res = web_connector.get_fund()
                            if res.get("success"):
                                logger.info(f"💓 [KeepAlive] 单账户心跳成功 | 资金: ¥{res.get('data', 0):,.2f}")
                        continue

                    active_id = db_store.get_active_account_id()
                    for acc in accounts:
                        acc_id = acc["id"]
                        acc_name = acc.get("name", f"账户{acc_id}")
                        cookie = db_store.get_account_cookie(acc_id)
                        if not cookie or cookie.startswith("sandbox_"):
                            continue

                        try:
                            trader = account_manager.get_trader(acc_id)
                            if trader:
                                sync_res = trader.sync_from_ths()
                                if sync_res.get("success"):
                                    logger.info(f"💓 [KeepAlive] 账户 [{acc_name}] (ID:{acc_id}) 心跳续签成功 | 资金: ¥{sync_res.get('ky_fund', 0):,.2f}")
                                    if acc_id == active_id:
                                        web_connector.cookie = cookie
                                        web_connector.connected = True
                                else:
                                    logger.warning(f"⚠️ [KeepAlive] 账户 [{acc_name}] (ID:{acc_id}) 心跳返回异常: {sync_res.get('message', '未知')}")
                        except Exception as acc_e:
                            logger.warning(f"⚠️ [KeepAlive] 账户 [{acc_name}] (ID:{acc_id}) 心跳检测出错: {acc_e}")

                except Exception as e:
                    logger.debug(f"[KeepAlive] 矩阵心跳守护异常: {e}")

        self._keep_alive_thread = threading.Thread(target=_worker, daemon=True, name="ths-keep-alive")
        self._keep_alive_thread.start()

    def stop_keep_alive(self):
        """停止心跳保活"""
        self._running_keep_alive = False


# 全局单例
auto_login_service = THSAutoLoginService()
