"""
THS Trading AI - 打包脚本
使用 PyInstaller 打包为桌面应用
"""

import os
import sys
import shutil
from pathlib import Path

print("=" * 60)
print("THS Trading AI - 打包工具")
print("=" * 60)

# 检查 PyInstaller
try:
    import PyInstaller
    print(f"[OK] PyInstaller {PyInstaller.__version__}")
except ImportError:
    print("[INSTALL] 正在安装 PyInstaller...")
    os.system("pip install pyinstaller")
    import PyInstaller

# 清理旧构建
print("\n[1/4] 清理旧构建...")
for folder in ['build', 'dist']:
    if os.path.exists(folder):
        shutil.rmtree(folder)
        print(f"  删除 {folder}")

# 打包
print("\n[2/4] 开始打包...")
cmd = [
    "pyinstaller",
    "--name=THS_Trading_AI",
    "--onedir",  # 单文件夹模式（启动更快）
    "--noconsole",  # 不显示控制台
    "--icon=static/mobile/icon-512.ico",
    "--add-data=static;static",
    "--add-data=config;config",
    "--add-data=src;src",
    "--hidden-import=flask",
    "--hidden-import=flask_cors",
    "--hidden-import=pystray",
    "--hidden-import=PIL",
    "--hidden-import=requests",
    "--hidden-import=pandas",
    "--hidden-import=numpy",
    "--hidden-import=akshare",
    "desktop_app.py"
]

result = os.system(" ".join(cmd))

if result == 0:
    print("\n[3/4] 打包成功!")

    # 复制配置文件
    print("\n[4/4] 复制配置文件...")
    dist_dir = Path("dist/THS_Trading_AI")

    # 确保配置目录存在
    (dist_dir / "config").mkdir(exist_ok=True)
    (dist_dir / "data").mkdir(exist_ok=True)

    # 复制默认配置（如果不存在）
    for config_file in ['competition_rules.json', 'ai_config.json']:
        src = Path(f"config/{config_file}")
        dst = dist_dir / "config" / config_file
        if src.exists() and not dst.exists():
            shutil.copy(src, dst)
            print(f"  复制 {config_file}")

    print("\n" + "=" * 60)
    print("打包完成!")
    print(f"输出目录: {dist_dir}")
    print(f"运行程序: {dist_dir / 'THS_Trading_AI.exe'}")
    print("=" * 60)
else:
    print("\n[ERROR] 打包失败!")
    print("请检查错误信息")
