"""
tools.py — เครื่องมือ (tools) ที่ AI Agent เรียกใช้ได้

รวมเครื่องมือคำนวณ/เวลา/เว็บ และเครื่องมือจัดการไฟล์ที่ทำงานใน "โฟลเดอร์งาน"
(workspace) ที่ตั้งค่าได้ — ปลอดภัยเพราะทุกการอ่าน/เขียนถูกล็อกให้อยู่ในโฟลเดอร์นี้เท่านั้น

วิธีเพิ่ม tool ใหม่:
1. เขียนฟังก์ชัน Python (รับ argument, คืนค่าเป็น string)
2. ลงทะเบียนใน TOOLS
3. เพิ่ม schema ใน TOOL_SCHEMAS
"""

from __future__ import annotations

import ast
import datetime
import glob
import operator
import os
import shutil
import subprocess
import tempfile
import sys as _sys
import urllib.request
import urllib.error
import ipaddress
import socket
import http.client
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp_client import mcp_manager
import knowledge_store
import winproc  # QUAL-1: no_window_kwargs() ที่ใช้ร่วมกับ server.py

# ---------------------------------------------------------------------------
# โฟลเดอร์งาน (workspace) — เปลี่ยนได้ผ่าน set_workspace()
# ค่าเริ่มต้น: โฟลเดอร์ย่อย "workspace" ข้างโปรแกรม
# ---------------------------------------------------------------------------
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_WS = os.path.join(_BASE_DIR, "workspace")
_ALLOWED_WORKSPACE_ROOTS = tuple(
    os.path.abspath(p)
    for p in (_BASE_DIR, os.path.expanduser("~"), tempfile.gettempdir())
    if p
)
WORKSPACE = _DEFAULT_WS

# เมื่อเปิดโหมด Co-Work: ปลดล็อกให้ตั้งโฟลเดอร์งานที่ไหนก็ได้ที่ผู้ใช้เลือก
# (ยังกัน root ของไดรฟ์และโฟลเดอร์ระบบไว้เสมอเพื่อความปลอดภัย)
_COWORK = False

# ---------------------------------------------------------------------------
# MCP — config อยู่ข้างโปรแกรม (.exe ใช้ข้างไฟล์ .exe ไม่ใช่ temp ของ PyInstaller)
# QUAL-5: เดิมโหลดตอน import ทำให้ (ก) import tools = spawn subprocess ทันที
# (ข) startup ถูกบล็อกได้ 10s+/server (ค) pytest ก็ spawn ด้วยถ้ามี mcp.json
# ตอนนี้ต้องเรียก init_mcp() เอง (server.main() เรียกใน background thread)
# ---------------------------------------------------------------------------
if getattr(_sys, "frozen", False):
    _APP_DIR = os.path.dirname(_sys.executable)
else:
    _APP_DIR = _BASE_DIR
MCP_CONFIG_PATH = os.path.join(_APP_DIR, "mcp.json")


def init_mcp() -> int:
    """โหลด/รีโหลด MCP servers จาก mcp.json. คืนจำนวน tools ที่ได้มา."""
    mcp_manager.stop_all()
    mcp_manager.load_config(MCP_CONFIG_PATH)
    return len(mcp_manager.tool_schemas)


def is_mcp_tool(name: str) -> bool:
    """SEC-6: server ใช้เช็คว่า tool call นี้เป็นของ MCP (ต้องผ่าน confirm ครั้งแรก)."""
    return name in mcp_manager.tool_mapping


def set_cowork(flag: bool) -> None:
    """เปิด/ปิดโหมด Co-Work. เปิดอยู่ = อนุญาตโฟลเดอร์งานนอก home/temp ได้
    (ยกเว้น root ไดรฟ์และโฟลเดอร์ระบบที่ยังถูกปฏิเสธเสมอ)."""
    global _COWORK
    _COWORK = bool(flag)


def _is_blocked_root(p: str) -> bool:
    """กันการตั้ง workspace ไปยัง root ของไดรฟ์/ไฟล์ซิสเต็ม หรือโฟลเดอร์ระบบสำคัญ."""
    norm = os.path.normpath(os.path.abspath(p))
    if os.path.dirname(norm) == norm:      # root เช่น C:\ หรือ /
        return True
    low = norm.lower()
    sys_dirs = (r"c:\windows", r"c:\program files", r"c:\program files (x86)",
                r"c:\programdata", "/etc", "/bin", "/usr", "/boot", "/sys")
    for d in sys_dirs:
        if low == d or low.startswith(d + os.sep) or low.startswith(d + "/"):
            return True
    return False


def _is_under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
    except ValueError:
        return False


def _is_allowed_workspace(p: str) -> bool:
    """อนุญาต workspace เฉพาะใต้โฟลเดอร์โปรเจกต์, home ของผู้ใช้, หรือ temp.

    ถ้าเปิดโหมด Co-Work อยู่: อนุญาตทุกโฟลเดอร์ที่ผู้ใช้เลือก
    ยกเว้น root ของไดรฟ์/โฟลเดอร์ระบบที่ยังถูกปฏิเสธเสมอ."""
    if _is_blocked_root(p):
        return False
    if _COWORK:
        return True
    return any(_is_under(p, root) for root in _ALLOWED_WORKSPACE_ROOTS)


def set_workspace(path: str | None) -> str:
    """ตั้งโฟลเดอร์งาน (สร้างให้ถ้ายังไม่มี). คืนพาธจริงที่ใช้.

    ถ้าพาธชี้ไป root ของไดรฟ์/โฟลเดอร์ระบบ จะปฏิเสธและกลับไปใช้โฟลเดอร์งานเริ่มต้น.
    """
    global WORKSPACE
    if path:
        p = os.path.abspath(os.path.expanduser(path.strip()))
        if not _is_allowed_workspace(p):
            p = _DEFAULT_WS
    else:
        p = _DEFAULT_WS
    os.makedirs(p, exist_ok=True)
    WORKSPACE = p
    return WORKSPACE


