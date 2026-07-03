"""
server.py — UI แบบหน้าต่างสไตล์ Claude desktop (ธีมน้ำเงิน) + Co-Work

ฟีเจอร์:
  - หลาย agent + tool calling (ผ่าน LM Studio — OpenAI-compatible server)
  - โฟลเดอร์งาน (workspace) ที่ตั้งค่าได้ — อ่าน/เขียนไฟล์เฉพาะในนี้
  - Co-Work: AI เสนอการสร้าง/แก้ไฟล์ -> ผู้ใช้กด "บันทึก" ยืนยันก่อนเขียนจริง
  - แนบไฟล์: อัปโหลดไฟล์ข้อความเข้าโฟลเดอร์งานให้ AI อ่าน

รัน:   py server.py    (หรือดับเบิลคลิก run-ui.bat)
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import logging
import os
import re as _re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# logging พื้นฐาน: แสดง warning+ บนคอนโซล พร้อม timestamp
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("server")


def _force_utf8_streams() -> None:
    """กันปัญหา 'charmap' codec บน Windows ภาษาไทย.

    เมื่อ build แบบ --windowed (console=False) ตัว stdout/stderr อาจเป็น None
    หรือเป็น cp874 ('charmap') ทำให้พอมีโค้ด/ไลบรารี (เช่น pywebview) พิมพ์ log
    ภาษาไทยหรืออีโมจิตอนเปิดหน้าต่าง จะเกิด UnicodeEncodeError แล้ว crash
    (ซึ่งก่อนหน้านี้ถูกเดาผิดว่าเป็นเพราะไม่มี WebView2 Runtime).
    ฟังก์ชันนี้บังคับให้ทุก stream เป็น utf-8 และไม่ล้มแม้เจอตัวอักษรแปลก ๆ.
    """
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            # โหมดหน้าต่างไม่มี console: ใส่ sink รองรับ utf-8 กันโค้ดที่ print แล้วพัง
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8", errors="replace"))
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            try:
                setattr(sys, name, io.TextIOWrapper(
                    stream.buffer, encoding="utf-8", errors="replace", line_buffering=True))
            except Exception:  # noqa: BLE001
                # QUAL-3: อย่างน้อย log ไว้ debug ระดับ (ไม่ raise ต่อเพราะเป็น fallback สุดท้ายจริงๆ)
                _log.debug("_force_utf8_streams: ตั้ง utf-8 stream ไม่สำเร็จ (%s)", name, exc_info=True)


_force_utf8_streams()

from agents import AGENTS, DEFAULT_AGENT
import tools as T
import skills_loader as SL
import agent_store as AG
import data_store as DS
import winproc  # QUAL-1: no_window_kwargs() ที่ใช้ร่วมกับ tools.py

# OBS-1: log ลงไฟล์ data/app.log ด้วย — โหมด .exe (--windowed) ไม่มีคอนโซล
# ถ้าไม่มีไฟล์ log แปลว่า warning/audit trail (SEC-3) และ traceback (D2) หายเงียบหมด
_FILE_LOG_READY = False


def _setup_file_logging() -> None:
    """เพิ่ม RotatingFileHandler ที่ data/app.log (1MB × เก็บย้อนหลัง 3 ไฟล์).

    คอนโซลยังแสดงเฉพาะ WARNING+ เหมือนเดิม แต่ไฟล์เก็บตั้งแต่ INFO ขึ้นไป.
    """
    global _FILE_LOG_READY
    if _FILE_LOG_READY:
        return
    try:
        from logging.handlers import RotatingFileHandler
        os.makedirs(DS.DATA_DIR, exist_ok=True)
        fh = RotatingFileHandler(
            os.path.join(DS.DATA_DIR, "app.log"),
            maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        root = logging.getLogger()
        for h in root.handlers:          # handler คอนโซลเดิม: คุมระดับไว้ที่ WARNING+
            h.setLevel(logging.WARNING)
        root.addHandler(fh)
        root.setLevel(logging.INFO)      # ให้ INFO ไหลถึง file handler ได้
        _FILE_LOG_READY = True
    except Exception:  # noqa: BLE001 — log ไฟล์เป็นของเสริม อย่าทำแอปพังเพราะมัน
        _log.debug("_setup_file_logging: ตั้ง file logging ไม่สำเร็จ", exc_info=True)


_setup_file_logging()

# ----------------------------- LM Studio backend -----------------------------
# แอปนี้คุยกับโมเดลผ่าน LM Studio (เซิร์ฟเวอร์ OpenAI-compatible)
# เปิด LM Studio -> แท็บ Developer / Local Server -> Start Server (ดีฟอลต์พอร์ต 1234)
# แล้วโหลดโมเดลที่รองรับ tool-calling (เช่น Qwen2.5-Instruct, Llama-3.1-Instruct)
#
# ตั้งค่าผ่าน env ได้:
#   LMSTUDIO_BASE_URL  (ดีฟอลต์ http://localhost:1234/v1)
#   LMSTUDIO_MODEL     (เว้นว่าง = ใช้โมเดลตัวแรกที่ LM Studio โหลดไว้อัตโนมัติ)
#   LMSTUDIO_API_KEY   (LM Studio ไม่ตรวจ key — ใส่อะไรก็ได้)
LMSTUDIO_BASE = os.environ.get(
    "LMSTUDIO_BASE_URL",
    os.environ.get("OPENAI_BASE_URL", "http://localhost:1234/v1"),
).rstrip("/")
LMSTUDIO_KEY = os.environ.get("LMSTUDIO_API_KEY", "lm-studio")
# โมเดลดีฟอลต์: ถ้าไม่ตั้ง env ไว้ จะ resolve เป็นตัวแรกที่ LM Studio โหลดอยู่ตอน runtime
MODEL = os.environ.get("LMSTUDIO_MODEL", os.environ.get("OLLAMA_MODEL", "")).strip()
HOST, PORT = "127.0.0.1", 11500
# พอร์ตจริงที่ bind สำเร็จ (อาจเลื่อนจาก PORT ถ้าชน) — _same_origin/URL ต้องใช้ค่านี้
ACTUAL_PORT = PORT
MAX_STEPS = 8
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _lm_get(path: str, timeout: int = 8):
    """GET ไปยัง LM Studio (OpenAI-compatible) แล้วคืน JSON (raise ถ้าต่อไม่ได้)."""
    url = LMSTUDIO_BASE + path
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {LMSTUDIO_KEY}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def lm_models() -> list[str]:
    """รายชื่อโมเดลที่ LM Studio โหลด/ให้บริการอยู่ (GET /models)."""
    try:
        data = _lm_get("/models")
        out = []
        for m in data.get("data", []) or []:
            mid = m.get("id") if isinstance(m, dict) else None
            if mid:
                out.append(mid)
        return out
    except Exception:  # noqa: BLE001 — LM Studio อาจยังไม่เปิด
        return []


def default_model() -> str:
    """โมเดลที่จะใช้: ค่าจาก env ถ้ามี ไม่งั้นเอาตัวแรกที่ LM Studio โหลดอยู่."""
    if MODEL:
        return MODEL
    models = lm_models()
    return models[0] if models else "local-model"


def _lm_alive(timeout: int = 3) -> bool:
    """เซิร์ฟเวอร์ LM Studio ตอบที่ /models ไหม (ไม่สนว่ามีโมเดลโหลดหรือยัง)."""
    try:
        _lm_get("/models", timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


# สตาร์ต lms ได้ครั้งเดียวต่อการรันโปรแกรม — กันการสแปม `lms server start` ซ้ำ ๆ
_LMS_AUTOSTART_TRIED = False


def _find_lms() -> str | None:
    """หา path ของ `lms` CLI ของ LM Studio (ใช้สตาร์ต server แบบ headless)."""
    exe = shutil.which("lms")
    if exe:
        return exe
    candidates = [
        os.path.expandvars(r"%USERPROFILE%\.lmstudio\bin\lms.exe"),
        os.path.expandvars(r"%USERPROFILE%\.cache\lm-studio\bin\lms.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\LM Studio\lms.exe"),
        os.path.expanduser("~/.lmstudio/bin/lms"),
        os.path.expanduser("~/.cache/lm-studio/bin/lms"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _show_lmstudio_gui() -> None:
    """พยายามเปิดหน้าต่าง LM Studio ขึ้นมาให้ผู้ใช้เห็น"""
    if os.name != "nt":
        return
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\lm-studio\LM Studio.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\LM Studio\LM Studio.exe"),
        os.path.expandvars(r"%USERPROFILE%\AppData\Local\Programs\lm-studio\LM Studio.exe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            try:
                os.startfile(c)
            except Exception:  # noqa: BLE001
                _log.debug("เปิดหน้าต่าง LM Studio ไม่ได้: %s", c, exc_info=True)
            break


def ensure_lmstudio(timeout: int = 30) -> bool:
    """ทำให้ LM Studio server พร้อมใช้งานโดยพยายามเปิดหน้าต่างแอป LM Studio ขึ้นมาด้วย.

    ถ้า server เปิดอยู่แล้ว -> คืน True ทันที
    ถ้ายังไม่เปิด -> เรียก `lms server start` สตาร์ตแบบ headless แล้วรอจนตอบ
    (โมเดลจะถูกโหลดอัตโนมัติแบบ JIT ตอนแชตครั้งแรก)
    คืน True ถ้า endpoint พร้อม, False ถ้าหา lms CLI ไม่เจอ/สตาร์ตไม่ขึ้น
    """
    global _LMS_AUTOSTART_TRIED
    if _lm_alive():
        return True
    # สตาร์ตแค่ครั้งเดียวตลอดการรัน — ถ้าลองไปแล้วไม่ขึ้น อย่าสแปมเปิด process ซ้ำ
    if _LMS_AUTOSTART_TRIED:
        return False
    _LMS_AUTOSTART_TRIED = True
    lms = _find_lms()
    if not lms:
        return False
    port = LMSTUDIO_BASE.rsplit(":", 1)[-1].split("/")[0]
    cmd = [lms, "server", "start"]
    if port.isdigit():
        cmd += ["--port", port]
        
    # รันเซิร์ฟเวอร์ lms แบบซ่อนหน้าต่างควบคู่ไปด้วย เพื่อให้ API พร้อมใช้ทันที
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, **winproc.no_window_kwargs(detached=True))
    except Exception:  # noqa: BLE001
        # QUAL-3: log ไว้ debug — เคยเกิดเงียบๆ ทำให้ไม่รู้ว่าทำไม lms server start ไม่ขึ้น
        _log.debug("ensure_lmstudio: เปิด lms server start ไม่สำเร็จ", exc_info=True)
        return False
        
    deadline = time.time() + timeout
    is_alive = False
    while time.time() < deadline:
        if _lm_alive():
            is_alive = True
            break
        time.sleep(1)
        
    # พยายามเปิดหน้าต่างโปรแกรม LM Studio ให้เห็น (หลังเซิร์ฟเวอร์พร้อมแล้ว เพื่อลดปัญหาการแย่งไฟล์/ค้าง)
    _show_lmstudio_gui()
    
    return is_alive


# ----------------------------- agent core -----------------------------
COWORK_TOOLS = {"list_files", "read_file", "write_file", "fetch_url",
                "check_audio_integrity", "check_ffmpeg", "install_ffmpeg", "remember"}
# SEC-4: เครื่องมือที่ "เปลี่ยนแปลงระบบ" (ติดตั้งซอฟต์แวร์ ฯลฯ) ต้องผ่านการยืนยันจาก
# ผู้ใช้ก่อนรันจริงเหมือน skill ใหม่ — โมเดลตัดสินใจเรียกเองทันทีไม่ได้อีกต่อไป
CONFIRM_TOOLS = {"install_ffmpeg"}
COWORK_PROMPT = (
    "\n\n[โหมด Co-Work เปิดอยู่] คุณมีสิทธิ์จัดการไฟล์ในโฟลเดอร์งานแล้ว. "
    "ถ้ามีงานที่เกี่ยวข้องกับไฟล์ ให้สำรวจด้วย list_files และอ่านด้วย read_file ก่อนเสมอ "
    "เวลาสร้างหรือแก้ไฟล์ใช้ write_file (ระบบจะแสดงตัวอย่างให้กดยืนยันก่อนบันทึกจริง อย่าเพิ่งบอกว่าทำแล้ว). "
    "อธิบายให้ชัดว่าจะอ่านหรือแก้ไฟล์อะไรบ้างทุกครั้ง."
)
ARTIFACTS_PROMPT = (
    "\n\n[Artifacts] You can present interactive code, UI components, HTML, SVG, or Mermaid diagrams using Artifacts. "
    "To create an Artifact, use the following XML tag format exactly:\n"
    '<antArtifact identifier="unique-id" title="Human-readable Title" type="text/html">\n'
    "// content here\n"
    "</antArtifact>\n"
    "Supported types: text/html, application/javascript, text/css, text/markdown, application/vnd.ant.mermaid, text/plain."
)


def schemas_for(agent: dict, cowork: bool = False) -> list:
    allowed_cats = agent.get("skill_categories")
    base = T.all_tool_schemas(allowed_cats)       # เครื่องมือพื้นฐาน + skills
    allowed = agent.get("tools")
    if allowed is None:
        result = list(base)                # agent นี้ใช้ได้ทุกอย่าง รวม skills
    elif not allowed:
        result = []                        # agent นี้ไม่ใช้เครื่องมือ
    else:
        names = set(allowed)
        # skill tools (และ use_skill) ให้ใช้ได้เสมอถ้า agent ไม่ได้จำกัดเป็น []
        base_names = {s["function"]["name"] for s in T.TOOL_SCHEMAS}
        result = [s for s in base if s["function"]["name"] in names
                  or s["function"]["name"] not in base_names]
    if cowork:
        # Co-Work: file tools ต้องมีเสมอ ไม่ว่า agent จะจำกัดเครื่องมืออย่างไร (รวม tools=[])
        have = {s["function"]["name"] for s in result}
        for s in T.all_tool_schemas():
            n = s["function"]["name"]
            if n in COWORK_TOOLS and n not in have:
                result.append(s)
    return result


def _handle_tool_call(fname, args, used_tools, proposals, skill_names=None):
    """จัดการ tool call หนึ่งครั้ง คืน result string. WRITE_TOOLS เก็บเป็น proposal (ไม่เขียนจริง).

    D1: ถ้า fname เป็น skill ที่ยังไม่ confirm → คืน proposal ประเภท skill_confirm
    ให้ UI แสดง dialog ถามผู้ใช้ก่อน แทนที่จะรัน tool.py ทันที.
    SEC-4: เครื่องมือใน CONFIRM_TOOLS (เปลี่ยนแปลงระบบ เช่น install_ffmpeg) ใช้
    กลไก confirm เดียวกับ skill — is_confirmed()/confirm_skill() เก็บแค่ชื่อ string
    เฉยๆ ไม่ผูกกับ "skill" จริง จึงใช้ร่วมกันได้.
    skill_names: set ของ skill name ทั้งหมดที่โหลดอยู่ (ถ้า None จะดึงจาก T.skills_list())
    """
    used_tools.append(fname)
    if fname in T.WRITE_TOOLS:
        if fname == "remember":
            # F1: แปลง remember(text) เป็น proposal เขียน _memory.md ฉบับเต็ม —
            # ผู้ใช้เห็น preview ความจำทั้งไฟล์ก่อนกดยืนยัน (reuse flow ของ write_file)
            path = T.MEMORY_FILE
            content = T.build_memory_content(args.get("text", ""))
        else:
            path = args.get("path", "untitled.txt")
            content = args.get("content", "")
        proposals.append({"path": path, "content": content, "exists": T.file_exists(path)})
        return (f"เสนอบันทึกไฟล์ '{path}' แล้ว — กำลังรอผู้ใช้กดยืนยันใน UI "
                f"(ยังไม่เขียนจริง)")
    # D1/SEC-4: ตรวจเครื่องมือที่ต้องยืนยันก่อนรัน (skill ใหม่ หรือเครื่องมือระบบใน CONFIRM_TOOLS)
    if skill_names is None:
        skill_names = {s["name"] for s in T.skills_list()}
    is_skill = fname in skill_names
    # SEC-6: MCP tool คือโค้ดภายนอกที่ทำอะไรก็ได้ — ต้องผ่าน confirm ครั้งแรกเหมือน skill
    is_mcp = T.is_mcp_tool(fname)
    if is_skill or is_mcp or fname in CONFIRM_TOOLS:
        if not SL.is_confirmed(fname):
            proposals.append({"type": "skill_confirm", "name": fname, "args": args})
            return (f"⚠️ '{fname}' ยังไม่ได้รับการยืนยัน — "
                    f"กำลังรอผู้ใช้อนุมัติใน UI (จะรันหลังยืนยัน)")
        # SEC-3: หลัง confirm แล้วจะไม่ถูกถามซ้ำอีกในเซสชันนี้ — log argument ทุกครั้งที่รัน
        # (ไม่ใช่แค่ครั้งแรก) ไว้เป็น audit trail กันกรณีมี prompt injection สั่งเรียกด้วย
        # argument ผิดปกติหลังจากที่เคยยืนยันไปแล้ว
        kind = "skill" if is_skill else ("mcp tool" if is_mcp else "system tool")
        _log.warning("%s run (confirmed): %s args=%r", kind, fname, args)
    return _run(fname, args)


def _extract_message(resp: dict) -> dict | None:
    """ดึง assistant message จาก response อย่างปลอดภัย.

    คืน dict ที่มี content/tool_calls (อาจเป็น None) ถ้าเป็น response ปกติ,
    หรือ None ถ้า response ผิดรูปแบบ/เป็น error — ผู้เรียกจะได้สร้างข้อความแจ้งเอง.
    ทนต่อ: response ผิดรูปแบบ, LM Studio คืน {error:{message}}, choices ว่าง ฯลฯ
    (กัน KeyError/TypeError ที่เคยทำให้ agent loop ระเบิดเงียบ).
    """
    if not isinstance(resp, dict):
        return None
    choices = resp.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict) and isinstance(first.get("message"), dict):
            return first["message"]
    return None


def _format_error_reply(resp: dict) -> str:
    """สร้างข้อความที่อ่านรู้เรื่องจาก response ที่ไม่มี choices (เช่น {error:...})."""
    err = resp.get("error") if isinstance(resp, dict) else None
    if isinstance(err, dict):
        msg = err.get("message") or err.get("code") or str(err)
        return f"⚠️ โมเดลส่งกลับข้อผิดพลาด: {msg}"
    if err:
        return f"⚠️ โมเดลส่งกลับข้อผิดพลาด: {err}"
    return ("⚠️ โมเดลตอบกลับมาผิดรูปแบบ (อาจเป็นเพราะ context เกิน โมเดลโหลดไม่ติด "
            "หรือ provider ไม่รองรับ tool calling) — ลองเปลี่ยนโมเดลหรือถามใหม่อีกครั้ง")


def _openai_chat(base_url, api_key, model, messages, tools):
    """เรียกโมเดลผ่าน OpenAI-compatible endpoint (OpenAI/OpenRouter/Groq/LM Studio/vLLM/...)."""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages, "stream": False}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


# ---- cancel flag สำหรับ B2: ปุ่มหยุด ----
_cancel_lock = threading.Lock()
_cancel_requested = False


def request_cancel() -> None:
    """Set flag ให้ run_agent หยุดทำงาน (ปุ่ม Stop ฝั่ง UI)."""
    global _cancel_requested
    with _cancel_lock:
        _cancel_requested = True


def _clear_cancel() -> None:
    global _cancel_requested
    with _cancel_lock:
        _cancel_requested = False


def _is_cancelled() -> bool:
    with _cancel_lock:
        return _cancel_requested


def run_agent(agent_key: str, history: list, model: str,
              cowork: bool = False, provider: dict | None = None) -> dict:
    """
    รันลูป agent. WRITE_TOOLS ถูกเก็บเป็น 'proposals' ให้ผู้ใช้ยืนยันก่อนเขียนจริง.
    provider: ถ้ามี base_url -> route ไป endpoint นั้นแทน LM Studio (ดีฟอลต์).
    ตรวจ _cancel_requested ทุก step — ถ้าถูก cancel คืนผลบางส่วนที่ได้ทันที.
    """
    _clear_cancel()  # ล้าง flag เก่าทุกครั้งที่เริ่ม run ใหม่
    _agents = AG.all_agents()
    agent = _agents.get(agent_key) or _agents.get(DEFAULT_AGENT) or AGENTS[DEFAULT_AGENT]
    sys_prompt = agent["system"]
    sys_prompt += ARTIFACTS_PROMPT
    if cowork:
        sys_prompt += COWORK_PROMPT
    allowed_cats = agent.get("skill_categories")
    catalog = T.skills_catalog(allowed_cats)
    if catalog:
        sys_prompt += "\n\n" + catalog
    # SI-1: ผูกงานปัจจุบันกับ decision trail + ป้อนประสบการณ์ skill กลับให้โมเดล
    try:
        import skill_intelligence as SI
        _last_user = next((h.get("content", "") for h in reversed(history)
                           if h.get("role", "user") == "user"), "")
        SI.set_current_task(_last_user)
        if catalog:  # มี skills ให้เลือก จึงค่อยใส่สถิติ (ไม่มี skill = ไม่เปลืองบริบท)
            _exp = SI.experience_context(_last_user)
            if _exp:
                sys_prompt += "\n\n" + _exp
    except Exception:  # noqa: BLE001
        pass
    # F1: บริบทประจำโฟลเดอร์งาน (_agent.md/_memory.md) — ว่าง = ไม่เปลืองบริบทโมเดล
    ws_ctx = T.workspace_context()
    if ws_ctx:
        sys_prompt += "\n\n" + ws_ctx
    tool_schemas = schemas_for(agent, cowork)
    used_tools: list[str] = []
    proposals: list[dict] = []
    # D1: คำนวณ skill names ครั้งเดียว (ประหยัดการเรียก skills_list ทุก tool call)
    skill_names = {s["name"] for s in T.skills_list()}

    # backend: LM Studio (ดีฟอลต์) หรือ provider ภายนอกที่ส่ง base_url มาเอง
    if provider and provider.get("base_url"):
        base_url = provider.get("base_url").rstrip("/")
        api_key = provider.get("api_key", "")
    else:
        base_url = LMSTUDIO_BASE
        api_key = LMSTUDIO_KEY
    if not model:
        model = default_model()

    is_lmstudio = base_url == LMSTUDIO_BASE
    tried_autostart = False
    messages = [{"role": "system", "content": sys_prompt}]
    messages += [{"role": h.get("role", "user"), "content": h.get("content", "")}
                 for h in history]
    for _ in range(MAX_STEPS):
        if _is_cancelled():
            return {"reply": "⛔ ยกเลิกแล้ว", "tools": used_tools, "proposals": proposals,
                    "cancelled": True}
        try:
            resp = _openai_chat(base_url, api_key, model, messages, tool_schemas)
        except Exception as e:  # noqa: BLE001 — LM Studio ยังไม่เปิด/โหลดโมเดล
            _log.warning("_openai_chat failed: %s", e, exc_info=True)
            # self-healing: ถ้าเป็น backend LM Studio ลองสตาร์ต headless แล้วลองใหม่ครั้งเดียว
            if is_lmstudio and not tried_autostart:
                tried_autostart = True
                if ensure_lmstudio():
                    if not MODEL:
                        model = default_model()
                    continue
            return {"reply": (f"⚠️ ต่อ LM Studio ไม่ได้ ({e}).\n"
                              f"ลองเปิดโปรแกรมใหม่ หรือถ้ายังไม่มี `lms` CLI ให้รัน "
                              f"`npx lmstudio install-cli` หนึ่งครั้ง และต้องมีโมเดลใน LM Studio "
                              f"อย่างน้อย 1 ตัวนะครับ"),
                    "tools": used_tools, "proposals": proposals}
        msg = _extract_message(resp)
        if msg is None:
            # response ผิดรูปแบบ/เป็น error (เช่น context เกิน, โมเดลไม่โหลด) -> แจ้งให้ชัด
            return {"reply": _format_error_reply(resp), "tools": used_tools, "proposals": proposals}
        calls = msg.get("tool_calls")
        am = {"role": "assistant", "content": msg.get("content") or ""}
        if calls:
            am["tool_calls"] = calls
        messages.append(am)
        if not calls:
            return {"reply": msg.get("content") or "", "tools": used_tools, "proposals": proposals}
        for tc in calls:
            if _is_cancelled():
                return {"reply": "⛔ ยกเลิกแล้ว", "tools": used_tools, "proposals": proposals,
                        "cancelled": True}
            fn = tc.get("function", {})
            fname = fn.get("name", "")
            raw = fn.get("arguments") or "{}"
            try:
                args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
            except ValueError as e:
                err_msg = f"⚠️ Error parsing tool arguments: {e}. Please provide valid JSON."
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": err_msg})
                continue
            result = _handle_tool_call(fname, args, used_tools, proposals, skill_names)
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result})
    return {"reply": "ทำงานหลายขั้นเกินกำหนด", "tools": used_tools, "proposals": proposals}


def _run(name: str, args: dict) -> str:
    return T.run_tool(name, args)


# ------------------ F5: งานตามเวลา (Scheduled tasks) ------------------
# เก็บใน data/schedules.json (ผ่าน data_store key "schedules") รูปแบบรายการละ dict:
#   {id, title, time:"HH:MM", days:[0-6]?, prompt, agent, workspace, enabled,
#    last_run:"YYYY-MM-DD", last_result}
# ข้อจำกัดที่ตั้งใจ: รันเฉพาะตอนแอปเปิดอยู่ (ไม่ใช่ Windows service) และรันทีละงาน
_sched_running = threading.Lock()


def _sched_due(s: dict, now: _dt.datetime | None = None) -> bool:
    """งานถึงกำหนดหรือยัง: enabled + เลยเวลา HH:MM ของวันนี้ + วันตรง + ยังไม่รันวันนี้."""
    if not s.get("enabled", True):
        return False
    now = now or _dt.datetime.now()
    if s.get("last_run") == now.strftime("%Y-%m-%d"):
        return False
    days = s.get("days")
    if days and now.weekday() not in days:
        return False
    t = (s.get("time") or "").strip()
    if not _re.fullmatch(r"\d{1,2}:\d{2}", t):
        return False
    hh, mm = t.split(":")
    return now.hour * 60 + now.minute >= int(hh) * 60 + int(mm)


def _run_schedule(s: dict) -> str:
    """รันงานหนึ่งรายการแบบ headless แล้วเขียนรายงานลง reports/ ในโฟลเดอร์งานของงานนั้น.

    write_file proposals จากตัว agent จะไม่ถูกเขียน (ไม่มีใครกดยืนยัน) — รายงานจะบอก
    ผู้ใช้แทน. ผลลัพธ์หลัก (reply) ถูกเขียนตรงโดย server เพราะผู้ใช้ opt-in ตอนตั้งงานแล้ว.
    """
    title = (s.get("title") or s.get("id") or "งาน").strip()
    _log.info("scheduled task start: %s", title)
    T.set_cowork(True)   # ให้ตั้ง workspace ที่ผู้ใช้เลือกไว้ได้ (ยังกัน root/ระบบเสมอ)
    T.set_workspace(s.get("workspace") or None)
    res = run_agent(s.get("agent") or DEFAULT_AGENT,
                    [{"role": "user", "content": s.get("prompt") or ""}],
                    "", cowork=True)
    reply = res.get("reply", "")
    note = ""
    if res.get("proposals"):
        note = ("\n\n---\n⚠️ งานนี้พยายามสร้าง/แก้ไฟล์ "
                f"{len(res['proposals'])} รายการ แต่โหมดอัตโนมัติไม่เขียนไฟล์ให้ — "
                "เปิดแอปแล้วสั่งเองถ้าต้องการ")
    stamp = _dt.datetime.now().strftime("%Y-%m-%d")
    slug = _re.sub(r"[^0-9A-Za-zก-๙_-]", "_", title)[:40] or "task"
    path = f"reports/{stamp}-{slug}.md"
    T.write_file(path, f"# {title} — {stamp}\n\n{reply}{note}\n")
    _log.info("scheduled task done: %s -> %s", title, path)
    return path


def _scheduler_loop() -> None:
    """เช็คทุก 30 วิ — งานไหนถึงกำหนดก็รัน (ทีละงาน) แล้วบันทึก last_run/last_result."""
    while True:
        time.sleep(30)
        try:
            items = DS.load("schedules", []) or []
            changed = False
            for s in items:
                if not isinstance(s, dict) or not _sched_due(s):
                    continue
                if not _sched_running.acquire(blocking=False):
                    break            # มีงานกำลังรันอยู่ — รอรอบหน้า
                try:
                    s["last_run"] = _dt.datetime.now().strftime("%Y-%m-%d")
                    s["last_result"] = _run_schedule(s)
                except Exception as e:  # noqa: BLE001
                    s["last_result"] = f"ผิดพลาด: {e}"
                    _log.warning("scheduled task '%s' failed", s.get("title"), exc_info=True)
                finally:
                    _sched_running.release()
                changed = True
            if changed:
                DS.save("schedules", items)
        except Exception:  # noqa: BLE001
            _log.warning("scheduler loop error", exc_info=True)


# ------------------ งานตรวจไฟล์ (ปุ่มใน UI, ไม่พึ่ง AI) ------------------
# ตรวจครบในรอบเดียว: สรุปโฟลเดอร์ + ไฟล์ว่าง + ไฟล์ซ้ำ + ไฟล์เสียหาย
def _blank_scan() -> dict:
    return {"running": False, "finished": False, "total": 0, "done": 0,
            "target": "", "error": "", "ffmpeg": True,
            "summary": {}, "empty": [], "duplicates": [], "corrupt": []}


_audio_state = _blank_scan()
_audio_lock = threading.Lock()


def _audio_worker(folder: str, recursive: bool, ext: str) -> None:
    """สแกนไฟล์ทุกชนิดทีละไฟล์: สรุป/ว่าง/ซ้ำ/เสีย อัปเดต progress ให้ UI poll."""
    info = T.collect_files(folder, recursive, ext)
    if not info["ok"]:
        with _audio_lock:
            _audio_state.update(running=False, finished=True, error=info["error"],
                                target=info.get("target", ""), total=0, done=0)
        return
    exe, files, target = info["ffmpeg"], info["files"], info["target"]

    # รอบที่ 1: เก็บขนาด/ชนิด/จัดกลุ่มตามขนาด (เร็ว ใช้แค่ stat)
    sized: list[tuple[str, int]] = []
    by_type: dict[str, list[int]] = {}
    size_map: dict[int, list[str]] = {}
    total_bytes = 0
    for p in files:
        try:
            sz = os.path.getsize(p)
        except OSError:
            sz = 0
        sized.append((p, sz))
        ek = (os.path.splitext(p)[1].lower() or "(ไม่มีนามสกุล)")
        slot = by_type.setdefault(ek, [0, 0]); slot[0] += 1; slot[1] += sz
        total_bytes += sz
        if sz > 0:
            size_map.setdefault(sz, []).append(p)

    with _audio_lock:
        _audio_state.update(running=True, finished=False, total=len(files), done=0,
                            target=target, error="", ffmpeg=bool(exe),
                            summary={}, empty=[], duplicates=[], corrupt=[])

    # ไฟล์ที่ "ขนาดซ้ำกัน" เท่านั้นที่ต้อง hash (ประหยัดเวลา)
    cand = {p for ps in size_map.values() if len(ps) > 1 for p in ps}
    hash_map: dict[str, list[str]] = {}
    empty: list[str] = []
    corrupt: list[dict] = []

    # รอบที่ 2: ตรวจเสีย + hash หาไฟล์ซ้ำ (ส่วนที่ใช้เวลา -> มี progress)
    # หมายเหตุประสิทธิภาพ: ก่อนหน้านี้คัดลอก list(corrupt)/list(empty) ทุกไฟล์ภายใน lock
    # ทำให้สแกนหมื่นไฟล์เป็น O(n²) กระตุก. ตอนนี้อัปเดตเฉพาะ done ทุกไฟล์ (O(1))
    # ส่วน snapshot corrupt/empty คัดลอกเฉพาะรอบที่มีรายการใหม่เข้ามาจริง (จำกัดจำนวนครั้ง).
    for i, (p, sz) in enumerate(sized, 1):
        appended = False
        if sz == 0:
            empty.append(os.path.relpath(p, target))
            appended = True
        reason = T.check_one_file(p, exe)
        if reason is not None:
            corrupt.append({"file": os.path.relpath(p, target), "reason": reason})
            appended = True
        if p in cand:
            h = T.file_sha256(p)
            if h:
                hash_map.setdefault(h, []).append(p)
        with _audio_lock:
            _audio_state["done"] = i
            if appended:
                # คัดลอก snapshot เฉพาะเมื่อมีรายการใหม่ — ไม่ใช่ทุกไฟล์
                _audio_state["corrupt"] = list(corrupt)
                _audio_state["empty"] = list(empty)

    duplicates = []
    for ps in hash_map.values():
        if len(ps) > 1:
            try:
                dsz = os.path.getsize(ps[0])
            except OSError:
                dsz = 0
            duplicates.append({"size": dsz,
                               "files": [os.path.relpath(x, target) for x in sorted(ps)]})
    duplicates.sort(key=lambda d: -d["size"] * (len(d["files"]) - 1))

    by_type_list = sorted(
        [{"ext": k, "count": v[0], "bytes": v[1]} for k, v in by_type.items()],
        key=lambda x: -x["bytes"])
    largest = [{"file": os.path.relpath(p, target), "bytes": sz}
               for p, sz in sorted(sized, key=lambda x: -x[1])[:10] if sz > 0]
    summary = {"count": len(files), "bytes": total_bytes,
               "by_type": by_type_list[:20], "largest": largest,
               "wasted": sum(d["size"] * (len(d["files"]) - 1) for d in duplicates)}

    with _audio_lock:
        _audio_state.update(running=False, finished=True, summary=summary,
                            empty=empty, duplicates=duplicates, corrupt=corrupt)


# ----------------------------- web server -----------------------------

# C1: Route table สำหรับ do_POST — แต่ละ entry คือ (handler_method)
# handler เป็น method ของ Handler รับ (body: dict) คืน None (เรียก self._send เอง)
# สร้างทีหลัง class ผ่าน _register_routes() เพื่อให้ method binding ทำงานถูกต้อง
_POST_ROUTES: dict[str, str] = {}   # path -> method name (จะ bind ใน do_POST)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", no_cache=False):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if no_cache:
            # กัน WebView2 หยิบ index.html เก่าจาก cache (เปิดแล้ว "ไม่เห็นเปลี่ยน")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _json(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def _same_origin(self) -> bool:
        """กันคำสั่งข้ามเว็บ (CSRF): อนุญาตเฉพาะคำขอจากหน้าตัวเอง.

        pywebview/หน้าเว็บของแอปเองจะส่ง Origin/Referer เป็น http://127.0.0.1:<ACTUAL_PORT>
        หรือไม่ส่งมาเลย ส่วนเว็บภายนอกในเบราว์เซอร์จะมี Origin เป็นโดเมนอื่น -> ปฏิเสธ.
        ใช้ ACTUAL_PORT (อาจเลื่อนจาก PORT ตอน bind) ไม่ใช่ค่าคงที่ตายตัว.
        """
        port = ACTUAL_PORT
        allowed = f"http://{HOST}:{port}"
        allowed_host = f"{HOST}:{port}"
        host = (self.headers.get("Host") or "").lower()
        if host and host != allowed_host:
            return False
        origin = self.headers.get("Origin")
        if origin is not None:
            return origin == allowed
        ref = self.headers.get("Referer")
        if ref is not None:
            parsed = urllib.parse.urlparse(ref)
            return f"{parsed.scheme}://{parsed.netloc}" == allowed
        return True  # ไม่มี Origin/Referer (เช่น pywebview) -> ถือว่าปลอดภัย

    # ------------------------------------------------------------------
    # GET routes
    # ------------------------------------------------------------------
    def do_GET(self):
        path0 = self.path.split("?", 1)[0]   # ตัด query (?v=cache-buster) ออกก่อน match
        if path0.startswith("/api/") and not self._same_origin():
            self._send(403, json.dumps({"error": "ปฏิเสธ: คำขอข้ามต้นทาง (cross-origin)"},
                                       ensure_ascii=False))
            return
        if path0 in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8", no_cache=True)
            except FileNotFoundError:
                self._send(404, "index.html not found", "text/plain; charset=utf-8")
        elif self.path == "/api/agents":
            self._send(200, json.dumps({
                "model": default_model(),
                "default": DEFAULT_AGENT,
                "workspace": T.WORKSPACE,
                "agents": AG.list_agents(),
                "tools_available": AG.AVAILABLE_TOOLS,
                "skills": T.skills_list(),
            }, ensure_ascii=False))
        elif self.path == "/api/models":
            names = lm_models()              # ดึงรายชื่อโมเดลจาก LM Studio
            if MODEL and MODEL not in names:
                names.insert(0, MODEL)
            self._send(200, json.dumps({"models": names}, ensure_ascii=False))
        elif self.path == "/api/audio-scan-status":
            with _audio_lock:
                self._send(200, json.dumps(dict(_audio_state), ensure_ascii=False))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    # ------------------------------------------------------------------
    # POST route handlers (C1: แยกแต่ละ route เป็นฟังก์ชัน)
    # ------------------------------------------------------------------

    def _route_audio_scan(self, p: dict) -> None:
        with _audio_lock:
            if _audio_state["running"]:
                self._send(200, json.dumps({"ok": False, "error": "กำลังตรวจอยู่แล้ว"},
                                           ensure_ascii=False))
                return
            _audio_state.clear()
            _audio_state.update(_blank_scan())
            _audio_state["running"] = True
        threading.Thread(
            target=_audio_worker,
            args=(p.get("folder", ""), p.get("recursive", True), p.get("ext", "")),
            daemon=True).start()
        self._send(200, json.dumps({"ok": True}, ensure_ascii=False))

    def _route_chat(self, p: dict) -> None:
        T.set_cowork(p.get("cowork", False))
        if p.get("workspace"):
            T.set_workspace(p["workspace"])
        result = run_agent(p.get("agent", DEFAULT_AGENT),
                           p.get("messages", []),
                           p.get("model", MODEL),
                           cowork=p.get("cowork", False),
                           provider=p.get("provider"))
        self._send(200, json.dumps(result, ensure_ascii=False))

    def _route_cancel(self, p: dict) -> None:  # noqa: ARG002
        """B2: หยุด run_agent ที่กำลังทำงานอยู่."""
        request_cancel()
        self._send(200, json.dumps({"ok": True}, ensure_ascii=False))

    def _route_apply(self, p: dict) -> None:
        # ผู้ใช้กดยืนยัน -> เขียนไฟล์จริง (สำรอง .bak ให้อัตโนมัติ)
        if p.get("workspace"):
            T.set_workspace(p["workspace"])
        msg = T.write_file(p.get("path", "untitled.txt"), p.get("content", ""))
        self._send(200, json.dumps({"ok": True, "message": msg}, ensure_ascii=False))

    def _route_upload(self, p: dict) -> None:
        # แนบไฟล์ -> เซฟลงโฟลเดอร์งาน
        if p.get("workspace"):
            T.set_workspace(p["workspace"])
        name = os.path.basename(p.get("name", "upload.txt"))
        msg = T.write_file(name, p.get("content", ""))
        self._send(200, json.dumps({"ok": True, "name": name, "message": msg},
                                   ensure_ascii=False))

    def _route_setdir(self, p: dict) -> None:
        T.set_cowork(p.get("cowork", False))
        actual = T.set_workspace(p.get("path"))
        self._send(200, json.dumps({"ok": True, "workspace": actual}, ensure_ascii=False))

    def _route_reload_skills(self, p: dict) -> None:  # noqa: ARG002
        n = T.reload_skills()
        self._send(200, json.dumps({"ok": True, "count": n, "skills": T.skills_list()},
                                   ensure_ascii=False))

    def _route_create_skill(self, p: dict) -> None:
        ok, msg = SL.create_skill(
            p.get("name", ""), p.get("description", ""), p.get("type", "code"),
            p.get("parameters"), p.get("tool_code"), p.get("prompt_text"),
            category=p.get("category", "General"), overwrite=p.get("is_edit", False))
        if ok:
            T.reload_skills()
        self._send(200, json.dumps({"ok": ok, "message": msg, "skills": T.skills_list()},
                                   ensure_ascii=False))

    def _route_get_skill(self, p: dict) -> None:
        data = SL.get_skill_data(p.get("name", ""))
        if data:
            self._send(200, json.dumps({"ok": True, "data": data}, ensure_ascii=False))
        else:
            self._send(200, json.dumps({"ok": False, "message": "ไม่พบข้อมูล skill"},
                                       ensure_ascii=False))

    def _route_delete_skill(self, p: dict) -> None:
        ok, msg = SL.delete_skill(p.get("name", ""))
        if ok:
            T.reload_skills()
        self._send(200, json.dumps({"ok": ok, "message": msg, "skills": T.skills_list()},
                                   ensure_ascii=False))

    def _route_import_skill(self, p: dict) -> None:
        ok, msg = SL.import_or_convert_skill(p.get("path", ""))
        if ok:
            T.reload_skills()
        self._send(200, json.dumps({"ok": ok, "message": msg, "skills": T.skills_list()},
                                   ensure_ascii=False))

    def _route_files(self, p: dict) -> None:
        if p.get("workspace"):
            T.set_workspace(p["workspace"])
        base = os.path.abspath(T.WORKSPACE)
        sub = p.get("path", "") or ""
        full = os.path.abspath(os.path.join(base, sub))
        if not (full == base or full.startswith(base + os.sep)):
            full, sub = base, ""
        items = []
        if os.path.isdir(full):
            for name in sorted(os.listdir(full), key=lambda s: s.lower()):
                fp = os.path.join(full, name)
                is_dir = os.path.isdir(fp)
                items.append({"name": name, "dir": is_dir,
                              "size": 0 if is_dir else os.path.getsize(fp)})
        items.sort(key=lambda x: (not x["dir"], x["name"].lower()))
        self._send(200, json.dumps({"workspace": base, "path": sub, "files": items},
                                   ensure_ascii=False))

    def _route_readfile(self, p: dict) -> None:
        if p.get("workspace"):
            T.set_workspace(p["workspace"])
        content = T.read_file(p.get("path", ""))
        self._send(200, json.dumps({"content": content}, ensure_ascii=False))

    def _route_get_agent(self, p: dict) -> None:
        data = AG.get_agent(p.get("key", ""))
        if data:
            out = dict(data)
            out["key"] = p.get("key", "")
            self._send(200, json.dumps({"ok": True, "data": out}, ensure_ascii=False))
        else:
            self._send(200, json.dumps({"ok": False, "message": "ไม่พบ agent"},
                                       ensure_ascii=False))

    def _route_save_agent(self, p: dict) -> None:
        data = {
            "title": p.get("title", ""),
            "description": p.get("description", ""),
            "system": p.get("system", ""),
            "tools": p.get("tools", None),
            "skill_categories": p.get("skill_categories"),
        }
        ok, msg = AG.save_agent(p.get("key", ""), data, is_new=bool(p.get("is_new")))
        self._send(200, json.dumps(
            {"ok": ok, "message": msg, "agents": AG.list_agents()}, ensure_ascii=False))

    def _route_delete_agent(self, p: dict) -> None:
        ok, msg = AG.delete_agent(p.get("key", ""))
        self._send(200, json.dumps(
            {"ok": ok, "message": msg, "agents": AG.list_agents()}, ensure_ascii=False))

    def _route_import_agent_folder(self, p: dict) -> None:
        folder = p.get("path", "")
        if not os.path.isdir(folder):
            self._send(200, json.dumps({"ok": False, "message": "ไม่พบโฟลเดอร์"},
                                       ensure_ascii=False))
            return
        # SEC-1: กันไม่ให้เดินไฟล์ทั้ง root ไดรฟ์/โฟลเดอร์ระบบ (เช่น C:\ หรือ C:\Windows)
        # ที่นี่จะอ่านไฟล์ข้อความทุกไฟล์ในโฟลเดอร์แล้วฝังเป็น system prompt ของ agent
        # ใหม่ — เหมือนกับที่ _route_audio_scan/collect_files กันไว้อยู่แล้ว
        if T._is_blocked_root(os.path.abspath(folder)):
            self._send(200, json.dumps(
                {"ok": False, "message": "ปฏิเสธ: นำเข้าจาก root ไดรฟ์หรือโฟลเดอร์ระบบไม่ได้"},
                ensure_ascii=False))
            return
        base = os.path.basename(os.path.normpath(folder)) or "imported_agent"
        skip = (".png", ".jpg", ".jpeg", ".gif", ".exe", ".dll", ".so", ".pyc",
                ".zip", ".tar", ".gz", ".mp4", ".mp3", ".wav", ".pdf")
        chunks = []
        for root, dirs, files in os.walk(folder):
            for fn in files:
                if fn.startswith("."):
                    continue
                if fn.lower().endswith(skip):
                    continue
                fp = os.path.join(root, fn)
                try:
                    if os.path.getsize(fp) < 50000:
                        with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                            c = fh.read()
                        if c.strip():
                            rel = os.path.relpath(fp, folder)
                            chunks.append(f"## {rel}\n{c}")
                except Exception:  # noqa: BLE001
                    _log.debug("import-agent-folder: skip unreadable file", exc_info=True)
        text = ("\n\n".join(chunks))[:100000]
        existing = set(AG.all_agents().keys())
        slug = _re.sub(r"[^a-zA-Z0-9_-]", "_", base).strip("_").lower() or "imported_agent"
        key, i = slug, 1
        while key in existing:
            key = f"{slug}_{i}"
            i += 1
        sysp = (f"คุณคือผู้ช่วยที่มีความรู้จากโฟลเดอร์ '{base}' ตอบเป็นภาษาไทย.\n\n"
                f"[ความรู้อ้างอิง]\n{text}")
        ok, msg = AG.save_agent(key, {
            "title": f"📁 {base}", "description": f"agent จากโฟลเดอร์ {base}",
            "system": sysp, "tools": None, "skill_categories": None}, is_new=True)
        self._send(200, json.dumps({
            "ok": ok,
            "message": (f"นำเข้าเป็น agent '{key}' แล้ว") if ok else msg,
            "agents": AG.list_agents()}, ensure_ascii=False))

    def _route_init_workspace(self, p: dict) -> None:
        """F3: จัดโครงสร้างโฟลเดอร์งานเริ่มต้น (สร้างเฉพาะที่ยังไม่มี — ไม่ทับของเดิม).

        เขียนตรงได้โดยไม่ผ่าน proposal flow เพราะ trigger คือผู้ใช้กดปุ่มเอง
        (ไม่ใช่ AI ตัดสินใจ) และไม่ overwrite ไฟล์ใดๆ ที่มีอยู่แล้ว.
        """
        if p.get("workspace"):
            T.set_workspace(p["workspace"])
        base = os.path.abspath(T.WORKSPACE)
        created: list[str] = []
        skipped: list[str] = []
        for d in ("inbox", "projects", "archive"):
            full = os.path.join(base, d)
            if os.path.isdir(full):
                skipped.append(d + "/")
            else:
                os.makedirs(full, exist_ok=True)
                created.append(d + "/")
        templates = {
            T.AGENT_FILE: (
                "# คำสั่งประจำโฟลเดอร์งาน\n\n"
                "AI อ่านไฟล์นี้อัตโนมัติทุกครั้งก่อนเริ่มตอบในโฟลเดอร์นี้ — เขียนกฎ/บริบทของงาน เช่น\n\n"
                "- ตอบภาษาไทย กระชับ ตรงประเด็น\n"
                "- งานในโฟลเดอร์นี้คือ: (เติมเอง)\n"
                "- ไฟล์งานใหม่เข้าที่ inbox/ งานที่ทำอยู่ใน projects/ งานจบแล้วย้ายไป archive/\n\n"
                f"(อ่านสูงสุด {T.AGENT_MAX_CHARS} ตัวอักษรแรก — เขียนเรื่องสำคัญไว้บนสุด)\n"
            ),
            T.MEMORY_FILE: (
                "# ความจำของโฟลเดอร์นี้\n\n"
                "AI เพิ่มบันทึกที่นี่ผ่านเครื่องมือ remember (ถามยืนยันก่อนทุกครั้ง) "
                "และคุณแก้/ลบเองได้ตามใจ\n"
            ),
        }
        for name, content in templates.items():
            full = os.path.join(base, name)
            if os.path.isfile(full):
                skipped.append(name)
            else:
                with open(full, "w", encoding="utf-8") as f:
                    f.write(content)
                created.append(name)
        self._send(200, json.dumps({"ok": True, "created": created, "skipped": skipped},
                                   ensure_ascii=False))

    # ------------------------------------------------------------------
    # F4: MCP Connectors — จัดการ mcp.json ผ่าน UI
    # ------------------------------------------------------------------

    @staticmethod
    def _load_mcp_config() -> dict:
        if os.path.isfile(T.MCP_CONFIG_PATH):
            try:
                with open(T.MCP_CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if isinstance(cfg, dict):
                    return cfg
            except Exception:  # noqa: BLE001
                _log.warning("mcp.json อ่านไม่ได้/ผิดรูปแบบ — เริ่มจาก config ว่าง", exc_info=True)
        return {}

    @staticmethod
    def _save_mcp_config(cfg: dict) -> None:
        # เขียนแบบ atomic (tmp + os.replace) — ดับกลางคันไฟล์ไม่พัง
        tmp = T.MCP_CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, T.MCP_CONFIG_PATH)

    def _route_mcp_status(self, p: dict) -> None:  # noqa: ARG002
        self._send(200, json.dumps({
            "ok": True,
            "config_path": T.MCP_CONFIG_PATH,
            "exists": os.path.isfile(T.MCP_CONFIG_PATH),
            "servers": T.mcp_manager.status(),
        }, ensure_ascii=False))

    def _route_mcp_reload(self, p: dict) -> None:  # noqa: ARG002
        n = T.init_mcp()
        self._send(200, json.dumps({"ok": True, "tools": n,
                                    "servers": T.mcp_manager.status()}, ensure_ascii=False))

    def _route_mcp_save(self, p: dict) -> None:
        """เพิ่ม/แก้ MCP server หนึ่งตัวใน mcp.json แล้ว reload."""
        sid = _re.sub(r"[^a-zA-Z0-9_-]", "_", (p.get("id") or "").strip()).strip("_")
        command = (p.get("command") or "").strip()
        if not sid or not command:
            self._send(200, json.dumps({"ok": False, "message": "ต้องระบุ id และ command"},
                                       ensure_ascii=False))
            return
        args = p.get("args") or []
        if isinstance(args, str):
            args = args.split()
        env = p.get("env") if isinstance(p.get("env"), dict) else {}
        cfg = self._load_mcp_config()
        cfg.setdefault("mcpServers", {})[sid] = {
            "command": command, "args": [str(a) for a in args], "env": env}
        self._save_mcp_config(cfg)
        n = T.init_mcp()
        self._send(200, json.dumps({
            "ok": True, "id": sid, "tools": n, "servers": T.mcp_manager.status(),
            "message": (f"บันทึก '{sid}' แล้ว — เครื่องมือของ connector "
                        f"จะถูกถามยืนยันก่อนใช้ครั้งแรกเสมอ")}, ensure_ascii=False))

    def _route_mcp_delete(self, p: dict) -> None:
        sid = (p.get("id") or "").strip()
        cfg = self._load_mcp_config()
        servers = cfg.get("mcpServers", {})
        if sid not in servers:
            self._send(200, json.dumps({"ok": False, "message": f"ไม่พบ connector '{sid}'"},
                                       ensure_ascii=False))
            return
        del servers[sid]
        self._save_mcp_config(cfg)
        n = T.init_mcp()
        self._send(200, json.dumps({"ok": True, "tools": n,
                                    "servers": T.mcp_manager.status()}, ensure_ascii=False))

    def _route_schedule_run(self, p: dict) -> None:
        """F5: รันงานตามเวลาทันที (ปุ่ม ▶ ใน UI) — รันใน background ไม่บล็อก request.

        ไม่แตะ last_run (การทดสอบด้วยมือไม่ควรทำให้รอบอัตโนมัติของวันนี้ถูกข้าม).
        """
        sid = p.get("id") or ""
        items = DS.load("schedules", []) or []
        s = next((x for x in items if isinstance(x, dict) and x.get("id") == sid), None)
        if s is None:
            self._send(200, json.dumps({"ok": False, "message": "ไม่พบงานนี้"},
                                       ensure_ascii=False))
            return

        def _bg(job: dict):
            with _sched_running:
                try:
                    result = _run_schedule(job)
                except Exception as e:  # noqa: BLE001
                    result = f"ผิดพลาด: {e}"
                    _log.warning("schedule-run '%s' failed", job.get("title"), exc_info=True)
                cur_items = DS.load("schedules", []) or []
                for x in cur_items:
                    if isinstance(x, dict) and x.get("id") == sid:
                        x["last_result"] = result
                DS.save("schedules", cur_items)

        threading.Thread(target=_bg, args=(dict(s),), daemon=True).start()
        self._send(200, json.dumps(
            {"ok": True, "message": "เริ่มรันแล้ว — ผลจะอยู่ในโฟลเดอร์ reports/ ของโฟลเดอร์งาน"},
            ensure_ascii=False))

    def _route_confirm_skill(self, p: dict) -> None:
        """D1: ผู้ใช้กด 'อนุมัติ' skill ใหม่ → confirm แล้วรันจริง คืนผล."""
        name = p.get("name", "")
        args = p.get("args") or {}
        if not name:
            self._send(200, json.dumps({"ok": False, "message": "ต้องระบุชื่อ skill"},
                                       ensure_ascii=False))
            return
        SL.confirm_skill(name)
        result = _run(name, args)
        self._send(200, json.dumps({"ok": True, "result": result}, ensure_ascii=False))

    def _route_get_data(self, p: dict) -> None:
        self._send(200, json.dumps({"ok": True, "data": DS.load(p.get("key", ""))},
                                   ensure_ascii=False))

    def _route_set_data(self, p: dict) -> None:
        ok = DS.save(p.get("key", ""), p.get("data"))
        self._send(200, json.dumps({"ok": ok}, ensure_ascii=False))

    def _route_model_info(self, p: dict) -> None:
        name = p.get("model") or default_model()
        info: dict = {"context": None}
        try:
            data = _lm_get("/models")
            for m in data.get("data", []) or []:
                if isinstance(m, dict) and m.get("id") == name:
                    ctx = (m.get("context_length")
                           or m.get("max_context_length")
                           or m.get("loaded_context_length"))
                    if ctx:
                        try:
                            info["context"] = int(ctx)
                        except Exception:  # noqa: BLE001
                            _log.debug("model-info: bad context value", exc_info=True)
                    info["family"] = m.get("type") or m.get("object")
                    break
        except Exception as e:  # noqa: BLE001
            _log.warning("model-info: %s", e)
            info["error"] = str(e)
        self._send(200, json.dumps({"ok": True, "info": info}, ensure_ascii=False))

    # ------------------------------------------------------------------
    # C1: Route dispatch (do_POST ใหม่ — ใช้ route table)
    # ------------------------------------------------------------------

    # POST_ROUTES: map path -> method name
    _POST_ROUTE_TABLE: dict[str, str] = {
        "/api/audio-scan":           "_route_audio_scan",
        "/api/chat":                 "_route_chat",
        "/api/cancel":               "_route_cancel",
        "/api/apply":                "_route_apply",
        "/api/upload":               "_route_upload",
        "/api/setdir":               "_route_setdir",
        "/api/reload-skills":        "_route_reload_skills",
        "/api/create-skill":         "_route_create_skill",
        "/api/get-skill":            "_route_get_skill",
        "/api/delete-skill":         "_route_delete_skill",
        "/api/import-skill":         "_route_import_skill",
        "/api/files":                "_route_files",
        "/api/readfile":             "_route_readfile",
        "/api/get-agent":            "_route_get_agent",
        "/api/save-agent":           "_route_save_agent",
        "/api/delete-agent":         "_route_delete_agent",
        "/api/import-agent-folder":  "_route_import_agent_folder",
        "/api/get-data":             "_route_get_data",
        "/api/set-data":             "_route_set_data",
        "/api/model-info":           "_route_model_info",
        "/api/confirm-skill":        "_route_confirm_skill",   # D1
        "/api/init-workspace":       "_route_init_workspace",  # F3
        "/api/mcp-status":           "_route_mcp_status",      # F4
        "/api/mcp-reload":           "_route_mcp_reload",      # F4
        "/api/mcp-save":             "_route_mcp_save",        # F4
        "/api/mcp-delete":           "_route_mcp_delete",      # F4
        "/api/schedule-run":         "_route_schedule_run",    # F5
    }

    def do_POST(self) -> None:
        if not self._same_origin():
            self._send(403, json.dumps({"error": "ปฏิเสธ: คำขอข้ามต้นทาง (cross-origin)"},
                                       ensure_ascii=False))
            return
        method_name = self._POST_ROUTE_TABLE.get(self.path)
        if method_name is None:
            self._send(404, json.dumps({"error": "not found"}))
            return
        # D2: log traceback แทนกลืนเงียบ
        try:
            body = self._json()
            getattr(self, method_name)(body)
        except Exception as e:  # noqa: BLE001
            _log.error("POST %s raised: %s\n%s", self.path, e, traceback.format_exc())
            try:
                self._send(500, json.dumps({"reply": f"เกิดข้อผิดพลาด: {e}",
                                            "error": str(e),
                                            "tools": [], "proposals": []},
                                           ensure_ascii=False))
            except Exception:  # noqa: BLE001  — อาจ send ซ้ำถ้า header ส่งไปแล้ว
                pass


class JsApi:
    """API ที่ฝั่งหน้าเว็บเรียกผ่าน window.pywebview.api เพื่อเปิดกล่องเลือกโฟลเดอร์จริง."""
    def pick_folder(self):
        try:
            import webview
            win = webview.windows[0]
            res = win.create_file_dialog(webview.FOLDER_DIALOG)
            if res:
                return res[0] if isinstance(res, (list, tuple)) else res
        except Exception:  # noqa: BLE001
            # QUAL-3: log ไว้ debug — เดิมกลืนเงียบทำให้ debug ไม่ได้ว่าทำไมเลือกโฟลเดอร์ไม่ได้
            _log.debug("pick_folder: เปิด file dialog ไม่สำเร็จ", exc_info=True)
        return ""

    def copy_text(self, text):
        """คัดลอกข้อความลงคลิปบอร์ดฝั่ง Python (fallback เมื่อ WebView2 บล็อก clipboard ของ JS)."""
        # 1) ลองใช้ tkinter (มีใน stdlib ไม่ต้องติดตั้งเพิ่ม)
        try:
            import tkinter
            r = tkinter.Tk()
            r.withdraw()
            r.clipboard_clear()
            r.clipboard_append(text)
            r.update()  # ค้างค่าไว้ในคลิปบอร์ด
            r.destroy()
            return True
        except Exception:  # noqa: BLE001
            # QUAL-3: tkinter ใช้ไม่ได้เป็นเรื่องปกติในบางเครื่อง (ไม่มี Tcl/Tk) — log debug แล้วลอง pyperclip ต่อ
            _log.debug("copy_text: tkinter ใช้ไม่ได้ ลอง pyperclip ต่อ", exc_info=True)
        # 2) สำรอง: pyperclip ถ้ามีติดตั้ง
        try:
            import pyperclip
            pyperclip.copy(text)
            return True
        except Exception:  # noqa: BLE001
            _log.debug("copy_text: pyperclip ก็ใช้ไม่ได้ — คัดลอกไม่สำเร็จ", exc_info=True)
            return False

    def read_text(self):
        """อ่านข้อความจากคลิปบอร์ดฝั่ง Python (fallback สำหรับ Paste เมื่อ WebView2 บล็อก clipboard ของ JS)."""
        # 1) tkinter (stdlib)
        try:
            import tkinter
            r = tkinter.Tk()
            r.withdraw()
            try:
                data = r.clipboard_get()
            except Exception:  # noqa: BLE001 - คลิปบอร์ดว่าง/ไม่ใช่ข้อความ
                data = ""
            r.destroy()
            return data
        except Exception:  # noqa: BLE001
            # QUAL-3: tkinter ใช้ไม่ได้เป็นเรื่องปกติในบางเครื่อง (ไม่มี Tcl/Tk) — log debug แล้วลอง pyperclip ต่อ
            _log.debug("read_text: tkinter ใช้ไม่ได้ ลอง pyperclip ต่อ", exc_info=True)
        # 2) สำรอง: pyperclip
        try:
            import pyperclip
            return pyperclip.paste() or ""
        except Exception:  # noqa: BLE001
            _log.debug("read_text: pyperclip ก็ใช้ไม่ได้ — อ่านคลิปบอร์ดไม่สำเร็จ", exc_info=True)
            return ""


def _bind_server() -> ThreadingHTTPServer | None:
    """สร้างเซิร์ฟเวอร์ — ลอง bind พอร์ตเริ่มต้น ถ้าชนเลื่อนไปพอร์ตถัดไป (สูงสุด 10 พอร์ต).

    อัปเดต global ACTUAL_PORT เป็นพอร์ตที่ bind สำเร็จ คืน server หรือ None ถ้าทุกพอร์ตไม่ว่าง
    (กันแอป crash ตอนเปิดเมื่อมีอินสแตนซ์เดิมค้างอยู่ / พอร์ตถูกใช้).
    """
    global ACTUAL_PORT
    for offset in range(10):
        port = PORT + offset
        try:
            srv = ThreadingHTTPServer((HOST, port), Handler)
            ACTUAL_PORT = port
            return srv
        except OSError:
            continue
    return None


def _apply_window_icon(title: str = "LM Co-work") -> None:
    """ตั้งไอคอนหน้าต่าง/taskbar บน Windows.

    ทำไมต้องมี: `icon=` ใน PyInstaller spec ตั้งไอคอนให้ "ตัวไฟล์ .exe" เท่านั้น
    แต่หน้าต่างที่ pywebview สร้าง (WinForms) ใช้ไอคอน generic ของตัวเอง ทำให้
    ไอคอนบน taskbar ตอนรันไม่ใช่โลโก้แอป — ต้องยิง WM_SETICON ใส่ hwnd เอง.
    รันใน background thread: รอหน้าต่างโผล่ก่อน (โพลสูงสุด 10 วิ) แล้วค่อยตั้ง.
    """
    if os.name != "nt":
        return
    ico = os.path.join(HERE, "icon.ico")
    if not os.path.isfile(ico):
        _log.debug("_apply_window_icon: ไม่พบ icon.ico ที่ %s", ico)
        return
    try:
        import ctypes
        hwnd = 0
        deadline = time.time() + 10
        while not hwnd and time.time() < deadline:
            hwnd = ctypes.windll.user32.FindWindowW(None, title)
            if not hwnd:
                time.sleep(0.3)
        if not hwnd:
            _log.debug("_apply_window_icon: หา hwnd ของหน้าต่าง '%s' ไม่เจอ", title)
            return
        IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
        WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
        for size, which in ((16, ICON_SMALL), (32, ICON_BIG)):
            hicon = ctypes.windll.user32.LoadImageW(
                None, ico, IMAGE_ICON, size, size, LR_LOADFROMFILE)
            if hicon:
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, which, hicon)
    except Exception:  # noqa: BLE001
        _log.debug("_apply_window_icon: ตั้งไอคอนไม่สำเร็จ", exc_info=True)


def main():
    # ให้ Windows มองแอปนี้เป็นแอปของตัวเอง (ไม่จัดกลุ่ม/ใช้ไอคอนของ host process อื่น)
    # ต้องเรียกก่อนสร้างหน้าต่าง — มีผลกับไอคอน+การ pin บน taskbar
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LMCoWork.App")
        except Exception:  # noqa: BLE001
            _log.debug("SetCurrentProcessExplicitAppUserModelID ไม่สำเร็จ", exc_info=True)
    T.set_workspace(None)  # สร้างโฟลเดอร์งานเริ่มต้น

    # QUAL-5: โหลด MCP servers ใน background — ไม่บล็อกการเปิดหน้าต่าง
    def _mcp_boot():
        try:
            n = T.init_mcp()
            if n:
                _log.info("MCP พร้อม: %d tools จาก %d server", n, len(T.mcp_manager.clients))
        except Exception:  # noqa: BLE001
            _log.warning("โหลด MCP ไม่สำเร็จ", exc_info=True)

    threading.Thread(target=_mcp_boot, daemon=True).start()

    # F5: scheduler งานตามเวลา (รันเฉพาะตอนแอปเปิดอยู่)
    threading.Thread(target=_scheduler_loop, daemon=True).start()

    # สตาร์ต LM Studio server แบบ headless อัตโนมัติ (ไม่ต้องเปิดหน้าต่างแอป LM Studio)
    # ทำใน background thread เพื่อไม่บล็อกการเปิดหน้าต่าง และสตาร์ตแค่ครั้งเดียว
    if not _lm_alive():
        print("⏳ กำลังสตาร์ต LM Studio server แบบ headless (เบื้องหลัง) ...")
        threading.Thread(target=ensure_lmstudio, daemon=True).start()
    else:
        print(f"✅ LM Studio server พร้อม ({LMSTUDIO_BASE})")

    server = _bind_server()
    if server is None:
        # ทุกพอร์ตไม่ว่าง -> แจ้งเตือนชัด (อย่า crash เงียบ)
        msg = (f"เปิดเซิร์ฟเวอร์ไม่ได้: พอร์ต {PORT}–{PORT + 9} ทั้งหมดถูกใช้อยู่ "
               f"(อาจมี LM Co-work เปิดค้างอยู่อินสแตนซ์เดิม) ปิดโปรแกรมเดิมแล้วลองอีกครั้ง")
        frozen = getattr(sys, "frozen", False)
        if frozen:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, msg, "LM Co-work", 0x10)
            except Exception:  # noqa: BLE001
                print(msg, file=sys.stderr)
        else:
            print(msg, file=sys.stderr)
        return

    # cache-buster ?v=<เวลาเปิด> ทำให้ URL ไม่ซ้ำเดิม -> WebView2 โหลด index.html ใหม่เสมอ
    url = f"http://{HOST}:{ACTUAL_PORT}/?v={int(time.time())}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    frozen = getattr(sys, "frozen", False)   # True เมื่อรันเป็น .exe
    print(f"🤖 LM Co-work พร้อมแล้ว -> {url}"
          + (f"  (พอร์ต {PORT} ชน เลื่อนไปใช้ {ACTUAL_PORT})" if ACTUAL_PORT != PORT else ""))
    print(f"   โฟลเดอร์งานเริ่มต้น: {T.WORKSPACE}")

    # เปิดเป็นหน้าต่างโปรแกรมจริง (pywebview)
    try:
        import webview  # type: ignore
        
        # System Tray & Hotkey logic
        def start_tray(window):
            try:
                import pystray
                from PIL import Image, ImageDraw
                import keyboard
                import os
                
                # Create a simple icon
                image = Image.new('RGB', (64, 64), color=(37, 99, 235))
                d = ImageDraw.Draw(image)
                d.text((10,20), "LM", fill=(255,255,255))
                
                def show_window(icon, item):
                    window.show()
                    window.restore()
                    
                def quit_app(icon, item):
                    icon.stop()
                    window.destroy()
                    os._exit(0)
                    
                icon = pystray.Icon("LM Co-work", image, "LM Co-work", menu=pystray.Menu(
                    pystray.MenuItem("Show", show_window, default=True),
                    pystray.MenuItem("Quit", quit_app)
                ))
                
                # Global hotkey to summon
                def summon():
                    window.show()
                    window.restore()
                    
                keyboard.add_hotkey('ctrl+alt+space', summon)
                
                icon.run()
            except Exception as e:
                print(f"System tray/Hotkey not available: {e}")

        window = webview.create_window("LM Co-work", url, js_api=JsApi(), width=1180, height=800,
                              min_size=(860, 580), text_select=True)
                              
        threading.Thread(target=start_tray, args=(window,), daemon=True).start()
        # ตั้งไอคอนหน้าต่าง/taskbar หลังหน้าต่างโผล่ (WinForms ใช้ไอคอน generic ถ้าไม่ตั้งเอง)
        threading.Thread(target=_apply_window_icon, daemon=True).start()

        webview.start()
        print("\nปิดโปรแกรมแล้ว 👋")
        return
    except Exception as e:  # noqa: BLE001
        import traceback
        detail = traceback.format_exc()
        # แนะนำติดตั้ง WebView2 เฉพาะเมื่ออาการเข้าข่ายจริง (อย่าเดามั่ว)
        looks_like_webview2 = any(
            k in detail.lower()
            for k in ("webview2", "edgechromium", "edge", "clr", "pythonnet", "winrt", "0x80")
        )
        if frozen:
            # โหมด .exe: ไม่เปิดเบราว์เซอร์ — แจ้งเตือนแล้วปิด
            hint = ("ต้องติดตั้ง Microsoft Edge WebView2 Runtime ก่อน "
                    "(ดาวน์โหลดฟรีจากเว็บ Microsoft)"
                    if looks_like_webview2 else
                    "เกิดข้อผิดพลาดระหว่างเปิดหน้าต่าง โปรดส่งข้อความด้านล่างนี้ให้ผู้พัฒนา")
            msg = f"เปิดหน้าต่างโปรแกรมไม่ได้:\n{e!r}\n\n{hint}"
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, msg, "LM Co-work", 0x10)
            except Exception:  # noqa: BLE001
                print(msg)
            print(detail, file=sys.stderr)
            return
        # โหมดสคริปต์ (dev): เปิดเบราว์เซอร์ให้สะดวก
        print(f"   (ไม่พบ pywebview จึงเปิดในเบราว์เซอร์แทน: {e})")
        webbrowser.open(url)
        try:
            # BUG-FIX: เดิม import time ซ้ำตรงนี้ ทำให้ Python ตีความ `time` เป็น
            # local variable ของทั้งฟังก์ชัน main() -> บรรทัด int(time.time()) ด้านบน
            # (ตอนสร้าง url) กลาย เป็น UnboundLocalError ทุกครั้งที่เรียก main() เลย
            # (ไม่ต้องรอให้ pywebview ล้มก่อนด้วยซ้ำ) ตัดออก ใช้ time ที่ import ไว้
            # module-level บรรทัดบนสุดของไฟล์แทน
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nปิดเซิร์ฟเวอร์แล้ว 👋")


if __name__ == "__main__":
    main()
