import os
import re

html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    content = f.read()

print("Original file length:", len(content))

# =========================================================================
# 1. HTML tag & CSS Root Theme: Only Keep Warm Beige
# =========================================================================
# Set <html lang="zh-CN" data-theme="warm">
content = re.sub(r'<html\s+lang=["\']zh-CN["\'][^>]*>', '<html lang="zh-CN" data-theme="warm">', content)

# Replace :root / theme variables so :root IS the Warm Beige theme
css_root_pattern = re.compile(
    r':root,\s*\[data-theme=["\']slate["\']\]\s*\{.*?'
    r'/\*\s*🎨\s*2\s*暖米纸.*?'
    r'/\*\s*🎨\s*3\s*极简通透白.*?'
    r'/\*\s*🎨\s*4\s*暗夜极客黑.*?'
    r'\}\s*\}',
    re.DOTALL
)

# Let's inspect where :root starts and [data-theme="dark"] ends
r_start = content.find(':root')
d_end = content.find('/* --- 基础排版与全局重置 --- */')
if r_start != -1 and d_end != -1:
    warm_css_vars = ''':root, [data-theme="warm"], [data-theme="slate"], [data-theme="white"], [data-theme="dark"] {
            --bg-base: #ede7dc;            /* 温暖米纸底色 (暖米护眼) */
            --bg-surface: #fbf9f4;         /* 暖白卡片背景 */
            --bg-card: #fbf9f4;
            --bg-card-hover: #f3ece0;      /* 悬停微黄白 */
            --bg-glass: rgba(251, 249, 244, 0.96);
            --bg-inset: #e7dfd1;           /* 暖灰内嵌槽位 */
            --border-subtle: #e2d9cc;
            --border-default: #dad0be;      /* 暖纸边框 */
            --border-glow: 0 1px 3px 0 rgba(100, 70, 40, 0.08);
            --border-focus: #0369a1;
            --text-primary: #292524;       /* 浓墨暖黑文字 */
            --text-secondary: #57534e;     /* 次级灰墨文字 */
            --text-muted: #78716c;         /* 浅墨提示文字 */
            --up-red: #c2410c;             /* 暖调红涨 (A股红) */
            --up-red-bg: #ffedd5;
            --up-red-border: #fed7aa;
            --down-green: #15803d;         /* 暖调绿跌 (A股绿) */
            --down-green-bg: #dcfce7;
            --down-green-border: #bbf7d0;
            --brand-blue: #0369a1;         /* 机构深青蓝 */
            --brand-blue-bg: #e0f2fe;
            --brand-blue-border: #bae6fd;
            --brand-red: #c2410c;
            --brand-yellow: #b45309;
            --warning-amber: #b45309;
            --warning-amber-bg: #fef3c7;
            --warning-amber-border: #fde68a;
            --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            --font-mono: 'JetBrains Mono', Consolas, "SF Mono", monospace;
            --radius-sm: 6px;
            --radius-md: 8px;
            --radius-lg: 12px;
        }

        '''
    content = content[:r_start] + warm_css_vars + content[d_end:]
    print("Replaced CSS variables to solely Warm Beige")
else:
    print("Warning: could not locate :root or base reset delimiter")

# =========================================================================
# 2. Header Theme Pill: Only keep one warm beige badge
# =========================================================================
old_theme_pill = re.search(r'<div class="theme-switch-pill".*?</div>', content, re.DOTALL)
if old_theme_pill:
    new_theme_pill = '''<div class="theme-switch-pill" style="display:flex;align-items:center;background:var(--bg-inset);border:1px solid var(--border-default);border-radius:20px;padding:3px 10px;gap:4px;" title="固定暖米色金融护眼主题">
                <span style="font-size:11px;font-weight:700;color:var(--text-secondary);">📜 暖米色</span>
            </div>'''
    content = content[:old_theme_pill.start()] + new_theme_pill + content[old_theme_pill.end():]
    print("Replaced header theme switch pill with single 暖米色 badge")
else:
    print("Warning: old_theme_pill not found")

