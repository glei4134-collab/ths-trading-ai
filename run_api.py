"""
唯一本地启动脚本。生产请用 gunicorn api:app。
不要再运行 api.py（已冻结为历史单体，避免与 api 包撞名）。
"""

import logging
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
)

from api import create_app  # noqa: E402

app = create_app()


def main():
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    print("=" * 50)
    print("THS Trading AI  —  python run_api.py")
    print(f"http://127.0.0.1:{port}")
    print("鉴权: config/api_token.json 或环境变量 API_TOKEN")
    print("关闭鉴权(仅调试): ALLOW_INSECURE=1")
    print("=" * 50)
    app.run(debug=debug, host="0.0.0.0", port=port, use_reloader=False)


if __name__ == "__main__":
    main()
