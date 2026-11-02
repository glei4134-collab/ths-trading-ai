"""
量化系统 Flask 核心应用初始化与蓝图注册中心
生产入口：gunicorn -c deploy/gunicorn_conf.py api:app
本地入口：python run_api.py
"""

import os
import sys
import logging
import threading
from pathlib import Path
from flask import Flask, send_from_directory, request
from flask_cors import CORS

from api.context import require_auth, auto_login_service
from api.routes import (
    account_bp,
    trade_bp,
    market_bp,
    scheduler_bp,
    system_bp
)

logger = logging.getLogger(__name__)

# 全局结构化日志配置（确保 ai_scheduler、account_manager 等 logger.info 能正常输出至 systemd journalctl）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

_boot_started = False
_boot_lock = threading.Lock()


def boot_runtime_services():
    """调度器 / 保活 / 静默体检只启动一次。"""
    global _boot_started
    with _boot_lock:
        if _boot_started:
            return
        _boot_started = True

    try:
        auto_login_service.start_keep_alive(600)
    except Exception as e:
        logger.debug(f"启动保活守护异常: {e}")

    try:
        from tools.auto_debugger import silent_health_daemon
        silent_health_daemon.start(30)
    except Exception as e:
        logger.debug(f"启动静默健康自愈守护异常: {e}")

    try:
        from notification import daily_scheduler
        daily_scheduler.start()
    except Exception:
        pass

    try:
        from ai_scheduler import ai_scheduler
        if ai_scheduler.enabled:
            ai_scheduler.start()
            logger.info(f"AI自动交易调度器已启动 (间隔={ai_scheduler.interval_minutes}分钟)")
    except Exception as e:
        logger.error(f"AI调度器启动失败: {e}")

    def _load_cookie_bg():
        try:
            from ths_connector import web_connector, decrypt_cookie
            from db_store import db_store
            active_id = db_store.get_active_account_id()
            active_acc = db_store.get_account(active_id)
            if active_acc and active_acc.get("cookie"):
                raw_cookie = decrypt_cookie(active_acc["cookie"])
                if raw_cookie:
                    web_connector.cookie = raw_cookie
                    test_result = web_connector.get_fund()
                    if test_result.get("success"):
                        web_connector.connected = True
                        logger.info("网页API已连接")
        except Exception as e:
            logger.debug(f"Cookie加载: {e}")

    threading.Thread(target=_load_cookie_bg, daemon=True, name="cookie-boot").start()


def create_app() -> Flask:
    """应用工厂函数"""
    static_folder = Path(__file__).resolve().parent.parent / "static"
    app = Flask(__name__, static_folder=str(static_folder))
    CORS(app)

    boot_runtime_services()

    @app.before_request
    def _security_firewall():
        """轻量级安全防护中间件：毫秒级静默拦截公网机器人与恶意爬虫扫描，防范单线程资源耗尽"""
        path = request.path.lower()
        MALICIOUS_PROBES = (
            "/.env", "/wp-config", "/.aws", "/.git", "/phpinfo", "/wp-json",
            "/.s3cfg", "/wp-admin", "/wp-login", "/phpmyadmin", "/.svn",
            "/.ds_store", "/credentials", "/id_rsa", "/config.json", "/actuator"
        )
        if any(probe in path for probe in MALICIOUS_PROBES) or path.endswith((".php", ".env", ".yml", ".yaml")):
            logger.warning(f"🚫 [WAF] 拦截公网恶意探测: {request.remote_addr} -> {request.path}")
            return ("", 403)

    @app.before_request
    def _check_auth():
        return require_auth()

    @app.after_request
    def add_no_cache_headers(response):
        if request.path == '/' or request.path.endswith('.html') or request.path.endswith('.js'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response

    app.register_blueprint(account_bp)
    app.register_blueprint(trade_bp)
    app.register_blueprint(market_bp)
    app.register_blueprint(scheduler_bp)
    app.register_blueprint(system_bp)

    @app.route('/')
    def index():
        return send_from_directory(app.static_folder, 'index.html')

    @app.route('/<path:path>', methods=['GET'])
    def static_files(path):
        if path.startswith('api/'):
            return {"success": False, "message": "API endpoint not found"}, 404
        return send_from_directory(app.static_folder, path)

    return app


_app_instance = None


def __getattr__(name: str):
    global _app_instance
    if name == "app":
        if _app_instance is None:
            _app_instance = create_app()
        return _app_instance
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
