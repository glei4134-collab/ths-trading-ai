#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
THS Quant Cockpit - 自动化前端集成渲染与交付验收测试套件 (Frontend E2E Gatekeeper)
=============================================================================
规约：所有发布、交接与代码部署前必须 100% 通过本测试，杜绝“代码上线但界面卡在加载态”
=============================================================================
"""

import sys
import json
import urllib.request
import re
from typing import List, Dict, Tuple

# 解决 Windows 控制台 GBK 打印问题
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

SERVER_HOST = "http://81.70.51.173"

class FrontendVerificationSuite:
    def __init__(self, target_url: str = SERVER_HOST):
        self.target_url = target_url
        self.passed = 0
        self.failed = 0
        self.warnings = 0

    def assert_test(self, name: str, condition: bool, err_msg: str = ""):
        if condition:
            print(f"  [PASS] {name}")
            self.passed += 1
        else:
            print(f"  [FAIL] {name} -> {err_msg}")
            self.failed += 1

    def run_all(self) -> bool:
        print("=" * 70)
        print("[THS QUANT] 自动化前端集成与生产页面交付验收测试")
        print(f"验收目标: {self.target_url}")
        print("=" * 70)

        self.test_1_remote_html_availability()
        self.test_2_critical_dom_elements()
        self.test_3_debug_buttons_removal()
        self.test_4_engine_script_loaded()
        self.test_5_backend_api_data_integrity()
        self.test_6_runtime_logic_dry_run()

        print("-" * 70)
        print(f"验收总结: 通过 {self.passed} 项, 失败 {self.failed} 项, 告警 {self.warnings} 项")
        print("=" * 70)
        return self.failed == 0

    def test_1_remote_html_availability(self):
        print("\n[测试阶段 1] 生产环境 HTTP 200 与 HTML 主文档拉取")
        try:
            req = urllib.request.Request(self.target_url, headers={"User-Agent": "THS-E2E-Tester/1.0"})
            with urllib.request.urlopen(req, timeout=5) as res:
                self.html_content = res.read().decode("utf-8")
                self.assert_test("生产页面响应 HTTP 200", res.status == 200)
                self.assert_test("HTML 包含完整闭合标签", "</html>" in self.html_content and "</body>" in self.html_content)
        except Exception as e:
            self.html_content = ""
            self.assert_test("生产页面网络拉取", False, str(e))

    def test_2_critical_dom_elements(self):
        print("\n[测试阶段 2] 核心面板 DOM 容器与 ID 完备性校验")
        if not self.html_content:
            self.assert_test("跳过阶段 2 (无 HTML 内容)", False)
            return

        required_ids = [
            ("pipelineCardsList", "8大时序流水线列表容器"),
            ("pipelineSummaryText", "时序节点汇总标签"),
            ("candidateTableBody", "冲击候选池表格主体"),
            ("candidateMobileList", "移动端候选池卡片流容器"),
            ("accountCardsGrid", "3账户异构卡片网格"),
            ("ensembleCardsGrid", "5大核心策略加权表决卡片网格"),
            ("consensusSignalBadge", "表决信号共识徽章"),
            ("consensusTargetPosition", "建议单票目标仓位标签"),
            ("hudClockTime", "全局态势时钟"),
            ("hudRttText", "网关 RTT 延迟标签"),
            ("hudMaxPosition", "单票最高仓位风控标签"),
            ("hudHoldingCount", "持仓分散度只数标签"),
            ("hudMaxDrawdown", "团队最大回撤标签"),
            ("sideTotalAssets", "右侧资产看板总资产"),
            ("sideAvailable", "右侧资产看板可用现金"),
            ("consTotalAssets", "合并总资产卡片"),
            ("consAvailable", "合并可用现金卡片"),
            ("sidePositionList", "持仓列表容器"),
            ("sideExecutionLog", "执行流水日志容器")
        ]

        for elem_id, desc in required_ids:
            found = f'id="{elem_id}"' in self.html_content or f"id='{elem_id}'" in self.html_content
            self.assert_test(f"DOM 容器 [{desc}] (id='{elem_id}') 存在", found, "页面缺失该挂载节点")

    def test_3_debug_buttons_removal(self):
        print("\n[测试阶段 3] 生产环境调试入口与演练按钮彻底剥离审计")
        if not self.html_content:
            return

        # 检查是否残留演练/时钟按钮
        has_debug_btn = "🧪 演练/时钟" in self.html_content or 'onclick="openDebugModal()"' in self.html_content
        self.assert_test("界面完全剥离调试按钮 (openDebugModal)", not has_debug_btn, "发现残留的 openDebugModal 调试按钮")

        # 检查右上角立即分析按钮是否被下线
        has_run_now_header = '<button onclick="runAISchedulerNow()"' in self.html_content
        self.assert_test("顶栏彻底移除手动 '立即分析' 调试按钮", not has_run_now_header, "发现顶栏残留立即分析按钮")

    def test_4_engine_script_loaded(self):
        print("\n[测试阶段 4] 前端数据驱动引擎 (ProCockpitEngine) 挂载校验")
        has_engine = "ProCockpitEngine" in self.html_content
        has_clock_func = "tickHudClock" in self.html_content
        has_candidate_render = "renderCandidateTable" in self.html_content
        has_pipeline_render = "renderPipelineList" in self.html_content

        self.assert_test("HTML 成功集成 ProCockpitEngine 核心代码", has_engine, "页面未检测到 ProCockpitEngine 标识")
        self.assert_test("包含时钟与 HUD 态势驱动器 (tickHudClock)", has_clock_func)
        self.assert_test("包含冲击池渲染驱动器 (renderCandidateTable)", has_candidate_render)
        self.assert_test("包含8大时序渲染驱动器 (renderPipelineList)", has_pipeline_render)

    def test_5_backend_api_data_integrity(self):
        print("\n[测试阶段 5] 前端数据源后端 API 数据完整度校验")
        api_checks = [
            ("/api/accounts", lambda d: "accounts" in d.get("data", d) and len(d.get("data", d)["accounts"]) >= 1, "需返回至少1个真实账户"),
            ("/api/ai/candidates/today", lambda d: len(d.get("candidates", d.get("data", []))) > 0, "需返回今日多因子候选股票池"),
            ("/api/ai/scheduler/ledger/today", lambda d: "entries" in d.get("data", {}), "需返回时序流水线 entries 节点"),
            ("/api/ai/scheduler/status", lambda d: "enabled" in d.get("data", {}), "需返回自律调度状态"),
            ("/api/connection/info", lambda d: "connected" in d, "需返回同花顺网关连接态")
        ]

        for ep, validator, desc in api_checks:
            try:
                url = self.target_url + ep
                req = urllib.request.Request(url, headers={"User-Agent": "THS-E2E-Tester/1.0"})
                with urllib.request.urlopen(req, timeout=4) as res:
                    data = json.loads(res.read().decode("utf-8"))
                    is_valid = validator(data)
                    self.assert_test(f"API [{ep}] 结构合规 ({desc})", is_valid, f"数据返回异常: {str(data)[:100]}")
            except Exception as e:
                self.assert_test(f"API [{ep}] 连通性测试", False, str(e))

    def test_6_runtime_logic_dry_run(self):
        print("\n[测试阶段 6] 模拟前端数据映射与占位符填充测试")
        has_fallback = "非交易时段" in self.html_content or "W0" in self.html_content
        self.assert_test("具备非交易时段及空账本优雅降级保护", has_fallback, "缺少离线时段降级显示逻辑")


if __name__ == "__main__":
    suite = FrontendVerificationSuite()
    success = suite.run_all()
    sys.exit(0 if success else 1)
