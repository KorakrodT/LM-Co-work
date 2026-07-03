"""Smoke tests — รันเร็ว ๆ ดักบั๊กพื้นฐานก่อน build/ปล่อย.

รัน:  python -m pytest -q
ไม่ต้องมี Ollama/pywebview ก็รันผ่าน (มีการ stub ก่อน import server)
"""
from __future__ import annotations

import io
import json
import os
import sys
import types
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _stub_optional_deps() -> None:
    """เผื่ออนาคต/โมดูลเสริม — ปัจจุบัน server.py คุยกับ LM Studio ผ่าน urllib
    จึงไม่ต้องพึ่งแพ็กเกจ client ภายนอกแล้ว (no-op ที่ปลอดภัย)."""
    return None


# --------------------------------------------------------------------------
# 1) charmap fix: _force_utf8_streams ต้องเปลี่ยน stream cp874/None เป็น utf-8
# --------------------------------------------------------------------------
def test_force_utf8_handles_cp874_stream(monkeypatch):
    _stub_optional_deps()
    server = importlib.import_module("server")

    # จำลอง stdout แบบ cp874 ('charmap') ที่ encode ภาษาไทยไม่ได้
    fake = io.TextIOWrapper(io.BytesIO(), encoding="cp874", errors="strict")
    monkeypatch.setattr(sys, "stdout", fake, raising=False)
    server._force_utf8_streams()
    # หลังแก้ต้องพิมพ์ไทย+อีโมจิได้โดยไม่ระเบิด
    print("ทดสอบ 🤖 ภาษาไทย")  # ต้องไม่ raise UnicodeEncodeError


def test_force_utf8_handles_none_stream(monkeypatch):
    _stub_optional_deps()
    server = importlib.import_module("server")
    monkeypatch.setattr(sys, "stdout", None, raising=False)
    monkeypatch.setattr(sys, "stderr", None, raising=False)
    server._force_utf8_streams()
    assert sys.stdout is not None and sys.stderr is not None
    print("ok 🤖")  # ต้องไม่ระเบิดแม้เดิมเป็น None


# --------------------------------------------------------------------------
# 2) path jail: ห้ามหลุดออกนอกโฟลเดอร์งาน
# --------------------------------------------------------------------------
def test_path_jail_blocks_traversal(tmp_path):
    _stub_optional_deps()
    import tools as T
    T.set_workspace(str(tmp_path))
    root = os.path.abspath(str(tmp_path))
    # ไฟล์ปกติในงาน -> ผ่าน
    assert T._resolve("note.txt") is not None
    # '..' หลุดออกนอก -> ปฏิเสธ (None)
    assert T._resolve("../../etc/passwd") is None
    assert T._resolve("../outside.txt") is None
    # absolute path ถูกทำให้เป็น relative แล้ว jail ไว้ใน workspace (ปลอดภัย ไม่หลุด)
    for p in ("/etc/passwd", "C:\\Windows\\system.ini", "\\\\server\\share\\x"):
        r = T._resolve(p)
        assert r is None or r == root or r.startswith(root + os.sep), f"escaped: {p} -> {r}"


def test_write_then_read_roundtrip(tmp_path):
    _stub_optional_deps()
    import tools as T
    T.set_workspace(str(tmp_path))
    T.write_file("ไทย.txt", "สวัสดี 🤖")
    assert "สวัสดี" in T.read_file("ไทย.txt")


# --------------------------------------------------------------------------
# 3) calculator ปลอดภัย: นิพจน์อันตรายต้องไม่ถูกรัน
# --------------------------------------------------------------------------
def test_calculator_safe():
    _stub_optional_deps()
    import tools as T
    assert "= 14" in T.calculator("2 * (3 + 4)")
    assert "ไม่" in T.calculator("__import__('os').system('echo hi')")


# --------------------------------------------------------------------------
# 4) SSRF guard: host ภายในต้องถูกปฏิเสธ
# --------------------------------------------------------------------------
def test_fetch_url_blocks_internal():
    _stub_optional_deps()
    import tools as T
    assert "ปฏิเสธ" in T.fetch_url("http://127.0.0.1:11500/")
    assert "ปฏิเสธ" in T.fetch_url("http://localhost/")
    assert "http" in T.fetch_url("ftp://example.com").lower() or "URL" in T.fetch_url("ftp://x")

# --------------------------------------------------------------------------
# 5) security regressions จากรอบปรับปรุง
# --------------------------------------------------------------------------
def test_cowork_adds_file_tools_even_when_agent_disables_tools():
    _stub_optional_deps()
    server = importlib.import_module("server")
    schemas = server.schemas_for({"tools": []}, cowork=True)
    names = {s["function"]["name"] for s in schemas}
    assert server.COWORK_TOOLS.issubset(names)
    assert server.schemas_for({"tools": []}, cowork=False) == []


def test_same_origin_blocks_cross_site_origin_and_host():
    _stub_optional_deps()
    server = importlib.import_module("server")
    h = object.__new__(server.Handler)

    h.headers = {"Host": "127.0.0.1:11500", "Origin": "http://evil.example"}
    assert not h._same_origin()

    h.headers = {"Host": "evil.example", "Origin": "http://127.0.0.1:11500"}
    assert not h._same_origin()

    h.headers = {"Host": "127.0.0.1:11500", "Origin": "http://127.0.0.1:11500"}
    assert h._same_origin()


def test_get_current_time_uses_timezone():
    _stub_optional_deps()
    import tools as T
    assert "Asia/Bangkok" in T.get_current_time("Asia/Bangkok")
    assert "ไม่รู้จัก timezone" in T.get_current_time("Not/AZone")


def test_workspace_rejects_filesystem_root():
    _stub_optional_deps()
    import tools as T
    actual = T.set_workspace(os.path.abspath(os.sep))
    assert actual == T._DEFAULT_WS


def test_redirect_handler_is_limited_to_three_hops():
    _stub_optional_deps()
    import tools as T
    assert T._SafeRedirectHandler.max_redirections == 3
    assert T._SafeRedirectHandler.max_repeats == 3


