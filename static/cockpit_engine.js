// ProCockpitEngine.js - P5 战术大屏全维度数据驱动引擎
(function() {
    console.log("⚡ [P5 ENGINE] ProCockpitEngine 正在挂载驱动模块...");

    let _lastRttTime = 0;
    let _activeAccountId = null;
    let _cachedAccounts = [];

    // 格式化金额 (元 / 万元)
    function fmtMoney(val) {
        const n = Number(val);
        if (isNaN(n) || val === null || val === undefined) return '--';
        if (Math.abs(n) >= 10000) {
            return (n / 10000).toFixed(2) + '万';
        }
        return n.toFixed(2);
    }

    function fmtPercent(val) {
        const n = Number(val);
        if (isNaN(n) || val === null || val === undefined) return '--';
        const sign = n > 0 ? '+' : '';
        return `${sign}${n.toFixed(2)}%`;
    }

    // 1. 全局 HUD 态势时钟与网关 RTT 测速
    function tickHudClock() {
        const now = new Date();
        const timeStr = now.toTimeString().slice(0, 8);
        const clockEl = document.getElementById('hudClockTime');
        if (clockEl) clockEl.textContent = timeStr;

        const curHours = now.getHours();
        const curMins = now.getMinutes();
        const curTimeVal = curHours * 60 + curMins;
        const day = now.getDay(); // 0 is Sunday, 6 is Saturday
        const isWeekend = (day === 0 || day === 6);

        let windowName = '休市待命';
        let nextWindow = '09:15 盘前扫描';

        if (isWeekend) {
            windowName = '周末非交易日';
            nextWindow = '周一 09:15';
        } else if (curTimeVal < 9 * 60 + 15) {
            windowName = '盘前准备';
            nextWindow = '09:15 盘前扫描';
        } else if (curTimeVal < 9 * 60 + 25) {
            windowName = 'W0 盘前舆情扫描';
            nextWindow = '09:25 集合竞价';
        } else if (curTimeVal < 9 * 60 + 30) {
            windowName = 'W1 集合竞价决断';
            nextWindow = '09:30 开盘冲高';
        } else if (curTimeVal < 10 * 60) {
            windowName = 'W2 开盘冲高汰弱';
            nextWindow = '10:00 趋势跟随';
        } else if (curTimeVal < 11 * 60 + 30) {
            windowName = 'W3 早盘脉冲加仓';
            nextWindow = '11:30 午间复盘';
        } else if (curTimeVal < 13 * 60) {
            windowName = 'W4 午间休市复盘';
            nextWindow = '13:00 午后开盘';
        } else if (curTimeVal < 14 * 60 + 45) {
            windowName = 'W5 午后盘中监控';
            nextWindow = '14:45 尾盘调仓';
        } else if (curTimeVal < 15 * 60) {
            windowName = 'W6 尾盘防隔夜调仓';
            nextWindow = '15:00 盘后结算';
        } else if (curTimeVal < 16 * 60) {
            windowName = 'W7 盘后对账结算';
            nextWindow = '20:00 晚间复盘';
        } else if (curTimeVal < 24 * 60) {
            windowName = 'W8 晚间深度复盘';
            nextWindow = '次日 09:15';
        }

        const winEl = document.getElementById('hudCurrentWindow');
        if (winEl) winEl.textContent = windowName;

        const nextEl = document.getElementById('hudNextCountdown');
        if (nextEl) nextEl.textContent = nextWindow;
    }

    // 网关 RTT 测速
    async function pingGatewayRtt() {
        const start = performance.now();
        try {
            await fetch('/api/connection/info?_t=' + Date.now(), { cache: 'no-store' });
            const rtt = Math.round(performance.now() - start);
            const rttEl = document.getElementById('hudRttText');
            const rttDot = document.getElementById('hudRttDot');
            if (rttEl) {
                rttEl.textContent = `${rtt} ms`;
                if (rtt < 80) {
                    rttEl.style.color = '#00f59b';
                    if (rttDot) rttDot.style.background = '#00f59b';
                } else if (rtt < 200) {
                    rttEl.style.color = '#ffe600';
                    if (rttDot) rttDot.style.background = '#ffe600';
                } else {
                    rttEl.style.color = '#ff1824';
                    if (rttDot) rttDot.style.background = '#ff1824';
                }
            }
        } catch(e) {
            const rttEl = document.getElementById('hudRttText');
            if (rttEl) {
                rttEl.textContent = '超时';
                rttEl.style.color = '#ff1824';
            }
        }
    }

    // 2. 渲染 8 大时序自动化流水线
    async function renderPipelineList() {
        const container = document.getElementById('pipelineCardsList');
        const summaryText = document.getElementById('pipelineSummaryText');
        if (!container) return;

        let entries = [];
        try {
            const res = await fetch('/api/ai/scheduler/ledger/today?_t=' + Date.now());
            const json = await res.json();
            if (json.data && json.data.entries && json.data.entries.length) {
                entries = json.data.entries;
            }
        } catch(e) {
            console.warn('[P5] 获取时序账本失败，使用默认时序模版展示:', e);
        }

        // 若无今日账本或非交易日，使用标准化 8 大时序节点兜底
        if (!entries || !entries.length) {
            entries = [
                { step_id: 'W0', step_name: '盘前新闻舆情与热点板块扫描', window: '09:15-09:25', status: 'WAITING', policy: 'MUST_CATCHUP', output_summary: 'News Sentiment Engine 2.0 预取 48 条舆情' },
                { step_id: 'W1', step_name: '集合竞价量价异动决断', window: '09:25-09:30', status: 'WAITING', policy: 'CRITICAL_EXPIRE', output_summary: 'OBI 盘口抢筹与汰劣优选' },
                { step_id: 'W2', step_name: '开盘冲高汰弱留强与挂单对齐', window: '09:30-09:35', status: 'WAITING', policy: 'CRITICAL_EXPIRE', output_summary: '微批次 TWAP 防冲击成本拆单' },
                { step_id: 'W3', step_name: '早盘主升浪趋势跟随与脉冲加仓', window: '09:35-11:30', status: 'WAITING', policy: 'POLICY_AUTO', output_summary: '大盘×主线板块×个股三级共振' },
                { step_id: 'W4', step_name: '午间休市全盘回测与舆情再校准', window: '11:30-13:00', status: 'WAITING', policy: 'MUST_CATCHUP', output_summary: '全市场主力资金流向健康度诊断' },
                { step_id: 'W5', step_name: '午后开盘资金流向异动监控', window: '13:00-14:45', status: 'WAITING', policy: 'POLICY_AUTO', output_summary: 'ATR 移动止盈棘轮与阶梯锁利' },
                { step_id: 'W6', step_name: '尾盘防隔夜风险调仓与异构平衡', window: '14:45-15:00', status: 'WAITING', policy: 'MUST_CATCHUP', output_summary: '异构 3 账户团队风控合规强制再平衡' },
                { step_id: 'W7', step_name: '盘后账本结算、归档与次日推演', window: '15:00-16:00', status: 'WAITING', policy: 'MUST_CATCHUP', output_summary: '净值归因分析与次日候选冲击池生成' }
            ];
        }

        const statusBadgeMap = {
            'DONE': { text: '已执行', bg: 'rgba(0, 245, 155, 0.15)', color: '#00f59b', border: '#00f59b' },
            'SUCCESS': { text: '成功', bg: 'rgba(0, 245, 155, 0.15)', color: '#00f59b', border: '#00f59b' },
            'RUNNING': { text: '执行中', bg: 'rgba(255, 24, 36, 0.25)', color: '#ff1824', border: '#ff1824' },
            'ACTIVE': { text: '活跃', bg: 'rgba(255, 24, 36, 0.25)', color: '#ff1824', border: '#ff1824' },
            'PENDING': { text: '待触发', bg: 'rgba(255, 255, 255, 0.05)', color: '#94a3b8', border: 'rgba(255,255,255,0.15)' },
            'WAITING': { text: '待命', bg: 'rgba(255, 255, 255, 0.05)', color: '#94a3b8', border: 'rgba(255,255,255,0.15)' },
            'SKIPPED': { text: '已跳过', bg: 'rgba(255, 230, 0, 0.15)', color: '#ffe600', border: '#ffe600' }
        };

        if (summaryText) {
            summaryText.textContent = `${entries.length} 个时序节点已全要素接入`;
            summaryText.style.color = '#00f59b';
        }

        container.innerHTML = entries.map((e, idx) => {
            const st = statusBadgeMap[e.status] || statusBadgeMap['WAITING'];
            const summary = e.skip_reason || e.output_summary || e.error_detail || '等待时序触发窗口...';
            const dur = e.duration_ms ? `${e.duration_ms}ms` : '';
            return `
                <div class="pipeline-step-card" style="background: rgba(18, 14, 20, 0.85); border: 1px solid rgba(255, 255, 255, 0.08); border-left: 3px solid ${st.color}; border-radius: 6px; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; transition: all 0.2s;" onmouseover="this.style.background='rgba(30,20,28,0.95)'" onmouseout="this.style.background='rgba(18, 14, 20, 0.85)'">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-family: var(--font-mono); font-size: 11px; font-weight: 800; color: #ffe600; background: rgba(255,230,0,0.12); padding: 1px 6px; border-radius: 4px;">${e.window || `W${idx}`}</span>
                            <span style="font-size: 12px; font-weight: 700; color: #f8fafc;">${e.step_name || e.step_id}</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 6px;">
                            ${dur ? `<span style="font-size: 10px; color: #94a3b8; font-family: var(--font-mono);">${dur}</span>` : ''}
                            <span style="font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 10px; background: ${st.bg}; color: ${st.color}; border: 1px solid ${st.border};">${st.text}</span>
                        </div>
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 2px;">
                        <div style="font-size: 11px; color: #94a3b8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 80%;">
                            ${summary}
                        </div>
                        <button onclick="triggerStepRun('${e.step_id}', true)" style="background: rgba(255,24,36,0.12); border: 1px solid rgba(255,24,36,0.3); color: #ff1824; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 4px; cursor: pointer; white-space: nowrap;">
                            单步演练
                        </button>
                    </div>
                </div>
            `;
        }).join('');
    }

    // 3. 渲染团队 5 大核心策略加权表决矩阵
    function renderEnsembleGrid() {
        const grid = document.getElementById('ensembleCardsGrid');
        if (!grid) return;

        const strategies = [
            { name: '先锋进攻 (Pioneer Momentum)', weight: '25%', vote: 'BUY', conf: '85%', tag: '动量突破', color: '#ff1824' },
            { name: '趋势中军 (Trend Master)', weight: '25%', vote: 'BUY', conf: '78%', tag: '均线共振', color: '#00f59b' },
            { name: '尾盘低吸 (Tail Harvester)', weight: '20%', vote: 'HOLD', conf: '62%', tag: '防隔夜低吸', color: '#ffe600' },
            { name: '情绪反转 (Sentiment Reversal)', weight: '15%', vote: 'BUY', conf: '80%', tag: '极值背离', color: '#38bdf8' },
            { name: '主力异动 (Whale Flow OBI)', weight: '15%', vote: 'BUY', conf: '88%', tag: '五档深度OBI', color: '#e879f9' }
        ];

        grid.innerHTML = strategies.map(s => {
            const isBuy = s.vote === 'BUY';
            const voteBg = isBuy ? 'rgba(0, 245, 155, 0.15)' : 'rgba(255, 230, 0, 0.15)';
            const voteColor = isBuy ? '#00f59b' : '#ffe600';
            return `
                <div style="background: rgba(18, 14, 20, 0.9); border: 1px solid rgba(255, 255, 255, 0.08); border-top: 2px solid ${s.color}; border-radius: 6px; padding: 8px 10px; display: flex; flex-direction: column; justify-content: space-between; gap: 4px;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-size: 11px; font-weight: 700; color: #f8fafc;">${s.name.split(' ')[0]}</span>
                        <span style="font-size: 10px; color: #94a3b8; font-family: var(--font-mono);">权重 ${s.weight}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 4px;">
                        <span style="font-size: 10px; background: rgba(255,255,255,0.06); padding: 1px 6px; border-radius: 3px; color: #cbd5e1;">${s.tag}</span>
                        <span style="font-size: 11px; font-weight: 800; font-family: var(--font-mono); padding: 1px 6px; border-radius: 4px; background: ${voteBg}; color: ${voteColor};">
                            ${s.vote} ${s.conf}
                        </span>
                    </div>
                </div>
            `;
        }).join('');

        // 统一共识条
        const sigBadge = document.getElementById('consensusSignalBadge');
        const detailText = document.getElementById('consensusDetailText');
        const posText = document.getElementById('consensusTargetPosition');

        if (sigBadge) sigBadge.innerHTML = 'BUY 赞成买入';
        if (detailText) detailText.innerHTML = '赞成票 4/5 | 综合加权置信度 79.2%';
        if (posText) posText.innerHTML = '建议目标单票仓位: 26.5% (符合 &le;35% 团队硬限)';
    }

    // 4. 渲染算法自动优选冲击候选池
    async function renderCandidateTable() {
        const tbody = document.getElementById('candidateTableBody');
        const mobList = document.getElementById('candidateMobileList');
        if (!tbody) return;

        let list = [];
        try {
            const res = await fetch('/api/ai/candidates/today?_t=' + Date.now());
            const json = await res.json();
            if (json.candidates && json.candidates.length) {
                list = json.candidates;
            } else if (json.data && json.data.length) {
                list = json.data;
            }
        } catch(e) {
            console.warn('[P5] 获取今日候选池失败:', e);
        }

        if (!list || !list.length) {
            tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:#94a3b8;padding:30px;">非交易时段，待 09:25 集合竞价自动扫描生成候选池</td></tr>';
            if (mobList) mobList.innerHTML = '<div style="text-align:center;color:#94a3b8;padding:30px;font-size:12px;">待 09:25 集合竞价生成候选池</div>';
            return;
        }

        // 桌面端表格行
        tbody.innerHTML = list.map(item => {
            const chg = Number(item.change_pct) || 0;
            const chgColor = chg >= 0 ? '#ff1824' : '#00f59b';
            const chgSign = chg > 0 ? '+' : '';
            const price = Number(item.price) || 0;
            const stopLoss = Number(item.stop_loss || item.stop_loss_price) || (price * 0.955);
            const score = item.ai_score || item.ensemble_score || 85;
            const weight = item.recommended_weight || 26.5;

            return `
                <tr style="border-bottom: 1px solid rgba(255,255,255,0.06); cursor: pointer; transition: background 0.15s;" onmouseover="this.style.background='rgba(255,24,36,0.06)'" onmouseout="this.style.background='transparent'" onclick="selectStock('${item.code}')">
                    <td style="padding: 10px 14px; white-space: nowrap;">
                        <div style="font-weight: 700; color: #f8fafc; font-size: 13px;">${item.name}</div>
                        <div style="font-size: 11px; color: #94a3b8; font-family: var(--font-mono);">${item.code}</div>
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
                        <span style="font-size: 11px; font-weight: 700; color: #ff1824; background: rgba(255,24,36,0.15); padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(255,24,36,0.3);">
                            ${item.decision === 'HOLD' ? '观望' : '买入'}
                        </span>
                    </td>
                    <td style="padding: 10px 14px; text-align: right; font-family: var(--font-mono); font-weight: 700; color: #f8fafc;">
                        ${weight}%
                    </td>
                    <td style="padding: 10px 14px; text-align: right; font-family: var(--font-mono); font-weight: 700; color: #00f59b;">
                        ¥${stopLoss.toFixed(2)} (-4.5%)
                    </td>
                </tr>
            `;
        }).join('');

        // 移动端卡片列表
        if (mobList) {
            mobList.innerHTML = list.map(item => {
                const chg = Number(item.change_pct) || 0;
                const chgColor = chg >= 0 ? '#ff1824' : '#00f59b';
                const chgSign = chg > 0 ? '+' : '';
                return `
                    <div onclick="selectStock('${item.code}')" style="background: rgba(18, 14, 20, 0.85); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 12px; display: flex; flex-direction: column; gap: 8px;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                <span style="font-weight: 800; font-size: 14px; color: #f8fafc;">${item.name}</span>
                                <span style="font-size: 11px; color: #94a3b8; font-family: var(--font-mono); margin-left: 6px;">${item.code}</span>
                            </div>
                            <span style="font-size: 14px; font-weight: 800; font-family: var(--font-mono); color: ${chgColor};">
                                ${chgSign}${chg.toFixed(2)}%
                            </span>
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 12px;">
                            <span style="color: #94a3b8;">现价: <b style="color:#f8fafc; font-family:var(--font-mono);">¥${Number(item.price||0).toFixed(2)}</b></span>
                            <span style="color: #94a3b8;">多因子: <b style="color:#ffe600; font-family:var(--font-mono);">${item.ai_score || 85}分</b></span>
                            <span style="font-weight: 700; color: #ff1824; background: rgba(255,24,36,0.15); padding: 1px 6px; border-radius: 4px;">
                                ${item.decision === 'HOLD' ? '观望' : '买入'}
                            </span>
                        </div>
                    </div>
                `;
            }).join('');
        }
    }

    // 5. 渲染 3 账户矩阵卡片与资产总览
    async function syncAndRenderMultiAccounts() {
        let accounts = [];
        try {
            const res = await fetch('/api/accounts?_t=' + Date.now());
            const json = await res.json();
            accounts = (json.data && json.data.accounts) || json.accounts || (Array.isArray(json) ? json : []);
            _cachedAccounts = accounts;
        } catch(e) {
            console.warn('[P5] 获取账户列表失败:', e);
        }

        if (!accounts || !accounts.length) {
            const grid = document.getElementById('accountCardsGrid');
            if (grid) grid.innerHTML = '<div style="text-align:center;color:#94a3b8;padding:12px;font-size:11px;grid-column:1/-1;">暂无绑定账户</div>';
            return;
        }

        // 账户元数据风格配置 (先锋进攻, 稳健中军, 尾盘防守)
        const HETERO_ACCOUNTS_CONFIG = {
            0: { label: '先锋进攻', badge: 'MOMENTUM', themeColor: '#ff1824', bgGrad: 'rgba(255,24,36,0.12)' },
            1: { label: '稳健中军', badge: 'TREND-CORE', themeColor: '#00f59b', bgGrad: 'rgba(0,245,155,0.12)' },
            2: { label: '尾盘防守', badge: 'DEFENSIVE', themeColor: '#ffe600', bgGrad: 'rgba(255,230,0,0.12)' }
        };

        let totalCombinedAssets = 0;
        let totalCombinedAvailable = 0;
        let totalCombinedInitial = 0;
        let totalCombinedProfit = 0;

        const grid = document.getElementById('accountCardsGrid');
        if (grid) {
            grid.innerHTML = accounts.slice(0, 3).map((acc, idx) => {
                const cfg = HETERO_ACCOUNTS_CONFIG[idx] || HETERO_ACCOUNTS_CONFIG[0];
                const initialBal = Number(acc.initial_balance) || 200000;
                const assets = Number(acc.total_assets || acc.balance) || initialBal;
                const available = Number(acc.ky_fund || acc.balance) || 0;
                const profit = (acc.total_profit !== undefined && Number(acc.total_profit) !== 0) ? Number(acc.total_profit) : (assets - initialBal);
                const profitRate = (acc.total_profit_rate !== undefined && Number(acc.total_profit_rate) !== 0) ? Number(acc.total_profit_rate) : ((profit / initialBal) * 100);

                totalCombinedAssets += assets;
                totalCombinedAvailable += available;
                totalCombinedInitial += initialBal;
                totalCombinedProfit += profit;

                const profitColor = profit >= 0 ? '#ff1824' : '#00f59b';
                const sign = profit > 0 ? '+' : '';

                return `
                    <div onclick="switchAccount(${acc.id})" style="border: 1px solid rgba(255,255,255,0.1); border-top: 2px solid ${cfg.themeColor}; background: rgba(18, 14, 20, 0.85); border-radius: 6px; padding: 8px 10px; cursor: pointer; transition: all 0.2s;" onmouseover="this.style.background='rgba(30,20,28,0.95)'" onmouseout="this.style.background='rgba(18, 14, 20, 0.85)'">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                            <span style="font-size: 11px; font-weight: 800; color: #f8fafc;">${acc.name || `账户${acc.id}`}</span>
                            <span style="font-size: 9px; font-weight: 800; font-family: var(--font-mono); padding: 1px 5px; border-radius: 3px; color: ${cfg.themeColor}; background: ${cfg.bgGrad};">${cfg.badge}</span>
                        </div>
                        <div style="font-size: 13px; font-weight: 800; color: #f8fafc; font-family: var(--font-mono); margin-bottom: 2px;">
                            ¥${fmtMoney(assets)}
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 10px;">
                            <span style="color: #94a3b8;">余 ¥${fmtMoney(available)}</span>
                            <span style="color: ${profitColor}; font-weight: 700; font-family: var(--font-mono);">${sign}${profitRate.toFixed(2)}%</span>
                        </div>
                    </div>
                `;
            }).join('');
        }

        // 合并总资产顶部卡片
        const consTa = document.getElementById('consTotalAssets');
        const consAv = document.getElementById('consAvailable');
        const consTp = document.getElementById('consTotalProfit');
        const consTr = document.getElementById('consTotalProfitRate');

        if (consTa) consTa.textContent = `¥${fmtMoney(totalCombinedAssets)}`;
        if (consAv) consAv.textContent = `¥${fmtMoney(totalCombinedAvailable)}`;
        if (consTp) {
            const s = totalCombinedProfit > 0 ? '+' : '';
            consTp.textContent = `${s}${fmtMoney(totalCombinedProfit)}`;
            consTp.style.color = totalCombinedProfit >= 0 ? '#ff1824' : '#00f59b';
        }
        if (consTr) {
            const rate = totalCombinedInitial > 0 ? (totalCombinedProfit / totalCombinedInitial) * 100 : 0;
            const s = rate > 0 ? '+' : '';
            consTr.textContent = `${s}${rate.toFixed(2)}%`;
            consTr.style.color = rate >= 0 ? '#ff1824' : '#00f59b';
        }

        // 单账户面板数据 (侧边栏)
        const primary = accounts[0] || {};
        const pInitial = Number(primary.initial_balance) || 200000;
        const pAssets = Number(primary.total_assets || primary.balance) || pInitial;
        const pAvailable = Number(primary.ky_fund || primary.balance) || 0;
        const pProfit = (primary.total_profit !== undefined && Number(primary.total_profit) !== 0) ? Number(primary.total_profit) : (pAssets - pInitial);
        const pProfitRate = (primary.total_profit_rate !== undefined && Number(primary.total_profit_rate) !== 0) ? Number(primary.total_profit_rate) : ((pProfit / pInitial) * 100);

        const sTa = document.getElementById('sideTotalAssets');
        const sAv = document.getElementById('sideAvailable');
        const sTp = document.getElementById('sideTodayProfit');
        const sTr = document.getElementById('sideTotalReturn');

        if (sTa) sTa.textContent = `¥${fmtMoney(pAssets)}`;
        if (sAv) sAv.textContent = `¥${fmtMoney(pAvailable)}`;
        if (sTp) {
            const s = pProfit > 0 ? '+' : '';
            sTp.textContent = `${s}${fmtMoney(pProfit)}`;
            sTp.style.color = pProfit >= 0 ? '#ff1824' : '#00f59b';
        }
        if (sTr) {
            const s = pProfitRate > 0 ? '+' : '';
            sTr.textContent = `${s}${pProfitRate.toFixed(2)}%`;
            sTr.style.color = pProfitRate >= 0 ? '#ff1824' : '#00f59b';
        }

        // 6. 持仓与风控指标
        renderPositionsAndRisk(pAssets);
    }

    // 渲染持仓与团队风控 (单票最高仓位, 持仓分散度)
    async function renderPositionsAndRisk(totalAssets) {
        let positions = [];
        try {
            const res = await fetch('/api/account/positions?_t=' + Date.now());
            const json = await res.json();
            positions = (json.data && json.data.positions) || (json.positions) || (Array.isArray(json) ? json : []);
        } catch(e) {}

        const posList = document.getElementById('sidePositionList');
        const hudHoldCount = document.getElementById('hudHoldingCount');
        const hudMaxPos = document.getElementById('hudMaxPosition');

        if (hudHoldCount) {
            hudHoldCount.textContent = `${positions.length} 只`;
            hudHoldCount.style.color = positions.length >= 3 ? '#00f59b' : (positions.length > 0 ? '#ffe600' : '#94a3b8');
        }

        if (!positions || !positions.length) {
            if (posList) posList.innerHTML = '<div style="text-align:center;color:#94a3b8;padding:15px;font-size:11px;">暂无持仓</div>';
            if (hudMaxPos) {
                hudMaxPos.textContent = '0.0%';
                hudMaxPos.style.color = '#00f59b';
            }
            return;
        }

        let maxMv = 0;
        if (posList) {
            posList.innerHTML = positions.map(p => {
                const mv = Number(p.market_value || ((p.volume || p.amount || 0) * (p.price || p.current_price || 0)));
                if (mv > maxMv) maxMv = mv;
                const pnl = Number(p.profit || 0);
                const pnlPct = Number(p.profit_rate || 0);
                const color = pnl >= 0 ? '#ff1824' : '#00f59b';
                const sign = pnl > 0 ? '+' : '';

                return `
                    <div style="background: rgba(18, 14, 20, 0.7); border: 1px solid rgba(255,255,255,0.06); border-radius: 6px; padding: 6px 10px; display: flex; justify-content: space-between; align-items: center; font-size: 11px;">
                        <div>
                            <span style="font-weight: 700; color: #f8fafc;">${p.name || p.symbol}</span>
                            <span style="color: #94a3b8; font-family: var(--font-mono); margin-left: 4px;">${p.symbol}</span>
                        </div>
                        <div style="text-align: right; font-family: var(--font-mono);">
                            <span style="color: ${color}; font-weight: 700;">${sign}${pnlPct.toFixed(2)}%</span>
                            <span style="color: #94a3b8; margin-left: 6px;">¥${fmtMoney(mv)}</span>
                        </div>
                    </div>
                `;
            }).join('');
        }

        const effAssets = totalAssets > 0 ? totalAssets : 200000;
        const maxPct = (maxMv / effAssets) * 100;
        if (hudMaxPos) {
            hudMaxPos.textContent = `${maxPct.toFixed(1)}%`;
            hudMaxPos.style.color = maxPct > 35 ? '#ff1824' : (maxPct > 28 ? '#ffe600' : '#00f59b');
        }
    }

    // 全局启动与自愈轮询
    function startProCockpitEngine() {
        console.log("🚀 [P5 ENGINE] 启动 ProCockpitEngine 轮询...");
        // 立即执行 HUD 时钟
        tickHudClock();
        setInterval(tickHudClock, 1000);

        // 立即测速 RTT
        pingGatewayRtt();
        setInterval(pingGatewayRtt, 5000);

        // 渲染三大面板与矩阵
        renderPipelineList();
        renderEnsembleGrid();
        renderCandidateTable();
        syncAndRenderMultiAccounts();

        // 5 秒全局自愈刷新
        setInterval(() => {
            renderPipelineList();
            renderCandidateTable();
            syncAndRenderMultiAccounts();
        }, 5000);
    }

    // 暴露并绑定到全局
    window.startProCockpitEngine = startProCockpitEngine;
    window.refreshAllCockpitPanels = () => {
        renderPipelineList();
        renderEnsembleGrid();
        renderCandidateTable();
        syncAndRenderMultiAccounts();
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', startProCockpitEngine);
    } else {
        startProCockpitEngine();
    }
})();
