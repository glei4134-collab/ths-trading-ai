# -*- coding: utf-8 -*-
"""纯 Python 版 v（chameleon cookie）生成器 —— 从 v_new.js 算法翻译，环境参数与真实浏览器（Chrome/146）一致"""
import time
import random
import re as _re

# ========== 浏览器环境参数（MCP 浏览器实测，Chrome/146 Windows） ==========
BROWSER_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36'
NAV_PLATFORM = 'Win32'
NAV_VENDOR = 'Google Inc.'
NAV_LANGUAGE = 'zh-CN'
NAV_LANGUAGES = ['zh-CN', 'zh']
NAV_WEBDRIVER = False
NAV_DO_NOT_TRACK = None
NAV_JAVA_ENABLED = False
NAV_PLUGIN_NAMES = ['PDF Viewer', 'Chrome PDF Viewer', 'Chromium PDF Viewer', 'Microsoft Edge PDF Viewer', 'WebKit built-in PDF']
DOC_ONTOUCHSTART = False
DOC_DEFAULT_CHARSET = None
WIN_LOCALSTORAGE = True
WIN_WEBGL2 = True
WIN_ACTIVEX = False

# ---------- 字符串解码器（来自 v_new.js） ----------
def at(s):
    if not s:
        return ""
    out = []
    v = 9527
    for f, ch in enumerate(s):
        l = ord(ch)
        out.append(chr(l ^ v))
        v = (v * f % 256) + 2333
    return "".join(out)

def v_dec(s):
    if not s:
        return ""
    out = []
    o = 9527
    for ch in s:
        v = ord(ch)
        out.append(chr(v ^ o))
        o = v
    return "".join(out)

def ot(s):
    if not s:
        return ""
    out = []
    key = "V587"
    f = 2333
    for ch in s:
        f = (f + 1) % len(key)
        out.append(chr(ord(ch) ^ ord(key[f])))
    return "".join(out)

# ---------- JS 32 位整数模拟 ----------
def to_int32(x):
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x >= 0x80000000 else x

# ---------- Jn 工具 ----------
def strhash(s):
    c = 0
    for ch in s:
        c = to_int32(to_int32(c) << 5) - c + ord(ch)
        c &= 0xFFFFFFFF
    return c

def random_u32():
    return int(random.random() * 4294967295) & 0xFFFFFFFF

def time_now():
    return int(time.time()) & 0xFFFFFFFF

# 服务器时间：请求 s.thsi.cn 的 time.1 接口（浏览器 chameleon 同款），带缓存
_server_time_cache = {'t': 0, 'v': 0}

def server_time_now(force=False):
    import requests
    now = time.time()
    if not force and _server_time_cache['v'] and now - _server_time_cache['t'] < 600:
        return _server_time_cache['v']
    try:
        ts = int(now * 1000 / 1200000)
        url = f'https://s.thsi.cn/js/chameleon/time.1{ts}.js'
        r = requests.get(url, timeout=10,
                         headers={'User-Agent': BROWSER_UA,
                                  'Referer': 'https://upass.10jqka.com.cn/'})
        m = _re.search(r'TOKEN_SERVER_TIME=([\d.]+)', r.text)
        if m:
            v = int(float(m.group(1)))
            _server_time_cache['t'] = now
            _server_time_cache['v'] = v
            return v
    except Exception:
        pass
    # 兜底：本地时间（time.1 与本地时间基本同步）
    return int(time.time())

# ---------- zn 编码器 ----------
BASE64_TABLE = at("\u2576\u095f\u0979\u09d5\u0995\u091b\u09a9\u09f9\u09bd\u09f7\u0989\u09fd\u09f5\u09f3\u09f9\u0a41\u0a4d\u098f\u0999\u0905\u0975\u09cb\u09a9\u09a9\u099d\u0927\u0933\u0913\u0a6b\u0999\u09a3\u0937\u098b\u09f5\u0933\u0a7b\u091b\u09b1\u0a63\u095f\u09fb\u094d\u0993\u0943\u092b\u0949\u09a3\u09e7\u09cb\u0925\u0993\u09ab\u09f0\u092c\u092c\u0942\u0950\u09c8\u0944\u09c6\u0990\u0944\u09cb\u098e")

def zn_base64_encode(data):
    m = BASE64_TABLE
    out = []
    i = 0
    n = len(data)
    while i < n:
        l = (data[i] << 16) | (data[i+1] << 8) | data[i+2]
        out.append(m[l >> 18 & 0x3F])
        out.append(m[l >> 12 & 0x3F])
        out.append(m[l >> 6 & 0x3F])
        out.append(m[l & 0x3F])
        i += 3
    return "".join(out)

def zn_checksum(buf):
    e = 0
    for b in buf:
        e = to_int32(to_int32(e) << 5) - e + b
    return to_int32(e) & 0xFF

def zn_encode(buf):
    r = zn_checksum(buf)
    e = [3, r]
    u = r
    for b in buf:
        e.append(b ^ (u & 0xFF))
        u = ~to_int32(u * 131)
    return zn_base64_encode(e)

