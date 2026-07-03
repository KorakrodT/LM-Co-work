"""
agent_store.py — ระบบจัดการ Agent (built-in + ที่ผู้ใช้สร้าง/แก้เอง)

- agent ในตัวมาจาก agents.py (BUILTIN)
- ผู้ใช้แก้ทับ (override) agent ในตัว หรือสร้างใหม่ได้ เก็บถาวรเป็น JSON ข้างโปรแกรม
  (agents_user.json) — รันแบบ .exe ก็วางไฟล์นี้ข้าง ๆ ได้
- server ใช้ all_agents() เป็นรายชื่อ agent ที่มีผลจริง (merged)

ฟิลด์ของ agent:
  title            ชื่อแสดงในเมนู
  description      คำอธิบายสั้น
  system           system prompt
  tools            None = ทุกเครื่องมือ, [] = ไม่ใช้, [list] = เฉพาะที่ระบุ
  skill_categories (ออปชัน) จำกัดหมวด skill ที่ใช้ได้ (เว้น = ทุกหมวด)
"""

from __future__ import annotations

import json
import os
import re
import sys

from agents import AGENTS as BUILTIN, DEFAULT_AGENT

if getattr(sys, "frozen", False):
    _BASE = os.path.dirname(sys.executable)
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_BASE, "data")
STORE_PATH = os.path.join(DATA_DIR, "agents.json")
_OLD_PATH = os.path.join(_BASE, "agents_user.json")  # ของเดิม ก่อนย้ายเข้า data/

# เครื่องมือพื้นฐานที่ให้เลือกในฟอร์ม (ตรงกับ tools.py)
AVAILABLE_TOOLS = [
    "calculator", "get_current_time", "list_files",
    "read_file", "write_file", "fetch_url", "remember",
]

_FIELDS = ("title", "description", "system", "tools", "skill_categories")


def _slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", (name or "").strip()).strip("_").lower()


def _load() -> dict:
    path = STORE_PATH if os.path.isfile(STORE_PATH) else _OLD_PATH
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save(d: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STORE_PATH)


def _clean(data: dict) -> dict:
    """กรอง/normalize ฟิลด์ก่อนบันทึก."""
    out = {}
    if "title" in data:
        out["title"] = str(data.get("title") or "").strip()
    if "description" in data:
        out["description"] = str(data.get("description") or "").strip()
    if "system" in data:
        out["system"] = str(data.get("system") or "").strip()
    if "tools" in data:
        t = data.get("tools")
        if t is None:
            out["tools"] = None
        elif isinstance(t, list):
            out["tools"] = [str(x) for x in t if x]
        else:
            out["tools"] = None
    if "skill_categories" in data:
        sc = data.get("skill_categories")
        if isinstance(sc, list):
            out["skill_categories"] = [str(x).strip() for x in sc if str(x).strip()]
        else:
            out["skill_categories"] = None
    return out


def all_agents() -> dict:
    """รวม built-in + override/custom -> dict ของ agent ที่มีผลจริง."""
    merged: dict = {}
    for k, v in BUILTIN.items():
        a = {kk: v[kk] for kk in _FIELDS if kk in v}
        a["builtin"] = True
        a["edited"] = False
        merged[k] = a
    for k, v in _load().items():
        if k in merged:                       # override agent ในตัว
            for kk in _FIELDS:
                if kk in v:
                    merged[k][kk] = v[kk]
            merged[k]["edited"] = True
        else:                                 # agent ที่ผู้ใช้สร้างเอง
            a = {kk: v.get(kk) for kk in _FIELDS if kk in v}
            a["builtin"] = False
            a["edited"] = False
            merged[k] = a
    return merged


def get_agent(key: str) -> dict | None:
    return all_agents().get(key)


def list_agents() -> list:
    """ข้อมูลย่อสำหรับ UI."""
    out = []
    for k, a in all_agents().items():
        out.append({
            "key": k,
            "title": a.get("title", k),
            "description": a.get("description", ""),
            "builtin": a.get("builtin", False),
            "edited": a.get("edited", False),
        })
    return out


def save_agent(key: str, data: dict, is_new: bool = False):
    """สร้าง/แก้ agent. ถ้า is_new จะตั้ง key จาก key ที่ส่งมา (ต้องเป็นอังกฤษ)."""
    user = _load()
    if is_new:
        slug = _slug(key)
        if not slug:
            return False, "ต้องมีรหัส agent (อังกฤษ a-z, 0-9, _ , -)"
        if slug in BUILTIN or slug in user:
            return False, f"มี agent รหัส '{slug}' อยู่แล้ว"
        key = slug
    elif not key:
        return False, "ไม่ระบุ agent ที่จะแก้"
    if not str(data.get("system", "")).strip():
        return False, "ต้องมี system prompt"
    if not str(data.get("title", "")).strip():
        return False, "ต้องมีชื่อแสดง (title)"
    user[key] = _clean(data)
    _save(user)
    return True, key


def delete_agent(key: str):
    """ลบ custom agent — หรือคืนค่า agent ในตัวที่ถูกแก้ให้เป็นค่าเริ่มต้น."""
    user = _load()
    if key in BUILTIN:
        if key in user:
            del user[key]
            _save(user)
            return True, "คืนค่า agent ในตัวเป็นค่าเริ่มต้นแล้ว"
        return False, "agent ในตัวลบไม่ได้ (ถ้าแก้ไว้ กด 'คืนค่า' เพื่อรีเซ็ต)"
    if key in user:
        del user[key]
        _save(user)
        return True, "ลบ agent แล้ว"
    return False, "ไม่พบ agent"


def default_agent() -> str:
    return DEFAULT_AGENT