def test_cowork_unlocks_arbitrary_workspace(monkeypatch, tmp_path):
    # Co-Work ON  -> workspace outside allowed roots is allowed
    # Co-Work OFF -> restricted to allowed roots; drive root / system dirs always blocked
    _stub_optional_deps()
    import tools as T
    outside = tmp_path / "music" / "Wav"
    outside.mkdir(parents=True)
    target = str(outside)
    # ทำให้ target รับประกันว่า "นอก allowed roots": ตั้ง allowed roots เป็นพาธอื่นที่ไม่ใช่ tmp
    monkeypatch.setattr(T, "_ALLOWED_WORKSPACE_ROOTS", (str(tmp_path / "nowhere"),))
    try:
        T.set_cowork(False)
        assert not T._is_allowed_workspace(target)     # off: rejected
        T.set_cowork(True)
        assert T._is_allowed_workspace(target)          # on: allowed
        assert T.set_workspace(target) == os.path.abspath(target)
        # even with Co-Work on, drive root / system folders stay blocked
        assert not T._is_allowed_workspace(os.path.abspath(os.sep))
    finally:
        T.set_cowork(False)


# --------------------------------------------------------------------------
# 6) LM Studio backend + เครื่องมือตรวจไฟล์เสียงที่เพิ่มเข้ามา
# --------------------------------------------------------------------------
def test_lmstudio_helpers_exist_and_fallback():
    _stub_optional_deps()
    server = importlib.import_module("server")
    # โครงสร้าง LM Studio พร้อม และ fallback เมื่อ LM Studio ไม่เปิด
    assert server.LMSTUDIO_BASE.endswith("/v1")
    assert isinstance(server.lm_models(), list)        # ต่อไม่ได้ -> []
    assert server.default_model()                       # อย่างน้อยได้ 'local-model'


def test_audio_tools_registered():
    _stub_optional_deps()
    import tools as T
    for name in ("check_audio_integrity", "check_ffmpeg", "install_ffmpeg"):
        assert name in T.TOOLS, f"missing tool: {name}"
    schema_names = {s["function"]["name"] for s in T.TOOL_SCHEMAS}
    assert {"check_audio_integrity", "check_ffmpeg", "install_ffmpeg"}.issubset(schema_names)


def test_audio_check_handles_missing_ffmpeg(monkeypatch):
    _stub_optional_deps()
    import tools as T
    monkeypatch.setattr(T, "_find_ffmpeg", lambda: None)
    out = T.check_audio_integrity("/whatever")
    assert "ffmpeg" in out.lower()      # ต้องแนะนำให้ติดตั้ง ffmpeg


def test_audio_check_rejects_outside_workspace(monkeypatch, tmp_path):
    """การตรวจ audio เป็นแบบ read-only จึงอนุญาตโฟลเดอร์ใดก็ได้ยกเว้น root ของไดรฟ์/ระบบ."""
    _stub_optional_deps()
    import tools as T
    monkeypatch.setattr(T, "_find_ffmpeg", lambda: "ffmpeg")  # ผ่านด่าน ffmpeg
    T.set_cowork(False)
    # โฟลเดอร์ root ของไดรฟ์ -> ต้องปฏิเสธเสมอ (กันสแกนทั้งไดรฟ์/ระบบ)
    assert "ปฏิเสธ" in T.check_audio_integrity(os.path.abspath(os.sep))
    # โฟลเดอร์ที่ไม่มีอยู่จริง (นอก workspace แต่ไม่ใช่ root) -> "ไม่พบโฟลเดอร์" ไม่ใช่ "ปฏิเสธ"
    assert "ไม่พบโฟลเดอร์" in T.check_audio_integrity(str(tmp_path / "music" / "Wav"))


# --------------------------------------------------------------------------
# 7) A1: run_agent ต้องทนต่อ response ผิดรูปแบบ (กัน KeyError ระเบิดเงียบ)
# --------------------------------------------------------------------------
def test_extract_message_normal_response():
    _stub_optional_deps()
    server = importlib.import_module("server")
    msg = server._extract_message({"choices": [{"message": {"content": "hi", "tool_calls": None}}]})
    assert msg == {"content": "hi", "tool_calls": None}


def test_extract_message_handles_malformed():
    _stub_optional_deps()
    server = importlib.import_module("server")
    # แบบผิด ๆ ทุกหน้าตา -> ต้องคืน None ไม่ raise
    assert server._extract_message(None) is None
    assert server._extract_message({}) is None
    assert server._extract_message({"choices": []}) is None
    assert server._extract_message({"choices": [{}]}) is None
    assert server._extract_message({"error": {"message": "boom"}}) is None


def test_format_error_reply():
    _stub_optional_deps()
    server = importlib.import_module("server")
    assert "boom" in server._format_error_reply({"error": {"message": "boom"}})
    assert "ผิดรูปแบบ" in server._format_error_reply({})


def test_run_agent_handles_error_response(monkeypatch):
    """LM Studio คืน {error:...} (เช่น context เกิน/โมเดลไม่โหลด) -> ต้องได้ reply ที่อ่านรู้เรื่อง ไม่ raise."""
    _stub_optional_deps()
    server = importlib.import_module("server")
    monkeypatch.setattr(server, "_openai_chat",
                        lambda *a, **k: {"error": {"message": "model not loaded"}})
    result = server.run_agent("general", [{"role": "user", "content": "hi"}],
                              model="x", cowork=False)
    assert "model not loaded" in result["reply"]
    assert result["proposals"] == []


def test_run_agent_handles_malformed_response(monkeypatch):
    _stub_optional_deps()
    server = importlib.import_module("server")
    monkeypatch.setattr(server, "_openai_chat", lambda *a, **k: {"totally": "broken"})
    result = server.run_agent("general", [{"role": "user", "content": "hi"}],
                              model="x", cowork=False)
    assert "ผิดรูปแบบ" in result["reply"]


