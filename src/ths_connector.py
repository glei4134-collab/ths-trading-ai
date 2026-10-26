"""
同花顺连接模块
支持桌面客户端和网页Cookie两种连接方式
网页版对接同花顺模拟比赛真实API (mncg.10jqka.com.cn)
"""

import json
import os
import time
import logging
import hashlib
import base64
import socket
import requests
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


def _obfuscate_key() -> bytes:
    """基于机器标识生成混淆密钥（非强加密，防止明文泄露）"""
    seed = socket.gethostname() + "ths_trading_2026"
    return hashlib.sha256(seed.encode()).digest()[:16]


def encrypt_cookie(plain: str) -> str:
    """混淆加密 Cookie"""
    if not plain:
        return ""
    key = _obfuscate_key()
    raw = plain.encode("utf-8")
    # XOR + base64
    encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    return base64.b64encode(encrypted).decode("ascii")


def decrypt_cookie(cipher: str) -> str:
    """解混淆 Cookie"""
    if not cipher:
        return ""
    try:
        key = _obfuscate_key()
        raw = base64.b64decode(cipher)
        decrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
        return decrypted.decode("utf-8")
    except Exception:
        return cipher  # 兼容旧版明文

# Cookie存储路径
from paths import CONFIG_DIR as COOKIE_DIR
COOKIE_FILE = COOKIE_DIR / "ths_cookie.json"

# 同花顺模拟炒股 API 基础地址
THS_API_BASE = "https://mncg.10jqka.com.cn"