# Update setCockpitTheme JS so it always enforces 'warm'
set_theme_old = re.search(r'function setCockpitTheme\(theme\)\s*\{.*?window\.addEventListener\([\'"]DOMContentLoaded[\'"]', content, re.DOTALL)
if set_theme_old:
    set_theme_new = '''function setCockpitTheme(theme) {
        document.documentElement.setAttribute('data-theme', 'warm');
        try { localStorage.setItem('quant_cockpit_theme', 'warm'); } catch(e) {}
    }
    (function() {
        document.documentElement.setAttribute('data-theme', 'warm');
        window.addEventListener('DOMContentLoaded' '''
    content = content[:set_theme_old.start()] + set_theme_new + content[set_theme_old.end():]
    print("Updated setCockpitTheme JS function")

# =========================================================================
# 3. Account Switcher Popup: #accountSelectorPanel & renderAccountList
# =========================================================================
# In HTML markup:
old_panel_html = re.search(r'<div class="account-selector-panel" id="accountSelectorPanel".*?</div>\s*</div>\s*</div>', content, re.DOTALL)
if old_panel_html:
    new_panel_html = '''<div class="account-selector-panel" id="accountSelectorPanel" style="display:none;position:absolute;top:40px;right:0;width:350px;background:var(--bg-surface);border:1px solid var(--border-default);border-radius:12px;box-shadow:0 16px 40px rgba(70,50,30,0.14),0 2px 8px rgba(70,50,30,0.06);z-index:100001;overflow:hidden;">
                    <div class="account-selector-header" style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:1px solid var(--border-default);background:var(--bg-surface);">
                        <span style="font-weight:700;color:var(--text-primary);font-size:13px;">👥 账户切换与管理</span>
                        <span id="accountCount" style="font-size:11px;color:var(--text-muted);"></span>
                    </div>
                    <div class="account-selector-list" id="accountSelectorList" style="max-height:280px;overflow-y:auto;background:var(--bg-surface);">
                        <!-- 账号卡片动态渲染 -->
                    </div>
                    <div class="account-selector-footer" style="padding:10px 14px;border-top:1px solid var(--border-default);display:flex;gap:8px;background:var(--bg-inset);">
                        <button onclick="openConnectModal(); toggleAccountPanel();" style="flex:1;background:var(--brand-blue);color:white;border:none;padding:9px;border-radius:6px;font-size:12px;font-weight:700;cursor:pointer;box-shadow:0 1px 3px rgba(0,0,0,0.1);">+ 绑定/录入新账号</button>
                    </div>
                </div>
            </div>'''
    content = content[:old_panel_html.start()] + new_panel_html + content[old_panel_html.end():]
    print("Replaced #accountSelectorPanel markup")
else:
    print("Warning: old_panel_html not found")