# --------------------------------------------------------------------------
# 8) A2: หาพอร์ตว่างอัตโนมัติ + _same_origin ใช้พอร์ตจริง
# --------------------------------------------------------------------------
def test_bind_server_finds_next_free_port(monkeypatch):
    """ถ้าพอร์ตเริ่มต้นชน -> เลื่อนไปพอร์ตถัดไป + อัปเดต ACTUAL_PORT."""
    _stub_optional_deps()
    server = importlib.import_module("server")
    calls = {"n": 0}
    real_init = server.ThreadingHTTPServer.__init__

    def fake_init(self, addr, Handler, *a, **k):
        # ครั้งที่ 1, 2 ชน (จำลองพอร์ตไม่ว่าง), ครั้งที่ 3 ผ่าน
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError("address in use")
        # ครั้งที่ 3 -> bind จริงที่ addr ที่ส่งมา (ใช้พอร์ตจริงจาก addr)
        real_init(self, addr, Handler, *a, **k)
        self._bound_addr = addr

    monkeypatch.setattr(server.ThreadingHTTPServer, "__init__", fake_init)
    server.ACTUAL_PORT = server.PORT
    srv = server._bind_server()
    try:
        assert srv is not None
        assert server.ACTUAL_PORT == server.PORT + 2          # เลื่อน 2 ครั้ง
        assert srv._bound_addr[1] == server.PORT + 2
    finally:
        if srv is not None:
            srv.server_close()


def test_bind_server_returns_none_when_all_busy(monkeypatch):
    _stub_optional_deps()
    server = importlib.import_module("server")

    def always_busy(self, addr, Handler, *a, **k):
        raise OSError("address in use")

    monkeypatch.setattr(server.ThreadingHTTPServer, "__init__", always_busy)
    assert server._bind_server() is None


def test_same_origin_uses_actual_port(monkeypatch):
    """เมื่อ ACTUAL_PORT เลื่อน _same_origin ต้องยอมรับ Host/Origin พอร์ตใหม่ ปฏิเสธพอร์ตเก่า."""
    _stub_optional_deps()
    server = importlib.import_module("server")
    monkeypatch.setattr(server, "ACTUAL_PORT", 11502)
    h = object.__new__(server.Handler)

    h.headers = {"Host": "127.0.0.1:11502", "Origin": "http://127.0.0.1:11502"}
    assert h._same_origin()                                    # พอร์ตจริง -> ผ่าน

    h.headers = {"Host": "127.0.0.1:11500", "Origin": "http://127.0.0.1:11500"}
    assert not h._same_origin()                                # พอร์ตเก่า -> ปฏิเสธ


# --------------------------------------------------------------------------
# 9) A3: _audio_worker ต้อง snapshot corrupt/empty เฉพาะรอบที่มีรายการใหม่
#     (ก่อนหน้านี้ snapshot ทุกไฟล์ = O(n²) กระตุกเมื่อสแกนไลบรารีใหญ่)
# --------------------------------------------------------------------------
def test_audio_worker_snapshots_only_on_change(monkeypatch, tmp_path):
    _stub_optional_deps()
    server = importlib.import_module("server")
    import tools as T

    # สร้างไฟล์ธรรมดา 8 ไฟล์ ทั้งหมดปกติ (ไม่ว่าง/ไม่เสีย) -> ไม่ควร snapshot เลยใน loop
    folder = tmp_path / "lib"
    folder.mkdir()
    for i in range(8):
        (folder / f"song{i}.mp3").write_bytes(b"\x00" * 16)

    T.set_workspace(str(folder))
    monkeypatch.setattr(T, "_find_ffmpeg", lambda: "ffmpeg")
    # check_one_file คืน None (ปกติ) เสมอ -> corrupt ไม่เพิ่ม
    monkeypatch.setattr(T, "check_one_file", lambda p, exe: None)

    # นับจำนวนครั้งที่ assign corrupt/empty โดยแทน _audio_state ด้วย dict ที่ track การเขียน
    snapshot_count = {"n": 0}

    class TrackingDict(dict):
        def __setitem__(self, key, value):
            if key in ("corrupt", "empty"):
                snapshot_count["n"] += 1
            super().__setitem__(key, value)

        def update(self, *a, **kw):
            d = dict(*a, **kw)
            for key in d:
                if key in ("corrupt", "empty"):
                    snapshot_count["n"] += 1
            super().update(d)

    # แทนที่ _audio_state ด้วย TrackingDict (init ค่าเริ่มต้นเหมือน _blank_scan)
    new_state = TrackingDict(server._blank_scan())
    monkeypatch.setattr(server, "_audio_state", new_state)

    server._audio_worker(str(folder), recursive=True, ext="")

    # สิ่งสำคัญ: snapshot ต้องไม่ขึ้นกับจำนวนไฟล์ (O(1) คงที่ ไม่ใช่ O(n))
    # ก่อนแก้: 8 ไฟล์ปกติ -> สแกน 8 รอบใน loop แต่ละรอบ snapshot = 8 ครั้งใน loop + 2 ตอน reset/init
    # หลังแก้: snapshot เฉพาะตอน reset/init (2) + ตอนจบ (2) = 4 ครั้ง ไม่ว่าจะ 8 หรือ 800 ไฟล์
    assert snapshot_count["n"] == 4, (
        f"snapshot ควรคงที่ที่ 4 (reset+init+finish) แต่ได้ {snapshot_count['n']} ครั้ง — "
        f"ถ้าได้มากกว่านี้แปลว่ายัง snapshot ใน loop")
    # done ต้องครบ
    assert new_state["done"] == 8
    assert new_state["finished"] is True


