"""
量化系统 Flask 核心应用初始化与蓝图注册中心
"""

import os
import sys
import logging
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


def create_app() -> Flask:
    """应用工厂函数"""
    static_folder = Path(__file__).resolve().parent.parent / "static"
    app = Flask(__name__, static_folder=str(static_folder))
    CORS(app)

    # 启动同花顺 Session 保活心跳 (每10分钟唤醒保活一次)
    try:
        auto_login_service.start_keep_alive(600)
    except Exception as e:
        logger.debug(f"启动保活守护异常: {e}")

    # 全局请求认证
    @app.before_request
    def _check_auth():
        return require_auth()

    # 禁用缓存头
    @app.after_request
    def add_no_cache_headers(response):
        if request.path == '/' or request.path.endswith('.html') or request.path.endswith('.js'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response

    # 优先注册各大业务蓝图 (保持 100% 原始路径兼容)
    app.register_blueprint(account_bp)
    app.register_blueprint(trade_bp)
    app.register_blueprint(market_bp)
    app.register_blueprint(scheduler_bp)
    app.register_blueprint(system_bp)

    # 静态前端页面托管 (放在蓝图注册之后，严禁拦截 /api/ 请求)
    @app.route('/')
    def index():
        return send_from_directory(app.static_folder, 'index.html')

    @app.route('/<path:path>', methods=['GET'])
    def static_files(path):
        if path.startswith('api/'):
            return {"success": False, "message": "API endpoint not found"}, 404
        return send_from_directory(app.static_folder, path)

    return app


# 兼容 `from api import app` 单例懒加载导出
_app_instance = None


def __getattr__(name: str):
    global _app_instance
    if name == "app":
        if _app_instance is None:
            _app_instance = create_app()
        return _app_instance
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
