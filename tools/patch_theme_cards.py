import os
import re

html_path = r'd:\量化交易\ths-trading-ai\static\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add favicon link to <head> if not present
if '<link rel="icon"' not in content:
    target = '<title>同花顺模拟比赛AI交易系统</title>'
    replacement = '<link rel="icon" type="image/x-icon" href="/favicon.ico">\n    <link rel="shortcut icon" type="image/x-icon" href="/favicon.ico">\n    ' + target
    if target in content:
        content = content.replace(target, replacement, 1)
        print("Added favicon link to <head>")
    else:
        print("Warning: <title> not found for favicon link insertion")

# 2. Update HETERO_ACCOUNTS_META, getAccountMeta, renderMultiAccountMatrix, toggleConsolidatedView around line 6220
# Let's inspect the target block in content
block_pattern = re.compile(
    r'(\s*// =+ 3 账户异构策略元数据与矩阵渲染 =+.*?'
    r'function toggleConsolidatedView\(\)\s*\{.*?'
    r'function updateConsolidatedData\(\))',
    re.DOTALL
)

m = block_pattern.search(content)
if not m:
    print("Could not find HETERO_ACCOUNTS_META block with regex, let's search substrings...")
else:
    print("Found HETERO_ACCOUNTS_META block, length:", len(m.group(0)))