def zn_decode(s):
    m = BASE64_TABLE
    idx = {c: i for i, c in enumerate(m)}
    data = []
    i = 0
    n = len(s)
    while i < n:
        h = (idx[s[i]] << 18) | (idx[s[i+1]] << 12) | (idx[s[i+2]] << 6) | idx[s[i+3]]
        data.append(h >> 16 & 0xFF)
        data.append(h >> 8 & 0xFF)
        data.append(h & 0xFF)
        i += 4
    if data[0] != 3:
        return None
    a = data[1]
    out = []
    u = a
    for j in range(2, len(data)):
        out.append(data[j] ^ (u & 0xFF))
        u = ~to_int32(u * 131)
    if zn_checksum(out) != a:
        return None
    return out

# ---------- qn 缓冲区 ----------
FIELDS = [4, 4, 4, 4, 1, 1, 1, 3, 2, 2, 2, 2, 2, 2, 2, 4, 2, 1]

def qn_to_buffer(vals):
    """toBuffer()：字段值按大端序写入（JS: c[d]=l&255; --d; l>>=8，从后往前填低字节）"""
    out = []
    for v, p in zip(vals, FIELDS):
        l = (v if v is not None else 0) & 0xFFFFFFFF
        # 大端：高字节在前
        for shift in range(p - 1, -1, -1):
            out.append((l >> (8 * shift)) & 0xFF)
    return out

def qn_decode_buffer(buf):
    vals = []
    pos = 0
    for p in FIELDS:
        l = 0
        for _ in range(p):
            l = (l << 8) + buf[pos]
            pos += 1
        vals.append(l & 0xFFFFFFFF)
    return vals

# ---------- tt 浏览器特征（真实浏览器值） ----------
def get_platform():
    regexes = ["^Win32", "^Win64", "^Linux armv|Android", "^Android", "^iPhone",
               "^iPad", "^MacIntel", "^Linux [ix]\\d+", "^ARM", "^iPod", "^BlackBerry"]
    for i, r in enumerate(regexes):
        if _re.match(r, NAV_PLATFORM):
            return i + 1
    return 0

def get_browser_index():
    if NAV_VENDOR == "Google Inc.":
        return 10
    return 0

def get_plugin_num():
    return len(NAV_PLUGIN_NAMES)

def get_browser_feature():
    """tn = [q,j,H,X,L,En,D,bn,G,U,$,F,J,nn,Z,K]，n[0] 为最低位；浏览器实测 = 3748"""
    q = NAV_JAVA_ENABLED
    j = any(_re.search(r'ShockwaveFlash', p, _re.I) for p in NAV_PLUGIN_NAMES) or WIN_ACTIVEX
    H = any(_re.search(r'PDF|Acrobat', p, _re.I) for p in NAV_PLUGIN_NAMES)
    X = any(_re.search(r'Native Client', p, _re.I) for p in NAV_PLUGIN_NAMES)
    L = bool(NAV_WEBDRIVER)
    En = WIN_LOCALSTORAGE
    D = ("1" == (NAV_DO_NOT_TRACK or ""))
    bn = bool(_re.search(r'zh-cn', NAV_LANGUAGE or "", _re.I))
    G = DOC_ONTOUCHSTART
    U = (time.localtime().tm_gmtoff == 8 * 3600)
    webgl2 = WIN_WEBGL2
    F = bool(NAV_LANGUAGES)
    J = (DOC_DEFAULT_CHARSET == "iso-8859-1")
    bits = [q, j, H, X, L, En, D, bn, G, U, webgl2, F, J, False, False, False]
    val = 0
    for i, b in enumerate(bits):
        if b:
            val |= (1 << i)
    return val

# ---------- v 生成 ----------
_counter = 0

def get_v(old_v=None, server_time=None, behavior=None):
    """生成 v。old_v 可传之前的 v（复用随机字段）；server_time 可传 time.1 服务器时间"""
    global _counter
    _counter += 1
    vals = [0] * 18
    random_val = None
    if old_v and len(old_v) == 60:
        t = zn_decode(old_v)
        if t:
            old_vals = qn_decode_buffer(t)
            if old_vals[0] != 0:
                random_val = old_vals[0]
    if random_val is None:
        random_val = random_u32()
    vals[0] = random_val
    # P() 初始化
    if server_time is None:
        server_time = server_time_now()
    vals[1] = server_time & 0xFFFFFFFF
    vals[3] = strhash(BROWSER_UA)
    vals[4] = get_platform()
    vals[5] = get_browser_index()
    vals[6] = get_plugin_num()
    vals[13] = get_browser_feature()
    vals[17] = 3
    # O() 更新：鼠标/交互数据用随机值模拟（浏览器实测非 0）
    vals[16] = _counter & 0xFFFF
    vals[2] = time_now()
    if behavior:
        # ??/???????????????????? 11/12 ?????????
        vals[7] = behavior.get('mm', 55)      # mouseMove
        vals[8] = behavior.get('click', 5)    # mouseClick
        vals[9] = behavior.get('wheel', 0)    # mouseWhell
        vals[10] = behavior.get('key', 0)     # keyDown
        vals[11] = behavior.get('x', 583)     # clickX??????
        vals[12] = behavior.get('y', 524)     # clickY??????
    else:
        vals[7] = random.randint(1, 600)      # mouseMove
        vals[8] = random.randint(0, 30)       # mouseClick
        vals[9] = random.randint(0, 5)        # mouseWhell
        vals[10] = random.randint(0, 60)      # keyDown
        vals[11] = random.randint(100, 900)   # clickX
        vals[12] = random.randint(200, 700)   # clickY
    buf = qn_to_buffer(vals)
    return zn_encode(buf)

if __name__ == '__main__':
    print(get_v())