def test_audio_worker_snapshots_scale_with_findings_not_files(monkeypatch, tmp_path):
    """ยืนยัน O(1): เพิ่มไฟล์เป็น 40 ไฟล์ปกติ -> snapshot ยังคงที่ที่ 4 ครั้งเหมือนเดิม."""
    _stub_optional_deps()
    server = importlib.import_module("server")
    import tools as T

    folder = tmp_path / "lib2"
    folder.mkdir()
    for i in range(40):
        (folder / f"song{i}.mp3").write_bytes(b"\x00" * 16)

    T.set_workspace(str(folder))
    monkeypatch.setattr(T, "_find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(T, "check_one_file", lambda p, exe: None)

    snapshot_count = {"n": 0}

    class TrackingDict(dict):
        def __setitem__(self, key, value):
            if key in ("corrupt", "empty"):
                snapshot_count["n"] += 1
            super().__setitem__(key, value)

        def update(self, *a, **kw):
            d = dict(*a, **kw)
            for key in d:
                if key in ("corrupt", "empty"):
                    snapshot_count["n"] += 1
            super().update(d)

    new_state = TrackingDict(server._blank_scan())
    monkeypatch.setattr(server, "_audio_state", new_state)
    server._audio_worker(str(folder), recursive=True, ext="")

    # 40 ไฟล์ แต่ snapshot ยังคงที่ที่ 4 (เท่ากับกรณี 8 ไฟล์) -> ไม่ใช่ O(n)
    assert snapshot_count["n"] == 4
    assert new_state["done"] == 40


# --------------------------------------------------------------------------
# 10) C3: fetch_url ใช้ User-Agent ชื่อแอปปัจจุบัน (LMCoWork) ไม่ใช่ OllamaAgent
# --------------------------------------------------------------------------
def test_fetch_url_uses_correct_user_agent(monkeypatch):
    _stub_optional_deps()
    import tools as T
    captured = {}

    class FakeResp:
        def read(self, n=-1):
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeOpener:
        def open(self, req, timeout=None):
            captured["ua"] = req.headers.get("User-agent", "")
            return FakeResp()

    # ข้ามด่าน SSRF + stub opener (fetch_url ใช้ build_opener().open)
    monkeypatch.setattr(T, "_is_public_host", lambda host: True)
    monkeypatch.setattr(T.urllib.request, "build_opener", lambda *a: FakeOpener())
    T.fetch_url("https://example.com/")
    assert captured["ua"] == "LMCoWork/1.0", f"got {captured.get('ua')!r}"


# --------------------------------------------------------------------------
# 11) C1: do_POST ใช้ route table — ทุก route ที่มีจริงต้องลงทะเบียนครบ + path ที่ไม่รู้จักคืน 404
# --------------------------------------------------------------------------
def test_post_route_table_has_all_known_routes():
    """ตรวจว่า _POST_ROUTE_TABLE ลงทะเบียนทุก route และ method จริงมีอยู่ใน class."""
    _stub_optional_deps()
    server = importlib.import_module("server")
    table = server.Handler._POST_ROUTE_TABLE
    # ต้องมี route พื้นฐานอย่างน้อยครบ
    required = {
        "/api/chat", "/api/apply", "/api/upload", "/api/setdir",
        "/api/reload-skills", "/api/create-skill", "/api/get-skill",
        "/api/delete-skill", "/api/import-skill", "/api/files",
        "/api/readfile", "/api/get-agent", "/api/save-agent",
        "/api/delete-agent", "/api/get-data", "/api/set-data",
        "/api/model-info", "/api/audio-scan",
        "/api/cancel",   # B2
    }
    missing = required - set(table.keys())
    assert not missing, f"missing routes: {missing}"
    # ทุก method ที่ลงทะเบียนต้องมีจริงใน Handler
    for path, mname in table.items():
        assert hasattr(server.Handler, mname), f"method {mname!r} not found for route {path}"


def test_do_post_unknown_route_returns_404():
    """route ที่ไม่มีใน table ต้องตอบ 404."""
    _stub_optional_deps()
    server = importlib.import_module("server")
    h = object.__new__(server.Handler)

    sent = {}

    def fake_send(code, body, ctype="application/json; charset=utf-8"):
        sent["code"] = code
        sent["body"] = body if isinstance(body, str) else body.decode("utf-8")

    def fake_same_origin():
        return True

    def fake_json():
        return {}

    h._send = fake_send
    h._same_origin = fake_same_origin
    h._json = fake_json
    h.path = "/api/nonexistent-route-xyz"
    h.do_POST()
    assert sent.get("code") == 404


# --------------------------------------------------------------------------
# 12) D2: do_POST route ที่ระเบิด → ยังส่ง 500 + log traceback ไม่กลืนเงียบ
# --------------------------------------------------------------------------
def test_do_post_route_exception_returns_500(monkeypatch):
    """ถ้า route handler raise → ตอบ 500 พร้อม error message (ไม่ crash/กลืนเงียบ)."""
    _stub_optional_deps()
    server = importlib.import_module("server")
    h = object.__new__(server.Handler)

    sent = {}

    def fake_send(code, body, ctype="application/json; charset=utf-8"):
        sent["code"] = code
        sent["body"] = body if isinstance(body, str) else body.decode("utf-8")

    def fake_same_origin():
        return True

    def fake_json():
        return {}

    def boom(p):
        raise RuntimeError("boom from test")

    h._send = fake_send
    h._same_origin = fake_same_origin
    h._json = fake_json
    h.path = "/api/get-data"
    monkeypatch.setattr(server.Handler, "_route_get_data", boom)
    h.do_POST()
    assert sent.get("code") == 500
    assert "boom" in sent.get("body", "")



# --------------------------------------------------------------------------
# 14) B2: cancel flag — set/clear + run_agent หยุดเมื่อ cancel
# --------------------------------------------------------------------------
def test_cancel_flag_set_and_clear():
    _stub_optional_deps()
    server = importlib.import_module("server")
    server._clear_cancel()
    assert not server._is_cancelled()
    server.request_cancel()
    assert server._is_cancelled()
    server._clear_cancel()
    assert not server._is_cancelled()


def test_run_agent_stops_on_cancel(monkeypatch):
    """run_agent ต้องคืน cancelled=True ทันทีถ้า cancel ก่อนวนลูป."""
    _stub_optional_deps()
    server = importlib.import_module("server")
    # stub T.skills_list ซึ่งถูกเรียกก่อนลูป เพื่อ trigger cancel หลังจากที่ _clear_cancel ถูกเรียกแล้ว
    original_skills_list = server.T.skills_list
    def cancel_trigger_skills_list(*args, **kwargs):
        server.request_cancel()
        return original_skills_list(*args, **kwargs)
    monkeypatch.setattr(server.T, "skills_list", cancel_trigger_skills_list)

    # stub _openai_chat ให้ไม่ถูกเรียก (ถ้าถูกเรียกแสดงว่ายังไม่ตรวจ flag)
    call_count = {"n": 0}

    def should_not_call(*a, **k):
        call_count["n"] += 1
        return {"choices": [{"message": {"content": "hi"}}]}

    monkeypatch.setattr(server, "_openai_chat", should_not_call)
    result = server.run_agent("general", [{"role": "user", "content": "hi"}],
                              model="x", cowork=False)
    assert result.get("cancelled") is True
    assert call_count["n"] == 0, "_openai_chat ไม่ควรถูกเรียกเมื่อ cancel แล้ว"


def test_cancel_route_sets_flag():
    """/api/cancel route ต้อง set _cancel_requested."""
    _stub_optional_deps()
    server = importlib.import_module("server")
    server._clear_cancel()
    h = object.__new__(server.Handler)
    sent = {}

    def fake_send(code, body, ctype="application/json; charset=utf-8"):
        sent["code"] = code

    h._send = fake_send
    h._route_cancel({})
    assert server._is_cancelled()
    assert sent.get("code") == 200
    server._clear_cancel()


# --------------------------------------------------------------------------
# 15) D1: ยืนยัน skill ใหม่ก่อนรัน — skill ที่ยังไม่ confirm ต้องคืน proposal พิเศษ
# --------------------------------------------------------------------------
def test_skill_confirm_required_for_new_skill(tmp_path, monkeypatch):
    """skill ใหม่ที่ยังไม่ confirm → _handle_tool_call คืน proposal type=skill_confirm ไม่ใช่รันทันที."""
    _stub_optional_deps()
    server = importlib.import_module("server")
    import skills_loader as SL

    # สร้าง skill จำลอง
    skill_name = "test_d1_skill"
    SL.create_skill(skill_name, "test skill", "code",
                    parameters={"type": "object", "properties": {}},
                    tool_code="def run(**kwargs): return 'executed'",
                    skills_dir=str(tmp_path / "skills"))

    # แน่ใจว่า skill นี้ยังไม่ confirm
    SL.reset_confirmed_skills()

    used_tools: list = []
    proposals: list = []
    result = server._handle_tool_call(
        skill_name, {}, used_tools, proposals,
        skill_names={skill_name}  # บอกว่านี่คือ skill
    )
    # ต้องมี proposal ประเภท skill_confirm
    assert any(p.get("type") == "skill_confirm" for p in proposals), (
        f"proposals={proposals!r} — ต้องมี skill_confirm proposal"
    )


def test_skill_confirm_runs_after_confirmed(tmp_path, monkeypatch):
    """skill ที่ confirm แล้ว → รันได้ทันที ไม่มี skill_confirm proposal."""
    _stub_optional_deps()
    server = importlib.import_module("server")
    import skills_loader as SL

    skill_name = "test_d1_confirmed"
    SL.create_skill(skill_name, "test confirmed skill", "code",
                    parameters={"type": "object", "properties": {}},
                    tool_code="def run(**kwargs): return 'done'",
                    skills_dir=str(tmp_path / "skills"))

    # mark as confirmed
    SL.confirm_skill(skill_name)

    used_tools: list = []
    proposals: list = []
    # ถ้า skill ไม่ได้โหลดจริงใน TOOLS → _handle_tool_call จะเรียก _run และได้ผลอะไรก็ได้
    # แต่สิ่งสำคัญคือต้องไม่มี skill_confirm proposal
    server._handle_tool_call(
        skill_name, {}, used_tools, proposals,
        skill_names={skill_name}
    )
    assert not any(p.get("type") == "skill_confirm" for p in proposals), (
        "skill ที่ confirm แล้วต้องไม่มี skill_confirm proposal"
    )



# --------------------------------------------------------------------------
# 14) SEC-1: import-agent-folder ต้องปฏิเสธ root ไดรฟ์/โฟลเดอร์ระบบเหมือน endpoint อื่น
# --------------------------------------------------------------------------
def test_import_agent_folder_rejects_blocked_root(monkeypatch):
    """เดิม _route_import_agent_folder ไม่เช็ค _is_blocked_root เลย ต่างจาก
    _route_audio_scan/collect_files ที่กันไว้ — ทำให้ os.walk ทั้ง root ไดรฟ์ได้
    (อ่านไฟล์ทุกไฟล์ <50KB ฝังเป็น system prompt agent ใหม่ — เสี่ยง data exfiltration)."""
    _stub_optional_deps()
    server = importlib.import_module("server")
    h = object.__new__(server.Handler)

    sent = {}

    def fake_send(code, body, ctype="application/json; charset=utf-8"):
        sent["code"] = code
        sent["body"] = body if isinstance(body, str) else body.decode("utf-8")

    h._send = fake_send
    blocked_root = os.path.abspath(os.sep)  # "/" หรือ "C:\\" แล้วแต่ระบบปฏิบัติการ
    h._route_import_agent_folder({"path": blocked_root})
    assert sent.get("code") == 200
    body = json.loads(sent["body"])
    assert body.get("ok") is False
    assert "ปฏิเสธ" in body.get("message", "")


# --------------------------------------------------------------------------
# 15) SEC-4: install_ffmpeg (เปลี่ยนแปลงระบบ) ต้องผ่านการยืนยันก่อนรัน เหมือน skill ใหม่
# --------------------------------------------------------------------------
def test_install_ffmpeg_requires_confirmation():
    """install_ffmpeg เดิมรันทันทีไม่ต้องยืนยัน (ต่างจาก write_file/skill ใหม่) —
    ตอนนี้ต้องอยู่ใน CONFIRM_TOOLS และคืน proposal skill_confirm ก่อนรันจริง."""
    _stub_optional_deps()
    server = importlib.import_module("server")
    import skills_loader as SL

    assert "install_ffmpeg" in server.CONFIRM_TOOLS
    SL.reset_confirmed_skills()

    used_tools: list = []
    proposals: list = []
    result = server._handle_tool_call("install_ffmpeg", {}, used_tools, proposals)
    assert any(p.get("type") == "skill_confirm" and p.get("name") == "install_ffmpeg"
               for p in proposals), f"proposals={proposals!r} — ต้องรอยืนยันก่อน"
    assert "ยืนยัน" in result


def test_install_ffmpeg_runs_after_confirmed(monkeypatch):
    """หลัง confirm แล้ว install_ffmpeg ต้องรันได้จริง (ไม่มี skill_confirm proposal อีก)."""
    _stub_optional_deps()
    server = importlib.import_module("server")
    import skills_loader as SL
    import tools as T

    SL.confirm_skill("install_ffmpeg")
    # T.TOOLS["install_ffmpeg"] คือ reference ที่ T.run_tool() ใช้จริง (ผูกไว้ตอน import
    # โมดูล) — monkeypatch T.install_ffmpeg เฉยๆ ไม่มีผล ต้องแก้ที่ TOOLS dict โดยตรง
    monkeypatch.setitem(T.TOOLS, "install_ffmpeg", lambda: "ok ✅")

    used_tools: list = []
    proposals: list = []
    result = server._handle_tool_call("install_ffmpeg", {}, used_tools, proposals)
    assert not any(p.get("type") == "skill_confirm" for p in proposals)
    assert "ok" in result



# --------------------------------------------------------------------------
# 16) SEC-5: fetch_url ต้อง pin IP ที่ validate แล้ว แล้วดึงข้อมูลได้จริงผ่าน
#     pinned connection (ไม่ใช่แค่ mock — รันเซิร์ฟเวอร์ HTTP จริงบน localhost)
# --------------------------------------------------------------------------
def test_fetch_url_pinned_connection_roundtrip(monkeypatch):
    """จำลอง DNS-rebinding scenario: host ชื่ออะไรก็ได้แต่ผูก (pin) ไปที่ IP เดียวที่
    validate ไว้แล้วจริง ๆ — ทดสอบ round-trip เต็มผ่านเซิร์ฟเวอร์ HTTP จริง ไม่ใช่ mock
    เพื่อพิสูจน์ว่า _PinnedHTTPConnection/_PinnedHTTPHandler ทำงานได้จริง."""
    _stub_optional_deps()
    import tools as T
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b"hello from pinned test server"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        # ใช้ 127.0.0.1 ตรงๆ (ไม่ใช่โดเมนสมมติ) กัน CI/sandbox ที่มี HTTP(S)_PROXY
        # ตั้งไว้ ยิงคำขอไปที่ egress proxy แทนที่จะเป็น local server ตรงๆ — 127.0.0.1
        # มักอยู่ใน no_proxy เสมอ จึงต่อตรงและพิสูจน์ pinned-connection path ได้จริง
        monkeypatch.setattr(T, "_is_public_host", lambda host: True)
        monkeypatch.setattr(T, "_resolve_public_ip", lambda host: "127.0.0.1")
        out = T.fetch_url(f"http://127.0.0.1:{port}/")
        assert "hello from pinned test server" in out
    finally:
        srv.shutdown()
        t.join(timeout=5)


def test_pinned_ip_resolver_caches_per_host():
    """_PinnedIPResolver.pin() ต้อง resolve ครั้งเดียวต่อ host แล้ว cache ไว้ใช้ซ้ำ
    (ไม่ resolve DNS ใหม่ทุกครั้ง — คือหัวใจของการปิดช่อง DNS-rebinding TOCTOU)."""
    _stub_optional_deps()
    import tools as T

    calls = []

    def fake_resolve(host):
        calls.append(host)
        return "203.0.113.5"  # TEST-NET-3 (RFC 5737) — ใช้เป็นตัวอย่างเฉยๆ

    import unittest.mock
    with unittest.mock.patch.object(T, "_resolve_public_ip", side_effect=fake_resolve):
        resolver = T._PinnedIPResolver()
        ip1 = resolver.pin("example.com")
        ip2 = resolver.pin("example.com")
        assert ip1 == ip2 == "203.0.113.5"
        assert calls == ["example.com"], f"ต้อง resolve แค่ครั้งเดียว แต่ได้ {calls!r}"


# --------------------------------------------------------------------------
# F1: Memory files (_agent.md/_memory.md) + tool remember
# --------------------------------------------------------------------------
def test_workspace_context_empty_when_no_memory_files(tmp_path):
    """ไม่มี _agent.md/_memory.md -> ต้องคืน '' (ไม่เปลืองบริบทโมเดล)."""
    _stub_optional_deps()
    import tools as T
    T.set_workspace(str(tmp_path))
    assert T.workspace_context() == ""


def test_workspace_context_reads_agent_and_memory(tmp_path):
    _stub_optional_deps()
    import tools as T
    T.set_workspace(str(tmp_path))
    (tmp_path / T.AGENT_FILE).write_text("ตอบสั้นๆ เสมอ", encoding="utf-8")
    (tmp_path / T.MEMORY_FILE).write_text("- ลูกค้าหลักคือร้าน A", encoding="utf-8")
    ctx = T.workspace_context()
    assert "ตอบสั้นๆ เสมอ" in ctx
    assert "ลูกค้าหลักคือร้าน A" in ctx
    assert T.AGENT_FILE in ctx and T.MEMORY_FILE in ctx


def test_workspace_context_caps_memory_size(tmp_path):
    """_memory.md ใหญ่เกิน cap -> ต้องตัดหัว (เก็บบันทึกล่าสุดท้ายไฟล์ไว้)."""
    _stub_optional_deps()
    import tools as T
    T.set_workspace(str(tmp_path))
    lines = [f"- บันทึกที่ {i}" for i in range(500)]
    (tmp_path / T.MEMORY_FILE).write_text("\n".join(lines), encoding="utf-8")
    ctx = T.workspace_context()
    assert len(ctx) < T.MEMORY_MAX_CHARS + 500          # ถูก cap จริง
    assert "- บันทึกที่ 499" in ctx                      # อันล่าสุดต้องรอด
    assert "- บันทึกที่ 0" not in ctx                    # อันเก่าสุดถูกตัด


def test_remember_is_write_tool_and_becomes_proposal(tmp_path):
    """remember ต้องอยู่ใน WRITE_TOOLS และถูกดักเป็น proposal (ไม่เขียนทันที)."""
    _stub_optional_deps()
    import tools as T
    server = importlib.import_module("server")
    T.set_workspace(str(tmp_path))
    assert "remember" in T.WRITE_TOOLS
    used, proposals = [], []
    msg = server._handle_tool_call("remember", {"text": "ชอบตอบสั้นๆ"}, used, proposals,
                                   skill_names=set())
    assert "รอผู้ใช้" in msg
    assert len(proposals) == 1
    assert proposals[0]["path"] == T.MEMORY_FILE
    assert "ชอบตอบสั้นๆ" in proposals[0]["content"]
    # ยังไม่เขียนจริงจนกว่าจะ apply
    assert not (tmp_path / T.MEMORY_FILE).exists()


def test_build_memory_content_appends_and_caps(tmp_path):
    _stub_optional_deps()
    import tools as T
    T.set_workspace(str(tmp_path))
    (tmp_path / T.MEMORY_FILE).write_text("- [2026-01-01] ของเดิม", encoding="utf-8")
    new = T.build_memory_content("ของใหม่")
    assert "ของเดิม" in new and "ของใหม่" in new
    assert new.index("ของเดิม") < new.index("ของใหม่")   # append ต่อท้าย
    assert len(T.build_memory_content("x" * 10000)) <= T.MEMORY_MAX_CHARS


def test_run_agent_injects_workspace_context(monkeypatch, tmp_path):
    """system prompt ของ run_agent ต้องมีเนื้อหา _agent.md/_memory.md ต่อท้าย."""
    _stub_optional_deps()
    import tools as T
    server = importlib.import_module("server")
    T.set_workspace(str(tmp_path))
    (tmp_path / T.AGENT_FILE).write_text("กฎพิเศษ: เรียกผู้ใช้ว่าบอส", encoding="utf-8")

    captured = {}

    def fake_chat(base_url, api_key, model, messages, tools):
        captured["system"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "ok", "tool_calls": None}}]}

    monkeypatch.setattr(server, "_openai_chat", fake_chat)
    server.run_agent("general", [{"role": "user", "content": "สวัสดี"}], "m")
    assert "กฎพิเศษ: เรียกผู้ใช้ว่าบอส" in captured["system"]


