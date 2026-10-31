"""
量化交易系统 REST API 服务器入口
重构版本：由 api/ 蓝图模块化驱动，保证 100% 路由兼容
"""

import os
import sys
import logging
import threading
from pathlib import Path

# 配置路径
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from api import create_app
from api.context import db_store, auto_login_service

logger = logging.getLogger("api")

# 创建全局 Flask app 实例 (供 WSGI 容器或直接运行调用)
app = create_app()


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "true").lower() == "true"
    print("=" * 50)
    print("同花顺模拟比赛AI交易系统 - Web前端网关 (模块化蓝图版)")
    print("=" * 50)
    print(f"访问地址: http://localhost:{port}")
    print("=" * 50)

    # 后台加载Cookie到web_connector（不阻塞Flask启动）
    def _load_cookie_bg():
        try:
            from ths_connector import web_connector, decrypt_cookie
            active_id = db_store.get_active_account_id()
            active_acc = db_store.get_account(active_id)
            if active_acc and active_acc.get("cookie"):
                raw_cookie = decrypt_cookie(active_acc["cookie"])
                if raw_cookie:
                    web_connector.cookie = raw_cookie
                    test_result = web_connector.get_fund()
                    if test_result.get("success"):
                        web_connector.connected = True
                        logger.info(f"✅ 网页API已连接 (余额: {web_connector.ky_fund})")
        except Exception as e:
            logger.debug(f"Cookie加载: {e}")
    threading.Thread(target=_load_cookie_bg, daemon=True).start()

    # 启动每日汇总调度器
    try:
        from notification import daily_scheduler
        daily_scheduler.start()
    except Exception:
        pass

    # 启动AI自动交易调度器（如已启用）
    try:
        from ai_scheduler import ai_scheduler
        if ai_scheduler.enabled:
            ai_scheduler.start()
            logger.info(f"AI自动交易调度器已启动 (间隔={ai_scheduler.interval_minutes}分钟)")
        else:
            logger.info("AI自动交易调度器未启用，跳过启动")
    except Exception as e:
        logger.error(f"AI调度器启动失败: {e}")

    # 启动时从GitHub同步云端交易记录
    try:
        from github_sync import github_sync
        def _sync_on_start():
            try:
                github_sync.sync_to_local()
            except Exception as e:
                logger.debug(f"GitHub同步: {e}")
        threading.Thread(target=_sync_on_start, daemon=True, name="github-sync").start()
    except Exception:
        pass

    # Railway / 容器环境定时同步
    if os.environ.get("RAILWAY_ENVIRONMENT"):
        from api.routes.scheduler import _setup_scheduler
        _setup_scheduler()

    app.run(debug=debug, host="0.0.0.0", port=port)
