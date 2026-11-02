"""
量化系统 Flask 核心应用初始化与蓝图注册中心
生产入口：gunicorn -c deploy/gunicorn_conf.py api:app
本地入口：python run_api.py
"""

import os
import sys
import time
import logging
from logging.handlers import TimedRotatingFileHandler
import threading
from pathlib import Path
from urllib.parse import unquote
from flask import Flask, send_from_directory, request, jsonify
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
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        TimedRotatingFileHandler(Path(__file__).resolve().parent.parent / "trading.log",
                                 when="midnight", backupCount=14, encoding="utf-8"),
    ],
)

_boot_started = False
_boot_lock = threading.Lock()

# ===== 登录端点速率限制与防爆破状态 =====
_login_attempts = {}        # {ip: [timestamp, ...]}  最近窗口内的请求时间戳
_login_failures = {}        # {ip: count}              连续密码错误次数
_login_blacklist = {}       # {ip: expiry_timestamp}   IP 黑名单到期时间
_login_lock = threading.Lock()

_LOGIN_PATHS = {
    '/api/auth/login',
    '/api/ths/auth/login-pwd',
    '/api/ths/auth/login-sms',
}
_RATE_LIMIT_WINDOW = 60        # 60 秒滑动窗口
_RATE_LIMIT_MAX = 5            # 窗口内最多 5 次请求
_BLACKLIST_DURATION = 15 * 60  # 连续失败后拉黑 15 分钟
_MAX_CONSECUTIVE_FAILURES = 5  # 连续 5 次密码错误触发拉黑


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
        # URL 解码防绕过：先解码再转小写，检测 %2e%2e%2f 等编码绕过与路径穿越
        decoded_path = unquote(request.path).lower()
        MALICIOUS_PROBES = (
            "/.env", "/wp-config", "/.aws", "/.git", "/phpinfo", "/wp-json",
            "/.s3cfg", "/wp-admin", "/wp-login", "/phpmyadmin", "/.svn",
            "/.ds_store", "/credentials", "/id_rsa", "/config.json", "/actuator"
        )
        # 检测 '..' 路径段穿越（如 ../、/..、..\ 等）
        if '..' in decoded_path.split('/') or '../' in decoded_path or '..\\' in decoded_path:
            logger.warning(f"🚫 [WAF] 拦截路径穿越攻击: {request.remote_addr} -> {request.path}")
            return ("", 403)
        if any(probe in decoded_path for probe in MALICIOUS_PROBES) or decoded_path.endswith((".php", ".env", ".yml", ".yaml")):
            logger.warning(f"🚫 [WAF] 拦截公网恶意探测: {request.remote_addr} -> {request.path}")
            return ("", 403)

    @app.before_request
    def _login_rate_limit():
        """登录端点轻量级内存令牌桶限流 + IP 黑名单"""
        path = request.path
        if path not in _LOGIN_PATHS:
            return None
        ip = request.remote_addr or "unknown"
        now = time.time()
        with _login_lock:
            # 检查是否在黑名单内
            expiry = _login_blacklist.get(ip)
            if expiry:
                if now < expiry:
                    logger.warning(f"🚫 [限流] IP {ip} 处于登录黑名单 (剩余 {int(expiry - now)}秒)")
                    return jsonify({"success": False, "message": "登录尝试过于频繁，请 15 分钟后再试"}), 429
                # 黑名单已过期，清理
                del _login_blacklist[ip]

            # 清理窗口外的旧时间戳
            attempts = [t for t in _login_attempts.get(ip, []) if now - t < _RATE_LIMIT_WINDOW]

            if len(attempts) >= _RATE_LIMIT_MAX:
                logger.warning(f"🚫 [限流] IP {ip} 登录请求超限 ({len(attempts)}/{_RATE_LIMIT_MAX} 在 {_RATE_LIMIT_WINDOW}s 内)")
                return jsonify({"success": False, "message": "请求过于频繁，请稍后再试"}), 429

            attempts.append(now)
            _login_attempts[ip] = attempts
        return None

    @app.before_request
    def _check_auth():
        return require_auth()

    @app.after_request
    def _track_login_failures(response):
        """跟踪登录失败次数，连续 5 次密码错误将 IP 加入 15 分钟黑名单"""
        path = request.path
        if path in _LOGIN_PATHS:
            ip = request.remote_addr or "unknown"
            now = time.time()
            # 登录失败判定：状态码 400/401，或 JSON 中 success=False
            is_failure = response.status_code in (400, 401)
            if not is_failure:
                try:
                    data = response.get_json(silent=True)
                    if isinstance(data, dict) and data.get("success") is False:
                        is_failure = True
                except Exception:
                    pass
            with _login_lock:
                if is_failure:
                    _login_failures[ip] = _login_failures.get(ip, 0) + 1
                    if _login_failures[ip] >= _MAX_CONSECUTIVE_FAILURES:
                        _login_blacklist[ip] = now + _BLACKLIST_DURATION
                        _login_failures[ip] = 0
                        logger.warning(
                            f"🚫 [限流] IP {ip} 连续 {_MAX_CONSECUTIVE_FAILURES} 次登录失败，"
                            f"列入黑名单 {_BLACKLIST_DURATION // 60} 分钟"
                        )
                else:
                    # 登录成功，重置失败计数
                    _login_failures.pop(ip, None)
        return response

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