# --------------------------------------------------------------------------
# F3: init-workspace route
# --------------------------------------------------------------------------
def test_init_workspace_route_registered():
    _stub_optional_deps()
    server = importlib.import_module("server")
    assert "/api/init-workspace" in server.Handler._POST_ROUTE_TABLE


def test_init_workspace_creates_structure_without_overwrite(tmp_path):
    _stub_optional_deps()
    import tools as T
    server = importlib.import_module("server")
    T.set_workspace(str(tmp_path))
    # มี _agent.md อยู่แล้ว — ต้องไม่ถูกทับ
    (tmp_path / T.AGENT_FILE).write_text("ของเดิมห้ามหาย", encoding="utf-8")

    h = object.__new__(server.Handler)
    sent = {}
    h._send = lambda code, body, *a, **k: sent.update(code=code, body=body)
    h._route_init_workspace({})

    assert sent["code"] == 200
    data = json.loads(sent["body"])
    assert data["ok"]
    for d in ("inbox", "projects", "archive"):
        assert (tmp_path / d).is_dir()
    assert (tmp_path / T.MEMORY_FILE).is_file()
    assert (tmp_path / T.AGENT_FILE).read_text(encoding="utf-8") == "ของเดิมห้ามหาย"
    assert T.AGENT_FILE in data["skipped"]


