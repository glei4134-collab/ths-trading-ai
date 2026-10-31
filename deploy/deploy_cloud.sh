#!/usr/bin/env bash
# ==============================================================================
# THS-Trading-AI 云端自动化部署与热更新脚本 (针对 81.70.51.173)
# 支持交易时段发布拦截熔断门禁：工作日 09:15-11:30, 13:00-15:05 自动挂起至 15:05
# ==============================================================================
set -euo pipefail

APP_DIR="/opt/ths-trading-ai"
SERVICE_NAME="ths-trading.service"
DATA_DIR="${APP_DIR}/data"

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

echo "📦 更新依赖..."
pip install --upgrade pip
pip install -r requirements.txt

# 4. 复制 systemd 单元配置
if [ -f "deploy/ths-trading.service" ]; then
    echo "⚙️ 更新 systemd 服务配置..."
    cp deploy/ths-trading.service /etc/systemd/system/${SERVICE_NAME}
    systemctl daemon-reload
fi

# 5. 执行上线前全链路体检与 WAL 维护
echo "🩺 正在运行上线前量化全链路自检探针..."
python tools/auto_debugger.py --check-all --auto-fix

# 6. 重启服务
echo "🔄 重启量化交易 systemd 服务: ${SERVICE_NAME}..."
systemctl restart ${SERVICE_NAME}
systemctl enable ${SERVICE_NAME}

# 7. 等待并验证健康检查
sleep 3
if systemctl is-active --quiet ${SERVICE_NAME}; then
    echo "✅ 服务已成功启动并在后台运行！"
    echo "🌐 API 监听状态:"
    ss -tulpn | grep 5000 || true
    echo "📋 最近日志:"
    journalctl -u ${SERVICE_NAME} -n 15 --no-pager
else
    echo "❌ 服务启动异常，查看日志:"
    journalctl -u ${SERVICE_NAME} -n 30 --no-pager
    exit 1
fi

echo "=========================================================="
echo "🎉 云端部署与自检全部顺利完成！"
echo "=========================================================="