# In JS renderAccountList:
old_render_acc = re.search(r'function renderAccountList\(\)\s*\{.*?function showAddAccountModal\(\)', content, re.DOTALL)
if old_render_acc:
    new_render_acc = '''function renderAccountList() {
            const list = document.getElementById('accountSelectorList');
            if (!list) return;
            if (!allAccounts.length) {
                list.innerHTML = '<div class="notification-empty" style="padding:24px;text-align:center;color:var(--text-muted);font-size:12px;">暂无绑定账号<br><span style="font-size:11px;color:var(--brand-blue);cursor:pointer;" onclick="openConnectModal();">点击下方 "+ 绑定/录入新账号" 添加</span></div>';
                return;
            }
            list.innerHTML = allAccounts.map((acc, idx) => {
                const meta = getAccountMeta(acc.id, idx);
                const initialBal = Number(acc.initial_balance) || 200000;
                const totalAssets = Number(acc.total_assets || acc.balance) || initialBal;
                const available = Number(acc.ky_fund || acc.balance) || 0;

                let profit = 0;
                if (acc.total_profit !== undefined && Number(acc.total_profit) !== 0) {
                    profit = Number(acc.total_profit);
                } else {
                    profit = totalAssets - initialBal;
                }

                let profitRate = 0;
                if (acc.total_profit_rate !== undefined && acc.total_profit_rate !== null && Number(acc.total_profit_rate) !== 0) {
                    profitRate = Number(acc.total_profit_rate);
                } else {
                    profitRate = (profit / initialBal) * 100;
                }

                const profitColor = profit >= 0 ? 'var(--up-red)' : 'var(--down-green)';
                const profitSign = profit > 0 ? '+' : (profit < 0 ? '-' : '');
                const rateSign = profitRate > 0 ? '+' : (profitRate < 0 ? '-' : '');

                const syncStatus = acc.last_sync_at ? '<span style="color:var(--down-green)">● 已实时同步</span>' : '<span style="color:var(--text-muted)">● 实时通道在线</span>';
                const isActive = (activeAccountId === acc.id) || (allAccounts.length === 1);
                const borderStyle = isActive 
                    ? 'border-left:3px solid var(--brand-blue);background:var(--bg-card-hover);' 
                    : 'border-left:3px solid transparent;background:transparent;';

                return `
                    <div class="account-selector-item" style="${borderStyle};display:flex;align-items:center;justify-content:space-between;padding:11px 14px;border-bottom:1px solid var(--border-subtle);transition:all 0.2s;" onmouseover="if(!${isActive}) this.style.background='var(--bg-card-hover)'" onmouseout="if(!${isActive}) this.style.background='transparent'">
                        <div style="flex:1;cursor:pointer;min-width:0;padding-right:8px;" onclick="switchAccount(${acc.id})">
                            <div style="font-weight:700;color:var(--text-primary);font-size:13px;display:flex;align-items:center;gap:6px;flex-wrap:nowrap;">
                                <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${acc.name || `账户${acc.id}`}</span>
                                <span style="background:${meta.tagBg}; color:${meta.tagColor}; border:1px solid ${meta.tagBorder}; padding:1px 6px; border-radius:4px; font-size:10px; font-weight:700; white-space:nowrap;">${meta.badge}</span>
                                ${isActive ? '<span style="background:var(--brand-blue-bg);color:var(--brand-blue);border:1px solid var(--brand-blue-border);padding:1px 6px;border-radius:10px;font-size:10px;font-weight:700;white-space:nowrap;">● 当前活跃</span>' : ''}
                            </div>
                            <div style="font-size:11px;color:var(--text-secondary);font-family:var(--font-mono);margin-top:4px;white-space:nowrap;">
                                资产: <strong style="color:var(--text-primary);">¥${formatNumber(totalAssets)}</strong> | 可用: ¥${formatNumber(available)}
                            </div>
                            <div style="font-size:10px;color:var(--text-muted);margin-top:3px;display:flex;gap:6px;">
                                ${syncStatus} ${acc.ths_gdzh ? '| 账号: ' + acc.ths_gdzh : ''}
                            </div>
                        </div>
                        <div style="text-align:right;min-width:95px;flex-shrink:0;">
                            <div style="color:${profitColor};font-size:13px;font-weight:800;font-family:var(--font-mono);letter-spacing:0.3px;">
                                ${profitSign}¥${formatNumber(Math.abs(profit))}
                            </div>
                            <div style="color:${profitColor};font-size:11px;font-weight:700;font-family:var(--font-mono);margin-top:2px;">
                                ${rateSign}${Math.abs(profitRate).toFixed(2)}%
                            </div>
                            <div style="margin-top:6px;display:flex;justify-content:flex-end;gap:5px;">
                                ${!isActive ? `<button onclick="event.stopPropagation();switchAccount(${acc.id})"
                                        style="background:var(--brand-blue-bg);border:1px solid var(--brand-blue-border);color:var(--brand-blue);border-radius:4px;cursor:pointer;font-size:11px;padding:2px 8px;font-weight:700;">切换</button>` : ''}
                                <button onclick="event.stopPropagation();syncOneAccount(${acc.id})" id="syncBtn_${acc.id}"
                                        style="background:var(--bg-inset);border:1px solid var(--border-default);color:var(--text-secondary);border-radius:4px;cursor:pointer;font-size:11px;padding:2px 8px;font-weight:600;">🔄 同步</button>
                                <button onclick="event.stopPropagation();deleteAccount(${acc.id})"
                                        style="background:#fef2f2;border:1px solid #fecaca;color:#dc2626;border-radius:4px;cursor:pointer;font-size:11px;padding:2px 6px;font-weight:600;">删除</button>
                            </div>
                        </div>
                    </div>`;
            }).join('');
        }

        function showAddAccountModal()'''
    content = content[:old_render_acc.start()] + new_render_acc + content[old_render_acc.end():]
    print("Replaced renderAccountList JS implementation")
else:
    print("Warning: old_render_acc not found")