# --------------------------------------------------------------------------
# รอบ 2: SEC-6 (MCP confirm gate) + F4 (mcp routes) + OBS-1 (file logging)
# --------------------------------------------------------------------------
def test_mcp_tool_requires_confirmation(monkeypatch):
    """SEC-6: MCP tool ต้องถูกดักเป็น skill_confirm proposal ก่อนรันครั้งแรก."""
    _stub_optional_deps()
    import tools as T
    import skills_loader as SL
    server = importlib.import_module("server")
    monkeypatch.setitem(T.mcp_manager.tool_mapping, "srv_do_thing", "srv")
    SL.reset_confirmed_skills()
    used, proposals = [], []
    msg = server._handle_tool_call("srv_do_thing", {"x": 1}, used, proposals,
                                   skill_names=set())
    assert "ยังไม่ได้รับการยืนยัน" in msg
    assert proposals and proposals[0]["type"] == "skill_confirm"
    assert proposals[0]["name"] == "srv_do_thing"
    # หลัง confirm -> รันจริง (client ไม่มีอยู่ -> ได้ error string จาก mcp_manager ไม่ใช่ proposal)
    SL.confirm_skill("srv_do_thing")
    used2, proposals2 = [], []
    out = server._handle_tool_call("srv_do_thing", {"x": 1}, used2, proposals2,
                                   skill_names=set())
    assert not proposals2
    assert "not running" in out or "Error" in out
    SL.reset_confirmed_skills()


