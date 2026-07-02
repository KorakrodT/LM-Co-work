"""
data_store.py — ที่เก็บข้อมูลส่วนกลางของแอป (โฟลเดอร์ data/ ข้างโปรแกรม)

เก็บเป็นไฟล์ JSON ต่อชนิดข้อมูล: data/chats.json, data/projects.json, data/settings.json
- whitelist key (กัน path traversal / เขียนไฟล์มั่ว)
- รันแบบ .exe ก็จะสร้าง data/ ข้างไฟล์ .exe
"""

from __future__ import annotations

import json
import os
import sys

if getattr(sys, "frozen", False):
    _BASE = os.path.dirname(sys.executable)
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(_BASE, "data")

# คีย์ที่อนุญาตให้บันทึก/อ่านผ่าน endpoint เท่านั้น
ALLOWED = {"chats", "projects", "settings", "schedules"}  # F5: งานตามเวลา


def _path(key: str) -> str:
    return os.path.join(DATA_DIR, key + ".json")


def ensure() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def load(key: str, default=None):
    if key not in ALLOWED:
        return default
    p = _path(key)
    if not os.path.isfile(p):
        return default
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def save(key: str, data) -> bool:
    if key not in ALLOWED:
        return False
    ensure()
    tmp = _path(key) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _path(key))
    return True