# =========================================================================
# 4. Mobile Media Queries: Replace dark header, HUD, modals, and nav
# =========================================================================
# Mobile header:
content = content.replace(
    'background: rgba(10, 14, 23, 0.94) !important;',
    'background: var(--bg-surface) !important;'
)
content = content.replace(
    'border-bottom: 1px solid rgba(37, 51, 74, 0.8) !important;',
    'border-bottom: 1px solid var(--border-default) !important;'
)

# Mobile header tags:
content = content.replace(
    'background: rgba(255, 255, 255, 0.04) !important;\n                border-radius: 12px !important;\n                border: 1px solid rgba(255, 255, 255, 0.08) !important;',
    'background: var(--bg-inset) !important;\n                border-radius: 12px !important;\n                border: 1px solid var(--border-default) !important;\n                color: var(--text-secondary) !important;'
)

# Mobile HUD:
content = content.replace(
    'background: rgba(9, 13, 21, 0.95) !important;\n                border-bottom: 1px solid rgba(37, 51, 74, 0.7) !important;',
    'background: var(--bg-surface) !important;\n                border-bottom: 1px solid var(--border-default) !important;\n                color: var(--text-primary) !important;'
)

# Mobile modal:
content = content.replace(
    'background: linear-gradient(180deg, #131b2e 0%, #0d1424 100%) !important;\n                border: 1px solid rgba(255, 24, 36, 0.28) !important;\n                box-shadow: 0 16px 48px rgba(0, 0, 0, 0.85), 0 0 24px rgba(255, 24, 36, 0.15) !important;',
    'background: var(--bg-surface) !important;\n                border: 1px solid var(--border-default) !important;\n                box-shadow: 0 16px 48px rgba(70, 50, 30, 0.18), 0 2px 8px rgba(70, 50, 30, 0.08) !important;\n                color: var(--text-primary) !important;'
)

# Mobile bottom nav & items:
old_mobile_nav_css = re.search(r'\.mobile-nav\s*\{.*?\.mobile-nav-item\s*\{.*?\.mobile-nav-item\.active\s*\{.*?\}', content, re.DOTALL)
if old_mobile_nav_css:
    new_mobile_nav_css = '''.mobile-nav {
                display: flex !important;
                position: fixed !important;
                bottom: 0 !important;
                left: 0 !important;
                right: 0 !important;
                height: calc(56px + env(safe-area-inset-bottom, 20px)) !important;
                padding-bottom: env(safe-area-inset-bottom, 20px) !important;
                background: var(--bg-surface) !important;
                border-top: 1px solid var(--border-default) !important;
                box-shadow: 0 -4px 16px rgba(70, 50, 30, 0.08) !important;
                z-index: 99999 !important;
                backdrop-filter: blur(12px) !important;
                -webkit-backdrop-filter: blur(12px) !important;
            }
            .mobile-nav-item {
                flex: 1;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                gap: 2px;
                color: var(--text-muted) !important;
                cursor: pointer;
                transition: all 0.2s;
                padding: 4px 0;
            }
            .mobile-nav-item svg {
                width: 20px;
                height: 20px;
                stroke: currentColor;
                transition: transform 0.2s;
            }
            .mobile-nav-item.active {
                color: var(--brand-blue) !important;
                background: var(--brand-blue-bg) !important;
                border-radius: 8px !important;
                box-shadow: none !important;
                border: 1px solid var(--brand-blue-border) !important;
                stroke: var(--brand-blue) !important;
                filter: none !important;
            }'''
    content = content[:old_mobile_nav_css.start()] + new_mobile_nav_css + content[old_mobile_nav_css.end():]
    print("Replaced mobile bottom nav styles")

# =========================================================================
# 5. Modal and Input Base CSS: Clean Warm Beige
# =========================================================================
# Replace .modal base styles
modal_base_old = re.search(r'\.modal\s*\{\s*background:\s*#161b22;.*?width:\s*500px;.*?\}', content, re.DOTALL)
if modal_base_old:
    modal_base_new = '''.modal {
            background: var(--bg-surface);
            border-radius: 12px;
            border: 1px solid var(--border-default);
            color: var(--text-primary);
            box-shadow: 0 20px 48px rgba(70, 50, 30, 0.16), 0 2px 8px rgba(70, 50, 30, 0.06);
            width: 500px;
            max-width: 90%;
            max-height: 85vh;
            overflow-y: auto;
        }'''
    content = content[:modal_base_old.start()] + modal_base_new + content[modal_base_old.end():]
    print("Replaced .modal base CSS")

