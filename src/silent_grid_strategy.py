# -*- coding: utf-8 -*-
"""
静默Grid读取策略
通过Windows API直接读取CVirtualGridCtrl，完全不使用剪贴板
不抢焦点、不触发验证码弹窗、不干扰鼠标操作

优先级：
  1. SilentCopy — 通过LVM消息直接读取控件内容（零clipboard接触）
  2. SilentWMCopy — WMCopy + 静默验证码处理（备用方案）
"""

import ctypes
import ctypes.wintypes as wt
import io
import logging
import os
import time
from typing import Dict, List, Optional

import pandas as pd

from easytrader.grid_strategies import IGridStrategy
from easytrader.log import logger

# ===== Windows API 常量 =====
LVM_FIRST = 0x1000
LVM_GETITEMCOUNT = LVM_FIRST + 4       # 0x1004
LVM_GETITEMW = LVM_FIRST + 75          # 0x104B
LVM_GETHEADER = LVM_FIRST + 31         # 0x101F
LVIF_TEXT = 0x0001

HDM_FIRST = 0x1200
HDM_GETITEMCOUNT = HDM_FIRST + 0       # 0x1200
HDM_GETITEMW = HDM_FIRST + 11          # 0x120B
HDI_TEXT = 0x0002

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SETTEXT = 0x000C
BM_CLICK = 0x00F5
VK_F5 = 0x74
VK_RETURN = 0x0D


class LVITEMW(ctypes.Structure):
    """Win32 LISTVIEWITEM 结构体 (Unicode)"""
    _fields_ = [
        ("mask", wt.UINT),
        ("iItem", ctypes.c_int),
        ("iSubItem", ctypes.c_int),
        ("state", wt.UINT),
        ("stateMask", wt.UINT),
        ("pszText", ctypes.c_void_p),
        ("cchTextMax", ctypes.c_int),
        ("iImage", ctypes.c_int),
        ("lParam", wt.LPARAM),
    ]


class HDITEMW(ctypes.Structure):
    """Win32 HEADERITEM 结构体 (Unicode)"""
    _fields_ = [
        ("mask", wt.UINT),
        ("cxy", ctypes.c_int),
        ("pszText", ctypes.c_void_p),
        ("hbm", wt.HBITMAP),
        ("cchTextMax", ctypes.c_int),
        ("fmt", ctypes.c_int),
        ("lParam", wt.LPARAM),
    ]


class SilentCopy(IGridStrategy):
    """
    通过 Windows API 直接读取 CVirtualGridCtrl 内容
    完全不使用剪贴板、不改变窗口焦点、不触发验证码

    原理：
    - LVM_GETITEMCOUNT 获取行数
    - LVM_GETHEADER + HDM_GETITEMCOUNT 获取列数
    - HDM_GETITEMW 读取列名
    - LVM_GETITEMW 逐单元格读取数据
    所有操作通过 SendMessage 发送，不需要窗口在前台
    """

    def __init__(self):
        self._trader = None
        self._user32 = ctypes.windll.user32

    def set_trader(self, trader):
        self._trader = trader

    def get(self, control_id: int) -> List[Dict]:
        """读取Grid数据 — 零干扰"""
        grid = self._trader.main.child_window(
            control_id=control_id, class_name="CVirtualGridCtrl"
        )
        hwnd = grid.handle

        # 1. 获取行数
        row_count = self._user32.SendMessageW(hwnd, LVM_GETITEMCOUNT, 0, 0)
        if row_count <= 0:
            logger.info("SilentCopy: Grid为空 (row_count=0)")
            return []

        # 2. 获取表头控件句柄
        header_hwnd = self._user32.SendMessageW(hwnd, LVM_GETHEADER, 0, 0)
        if not header_hwnd:
            raise RuntimeError(
                "SilentCopy: 无法获取表头控件 — CVirtualGridCtrl可能不支持LVM消息"
            )

        # 3. 获取列数
        col_count = self._user32.SendMessageW(header_hwnd, HDM_GETITEMCOUNT, 0, 0)
        if col_count <= 0:
            raise RuntimeError("SilentCopy: 列数为0")

        # 4. 读取列名（表头文字）
        headers = []
        for c in range(col_count):
            h = self._read_header_item(header_hwnd, c)
            headers.append(h or f"col_{c}")
        logger.info(f"SilentCopy: 列名={headers}")

        # 5. 逐行逐列读取数据
        rows = []
        for r in range(row_count):
            row_data = {}
            for c in range(col_count):
                text = self._read_grid_cell(hwnd, r, c)
                if c < len(headers):
                    row_data[headers[c]] = text
            rows.append(row_data)

        logger.info(f"SilentCopy: 读取完成, {len(rows)}行 x {col_count}列")
        return rows

    def _read_header_item(self, header_hwnd: int, col: int) -> str:
        """通过 HDM_GETITEMW 读取表头列名"""
        buf = ctypes.create_unicode_buffer(256)
        item = HDITEMW()
        item.mask = HDI_TEXT
        item.pszText = ctypes.cast(buf, ctypes.c_void_p)
        item.cchTextMax = 256

        result = self._user32.SendMessageW(
            header_hwnd, HDM_GETITEMW, col, ctypes.byref(item)
        )
        return buf.value.strip() if result else ""

    def _read_grid_cell(self, grid_hwnd: int, row: int, col: int) -> str:
        """通过 LVM_GETITEMW 读取单元格文字"""
        buf = ctypes.create_unicode_buffer(1024)
        item = LVITEMW()
        item.mask = LVIF_TEXT
        item.iItem = row
        item.iSubItem = col
        item.pszText = ctypes.cast(buf, ctypes.c_void_p)
        item.cchTextMax = 1024

        self._user32.SendMessageW(grid_hwnd, LVM_GETITEMW, 0, ctypes.byref(item))
        return buf.value.strip()


