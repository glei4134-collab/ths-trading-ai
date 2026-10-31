# -*- coding: utf-8 -*-
"""
同花顺官方登录认证服务模块 (THS Unified Authentication Client)
支持：
1. 手机短信验证码发送与登录鉴权（调用同花顺官方统一通行证网关真实下发短信并完成登录）
2. 账号密码登录鉴权（支持标准 A 股同花顺通行证账号）
3. 自动化滑块验证码求解器（基于 OpenCV 图像差分算法毫秒级识别）
4. 模拟盘交易席位自动握手与 Cookie 自动保存绑定
"""

import base64
import hashlib
import hmac
import json
import logging
import random
import re
import threading
import time
from typing import Dict, Optional, Tuple

try:
    import cv2
except ImportError:
    cv2 = None
try:
    import numpy as np
except ImportError:
    np = None
try:
    from PIL import Image
except ImportError:
    Image = None
import requests
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad

from pure_v import get_v, BROWSER_UA

logger = logging.getLogger(__name__)

# ================= 官方认证端点 =================
UPASS_BASE = "https://upass.10jqka.com.cn"
PUBKEY_URL = f"{UPASS_BASE}/pubkey/default.js"
CAPTCHA_BASE = "https://captcha.10jqka.com.cn"
HAWKEYE_BASE = "https://hawkeye.10jqka.com.cn"

# default_5 公钥兜底（网站当前版本）
DEFAULT5_PUBKEY = {
    'v': 'default_5',
    'p': '10001',
    'm': 'D90F4DD5BF444916913F7B434F192587C354387FA531F2964725B5188FB9D5B40FDDD2B34F61B5560468D1F5C568796EB15F7799F03E4A3301EAF8B79B655F1B2B7DC6FFE1084E4C14A05DD9C6D0C72C5ED75890DC5D11AB5990A3C7DEBA0D68EFFD8C619B2A21AEFA3E7FF902CDAE0502025901EE2D42B76A0D7AD389F0BF69'
}

_pubkey_cache = None

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "Referer": f"{UPASS_BASE}/",
    "Sec-Fetch-Dest": "script",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": BROWSER_UA,
    "sec-ch-ua": '"Google Chrome";v="146", "Chromium";v="146", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


def str_md5(target: str) -> str:
    return hashlib.md5(target.encode('utf-8')).hexdigest()


def sha256_hex(target: str) -> str:
    return hashlib.sha256(target.encode('utf-8')).hexdigest()


def hmac_sha256_hex(key: str, msg: str) -> str:
    return hmac.new(key.encode('utf-8'), msg.encode('utf-8'), hashlib.sha256).hexdigest()


def get_crnd() -> str:
    return ''.join(random.choices('0123456789abcdefghijklmnopqrstuvwxyz', k=16))


def fetch_login_pubkey() -> dict:
    """获取登录 RSA 公钥（thspubkey），失败时用 default_5 兜底"""
    global _pubkey_cache
    if _pubkey_cache:
        return _pubkey_cache
    try:
        r = requests.get(PUBKEY_URL, timeout=8,
                         headers={'User-Agent': BROWSER_UA, 'Referer': f'{UPASS_BASE}/'})
        m = re.search(r'thspubkey=(\{.*?\});', r.text)
        if m:
            _pubkey_cache = json.loads(m.group(1))
            return _pubkey_cache
    except Exception as e:
        logger.debug(f"获取动态公钥失败，使用 default_5 兜底: {e}")
    return dict(DEFAULT5_PUBKEY)


def rsa_encrypt(data: str, pub: Optional[dict] = None) -> str:
    """JSEncrypt 兼容：PKCS#1 v1.5，UTF-8 加密"""
    pub = pub or fetch_login_pubkey()
    n = int(pub['m'], 16)
    e = int(pub['p'], 16)
    key = RSA.construct((n, e))
    cipher = PKCS1_v1_5.new(key)
    return base64.b64encode(cipher.encrypt(data.encode('utf-8'))).decode()


def _xor_bytes(e: bytes, t: bytes) -> bytes:
    n = len(t)
    return bytes(b ^ t[i % n] for i, b in enumerate(e))


def get_passwd_salt(ssv: str, crnd_dsk_sha256: str) -> str:
    ssv_decode = base64.b64decode(ssv)
    sb = _xor_bytes(ssv_decode, crnd_dsk_sha256.encode('latin-1')).decode('latin-1')
    return sb.split('=', 1)[1]