def test_tools_import_does_not_autoload_mcp():
    """QUAL-5: import tools ต้องไม่ spawn MCP subprocess เอง (โหลดผ่าน init_mcp เท่านั้น)."""
    _stub_optional_deps()
    import tools as T
    src = open(os.path.join(str(ROOT), "tools.py"), encoding="utf-8").read()
    head = src.split("def init_mcp", 1)[0]
    assert "load_config(" not in head, "ห้ามเรียก load_config ที่ module level ของ tools.py"
    assert callable(T.init_mcp) and callable(T.is_mcp_tool)


def test_mcp_routes_registered():
    _stub_optional_deps()
    server = importlib.import_module("server")
    table = server.Handler._POST_ROUTE_TABLE
    for r in ("/api/mcp-status", "/api/mcp-reload", "/api/mcp-save", "/api/mcp-delete"):
        assert r in table, f"missing {r}"
        assert hasattr(server.Handler, table[r])


def test_mcp_save_and_delete_roundtrip(monkeypatch, tmp_path):
    """F4: save เขียน mcp.json ถูกโครงสร้าง (atomic) และ delete เอาออกจริง."""
    _stub_optional_deps()
    import tools as T
    server = importlib.import_module("server")
    cfg_path = str(tmp_path / "mcp.json")
    monkeypatch.setattr(T, "MCP_CONFIG_PATH", cfg_path)
    monkeypatch.setattr(T, "init_mcp", lambda: 0)   # อย่า spawn จริงใน test

    h = object.__new__(server.Handler)
    sent = {}
    h._send = lambda code, body, *a, **k: sent.update(code=code, body=body)

    h._route_mcp_save({"id": "my tool!", "command": "npx",
                       "args": "-y some-mcp-server"})
    data = json.loads(sent["body"])
    assert data["ok"] and data["id"] == "my_tool"   # id ถูก slug
    cfg = json.loads(open(cfg_path, encoding="utf-8").read())
    assert cfg["mcpServers"]["my_tool"]["command"] == "npx"
    assert cfg["mcpServers"]["my_tool"]["args"] == ["-y", "some-mcp-server"]

    h._route_mcp_delete({"id": "my_tool"})
    assert json.loads(sent["body"])["ok"]
    cfg = json.loads(open(cfg_path, encoding="utf-8").read())
    assert "my_tool" not in cfg["mcpServers"]

    # ลบซ้ำ -> ok=False ไม่ระเบิด
    h._route_mcp_delete({"id": "my_tool"})
    assert not json.loads(sent["body"])["ok"]