class SilentWMCopy(IGridStrategy):
    """
    WMCopy + 静默验证码处理
    继承WMCopy的 post_message 读取方式，但验证码出现时：
    1. 截图验证码 (PrintWindow, 不抢焦点)
    2. OCR识别
    3. WM_SETTEXT 填入 (不抢焦点)
    4. BM_CLICK 确认 (不抢焦点)
    全程不调用 set_focus()、不使用键盘模拟
    """

    def __init__(self):
        self._trader = None
        self._user32 = ctypes.windll.user32
        self._format_grid_data = self._default_format

    def set_trader(self, trader):
        self._trader = trader

    def get(self, control_id: int) -> List[Dict]:
        from easytrader.utils.win_gui import win32defines

        grid = self._trader.main.child_window(
            control_id=control_id, class_name="CVirtualGridCtrl"
        )
        # 发送 WM_COMMAND 触发 Edit > Copy (不抢焦点)
        grid.post_message(win32defines.WM_COMMAND, 0xE122, 0)
        self._trader.wait(0.1)

        # 静默读取clipboard数据
        content = self._get_clipboard_data_silent()
        return self._format_grid_data(content)

    def _get_clipboard_data_silent(self) -> str:
        """
        静默版clipboard读取
        检测到验证码时通过Windows消息处理，不抢焦点
        """
        import pywinauto.clipboard
        from easytrader import grid_strategies as gs

        # 检查是否需要处理验证码
        if gs.Copy._need_captcha_reg:
            try:
                top_win = self._trader.app.top_window()
                if top_win.window(
                    class_name="Static", title_re="验证码"
                ).exists(timeout=1):
                    self._handle_captcha_silent()
            except Exception:
                pass

        # 读取clipboard
        count = 5
        while count > 0:
            try:
                return pywinauto.clipboard.GetData()
            except Exception:
                count -= 1
                self._trader.wait(0.1)
        return ""

    def _handle_captcha_silent(self):
        """
        静默处理验证码 — 全程不抢焦点
        1. capture_as_image 截图 (用PrintWindow, 无需前台)
        2. OCR识别验证码数字
        3. WM_SETTEXT 填入验证码 (SendMessage, 无需前台)
        4. BM_CLICK 或 SendMessage+ENTER 确认
        """
        from easytrader.utils.captcha import captcha_recognize
        from easytrader import grid_strategies as gs

        file_path = os.path.join(os.environ.get("TEMP", "."), "ths_captcha.png")
        top_win = self._trader.app.top_window()

        for attempt in range(5):
            try:
                # 1. 截图验证码图片
                captcha_ctrl = top_win.window(
                    control_id=0x965, class_name="Static"
                )
                captcha_img = captcha_ctrl.capture_as_image()
                captcha_img.save(file_path)

                # 2. OCR识别
                captcha_num = captcha_recognize(file_path).strip()
                captcha_num = "".join(captcha_num.split())
                logger.info(f"[静默验证码] OCR结果: {captcha_num}")

                if len(captcha_num) != 4:
                    logger.warning(f"[静默验证码] OCR结果不是4位数: {captcha_num}")
                    # 点击验证码图片刷新
                    try:
                        captcha_ctrl.click_input()  # 这个可能抢焦点，但仅在验证码区域
                    except:
                        pass
                    self._trader.wait(0.3)
                    continue

                # 3. 通过 WM_SETTEXT 填入验证码 (不需要窗口在前台)
                edit_hwnd = top_win.window(
                    control_id=0x964, class_name="Edit"
                ).handle
                self._user32.SendMessageW(
                    edit_hwnd, WM_SETTEXT, 0,
                    ctypes.c_wchar_p(captcha_num)
                )

                # 4. 确认 — 通过 SendMessage 发送 Enter 键
                self._user32.PostMessageW(top_win.handle, WM_KEYDOWN, VK_RETURN, 0)
                self._user32.PostMessageW(top_win.handle, WM_KEYUP, VK_RETURN, 0)

                self._trader.wait(0.5)

                # 5. 检查验证码对话框是否消失
                try:
                    still_exists = top_win.window(
                        class_name="Static", title_re="验证码"
                    ).exists(timeout=0.5)
                    if not still_exists:
                        gs.Copy._need_captcha_reg = False
                        logger.info("[静默验证码] ✅ 验证码自动解决!")
                        # 清理临时文件
                        try:
                            os.remove(file_path)
                        except:
                            pass
                        return
                except Exception:
                    # 对话框消失，验证码已解决
                    gs.Copy._need_captcha_reg = False
                    logger.info("[静默验证码] ✅ 验证码自动解决!")
                    return

            except Exception as e:
                logger.warning(f"[静默验证码] 第{attempt+1}次尝试失败: {e}")
                self._trader.wait(0.3)

        # 5次都失败，点击取消
        try:
            cancel_btn = top_win.Button2
            self._user32.SendMessageW(cancel_btn.handle, BM_CLICK, 0, 0)
        except:
            pass
        gs.Copy._need_captcha_reg = True
        logger.warning("[静默验证码] ❌ 验证码识别失败，已取消")

    def _default_format(self, data: str) -> List[Dict]:
        """格式化clipboard的TSV数据"""
        try:
            df = pd.read_csv(
                io.StringIO(data),
                delimiter="\t",
                dtype=self._trader.config.GRID_DTYPE,
                na_filter=False,
            )
            return df.to_dict("records")
        except Exception:
            from easytrader import grid_strategies as gs
            gs.Copy._need_captcha_reg = True
            return []


class UltraSilentCopy(IGridStrategy):
    """
    终极静默策略 — 双保险
    优先级：
    1. SilentCopy (LVM消息直接读取, 完全不碰clipboard)
    2. SilentWMCopy (WMCopy + 静默验证码, 仅在LVM失败时使用)

    这是最推荐的策略：零干扰、零弹窗、零鼠标占用
    """

    def __init__(self):
        self._trader = None
        self._lvm = SilentCopy()
        self._wmcopy = SilentWMCopy()

    def set_trader(self, trader):
        self._trader = trader
        self._lvm.set_trader(trader)
        self._wmcopy.set_trader(trader)

    def get(self, control_id: int) -> List[Dict]:
        # 优先尝试 LVM 直接读取（完全零干扰）
        try:
            result = self._lvm.get(control_id)
            if result:
                return result
            logger.info("UltraSilent: LVM读取返回空，尝试WMCopy")
        except Exception as e:
            logger.warning(f"UltraSilent: LVM读取失败({e})，回退到WMCopy")

        # 回退到 WMCopy + 静默验证码
        return self._wmcopy.get(control_id)