# Replace .modal-header, .modal-footer, .form-input
content = content.replace(
    '.modal-header {\n            display: flex;\n            justify-content: space-between;\n            align-items: center;\n            padding: 20px;\n            border-bottom: 1px solid #30363d;\n        }',
    '.modal-header {\n            display: flex;\n            justify-content: space-between;\n            align-items: center;\n            padding: 16px 20px;\n            border-bottom: 1px solid var(--border-default);\n            background: var(--bg-surface);\n        }'
)
content = content.replace(
    '.modal-footer {\n            padding: 15px 20px;\n            border-top: 1px solid #30363d;\n            display: flex;\n            justify-content: flex-end;\n            gap: 10px;\n        }',
    '.modal-footer {\n            padding: 14px 20px;\n            border-top: 1px solid var(--border-default);\n            background: var(--bg-inset);\n            display: flex;\n            justify-content: flex-end;\n            gap: 10px;\n        }'
)
content = content.replace(
    '.form-input {\n            width: 100%;\n            background: #0d1117;\n            border: 1px solid #30363d;\n            border-radius: 6px;\n            padding: 10px 12px;\n            color: #e6edf3;\n            font-size: 14px;\n        }',
    '.form-input, .form-select {\n            width: 100%;\n            background: var(--bg-surface);\n            border: 1px solid var(--border-default);\n            border-radius: 6px;\n            padding: 9px 12px;\n            color: var(--text-primary);\n            font-size: 13px;\n            box-sizing: border-box;\n        }'
)

# Connect modal header gradient -> Warm beige header
content = content.replace(
    'background: linear-gradient(135deg, #0f2442 0%, #111a2e 100%); border-bottom: 1px solid rgba(255, 24, 36, 0.25);',
    'background: var(--bg-surface); border-bottom: 1px solid var(--border-default);'
)
content = content.replace(
    'color: #f8fafc; letter-spacing: 0.3px;">登录与绑定同花顺账户',
    'color: var(--text-primary); letter-spacing: 0.3px;">登录与绑定同花顺账户'
)
# Connect modal tab strip
content = content.replace(
    'background: rgba(0, 0, 0, 0.4); padding: 4px; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.08);',
    'background: var(--bg-inset); padding: 4px; border-radius: 8px; border: 1px solid var(--border-default);'
)
# Connect modal QR container
content = content.replace(
    'background: #0d1117; border: 1px solid #30363d; text-align: center;',
    'background: var(--bg-inset); border: 1px solid var(--border-default); text-align: center;'
)
content = content.replace(
    'style="background: #21262d; border: 1px solid #30363d; color: #c9d1d9; padding: 6px 14px; border-radius: 6px; font-size: 12px; cursor: pointer;">',
    'style="background: var(--bg-surface); border: 1px solid var(--border-default); color: var(--text-primary); padding: 6px 14px; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer;">'
)

# Fix AI config cancel button (#30363d)
content = content.replace(
    'background:#30363d;">取消</button>',
    'background:var(--bg-inset);border:1px solid var(--border-default);color:var(--text-secondary);">取消</button>'
)

# Fix SMS login buttons:
content = content.replace(
    'background: rgba(255, 24, 36, 0.15); border: 1px solid rgba(255, 24, 36, 0.35); color: #ff1824;',
    'background: var(--brand-blue-bg); border: 1px solid var(--brand-blue-border); color: var(--brand-blue);'
)
content = content.replace(
    'background: linear-gradient(135deg, #c8101e 0%, #ff1824 100%); color: #ffffff; border: none; padding: 11px; border-radius: 6px; font-size: 14px; font-weight: 700; cursor: pointer; box-shadow: 0 4px 16px rgba(255, 24, 36, 0.3);',
    'background: var(--brand-blue); color: #ffffff; border: none; padding: 10px; border-radius: 6px; font-size: 13px; font-weight: 700; cursor: pointer; box-shadow: 0 1px 3px rgba(0,0,0,0.1);'
)
content = content.replace(
    'background: linear-gradient(135deg, #238636 0%, #2ea043 100%); color: white; border: none; padding: 11px; border-radius: 6px; font-size: 14px; font-weight: 700; cursor: pointer; box-shadow: 0 4px 16px rgba(46, 160, 67, 0.3);',
    'background: var(--brand-blue); color: white; border: none; padding: 10px; border-radius: 6px; font-size: 13px; font-weight: 700; cursor: pointer; box-shadow: 0 1px 3px rgba(0,0,0,0.1);'
)