def _resolve(path: str) -> str | None:
    """รวมพาธกับ workspace และกันไม่ให้หลุดออกนอกโฟลเดอร์งาน (path traversal)."""
    path = path.lstrip("/\\")
    if os.path.splitdrive(path)[0]:
        path = os.path.splitdrive(path)[1].lstrip("/\\")
    full = os.path.abspath(os.path.join(WORKSPACE, path))
    root = os.path.abspath(WORKSPACE)
    if full == root or full.startswith(root + os.sep):
        return full
    return None


# ---------------------------------------------------------------------------
# Calculator — คำนวณนิพจน์อย่างปลอดภัย
# ---------------------------------------------------------------------------
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("นิพจน์ไม่ปลอดภัยหรือไม่รองรับ")


def calculator(expression: str) -> str:
    """คำนวณนิพจน์เลข เช่น '12 * (3 + 4)'."""
    try:
        return f"{expression} = {_eval(ast.parse(expression, mode='eval').body)}"
    except Exception as e:  # noqa: BLE001
        return f"คำนวณไม่ได้: {e}"


def get_current_time(timezone: str | None = None) -> str:
    """วันและเวลาปัจจุบัน รองรับ timezone แบบ IANA เช่น Asia/Bangkok หรือ UTC."""
    tz = None
    label = "เวลาท้องถิ่น"
    if timezone:
        try:
            tz = ZoneInfo(timezone)
            label = timezone
        except ZoneInfoNotFoundError:
            return f"ไม่รู้จัก timezone: {timezone}"
    now = datetime.datetime.now(tz).astimezone() if tz is None else datetime.datetime.now(tz)
    return now.strftime(f"วันนี้คือ %A ที่ %d/%m/%Y เวลา %H:%M:%S ({label})")


# ---------------------------------------------------------------------------
# ไฟล์ — ทำงานในโฟลเดอร์งานเท่านั้น
# ---------------------------------------------------------------------------
def list_files(subdir: str = "") -> str:
    """แสดงรายชื่อไฟล์/โฟลเดอร์ในโฟลเดอร์งาน (หรือโฟลเดอร์ย่อย)."""
    full = _resolve(subdir or ".")
    if full is None:
        return "ปฏิเสธ: อยู่นอกโฟลเดอร์งาน"
    if not os.path.isdir(full):
        return f"ไม่พบโฟลเดอร์: {subdir}"
    items = []
    for name in sorted(os.listdir(full)):
        p = os.path.join(full, name)
        if os.path.isdir(p):
            items.append(f"[DIR] {name}/")
        else:
            items.append(f"      {name} ({os.path.getsize(p)} bytes)")
    return "\n".join(items) if items else "(โฟลเดอร์ว่าง)"


def read_file(path: str) -> str:
    """อ่านไฟล์ข้อความในโฟลเดอร์งาน."""
    full = _resolve(path)
    if full is None:
        return "ปฏิเสธ: อยู่นอกโฟลเดอร์งาน"
    if not os.path.isfile(full):
        return f"ไม่พบไฟล์: {path}"
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
        if len(data) > 8000:
            return data[:8000] + "\n\n…(ตัดเนื้อหาที่ 8000 ตัวอักษร — ไฟล์ยาวกว่านี้)"
        return data
    except Exception as e:  # noqa: BLE001
        return f"อ่านไฟล์ไม่ได้: {e}"


def write_file(path: str, content: str) -> str:
    """เขียนไฟล์ในโฟลเดอร์งาน (ถ้ามีอยู่แล้วจะสำรองเป็น .bak ก่อนทับ).

    หมายเหตุ: ในเวอร์ชัน UI การเขียนจริงจะถูกถามยืนยันก่อน (ดู server.py).
    """
    full = _resolve(path)
    if full is None:
        return "ปฏิเสธ: อยู่นอกโฟลเดอร์งาน"
    try:
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        backed = ""
        if os.path.isfile(full):
            bak = full + ".bak"
            with open(full, "r", encoding="utf-8", errors="replace") as src:
                old = src.read()
            with open(bak, "w", encoding="utf-8") as dst:
                dst.write(old)
            backed = f" (สำรองของเดิมเป็น {os.path.basename(bak)})"
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"บันทึก {path} เรียบร้อย ({len(content)} ตัวอักษร){backed}"
    except Exception as e:  # noqa: BLE001
        return f"เขียนไฟล์ไม่ได้: {e}"


def file_exists(path: str) -> bool:
    full = _resolve(path)
    return bool(full and os.path.isfile(full))


# ---------------------------------------------------------------------------
# F1: Memory files — ไฟล์คำสั่ง/ความจำประจำโฟลเดอร์งาน (inject เข้า system prompt)
# ---------------------------------------------------------------------------
AGENT_FILE = "_agent.md"      # คำสั่งประจำโฟลเดอร์ (ผู้ใช้เขียนเอง)
MEMORY_FILE = "_memory.md"    # ความจำสะสม (AI เพิ่มผ่าน tool remember + ผู้ใช้แก้ได้)
# โมเดล local context สั้น (4k–8k) — ต้อง cap ขนาดที่ inject เสมอ
AGENT_MAX_CHARS = 4000        # _agent.md เก็บ "หัวไฟล์" (คำสั่งหลักมักอยู่ต้นไฟล์)
MEMORY_MAX_CHARS = 3000       # _memory.md เก็บ "ท้ายไฟล์" (บันทึกใหม่สุด append ท้าย)


def _read_ws_text(name: str) -> str:
    """อ่านไฟล์ข้อความใน workspace แบบเงียบ — คืน '' ถ้าไม่มี/อ่านไม่ได้."""
    full = _resolve(name)
    if not full or not os.path.isfile(full):
        return ""
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except Exception:  # noqa: BLE001
        return ""


