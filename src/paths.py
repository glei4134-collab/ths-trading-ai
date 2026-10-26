"""
统一基础路径解析
PyInstaller exe: 数据/配置在 exe 旁边
源码运行: 在项目目录
"""
import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent.parent

DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
