#!/usr/bin/env bash
# ==============================================================================
# THS-Trading-AI 云端自动化部署与热更新脚本 (针对 81.70.51.173)
# 支持生产级 Gunicorn 零闪断热重载 (kill -HUP) 与交易时段发布防护门禁
# 包含“更新前自动备份 static.bak_last_good + 验收测试未通过自动回滚”熔断保险
# ==============================================================================
set -euo pipefail

APP_DIR="/opt/ths-trading-ai"
SERVICE_NAME="ths-trading.service"
DATA_DIR="${APP_DIR}/data"
BAK_STATIC_DIR="${APP_DIR}/static.bak_last_good"

echo "=========================================================="
echo "🚀 开始 THS-Trading-AI 云端自动化发布与健康自愈流程..."
echo "=========================================================="

# 1. 确保目录与权限
mkdir -p "${DATA_DIR}" "${APP_DIR}/config"
cd "${APP_DIR}"

# 2. 检查 Python 虚拟环境与依赖
if [ ! -d "venv" ]; then
    echo "📦 创建 Python 3 虚拟环境..."
    python3 -m venv venv
fi
source venv/bin/activate

# 3. 交易时段发布防护门禁检测 (A股交易时段严禁停机重启)
echo "🛡️ 执行交易时段发布熔断门禁检测..."
python deploy/release_gatekeeper.py --action wait "$@"

# 4. 更新前自动备份静态资源安全快照 (static.bak_last_good)
if [ -d "${APP_DIR}/static" ]; then
    echo "💾 正在自动创建最新静态资源安全快照: static.bak_last_good..."
    rm -rf "${BAK_STATIC_DIR}"
    cp -r "${APP_DIR}/static" "${BAK_STATIC_DIR}"
    echo "✅ static.bak_last_good 安全基线快照已就绪"
fi

echo "📦 更新依赖..."
pip install --upgrade pip
pip install -r requirements.txt

# 5. 复制 systemd 单元配置
if [ -f "deploy/ths-trading.service" ]; then
    echo "⚙️ 更新 systemd 服务配置..."
    cp deploy/ths-trading.service /etc/systemd/system/${SERVICE_NAME}
    systemctl daemon-reload
fi

# 6. 执行上线前全链路体检与 WAL 维护
echo "🩺 正在运行上线前量化全链路自检探针..."
python tools/auto_debugger.py --check-all --auto-fix

# 7. 平滑热重载或拉起服务 (基于 Gunicorn kill -HUP 的零闪断热重载)
if systemctl is-active --quiet ${SERVICE_NAME}; then
    echo "🔄 服务运行中，执行 Gunicorn 零闪断平滑热更 (kill -HUP)..."
    systemctl reload ${SERVICE_NAME} || systemctl restart ${SERVICE_NAME}
else
    echo "🚀 首次拉起量化交易 systemd 服务: ${SERVICE_NAME}..."
    systemctl restart ${SERVICE_NAME}
    systemctl enable ${SERVICE_NAME}
fi

# 8. 等待并验证进程存活状态
sleep 3
if systemctl is-active --quiet ${SERVICE_NAME}; then
    echo "✅ 服务已成功拉起运行！"
    echo "🌐 API 监听状态:"
    ss -tulpn | grep 5000 || true
    echo "📋 最近日志:"
    journalctl -u ${SERVICE_NAME} -n 15 --no-pager
else
    echo "❌ 服务启动异常，查看日志:"
    journalctl -u ${SERVICE_NAME} -n 30 --no-pager
    exit 1
fi

# ==================== 自动化验收门禁与自动回滚熔断逻辑 ====================

rollback_and_abort() {
    local err_msg="$1"
    echo ""
    echo "=========================================================="
    echo "🚨 [DEPLOYMENT GATE REJECTED] 部署验收门禁未通过: ${err_msg}"
    echo "🔄 正在自动回滚至上一个已验证的完好静态资源快照 (static.bak_last_good)..."
    echo "=========================================================="
    if [ -d "${BAK_STATIC_DIR}" ]; then
        rm -rf "${APP_DIR}/static"
        cp -r "${BAK_STATIC_DIR}" "${APP_DIR}/static"
        echo "✅ 静态资源已回滚还原至 static.bak_last_good"
    fi
    echo "🔄 正在平滑重载服务以恢复历史稳定版本..."
    systemctl reload ${SERVICE_NAME} || systemctl restart ${SERVICE_NAME}
    echo "❌ 部署已安全熔断终止并回滚完毕，未破坏线上运行状态。"
    exit 1
}

# 9. 强制执行自动化前端集成与生产页面交付验收测试 (37项全量断言)
echo "🔍 正在执行自动化前端渲染与交付验收测试 (Frontend E2E Gatekeeper)..."
if ! python tools/verify_frontend_cockpit.py http://127.0.0.1:5000; then
    rollback_and_abort "前端 37 项 E2E 验收测试未完全通过！可能存在白屏、卡加载态或核心 DOM 缺失风险"
fi
echo "✅ 前端 37 项验收测试 100% 通过，页面渲染数据完备！"

# 10. 验证后端静默自愈守护与数据库状态
echo "🔍 正在执行后端健康接口验收 (/api/system/health/silent)..."
if ! curl -sf http://127.0.0.1:5000/api/system/health/silent > /dev/null; then
    rollback_and_abort "后端静默健康守护接口未能正常响应 HTTP 200！"
fi
echo "✅ 后端静默自愈健康守护探针与数据接口均响应正常！"

echo "=========================================================="
echo "🎉 云端部署、零闪断平滑热更与全套自动化验收 100% 顺利完成！"
echo "=========================================================="