def workspace_context() -> str:
    """บริบทประจำโฟลเดอร์งานสำหรับต่อท้าย system prompt (F1).

    คืน '' ถ้าไม่มีทั้ง _agent.md และ _memory.md — จะไม่เปลืองบริบทของโมเดลเลย.
    """
    parts: list[str] = []
    agent = _read_ws_text(AGENT_FILE)
    if agent:
        if len(agent) > AGENT_MAX_CHARS:
            agent = agent[:AGENT_MAX_CHARS] + "\n…(ตัดที่ {} ตัวอักษร)".format(AGENT_MAX_CHARS)
        parts.append(f"[คำสั่งประจำโฟลเดอร์งาน — {AGENT_FILE}]\n{agent}")
    memory = _read_ws_text(MEMORY_FILE)
    if memory:
        if len(memory) > MEMORY_MAX_CHARS:
            # เก็บท้ายไฟล์ (บันทึกล่าสุด) และตัดบรรทัดแรกที่ขาดครึ่งทิ้ง
            memory = memory[-MEMORY_MAX_CHARS:]
            nl = memory.find("\n")
            if 0 <= nl < 200:
                memory = memory[nl + 1:]
        parts.append(f"[ความจำของโฟลเดอร์นี้ — {MEMORY_FILE}]\n{memory}")
    return "\n\n".join(parts)


def build_memory_content(text: str) -> str:
    """สร้างเนื้อหา _memory.md ฉบับเต็มหลังเพิ่มบันทึกใหม่ (ใช้ทำ proposal ให้ผู้ใช้ยืนยัน).

    เกินเพดานเมื่อไร ตัดบันทึกเก่าสุด (หัวไฟล์) ทิ้งก่อน — ความจำใหม่สำคัญกว่า.
    """
    text = (text or "").strip()
    stamp = datetime.date.today().isoformat()
    entry = f"- [{stamp}] {text}"
    old = _read_ws_text(MEMORY_FILE)
    new = (old + "\n" if old else "") + entry
    if len(new) > MEMORY_MAX_CHARS:
        new = new[-MEMORY_MAX_CHARS:]
        nl = new.find("\n")
        if 0 <= nl < 200:
            new = new[nl + 1:]
    return new


def remember(text: str) -> str:
    """จดสิ่งสำคัญลงไฟล์ความจำ _memory.md ของโฟลเดอร์งาน.

    หมายเหตุ: ในเวอร์ชัน UI เครื่องมือนี้อยู่ใน WRITE_TOOLS — server จะดักเป็น proposal
    ให้ผู้ใช้กดยืนยันก่อนเขียนจริงเสมอ (ฟังก์ชันนี้ถูกเรียกตรงเฉพาะนอก UI/ใน test).
    """
    if not (text or "").strip():
        return "ไม่มีข้อความให้จำ"
    return write_file(MEMORY_FILE, build_memory_content(text))


def _is_public_host(host: str) -> bool:
    """True ถ้า host ชี้ไปยังที่อยู่สาธารณะเท่านั้น (กัน SSRF เข้าถึง localhost/วงในเครือข่าย)."""
    return _resolve_public_ip(host) is not None


