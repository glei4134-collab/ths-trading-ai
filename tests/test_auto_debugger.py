"""
单元测试：量化自动调试与自愈体检引擎 (Test Auto-Debugger)
验证各项诊断探针、健康评分算法与一键修复器
"""

import sys
import unittest
from pathlib import Path

# 确保路径解析
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from auto_debugger import QuantAutoDebugger, ProbeSeverity


class TestAutoDebugger(unittest.TestCase):
    """测试全链路自动调试器"""

    def setUp(self):
        self.debugger = QuantAutoDebugger()

    def test_environment_probe(self):
        """测试运行环境探针"""
        items = self.debugger.check_environment()
        self.assertGreaterEqual(len(items), 2)
        names = [it.name for it in items]
        self.assertIn("Python版本环境", names)
        self.assertIn("时区与时钟 (Asia/Shanghai)", names)
        for it in items:
            if it.name == "Python版本环境":
                self.assertTrue(it.passed)

    def test_scheduler_and_clock_probe(self):
        """测试调度账本与时钟探针"""
        items = self.debugger.check_scheduler_and_clock()
        self.assertGreaterEqual(len(items), 2)
        names = [it.name for it in items]
        self.assertIn("时钟状态", names)
        self.assertIn("调度账本执行状态", names)

    def test_risk_compliance_probe(self):
        """测试团队赛风控探针"""
        items = self.debugger.check_risk_compliance()
        self.assertGreaterEqual(len(items), 3)
        names = [it.name for it in items]
        self.assertIn("赛事规程适配", names)
        self.assertIn("单票仓位红线 (<=35%)", names)
        self.assertIn("组合分散度 (>=3只标的)", names)

    def test_run_all_checks_structure(self):
        """测试全链路体检报告结构与评分"""
        report = self.debugger.run_all_checks()
        self.assertIn("health_score", report)
        self.assertIn("status", report)
        self.assertIn("summary", report)
        self.assertIn("items", report)
        self.assertIn("fixable_actions", report)
        self.assertIsInstance(report["health_score"], int)
        self.assertTrue(0 <= report["health_score"] <= 100)
        self.assertGreaterEqual(report["summary"]["total_checks"], 10)

    def test_auto_fixers(self):
        """测试一键自愈修复器"""
        # 测试 fix_mock_clock
        res_clock = self.debugger.run_auto_fix("fix_mock_clock")
        self.assertTrue(res_clock["success"])

        # 测试 fix_ledger
        res_ledger = self.debugger.run_auto_fix("fix_ledger")
        self.assertTrue(res_ledger["success"])

        # 测试 fix_all
        res_all = self.debugger.run_auto_fix("fix_all")
        self.assertTrue(res_all["success"])


if __name__ == "__main__":
    unittest.main()