new_block = '''        // ==================== 3 账户异构策略元数据与矩阵渲染 (统一机构浅色调) ====================
        const HETERO_ACCOUNTS_META = {
            1: {
                id: 1,
                code: "ACC-01",
                name: "账户1",
                label: "先锋进攻",
                style: "高弹性连板 / 冲击龙头",
                badge: "⚡ 先锋进攻",
                tagBg: "#fee2e2",
                tagBorder: "#fca5a5",
                tagColor: "#dc2626",
                targetPos: "60%~80%",
                maxSingle: "30%",
                themeColor: "#dc2626"
            },
            2: {
                id: 2,
                code: "ACC-02",
                name: "账户2",
                label: "趋势中军",
                style: "主升浪持股 / 突破回踩",
                badge: "📈 趋势中军",
                tagBg: "#eff6ff",
                tagBorder: "#bfdbfe",
                tagColor: "#2563eb",
                targetPos: "40%~60%",
                maxSingle: "25%",
                themeColor: "#2563eb"
            },
            3: {
                id: 3,
                code: "ACC-03",
                name: "账户3",
                label: "防守套利",
                style: "量缩背离 / 极限低吸反转",
                badge: "🛡️ 防守套利",
                tagBg: "#fef3c7",
                tagBorder: "#fde68a",
                tagColor: "#b45309",
                targetPos: "20%~40%",
                maxSingle: "20%",
                themeColor: "#b45309"
            }
        };

        function getAccountMeta(accId, index = 0) {
            if (HETERO_ACCOUNTS_META[accId]) {
                return HETERO_ACCOUNTS_META[accId];
            }
            const fallbackKeys = [1, 2, 3];
            const key = fallbackKeys[index % 3];
            return {
                ...HETERO_ACCOUNTS_META[key],
                id: accId,
                name: `账户${accId}`
            };
        }

        function renderMultiAccountMatrix() {
            const grid = document.getElementById('accountCardsGrid');
            if (!grid) return;

            if (typeof syncAndRenderMultiAccounts === 'function') {
                syncAndRenderMultiAccounts();
                return;
            }

            if (!allAccounts || !allAccounts.length) {
                grid.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:12px;font-size:11px;grid-column:1/-1;">暂无绑定账户</div>';
                return;
            }

            const displayAccounts = allAccounts.slice(0, 3);
            const currentActiveId = window.activeAccountId || (allAccounts[0] && allAccounts[0].id);

            grid.innerHTML = displayAccounts.map((acc, idx) => {
                const meta = getAccountMeta(acc.id, idx);
                const isActive = (currentActiveId === acc.id) || (allAccounts.length === 1);

                const initialBal = Number(acc.initial_balance) || 200000;
                const totalAssets = Number(acc.total_assets || acc.balance) || initialBal;
                const available = Number(acc.ky_fund || acc.balance) || 0;
                const marketValue = totalAssets - available;
                const posRatio = totalAssets > 0 ? Math.min(100, Math.max(0, (marketValue / totalAssets) * 100)) : 0;

                let profit = (acc.total_profit !== undefined && Number(acc.total_profit) !== 0)
                    ? Number(acc.total_profit)
                    : (totalAssets - initialBal);
                let profitRate = (acc.total_profit_rate !== undefined && acc.total_profit_rate !== null && Number(acc.total_profit_rate) !== 0)
                    ? Number(acc.total_profit_rate)
                    : ((profit / initialBal) * 100);

                const profitColor = profit >= 0 ? 'var(--up-red)' : 'var(--down-green)';
                const rateSign = profitRate > 0 ? '+' : '';

                const activeBorder = isActive
                    ? `border: 1.5px solid ${meta.themeColor}; border-top: 3px solid ${meta.themeColor}; background: var(--bg-surface); box-shadow: 0 2px 8px rgba(0,0,0,0.06);`
                    : `border: 1px solid var(--border-default); border-top: 2px solid ${meta.tagBorder}; background: var(--bg-inset);`;

                const rawName = acc.name ? String(acc.name) : String(acc.id);
                const dispName = (rawName.startsWith('账户') || rawName.startsWith('#')) ? rawName : `账户${rawName}`;
                const dispAsset = (Math.abs(totalAssets) >= 10000) ? (totalAssets / 10000).toFixed(2) + '万' : totalAssets.toFixed(2);
                const dispAvail = (Math.abs(available) >= 10000) ? (available / 10000).toFixed(2) + '万' : available.toFixed(2);

                return `
                    <div onclick="switchAccount(${acc.id})" style="${activeBorder} border-radius: 6px; padding: 7px 8px; cursor: pointer; transition: all 0.2s; display: flex; flex-direction: column; justify-content: space-between; min-width: 0;" onmouseover="this.style.transform='translateY(-1px)'" onmouseout="this.style.transform='none'">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                            <div style="display: flex; align-items: center; gap: 4px; min-width: 0;">
                                <span style="font-size: 11px; font-weight: 800; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                                    ${dispName}
                                </span>
                                ${isActive ? '<span style="background: #16a34a; color: #fff; font-size: 8px; font-weight: 800; padding: 0 3px; border-radius: 2px;">活跃</span>' : ''}
                            </div>
                            <span style="font-size: 9px; color: ${meta.tagColor}; font-weight: 700; background: ${meta.tagBg}; padding: 1px 4px; border-radius: 3px; white-space: nowrap;">
                                ${meta.badge}
                            </span>
                        </div>

                        <div style="display: flex; justify-content: space-between; align-items: baseline; margin: 2px 0;">
                            <div style="font-size: 13px; font-weight: 800; color: var(--text-primary); font-family: var(--font-mono); white-space: nowrap;">
                                ¥${dispAsset}
                            </div>
                            <div style="font-size: 10px; font-weight: 700; font-family: var(--font-mono); color: ${profitColor}; white-space: nowrap;">
                                ${rateSign}${profitRate.toFixed(1)}%
                            </div>
                        </div>

                        <!-- 仓位进度条 -->
                        <div style="width: 100%; background: rgba(0,0,0,0.06); border-radius: 2px; height: 4px; overflow: hidden; margin: 2px 0;" title="实际仓位: ${posRatio.toFixed(1)}%">
                            <div style="width: ${posRatio.toFixed(1)}%; background: ${meta.themeColor}; height: 100%; border-radius: 2px; transition: width 0.3s;"></div>
                        </div>

                        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 9px; color: var(--text-muted); font-family: var(--font-mono);">
                            <span>仓位 ${posRatio.toFixed(1)}%</span>
                            <span>目标${meta.targetPos}</span>
                        </div>
                    </div>
                `;
            }).join('');

            updateConsolidatedData();
        }

        function toggleConsolidatedView() {
            const box = document.getElementById('consolidatedSummaryBox');
            const btn = document.getElementById('btnConsolidatedView');
            if (!box) return;
            const isOpen = (box.style.display !== 'none' && box.style.display !== '');
            if (!isOpen) {
                box.style.display = 'block';
                if (btn) {
                    btn.style.background = '#dbeafe';
                    btn.style.borderColor = '#93c5fd';
                    btn.style.color = '#1d4ed8';
                    btn.textContent = '收起合并总览';
                }
                updateConsolidatedData();
            } else {
                box.style.display = 'none';
                if (btn) {
                    btn.style.background = '#eff6ff';
                    btn.style.borderColor = '#bfdbfe';
                    btn.style.color = '#2563eb';
                    btn.textContent = '📊 全局合并总览';
                }
            }
        }

        function updateConsolidatedData()'''

if m:
    content = content[:m.start()] + new_block + content[m.end():]
    print("Replaced HETERO_ACCOUNTS_META block successfully")

# 3. Update syncAndRenderMultiAccounts (lines around 9380) so that it also renders the position bar and target pos in the exact same format
sync_card_old = '''                return `
                    <div onclick="switchAccount(${acc.id})" style="${activeStyle} border-radius: 6px; padding: 7px 8px; cursor: pointer; transition: all 0.2s; min-width: 0;" onmouseover="this.style.transform=\'translateY(-1px)\'" onmouseout="this.style.transform=\'none\'">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 3px;">
                            <div style="display: flex; align-items: center; gap: 3px; min-width: 0;">
                                <span style="font-size: 11px; font-weight: 800; color: var(--text-primary); white-space: nowrap;">${dispName}</span>
                                ${isActive ? \'<span style="font-size: 8px; color: #16a34a; font-weight: 900;">●</span>\' : \'\'}
                            </div>
                            <span style="font-size: 9px; font-weight: 700; color: ${cfg.themeColor}; background: ${cfg.bgGrad}; padding: 1px 4px; border-radius: 3px; white-space: nowrap;">${cfg.label}</span>
                        </div>
                        <div style="font-size: 13px; font-weight: 800; color: var(--text-primary); font-family: var(--font-mono); margin-bottom: 2px; white-space: nowrap;">
                            ¥${fmtMoney(assets)}
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 10px; white-space: nowrap;">
                            <span style="color: var(--text-muted);">余 ¥${fmtMoney(available)}</span>
                            <span style="color: ${profit >= 0 ? \'var(--up-red)\' : \'var(--down-green)\'}; font-weight: 700; font-family: var(--font-mono);">${sign}${profitRate.toFixed(1)}%</span>
                        </div>
                    </div>
                `;'''