def get_passwd_salt2(hex_hmac_result: str, dsv_sha256: str) -> str:
    n = _xor_bytes(hex_hmac_result.encode('latin-1'), dsv_sha256.encode('latin-1'))
    n_b64 = base64.b64encode(n).decode()
    return rsa_encrypt(n_b64, fetch_login_pubkey())


# ================= 自动化滑块求解器 =================
class CaptchaSolver:
    """同花顺缺口滑块验证码自动识别与取票器"""

    @staticmethod
    def solve(session: requests.Session, last_v: Optional[str] = None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        全自动完成滑块验证码预检、下载图片、缺口识别与票据获取
        Returns: (ticket, phrase, signature)
        """
        try:
            # 1. getPreHandle
            params = {
                'captcha_type': '4',
                'appid': 'registernew',
                'random': random.random() * time.time() * 1000,
                'callback': 'PreHandle',
            }
            v_val = get_v(old_v=last_v)
            session.cookies.set('v', v_val)
            headers = dict(HEADERS)
            headers['hexin-v'] = v_val

            r = session.get(f"{CAPTCHA_BASE}/getPreHandle", headers=headers, params=params, timeout=12)
            text = r.text
            url_params_str = ''.join(re.findall(r'urlParams":"(.*?)"', text))
            imgs_match = re.findall(r'"imgs":(\[.*?\]),', text)
            if not imgs_match:
                logger.warning(f"未能解析滑块图片列表: {text[:100]}")
                return None, None, None

            imgs = eval(''.join(imgs_match))
            m_inity = re.search(r'inity":(\d+)', text)
            inity = int(m_inity.group(1)) if m_inity else 0
            from urllib.parse import parse_qs
            p = parse_qs(url_params_str)

            # 2. 下载背景图与滑块图
            img_contents = []
            img_headers = dict(headers)
            img_headers['Accept'] = 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
            for img_id in imgs:
                img_url = f"{CAPTCHA_BASE}/getImg"
                p_img = {
                    'rand': p['rand'][0],
                    'time': p['time'][0],
                    'appid': 'registernew',
                    'captcha_type': '4',
                    'signature': p['signature'][0],
                    'iuk': img_id,
                }
                ir = session.get(img_url, headers=img_headers, params=p_img, timeout=12)
                img_contents.append(ir.content)

            if len(img_contents) < 2:
                return None, None, None

            # 3. OpenCV 缺口匹配
            bg = cv2.imdecode(np.frombuffer(img_contents[0], np.uint8), cv2.IMREAD_COLOR)
            bl = cv2.imdecode(np.frombuffer(img_contents[1], np.uint8), cv2.IMREAD_UNCHANGED)
            alpha = bl[:, :, 3]
            mask = alpha > 40
            bl_gray = cv2.cvtColor(bl[:, :, :3], cv2.COLOR_BGR2GRAY).astype(np.int16)
            bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY).astype(np.int16)
            bh, bw = bl.shape[:2]

            best = (0.0, 0, 0)
            y0 = max(0, inity - 12)
            y1 = min(bg.shape[0] - bh + 1, inity + 12)
            for y in range(y0, y1):
                for x in range(0, bg.shape[1] - bw + 1):
                    roi = bg_gray[y:y + bh, x:x + bw]
                    score = float(np.abs(roi - bl_gray)[mask].mean())
                    if score > best[0]:
                        best = (score, x, y)

            x340 = best[1]
            x_disp = x340 * 309 / 340

            # 4. 获取验证票据 getTicket
            height = 177.22058823529412
            y_disp = inity / 195 * height
            phrase = f"{x_disp};{y_disp};309;{height}"
            ticket_params = {
                'rand': p['rand'][0],
                'time': p['time'][0],
                'appid': 'registernew',
                'captcha_type': '4',
                'signature': p['signature'][0],
                'phrase': phrase,
                'callback': 'verify',
            }
            v_behavior = get_v(old_v=v_val, behavior={'mm': 55, 'click': 5, 'x': 583, 'y': 524})
            session.cookies.set('v', v_behavior)
            headers['hexin-v'] = v_behavior

            tr = session.get(f"{CAPTCHA_BASE}/getTicket", headers=headers, params=ticket_params, timeout=12)
            ticket_match = re.findall(r'"ticket":"(.*?)"', tr.text)
            if ticket_match:
                ticket = ticket_match[0]
                return ticket, phrase, p['signature'][0]

        except Exception as e:
            logger.error(f"自动化滑块验证码识别异常: {e}")

        return None, None, None


# ================= 统一官方认证客户端 =================
class THSAuthClient:
    """
    同花顺统一通行证官方认证客户端
    为系统提供：
    1. 手机短信验证码下发 (send_phone_verify_code)
    2. 手机验证码登录鉴权与席位激活 (login_by_phone_code)
    3. 账号密码登录鉴权与席位激活 (login_by_password)
    """

    def __init__(self):
        self._phone_sessions: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    def _create_session(self) -> requests.Session:
        s = requests.Session()
        s.trust_env = False
        s.headers.update(HEADERS)
        s.cookies.set("vvvv", "1", domain="upass.10jqka.com.cn")
        return s

    def _clean_expired_sessions(self):
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._phone_sessions.items() if (now - v.get("time", 0)) > 600]
            for k in expired:
                self._phone_sessions.pop(k, None)

    def send_phone_verify_code(self, phone: str) -> Dict:
        """
        调用同花顺官方统一认证网关发送短信验证码

        Args:
            phone: 11 位手机号码

        Returns:
            Dict: {"success": bool, "message": str, "phone": str}
        """
        phone = str(phone or "").strip()
        if not re.match(r"^1[3-9]\d{9}$", phone):
            return {"success": False, "message": "手机号码格式不正确，请输入有效的11位大陆手机号"}

        self._clean_expired_sessions()
        session = self._create_session()
        pub = fetch_login_pubkey()

        # 1. 求解滑块验证码（最多重试 4 次确保高成功率）
        ticket = phrase = signature = None
        for _ in range(4):
            ticket, phrase, signature = CaptchaSolver.solve(session)
            if ticket:
                break
            time.sleep(1)

        if not ticket:
            return {"success": False, "message": "滑块安全防刷校验未通过，请稍后重试"}

        # 2. 构造加密载荷
        enc_mobile = rsa_encrypt(phone, pub)
        data = {
            'country_code': '86',
            'mobile': enc_mobile,
            'rsa_version': pub.get('v', 'default_5'),
            'source': 'pc_web',
            'request_type': 'login',
            'captcha_type': '4',
            'captcha_phrase': phrase,
            'captcha_ticket': ticket,
            'captcha_signature': signature,
        }

        v_val = get_v()
        session.cookies.set('v', v_val)
        headers = dict(HEADERS)
        headers['hexin-v'] = v_val

        try:
            r = session.post(f"{UPASS_BASE}/smslogin/sendSms", headers=headers, data=data, timeout=15)
            resp = r.json()
            errorcode = resp.get("errorcode", -1)
            errormsg = resp.get("errormsg", "")

            if errorcode == 0:
                with self._lock:
                    self._phone_sessions[phone] = {
                        "session": session,
                        "ticket": ticket,
                        "phrase": phrase,
                        "signature": signature,
                        "time": time.time(),
                        "last_v": v_val
                    }
                logger.info(f"✅ [THS-AUTH] 官方短信验证码已成功下发至手机: {phone[:3]}****{phone[-4:]}")
                return {
                    "success": True,
                    "message": "短信验证码已由同花顺官方网关下发至您的手机，请查收短信并在5分钟内完成输入",
                    "phone": phone
                }
            elif errorcode == -11002:
                return {"success": False, "message": "获取验证码过于频繁，请稍后再试"}
            elif errorcode == -11003:
                return {"success": False, "message": "获取验证码次数达上限，请稍后重试"}
            else:
                return {"success": False, "message": f"同花顺网关返回: {errormsg or '发送短信失败'}", "errorcode": errorcode}

        except Exception as e:
            logger.error(f"调用同花顺短信发送接口异常: {e}")
            return {"success": False, "message": f"连接同花顺认证中心失败: {str(e)}"}

    def login_by_phone_code(self, phone: str, code: str, account_id: Optional[int] = None) -> Dict:
        """
        提交手机号与短信验证码，完成同花顺登录并自动激活模拟盘席位与绑定

        Args:
            phone: 11 位手机号码
            code: 4-6 位短信验证码
            account_id: 指定绑定的账户ID（可选，默认智能匹配或新建）

        Returns:
            Dict: {"success": bool, "message": str, "cookie": str, ...}
        """
        phone = str(phone or "").strip()
        code = str(code or "").strip()

        if not re.match(r"^1[3-9]\d{9}$", phone):
            return {"success": False, "message": "手机号码格式不正确"}
        if not re.match(r"^\d{4,6}$", code):
            return {"success": False, "message": "短信验证码必须为4至6位数字"}

        # 提取或复用 Session
        with self._lock:
            cached = self._phone_sessions.get(phone)

        if cached and (time.time() - cached.get("time", 0)) < 300:
            session = cached["session"]
            ticket = cached.get("ticket")
            phrase = cached.get("phrase")
            signature = cached.get("signature")
            last_v = cached.get("last_v")
        else:
            session = self._create_session()
            ticket = phrase = signature = None
            for _ in range(4):
                ticket, phrase, signature = CaptchaSolver.solve(session)
                if ticket:
                    break
                time.sleep(1)
            last_v = None

        pub = fetch_login_pubkey()
        enc_mobile = rsa_encrypt(phone, pub)
        enc_code = rsa_encrypt(code, pub)

        data = {
            'country_code': '86',
            'mobile': enc_mobile,
            'signcode': enc_code,
            'rsa_version': pub.get('v', 'default_5'),
            'source': 'pc_web',
            'request_type': 'login',
        }
        if ticket:
            data.update({
                'captcha_type': '4',
                'captcha_phrase': phrase,
                'captcha_ticket': ticket,
                'captcha_signature': signature,
            })

        v_val = get_v(old_v=last_v)
        session.cookies.set('v', v_val)
        headers = dict(HEADERS)
        headers['hexin-v'] = v_val

        try:
            # 优先使用 /smslogin/checkSms 接口验证
            r = session.post(f"{UPASS_BASE}/smslogin/checkSms", headers=headers, data=data, timeout=15)
            resp = r.json()
            errorcode = resp.get("errorcode", resp.get("errorCode", -1))
            errormsg = resp.get("errormsg", resp.get("errorMsg", ""))

            # 备用容错：如果 checkSms 返回滑块错误或特定异常，尝试 /smslogin/check
            if errorcode != 0:
                r2 = session.post(f"{UPASS_BASE}/smslogin/check", headers=headers, data=data, timeout=15)
                resp2 = r2.json()
                if resp2.get("errorCode") == 0 or resp2.get("errorcode") == 0:
                    resp = resp2
                    errorcode = 0

            if errorcode != 0:
                if errorcode == -11011:
                    return {"success": False, "message": "短信验证码错误或已失效，请核对后重试"}
                return {"success": False, "message": f"验证失败: {errormsg or '未知错误'}", "errorcode": errorcode}

            # 登录成功，拼装通行证完整 Cookie
            cookie_dict = {c.name: c.value for c in session.cookies}
            raw_cookie = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

            # 自动激活模拟盘席位上下文 (PHPSESSID / zjzh)
            from ths_connector import activate_mncg_trade_session
            act_res = activate_mncg_trade_session(raw_cookie, session=session)
            final_cookie = act_res.get("cookie") if act_res.get("success") else raw_cookie

            # 自动绑定或续签至账户管理层
            from account_manager import account_manager
            acc_name = f"手机用户_{phone[-4:]}"
            bind_res = account_manager.add_account(acc_name, final_cookie)

            # 清理临时缓存
            with self._lock:
                self._phone_sessions.pop(phone, None)

            logger.info(f"🎉 [THS-AUTH] 手机号 {phone} 登录成功并已安全同步席位！")
            return {
                "success": True,
                "message": "同花顺手机验证码登录成功，模拟盘交易席位已激活并绑定！",
                "cookie": final_cookie,
                "phone": phone,
                "account_info": bind_res.get("data", {}),
                "account_id": bind_res.get("data", {}).get("id") or account_id,
            }

        except Exception as e:
            logger.error(f"验证手机验证码登录异常: {e}")
            return {"success": False, "message": f"登录认证失败: {str(e)}"}

    def login_by_password(self, account: str, password: str, account_id: Optional[int] = None) -> Dict:
        """
        账号密码登录鉴权

        Args:
            account: 同花顺通行证用户名/A股账号
            password: 明文登录密码
            account_id: 指定绑定的账户ID（可选）

        Returns:
            Dict: {"success": bool, "message": str, "cookie": str, ...}
        """
        account = str(account or "").strip()
        password = str(password or "").strip()
        if not account or not password:
            return {"success": False, "message": "账号和密码不能为空"}

        session = self._create_session()
        pub = fetch_login_pubkey()

        # 1. 设备指纹与 setDeviceCookie
        try:
            r_pub = session.get(f"{UPASS_BASE}/common/getFingerprintRsaInfo?", headers=HEADERS, timeout=10)
            finger_pub = r_pub.json().get('pubkey', '')
            if finger_pub:
                # AES-ECB + RSA 加密设备指纹
                if finger_pub.startswith('-----BEGIN') and '\n' not in finger_pub:
                    body = finger_pub.replace('-----BEGIN PUBLIC KEY-----', '').replace('-----END PUBLIC KEY-----', '').strip().replace('\\/', '/')
                    lines = [body[i:i+64] for i in range(0, len(body), 64)]
                    finger_pub = '-----BEGIN PUBLIC KEY-----\n' + '\n'.join(lines) + '\n-----END PUBLIC KEY-----'
                
                aes_key = ''.join(random.choice('0123456789abcdef') for _ in range(16))
                e_str = '{"browser":"Chrome","version":"146.0.0.0","platform":"Win32"}'
                cipher = AES.new(aes_key.encode('utf-8'), AES.MODE_ECB)
                enc_data = base64.b64encode(cipher.encrypt(pad(e_str.encode('utf-8'), 16))).decode()
                rsa_k = RSA.import_key(finger_pub)
                c_rsa = PKCS1_v1_5.new(rsa_k)
                enc_k = base64.b64encode(c_rsa.encrypt(aes_key.encode('utf-8'))).decode()
                collections = enc_k + '#' + enc_data

                v1 = get_v()
                session.cookies.set('v', v1)
                hw_res = session.post(
                    f"{HAWKEYE_BASE}/v1/hawkeye/generate",
                    headers=HEADERS,
                    cookies={'v': v1},
                    data={'pass_code': '', 'user_id': 'null', 'source_type': 'web', 'collections': collections, 'protocol': 'fingerprint_1'},
                    timeout=10
                ).json()
                if hw_res.get('data'):
                    session.post(
                        f"{UPASS_BASE}/common/setDeviceCookie",
                        headers=HEADERS,
                        data={
                            'u_dpass': hw_res['data']['pass_code'],
                            'u_did': hw_res['data']['device_code'],
                            'u_uver': '1.0.0',
                            'expires_time': hw_res['data']['expires_time'],
                        },
                        timeout=10
                    )
        except Exception as fe:
            logger.debug(f"指纹生成跳过: {fe}")

        # 2. 初始 getGS 并触发第一次预请求（注册服务端登录会话上下文）
        crnd = get_crnd()
        uname_enc = rsa_encrypt(account, pub)
        gs_res = session.post(
            f"{UPASS_BASE}/user/getGS",
            headers=HEADERS,
            data={'uname': uname_enc, 'rsa_version': pub['v'], 'crnd': crnd},
            timeout=10
        ).json()

        dsk = gs_res.get('dsk', '')
        ssv = gs_res.get('ssv', '')
        dsv = gs_res.get('dsv', '')
        if ssv and dsk and dsv:
            crnd_dsk_sha256 = sha256_hex(crnd + dsk)
            sb = get_passwd_salt(ssv, crnd_dsk_sha256)
            hmac_res = hmac_sha256_hex(sb, str_md5(password))
            passwd_salt = get_passwd_salt2(hmac_res, sha256_hex(dsv))
        else:
            passwd_salt = ''

        v0 = get_v()
        session.cookies.set('v', v0)
        h0 = dict(HEADERS)
        h0['hexin-v'] = v0
        try:
            session.post(
                f"{UPASS_BASE}/login/dologinreturnjson2",
                headers=h0,
                data={
                    'uname': uname_enc,
                    'passwd': rsa_encrypt(str_md5(password), pub),
                    'saltLoginTimes': '1',
                    'longLogin': 'on',
                    'rsa_version': pub['v'],
                    'source': 'pc_web',
                    'request_type': 'login',
                    'captcha_type': '4',
                    'upwd_score': 65,
                    'passwdSalt': passwd_salt,
                    'dsk': dsk,
                    'crnd': crnd,
                    'ttype': 'WEB',
                    'sdtis': 'C22',
                    'timestamp': int(time.time()),
                },
                timeout=10
            )
        except Exception:
            pass

        # 3. 二次刷新 getGS 盐并求解滑块验证码
        try:
            gs_res2 = session.post(
                f"{UPASS_BASE}/user/getGS",
                headers=HEADERS,
                data={'uname': uname_enc, 'rsa_version': pub['v'], 'crnd': crnd},
                timeout=10
            ).json()
            if gs_res2.get('dsk'):
                dsk = gs_res2.get('dsk', dsk)
                ssv = gs_res2.get('ssv', ssv)
                dsv = gs_res2.get('dsv', dsv)
                if ssv and dsk and dsv:
                    crnd_dsk_sha256 = sha256_hex(crnd + dsk)
                    sb = get_passwd_salt(ssv, crnd_dsk_sha256)
                    hmac_res = hmac_sha256_hex(sb, str_md5(password))
                    passwd_salt = get_passwd_salt2(hmac_res, sha256_hex(dsv))
        except Exception:
            pass

        ticket = phrase = signature = None
        for _ in range(5):
            ticket, phrase, signature = CaptchaSolver.solve(session)
            if ticket:
                break
            time.sleep(1)

        if not ticket:
            return {"success": False, "message": "滑块验证码解析失败，请稍后重试"}

        payload = {
            'uname': uname_enc,
            'passwd': rsa_encrypt(str_md5(password), pub),
            'saltLoginTimes': '1',
            'longLogin': 'on',
            'rsa_version': pub['v'],
            'source': 'pc_web',
            'request_type': 'login',
            'captcha_type': '4',
            'upwd_score': 65,
            'passwdSalt': passwd_salt,
            'dsk': dsk,
            'crnd': crnd,
            'ttype': 'WEB',
            'sdtis': 'C22',
            'timestamp': int(time.time()),
            'captcha_phrase': phrase,
            'captcha_ticket': ticket,
            'captcha_signature': signature,
        }

        v2 = get_v()
        session.cookies.set('v', v2)
        login_headers = dict(HEADERS)
        login_headers.update({
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': UPASS_BASE,
            'Referer': f'{UPASS_BASE}/login?redir=HTTP_REFERER',
            'X-Requested-With': 'XMLHttpRequest',
            'hexin-v': v2,
        })

        try:
            r = session.post(f"{UPASS_BASE}/login/dologinreturnjson2", headers=login_headers, data=payload, timeout=15)
            resp = r.json()
            errorcode = resp.get("errorcode", -1)
            errormsg = resp.get("errormsg", "")

            if errorcode == -11032:
                return {"success": False, "message": "用户名或密码错误，请核对后重新输入"}
            if errorcode == -10510:
                return {"success": False, "message": "该账号已被同花顺风控限制密码登录，请改用【手机验证码登录】"}
            if errorcode != 0:
                return {"success": False, "message": f"登录失败: {errormsg or '未知错误'}", "errorcode": errorcode}

            # 登录成功
            cookie_dict = {c.name: c.value for c in session.cookies if c.name != 'v'}
            raw_cookie = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

            # 激活模拟盘席位
            from ths_connector import activate_mncg_trade_session
            act_res = activate_mncg_trade_session(raw_cookie, session=session)
            final_cookie = act_res.get("cookie") if act_res.get("success") else raw_cookie

            # 绑定账户
            from account_manager import account_manager
            bind_res = account_manager.add_account(account, final_cookie)

            logger.info(f"🎉 [THS-AUTH] 账号 {account} 密码登录成功并已激活席位！")
            return {
                "success": True,
                "message": "同花顺账号密码登录成功，模拟盘交易席位已激活并绑定！",
                "cookie": final_cookie,
                "username": account,
                "account_info": bind_res.get("data", {}),
                "account_id": bind_res.get("data", {}).get("id") or account_id,
            }

        except Exception as e:
            logger.error(f"账号密码登录异常: {e}")
            return {"success": False, "message": f"登录认证请求异常: {str(e)}"}


# 全局单例
ths_auth_client = THSAuthClient()
