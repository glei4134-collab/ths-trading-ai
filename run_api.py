"""
唯一本地启动脚本。生产请用 gunicorn api:app。
不要再运行 api.py（已冻结为历史单体，避免与 api 包撞名）。
"""

import logging
from logging.handlers import TimedRotatingFileHandler
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        TimedRotatingFileHandler(ROOT / "trading.log", when="midnight", backupCount=14, encoding="utf-8"),
    ],
)

from api import create_app  # noqa: E402

app = create_app()


def main():
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    host = os.environ.get("HOST", "127.0.0.1")
    from api.context import API_TOKEN, _ALLOW_INSECURE
    print("=" * 60)
    print("THS Trading AI  —  python run_api.py")
    print(f"服务地址: http://{host}:{port}")
    if _ALLOW_INSECURE:
        print("鉴权状态: [免鉴权模式] ALLOW_INSECURE=1 (仅限开发调试)")
    else:
        print(f"访问令牌: {API_TOKEN}")
        print("提示: 令牌已保存至 config/api_token.json，请通过 Authorization: Bearer <token> 头传递，勿在 URL 中传参")
        print("免密调试: set ALLOW_INSECURE=1")
    print("=" * 60)
    app.run(debug=debug, host=host, port=port, use_reloader=False)


if __name__ == "__main__":
    main()
