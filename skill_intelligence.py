"""skill_intelligence.py — บันทึกการใช้ skill เป็น decision trail + ป้อนประสบการณ์กลับให้โมเดล

แนวคิดจาก Semantica Decision Intelligence (MIT): ทุกครั้งที่ agent เรียกใช้ skill
จะถูกบันทึกเป็น "การตัดสินใจ" ที่ตรวจสอบย้อนหลังได้ (audit trail) และสถิติ
ความสำเร็จ/ล้มเหลวจะถูกสรุปใส่ system prompt เพื่อให้โมเดลเลือกใช้ skill
ได้ฉลาดขึ้นในรอบถัดไป

ใช้เฉพาะ standard library — ไม่เพิ่ม dependency ให้แอป
เก็บข้อมูลแบบ append-only ที่ data/skill_decisions.jsonl (ไม่เขียนทับประวัติ)
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.path.join(_BASE_DIR, "data", "skill_decisions.jsonl")

_lock = threading.Lock()
_current_task: str = ""  # ข้อความล่าสุดของผู้ใช้ — ตั้งโดย server ก่อนเริ่มลูป agent

# จำกัดจำนวนรายการที่โหลดมาคิดสถิติ (กันไฟล์โตแล้วช้า)
_MAX_LOADED = 2000


# ---------------------------------------------------------------------------
# บันทึก
# ---------------------------------------------------------------------------

def set_current_task(text: str) -> None:
    """ให้ server เรียกก่อนเริ่มลูป agent เพื่อผูกการใช้ skill กับงานของผู้ใช้."""
    global _current_task
    _current_task = (text or "")[:500]


def record_use(name: str, ok: bool, error: str = "", duration: float = 0.0,
               kind: str = "skill") -> None:
    """บันทึกการเรียกใช้ skill/เครื่องมือหนึ่งครั้ง (เงียบเสมอ — ห้ามพังงานหลัก)."""
    try:
        entry = {
            "id": uuid.uuid4().hex[:12],
            "ts": time.time(),
            "task": _current_task,
            "skill": name,
            "kind": kind,  # skill | prompt_skill | mcp
            "ok": bool(ok),
            "error": (error or "")[:300],
            "duration": round(float(duration), 2),
        }
        line = json.dumps(entry, ensure_ascii=False)
        with _lock:
            os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
            with open(STORE_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# อ่าน + สถิติ
# ---------------------------------------------------------------------------

def _load(limit: int = _MAX_LOADED) -> list[dict]:
    try:
        with open(STORE_PATH, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def stats() -> dict[str, dict]:
    """สถิติต่อ skill: จำนวนครั้ง สำเร็จ ล้มเหลว เวลาเฉลี่ย และ error ล่าสุด."""
    agg: dict[str, dict] = {}
    for e in _load():
        name = e.get("skill", "")
        if not name:
            continue
        s = agg.setdefault(name, {"runs": 0, "ok": 0, "fail": 0,
                                  "total_time": 0.0, "last_error": ""})
        s["runs"] += 1
        if e.get("ok"):
            s["ok"] += 1
            s["total_time"] += e.get("duration", 0.0)
        else:
            s["fail"] += 1
            if e.get("error"):
                s["last_error"] = e["error"]
    return agg


# ---------------------------------------------------------------------------
# ค้นหางานที่คล้ายกัน (รองรับภาษาไทยด้วย character trigram)
# ---------------------------------------------------------------------------

_LATIN_RE = re.compile(r"[a-zA-Z0-9]+")
_THAI_RE = re.compile(r"[ก-๙]+")


def _tokens(text: str) -> set[str]:
    toks = {t.lower() for t in _LATIN_RE.findall(text)}
    # ภาษาไทยไม่มีช่องว่างคั่นคำ — ใช้ trigram ของตัวอักษรแทน
    for run in _THAI_RE.findall(text):
        toks.update(run[i:i + 3] for i in range(max(len(run) - 2, 1)))
    return toks


def _similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def find_precedents(task: str, max_results: int = 3) -> list[dict]:
    """รายการการใช้ skill ในงานที่คล้ายกับ task นี้ (คะแนนมาก่อน)."""
    now = time.time()
    scored = []
    for e in _load():
        if not e.get("task"):
            continue
        sim = _similarity(task, e["task"])
        if sim < 0.08:
            continue
        recency = 0.5 ** (max(0.0, now - e.get("ts", now)) / (30 * 86400))
        success = 1.0 if e.get("ok") else 0.3
        scored.append((0.6 * sim + 0.15 * recency + 0.25 * success * sim, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:max_results]]


# ---------------------------------------------------------------------------
# สรุปประสบการณ์ใส่ system prompt
# ---------------------------------------------------------------------------

def experience_context(task: str = "", max_skills: int = 8) -> str:
    """บล็อกข้อความภาษาไทยสรุปสถิติ+งานคล้ายกัน สำหรับต่อท้าย system prompt.

    คืนสตริงว่างถ้ายังไม่มีประวัติ — จะได้ไม่เปลืองบริบทโมเดล
    """
    agg = stats()
    finished = {k: v for k, v in agg.items() if v["ok"] + v["fail"] > 0}
    if not finished:
        return ""

    lines = ["[ประสบการณ์การใช้ Skills จากงานก่อนหน้า]"]

    ranked = sorted(finished.items(),
                    key=lambda kv: (kv[1]["ok"] / max(kv[1]["ok"] + kv[1]["fail"], 1),
                                    kv[1]["runs"]),
                    reverse=True)
    for name, s in ranked[:max_skills]:
        total = s["ok"] + s["fail"]
        rate = s["ok"] * 100 // total
        avg = f" เฉลี่ย {s['total_time'] / s['ok']:.0f}s" if s["ok"] and s["total_time"] else ""
        err = f" (ผิดพลาดล่าสุด: {s['last_error'][:80]})" if s["fail"] and s["last_error"] else ""
        lines.append(f"- {name}: สำเร็จ {rate}% จาก {total} ครั้ง{avg}{err}")

    if task:
        precedents = find_precedents(task)
        if precedents:
            lines.append("\nงานคล้ายกันที่เคยทำ:")
            for e in precedents:
                res = "สำเร็จ" if e.get("ok") else f"ล้มเหลว ({e.get('error', '')[:60]})"
                lines.append(f"- \"{e['task'][:80]}\" -> ใช้ {e['skill']} -> {res}")

    lines.append("\nให้พิจารณาเลือก skill ที่มีสถิติสำเร็จสูงกับงานลักษณะเดียวกัน "
                 "และระวังเป็นพิเศษกับ skill ที่เคยล้มเหลว")
    return "\n".join(lines)