# =========================================================================
# 6. Mobile Stock Card in candidate list: Warm theme palette
# =========================================================================
# Fix mobile stock card CSS
content = content.replace(
    '.mobile-stock-card {\n            background: #ffffff;\n            border: 1px solid #e2e8f0;',
    '.mobile-stock-card {\n            background: var(--bg-surface);\n            border: 1px solid var(--border-default);'
)
content = content.replace(
    '.m-stock-name {\n            font-size: 15px;\n            font-weight: 800;\n            color: #0f172a;',
    '.m-stock-name {\n            font-size: 14px;\n            font-weight: 800;\n            color: var(--text-primary);'
)

# Fix mobile card generator tags in renderCandidateTable:
content = content.replace(
    '<div class="m-card-tag" style="color:#cbd5e1;font-size:11px;">',
    '<div class="m-card-tag" style="color:var(--text-secondary);font-size:11px;">'
)
content = content.replace(
    '<div class="m-card-tag" style="color:#ffe600;font-weight:700;font-family:var(--font-mono);font-size:11px;">',
    '<div class="m-card-tag" style="color:var(--warning-amber);font-weight:700;font-family:var(--font-mono);font-size:11px;">'
)
content = content.replace(
    'background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);padding:1px 6px;border-radius:4px;',
    'background:var(--bg-inset);border:1px solid var(--border-default);padding:1px 6px;border-radius:4px;color:var(--text-secondary);'
)
content = content.replace(
    '<b style="color:#00f59b;font-family:var(--font-mono);">&yen;${stopLossPrice}</b>',
    '<b style="color:var(--down-green);font-family:var(--font-mono);">&yen;${stopLossPrice}</b>'
)
content = content.replace(
    '<span style="font-size:10px;color:#94a3b8;">🎯 针对: <b style="color:#f8fafc;">${accName}</b></span>',
    '<span style="font-size:10px;color:var(--text-muted);">🎯 针对: <b style="color:var(--text-primary);">${accName}</b></span>'
)
content = content.replace(
    'actBadgeStyled = `<span style="background:rgba(255,77,79,0.2);color:#ff4d4f;border:1px solid rgba(255,77,79,0.5);padding:2px 8px;border-radius:12px;font-size:11px;font-weight:800;">${isLimit ? \'⚠️ 仓位达限\' : \'⚠️ 资金不足\'}</span>`;',
    'actBadgeStyled = `<span style="background:var(--up-red-bg);color:var(--up-red);border:1px solid var(--up-red-border);padding:2px 8px;border-radius:12px;font-size:11px;font-weight:800;">${isLimit ? \'⚠️ 仓位达限\' : \'⚠️ 资金不足\'}</span>`;'
)
content = content.replace(
    'actBadgeStyled = \'<span style="background:rgba(255,24,36,0.18);color:#ff1824;border:1px solid rgba(255,24,36,0.45);padding:2px 8px;border-radius:12px;font-size:11px;font-weight:800;">买入进攻</span>\';',
    'actBadgeStyled = \'<span style="background:var(--up-red-bg);color:var(--up-red);border:1px solid var(--up-red-border);padding:2px 8px;border-radius:12px;font-size:11px;font-weight:800;">买入进攻</span>\';'
)
content = content.replace(
    'actBadgeStyled = \'<span style="background:rgba(255,230,0,0.16);color:#ffe600;border:1px solid rgba(255,230,0,0.4);padding:2px 8px;border-radius:12px;font-size:11px;font-weight:800;">保持观望</span>\';',
    'actBadgeStyled = \'<span style="background:var(--warning-amber-bg);color:var(--warning-amber);border:1px solid var(--warning-amber-border);padding:2px 8px;border-radius:12px;font-size:11px;font-weight:800;">保持观望</span>\';'
)

# Save updated file
with open(html_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated index.html successfully, new length:", len(content))
