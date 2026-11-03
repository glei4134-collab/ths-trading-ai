html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Replace CSS variables range [2369:7106]
r_idx = content.find(':root')
dark_idx = content.find('[data-theme="dark"]', r_idx)
brace_count = 0
dark_end = dark_idx
for i in range(dark_idx, len(content)):
    if content[i] == '{':
        brace_count += 1
    elif content[i] == '}':
        brace_count -= 1
        if brace_count == 0:
            dark_end = i + 1
            break

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
        }'''

content = content[:r_idx] + warm_css_vars + content[dark_end:]
print(f"Replaced root theme block ({r_idx} to {dark_end}) with Warm Beige")

# 2. Fix all mismatched buttons
replacements = [
    # Button in AI config modal
    (
        'style="flex:1; background:#30363d;">取消</button>',
        'style="flex:1; background:var(--bg-inset); border:1px solid var(--border-default); color:var(--text-secondary); padding:8px; border-radius:6px; font-weight:600; cursor:pointer;">取消</button>'
    ),
    (
        '<button class="connect-btn" onclick="saveAIConfig()" style="flex:1;">保存</button>',
        '<button class="connect-btn" onclick="saveAIConfig()" style="flex:1; background:var(--brand-blue); border:none; color:white; padding:8px; border-radius:6px; font-weight:700; cursor:pointer;">保存</button>'
    ),
    # Cancel button in modal
    (
        'style="flex:1;background:#21262d;border:1px solid #30363d;color:#cbd5e1;padding:8px;border-radius:6px;cursor:pointer;font-size:12px;" onclick="this.closest(\'.modal-overlay\').remove()"',
        'style="flex:1;background:var(--bg-inset);border:1px solid var(--border-default);color:var(--text-secondary);padding:8px;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600;" onclick="this.closest(\'.modal-overlay\').remove()"'
    ),
    # Ledger refresh and mode buttons
    (
        'style="background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:4px 8px;border-radius:6px;font-size:11px;cursor:pointer;">刷新</button>',
        'style="background:var(--bg-inset);border:1px solid var(--border-default);color:var(--text-secondary);padding:4px 8px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;">刷新</button>'
    ),
    (
        'style="background:#21262d;border:1px solid #388bfd;color:#79c0ff;padding:3px 9px;border-radius:6px;font-size:11px;cursor:pointer;"',
        'style="background:var(--brand-blue-bg);border:1px solid var(--brand-blue-border);color:var(--brand-blue);padding:3px 9px;border-radius:6px;font-size:11px;font-weight:700;cursor:pointer;"'
    ),
    # Trade confirm modal buttons & background
    (
        '<div class="modal" style="max-width:440px;background:#0d1117;border:1px solid rgba(255, 24, 36,0.3);border-radius:12px;box-shadow:0 24px 64px rgba(0,0,0,0.85);overflow:hidden;">',
        '<div class="modal" style="max-width:440px;background:var(--bg-surface);border:1px solid var(--border-default);border-radius:12px;box-shadow:0 24px 64px rgba(70,50,30,0.18);overflow:hidden;">'
    ),
    (
        '<div class="modal-header" style="background:linear-gradient(135deg, #161f30 0%, #0f1624 100%);border-bottom:1px solid #30363d;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;">',
        '<div class="modal-header" style="background:var(--bg-surface);border-bottom:1px solid var(--border-default);padding:12px 16px;display:flex;justify-content:space-between;align-items:center;">'
    ),
    (
        '<div class="modal-footer" style="padding:10px 16px;border-top:1px solid #21262d;background:#0d1117;display:flex;gap:8px;">',
        '<div class="modal-footer" style="padding:10px 16px;border-top:1px solid var(--border-default);background:var(--bg-inset);display:flex;gap:8px;">'
    ),
    (
        'style="display:flex;gap:6px;margin-bottom:12px;background:rgba(0,0,0,0.4);padding:4px;border-radius:8px;border:1px solid rgba(255,255,255,0.08);"',
        'style="display:flex;gap:6px;margin-bottom:12px;background:var(--bg-inset);padding:4px;border-radius:8px;border:1px solid var(--border-default);"'
    ),
    # Batch order feedback modal
    (
        '<div class="modal" style="max-width:540px;background:#0d1117;border:1px solid rgba(255, 24, 36,0.4);border-radius:14px;box-shadow:0 24px 64px rgba(0,0,0,0.85);overflow:hidden;">',
        '<div class="modal" style="max-width:540px;background:var(--bg-surface);border:1px solid var(--border-default);border-radius:14px;box-shadow:0 24px 64px rgba(70,50,30,0.18);overflow:hidden;">'
    ),
    (
        '<div class="modal-header" style="background:linear-gradient(90deg, #161f30 0%, #0f1624 100%);border-bottom:1px solid #30363d;padding:14px 18px;display:flex;justify-content:space-between;align-items:center;">',
        '<div class="modal-header" style="background:var(--bg-surface);border-bottom:1px solid var(--border-default);padding:14px 18px;display:flex;justify-content:space-between;align-items:center;">'
    ),
    (
        '<div class="modal-footer" style="padding:10px 16px;border-top:1px solid #21262d;background:#0d1117;display:flex;justify-content:flex-end;">',
        '<div class="modal-footer" style="padding:10px 16px;border-top:1px solid var(--border-default);background:var(--bg-inset);display:flex;justify-content:flex-end;">'
    ),
    (
        '<tr style="border-bottom:1px solid rgba(255,255,255,0.04);font-size:12px;">\n                        <td style="padding:8px 12px;font-weight:700;color:#f8fafc;">${r.account_name}</td>',
        '<tr style="border-bottom:1px solid var(--border-subtle);font-size:12px;">\n                        <td style="padding:8px 12px;font-weight:700;color:var(--text-primary);">${r.account_name}</td>'
    ),
    (
        'style="background:#238636;color:white;border:none;padding:7px 18px;border-radius:6px;font-size:12px;font-weight:700;cursor:pointer;" onclick="this.closest(\'.modal-overlay\').remove()"',
        'style="background:var(--brand-blue);color:white;border:none;padding:7px 18px;border-radius:6px;font-size:12px;font-weight:700;cursor:pointer;" onclick="this.closest(\'.modal-overlay\').remove()"'
    ),
    # Connect modal login tabs
    (
        'color: #94a3b8; border: none; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; transition: all 0.2s;">\n                        📱 手机验证码',
        'color: var(--text-secondary); border: none; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; transition: all 0.2s;">\n                        📱 手机验证码'
    ),
    (
        'color: #94a3b8; border: none; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; transition: all 0.2s;">\n                        🔑 账号密码',
        'color: var(--text-secondary); border: none; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; transition: all 0.2s;">\n                        🔑 账号密码'
    ),
    # Ledger modal currentWindowCard
    (
        '<div id="currentWindowCard" style="background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:8px 12px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;flex-shrink:0;gap:8px;">',
        '<div id="currentWindowCard" style="background:var(--bg-inset);border:1px solid var(--border-default);border-radius:8px;padding:8px 12px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;flex-shrink:0;gap:8px;">'
    ),
    (
        'color:#e6edf3;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">-',
        'color:var(--text-primary);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">-'
    ),
    (
        '<strong id="ledgerEntryCount" style="color:#e6edf3;">0</strong>',
        '<strong id="ledgerEntryCount" style="color:var(--text-primary);">0</strong>'
    ),
    # Ledger modal header title
    (
        '<span style="font-size:${isMob ? \'14px\' : \'17px\'};font-weight:700;color:#ff1824;letter-spacing:0.3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block;">🕒 每日时序执行审计账本</span>',
        '<span style="font-size:${isMob ? \'14px\' : \'17px\'};font-weight:700;color:var(--text-primary);letter-spacing:0.3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block;">🕒 每日时序执行审计账本</span>'
    ),
    (
        'border-bottom:1px solid #21262d;padding-bottom:10px;flex-shrink:0;',
        'border-bottom:1px solid var(--border-default);padding-bottom:10px;flex-shrink:0;'
    ),
    # Perf modal close button
    (
        'style="background:rgba(255,24,36,0.15);border:1px solid #ff1824;color:#ff1824;padding:5px 16px;border-radius:4px;font-size:12px;cursor:pointer;"',
        'style="background:var(--bg-inset);border:1px solid var(--border-default);color:var(--text-secondary);padding:5px 16px;border-radius:4px;font-size:12px;font-weight:600;cursor:pointer;"'
    ),
    # Candidate table header button '全自动多因子扫描'
    (
        'style="background:rgba(0, 210, 255, 0.12);color:#00d2ff;border:1px solid rgba(0, 210, 255, 0.35);font-size:10px;padding:2px 8px;border-radius:4px;font-weight:700;"',
        'style="background:var(--brand-blue-bg);color:var(--brand-blue);border:1px solid var(--brand-blue-border);font-size:10px;padding:2px 8px;border-radius:4px;font-weight:700;"'
    ),
    # Candidate table '震荡自适应'
    (
        'style="font-size:10px;background:rgba(255, 24, 36,0.15);color:#ff1824;padding:1px 6px;border-radius:4px;font-weight:600;"',
        'style="font-size:10px;background:var(--warning-amber-bg);color:var(--warning-amber);border:1px solid var(--warning-amber-border);padding:1px 6px;border-radius:4px;font-weight:600;"'
    )
]

for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        print(f"Replaced button/markup snippet: {old[:40]}...")
    else:
        print(f"Notice: snippet not found: {old[:40]}...")

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Saved index.html successfully.")