def _resolve_public_ip(host: str) -> str | None:
    """Resolve host แล้วคืน IP สาธารณะตัวแรก — คืน None ถ้า resolve ไม่ได้ หรือมีที่อยู่
    ใดเป็นวงใน/สงวนไว้แม้แค่ตัวเดียว (เข้มงวดเหมือน _is_public_host เดิม).

    SEC-5: IP ที่คืนมานี้เอาไปใช้ "pin" การเชื่อมต่อจริงใน fetch_url — กัน DNS-rebinding
    TOCTOU (เดิม _is_public_host เช็คแล้วปล่อยให้ urlopen resolve ซ้ำเอง; ถ้า DNS มี TTL
    สั้นและสลับ IP ระหว่างสองครั้งนี้ อาจเข้าถึง IP วงในได้ตอน urlopen จริง — ตอนนี้ resolve
    ครั้งเดียวแล้วต่อ socket ไปยัง IP นั้นตรงๆ ไม่ resolve DNS ซ้ำอีกตอนเชื่อมต่อจริง)
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001 — เช่น resolve ไม่ได้
        return None
    ips: list[str] = []
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None
        if not addr.is_global:
            return None
        ips.append(ip)
    return ips[0] if ips else None


class _PinnedIPResolver:
    """เก็บ mapping hostname -> IP สาธารณะที่ validate แล้ว ต่อการเรียก fetch_url หนึ่งครั้ง
    (รวมทุก redirect ในคำขอเดียวกัน) — resolve ครั้งเดียวต่อ host แล้ว cache ไว้ใช้ซ้ำ
    ปิดช่อง DNS-rebinding TOCTOU (SEC-5)."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def pin(self, host: str) -> str:
        ip = self._cache.get(host)
        if ip is None:
            ip = _resolve_public_ip(host)
            if ip is None:
                raise urllib.error.URLError(f"ปฏิเสธ: {host} ไม่ใช่ที่อยู่สาธารณะ (หรือ resolve ไม่ได้)")
            self._cache[host] = ip
        return ip


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection ที่เชื่อมต่อไปยัง IP ที่ pin ไว้ตรงๆ แทนที่จะ resolve DNS ของ host ซ้ำ."""

    def __init__(self, host, resolver: _PinnedIPResolver, *args, **kwargs):
        super().__init__(host, *args, **kwargs)
        self._resolver = resolver

    def connect(self):
        ip = self._resolver.pin(self.host)
        self.sock = self._create_connection((ip, self.port), self.timeout, self.source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """เหมือน _PinnedHTTPConnection แต่ wrap TLS โดยยังใช้ hostname เดิมสำหรับ SNI/ตรวจ cert
    (ตรวจ cert กับชื่อโดเมนจริง ไม่ใช่กับ IP — ป้องกัน MITM ตามปกติเหมือนเดิมทุกอย่าง
    ต่างแค่ต่อ socket ไปยัง IP ที่ pin ไว้แทนการ resolve ซ้ำ)."""

    def __init__(self, host, resolver: _PinnedIPResolver, *args, **kwargs):
        super().__init__(host, *args, **kwargs)
        self._resolver = resolver

    def connect(self):
        ip = self._resolver.pin(self.host)
        sock = self._create_connection((ip, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, resolver: _PinnedIPResolver):
        super().__init__()
        self._resolver = resolver

    def http_open(self, req):
        return self.do_open(
            lambda host, **kw: _PinnedHTTPConnection(host, self._resolver, **kw), req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, resolver: _PinnedIPResolver):
        super().__init__()
        self._resolver = resolver

    def https_open(self, req):
        return self.do_open(
            lambda host, **kw: _PinnedHTTPSConnection(host, self._resolver, **kw),
            req, context=self._context)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """ตรวจปลายทางของทุก redirect ซ้ำ — กัน SSRF ที่ใช้ URL สาธารณะ 302 ไปยัง localhost/วงใน."""
    max_redirections = 3
    max_repeats = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        host = parsed.hostname
        if (parsed.scheme not in ("http", "https")
                or not host or not _is_public_host(host)):
            raise urllib.error.HTTPError(
                newurl, code, "ปฏิเสธ redirect ไปยังปลายทางที่ไม่ปลอดภัย", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_url(url: str) -> str:
    """ดึงข้อความ/HTML จาก URL สาธารณะ (ตัดมาแค่ต้น ๆ)."""
    if not url.startswith(("http://", "https://")):
        return "URL ต้องขึ้นต้นด้วย http:// หรือ https://"
    host = urlparse(url).hostname
    if not host or not _is_public_host(host):
        return "ปฏิเสธ: อนุญาตเฉพาะ URL สาธารณะ (กันการเข้าถึงเครื่อง/เครือข่ายภายใน)"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LMCoWork/1.0"})
        # SEC-5: pin การเชื่อมต่อจริงไปยัง IP ที่ validate แล้ว (ดู _PinnedIPResolver) —
        # กัน DNS-rebinding ระหว่างตรวจ host กับตอน connect จริง
        resolver = _PinnedIPResolver()
        opener = urllib.request.build_opener(
            _SafeRedirectHandler(), _PinnedHTTPHandler(resolver), _PinnedHTTPSHandler(resolver))
        with opener.open(req, timeout=15) as resp:  # noqa: S310
            data = resp.read(8000).decode("utf-8", errors="replace")
        if len(data) >= 8000:
            data += "\n\n…(ตัดเนื้อหาที่ 8000 ตัวอักษร)"
        return data
    except Exception as e:  # noqa: BLE001
        return f"ดึงข้อมูลไม่ได้: {e}"


# ---------------------------------------------------------------------------
# เสียง/เพลง — ตรวจหาไฟล์เสียงที่เสียหาย (corrupt) ด้วย ffmpeg
# ---------------------------------------------------------------------------
_AUDIO_EXTS = (
    ".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".wma",
    ".aiff", ".aif", ".alac", ".ape", ".wv", ".mka", ".mp2",
)


def _find_ffmpeg() -> str | None:
    """หา path ของ ffmpeg: ใน PATH ก่อน แล้วค่อยไล่ตำแหน่งติดตั้งทั่วไปบน Windows."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    candidates = [
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
        os.path.expandvars(r"%USERPROFILE%\scoop\shims\ffmpeg.exe"),
        os.path.expandvars(r"%ProgramData%\chocolatey\bin\ffmpeg.exe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    # โฟลเดอร์แพ็กเกจของ winget (Gyan.FFmpeg)
    base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    if os.path.isdir(base):
        for hit in glob.glob(os.path.join(base, "Gyan.FFmpeg*", "**", "ffmpeg.exe"),
                             recursive=True):
            return hit
    return None


def check_ffmpeg() -> str:
    """ตรวจว่ามี ffmpeg ในเครื่องหรือไม่ (ต้องใช้ตอนตรวจไฟล์เสียง). คืนสถานะ + ตำแหน่ง."""
    exe = _find_ffmpeg()
    if exe:
        try:
            out = subprocess.run([exe, "-version"], capture_output=True, text=True,
                                 timeout=15, **winproc.no_window_kwargs())
            text = (out.stdout or out.stderr or "").splitlines()
            first = text[0] if text else "(ตรวจเวอร์ชันไม่ได้)"
        except Exception as e:  # noqa: BLE001
            return f"พบ ffmpeg ที่ {exe} แต่เรียกใช้ไม่ได้: {e}"
        return f"ffmpeg พร้อมใช้งาน ✅\n{first}\nตำแหน่ง: {exe}"
    return (
        "ยังไม่พบ ffmpeg ในเครื่อง ❌ (จำเป็นสำหรับตรวจไฟล์เสียง)\n"
        "ติดตั้งเองได้ด้วยคำสั่งใน PowerShell/Terminal:\n"
        "    winget install --id Gyan.FFmpeg -e\n"
        "หรือสั่งให้ผมติดตั้งให้อัตโนมัติด้วยเครื่องมือ install_ffmpeg"
    )


def install_ffmpeg() -> str:
    """ติดตั้ง ffmpeg อัตโนมัติผ่าน winget (Windows). ใช้เมื่อยังไม่มี ffmpeg ในเครื่อง."""
    if _find_ffmpeg():
        return "มี ffmpeg อยู่แล้ว ไม่ต้องติดตั้งซ้ำ ✅"
    winget = shutil.which("winget")
    if not winget:
        return (
            "ไม่พบ winget ในเครื่องนี้ — ติดตั้ง ffmpeg เองได้จาก\n"
            "https://www.gyan.dev/ffmpeg/builds/  หรือ  https://ffmpeg.org/download.html\n"
            "แล้วเพิ่มโฟลเดอร์ bin ลงใน PATH"
        )
    try:
        proc = subprocess.run(
            [winget, "install", "--id", "Gyan.FFmpeg", "-e",
             "--accept-source-agreements", "--accept-package-agreements"],
            capture_output=True, text=True, timeout=900, **winproc.no_window_kwargs())
    except Exception as e:  # noqa: BLE001
        return f"ติดตั้งผ่าน winget ไม่สำเร็จ: {e}"
    tail = (proc.stdout or proc.stderr or "")[-600:]
    if _find_ffmpeg():
        return ("ติดตั้ง ffmpeg สำเร็จ ✅ — อาจต้องปิดแล้วเปิดโปรแกรมใหม่เพื่อให้ PATH อัปเดต\n"
                f"{tail}")
    return ("รัน winget เสร็จแล้วแต่ยังหา ffmpeg ไม่เจอ — ลองปิดแล้วเปิดโปรแกรม/เทอร์มินัลใหม่\n"
            f"ผลลัพธ์ winget:\n{tail}")


def _decode_check(exe: str, path: str) -> str | None:
    """decode ทั้งไฟล์ด้วย ffmpeg. คืน None ถ้าปกติ, หรือข้อความเหตุผลถ้าเสีย."""
    try:
        if os.path.getsize(path) == 0:
            return "ไฟล์ว่าง (0 bytes)"
    except OSError as e:
        return f"อ่านไฟล์ไม่ได้: {e}"
    try:
        proc = subprocess.run(
            [exe, "-v", "error", "-xerror", "-i", path, "-f", "null", "-"],
            capture_output=True, text=True, timeout=600, **winproc.no_window_kwargs())
    except subprocess.TimeoutExpired:
        return "decode นานเกินกำหนด (อาจเสียหาย)"
    except Exception as e:  # noqa: BLE001
        return f"เรียก ffmpeg ไม่ได้: {e}"
    err = (proc.stderr or "").strip()
    if proc.returncode != 0 or err:
        first = err.splitlines()[0] if err else f"exit code {proc.returncode}"
        return first[:200]
    return None


def check_audio_integrity(folder: str = "", recursive: bool = True, ext: str = "") -> str:
    """ตรวจหาไฟล์เพลง/ไฟล์เสียงที่เสียหายในโฟลเดอร์ โดย decode ทั้งไฟล์จริงด้วย ffmpeg.

    folder    : โฟลเดอร์ที่จะตรวจ (เว้นว่าง = โฟลเดอร์งานปัจจุบัน)
    recursive : ตรวจโฟลเดอร์ย่อยด้วยหรือไม่ (ดีฟอลต์ True)
    ext       : จำกัดเฉพาะนามสกุล คั่นด้วย comma เช่น 'mp3,flac' (เว้นว่าง = ทุกฟอร์แมตเสียงทั่วไป)
    """
    exe = _find_ffmpeg()
    if not exe:
        return check_ffmpeg()  # ยังไม่มี ffmpeg -> แจ้งวิธีติดตั้ง

    # เลือกโฟลเดอร์เป้าหมาย (การตรวจนี้ "อ่านอย่างเดียว" ไม่เขียนไฟล์ จึงอนุญาตโฟลเดอร์ใดก็ได้
    # ยกเว้น root ของไดรฟ์/โฟลเดอร์ระบบที่ยังกันไว้เสมอ)
    if folder:
        target = os.path.abspath(os.path.expanduser(folder.strip()))
        if _is_blocked_root(target):
            return "ปฏิเสธ: ตรวจที่ root ของไดรฟ์หรือโฟลเดอร์ระบบไม่ได้"
    else:
        target = os.path.abspath(WORKSPACE)
    if not os.path.isdir(target):
        return f"ไม่พบโฟลเดอร์: {target}"

    if ext.strip():
        wanted = tuple("." + e.strip().lower().lstrip(".") for e in ext.split(",") if e.strip())
    else:
        wanted = _AUDIO_EXTS

    files: list[str] = []
    if recursive:
        for root, _dirs, names in os.walk(target):
            for n in names:
                if n.lower().endswith(wanted):
                    files.append(os.path.join(root, n))
    else:
        for n in os.listdir(target):
            p = os.path.join(target, n)
            if os.path.isfile(p) and n.lower().endswith(wanted):
                files.append(p)
    files.sort()
    if not files:
        return f"ไม่พบไฟล์เสียงในโฟลเดอร์: {target}"

    corrupt: list[tuple[str, str]] = []
    for p in files:
        reason = _decode_check(exe, p)
        if reason is not None:
            corrupt.append((p, reason))

    total = len(files)
    if not corrupt:
        return f"ตรวจ {total} ไฟล์ — ไม่พบไฟล์เสีย ✅\nโฟลเดอร์: {target}"
    lines = [f"ตรวจ {total} ไฟล์ — พบไฟล์เสีย {len(corrupt)} ไฟล์ ❌",
             f"โฟลเดอร์: {target}", ""]
    for p, reason in corrupt:
        lines.append(f"• {os.path.relpath(p, target)}  — {reason}")
    return "\n".join(lines)


def collect_audio_files(folder: str = "", recursive: bool = True, ext: str = "") -> dict:
    """หาไฟล์เสียงในโฟลเดอร์ (สำหรับ UI ที่ตรวจทีละไฟล์พร้อม progress).

    คืน dict: {ok, error, target, files, ffmpeg}
    """
    exe = _find_ffmpeg()
    if not exe:
        return {"ok": False, "error": check_ffmpeg(), "target": folder, "files": [], "ffmpeg": None}
    if folder.strip():
        target = os.path.abspath(os.path.expanduser(folder.strip()))
        if _is_blocked_root(target):
            return {"ok": False, "error": "ปฏิเสธ: ตรวจที่ root/โฟลเดอร์ระบบไม่ได้",
                    "target": target, "files": [], "ffmpeg": exe}
    else:
        target = os.path.abspath(WORKSPACE)
    if not os.path.isdir(target):
        return {"ok": False, "error": f"ไม่พบโฟลเดอร์: {target}",
                "target": target, "files": [], "ffmpeg": exe}
    if ext.strip():
        wanted = tuple("." + e.strip().lower().lstrip(".") for e in ext.split(",") if e.strip())
    else:
        wanted = _AUDIO_EXTS
    files: list[str] = []
    if recursive:
        for root, _dirs, names in os.walk(target):
            for n in names:
                if n.lower().endswith(wanted):
                    files.append(os.path.join(root, n))
    else:
        for n in os.listdir(target):
            p = os.path.join(target, n)
            if os.path.isfile(p) and n.lower().endswith(wanted):
                files.append(p)
    files.sort()
    return {"ok": True, "error": "", "target": target, "files": files, "ffmpeg": exe}


def audio_decode_check(exe: str, path: str) -> str | None:
    """ตรวจไฟล์เดียว (ใช้ร่วมกับ collect_audio_files). คืน None ถ้าปกติ, ข้อความถ้าเสีย."""
    return _decode_check(exe, path)


# ---------------------------------------------------------------------------
# ตรวจไฟล์เสีย "ทุกชนิด" — เลือกวิธีตรวจตามประเภทไฟล์
# ---------------------------------------------------------------------------
_VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
               ".mpg", ".mpeg", ".ts", ".3gp", ".m2ts", ".vob")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp",
               ".heic", ".heif", ".jp2", ".ico")
