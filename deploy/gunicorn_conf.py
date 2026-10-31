# ==============================================================================
# 生产级 Gunicorn / Gevent 高并发 WSGI 配置文件 (Zero-Downtime Hot Reload)
# ==============================================================================
import os
import sys
import multiprocessing
from pathlib import Path

# 确保导入路径
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# 1. 基础网络监听
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:5000")
backlog = 2048

# 2. Worker 进程模型
# 量化交易系统核心状态（多账户会话、持仓、风控）要求单实例或线程协程内内存一致性
# 使用 1 个 Master 进程 + gevent 异步协程 (或 gthread 多线程)，保障内存状态一致并承载高并发
try:
    import gevent
    worker_class = "gevent"
    worker_connections = 1000
except ImportError:
    worker_class = "gthread"
    threads = 8

workers = int(os.environ.get("GUNICORN_WORKERS", 1))
timeout = 60
graceful_timeout = 15
keepalive = 5

# 3. 内存泄漏防护与请求轮转
max_requests = 5000
max_requests_jitter = 500
preload_app = False  # 设为 False 以便 kill -HUP 时能够完整热重载最新代码

# 4. 进程与日志路径
DATA_DIR = REPO_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

pidfile = str(DATA_DIR / "gunicorn.pid")
accesslog = str(DATA_DIR / "gunicorn_access.log")
errorlog = str(DATA_DIR / "gunicorn_error.log")
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" (%(D)sus)'


# ==================== 生命周期钩子与零闪断热重载防护 ====================

def on_starting(server):
    """服务启动前自检"""
    server.log.info("🚀 [Gunicorn] 正在初始化量化交易生产级 WSGI 容器...")


def on_reload(server):
    """
    收到 SIGHUP (kill -HUP $MAINPID) 触发热重载时的前置检查
    无缝衔接【交易时段发布拦截熔断门禁】：若在交易敏感时段触发重载，严格拦截阻断！
    """
    server.log.info("🔄 [Gunicorn] 侦测到 SIGHUP 信号，触发零闪断平滑热重载...")
    try:
        from deploy.release_gatekeeper import ReleaseGatekeeper
        blocked, reason, wait_sec = ReleaseGatekeeper.check_release_window()
        if blocked:
            server.log.error(f"🛑 [Gunicorn Reload BLOCKED] 交易时段门禁拦截: {reason}")
            # 记录审计日志
            ReleaseGatekeeper.audit_log("GUNICORN_RELOAD_BLOCKED", {
                "reason": reason,
                "wait_seconds": wait_sec
            })
            raise RuntimeError(f"Gatekeeper Aborted Reload: {reason}")
        else:
            server.log.info(f"✅ [Gunicorn Reload Allowed] 门禁放行: {reason}")
    except Exception as e:
        if "Gatekeeper Aborted Reload" in str(e):
            raise
        server.log.warning(f"门禁检测非阻断异常: {e}")


def post_worker_init(worker):
    """Worker 进程就绪回调"""
    worker.log.info(f"🟢 [Gunicorn Worker] Worker 进程就绪 (PID: {worker.pid}, Class: {worker_class})")
    # 初始化 WAL 连接与优化
    try:
        from db_store import db_store
        with db_store._get_conn() as conn:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
    except Exception as e:
        worker.log.warning(f"Worker 初始化数据库 PRAGMA 异常: {e}")


def worker_exit(server, worker):
    """Worker 退出前优雅清理：执行 SQLite WAL 截断与连接收尾"""
    server.log.info(f"🟡 [Gunicorn Worker] Worker 退出收尾 (PID: {worker.pid})，执行 WAL Checkpoint...")
    try:
        from db_store import db_store
        db_store.checkpoint_wal(mode="TRUNCATE")
    except Exception as e:
        server.log.warning(f"Worker 退出 WAL 截断异常: {e}")
