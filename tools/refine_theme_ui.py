#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
精细化升级前端量化交易座舱 UI 风格：
彻底清除残留的暗色渐变、霓虹红黄配饰、错位截断和白字隐形 Bug，
打造极致统一、高对比度、呼吸感一流的现代机构级量化终端界面。
"""

import re
from pathlib import Path

html_path = Path(__file__).resolve().parent.parent / "static" / "index.html"
content = html_path.read_text(encoding="utf-8")

# 1. 规范化换行符为 \n 便于精确替换
is_crlf = "\r\n" in content
content = content.replace("\r\n", "\n")

# 2. 升级主题变量 :root, [data-theme="slate"]
old_slate_pattern = r':root,\s*\[data-theme="slate"\]\s*\{[^}]+\}'
new_slate = """:root, [data-theme="slate"] {
            --bg-base: #f1f5f9;         /* 舒适低眩光基底 Slate-100 */
            --bg-surface: #ffffff;      /* 纯净清爽卡片底色 */
            --bg-card: #ffffff;         /* 面板底色 */
            --bg-card-hover: #f8fafc;   /* 悬浮微深白 */
            --bg-glass: rgba(255, 255, 255, 0.96);
            --bg-inset: #f8fafc;        /* 内嵌区域（日志、表头、代码） */
            --border-subtle: #f1f5f9;   /* 极细边框 */
            --border-default: #e2e8f0;  /* 清晰边界 Slate-200 */
            --border-glow: 0 1px 3px 0 rgba(0, 0, 0, 0.05);
            --border-focus: #2563eb;
            --text-primary: #0f172a;    /* 极高清晰度炭黑 Slate-900 */
            --text-secondary: #475569;  /* 舒适次级字 Slate-600 */
            --text-muted: #94a3b8;      /* 辅助文本 Slate-400 */
            --up-red: #dc2626;
            --up-red-bg: #fee2e2;
            --up-red-border: #fca5a5;
            --down-green: #16a34a;
            --down-green-bg: #f0fdf4;
            --down-green-border: #86efac;
            --brand-blue: #2563eb;
            --brand-blue-bg: #eff6ff;
            --brand-red: #dc2626;
            --brand-yellow: #b45309;
            --warning-amber: #b45309;
            --warning-amber-bg: #fef3c7;
            --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            --font-mono: 'JetBrains Mono', Consolas, "SF Mono", monospace;
            --radius-sm: 6px;
            --radius-md: 8px;
            --radius-lg: 12px;
        }"""
content, count = re.subn(old_slate_pattern, new_slate, content, count=1)
print(f"Update slate theme: {count}")

# 3. 升级 container 三列栅格比例 (左310px 中1fr 右380px)
old_container = """.container {
            display: grid;
            grid-template-columns: 320px 1fr 360px;
            gap: 12px;
            padding: 12px 16px;
            height: calc(100vh - 85px);
            background: var(--bg-base);
        }"""
new_container = """.container {
            display: grid;
            grid-template-columns: 310px 1fr 380px;
            gap: 12px;
            padding: 12px 16px;
            height: calc(100vh - 85px);
            background: var(--bg-base);
        }"""
if old_container in content:
    content = content.replace(old_container, new_container)
    print("Update container columns: OK")
else:
    print("Warning: old_container not found verbatim")

# 4. 彻底剔除 .panel 和 .panel-header 中的深色背景渐变
old_panel_block = """.panel {
            background: linear-gradient(180deg, rgba(15, 22, 37, 0.88) 0%, rgba(10, 15, 26, 0.88) 100%);
            backdrop-filter: blur(14px);
            border-radius: var(--radius-md);
            border: 1px solid var(--border-default);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.04);
            overflow: hidden;
            display: flex;
            flex-direction: column;
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
        }

        .panel-header {
            background: linear-gradient(90deg, rgba(22, 33, 54, 0.75) 0%, rgba(14, 21, 36, 0.5) 100%);
            backdrop-filter: blur(10px);
            padding: 11px 16px;
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 0.3px;
            border-bottom: 1px solid var(--border-default);
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: relative;
        }
        .panel-header::before {
            content: '';
            position: absolute;
            left: 0;
            top: 22%;
            bottom: 22%;
            width: 3px;
            background: linear-gradient(180deg, #ffe600 0%, #3b82f6 100%);
            border-radius: 0 2px 2px 0;
            box-shadow: 0 0 8px rgba(0, 210, 255, 0.6);
        }"""

new_panel_block = """.panel {
            background: var(--bg-surface);
            border-radius: var(--radius-md);
            border: 1px solid var(--border-default);
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
            overflow: hidden;
            display: flex;
            flex-direction: column;
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
        }

        .panel-header {
            background: var(--bg-surface) !important;
            padding: 10px 16px;
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 0.2px;
            color: var(--text-primary) !important;
            border-bottom: 1px solid var(--border-default) !important;
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: relative;
        }
        .panel-header::before {
            content: '';
            position: absolute;
            left: 0;
            top: 25%;
            bottom: 25%;
            width: 3px;
            background: var(--brand-blue);
            border-radius: 0 2px 2px 0;
        }"""
if old_panel_block in content:
    content = content.replace(old_panel_block, new_panel_block)
    print("Update panel & panel-header: OK")
else:
    print("Warning: old_panel_block not found verbatim")

# 5. 替换全局滚动条底槽为 var(--bg-inset) 消除漆黑硬道
content = re.sub(
    r'::-webkit-scrollbar-track\s*\{\s*background:\s*#0d1117;\s*\}',
    '::-webkit-scrollbar-track { background: var(--bg-inset); }',
    content
)

# 6. 替换 #globalStatusHud 的漆黑覆盖
old_hud_css = """        /* 🌌 顶部科技 HUD 呼吸脉冲与高斯流光 */
        #globalStatusHud {
            background: rgba(8, 12, 22, 0.88) !important;
            backdrop-filter: blur(18px) !important;
            -webkit-backdrop-filter: blur(18px) !important;
            border-bottom: 1px solid rgba(255, 24, 36, 0.22) !important;
            box-shadow: 0 4px 24px rgba(0, 0, 0, 0.45) !important;
        }"""
new_hud_css = """        /* 🏛️ 统一全局态势 HUD 顶栏 */
        #globalStatusHud {
            background: var(--bg-surface) !important;
            border-bottom: 1px solid var(--border-default) !important;
            box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03) !important;
            color: var(--text-primary) !important;
        }"""
if old_hud_css in content:
    content = content.replace(old_hud_css, new_hud_css)
    print("Update #globalStatusHud CSS: OK")
else:
    print("Warning: old_hud_css not found verbatim")

# 7. 替换表格悬浮变红为典雅淡灰
old_hover_tr = """        table tbody tr:hover {
            background: rgba(255, 24, 36, 0.08) !important;
            box-shadow: inset 0 0 16px rgba(255, 24, 36, 0.12) !important;
        }"""
new_hover_tr = """        table tbody tr:hover {
            background: var(--bg-card-hover) !important;
            box-shadow: none !important;
        }"""
if old_hover_tr in content:
    content = content.replace(old_hover_tr, new_hover_tr)
    print("Update table hover CSS: OK")
else:
    print("Warning: old_hover_tr not found verbatim")

# 8. 替换候选池表格滚动槽暗色
content = content.replace("scrollbar-color: #25334a #090e18;", "scrollbar-color: var(--border-default) var(--bg-inset);")

# 9. 升级顶部 Logo 和状态指示条 (移除 P5 和 ROYAL 歪斜红黄贴纸，升级为现代金融机构标志)
old_header_brand = """        <div style="display:flex;align-items:center;gap:12px;">
            <div class="logo" style="font-size:15px;font-weight:900;display:flex;align-items:center;gap:6px;cursor:default;">
                <span class="p5-tag" style="font-size:12px;padding:2px 8px;transform:skewX(-8deg);box-shadow:2px 2px 0 #ffe600;background:#ff1824;color:#fff;font-weight:900;">P5</span>
                <span style="color:#0f172a;font-weight:900;letter-spacing:0.8px;transform:skewX(-4deg);">THS QUANT</span>
                <span class="p5-tag-yellow" style="font-size:10px;padding:1px 6px;transform:skewX(-8deg);background:#ffe600;color:#000;font-weight:900;">★ ROYAL</span>
            </div>
            <!-- 量化全自动座舱主控标识 -->
            <div id="viewSwitcherContainer" style="display:flex;align-items:center;background:var(--bg-inset);border:1px solid var(--border-default);border-radius:var(--radius-sm);padding:3px;">
                <span id="viewBtnCockpit" style="background:#ff1824;color:#ffffff;border-radius:4px;padding:4px 12px;font-size:12px;font-weight:900;transform:skewX(-4deg);box-shadow:2px 2px 0 #ffe600;">📊 量化全自动座舱</span>
            </div>
            <div id="connStatusHeader" style="display:flex;align-items:center;gap:6px;font-size:12px;color:#8b949e;">
                <span id="connDotHeader" style="width:7px;height:7px;border-radius:50%;background:#666;"></span>
                <span id="connTextHeader">未连接</span>
            </div>
            <div id="autoTradeHeader" style="display:flex;align-items:center;gap:6px;font-size:12px;color:#8b949e;">
                <span id="autoDotHeader" style="width:7px;height:7px;border-radius:50%;background:#666;"></span>
                <span id="autoTextHeader">已停止</span>
            </div>
        </div>"""

new_header_brand = """        <div style="display:flex;align-items:center;gap:12px;">
            <div class="logo" style="font-size:14px;font-weight:800;display:flex;align-items:center;gap:8px;cursor:default;">
                <span style="background:linear-gradient(135deg, #1e40af 0%, #2563eb 100%);color:#ffffff;font-size:11px;font-weight:900;padding:2px 7px;border-radius:4px;letter-spacing:0.5px;">THS</span>
                <span style="color:var(--text-primary);font-weight:800;letter-spacing:0.5px;font-size:14px;">QUANT AI</span>
                <span style="font-size:10px;padding:1px 6px;border-radius:3px;background:var(--bg-inset);color:var(--text-secondary);border:1px solid var(--border-default);font-weight:700;">PRO</span>
            </div>
            <!-- 量化全自动座舱主控标识 -->
            <div id="viewSwitcherContainer" style="display:flex;align-items:center;">
                <span id="viewBtnCockpit" style="background:#eff6ff;border:1px solid #bfdbfe;color:#1d4ed8;border-radius:6px;padding:3px 10px;font-size:11px;font-weight:700;">⚡ 实盘自动化座舱</span>
            </div>
            <div id="connStatusHeader" style="display:flex;align-items:center;gap:5px;font-size:11px;background:#f0fdf4;border:1px solid #bbf7d0;color:#15803d;padding:2px 8px;border-radius:12px;font-weight:600;">
                <span id="connDotHeader" style="width:6px;height:6px;border-radius:50%;background:#16a34a;"></span>
                <span id="connTextHeader">已连接</span>
            </div>
            <div id="autoTradeHeader" style="display:flex;align-items:center;gap:5px;font-size:11px;background:#eff6ff;border:1px solid #bfdbfe;color:#1d4ed8;padding:2px 8px;border-radius:12px;font-weight:600;">
                <span id="autoDotHeader" style="width:6px;height:6px;border-radius:50%;background:#2563eb;"></span>
                <span id="autoTextHeader">自律调度中</span>
            </div>
        </div>"""
if old_header_brand in content:
    content = content.replace(old_header_brand, new_header_brand)
    print("Update header branding: OK")
else:
    print("Warning: old_header_brand not found verbatim")

# 10. 升级账号切换下拉触发按钮样式（从暗黑杂色改为统一淡色卡片）
old_acc_sel = """                <div class="account-selector" onclick="toggleAccountPanel(event)" title="点击管理与切换账号" style="display:flex;align-items:center;gap:6px;cursor:pointer;padding:4px 10px;background:rgba(22, 27, 34, 0.95);border:1px solid rgba(255, 24, 36, 0.25);border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.3);transition:all 0.2s;">
                    <div class="account-avatar" id="accountAvatar" style="width:22px;height:22px;font-size:10px;">👤</div>
                    <span id="accountDetailName" style="font-size:11px;color:#0f172a;font-weight:600;">多账号管理</span>
                    <span style="font-size:10px;color:#ff1824;">▼</span>
                </div>"""
new_acc_sel = """                <div class="account-selector" onclick="toggleAccountPanel(event)" title="点击管理与切换账号" style="display:flex;align-items:center;gap:6px;cursor:pointer;padding:4px 10px;background:var(--bg-inset);border:1px solid var(--border-default);border-radius:6px;box-shadow:0 1px 2px rgba(0,0,0,0.04);transition:all 0.2s;">
                    <div class="account-avatar" id="accountAvatar" style="width:20px;height:20px;font-size:10px;">👤</div>
                    <span id="accountDetailName" style="font-size:11px;color:var(--text-primary);font-weight:600;">多账号管理</span>
                    <span style="font-size:10px;color:var(--text-muted);">▼</span>
                </div>"""
if old_acc_sel in content:
    content = content.replace(old_acc_sel, new_acc_sel)
    print("Update account selector: OK")

# 11. 升级 HUD 内部的时钟、罗盘、SSE 和 RTT 胶囊样式
old_hud_inner = """        <!-- Left: 时钟与窗口时序带 -->
        <div style="display:flex;align-items:center;gap:12px;white-space:nowrap;">
            <div style="display:flex;align-items:center;gap:8px;background:var(--bg-card);padding:4px 12px;border-radius:var(--radius-sm);border:1px solid var(--border-default);">
                <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#3fb950;box-shadow:0 0 10px #3fb950;" id="hudClockDot"></span>
                <span style="font-family:var(--font-mono);font-size:13px;font-weight:700;color:var(--brand-blue);letter-spacing:0.5px;" id="hudClockTime">--:--:--</span>
                <span id="hudMockTag" style="display:none;background:#d29922;color:#000;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:700;">虚拟时钟</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;">
                <span class="hud-window-label" style="color:var(--text-secondary);font-size:12px;font-weight:500;">时序窗口:</span>
                <span id="hudCurrentWindow" style="background:var(--brand-blue-bg);color:var(--brand-blue);border:1px solid rgba(255, 24, 36,0.4);border-radius:var(--radius-sm);padding:3px 10px;font-size:12px;font-weight:600;">盘前扫描</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;color:var(--text-secondary);font-size:12px;">
                <span class="hud-next-label">⏱️ 下一时序:</span>
                <span id="hudNextCountdown" style="color:var(--warning-amber);font-family:var(--font-mono);font-size:12px;font-weight:700;">计算中...</span>
            </div>
        </div>

        <!-- Center: 团队赛风控态势胶囊 -->
        <div style="display:flex;align-items:center;gap:12px;white-space:nowrap;">
            <div style="display:flex;align-items:center;gap:6px;background:var(--bg-card);padding:4px 12px;border-radius:var(--radius-sm);border:1px solid var(--border-default);">
                <span style="color:var(--text-secondary);font-size:12px;">单票最高仓位:</span>
                <span id="hudMaxPosition" style="font-weight:700;font-size:13px;color:#16a34a;font-family:var(--font-mono);">0.0%</span>
                <span style="color:var(--text-muted);font-size:11px;">(硬限 &le;35%)</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;background:var(--bg-card);padding:4px 12px;border-radius:var(--radius-sm);border:1px solid var(--border-default);">
                <span style="color:var(--text-secondary);font-size:12px;">持仓分散度:</span>
                <span id="hudHoldingCount" style="font-weight:700;font-size:13px;color:var(--brand-blue);font-family:var(--font-mono);">0 只</span>
                <span style="color:var(--text-muted);font-size:11px;">(规则 &ge;3只)</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;background:var(--bg-card);padding:4px 12px;border-radius:var(--radius-sm);border:1px solid var(--border-default);">
                <span style="color:var(--text-secondary);font-size:12px;">团队最大回撤:</span>
                <span id="hudMaxDrawdown" style="font-weight:700;font-size:13px;color:#16a34a;font-family:var(--font-mono);">0.0%</span>
                <span style="color:var(--text-muted);font-size:11px;">(警戒 12%)</span>
            </div>
            <div id="hudMacroCompass" style="display:flex;align-items:center;gap:6px;background:var(--bg-card);padding:4px 12px;border-radius:var(--radius-sm);border:1px solid rgba(99,102,241,0.4);box-shadow:0 0 10px rgba(99,102,241,0.15);" title="四象限宏观态势与隐马尔可夫时态罗盘">
                <span style="font-size:12px;">🧭</span>
                <span style="color:var(--text-secondary);font-size:12px;">宏观罗盘:</span>
                <span id="hudMacroRegimeText" style="font-weight:700;font-size:12px;color:#818cf8;font-family:var(--font-mono);">牛市低波</span>
                <span id="hudMacroConfText" style="font-size:10px;color:#a5b4fc;background:rgba(99,102,241,0.2);padding:1px 5px;border-radius:3px;font-family:var(--font-mono);">75%</span>
            </div>
        </div>

        <!-- Right: 系统体检胶囊 & 桌面端标识 -->
        <div style="display:flex;align-items:center;gap:12px;white-space:nowrap;">
            <div style="display:flex;align-items:center;gap:6px;background:var(--bg-card);padding:4px 10px;border-radius:var(--radius-sm);border:1px solid rgba(255, 24, 36,0.35);" id="hudSsePill" title="驾驶舱毫秒级 SSE 流式总线">
                <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#00e676;box-shadow:0 0 8px #00e676;animation:pulseDot 2s infinite;" id="hudSseDot"></span>
                <span style="color:#0f172a;font-size:11px;font-weight:700;" id="hudSseText">⚡ SSE 实时推流</span>
            </div>
            <div onclick="openPerformanceModal()" style="cursor:pointer;display:flex;align-items:center;gap:6px;background:var(--bg-card);padding:4px 12px;border-radius:var(--radius-sm);border:1px solid rgba(255, 24, 36,0.4);transition:all 0.2s;" title="点击打开同花顺API网络延迟与链路健康监控">
                <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#00e676;box-shadow:0 0 8px #00e676;" id="hudRttDot"></span>
                <span style="color:var(--text-secondary);font-size:11px;">网关 RTT:</span>
                <span style="color:#16a34a;font-weight:700;font-size:12px;font-family:var(--font-mono);" id="hudRttText">-- ms</span>
            </div>
        </div>"""

new_hud_inner = """        <!-- Left: 时钟与窗口时序带 -->
        <div style="display:flex;align-items:center;gap:12px;white-space:nowrap;">
            <div style="display:flex;align-items:center;gap:8px;background:var(--bg-inset);padding:4px 12px;border-radius:var(--radius-sm);border:1px solid var(--border-default);">
                <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#16a34a;box-shadow:0 0 6px #86efac;" id="hudClockDot"></span>
                <span style="font-family:var(--font-mono);font-size:13px;font-weight:800;color:var(--brand-blue);letter-spacing:0.5px;" id="hudClockTime">--:--:--</span>
                <span id="hudMockTag" style="display:none;background:#fef3c7;color:#b45309;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:700;">虚拟时钟</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;">
                <span class="hud-window-label" style="color:var(--text-secondary);font-size:12px;font-weight:600;">时序窗口:</span>
                <span id="hudCurrentWindow" style="background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;border-radius:var(--radius-sm);padding:3px 10px;font-size:12px;font-weight:700;">盘前扫描</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;color:var(--text-secondary);font-size:12px;">
                <span class="hud-next-label">⏱️ 下一时序:</span>
                <span id="hudNextCountdown" style="color:var(--warning-amber);font-family:var(--font-mono);font-size:12px;font-weight:700;">计算中...</span>
            </div>
        </div>

        <!-- Center: 团队赛风控态势胶囊 -->
        <div style="display:flex;align-items:center;gap:12px;white-space:nowrap;">
            <div style="display:flex;align-items:center;gap:6px;background:var(--bg-inset);padding:4px 12px;border-radius:var(--radius-sm);border:1px solid var(--border-default);">
                <span style="color:var(--text-secondary);font-size:12px;font-weight:500;">单票最高仓位:</span>
                <span id="hudMaxPosition" style="font-weight:800;font-size:13px;color:#16a34a;font-family:var(--font-mono);">0.0%</span>
                <span style="color:var(--text-muted);font-size:11px;">(硬限 &le;35%)</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;background:var(--bg-inset);padding:4px 12px;border-radius:var(--radius-sm);border:1px solid var(--border-default);">
                <span style="color:var(--text-secondary);font-size:12px;font-weight:500;">持仓分散度:</span>
                <span id="hudHoldingCount" style="font-weight:800;font-size:13px;color:var(--brand-blue);font-family:var(--font-mono);">0 只</span>
                <span style="color:var(--text-muted);font-size:11px;">(规则 &ge;3只)</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;background:var(--bg-inset);padding:4px 12px;border-radius:var(--radius-sm);border:1px solid var(--border-default);">
                <span style="color:var(--text-secondary);font-size:12px;font-weight:500;">团队最大回撤:</span>
                <span id="hudMaxDrawdown" style="font-weight:800;font-size:13px;color:#16a34a;font-family:var(--font-mono);">0.0%</span>
                <span style="color:var(--text-muted);font-size:11px;">(警戒 12%)</span>
            </div>
            <div id="hudMacroCompass" style="display:flex;align-items:center;gap:6px;background:#eef2ff;padding:4px 12px;border-radius:var(--radius-sm);border:1px solid #c7d2fe;" title="四象限宏观态势与隐马尔可夫时态罗盘">
                <span style="font-size:12px;">🧭</span>
                <span style="color:#4338ca;font-size:12px;font-weight:600;">宏观罗盘:</span>
                <span id="hudMacroRegimeText" style="font-weight:700;font-size:12px;color:#3730a3;font-family:var(--font-mono);">牛市低波</span>
                <span id="hudMacroConfText" style="font-size:10px;color:#312e81;background:#e0e7ff;padding:1px 5px;border-radius:3px;font-family:var(--font-mono);font-weight:700;">75%</span>
            </div>
        </div>

        <!-- Right: 系统体检胶囊 & 桌面端标识 -->
        <div style="display:flex;align-items:center;gap:12px;white-space:nowrap;">
            <div style="display:flex;align-items:center;gap:6px;background:#f0fdf4;padding:4px 10px;border-radius:var(--radius-sm);border:1px solid #bbf7d0;" id="hudSsePill" title="驾驶舱毫秒级 SSE 流式总线">
                <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#16a34a;" id="hudSseDot"></span>
                <span style="color:#15803d;font-size:11px;font-weight:700;" id="hudSseText">⚡ SSE 实时推流</span>
            </div>
            <div onclick="openPerformanceModal()" style="cursor:pointer;display:flex;align-items:center;gap:6px;background:#f0fdf4;padding:4px 12px;border-radius:var(--radius-sm);border:1px solid #bbf7d0;transition:all 0.2s;" title="点击打开同花顺API网络延迟与链路健康监控">
                <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#16a34a;" id="hudRttDot"></span>
                <span style="color:#15803d;font-size:11px;font-weight:600;">网关 RTT:</span>
                <span style="color:#16a34a;font-weight:700;font-size:12px;font-family:var(--font-mono);" id="hudRttText">-- ms</span>
            </div>
        </div>"""

if old_hud_inner in content:
    content = content.replace(old_hud_inner, new_hud_inner)
    print("Update HUD inner content: OK")
else:
    print("Warning: old_hud_inner not found verbatim")

# 12. 升级左侧 Panel 1 总结条（多维数据融合权重的高清晰度）
old_p1_summary = """                <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;margin-bottom:12px;display:flex;flex-direction:column;gap:6px;font-size:11px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <span style="color:#8b949e;">今日时序推进状态</span>
                        <span id="pipelineSummaryText" style="color:#3fb950;font-weight:600;">8个时序节点已就绪</span>
                    </div>
                    <div style="display:flex;align-items:center;justify-content:space-between;border-top:1px solid rgba(255,255,255,0.06);padding-top:5px;">
                        <span style="color:#a5b4fc;font-size:10.5px;font-weight:700;">⚖️ 多维数据融合权重</span>
                        <span style="color:#cbd5e1;font-size:10px;font-family:var(--font-mono);background:rgba(99,102,241,0.15);padding:1px 6px;border-radius:4px;border:1px solid rgba(99,102,241,0.3);">技术65% · 盘口/未成交20% · 舆情15%</span>
                    </div>
                </div>"""

new_p1_summary = """                <div style="background:var(--bg-inset);border:1px solid var(--border-default);border-radius:8px;padding:10px 12px;margin-bottom:12px;display:flex;flex-direction:column;gap:6px;font-size:11px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <span style="color:var(--text-secondary);font-weight:600;">今日时序推进状态</span>
                        <span id="pipelineSummaryText" style="color:#16a34a;font-weight:700;">8个时序节点已就绪</span>
                    </div>
                    <div style="display:flex;align-items:center;justify-content:space-between;border-top:1px solid var(--border-default);padding-top:6px;">
                        <span style="color:#4338ca;font-size:11px;font-weight:700;">⚖️ 多维数据融合权重</span>
                        <span style="color:#3730a3;font-size:10px;font-family:var(--font-mono);background:#e0e7ff;padding:1px 6px;border-radius:4px;border:1px solid #c7d2fe;font-weight:600;">技术65% · 盘口/未成交20% · 舆情15%</span>
                    </div>
                </div>"""
if old_p1_summary in content:
    content = content.replace(old_p1_summary, new_p1_summary)
    print("Update pipeline summary bar: OK")
else:
    print("Warning: old_p1_summary not found verbatim")

# 13. 升级中间 Panel 2 标题和候选池标签
old_p2_badges = """                            <span style="font-size:13px;font-weight:800;color:var(--text-primary);letter-spacing:0.4px;white-space:nowrap;display:flex;align-items:center;gap:4px;">🎯 算法自动优选冲击候选池</span>
                            <span style="font-size:11px;background:rgba(0,210,255,0.12);color:var(--brand-cyan, #ffe600);border:1px solid rgba(0,210,255,0.35);padding:2px 8px;border-radius:12px;font-weight:700;white-space:nowrap;box-shadow:0 0 8px rgba(0,210,255,0.2);">全自动多因子扫描</span>
                            <span id="dataUtilizationBadge" style="display:inline-flex;align-items:center;gap:4px;font-size:11px;background:rgba(99,102,241,0.15);color:#c7d2fe;border:1px solid rgba(99,102,241,0.4);padding:2px 8px;border-radius:12px;font-weight:700;white-space:nowrap;" title="多维量化加权表决矩阵">⚖️ 技术65% · 盘口/未成交20% · 舆情15%</span>
                        </div>
                        <span id="candidateActiveAccountPill" style="display:inline-block;font-size:11px;background:rgba(56,189,248,0.15);color:#38bdf8;border:1px solid rgba(56,189,248,0.35);padding:2px 8px;border-radius:10px;font-weight:700;">🎯 联动账户: 实时资金联动计算</span>"""

new_p2_badges = """                            <span style="font-size:13px;font-weight:800;color:var(--text-primary);letter-spacing:0.4px;white-space:nowrap;display:flex;align-items:center;gap:4px;">🎯 算法自动优选冲击候选池</span>
                            <span style="font-size:11px;background:#eff6ff;color:#2563eb;border:1px solid #bfdbfe;padding:2px 8px;border-radius:12px;font-weight:700;white-space:nowrap;">全自动多因子扫描</span>
                            <span id="dataUtilizationBadge" style="display:inline-flex;align-items:center;gap:4px;font-size:11px;background:#e0e7ff;color:#4338ca;border:1px solid #c7d2fe;padding:2px 8px;border-radius:12px;font-weight:700;white-space:nowrap;" title="多维量化加权表决矩阵">⚖️ 技术65% · 盘口/未成交20% · 舆情15%</span>
                        </div>
                        <span id="candidateActiveAccountPill" style="display:inline-block;font-size:11px;background:#f0fdf4;color:#15803d;border:1px solid #86efac;padding:2px 8px;border-radius:10px;font-weight:700;">🎯 联动账户: 实时资金联动计算</span>"""
if old_p2_badges in content:
    content = content.replace(old_p2_badges, new_p2_badges)
    print("Update candidate pool badges: OK")
else:
    print("Warning: old_p2_badges not found verbatim")

# 14. 升级候选池表头 padding 使得在大屏和小屏下紧凑而不挤出滚动条
old_table_th = """                                <tr style="color:var(--text-secondary);font-size:12px;font-weight:700;letter-spacing:0.4px;white-space:nowrap;">
                                    <th style="padding:10px 14px;text-align:left;white-space:nowrap;min-width:130px;">代码 / 名称</th>
                                    <th style="padding:10px 14px;text-align:left;white-space:nowrap;min-width:100px;">行业板块</th>
                                    <th style="padding:10px 14px;text-align:right;white-space:nowrap;min-width:90px;">最新价</th>
                                    <th style="padding:10px 14px;text-align:right;white-space:nowrap;min-width:90px;">涨跌幅</th>
                                    <th style="padding:10px 14px;text-align:right;white-space:nowrap;min-width:90px;">多因子评分</th>
                                    <th style="padding:10px 14px;text-align:center;white-space:nowrap;min-width:95px;">策略表决</th>
                                    <th style="padding:10px 14px;text-align:center;white-space:nowrap;min-width:95px;">推荐决策</th>
                                    <th style="padding:10px 14px;text-align:right;white-space:nowrap;min-width:85px;">建议仓位</th>
                                    <th style="padding:10px 14px;text-align:right;white-space:nowrap;min-width:130px;">合规止损线</th>
                                </tr>"""

new_table_th = """                                <tr style="color:var(--text-secondary);font-size:12px;font-weight:700;letter-spacing:0.3px;white-space:nowrap;">
                                    <th style="padding:8px 10px;text-align:left;white-space:nowrap;min-width:110px;">代码 / 名称</th>
                                    <th style="padding:8px 10px;text-align:left;white-space:nowrap;min-width:80px;">行业板块</th>
                                    <th style="padding:8px 10px;text-align:right;white-space:nowrap;min-width:70px;">最新价</th>
                                    <th style="padding:8px 10px;text-align:right;white-space:nowrap;min-width:70px;">涨跌幅</th>
                                    <th style="padding:8px 10px;text-align:right;white-space:nowrap;min-width:75px;">多因子评分</th>
                                    <th style="padding:8px 10px;text-align:center;white-space:nowrap;min-width:80px;">策略表决</th>
                                    <th style="padding:8px 10px;text-align:center;white-space:nowrap;min-width:80px;">推荐决策</th>
                                    <th style="padding:8px 10px;text-align:right;white-space:nowrap;min-width:90px;">建议仓位</th>
                                    <th style="padding:8px 10px;text-align:right;white-space:nowrap;min-width:105px;">合规止损线</th>
                                </tr>"""
if old_table_th in content:
    content = content.replace(old_table_th, new_table_th)
    print("Update candidate table headers: OK")
else:
    print("Warning: old_table_th not found verbatim")

# 15. 升级 JS: renderPipelineList 内部节点卡片与演练按钮
old_pipeline_card = """            return `
                <div class="pipeline-step-card" style="background: #ffffff; border: 1px solid #e2e8f0; border-left: 3px solid ${st.color}; border-radius: 6px; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; transition: all 0.2s; box-shadow: 0 1px 2px rgba(0,0,0,0.03);" onmouseover="this.style.background='#f8fafc'" onmouseout="this.style.background='#ffffff'">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-family: var(--font-mono); font-size: 11px; font-weight: 800; color: #d97706; background: #fffbeb; border: 1px solid #fde68a; padding: 1px 6px; border-radius: 4px;">${e.window || `W${idx}`}</span>
                            <span style="font-size: 12px; font-weight: 700; color: #0f172a;">${e.step_name || e.step_id}</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 6px;">
                            ${dur ? `<span style="font-size: 10px; color: #64748b; font-family: var(--font-mono);">${dur}</span>` : ''}
                            <span style="font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 10px; background: ${st.bg}; color: ${st.color}; border: 1px solid ${st.border};">${st.text}</span>
                        </div>
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 2px;">
                        <div style="font-size: 11px; color: #64748b; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 78%;">
                            ${summary}
                        </div>
                        <button onclick="triggerStepRun('${e.step_id}', true)" style="background: #eff6ff; border: 1px solid #bfdbfe; color: #2563eb; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 4px; cursor: pointer; white-space: nowrap;">
                            单步演练
                        </button>
                    </div>
                </div>
            `;"""

new_pipeline_card = """            const durRaw = Number(e.duration_ms) || 0;
            const durFormatted = durRaw > 0 ? (durRaw >= 1000 ? `${(durRaw / 1000).toFixed(1)}s` : `${durRaw}ms`) : '';
            return `
                <div class="pipeline-step-card" style="background: var(--bg-card); border: 1px solid var(--border-default); border-left: 3px solid ${st.color}; border-radius: 6px; padding: 9px 10px; display: flex; flex-direction: column; gap: 3px; transition: all 0.2s; box-shadow: 0 1px 2px rgba(0,0,0,0.02);" onmouseover="this.style.background='var(--bg-card-hover)'" onmouseout="this.style.background='var(--bg-card)'">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div style="display: flex; align-items: center; gap: 6px; min-width: 0;">
                            <span class="pipeline-time-pill" style="font-family: var(--font-mono); font-size: 11px; font-weight: 700; color: var(--text-secondary); background: var(--bg-inset); border: 1px solid var(--border-default); padding: 1px 6px; border-radius: 4px; flex-shrink: 0;">${e.window || `W${idx}`}</span>
                            <span style="font-size: 12px; font-weight: 700; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${e.step_name || e.step_id}</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 5px; flex-shrink: 0;">
                            ${durFormatted ? `<span style="font-size: 10px; color: var(--text-muted); font-family: var(--font-mono);">${durFormatted}</span>` : ''}
                            <span style="font-size: 10.5px; font-weight: 700; padding: 2px 8px; border-radius: 12px; background: ${st.bg}; color: ${st.color}; border: 1px solid ${st.border}; white-space: nowrap; min-width: 42px; text-align: center;">${st.text}</span>
                        </div>
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 2px;">
                        <div style="font-size: 11px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 75%;">
                            ${summary}
                        </div>
                        <button onclick="triggerStepRun('${e.step_id}', true)" style="background: var(--bg-surface); border: 1px solid var(--border-default); color: var(--brand-blue); font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px; cursor: pointer; white-space: nowrap; transition: all 0.2s;" onmouseover="this.style.background='var(--bg-card-hover)'" onmouseout="this.style.background='var(--bg-surface)'">
                            单步演练
                        </button>
                    </div>
                </div>
            `;"""
if old_pipeline_card in content:
    content = content.replace(old_pipeline_card, new_pipeline_card)
    print("Update pipeline card render: OK")
else:
    print("Warning: old_pipeline_card not found verbatim")

# 16. 升级 JS: renderCandidateTable 彻底消除白字隐形与颜色对比度问题
old_render_row = """                <tr style="border-bottom: 1px solid #f1f5f9; cursor: pointer; transition: background 0.15s;" onmouseover="this.style.background='#f8fafc'" onmouseout="this.style.background='transparent'" onclick="if(typeof selectStock==='function') selectStock('${item.code}')">
                    <td style="padding: 10px 14px; white-space: nowrap;">
                        <div style="font-weight: 700; color: #0f172a; font-size: 13px;">${item.name}</div>
                        <div style="display: flex; align-items: center; gap: 6px; margin-top: 2px;">
                            <span style="font-size: 11px; color: #64748b; font-family: var(--font-mono);">${item.code}</span>
                            <span style="font-size: 10px; color: ${Number(item.pullback_pct || 0) > 0 ? '#d97706' : '#64748b'}; font-family: var(--font-mono);" title="日内高点起伏回踩">起伏:${Number(item.pullback_pct || 0).toFixed(1)}%</span>
                            <span style="font-size: 10px; color: ${(Number(item.obi || 0) >= 0) ? '#dc2626' : '#0891b2'}; font-family: var(--font-mono);" title="盘口委比 (Order Book Imbalance)">OBI:${item.obi_pct || ((Number(item.obi || 0) >= 0 ? '+' : '') + (Number(item.obi || 0) * 100).toFixed(1) + '%')}</span>
                        </div>
                    </td>
                    <td style="padding: 10px 14px; white-space: nowrap;">
                        <span style="font-size: 11px; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.1); color: #cbd5e1; padding: 2px 7px; border-radius: 4px;">
                            ${item.industry || item.sector || '主线热门'}
                        </span>
                    </td>
                    <td style="padding: 10px 14px; text-align: right; font-family: var(--font-mono); font-weight: 700; color: #f8fafc; font-size: 13px;">
                        ¥${price.toFixed(2)}
                    </td>
                    <td style="padding: 10px 14px; text-align: right; font-family: var(--font-mono); font-weight: 800; color: ${chgColor}; font-size: 13px;">
                        ${chgSign}${chg.toFixed(2)}%
                    </td>
                    <td style="padding: 10px 14px; text-align: right;">
                        <span style="font-family: var(--font-mono); font-weight: 800; font-size: 12px; color: #ffe600; background: rgba(255,230,0,0.12); padding: 2px 6px; border-radius: 4px;">
                            ${score}
                        </span>
                    </td>
                    <td style="padding: 10px 14px; text-align: center;">
                        <span style="font-size: 11px; font-weight: 700; color: #00f59b; background: rgba(0,245,155,0.12); padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(0,245,155,0.3);">
                            ${item.ensemble_votes || '3/5 赞成'}
                        </span>
                    </td>
                    <td style="padding: 10px 14px; text-align: center;">
                        <span style="font-size: 11px; font-weight: 700; color: ${isBuy ? '#ff1824' : '#ffe600'}; background: ${isBuy ? 'rgba(255,24,36,0.15)' : 'rgba(255,230,0,0.14)'}; padding: 2px 8px; border-radius: 4px; border: 1px solid ${isBuy ? 'rgba(255,24,36,0.3)' : 'rgba(255,230,0,0.35)'};">
                            ${isBuy ? '买入' : '观望'}
                        </span>
                    </td>
                    <td style="padding: 10px 14px; text-align: right;">
                        ${positionCellHtml}
                    </td>
                    <td style="padding: 10px 14px; text-align: right; font-family: var(--font-mono); font-weight: 700; color: #00f59b;">
                        ¥${stopLoss.toFixed(2)} <span style="font-size: 10.5px; color: #8b949e;">(${stopLossPctText})</span>
                    </td>
                </tr>"""

new_render_row = """                <tr style="border-bottom: 1px solid var(--border-subtle); cursor: pointer; transition: background 0.15s;" onmouseover="this.style.background='var(--bg-card-hover)'" onmouseout="this.style.background='transparent'" onclick="if(typeof selectStock==='function') selectStock('${item.code}')">
                    <td style="padding: 8px 10px; white-space: nowrap;">
                        <div style="font-weight: 700; color: var(--text-primary); font-size: 13px;">${item.name}</div>
                        <div style="display: flex; align-items: center; gap: 5px; margin-top: 2px;">
                            <span style="font-size: 11px; color: var(--text-muted); font-family: var(--font-mono);">${item.code}</span>
                            <span style="font-size: 10px; color: ${Number(item.pullback_pct || 0) > 0 ? '#b45309' : 'var(--text-muted)'}; font-family: var(--font-mono);" title="日内高点起伏回踩">起伏:${Number(item.pullback_pct || 0).toFixed(1)}%</span>
                            <span style="font-size: 10px; color: ${(Number(item.obi || 0) >= 0) ? '#dc2626' : '#2563eb'}; font-family: var(--font-mono);" title="盘口委比 (Order Book Imbalance)">OBI:${item.obi_pct || ((Number(item.obi || 0) >= 0 ? '+' : '') + (Number(item.obi || 0) * 100).toFixed(1) + '%')}</span>
                        </div>
                    </td>
                    <td style="padding: 8px 10px; white-space: nowrap;">
                        <span style="font-size: 11px; background: var(--bg-inset); border: 1px solid var(--border-default); color: var(--text-secondary); padding: 2px 6px; border-radius: 4px;">
                            ${item.industry || item.sector || '主线热门'}
                        </span>
                    </td>
                    <td style="padding: 8px 10px; text-align: right; font-family: var(--font-mono); font-weight: 700; color: var(--text-primary); font-size: 13px;">
                        ¥${price.toFixed(2)}
                    </td>
                    <td style="padding: 8px 10px; text-align: right; font-family: var(--font-mono); font-weight: 800; color: ${chg >= 0 ? 'var(--up-red)' : 'var(--down-green)'}; font-size: 13px;">
                        ${chgSign}${chg.toFixed(2)}%
                    </td>
                    <td style="padding: 8px 10px; text-align: right;">
                        <span style="font-family: var(--font-mono); font-weight: 800; font-size: 12px; color: #b45309; background: #fef3c7; border: 1px solid #fde68a; padding: 2px 6px; border-radius: 4px;">
                            ${score}
                        </span>
                    </td>
                    <td style="padding: 8px 10px; text-align: center;">
                        <span style="font-size: 11px; font-weight: 700; color: #15803d; background: #dcfce7; padding: 2px 7px; border-radius: 4px; border: 1px solid #86efac;">
                            ${item.ensemble_votes || '3/5 赞成'}
                        </span>
                    </td>
                    <td style="padding: 8px 10px; text-align: center;">
                        <span style="font-size: 11px; font-weight: 700; color: ${isBuy ? '#dc2626' : '#b45309'}; background: ${isBuy ? '#fee2e2' : '#fef3c7'}; padding: 2px 7px; border-radius: 4px; border: 1px solid ${isBuy ? '#fca5a5' : '#fde68a'};">
                            ${isBuy ? '买入' : '观望'}
                        </span>
                    </td>
                    <td style="padding: 8px 10px; text-align: right;">
                        ${positionCellHtml}
                    </td>
                    <td style="padding: 8px 10px; text-align: right; font-family: var(--font-mono); font-weight: 700; color: var(--text-primary);">
                        ¥${stopLoss.toFixed(2)} <span style="font-size: 10px; color: var(--up-red); font-weight: 600;">(${stopLossPctText})</span>
                    </td>
                </tr>"""

if old_render_row in content:
    content = content.replace(old_render_row, new_render_row)
    print("Update candidate row render: OK")
else:
    print("Warning: old_render_row not found verbatim")

# 17. 升级 positionCellHtml 里的资金不足和仓位达限徽章
old_pos_cell = """                    positionCellHtml = `
                        <div style="font-weight: 700; color: #f8fafc; font-size: 13px; font-family: var(--font-mono);">
                            ${weight}%
                        </div>
                        <div style="margin-top: 3px;">
                            <span style="font-size: 10px; background: rgba(255,77,79,0.18); border: 1px solid rgba(255,77,79,0.45); color: #ff4d4f; padding: 1px 6px; border-radius: 3px; font-weight: 800; display: inline-block;" title="${item.reason || ''}">
                                ${badgeText}
                            </span>
                        </div>
                        <div style="font-size: 9.5px; color: #f87171; margin-top: 2px; max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${item.reason || ''}">
                            ${item.reason || '资金不足买入1手'}
                        </div>
                    `;"""

new_pos_cell = """                    positionCellHtml = `
                        <div style="font-weight: 700; color: var(--text-primary); font-size: 13px; font-family: var(--font-mono);">
                            ${weight}%
                        </div>
                        <div style="margin-top: 2px;">
                            <span style="font-size: 10px; background: #fee2e2; border: 1px solid #fca5a5; color: #dc2626; padding: 1px 5px; border-radius: 3px; font-weight: 700; display: inline-block;" title="${item.reason || ''}">
                                ${badgeText}
                            </span>
                        </div>
                        <div style="font-size: 9.5px; color: var(--text-muted); margin-top: 1px; max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${item.reason || ''}">
                            ${item.reason || '资金不足买入1手'}
                        </div>
                    `;"""
if old_pos_cell in content:
    content = content.replace(old_pos_cell, new_pos_cell)
    print("Update positionCellHtml: OK")
else:
    print("Warning: old_pos_cell not found verbatim")

# 18. 升级 3 账户异构卡片配置与渲染 (消除截断为 账... 的问题)
old_acc_cfg = """        const HETERO_ACCOUNTS_CONFIG = {
            0: { label: '先锋进攻', badge: 'MOMENTUM', themeColor: '#ff1824', bgGrad: 'rgba(255,24,36,0.12)' },
            1: { label: '稳健中军', badge: 'TREND-CORE', themeColor: '#00f59b', bgGrad: 'rgba(0,245,155,0.12)' },
            2: { label: '尾盘防守', badge: 'DEFENSIVE', themeColor: '#ffe600', bgGrad: 'rgba(255,230,0,0.12)' }
        };"""

new_acc_cfg = """        const HETERO_ACCOUNTS_CONFIG = {
            0: { label: '先锋进攻', badge: 'MOMENTUM', themeColor: '#dc2626', bgGrad: '#fee2e2', borderColor: '#fca5a5' },
            1: { label: '稳健中军', badge: 'TREND-CORE', themeColor: '#2563eb', bgGrad: '#eff6ff', borderColor: '#bfdbfe' },
            2: { label: '尾盘防守', badge: 'DEFENSIVE', themeColor: '#b45309', bgGrad: '#fef3c7', borderColor: '#fde68a' }
        };"""
if old_acc_cfg in content:
    content = content.replace(old_acc_cfg, new_acc_cfg)
    print("Update HETERO_ACCOUNTS_CONFIG: OK")
else:
    print("Warning: old_acc_cfg not found verbatim")

old_acc_card_render = """                const activeStyle = isActive
                    ? `border: 1.5px solid ${cfg.themeColor}; border-top: 3px solid ${cfg.themeColor}; background: #ffffff; box-shadow: 0 4px 12px rgba(0,0,0,0.08);`
                    : `border: 1px solid #e2e8f0; border-top: 2px solid ${cfg.themeColor}; background: #f8fafc;`;

                const dispName = (acc.name && String(acc.name).length <= 4) ? `账户${acc.name}` : (acc.name || `账户${acc.id}`);

                return `
                    <div onclick="switchAccount(${acc.id})" style="${activeStyle} border-radius: 6px; padding: 8px 8px; cursor: pointer; transition: all 0.2s; min-width: 0; overflow: hidden;" onmouseover="this.style.transform='translateY(-2px)'" onmouseout="this.style.transform='none'">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; gap: 2px;">
                            <div style="display: flex; align-items: center; gap: 3px; min-width: 0; overflow: hidden;">
                                <span style="font-size: 11px; font-weight: 800; color: #0f172a; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${dispName}</span>
                                ${isActive ? '<span style="font-size: 9px; color: #16a34a; font-weight: 800; flex-shrink: 0;">●</span>' : ''}
                            </div>
                            <span style="font-size: 8px; font-weight: 800; font-family: var(--font-mono); padding: 1px 4px; border-radius: 3px; color: ${cfg.themeColor}; background: ${cfg.bgGrad}; flex-shrink: 0;">${cfg.label}</span>
                        </div>
                        <div style="font-size: 13px; font-weight: 800; color: #0f172a; font-family: var(--font-mono); margin-bottom: 2px; white-space: nowrap;">
                            ¥${fmtMoney(assets)}
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 10px; white-space: nowrap;">
                            <span style="color: #64748b;">余 ¥${fmtMoney(available)}</span>
                            <span style="color: ${profitColor}; font-weight: 700; font-family: var(--font-mono);">${sign}${profitRate.toFixed(1)}%</span>
                        </div>
                    </div>
                `;"""

new_acc_card_render = """                const activeStyle = isActive
                    ? `border: 1.5px solid ${cfg.themeColor}; border-top: 3px solid ${cfg.themeColor}; background: var(--bg-surface); box-shadow: 0 2px 8px rgba(0,0,0,0.06);`
                    : `border: 1px solid var(--border-default); border-top: 2px solid ${cfg.borderColor}; background: var(--bg-inset);`;

                const rawName = acc.name ? String(acc.name) : String(acc.id);
                const dispName = (rawName.startsWith('账户') || rawName.startsWith('#')) ? rawName : `账户${rawName}`;

                return `
                    <div onclick="switchAccount(${acc.id})" style="${activeStyle} border-radius: 6px; padding: 7px 8px; cursor: pointer; transition: all 0.2s; min-width: 0;" onmouseover="this.style.transform='translateY(-1px)'" onmouseout="this.style.transform='none'">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 3px;">
                            <div style="display: flex; align-items: center; gap: 3px; min-width: 0;">
                                <span style="font-size: 11px; font-weight: 800; color: var(--text-primary); white-space: nowrap;">${dispName}</span>
                                ${isActive ? '<span style="font-size: 8px; color: #16a34a; font-weight: 900;">●</span>' : ''}
                            </div>
                            <span style="font-size: 9px; font-weight: 700; color: ${cfg.themeColor}; background: ${cfg.bgGrad}; padding: 1px 4px; border-radius: 3px; white-space: nowrap;">${cfg.label}</span>
                        </div>
                        <div style="font-size: 13px; font-weight: 800; color: var(--text-primary); font-family: var(--font-mono); margin-bottom: 2px; white-space: nowrap;">
                            ¥${fmtMoney(assets)}
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 10px; white-space: nowrap;">
                            <span style="color: var(--text-muted);">余 ¥${fmtMoney(available)}</span>
                            <span style="color: ${profit >= 0 ? 'var(--up-red)' : 'var(--down-green)'}; font-weight: 700; font-family: var(--font-mono);">${sign}${profitRate.toFixed(1)}%</span>
                        </div>
                    </div>
                `;"""

if old_acc_card_render in content:
    content = content.replace(old_acc_card_render, new_acc_card_render)
    print("Update account card render: OK")
else:
    print("Warning: old_acc_card_render not found verbatim")

# 18.5 替换 HUD 内部结构
hud_start_needle = '<div style="display:flex;align-items:center;gap:8px;background:var(--bg-card);padding:4px 12px;border-radius:var(--radius-sm);border:1px solid var(--border-default);">'
hud_end_needle = '</div>\n    </div>\n\n    <!-- 工作台统一视口'

idx1 = content.find(hud_start_needle)
idx2 = content.find(hud_end_needle)
if idx1 != -1 and idx2 != -1 and idx2 > idx1:
    old_hud_slice = content[idx1:idx2]
    new_hud_slice = """<div style="display:flex;align-items:center;gap:8px;background:var(--bg-inset);padding:4px 12px;border-radius:var(--radius-sm);border:1px solid var(--border-default);">
                <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#16a34a;box-shadow:0 0 6px #86efac;" id="hudClockDot"></span>
                <span style="font-family:var(--font-mono);font-size:13px;font-weight:800;color:var(--brand-blue);letter-spacing:0.5px;" id="hudClockTime">--:--:--</span>
                <span id="hudMockTag" style="display:none;background:#fef3c7;color:#b45309;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:700;">虚拟时钟</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;">
                <span class="hud-window-label" style="color:var(--text-secondary);font-size:12px;font-weight:600;">时序窗口:</span>
                <span id="hudCurrentWindow" style="background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;border-radius:var(--radius-sm);padding:3px 10px;font-size:12px;font-weight:700;">盘前扫描</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;color:var(--text-secondary);font-size:12px;">
                <span class="hud-next-label">⏱️ 下一时序:</span>
                <span id="hudNextCountdown" style="color:var(--warning-amber);font-family:var(--font-mono);font-size:12px;font-weight:700;">计算中...</span>
            </div>
        </div>

        <!-- Center: 团队赛风控态势胶囊 -->
        <div style="display:flex;align-items:center;gap:12px;white-space:nowrap;">
            <div style="display:flex;align-items:center;gap:6px;background:var(--bg-inset);padding:4px 12px;border-radius:var(--radius-sm);border:1px solid var(--border-default);">
                <span style="color:var(--text-secondary);font-size:12px;font-weight:500;">单票最高仓位:</span>
                <span id="hudMaxPosition" style="font-weight:800;font-size:13px;color:#16a34a;font-family:var(--font-mono);">0.0%</span>
                <span style="color:var(--text-muted);font-size:11px;">(硬限 &le;35%)</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;background:var(--bg-inset);padding:4px 12px;border-radius:var(--radius-sm);border:1px solid var(--border-default);">
                <span style="color:var(--text-secondary);font-size:12px;font-weight:500;">持仓分散度:</span>
                <span id="hudHoldingCount" style="font-weight:800;font-size:13px;color:var(--brand-blue);font-family:var(--font-mono);">0 只</span>
                <span style="color:var(--text-muted);font-size:11px;">(规则 &ge;3只)</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;background:var(--bg-inset);padding:4px 12px;border-radius:var(--radius-sm);border:1px solid var(--border-default);">
                <span style="color:var(--text-secondary);font-size:12px;font-weight:500;">团队最大回撤:</span>
                <span id="hudMaxDrawdown" style="font-weight:800;font-size:13px;color:#16a34a;font-family:var(--font-mono);">0.0%</span>
                <span style="color:var(--text-muted);font-size:11px;">(警戒 12%)</span>
            </div>
            <div id="hudMacroCompass" style="display:flex;align-items:center;gap:6px;background:#eef2ff;padding:4px 12px;border-radius:var(--radius-sm);border:1px solid #c7d2fe;" title="四象限宏观态势与隐马尔可夫时态罗盘">
                <span style="font-size:12px;">🧭</span>
                <span style="color:#4338ca;font-size:12px;font-weight:600;">宏观罗盘:</span>
                <span id="hudMacroRegimeText" style="font-weight:700;font-size:12px;color:#3730a3;font-family:var(--font-mono);">牛市低波</span>
                <span id="hudMacroConfText" style="font-size:10px;color:#312e81;background:#e0e7ff;padding:1px 5px;border-radius:3px;font-family:var(--font-mono);font-weight:700;">75%</span>
            </div>
        </div>

        <!-- Right: 系统体检胶囊 & 桌面端标识 -->
        <div style="display:flex;align-items:center;gap:12px;white-space:nowrap;">
            <div style="display:flex;align-items:center;gap:6px;background:#f0fdf4;padding:4px 10px;border-radius:var(--radius-sm);border:1px solid #bbf7d0;" id="hudSsePill" title="驾驶舱毫秒级 SSE 流式总线">
                <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#16a34a;" id="hudSseDot"></span>
                <span style="color:#15803d;font-size:11px;font-weight:700;" id="hudSseText">⚡ SSE 实时推流</span>
            </div>
            <div onclick="openPerformanceModal()" style="cursor:pointer;display:flex;align-items:center;gap:6px;background:#f0fdf4;padding:4px 12px;border-radius:var(--radius-sm);border:1px solid #bbf7d0;transition:all 0.2s;" title="点击打开同花顺API网络延迟与链路健康监控">
                <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#16a34a;" id="hudRttDot"></span>
                <span style="color:#15803d;font-size:11px;font-weight:600;">网关 RTT:</span>
                <span style="color:#16a34a;font-weight:700;font-size:12px;font-family:var(--font-mono);" id="hudRttText">-- ms</span>
            </div>"""
    content = content[:idx1] + new_hud_slice + content[idx2:]
    print("Update HUD slice: OK")
else:
    print(f"Warning: HUD markers not found: idx1={idx1}, idx2={idx2}")

# 19. 还原换行符写回
if is_crlf:
    content = content.replace("\n", "\r\n")

html_path.write_text(content, encoding="utf-8")
print("[SUCCESS] Refinement written to static/index.html")
