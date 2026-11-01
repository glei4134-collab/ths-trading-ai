#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
THS Trading AI - 核心 Web API 兼容服务入口
转发至模块化蓝图包 api/ (create_app)，兼容现有 systemd、Gunicorn 及本地脚本
"""

import sys
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"

for p in (str(ROOT_DIR), str(SRC_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from api import app, create_app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    print("=" * 50)
    print("THS Trading AI Web Service  —  python api.py")
    print(f"http://127.0.0.1:{port}")
    print("=" * 50)
    app.run(debug=debug, host="0.0.0.0", port=port, use_reloader=False)
