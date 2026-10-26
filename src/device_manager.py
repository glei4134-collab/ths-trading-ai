"""
设备管理模块
手机绑定、JWT鉴权、二维码管理

绑定流程:
  PC 生成 bind_code (6位数字, 30秒过期) → 显示二维码
  → 手机扫码打开 PC 地址 → 提交 bind_code → 验证通过
  → 服务端签发 JWT (1年有效) → 手机存储 JWT → 绑定完成
"""

import os
import json
import time
import secrets
import string
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

# JWT密钥持久化到 config/jwt_secret.json
_jwt_secret_file = Path(__file__).parent.parent / "config" / "jwt_secret.json"

def _load_jwt_secret():
    """加载或生成JWT密钥"""
    if _jwt_secret_file.exists():
        try:
            return json.loads(_jwt_secret_file.read_text(encoding="utf-8"))["secret"]
        except Exception:
            pass
    secret = secrets.token_hex(32)
    _jwt_secret_file.parent.mkdir(parents=True, exist_ok=True)
    _jwt_secret_file.write_text(json.dumps({"secret": secret}), encoding="utf-8")
    logger.info(f"Generated new JWT secret → {_jwt_secret_file}")
    return secret

JWT_SECRET = _load_jwt_secret()

# 配置
QR_EXPIRE = 30         # 二维码30秒过期
QR_REFRESH = 5         # 二维码每5秒刷新
TOKEN_LIFETIME_DAYS = 365  # JWT 1年有效期

# 简易JWT实现（避免依赖PyJWT）
import base64
import hmac
import hashlib

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()

def _b64url_decode(s: str) -> bytes:
    s += '=' * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)