class THSDesktopConnector:
    """
    同花顺桌面客户端连接器
    使用easytrader操控同花顺客户端
    支持自动启动xiadan.exe并登录
    """

    _CONFIG_FILE = None  # 延迟初始化

    def __init__(self):
        self.user = None
        self.connected = False
        self.exe_path = None
        self._launched_process = None  # 自动启动的进程引用
        self._load_saved_config()

    def _get_config_file(self):
        if self._CONFIG_FILE is None:
            from paths import CONFIG_DIR
            self._CONFIG_FILE = CONFIG_DIR / "desktop_config.json"
        return self._CONFIG_FILE

    def _load_saved_config(self):
        """从配置文件加载已保存的路径和登录凭据"""
        try:
            cfg = self._get_config_file()
            if cfg.exists():
                with open(cfg, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.exe_path = data.get("exe_path", "")
                # 加载加密保存的登录凭据
                enc_user = data.get("ths_user", "")
                enc_pass = data.get("ths_password", "")
                self._saved_user = decrypt_cookie(enc_user) if enc_user else ""
                self._saved_password = decrypt_cookie(enc_pass) if enc_pass else ""
            else:
                self._saved_user = ""
                self._saved_password = ""
        except Exception:
            self._saved_user = ""
            self._saved_password = ""

    def _save_config(self):
        """保存路径和登录凭据到配置文件"""
        try:
            from paths import CONFIG_DIR
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            cfg = self._get_config_file()
            data = {"exe_path": self.exe_path or ""}
            # 加密保存登录凭据
            if getattr(self, '_saved_user', ''):
                data["ths_user"] = encrypt_cookie(self._saved_user)
            if getattr(self, '_saved_password', ''):
                data["ths_password"] = encrypt_cookie(self._saved_password)
            with open(cfg, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def save_credentials(self, user: str, password: str) -> Dict:
        """保存登录凭据（加密存储），供下次自动登录"""
        self._saved_user = user
        self._saved_password = password
        self._save_config()
        return {"success": True, "message": "登录凭据已保存"}

    def launch_and_connect(self, exe_path: str = None) -> Dict:
        """
        自动启动同花顺客户端并连接
        1. 检测xiadan.exe是否已运行
        2. 未运行则自动启动
        3. 等待窗口就绪
        4. 使用easytrader连接
        5. 如有保存的凭据则自动登录
        """
        import subprocess
        import os
        path = exe_path or self.exe_path

        if not path:
            # 搜索默认路径
            for p in _DEFAULT_THS_PATHS:
                if os.path.exists(p):
                    path = p
                    break

        if not path or not os.path.exists(path):
            return {"success": False, "message": "未找到同花顺客户端(xiadan.exe)\n请确认已安装同花顺"}

        self.exe_path = path

        # 检查是否已在运行
        already_running = self._is_process_running("xiadan.exe")

        if not already_running:
            # 自动启动xiadan.exe
            logger.info(f"🚀 自动启动同花顺客户端: {path}")
            try:
                self._launched_process = subprocess.Popen(
                    [path],
                    cwd=os.path.dirname(path),
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                )
                # 等待窗口就绪（最多30秒）
                logger.info("⏳ 等待同花顺客户端启动...")
                for i in range(30):
                    time.sleep(1)
                    if self._is_process_running("xiadan.exe"):
                        # 额外等待几秒让窗口完全加载
                        time.sleep(3)
                        break
                else:
                    return {"success": False, "message": "同花顺客户端启动超时，请手动打开"}
                logger.info("✅ 同花顺客户端已启动")
            except Exception as e:
                return {"success": False, "message": f"启动同花顺失败: {e}"}

        # 连接
        return self.connect(path)

    @staticmethod
    def _is_process_running(process_name: str) -> bool:
        """检查进程是否在运行（Windows）"""
        try:
            import subprocess
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {process_name}"],
                capture_output=True, text=True, timeout=5
            )
            return process_name.lower() in result.stdout.lower()
        except Exception:
            return False

    @staticmethod
    def _config_tesseract():
        """配置Tesseract OCR路径（用于验证码识别）"""
        try:
            import pytesseract
            tesseract_paths = [
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            ]
            for p in tesseract_paths:
                if os.path.exists(p):
                    pytesseract.pytesseract.tesseract_cmd = p
                    logger.info(f"Tesseract OCR: {p}")
                    return
            logger.warning("Tesseract OCR not found - captcha auto-recognition may fail")
        except ImportError:
            logger.warning("pytesseract not installed - captcha auto-recognition disabled")

    def connect(self, exe_path: str, user: str = None, password: str = None) -> Dict:
        try:
            import easytrader
        except ImportError:
            return {"success": False, "message": "未安装easytrader", "need_broker_login": False}

        # 配置Tesseract OCR（验证码自动识别）
        self._config_tesseract()

        try:
            self.exe_path = exe_path
            self.user = easytrader.use('ths')

            # 使用静默策略：完全不抢焦点、不弹验证码
            # UltraSilentCopy > SilentWMCopy > SilentCopy
            try:
                from silent_grid_strategy import UltraSilentCopy
                self.user.grid_strategy = UltraSilentCopy()
                logger.info("✅ 使用 UltraSilentCopy 策略 (LVM直接读取 + 静默验证码)")
            except Exception as e:
                logger.warning(f"UltraSilentCopy加载失败({e})，尝试SilentWMCopy")
                try:
                    from silent_grid_strategy import SilentWMCopy
                    self.user.grid_strategy = SilentWMCopy()
                    logger.info("✅ 使用 SilentWMCopy 策略")
                except Exception as e2:
                    from easytrader import grid_strategies
                    self.user.grid_strategy = grid_strategies.WMCopy
                    logger.warning(f"静默策略全部加载失败，使用WMCopy: {e2}")

            # 猴子补丁：替换 _switch_left_menus 为不抢焦点版本
            self._patch_switch_left_menus()

            self.user.connect(exe_path)

            # 连接成功后尝试读取数据，验证券商账户已登录
            try:
                _ = self.user.position
                _ = self.user.balance
                self.connected = True
                self._save_config()
                return {"success": True, "message": "连接成功", "exe_path": exe_path}
            except Exception:
                # 窗口连上了但数据读取失败
                # easytrader THS模式：用户直接在xiadan.exe中登录，无需prepare()
                self.connected = True
                self._save_config()
                logger.info("✅ 窗口已连接（登录态由客户端管理）")
                return {"success": True, "message": "连接成功（登录态由同花顺客户端管理）", "exe_path": exe_path}
        except Exception as e:
            return {"success": False, "message": f"连接失败: {str(e)}", "need_broker_login": False}

    def _patch_switch_left_menus(self):
        """
        猴子补丁：替换 easytrader 的 _switch_left_menus 方法
        原版用 type_keys('{F5}') 会抢焦点
        改为用 PostMessage 发送 F5，完全不改变窗口焦点
        """
        import types
        import ctypes as _ctypes

        WM_KEYDOWN = 0x0100
        WM_KEYUP = 0x0101
        VK_F5 = 0x74

        def _silent_switch_left_menus(self, path, sleep=0.2):
            """不抢焦点的菜单切换"""
            self.close_pop_dialog()
            self._get_left_menus_handle().get_item(path).select()
            # 用 PostMessage 发送 F5（不抢焦点）
            try:
                hwnd = self._app.top_window().handle
                _ctypes.windll.user32.PostMessageW(hwnd, WM_KEYDOWN, VK_F5, 0)
                _ctypes.windll.user32.PostMessageW(hwnd, WM_KEYUP, VK_F5, 0)
            except Exception:
                pass
            self.wait(sleep)

        if self.user:
            self.user._switch_left_menus = types.MethodType(
                _silent_switch_left_menus, self.user
            )
            logger.info("✅ 已补丁 _switch_left_menus → PostMessage F5 (不抢焦点)")

    def buy(self, symbol: str, price: float, amount: int) -> Dict:
        if not self.connected:
            return {"success": False, "message": "未连接"}
        try:
            result = self.user.buy(symbol, price=price, amount=amount)
            return {"success": True, "message": "买入委托成功", "data": result}
        except Exception as e:
            return {"success": False, "message": f"买入失败: {str(e)}"}

    def sell(self, symbol: str, price: float, amount: int) -> Dict:
        if not self.connected:
            return {"success": False, "message": "未连接"}
        try:
            result = self.user.sell(symbol, price=price, amount=amount)
            return {"success": True, "message": "卖出委托成功", "data": result}
        except Exception as e:
            return {"success": False, "message": f"卖出失败: {str(e)}"}

    def get_positions(self) -> Dict:
        if not self.connected:
            return {"success": False, "message": "未连接"}
        try:
            return {"success": True, "data": self.user.position}
        except Exception as e:
            return {"success": False, "message": f"获取持仓失败: {str(e)}"}

    def get_balance(self) -> Dict:
        if not self.connected:
            return {"success": False, "message": "未连接"}
        try:
            return {"success": True, "data": self.user.balance}
        except Exception as e:
            return {"success": False, "message": f"获取余额失败: {str(e)}"}


class THSWebConnector:
    """
    同花顺网页版连接器
    通过Cookie调用同花顺模拟比赛真实API
    API地址: https://mncg.10jqka.com.cn
    """

    def __init__(self):
        self.cookie = None
        self.connected = False
        self.gdzh = None  # 股东账户号
        self.ky_fund = 0  # 可用资金

    def save_cookie(self, cookie: str, user_info: Dict = None) -> Dict:
        """
        保存登录Cookie并验证

        Args:
            cookie: 浏览器中的Cookie字符串（从F12开发者工具复制）
            user_info: 用户信息（可选）

        Returns:
            保存结果
        """
        try:
            COOKIE_DIR.mkdir(parents=True, exist_ok=True)

            data = {
                "cookie": encrypt_cookie(cookie),
                "user_info": user_info or {},
                "save_time": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
            }

            with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            self.cookie = cookie

            # 验证Cookie是否有效
            result = self.get_fund()
            if result["success"]:
                self.connected = True
                return {
                    "success": True,
                    "message": f"Cookie验证成功，可用资金: {result['data']}"
                }
            else:
                return {"success": False, "message": f"Cookie保存但验证失败: {result['message']}"}

        except Exception as e:
            return {"success": False, "message": f"保存失败: {str(e)}"}

    def load_cookie(self) -> Dict:
        """加载已保存的Cookie"""
        try:
            if not COOKIE_FILE.exists():
                return {"success": False, "message": "未找到保存的Cookie"}

            with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.cookie = decrypt_cookie(data.get("cookie", ""))

            result = self.get_fund()
            if result["success"]:
                self.connected = True
                return {
                    "success": True,
                    "message": "Cookie加载成功",
                    "save_time": data.get("save_time")
                }
            else:
                return {"success": False, "message": f"Cookie已过期: {result['message']}"}

        except Exception as e:
            return {"success": False, "message": f"加载失败: {str(e)}"}

    def _get_headers(self) -> Dict:
        """获取请求头"""
        return {
            "Cookie": self.cookie,
            "Host": "mncg.10jqka.com.cn",
            "Origin": "https://mncg.10jqka.com.cn",
            "Referer": "https://mncg.10jqka.com.cn/cgiwt/index/index",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded"
        }

    def _api_post(self, url: str, data: Dict) -> Dict:
        """发送API请求"""
        if not self.cookie:
            return {"success": False, "message": "未登录，请先保存Cookie"}

        try:
            headers = self._get_headers()
            response = requests.post(url, headers=headers, data=data, timeout=10,
                                     proxies={"http": None, "https": None})  # 绕过系统代理

            if response.status_code == 200:
                result = response.json()
                errorcode = result.get("errorcode", "")
                if str(errorcode) == "0":
                    return {"success": True, "data": result}
                else:
                    errormsg = result.get("errormsg", "未知错误")
                    return {"success": False, "message": f"API错误: {errormsg}"}
            else:
                return {"success": False, "message": f"HTTP请求失败: {response.status_code}"}

        except requests.exceptions.Timeout:
            return {"success": False, "message": "请求超时"}
        except requests.exceptions.ConnectionError:
            return {"success": False, "message": "网络连接失败，请检查网络"}
        except Exception as e:
            return {"success": False, "message": f"请求异常: {str(e)}"}

    def get_fund(self) -> Dict:
        """查询可用资金"""
        url = f"{THS_API_BASE}/cgiwt/delegate/updateclass"
        data = {
            "type": "cmd_wt_mairu",
            "updateClass": "qryzijin|"
        }
        result = self._api_post(url, data)
        if result["success"]:
            try:
                # API 返回格式: result.data.result.qryzijin.result.data.kyje
                api_data = result["data"]
                qryzijin = (api_data.get("result") or {}).get("qryzijin") or {}
                if qryzijin.get("errorcode") == 0:
                    fund_data = (qryzijin.get("result") or {}).get("data") or {}
                    self.ky_fund = float(fund_data.get("kyje", 0))
                    self.gdzh = fund_data.get("zjzh", "")  # 保存资金账号
                    return {"success": True, "data": self.ky_fund}
                else:
                    err = qryzijin.get("errormsg", "未知错误")
                    return {"success": False, "message": f"查询资金失败: {err}"}
            except (KeyError, TypeError, ValueError) as e:
                return {"success": False, "message": f"解析资金数据失败: {str(e)}"}
        return result

    def get_positions(self) -> Dict:
        """查询持仓 + 完整资金信息"""
        url = f"{THS_API_BASE}/cgiwt/delegate/updateclass"
        data = {
            "type": "cmd_wt_mairu",
            "updateClass": "qryzijin|qryChicang|"
        }
        result = self._api_post(url, data)
        if result["success"]:
            try:
                api_data = result["data"]
                # 解析资金 — 提取所有字段
                fund_info = {
                    "balance": 0, "available": 0, "frozen": 0,
                    "withdrawable": 0, "total_assets": 0, "market_value": 0
                }
                qryzijin = (api_data.get("result") or {}).get("qryzijin") or {}
                if qryzijin.get("errorcode") == 0:
                    fd = (qryzijin.get("result") or {}).get("data") or {}
                    fund_info["available"] = float(fd.get("kyje", 0))      # 可用余额
                    fund_info["balance"] = float(fd.get("zjye", 0))        # 资金余额
                    fund_info["frozen"] = float(fd.get("djje", 0))         # 冻结余额
                    fund_info["withdrawable"] = float(fd.get("kqje", 0))   # 可取金额
                    fund_info["total_assets"] = float(fd.get("zzc", 0))    # 总资产
                    fund_info["market_value"] = float(fd.get("sqsz", 0))   # 证券市值
                    self.ky_fund = fund_info["available"]

                # 解析持仓
                qryChicang = (api_data.get("result") or {}).get("qryChicang") or {}
                chicang_list = ((qryChicang.get("result") or {}).get("list")) or []
                positions = []
                for p in chicang_list:
                    positions.append({
                        "symbol": p.get("d_2102", ""),
                        "name": p.get("d_2103", ""),
                        "amount": int(p.get("d_2164", 0)),
                        "available": int(p.get("d_2121", 0)),
                        "cost_price": float(p.get("d_2122", 0)),
                        "market_value": float(p.get("d_2117", 0)),
                        "profit": float(p.get("d_2147", 0)),
                        "profit_rate": float(p.get("d_3616", 0)),
                        "market": p.get("d_2108", ""),
                        "gdzh": p.get("d_2106", ""),
                        "mkcode": p.get("d_2167", "")
                    })
                return {
                    "success": True,
                    "data": {
                        "balance": self.ky_fund,
                        "fund": fund_info,
                        "positions": positions
                    }
                }
            except (KeyError, TypeError) as e:
                return {"success": False, "message": f"解析持仓数据失败: {str(e)}"}
        return result

    def get_orders(self) -> Dict:
        """查询委托订单"""
        url = f"{THS_API_BASE}/cgiwt/delegate/updateclass"
        data = {
            "type": "cmd_qu_chedan",
            "updateClass": "qryzijin|qryChedan|"
        }
        result = self._api_post(url, data)
        if result["success"]:
            try:
                api_data = result["data"]
                # 解析资金
                qryzijin = (api_data.get("result") or {}).get("qryzijin") or {}
                if qryzijin.get("errorcode") == 0:
                    fund_data = (qryzijin.get("result") or {}).get("data") or {}
                    self.ky_fund = float(fund_data.get("kyje", 0))

                # 解析委托
                qryChedan = (api_data.get("result") or {}).get("qryChedan") or {}
                chedan_list = ((qryChedan.get("result") or {}).get("list")) or []
                orders = []
                for o in chedan_list:
                    orders.append({
                        "order_id": o.get("d_2135", ""),
                        "date": o.get("d_2139", ""),
                        "time": o.get("d_2140", ""),
                        "symbol": o.get("d_2102", ""),
                        "name": o.get("d_2103", ""),
                        "price": float(o.get("d_2127", 0)),
                        "amount": int(o.get("d_2126", 0)),
                        "type": o.get("d_2109", ""),
                        "status": o.get("d_2105", ""),
                        "market": o.get("d_2108", "")
                    })
                return {"success": True, "data": orders}
            except (KeyError, TypeError) as e:
                return {"success": False, "message": f"解析委托数据失败: {str(e)}"}
        return result

    def qry_stock(self, symbol: str, action: str = "buy") -> Dict:
        """查询股票信息（当前价格、可买数量、市场代码、五档盘口、股东代码）"""
        cmd = "cmd_wt_mairu" if action == "buy" else "cmd_wt_maichu"
        url = f"{THS_API_BASE}/cgiwt/delegate/qrystock/"
        data = {
            "type": cmd,
            "stockcode": symbol
        }
        result = self._api_post(url, data)
        if result["success"]:
            try:
                info = result["data"]["result"]["data"]
                mkcode = int(info.get("mkcode", 0))

                # 解析股东代码列表
                extend = info.get("extend", "")
                gdzh = ""
                if "gdzhlb*" in extend:
                    gdzh_part = extend.split("gdzhlb*")[1].split("|")[0]
                    for item in gdzh_part.split(";"):
                        parts = item.split(",")
                        if len(parts) == 2:
                            market_code, account = parts
                            if market_code == str(mkcode):
                                gdzh = account
                                break

                return {
                    "success": True,
                    "data": {
                        "cur_price": float(info.get("st_price", 0)),
                        "can_buy": int(info.get("canbuy", 0)),
                        "mkcode": mkcode,
                        "gdzh": gdzh,
                        "wudang": {
                            "buy_price": [info.get(f"mrjw{i}", 0) for i in range(1, 6)],
                            "buy_amount": [info.get(f"mrsl{i}", 0) for i in range(1, 6)],
                            "sell_price": [info.get(f"mcjw{i}", 0) for i in range(1, 6)],
                            "sell_amount": [info.get(f"mcsl{i}", 0) for i in range(1, 6)],
                        }
                    }
                }
            except (KeyError, TypeError) as e:
                return {"success": False, "message": f"解析股票数据失败: {str(e)}"}
        return result

    @staticmethod
    def _guess_mkcode(symbol: str) -> int:
        """根据股票代码推断市场代码（同花顺mkcode）
        688xxx = 科创板(3)
        600/601/603xxx = 上海主板(1)
        300xxx = 创业板(2)
        000/002xxx = 深圳主板(0)
        """
        if symbol.startswith("688"):
            return 3  # 科创板
        elif symbol.startswith("6"):
            return 1  # 上海主板
        elif symbol.startswith("3"):
            return 2  # 创业板
        else:
            return 0  # 深圳主板

    def _get_gdzh_from_positions(self, symbol: str = None) -> str:
        """从已有持仓中获取股东账号（qry_stock不可用时的备用方案）"""
        try:
            pos_result = self.get_positions()
            if pos_result["success"]:
                positions = pos_result["data"].get("positions", [])
                for p in positions:
                    if symbol and p.get("symbol") == symbol:
                        return p.get("gdzh", "")
                # 没找到指定股票的持仓，返回任意一个股东账号
                if positions:
                    return positions[0].get("gdzh", "")
        except Exception:
            pass
        return ""

    def buy(self, symbol: str, price: float, amount: int) -> Dict:
        """
        买入股票

        Args:
            symbol: 股票代码
            price: 价格（None则使用最新价）
            amount: 数量（必须是100的整数倍）
        """
        amount = int(amount / 100) * 100

        # 尝试 qry_stock 获取市场代码和价格
        mkcode = None
        gdzh = ""
        stock_info = self.qry_stock(symbol, "buy")

        if stock_info["success"]:
            mkcode = stock_info["data"]["mkcode"]
            gdzh = stock_info["data"].get("gdzh", "")
            if price is None:
                price = stock_info["data"]["cur_price"]
        else:
            # qry_stock 失败（同花顺接口限制），用备用方案
            mkcode = self._guess_mkcode(symbol)
            if price is None:
                # 用腾讯行情获取价格
                try:
                    from fast_data import fetch_realtime_single
                    quote = fetch_realtime_single(symbol)
                    if quote:
                        price = float(quote.get("price", 0))
                except Exception:
                    pass
            # 从已有持仓中获取 gdzh
            gdzh = self._get_gdzh_from_positions(symbol)

        if not price or price <= 0:
            return {"success": False, "message": f"无法获取{symbol}的当前价格，请手动输入价格"}

        # 确保 mkcode 和 gdzh 不为空
        if not mkcode and mkcode != 0:
            mkcode = self._guess_mkcode(symbol)
        if not gdzh:
            gdzh = self._get_gdzh_from_positions(symbol)

        mkcode_str = str(mkcode) if mkcode is not None else "0"
        logger.info(f"[BUY] symbol={symbol} price={price} amount={amount} mkcode={mkcode_str} gdzh={gdzh}")

        # 验证必填字段
        if not gdzh:
            return {"success": False, "message": f"无法获取{symbol}的股东代码，请先在'同步账户'中登录同花顺"}
        if mkcode_str == "0" and not symbol.startswith("0"):
            # mkcode 可能不正确，尝试重新推断
            mkcode_str = str(self._guess_mkcode(symbol))
            logger.warning(f"[BUY] mkcode 修正为 {mkcode_str}")

        url = f"{THS_API_BASE}/cgiwt/delegate/tradestock/"
        data = {
            "type": "cmd_wt_mairu",
            "mkcode": mkcode_str,
            "gdzh": gdzh,
            "stockcode": symbol,
            "price": str(price),
            "amount": str(amount)
        }
        logger.info(f"[BUY] request data: {data}")
        result = self._api_post(url, data)
        if result["success"]:
            return {
                "success": True,
                "message": f"买入委托成功: {symbol} {price}元 x{amount}股",
                "data": result["data"]
            }
        return result

    def sell(self, symbol: str, price: float, amount: int, mkcode: str = "", gdzh: str = "") -> Dict:
        """
        卖出股票

        Args:
            symbol: 股票代码
            price: 价格（None则使用最新价）
            amount: 数量（必须是100的整数倍）
            mkcode: 市场代码（从持仓中获取）
            gdzh: 股东账户（从持仓中获取）
        """
        amount = int(amount / 100) * 100

        # 尝试 qry_stock 获取市场代码和价格
        stock_info = self.qry_stock(symbol, "sell")

        if stock_info["success"]:
            if price is None:
                price = stock_info["data"]["cur_price"]
            if not mkcode:
                mkcode = stock_info["data"]["mkcode"]
            if not gdzh:
                gdzh = stock_info["data"].get("gdzh", "")
        else:
            # qry_stock 失败，用备用方案
            if not mkcode:
                mkcode = self._guess_mkcode(symbol)
            if price is None:
                try:
                    from fast_data import fetch_realtime_single
                    quote = fetch_realtime_single(symbol)
                    if quote:
                        price = float(quote.get("price", 0))
                except Exception:
                    pass
            if not gdzh:
                gdzh = self._get_gdzh_from_positions(symbol)

        if not price or price <= 0:
            return {"success": False, "message": f"无法获取{symbol}的当前价格，请手动输入价格"}

        # 确保 gdzh 不为空
        if not gdzh:
            return {"success": False, "message": f"无法获取{symbol}的股东代码，请先在'同步账户'中登录同花顺"}

        # 确保 mkcode 不为空
        if not mkcode and mkcode != 0:
            mkcode = self._guess_mkcode(symbol)

        mkcode_str = str(mkcode) if mkcode is not None else "0"
        logger.info(f"[SELL] symbol={symbol} price={price} amount={amount} mkcode={mkcode_str} gdzh={gdzh}")

        url = f"{THS_API_BASE}/cgiwt/delegate/tradestock/"
        data = {
            "type": "cmd_wt_maichu",
            "mkcode": mkcode_str,
            "gdzh": gdzh,
            "stockcode": symbol,
            "price": str(price),
            "amount": str(amount)
        }
        logger.info(f"[SELL] request data: {data}")
        result = self._api_post(url, data)
        if result["success"]:
            return {
                "success": True,
                "message": f"卖出委托成功: {symbol} {price}元 x{amount}股",
                "data": result["data"]
            }
        return result

    def cancel_order(self, order_id: str, date: str) -> Dict:
        """撤单"""
        url = f"{THS_API_BASE}/cgiwt/delegate/cancelDelegated/"
        data = {
            "htbh": order_id,
            "wtrq": date
        }
        result = self._api_post(url, data)
        if result["success"]:
            return {"success": True, "message": "撤单成功"}
        return result

    def cancel_all_orders(self) -> Dict:
        """撤销所有未成交委托"""
        orders_result = self.get_orders()
        if not orders_result["success"]:
            return orders_result

        cancelled = 0
        failed = 0
        for order in orders_result["data"]:
            if order["status"] == "未成交":
                result = self.cancel_order(order["order_id"], order["date"])
                if result["success"]:
                    cancelled += 1
                else:
                    failed += 1

        return {"success": True, "message": f"撤单完成: 成功{cancelled}笔, 失败{failed}笔"}

    def get_user_info(self) -> Dict:
        """
        获取当前登录用户的信息（昵称、比赛名称等）

        尝试多个API端点获取用户信息:
        1. 主页HTML中提取用户昵称
        2. 比赛列表接口获取比赛名称
        """
        if not self.cookie:
            return {"success": False, "message": "未登录"}

        result = {"username": "", "competition_name": "", "gdzh": self.gdzh or ""}

        try:
            headers = {
                "Cookie": self.cookie,
                "Host": "mncg.10jqka.com.cn",
                "Referer": "https://mncg.10jqka.com.cn/cgiwt/index/index",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }

            # 方法1: 从主页提取用户名
            try:
                resp = requests.get("https://mncg.10jqka.com.cn/cgiwt/index/index",
                                    headers=headers, timeout=10,
                                    proxies={"http": None, "https": None})
                if resp.status_code == 200:
                    text = resp.text
                    # 常见格式: <span class="user-name">张三</span> 或 data-username="xxx"
                    import re
                    # 尝试多种模式提取用户名
                    for pattern in [
                        r'data-username="([^"]+)"',
                        r'class="user-name"[^>]*>([^<]+)<',
                        r'"username"\s*:\s*"([^"]+)"',
                        r'"nickname"\s*:\s*"([^"]+)"',
                        r'"realname"\s*:\s*"([^"]+)"',
                    ]:
                        m = re.search(pattern, text)
                        if m:
                            result["username"] = m.group(1).strip()
                            break

                    # 提取比赛名称
                    for pattern in [
                        r'"competition_name"\s*:\s*"([^"]+)"',
                        r'"gameName"\s*:\s*"([^"]+)"',
                        r'"title"\s*:\s*"([^"]*比赛[^"]*)"',
                        r'<title>([^<]*比赛[^<]*)</title>',
                    ]:
                        m = re.search(pattern, text)
                        if m:
                            result["competition_name"] = m.group(1).strip()
                            break
            except Exception as e:
                logger.debug(f"主页提取用户信息失败: {e}")

            # 方法2: 通过API获取比赛列表
            if not result["competition_name"]:
                try:
                    resp = requests.post(
                        "https://mncg.10jqka.com.cn/cgiwt/delegate/updateclass",
                        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
                        data="type=cmd_wt_mairu&updateClass=queryGame|",
                        timeout=10,
                        proxies={"http": None, "https": None}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        game_data = ((data.get("result") or {}).get("queryGame") or {}).get("result") or {}
                        if game_data:
                            game_list = game_data.get("list", game_data.get("data", []))
                            if isinstance(game_list, list) and game_list:
                                game = game_list[0]
                                result["competition_name"] = (
                                    game.get("game_name", "") or
                                    game.get("gameName", "") or
                                    game.get("title", "")
                                )
                except Exception as e:
                    logger.debug(f"比赛列表API获取失败: {e}")

        except Exception as e:
            logger.error(f"获取用户信息异常: {e}")

        return {"success": True, "data": result}

    def get_account_summary(self) -> Dict:
        """获取完整账户摘要（余额+持仓+委托+总资产）"""
        fund_result = self.get_fund()
        pos_result = self.get_positions()
        order_result = self.get_orders()

        summary = {
            "balance": self.ky_fund,
            "positions": [],
            "orders": []
        }

        if pos_result["success"]:
            summary["positions"] = pos_result["data"].get("positions", [])

        if order_result["success"]:
            summary["orders"] = order_result["data"]

        total_market_value = sum(p.get("market_value", 0) for p in summary["positions"])
        total_profit = sum(p.get("profit", 0) for p in summary["positions"])
        summary["market_value"] = total_market_value
        summary["total_assets"] = self.ky_fund + total_market_value
        summary["total_profit"] = total_profit

        return {"success": True, "data": summary}


# 全局连接器实例
desktop_connector = THSDesktopConnector()
web_connector = THSWebConnector()


# 同花顺客户端默认路径
_DEFAULT_THS_PATHS = [
    r"C:\同花顺软件\同花顺\xiadan.exe",
    r"C:\同花顺\xiadan.exe",
    r"D:\同花顺软件\同花顺\xiadan.exe",
    r"D:\同花顺\xiadan.exe",
]


def auto_connect_desktop() -> Dict:
    """
    自动启动并连接同花顺桌面客户端
    启动时调用，优先级最高
    1. 已连接则跳过
    2. 尝试连接已运行的xiadan.exe
    3. 找不到运行中的进程则自动启动
    Returns: {success, message, exe_path}
    """
    # 已连接则跳过
    if desktop_connector.connected:
        return {"success": True, "message": "桌面客户端已连接"}

    # 使用 launch_and_connect：自动检测/启动/连接
    return desktop_connector.launch_and_connect()


def get_active_connector():
    """
    获取当前活跃的连接器 — 网页API
    """
    if web_connector.connected and web_connector.cookie:
        return web_connector, "web"
    # 通过 account_manager 查找
    try:
        from account_manager import account_manager
        from db_store import db_store
        accounts = db_store.list_accounts()
        if accounts:
            active_acc = next((a for a in accounts if a.get("ths_gdzh")), accounts[0])
            trader = account_manager.get_trader(active_acc["id"])
            if trader and trader.connected:
                return trader, "web"
    except Exception:
        pass
    return None, None


def get_connection_info() -> Dict:
    """获取连接状态详情（供前端显示）"""
    info = {
        "web_connected": web_connector.connected and bool(web_connector.cookie),
        "desktop_connected": desktop_connector.connected,
    }

    if info["web_connected"] and info["desktop_connected"]:
        info.update({
            "mode": "hybrid",
            "connected": True,
            "source": "网页API + 桌面客户端",
            "message": "读数据走网页API(零验证码)，下单走桌面客户端",
        })
    elif info["web_connected"]:
        info.update({
            "mode": "web",
            "connected": True,
            "source": "同花顺网页API (mncg.10jqka.com.cn)",
            "message": "通过网页API连接，读数据零验证码",
        })
    elif info["desktop_connected"]:
        info.update({
            "mode": "desktop",
            "connected": True,
            "source": "同花顺桌面客户端 (easytrader)",
            "message": "通过桌面客户端连接（持仓读取可能触发验证码）",
            "exe_path": desktop_connector.exe_path,
        })
    else:
        info.update({
            "mode": "none",
            "connected": False,
            "source": "未连接",
            "message": "请先扫码登录网页API或连接桌面客户端",
        })
    return info


# ========== 扫码登录 ==========

class QRLoginManager:
    """
    同花顺扫码登录管理器
    通过同花顺passport API生成二维码，用户扫码后自动获取Cookie
    支持30天免登录（remember=1）
    """

    # 同花顺passport二维码API
    _PASSPORT_BASE = "https://passport.10jqka.com.cn"
    _QR_GENERATE_URL = f"{_PASSPORT_BASE}/iframe/qrcode"
    _QR_POLL_URL = f"{_PASSPORT_BASE}/iframe/qrcode/query"
    _QR_CANCEL_URL = f"{_PASSPORT_BASE}/iframe/qrcode/cancel"

    def __init__(self):
        self._qrcode_id: Optional[str] = None
        self._qr_url: Optional[str] = None
        self._session = requests.Session()
        self._session.trust_env = False  # 绕过系统代理，直连同花顺服务器
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
            "Referer": "https://mncg.10jqka.com.cn/",
        })

    def generate_qrcode(self) -> Dict:
        """
        生成登录二维码
        Returns: {success, qrcode_id, qr_url, qr_image_base64}
        """
        try:
            # 请求二维码 — 带remember=1获取30天免登录
            resp = self._session.get(
                self._QR_GENERATE_URL,
                params={"remember": "1"},
                timeout=10
            )

            if resp.status_code != 200:
                return {"success": False, "message": f"请求失败: HTTP {resp.status_code}"}

            data = resp.json()
            qr_data = data.get("data", {})
            qr_url = qr_data.get("qrurl", "")
            qrcode_id = qr_data.get("qrcode_id", "") or qr_data.get("id", "")

            if not qr_url or not qrcode_id:
                return {"success": False, "message": "未获取到二维码数据", "raw": data}

            self._qrcode_id = qrcode_id
            self._qr_url = qr_url

            # 生成二维码图片（base64）
            qr_image_base64 = self._url_to_qr_base64(qr_url)

            logger.info(f"二维码已生成: id={qrcode_id}")
            return {
                "success": True,
                "qrcode_id": qrcode_id,
                "qr_url": qr_url,
                "qr_image_base64": qr_image_base64,
            }

        except requests.exceptions.Timeout:
            return {"success": False, "message": "请求超时，请检查网络"}
        except requests.exceptions.ConnectionError:
            return {"success": False, "message": "网络连接失败"}
        except Exception as e:
            logger.error(f"生成二维码失败: {e}")
            return {"success": False, "message": str(e)}

    def poll_status(self, qrcode_id: str = None) -> Dict:
        """
        轮询扫码状态
        Returns: {success, status, message, cookie?}
        status: "waiting" / "scanned" / "confirmed" / "expired" / "error"
        """
        qid = qrcode_id or self._qrcode_id
        if not qid:
            return {"success": False, "status": "error", "message": "无二维码ID"}

        try:
            resp = self._session.get(
                self._QR_POLL_URL,
                params={"id": qid},
                timeout=10
            )

            if resp.status_code != 200:
                return {"success": False, "status": "error", "message": f"HTTP {resp.status_code}"}

            data = resp.json()
            qr_status = data.get("data", {}).get("status", -1)
            # status: 0=等待扫码, 1=已扫码等待确认, 2=确认成功, -1=已过期

            if qr_status == 2 or qr_status == 1:
                # 扫码成功或已确认 — 提取Cookie
                # Cookie在response headers的Set-Cookie中
                cookies_dict = dict(self._session.cookies)
                cookie_str = "; ".join(f"{k}={v}" for k, v in cookies_dict.items())

                if cookie_str:
                    return {
                        "success": True,
                        "status": "confirmed",
                        "cookie": cookie_str,
                        "cookies_dict": cookies_dict,
                        "message": "扫码成功",
                    }
                else:
                    # 尝试从redirect URL获取
                    redirect_url = data.get("data", {}).get("url", "")
                    if redirect_url:
                        return {
                            "success": True,
                            "status": "confirmed",
                            "redirect_url": redirect_url,
                            "message": "扫码成功，需要跳转获取Cookie",
                        }
                    return {"success": False, "status": "scanned", "message": "已扫码，等待Cookie"}

            elif qr_status == 0:
                return {"success": True, "status": "waiting", "message": "等待扫码..."}
            elif qr_status == -1:
                return {"success": True, "status": "expired", "message": "二维码已过期，请重新生成"}
            else:
                return {"success": True, "status": "waiting", "message": f"状态: {qr_status}"}

        except Exception as e:
            logger.error(f"轮询二维码状态失败: {e}")
            return {"success": False, "status": "error", "message": str(e)}

    def validate_and_save_cookie(self, cookie: str) -> Dict:
        """
        验证Cookie并保存（调用web_connector）
        """
        result = web_connector.save_cookie(cookie)
        if result["success"]:
            # 额外保存到扫码登录专用文件（带时间戳）
            self._save_qr_cookie_record(cookie)
            logger.info("扫码登录Cookie验证并保存成功")
        return result

    def _save_qr_cookie_record(self, cookie: str):
        """保存扫码登录记录"""
        try:
            record_file = COOKIE_DIR / "qr_login_record.json"
            record = {
                "cookie_encrypted": encrypt_cookie(cookie),
                "login_time": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S"),
                "login_method": "qrcode",
                "remember": True,
            }
            with open(record_file, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_qr_cookie(self) -> Dict:
        """
        加载扫码登录保存的Cookie（30天内有效）
        """
        try:
            record_file = COOKIE_DIR / "qr_login_record.json"
            if not record_file.exists():
                return {"success": False, "message": "无扫码登录记录"}

            with open(record_file, "r", encoding="utf-8") as f:
                record = json.load(f)

            login_time = datetime.strptime(record.get("login_time", ""), "%Y-%m-%d %H:%M:%S")
            days_since = (datetime.now(ZoneInfo('Asia/Shanghai')) - login_time).days

            if days_since > 30:
                return {"success": False, "message": f"扫码登录已过期({days_since}天前), 请重新扫码"}

            cookie = decrypt_cookie(record.get("cookie_encrypted", ""))
            if not cookie:
                return {"success": False, "message": "Cookie解密失败"}

            # 验证Cookie是否仍然有效
            result = web_connector.save_cookie(cookie)
            if result["success"]:
                logger.info(f"扫码登录Cookie加载成功 (已用{days_since}天, 剩余{30-days_since}天)")
                return {
                    "success": True,
                    "message": f"Cookie加载成功 (已用{days_since}天, 剩余{30-days_since}天)",
                    "days_remaining": 30 - days_since,
                }
            else:
                return {"success": False, "message": f"Cookie已失效: {result['message']}"}

        except Exception as e:
            return {"success": False, "message": str(e)}

    def cancel_qrcode(self, qrcode_id: str = None):
        """取消二维码（用户关闭弹窗时调用）"""
        qid = qrcode_id or self._qrcode_id
        if not qid:
            return
        try:
            self._session.get(self._QR_CANCEL_URL, params={"id": qid}, timeout=5)
        except Exception:
            pass
        self._qrcode_id = None
        self._qr_url = None

    @staticmethod
    def _url_to_qr_base64(url: str) -> str:
        """将URL转换为二维码base64图片（使用segno，无需PIL）"""
        try:
            import segno
            import io
            import base64

            qr = segno.make(url)
            buffer = io.BytesIO()
            qr.save(buffer, kind="png", scale=8, border=2)
            buffer.seek(0)
            return base64.b64encode(buffer.getvalue()).decode("ascii")

        except ImportError:
            logger.warning("segno库未安装，无法生成二维码图片")
            return ""
        except Exception as e:
            logger.error(f"生成二维码图片失败: {e}")
            return ""


# 全局扫码登录实例
qr_login_manager = QRLoginManager()