def test_mcp_save_rejects_missing_fields(monkeypatch, tmp_path):
    _stub_optional_deps()
    import tools as T
    server = importlib.import_module("server")
    monkeypatch.setattr(T, "MCP_CONFIG_PATH", str(tmp_path / "mcp.json"))
    monkeypatch.setattr(T, "init_mcp", lambda: 0)
    h = object.__new__(server.Handler)
    sent = {}
    h._send = lambda code, body, *a, **k: sent.update(code=code, body=body)
    h._route_mcp_save({"id": "", "command": ""})
    assert not json.loads(sent["body"])["ok"]
    assert not (tmp_path / "mcp.json").exists()


def test_file_logging_writes_to_data_dir(monkeypatch, tmp_path):
    """OBS-1: _setup_file_logging ต้องสร้าง data/app.log แล้ว log ไหลลงไฟล์จริง."""
    _stub_optional_deps()
    import logging as _logging
    import data_store as DS
    server = importlib.import_module("server")
    monkeypatch.setattr(DS, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "_FILE_LOG_READY", False)
    server._setup_file_logging()
    log_path = tmp_path / "app.log"
    _logging.getLogger("obs1test").warning("ทดสอบ log ลงไฟล์ 🤖")
    # หา handler ที่เพิ่งเพิ่ม flush แล้วถอดออก (กัน handler รั่วไป test อื่น)
    root = _logging.getLogger()
    added = [h for h in root.handlers
             if getattr(h, "baseFilename", "") == str(log_path)]
    assert added, "ไม่พบ RotatingFileHandler ที่ชี้ไป app.log"
    for h in added:
        h.flush()
        root.removeHandler(h)
        h.close()
    assert log_path.is_file()
    assert "ทดสอบ log ลงไฟล์" in log_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# รอบ 3: F5 (Scheduled tasks)
# --------------------------------------------------------------------------
def test_schedules_key_allowed_in_data_store():
    _stub_optional_deps()
    import data_store as DS
    assert "schedules" in DS.ALLOWED


def test_sched_due_logic():
    """_sched_due: เช็ค enabled / เวลา / วัน / last_run ครบทุกกิ่ง."""
    _stub_optional_deps()
    import datetime as dt
    server = importlib.import_module("server")
    # อังคาร 2026-07-07 เวลา 09:00 (weekday=1)
    now = dt.datetime(2026, 7, 7, 9, 0)
    base = {"time": "08:30", "prompt": "x", "enabled": True}
    assert server._sched_due(dict(base), now)                          # เลยเวลาแล้ว -> รัน
    assert not server._sched_due(dict(base, time="09:30"), now)        # ยังไม่ถึงเวลา
    assert not server._sched_due(dict(base, enabled=False), now)       # ปิดอยู่
    assert not server._sched_due(dict(base, last_run="2026-07-07"), now)  # รันวันนี้แล้ว
    assert server._sched_due(dict(base, last_run="2026-07-06"), now)   # รันเมื่อวาน -> รันได้
    assert not server._sched_due(dict(base, days=[0, 2]), now)         # วันนี้ (อังคาร=1) ไม่อยู่ใน days
    assert server._sched_due(dict(base, days=[1]), now)                # วันตรง
    assert not server._sched_due(dict(base, time="เช้าๆ"), now)         # เวลาผิดรูปแบบ
    assert not server._sched_due(dict(base, time=""), now)


def test_run_schedule_writes_report(monkeypatch, tmp_path):
    """_run_schedule ต้องเขียนรายงานลง reports/ และแจ้งเตือนเมื่อมี proposal ที่ไม่ได้เขียน."""
    _stub_optional_deps()
    import tools as T
    server = importlib.import_module("server")

    def fake_run_agent(agent_key, history, model, cowork=False, provider=None):
        assert history[0]["content"] == "สรุปหน่อย"
        return {"reply": "นี่คือสรุปประจำวัน", "tools": [],
                "proposals": [{"path": "x.txt", "content": "y"}]}

    monkeypatch.setattr(server, "run_agent", fake_run_agent)
    s = {"id": "s1", "title": "งานเช้า", "time": "08:00", "prompt": "สรุปหน่อย",
         "workspace": str(tmp_path)}
    path = server._run_schedule(s)
    assert path.startswith("reports/")
    full = tmp_path / path
    assert full.is_file()
    text = full.read_text(encoding="utf-8")
    assert "นี่คือสรุปประจำวัน" in text
    assert "ไม่เขียนไฟล์ให้" in text          # เตือนเรื่อง proposal ค้าง


def test_schedule_run_route_registered():
    _stub_optional_deps()
    server = importlib.import_module("server")
    table = server.Handler._POST_ROUTE_TABLE
    assert "/api/schedule-run" in table
    assert hasattr(server.Handler, table["/api/schedule-run"])


def test_schedule_run_route_unknown_id(monkeypatch):
    _stub_optional_deps()
    import data_store as DS
    server = importlib.import_module("server")
    monkeypatch.setattr(DS, "load", lambda key, default=None: [])
    h = object.__new__(server.Handler)
    sent = {}
    h._send = lambda code, body, *a, **k: sent.update(code=code, body=body)
    h._route_schedule_run({"id": "no_such"})
    assert not json.loads(sent["body"])["ok"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