# ไฟล์ที่ ffmpeg decode ตรวจได้ (เสียง+วิดีโอ+รูป)
_MEDIA_EXTS = tuple(set(_AUDIO_EXTS) | set(_VIDEO_EXTS) | set(_IMAGE_EXTS))
# ไฟล์ตระกูล ZIP (รวม Office/OpenDocument/epub/jar ฯลฯ)
_ZIP_EXTS = (".zip", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
             ".epub", ".jar", ".apk", ".cbz", ".whl", ".xpi")
_GZIP_EXTS = (".gz", ".tgz", ".gzip")
_PDF_EXTS = (".pdf",)


def _zip_check(path: str) -> str | None:
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()           # None = ทุกไฟล์ใน archive ผ่าน CRC
        return f"ไฟล์ในอาร์ไคฟ์เสีย: {bad}" if bad else None
    except zipfile.BadZipFile:
        return "ไม่ใช่ ZIP ที่ถูกต้อง/เสียหาย"
    except Exception as e:  # noqa: BLE001
        return f"เปิดอาร์ไคฟ์ไม่ได้: {e}"


def _gzip_check(path: str) -> str | None:
    import gzip
    try:
        with gzip.open(path, "rb") as f:
            while f.read(1024 * 1024):
                pass
        return None
    except Exception as e:  # noqa: BLE001
        return f"gzip เสียหาย: {e}"


