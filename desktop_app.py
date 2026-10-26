"""
THS Trading AI - Desktop Application
System tray with auto-start, notifications, and market alerts.
"""

import sys
import os
import time
import signal
import logging
import threading
import webbrowser
import json
from pathlib import Path
from datetime import datetime, time as dtime

# Setup path
PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(PROJECT_DIR / "trading.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("desktop")

# --- Lazy imports ---
pystray = None
Image = ImageDraw = None
win10toast = None


def _import_gui():
    global pystray, Image, ImageDraw
    try:
        import pystray as _pystray
        from PIL import Image as _Image, ImageDraw as _ImageDraw
        pystray = _pystray
        Image = _Image
        ImageDraw = _ImageDraw
        return True
    except ImportError:
        logger.error("Missing: pip install pystray Pillow")
        return False


def _import_toast():
    global win10toast
    try:
        from win10toast import ToastNotifier as _Toast
        win10toast = _Toast()
        return True
    except ImportError:
        return False


# ================================================================
#  Auto-Start (Windows)
# ================================================================

AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "THSTradingAI"
APP_PATH = str(PROJECT_DIR / "desktop_app.py")


def set_autostart(enable=True):
    """设置开机自启动"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'pythonw "{APP_PATH}"')
            logger.info("Auto-start enabled")
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
            logger.info("Auto-start disabled")
        winreg.CloseKey(key)
        return True
    except Exception as e:
        logger.error(f"Auto-start failed: {e}")
        return False


def setup_firewall():
    """自动配置Windows防火墙"""
    import subprocess
    try:
        # 检查规则是否已存在
        result = subprocess.run(
            ['netsh', 'advfirewall', 'firewall', 'show', 'rule', 'name=THSTradingAI'],
            capture_output=True, text=True
        )
        if 'THSTradingAI' in result.stdout:
            logger.info("Firewall rule already exists")
            return True

        # 添加规则
        result = subprocess.run(
            ['netsh', 'advfirewall', 'firewall', 'add', 'rule',
             'name=THSTradingAI', 'dir=in', 'action=allow',
             'protocol=TCP', 'localport=5000'],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            logger.info("Firewall rule added successfully")
            return True
        else:
            logger.warning(f"Firewall rule add failed (may need admin): {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Firewall setup failed: {e}")
        return False


def is_autostart():
    """检查是否设置了开机自启动"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ)
        try:
            winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False
    except Exception:
        return False


# ================================================================
#  Notifications
# ================================================================

def show_notification(title, message, duration=5):
    """显示桌面通知"""
    if win10toast:
        try:
            win10toast.show_toast(title, message, duration=duration, threaded=True)
        except Exception as e:
            logger.error(f"Notification failed: {e}")
    else:
        logger.info(f"NOTIFICATION: {title} - {message}")


def check_market_status():
    """检查市场状态并发送通知"""
    now = datetime.now()
    current_time = now.time()
    weekday = now.weekday()

    # 周末不交易
    if weekday >= 5:
        return

    # 交易时段
    morning_open = dtime(9, 30)
    morning_close = dtime(11, 30)
    afternoon_open = dtime(13, 0)
    afternoon_close = dtime(15, 0)

    # 检查是否刚开市
    if morning_open <= current_time <= dtime(9, 35):
        show_notification("开市提醒", "上午盘已开市 (9:30-11:30)")
    elif afternoon_open <= current_time <= dtime(13, 5):
        show_notification("开市提醒", "下午盘已开市 (13:00-15:00)")

    # 检查是否快收市
    if dtime(14, 55) <= current_time <= dtime(15, 0):
        show_notification("收市提醒", "即将收市，请检查持仓")


# ================================================================
#  Trading Daemon
# ================================================================

class TradingDaemon:
    def __init__(self):
        self.flask_thread = None
        self.services_running = False
        self._icon = None
        self._shutdown_event = threading.Event()
        self._market_check_thread = None

    def start_all(self):
        """启动所有服务"""
        if self.services_running:
            return

        # 0. 配置防火墙
        setup_firewall()

        # 1. Flask API
        self.flask_thread = threading.Thread(target=self._run_flask, daemon=True)
        self.flask_thread.start()
        logger.info("Flask API starting on http://127.0.0.1:5000")
        time.sleep(1.5)

        # 2. AutoTrader
        try:
            from auto_trader import auto_trader
            auto_trader.enabled = True
            auto_trader.start_monitor()
            logger.info("AutoTrader started")

            def _sync():
                time.sleep(3)
                try:
                    from ths_connector import web_connector
                    web_connector.load_cookie()
                except Exception:
                    pass
                auto_trader.sync_from_ths()
            threading.Thread(target=_sync, daemon=True).start()
        except Exception as e:
            logger.error(f"AutoTrader failed: {e}")

        # 3. AI Scheduler
        try:
            from ai_scheduler import ai_scheduler
            ai_scheduler.start()
            logger.info(f"AI Scheduler started (interval={ai_scheduler.interval_minutes}min)")
        except Exception as e:
            logger.error(f"AI Scheduler failed: {e}")

        # 4. 注册通知回调（发送桌面通知）
        try:
            from notification import notification_manager
            def _on_notification(notification):
                title = notification.get("title", "")
                message = notification.get("message", "")
                if title:
                    show_notification(title, message)
            notification_manager.add_callback(_on_notification)
            logger.info("Notification callback registered")
        except Exception as e:
            logger.error(f"Notification callback failed: {e}")

        # 5. 市场状态监控
        self._start_market_monitor()

        self.services_running = True
        self._update_icon("green")
        show_notification("系统启动", "所有服务已启动，自动交易已开启")
        logger.info("All services started")

    def stop_all(self):
        """停止所有服务"""
        try:
            from auto_trader import auto_trader
            auto_trader.stop_monitor()
        except Exception:
            pass

        try:
            from ai_scheduler import ai_scheduler
            ai_scheduler.stop()
        except Exception:
            pass

        self.services_running = False
        self._update_icon("red")
        show_notification("系统停止", "所有服务已停止")
        logger.info("All services stopped")

    def _start_market_monitor(self):
        """启动市场状态监控"""
        def _monitor():
            last_status = None
            while self.services_running:
                try:
                    now = datetime.now()
                    current_time = now.time()
                    weekday = now.weekday()

                    if weekday < 5:  # 工作日
                        morning_open = dtime(9, 30)
                        afternoon_close = dtime(15, 0)

                        if morning_open <= current_time <= afternoon_close:
                            current_status = "trading"
                        else:
                            current_status = "closed"
                    else:
                        current_status = "weekend"

                    # 状态变化时通知
                    if current_status != last_status:
                        if current_status == "trading" and last_status == "closed":
                            show_notification("开市提醒", "交易时段已开始")
                        elif current_status == "closed" and last_status == "trading":
                            show_notification("收市提醒", "交易时段已结束")
                        last_status = current_status

                    # 检查交易通知
                    check_market_status()

                except Exception as e:
                    logger.error(f"Market monitor error: {e}")

                time.sleep(60)  # 每分钟检查一次

        self._market_check_thread = threading.Thread(target=_monitor, daemon=True)
        self._market_check_thread.start()

    def request_exit(self):
        """请求退出"""
        logger.info("Exit requested...")
        self.stop_all()
        self._shutdown_event.set()
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
        def _force():
            time.sleep(0.3)
            os._exit(0)
        threading.Thread(target=_force, daemon=True).start()

    def _run_flask(self):
        from api import app

        @app.route("/api/daemon/shutdown", methods=["POST"])
        def daemon_shutdown():
            from flask import jsonify
            threading.Thread(target=self.request_exit, daemon=True).start()
            return jsonify({"success": True, "message": "Shutting down"})

        @app.route("/api/daemon/status", methods=["GET"])
        def daemon_status():
            from flask import jsonify
            return jsonify({
                "success": True,
                "data": {
                    "services_running": self.services_running,
                    "autostart": is_autostart()
                }
            })

        @app.route("/api/daemon/autostart", methods=["POST"])
        def daemon_autostart():
            from flask import jsonify, request
            data = request.json or {}
            enable = data.get("enable", True)
            set_autostart(enable)
            return jsonify({"success": True, "autostart": enable})

        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

    def set_icon(self, icon):
        self._icon = icon

    def _update_icon(self, color):
        if self._icon and Image:
            self._icon.icon = _create_icon(color)


# ================================================================
#  System Tray Icon
# ================================================================

def _create_icon(color="green"):
    if not Image:
        return None
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    palette = {"green": "#4CAF50", "red": "#F44336", "gray": "#9E9E9E"}
    draw.ellipse([4, 4, 60, 60], fill=palette.get(color, "#4CAF50"))
    return img


# ================================================================
#  Menu callbacks
# ================================================================

daemon = TradingDaemon()


def _on_open(icon, item):
    webbrowser.open("http://localhost:5000")


def _on_toggle(icon, item):
    if daemon.services_running:
        daemon.stop_all()
    else:
        daemon.start_all()


def _on_autostart(icon, item):
    current = is_autostart()
    set_autostart(not current)
    show_notification("自启动设置", f"开机自启动已{'开启' if not current else '关闭'}")


def _on_exit(icon, item):
    threading.Thread(target=daemon.request_exit, daemon=True).start()


# ================================================================
#  Main
# ================================================================

def main():
    _import_gui()
    _import_toast()

    # 解析命令行参数
    if "--autostart" in sys.argv:
        set_autostart(True)
        logger.info("Auto-start enabled via command line")
        return
    if "--no-autostart" in sys.argv:
        set_autostart(False)
        logger.info("Auto-start disabled via command line")
        return

    if not pystray:
        logger.warning("pystray not available, running headless")
        daemon.start_all()
        def _sig(sig, frame):
            daemon.request_exit()
            sys.exit(0)
        signal.signal(signal.SIGTERM, _sig)
        signal.signal(signal.SIGINT, _sig)
        daemon._shutdown_event.wait()
        return

    menu = pystray.Menu(
        pystray.MenuItem("Open Panel", _on_open, default=True),
        pystray.MenuItem("Toggle Services", _on_toggle),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(f"Auto-Start: {'ON' if is_autostart() else 'OFF'}", _on_autostart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", _on_exit),
    )

    icon = pystray.Icon(
        "THS Trading AI",
        _create_icon("green"),
        "THS Trading AI",
        menu,
    )

    daemon.set_icon(icon)
    daemon.start_all()

    threading.Timer(2.0, lambda: webbrowser.open("http://localhost:5000")).start()

    icon.run()


if __name__ == "__main__":
    main()
