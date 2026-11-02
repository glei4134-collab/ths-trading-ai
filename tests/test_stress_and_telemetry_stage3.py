"""
Unit tests for Stage 3 Stress Tester & Telemetry Cryptographic Audit:
- ScenarioStressTester (2015/2020/2021/2024 4 black-swan scenarios replay)
- Pre-trade What-If Marginal VaR / CVaR & 35% cap sandbox
- OrderHMACAuthenticator (HMAC-SHA256 signing, replay attack rejection, timestamp skew check)
- TamperEvidentAuditChain (hash-chained audit log integrity verification)
- DeltaPatchStreamer (incremental state delta calculation)
"""
import time
import unittest
from src.stress_tester import ScenarioStressTester, ExtremeScenarios, scenario_stress_tester
from src.telemetry_audit import (
    OrderHMACAuthenticator,
    TamperEvidentAuditChain,
    DeltaPatchStreamer
)


class TestStressTesterAndTelemetryStage3(unittest.TestCase):

    def test_historical_scenarios_replay(self):
        positions = {
            "300750": {"name": "宁德时代", "amount": 2000, "price": 25.0, "market_value": 50000.0, "industry": "新能源"},
            "600519": {"name": "贵州茅台", "amount": 200, "price": 200.0, "market_value": 40000.0, "industry": "白酒"},
            "688981": {"name": "中芯国际", "amount": 1000, "price": 30.0, "market_value": 30000.0, "industry": "芯片"},
        }
        res = scenario_stress_tester.replay_scenarios(positions, total_assets=200000.0)
        self.assertIn("scenarios", res)
        self.assertEqual(len(res["scenarios"]), 4)
        self.assertGreater(res["worst_portfolio_loss_pct"], 2.0)
        self.assertIn("resilience_score", res)

    def test_pre_trade_marginal_risk_what_if(self):
        positions = {
            "688981": {"name": "中芯国际", "amount": 2000, "price": 30.0, "market_value": 60000.0, "industry": "芯片"},
            "000001": {"name": "平安银行", "amount": 2000, "price": 10.0, "market_value": 20000.0, "industry": "银行"}
        }
        # 拟对 688981 继续加仓 500 股 @ 30元 = 15,000元 -> 单票总计 75,000 / 200,000 = 37.5% (> 35% 单票红线)
        sim = scenario_stress_tester.what_if_marginal_analysis(
            current_positions=positions,
            total_assets=200000.0,
            new_symbol="688981",
            new_shares=500,
            new_price=30.0,
            new_name="中芯国际",
            new_industry="芯片"
        )
        self.assertTrue(sim["is_single_limit_breached"])
        self.assertFalse(sim["execution_approval"])
        self.assertGreater(sim["post_var_95_pct"], sim["pre_var_95_pct"])

    def test_order_hmac_signature_and_anti_replay(self):
        auth = OrderHMACAuthenticator(secret_key="unit-test-secret", max_clock_skew_sec=60)
        body = {"symbol": "000001", "action": "buy", "price": 12.5, "amount": 500}
        now_ts = int(time.time())

        headers = auth.sign_request("POST", "/api/trade/order", body, timestamp=now_ts, nonce="nonce-001")
        ok, msg = auth.verify_request(
            "POST", "/api/trade/order", body,
            timestamp_str=headers["X-Quant-Timestamp"],
            nonce=headers["X-Quant-Nonce"],
            signature=headers["X-Quant-Signature"],
            now_ts=now_ts
        )
        self.assertTrue(ok)
        self.assertEqual(msg, "OK")

        # 相同 Nonce 重放攻击应被立即拦截
        ok_replay, msg_replay = auth.verify_request(
            "POST", "/api/trade/order", body,
            timestamp_str=headers["X-Quant-Timestamp"],
            nonce=headers["X-Quant-Nonce"],
            signature=headers["X-Quant-Signature"],
            now_ts=now_ts
        )
        self.assertFalse(ok_replay)
        self.assertEqual(msg_replay, "REPLAY_ATTACK_DETECTED")

        # 篡改参数内容后签名失配应被拦截
        tampered_body = {"symbol": "000001", "action": "buy", "price": 12.5, "amount": 5000}
        ok_tamper, msg_tamper = auth.verify_request(
            "POST", "/api/trade/order", tampered_body,
            timestamp_str=headers["X-Quant-Timestamp"],
            nonce="nonce-002",
            signature=headers["X-Quant-Signature"],
            now_ts=now_ts
        )
        self.assertFalse(ok_tamper)
        self.assertEqual(msg_tamper, "SIGNATURE_MISMATCH")

    def test_tamper_evident_audit_chain(self):
        chain = TamperEvidentAuditChain()
        chain.append_event("ORDER_SUBMIT", "000001", "buy", {"amount": 500, "price": 10.5})
        chain.append_event("ORDER_FILLED", "000001", "buy", {"amount": 500, "price": 10.5})

        valid, count = chain.verify_chain_integrity()
        self.assertTrue(valid)
        self.assertEqual(count, 2)

        # 恶意篡改第一条记录金额
        chain._records[0]["details"]["amount"] = 100
        valid_after, _ = chain.verify_chain_integrity()
        self.assertFalse(valid_after)

    def test_delta_patch_streamer(self):
        streamer = DeltaPatchStreamer()
        d1 = streamer.compute_delta({"a": 1, "b": 2})
        self.assertEqual(d1["changed_count"], 2)

        # 第二帧仅修改 b，a 未变
        d2 = streamer.compute_delta({"a": 1, "b": 3})
        self.assertEqual(d2["changed_count"], 1)
        self.assertEqual(d2["changed"], {"b": 3})

    def test_stage3_telemetry_and_audit_api_routes(self):
        from flask import Flask
        from api.routes.ai_timing import ai_timing_bp
        app = Flask(__name__)
        app.register_blueprint(ai_timing_bp)
        client = app.test_client()

        # 1. /api/macro/quadrant
        r1 = client.get("/api/macro/quadrant")
        self.assertEqual(r1.status_code, 200)
        j1 = r1.get_json()
        self.assertTrue(j1["success"])
        self.assertIn(j1["data"]["quadrant_regime"], ("bull_low_vol", "bull_high_vol", "bear_low_vol", "bear_high_vol"))

        # 2. /api/risk/industry-exposure
        r2 = client.get("/api/risk/industry-exposure")
        self.assertEqual(r2.status_code, 200)
        j2 = r2.get_json()
        self.assertTrue(j2["success"])
        self.assertIn("hhi_index", j2["data"])

        # 3. /api/audit/sign-order & /api/audit/verify-signature
        r3 = client.post("/api/audit/sign-order", json={
            "account_id": 1, "symbol": "000001", "action": "buy", "price": 12.50, "amount": 500
        })
        self.assertEqual(r3.status_code, 200)
        j3 = r3.get_json()
        self.assertTrue(j3["success"])
        sig = j3["data"]["signature"]
        payload = j3["data"]["payload"]

        r4 = client.post("/api/audit/verify-signature", json={"payload": payload, "signature": sig})
        self.assertEqual(r4.status_code, 200)
        self.assertTrue(r4.get_json()["data"]["valid"])

        # Tamper amount -> verify must fail
        tampered = dict(payload)
        tampered["amount"] = 1000
        r4_bad = client.post("/api/audit/verify-signature", json={"payload": tampered, "signature": sig})
        self.assertFalse(r4_bad.get_json()["data"]["valid"])

        # 4. /api/risk/stress-test
        r5 = client.get("/api/risk/stress-test")
        self.assertEqual(r5.status_code, 200)
        j5 = r5.get_json()
        self.assertTrue(j5["success"])
        self.assertIn("stress_scenarios", j5["data"])

        # 5. /api/risk/what-if
        r6 = client.post("/api/risk/what-if", json={
            "account_id": 1, "symbol": "300750", "shares": 200, "price": 180.0, "industry": "新能源"
        })
        self.assertEqual(r6.status_code, 200)
        j6 = r6.get_json()
        self.assertTrue(j6["success"])
        self.assertIn("delta_var_95_pct", j6["data"])


if __name__ == "__main__":
    unittest.main()