def _pdf_check(path: str) -> str | None:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(8)
            f.seek(max(0, size - 2048))
            tail = f.read()
    except Exception as e:  # noqa: BLE001
        return f"อ่าน PDF ไม่ได้: {e}"
    if not head.startswith(b"%PDF-"):
        return "ไม่ใช่ PDF ที่ถูกต้อง (header ผิด)"
    if b"%%EOF" not in tail:
        return "PDF ไม่สมบูรณ์ (ไม่พบ %%EOF ท้ายไฟล์)"
    return None


def check_one_file(path: str, exe: str | None) -> str | None:
    """ตรวจไฟล์เดียวแบบเลือกวิธีตามประเภท. คืน None=ปกติ/ตรวจลึกไม่ได้, ข้อความ=เสีย."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return f"อ่านไฟล์ไม่ได้: {e}"
    if size == 0:
        return "ไฟล์ว่าง (0 bytes)"
    ext = os.path.splitext(path)[1].lower()
    if ext in _MEDIA_EXTS:
        return _decode_check(exe, path) if exe else None   # ต้องมี ffmpeg
    if ext in _ZIP_EXTS:
        return _zip_check(path)
    if ext in _GZIP_EXTS:
        return _gzip_check(path)
    if ext in _PDF_EXTS:
        return _pdf_check(path)
    # ชนิดอื่น: ตรวจลึกไม่ได้ — ผ่านถ้าอ่านได้และไม่ว่าง
    return None


def collect_files(folder: str = "", recursive: bool = True, ext: str = "") -> dict:
    """หาไฟล์ "ทุกชนิด" ในโฟลเดอร์ (สำหรับ UI ตรวจไฟล์เสียทีละไฟล์พร้อม progress).

    ext เว้นว่าง = ทุกไฟล์; ระบุได้ เช่น 'mp4,zip,pdf'
    คืน dict: {ok, error, target, files, ffmpeg}
    """
    exe = _find_ffmpeg()               # อาจเป็น None ก็ได้ (ไฟล์ที่ไม่ใช่มีเดียยังตรวจได้)
    if folder.strip():
        target = os.path.abspath(os.path.expanduser(folder.strip()))
        if _is_blocked_root(target):
            return {"ok": False, "error": "ปฏิเสธ: ตรวจที่ root/โฟลเดอร์ระบบไม่ได้",
                    "target": target, "files": [], "ffmpeg": exe}
    else:
        target = os.path.abspath(WORKSPACE)
    if not os.path.isdir(target):
        return {"ok": False, "error": f"ไม่พบโฟลเดอร์: {target}",
                "target": target, "files": [], "ffmpeg": exe}
    wanted = None
    if ext.strip():
        wanted = tuple("." + e.strip().lower().lstrip(".") for e in ext.split(",") if e.strip())
    files: list[str] = []
    if recursive:
        for root, _dirs, names in os.walk(target):
            for n in names:
                if wanted is None or n.lower().endswith(wanted):
                    files.append(os.path.join(root, n))
    else:
        for n in os.listdir(target):
            p = os.path.join(target, n)
            if os.path.isfile(p) and (wanted is None or n.lower().endswith(wanted)):
                files.append(p)
    files.sort()
    return {"ok": True, "error": "", "target": target, "files": files, "ffmpeg": exe}


def file_sha256(path: str) -> str | None:
    """แฮช SHA-256 ของไฟล์ (สตรีมทีละบล็อก) สำหรับหาไฟล์ซ้ำ. คืน None ถ้าอ่านไม่ได้."""
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return None


def check_file_integrity(folder: str = "", recursive: bool = True, ext: str = "") -> str:
    """ตรวจหาไฟล์ที่เสียหาย "ทุกชนิด" ในโฟลเดอร์ (เสียง/วิดีโอ/รูป/zip/office/pdf/gzip ฯลฯ).

    folder    : โฟลเดอร์ที่จะตรวจ (เว้นว่าง = โฟลเดอร์งานปัจจุบัน)
    recursive : ตรวจโฟลเดอร์ย่อยด้วยไหม (ดีฟอลต์ True)
    ext       : จำกัดนามสกุล คั่นด้วย comma เช่น 'mp4,zip,pdf' (เว้นว่าง = ทุกไฟล์)
    """
    info = collect_files(folder, recursive, ext)
    if not info["ok"]:
        return info["error"]
    exe, files, target = info["ffmpeg"], info["files"], info["target"]
    if not files:
        return f"ไม่พบไฟล์ในโฟลเดอร์: {target}"
    corrupt: list[tuple[str, str]] = []
    for p in files:
        reason = check_one_file(p, exe)
        if reason is not None:
            corrupt.append((p, reason))
    total = len(files)
    note = "" if exe else "\n(หมายเหตุ: ไม่มี ffmpeg จึงข้ามการตรวจลึกของไฟล์เสียง/วิดีโอ/รูป)"
    if not corrupt:
        return f"ตรวจ {total} ไฟล์ — ไม่พบไฟล์เสีย ✅\nโฟลเดอร์: {target}{note}"
    lines = [f"ตรวจ {total} ไฟล์ — พบไฟล์เสีย {len(corrupt)} ไฟล์ ❌",
             f"โฟลเดอร์: {target}", ""]
    for p, reason in corrupt:
        lines.append(f"• {os.path.relpath(p, target)}  — {reason}")
    if note:
        lines.append(note.strip())
    return "\n".join(lines)


# หมายเหตุ: check_ffmpeg() และ install_ffmpeg() นิยามไว้ด้านบนแล้ว (เวอร์ชันเต็มที่
# เรียก ffmpeg -version และติดตั้งผ่าน winget จริง) — เดิมมี stub ซ้ำตรงนี้ที่ override
# ทับของจริงทำให้ install_ffmpeg ใช้งานไม่ได้ตามที่ README โฆษณา จึงลบ stub ออก


def add_to_knowledge(doc_id: str, content: str) -> str:
    return knowledge_store.add_to_knowledge(doc_id, content)

def search_knowledge(query: str) -> str:
    return knowledge_store.search_knowledge(query)

# ---------------------------------------------------------------------------
# ลงทะเบียน
# ---------------------------------------------------------------------------
TOOLS = {
    "calculator": calculator,
    "get_current_time": get_current_time,
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "fetch_url": fetch_url,
    "check_audio_integrity": check_audio_integrity,
    "check_file_integrity": check_file_integrity,
    "check_ffmpeg": check_ffmpeg,
    "install_ffmpeg": install_ffmpeg,
    "add_to_knowledge": add_to_knowledge,
    "search_knowledge": search_knowledge,
    "remember": remember,
}

# เครื่องมือกลุ่มที่ "เขียน/แก้ไฟล์" — server จะดักไว้ถามยืนยันก่อน
# (F1: remember ก็เขียนไฟล์ _memory.md จึงต้องผ่านการยืนยันเช่นกัน)
WRITE_TOOLS = {"write_file", "remember"}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "calculator",
        "description": "คำนวณนิพจน์คณิตศาสตร์",
        "parameters": {"type": "object", "properties": {
            "expression": {"type": "string", "description": "นิพจน์ เช่น '12 * (3 + 4)'"}},
            "required": ["expression"]}}},
    {"type": "function", "function": {
        "name": "get_current_time",
        "description": "ขอวันและเวลาปัจจุบัน",
        "parameters": {"type": "object", "properties": {
            "timezone": {"type": "string", "description": "IANA timezone เช่น Asia/Bangkok หรือ UTC"}}}}},
    {"type": "function", "function": {
        "name": "list_files",
        "description": "ดูรายชื่อไฟล์ในโฟลเดอร์งาน",
        "parameters": {"type": "object", "properties": {
            "subdir": {"type": "string", "description": "โฟลเดอร์ย่อย (เว้นว่าง=รากของโฟลเดอร์งาน)"}}}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "อ่านเนื้อหาไฟล์ข้อความในโฟลเดอร์งาน",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "ชื่อ/พาธไฟล์"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "สร้างหรือแก้ไขไฟล์ในโฟลเดอร์งาน (ระบบจะถามผู้ใช้ยืนยันก่อนบันทึกจริง)",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "ชื่อไฟล์"},
            "content": {"type": "string", "description": "เนื้อหาทั้งหมดของไฟล์"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "fetch_url",
        "description": "ดึงข้อความ/HTML จากเว็บไซต์",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "URL"}},
            "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "check_audio_integrity",
        "description": ("ตรวจหาไฟล์เพลง/ไฟล์เสียงที่เสียหาย (corrupt) ในโฟลเดอร์ "
                        "โดย decode ทั้งไฟล์ด้วย ffmpeg แล้วรายงานไฟล์ที่พัง "
                        "รองรับ mp3/flac/wav/m4a/aac/ogg/opus ฯลฯ"),
        "parameters": {"type": "object", "properties": {
            "folder": {"type": "string",
                       "description": "โฟลเดอร์ที่จะตรวจ (เว้นว่าง = โฟลเดอร์งานปัจจุบัน)"},
            "recursive": {"type": "boolean",
                          "description": "ตรวจโฟลเดอร์ย่อยด้วยไหม (ดีฟอลต์ true)"},
            "ext": {"type": "string",
                    "description": "จำกัดนามสกุล คั่นด้วย comma เช่น 'mp3,flac' (เว้นว่าง=ทุกฟอร์แมต)"}}}}},
    {"type": "function", "function": {
        "name": "check_file_integrity",
        "description": ("ตรวจหาไฟล์เสียหายทุกชนิดในโฟลเดอร์ — เสียง/วิดีโอ/รูป (ffmpeg), "
                        "zip/docx/xlsx/pptx (ทดสอบ archive), pdf (ตรวจโครงสร้าง), gzip, "
                        "และจับไฟล์ว่าง 0 bytes"),
        "parameters": {"type": "object", "properties": {
            "folder": {"type": "string",
                       "description": "โฟลเดอร์ที่จะตรวจ (เว้นว่าง = โฟลเดอร์งานปัจจุบัน)"},
            "recursive": {"type": "boolean", "description": "ตรวจโฟลเดอร์ย่อยด้วยไหม (ดีฟอลต์ true)"},
            "ext": {"type": "string",
                    "description": "จำกัดนามสกุล คั่นด้วย comma เช่น 'mp4,zip,pdf' (เว้นว่าง=ทุกไฟล์)"}}}}},
    {"type": "function", "function": {
        "name": "check_ffmpeg",
        "description": "ตรวจว่ามี ffmpeg ในเครื่องหรือไม่ (จำเป็นสำหรับตรวจไฟล์เสียง)",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "install_ffmpeg",
        "description": "ติดตั้ง ffmpeg อัตโนมัติผ่าน winget (Windows) เมื่อยังไม่มีในเครื่อง",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "add_to_knowledge",
        "description": "Add text content to the project's knowledge base. Useful for storing facts, guidelines, or summaries.",
        "parameters": {"type": "object", "properties": {
            "doc_id": {"type": "string", "description": "Unique identifier for this document/knowledge piece"},
            "content": {"type": "string", "description": "The text content to store"}
        }, "required": ["doc_id", "content"]}}},
    {"type": "function", "function": {
        "name": "search_knowledge",
        "description": "Search the project's knowledge base for relevant information using a query string.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "The search query (keywords, questions)"}
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "remember",
        "description": ("จดข้อมูล/ข้อตกลง/ความชอบสำคัญของผู้ใช้ลงไฟล์ความจำ (_memory.md) "
                        "ของโฟลเดอร์งาน — จะถูกอ่านอัตโนมัติทุกครั้งที่เริ่มคุยในโฟลเดอร์นี้ "
                        "ใช้เมื่อผู้ใช้บอกให้จำ หรือพบข้อมูลที่ควรจำระยะยาว (สั้น กระชับ)"),
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "ข้อความสั้นๆ ที่ต้องการจำ (1-2 ประโยค)"}},
            "required": ["text"]}}},
]


# ---------------------------------------------------------------------------
# Skills — ความสามารถเสริมที่ผู้ใช้สร้างเอง (โหลดจากโฟลเดอร์ skills/)
# ---------------------------------------------------------------------------
try:
    from skills_loader import SkillRegistry
    SKILLS = SkillRegistry()
except Exception as e:  # noqa: BLE001
    SKILLS = None
    print(f"[skills] โหลดระบบ skills ไม่ได้: {e}")


def all_tool_schemas(allowed_categories=None) -> list:
    """schema ของเครื่องมือพื้นฐาน + skills (code skills + use_skill) + MCP tools."""
    schemas = list(TOOL_SCHEMAS)
    if SKILLS:
        schemas.extend(SKILLS.tool_schemas(allowed_categories))
    schemas.extend(mcp_manager.tool_schemas)
    return schemas


def _record_skill_use(name: str, ok: bool, error: str, started: float, kind: str) -> None:
    """SI-1: บันทึกการใช้ skill/MCP เป็น decision trail (เงียบเสมอ — ห้ามพังงานหลัก)."""
    try:
        import skill_intelligence as SI
        import time as _t
        SI.record_use(name, ok=ok, error=error, duration=_t.time() - started, kind=kind)
    except Exception:  # noqa: BLE001
        pass


def run_tool(name: str, args: dict) -> str:
    """เรียกเครื่องมือพื้นฐาน, skill หรือ MCP tool ตามชื่อ."""
    import time as _t
    if name in mcp_manager.tool_mapping:
        started = _t.time()
        try:
            result = mcp_manager.call_tool(name, args)
            _record_skill_use(name, ok=True, error="", started=started, kind="mcp")
            return result
        except Exception as e:  # noqa: BLE001
            _record_skill_use(name, ok=False, error=str(e), started=started, kind="mcp")
            raise

    fn = TOOLS.get(name)
    is_skill = False
    if fn is None and SKILLS:
        fn = SKILLS.tool_map().get(name)
        is_skill = fn is not None
    if fn is None:
        return f"ไม่รู้จักเครื่องมือ: {name}"
    started = _t.time()
    try:
        result = str(fn(**args))
        if is_skill:  # บันทึกเฉพาะ skills — ไม่บันทึกเครื่องมือพื้นฐาน (ลด noise)
            kind = "prompt_skill" if name == "use_skill" else "skill"
            _record_skill_use(name, ok=True, error="", started=started, kind=kind)
        return result
    except Exception as e:  # noqa: BLE001
        if is_skill:
            kind = "prompt_skill" if name == "use_skill" else "skill"
            _record_skill_use(name, ok=False, error=str(e), started=started, kind=kind)
        return f"เครื่องมือ {name} ผิดพลาด: {e}"


def skills_catalog(allowed_categories=None) -> str:
    """แค็ตตาล็อก skills สำหรับ system prompt."""
    return SKILLS.catalog(allowed_categories) if SKILLS else ""


def reload_skills() -> int:
    return SKILLS.reload() if SKILLS else 0


def skills_list() -> list:
    return SKILLS.public_list() if SKILLS else []

