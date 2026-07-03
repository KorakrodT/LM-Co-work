"""winproc.py — ยูทิลิตี้ subprocess เฉพาะ Windows ที่ใช้ร่วมกันระหว่าง server.py และ tools.py.

QUAL-1: เดิม `_no_window_kwargs()` ถูกนิยามซ้ำเป๊ะทั้งสองไฟล์ (เสี่ยงแก้ที่เดียวไม่ครบ)
รวมมาไว้ที่เดียวตรงนี้แทน
"""

from __future__ import annotations

import logging
import os
import subprocess

_log = logging.getLogger(__name__)


def no_window_kwargs(detached: bool = False) -> dict:
    """kwargs ให้ subprocess ไม่เปิดหน้าต่าง console บน Windows (กัน terminal เด้ง).

    detached=True  : ใช้กับ Popen แบบ fire-and-forget (เช่นสตาร์ต `lms server start`
                      เป็น background process ที่ไม่รอผล) — เพิ่ม DETACHED_PROCESS
                      ให้แยกจาก process หลักจริงๆ
    detached=False : (ดีฟอลต์) ใช้กับ subprocess.run ที่ยัง capture_output ผ่าน pipe
                      อยู่ (เช่น ffmpeg/winget) — ไม่ใส่ DETACHED_PROCESS เพราะอาจ
                      รบกวนการรับ stdout/stderr ผ่าน pipe บน Windows
    """
    kw: dict = {}
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if detached:
            flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        kw["creationflags"] = flags
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0  # SW_HIDE
            kw["startupinfo"] = si
        except Exception:  # noqa: BLE001
            _log.debug("STARTUPINFO unavailable", exc_info=True)
    return kw