sync_card_new = '''                const marketVal = assets - available;
                const posRatio = assets > 0 ? Math.min(100, Math.max(0, (marketVal / assets) * 100)) : 0;
                const targetRange = ['60%~80%', '40%~60%', '20%~40%'][idx % 3] || '30%~50%';

                return `
                    <div onclick="switchAccount(${acc.id})" style="${activeStyle} border-radius: 6px; padding: 7px 8px; cursor: pointer; transition: all 0.2s; display: flex; flex-direction: column; justify-content: space-between; min-width: 0;" onmouseover="this.style.transform=\'translateY(-1px)\'" onmouseout="this.style.transform=\'none\'">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 3px;">
                            <div style="display: flex; align-items: center; gap: 4px; min-width: 0;">
                                <span style="font-size: 11px; font-weight: 800; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${dispName}</span>
                                ${isActive ? \'<span style="background: #16a34a; color: #fff; font-size: 8px; font-weight: 800; padding: 0 3px; border-radius: 2px;">活跃</span>\' : \'\'}
                            </div>
                            <span style="font-size: 9px; font-weight: 700; color: ${cfg.themeColor}; background: ${cfg.bgGrad}; padding: 1px 4px; border-radius: 3px; white-space: nowrap;">${cfg.label}</span>
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: baseline; margin: 2px 0;">
                            <div style="font-size: 13px; font-weight: 800; color: var(--text-primary); font-family: var(--font-mono); white-space: nowrap;">
                                ¥${fmtMoney(assets)}
                            </div>
                            <div style="font-size: 10px; font-weight: 700; font-family: var(--font-mono); color: ${profit >= 0 ? \'var(--up-red)\' : \'var(--down-green)\'}; white-space: nowrap;">
                                ${sign}${profitRate.toFixed(1)}%
                            </div>
                        </div>
                        <!-- 仓位进度条 -->
                        <div style="width: 100%; background: rgba(0,0,0,0.06); border-radius: 2px; height: 4px; overflow: hidden; margin: 2px 0;" title="实际仓位: ${posRatio.toFixed(1)}%">
                            <div style="width: ${posRatio.toFixed(1)}%; background: ${cfg.themeColor}; height: 100%; border-radius: 2px; transition: width 0.3s;"></div>
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 9px; color: var(--text-muted); font-family: var(--font-mono);">
                            <span>仓位 ${posRatio.toFixed(1)}%</span>
                            <span>目标${targetRange}</span>
                        </div>
                    </div>
                `;'''

if sync_card_old in content:
    content = content.replace(sync_card_old, sync_card_new, 1)
    print("Replaced syncAndRenderMultiAccounts card template successfully")
else:
    print("Warning: sync_card_old not matched exactly, looking for partial match...")

# 4. Check toggleConsolidatedView near line 9566
toggle_old = '''    function toggleConsolidatedView() {
        _isConsolidatedViewOpen = !_isConsolidatedViewOpen;
        const box = document.getElementById('consolidatedSummaryBox');
        const btn = document.getElementById('btnConsolidatedView');
        if (box) box.style.display = _isConsolidatedViewOpen ? 'block' : 'none';
        if (btn) {
            btn.style.background = _isConsolidatedViewOpen ? 'rgba(255,24,36,0.28)' : 'rgba(255,24,36,0.12)';
            btn.textContent = _isConsolidatedViewOpen ? '收起合并总览' : '📊 全局合并总览';
        }
    }'''

toggle_new = '''    function toggleConsolidatedView() {
        _isConsolidatedViewOpen = !_isConsolidatedViewOpen;
        const box = document.getElementById('consolidatedSummaryBox');
        const btn = document.getElementById('btnConsolidatedView');
        if (box) box.style.display = _isConsolidatedViewOpen ? 'block' : 'none';
        if (btn) {
            btn.style.background = _isConsolidatedViewOpen ? '#dbeafe' : '#eff6ff';
            btn.style.borderColor = _isConsolidatedViewOpen ? '#93c5fd' : '#bfdbfe';
            btn.style.color = _isConsolidatedViewOpen ? '#1d4ed8' : '#2563eb';
            btn.textContent = _isConsolidatedViewOpen ? '收起合并总览' : '📊 全局合并总览';
        }
    }'''

if toggle_old in content:
    content = content.replace(toggle_old, toggle_new, 1)
    print("Replaced toggleConsolidatedView at 9566 successfully")

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated index.html successfully.")