def _jwt_encode(payload: dict) -> str:
    """生成JWT token"""
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(',', ':')).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(',', ':')).encode())
    signing_input = f"{header_b64}.{payload_b64}"
    sig = hmac.new(JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    sig_b64 = _b64url_encode(sig)
    return f"{header_b64}.{payload_b64}.{sig_b64}"

def _jwt_decode(token: str) -> Optional[dict]:
    """验证并解码JWT token"""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        header_b64, payload_b64, sig_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}"
        expected_sig = hmac.new(JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
        actual_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        payload = json.loads(_b64url_decode(payload_b64))
        # 检查过期
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


class DeviceManager:
    """设备绑定/鉴权管理器"""

    def __init__(self, db_store):
        self.db = db_store
        self._lock = threading.Lock()
        self._pending_codes: Dict[str, dict] = {}  # bind_code → {created_at, ip, claimed}
        self._bind_events: Dict[str, threading.Event] = {}  # bind_code → Event (PC端等待扫码结果)

    def init_db(self):
        """初始化设备表"""
        self.db._init_devices_table()

    # ========== 二维码绑定 ==========

    def generate_bind_code(self, pc_ip: str) -> dict:
        """生成6位数字绑定码，30秒过期"""
        with self._lock:
            # 清理过期的码
            now = time.time()
            expired = [k for k, v in self._pending_codes.items()
                       if now - v["created_at"] > QR_EXPIRE]
            for k in expired:
                del self._pending_codes[k]
                if k in self._bind_events:
                    del self._bind_events[k]

            # 生成新码
            code = ''.join(secrets.choice(string.digits) for _ in range(6))
            self._pending_codes[code] = {
                "created_at": now,
                "ip": pc_ip,
                "claimed": False,
                "result": None
            }
            self._bind_events[code] = threading.Event()

            logger.info(f"生成绑定码: {code} (IP: {pc_ip})")
            return {
                "bind_code": code,
                "expires_in": QR_EXPIRE,
                "pc_ip": pc_ip,
                "created_at": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%H:%M:%S")
            }

    def claim_bind_code(self, code: str, device_name: str = "") -> dict:
        """手机端提交绑定码，返回JWT"""
        with self._lock:
            info = self._pending_codes.get(code)
            if not info:
                return {"success": False, "message": "绑定码无效或已过期"}

            if time.time() - info["created_at"] > QR_EXPIRE:
                del self._pending_codes[code]
                return {"success": False, "message": "绑定码已过期，请刷新"}

            if info.get("claimed"):
                return {"success": False, "message": "该绑定码已被使用"}

            # 签发JWT
            info["claimed"] = True
            pc_ip = info["ip"]

        token = self._issue_token(pc_ip, device_name)

        # 解析device_id并存入数据库
        payload = _jwt_decode(token)
        if payload:
            device_id = payload["device_id"]
            self._save_device(device_id, device_name, pc_ip)

            # 通知PC端扫码成功
            with self._lock:
                info["result"] = {"device_id": device_id, "device_name": device_name}
                if code in self._bind_events:
                    self._bind_events[code].set()

            logger.info(f"设备绑定成功: {device_id} ({device_name}) from {pc_ip}")
            return {
                "success": True,
                "token": token,
                "device_id": device_id,
                "message": f"绑定成功！设备名: {device_name or '未命名'}"
            }

        return {"success": False, "message": "签发令牌失败"}

    def poll_bind_status(self, bind_code: str, timeout: int = 25) -> dict:
        """PC端轮询扫码状态（阻塞等待或超时）"""
        event = self._bind_events.get(bind_code)
        if not event:
            info = self._pending_codes.get(bind_code)
            if info and info.get("claimed"):
                return {"status": "claimed", "result": info.get("result")}
            return {"status": "expired"}

        info = self._pending_codes.get(bind_code)
        if not info:
            return {"status": "expired"}

        if info.get("claimed"):
            return {"status": "claimed", "result": info.get("result")}

        # 等待手机扫码（最多等 timeout 秒）
        event_set = event.wait(timeout=timeout)
        if event_set and info.get("claimed"):
            return {"status": "claimed", "result": info.get("result")}

        return {"status": "waiting"}

    def get_qr_content(self, pc_ip: str, port: int = 5000) -> str:
        """生成二维码内容（手机扫码打开的URL）"""
        return f"http://{pc_ip}:{port}/mobile"

    # ========== JWT鉴权 ==========

    def _issue_token(self, pc_ip: str, device_name: str = "") -> str:
        """签发JWT"""
        device_id = secrets.token_hex(8)
        now = int(time.time())
        payload = {
            "device_id": device_id,
            "device_name": device_name,
            "pc_ip": pc_ip,
            "iat": now,
            "exp": now + TOKEN_LIFETIME_DAYS * 86400
        }
        return _jwt_encode(payload)

    def verify_token(self, token: str) -> Optional[dict]:
        """验证JWT，返回payload或None"""
        payload = _jwt_decode(token)
        if payload:
            # 更新最后在线时间
            device_id = payload.get("device_id")
            if device_id:
                self._update_last_seen(device_id)
        return payload

    # ========== 设备管理 ==========

    def _save_device(self, device_id: str, name: str, ip: str):
        """保存设备到数据库"""
        try:
            now = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
            self.db.conn.execute(
                """INSERT OR REPLACE INTO devices (device_id, device_name, bound_at, last_seen_at, pc_ip)
                   VALUES (?, ?, ?, ?, ?)""",
                (device_id, name, now, now, ip)
            )
            self.db.conn.commit()
        except Exception as e:
            logger.error(f"保存设备失败: {e}")

    def _update_last_seen(self, device_id: str):
        """更新设备最后在线时间"""
        try:
            now = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
            self.db.conn.execute(
                "UPDATE devices SET last_seen_at = ? WHERE device_id = ?",
                (now, device_id)
            )
            self.db.conn.commit()
        except Exception:
            pass

    def list_devices(self) -> List[dict]:
        """列出所有已绑定设备"""
        try:
            rows = self.db.conn.execute(
                "SELECT * FROM devices ORDER BY bound_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def remove_device(self, device_id: str) -> bool:
        """解绑设备"""
        try:
            self.db.conn.execute(
                "DELETE FROM devices WHERE device_id = ?", (device_id,)
            )
            self.db.conn.commit()
            logger.info(f"设备解绑: {device_id}")
            return True
        except Exception:
            return False


# 全局实例（需在db_store初始化后创建）
device_manager: Optional[DeviceManager] = None

def init_device_manager(db_store):
    """初始化设备管理器"""
    global device_manager
    device_manager = DeviceManager(db_store)
    device_manager.init_db()
    return device_manager
