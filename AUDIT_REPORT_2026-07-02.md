# รายงานตรวจสอบโปรเจกต์ LM Co-work — 2 กรกฎาคม 2026

ตรวจใหม่ทั้งโปรเจกต์ครบทุกด้าน: **ความปลอดภัย / คุณภาพโค้ด+สถาปัตยกรรม / ประสิทธิภาพ+observability / เทสต์+CI / เอกสาร+repo**

วิธีตรวจ: อ่านโค้ดจริงทุกไฟล์ (`server.py`, `tools.py`, `skills_loader.py`, `mcp_client.py`,
`agent_store.py`, `data_store.py`, `knowledge_store.py`, `winproc.py`, `index.html`,
`tests/test_smoke.py`, `.github/workflows/ci.yml`) + **รันเครื่องมือจริง**: `pytest` (41 ผ่านครบ),
`ruff --select F` (ผ่าน), `ruff --select F,E9,B,S` (เหลือ 19 จุด — ดูหัวข้อเครื่องมือ)
และตรวจซ้ำทุกข้อใน `AUDIT_REPORT_2026-07-01.md` ว่าแก้จริงหรือไม่

**นี่คือรายงาน — ยังไม่ได้แก้ไขโค้ดใด ๆ** เลือกรหัสข้อที่ต้องการให้ลงมือแก้ได้เลย

---

## 1) ผลตรวจซ้ำข้อค้นพบรอบ 1 ก.ค. — เกือบทั้งหมดแก้แล้วจริง ✅

| รหัสเดิม | สถานะ | หลักฐานในโค้ด |
|---|---|---|
| SEC-1 (import-agent-folder ไม่กัน root) | ✅ แก้แล้ว | `server.py` เช็ค `T._is_blocked_root` ก่อน `os.walk` + test `test_import_agent_folder_rejects_blocked_root` |
| SEC-2 (esc ใน inline onclick) | ✅ แก้แล้ว | `renderChats`/`loadTree`/`openFile` เปลี่ยนเป็น `data-*` + `addEventListener` (index.html ~638–677) |
| SEC-3 (skill confirm แล้วไม่ถามซ้ำ) | ✅ บรรเทาตามแนะนำ | `_handle_tool_call` log ชื่อ+args **ทุกครั้ง**ที่รัน เป็น audit trail |
| SEC-4 (install_ffmpeg รันทันที) | ✅ แก้แล้ว | `CONFIRM_TOOLS` ใช้ flow ยืนยันเดียวกับ skill + 2 tests |
| SEC-5 (DNS rebinding — เคยบอกไม่ต้องแก้) | ✅ แก้เกินคาด | `_PinnedIPResolver` + `_PinnedHTTP(S)Connection` pin IP ที่ validate แล้ว + tests |
| QUAL-1 (โค้ดซ้ำ) | 🟡 แก้ครึ่งเดียว | `_no_window_kwargs` → `winproc.py` ✅ แต่ `_route_chat_stream` ยังซ้ำ logic สร้าง context กับ `run_agent` ❌ (ดู QUAL-6) |
| QUAL-2 (ไฟล์ขยะที่ root) | ✅ ลบแล้ว | ไม่พบไฟล์ ZIP ชื่อสุ่ม/.bak ค้างที่ root อีก |
| QUAL-3 (except-pass ไม่มี log) | ✅ เกือบครบ | เติม `_log.debug(..., exc_info=True)` แล้วเกือบทุกจุด — เหลือ `winproc.py` จุดเดียว (ดู QUAL-9) |
| QUAL-4 (null byte ใน md()) | ✅ แก้แล้ว | เปลี่ยนเป็น `⁣CB` (invisible separator) |
| TEST-1 (ไม่มี test import-agent-folder) | ✅ เพิ่มแล้ว | test ดังกล่าวข้างบน |
| TEST-2 (CI ไม่มี audit/lint) | ✅ เพิ่มแล้ว | CI มี `pip-audit` + `ruff --select F` + `compileall` + `pytest` ครบ 4 ขั้น |
| TEST-3 (ไม่มี test ฝั่ง JS) | 🟡 บรรเทา | มี manual QA checklist ใน `AGENTS.md` — ยังไม่มี automated test |
| DOC-1 (requirements ขาด dep) | ✅ แก้แล้ว | เพิ่ม `pystray`/`keyboard`/`Pillow` พร้อมคอมเมนต์อธิบาย |

ภาพรวม: ทีมงาน (คุณ) ปิดงานรอบก่อนได้จริงและมี test รองรับเกือบทุกข้อ — คุณภาพการแก้อยู่ในเกณฑ์ดีมาก

---

## 2) ผลรันเครื่องมือรอบนี้

- `pytest tests/ -q` → **41 passed** (0.97s)
- `ruff --select F` (กฎที่ CI ใช้) → **ผ่านหมด**
- `ruff --select F,E9,B,S` (เข้มกว่า CI) → 19 จุด: `S110`×6 (try-except-pass), `S603`×5
  (subprocess — ทุกจุดเป็น list args ไม่ใช้ shell, รับได้), `S310`×4 (urlopen — มี SSRF guard
  ครอบแล้ว, รับได้), `B007`×2, `B905`×1, `S606`×1 (`os.startfile` เปิด LM Studio — ตั้งใจ)
  → มีนัยจริงแค่ `S110` ใน `winproc.py` (QUAL-9)

---

## 3) ข้อค้นพบใหม่รอบนี้

### 🔒 ความปลอดภัย

#### SEC-6 🟡 กลาง — MCP tools ไม่ผ่านกลไกยืนยันใด ๆ
**ไฟล์:** `tools.py` → `run_tool()` (บรรทัด ~862), `server.py` → `_handle_tool_call`

Tool จาก MCP server ภายนอกถูกเรียกผ่าน `mcp_manager.call_tool` **ทันที** — ไม่เข้า flow
ยืนยันแบบ skill ใหม่ (D1) หรือ `CONFIRM_TOOLS` (SEC-4) ทั้งที่ MCP tool คือโค้ดภายนอก
ที่ทำอะไรก็ได้ (เขียนไฟล์นอก workspace, ยิงเน็ต ฯลฯ) ตอนนี้ยัง **latent** เพราะไม่มี
`mcp.json` ในเครื่อง แต่ทันทีที่ผู้ใช้เพิ่ม MCP server ช่องนี้จะเปิด

**แนะนำ:** ให้ MCP tools ต้องยืนยันครั้งแรกเหมือน skill (ชื่อ tool มี prefix `serverid_`
อยู่แล้ว เอาไปเช็คใน `_handle_tool_call` ได้เลย) หรืออย่างน้อย log ทุกครั้งแบบ SEC-3

#### SEC-7 ⚪ ต่ำ — race ของ global state `WORKSPACE`/`_COWORK`
**ไฟล์:** `tools.py` (module globals), `server.py` (ThreadingHTTPServer)

ทุก request ที่ส่ง `workspace` มา จะ `T.set_workspace()` ทับ global ตัวเดียวกัน ขณะที่
server เป็นแบบ multi-thread — ถ้ามี 2 request คนละ workspace วิ่งพร้อมกัน (เช่น แชต
ค้างอยู่ + เปิดแท็บ Files) path jail อาจชี้ผิดโฟลเดอร์ชั่วขณะ. แอป local ผู้ใช้คนเดียว
ความเสี่ยงต่ำ — บันทึกไว้ให้รู้ ถ้าจะแก้ให้ส่ง workspace เป็นพารามิเตอร์ต่อ-request
แทน global

### 🧹 คุณภาพโค้ด / ความทนทาน

#### QUAL-5 🟡 กลาง — `mcp_client.py` มีจุดเปราะ 3 จุด
1. **stderr deadlock:** `Popen(..., stderr=PIPE)` แต่ไม่มีใครอ่าน pipe นี้เลย — MCP server
   ที่พ่น log ทาง stderr มาก ๆ จะเต็ม buffer แล้วค้างทั้งโปรเซส (bug คลาสสิก)
   → อ่านทิ้งใน thread แยก หรือใช้ `stderr=DEVNULL`
2. **response รั่วเมื่อ timeout:** `send_request` ตอน timeout ลบแค่ `response_events`
   แต่ถ้า response มาช้ากว่านั้น `_read_loop` จะยัดเข้า `self.responses` ค้างตลอดชีพ
3. **side effect ตอน import:** `tools.py` เรียก `mcp_manager.load_config()` ที่
   module level — import `tools` = สตาร์ต MCP subprocess ทันที (บล็อกได้ 10s+/server
   และเกิดใน pytest ด้วยถ้ามี `mcp.json`) → ย้ายไปเรียกจาก `main()` จะคุมง่ายกว่า

#### QUAL-6 🟡 กลาง — `/api/chat-stream` + `provider` เป็นโค้ดที่ UI ไม่ได้ใช้เลย
**ไฟล์:** `server.py` (`_route_chat_stream`, `_openai_chat_stream`), `index.html`

grep ทั้ง `index.html` ไม่พบการเรียก `chat-stream` หรือการส่ง `provider` แม้แต่ครั้งเดียว
— ฟีเจอร์ B1 (streaming) สร้างฝั่ง backend เสร็จแต่ฝั่ง UI ยังใช้ `/api/chat` แบบเดิม
ผลคือ: (ก) โค้ด ~60 บรรทัดที่ต้อง maintain แต่ไม่มีผู้ใช้จริง (ข) มัน**ซ้ำ** logic สร้าง
system prompt/schemas กับ `run_agent` — คือครึ่งที่ยังไม่แก้ของ QUAL-1 เดิม ซึ่งเคยทำให้
เกิดบั๊ก ARTIFACTS_PROMPT หายมาแล้วครั้งหนึ่ง

**แนะนำ:** ตัดสินใจทางใดทางหนึ่ง — (ก) ต่อยอดให้ UI ใช้ streaming จริง แล้วดึงการสร้าง
context ออกเป็นฟังก์ชันเดียวใช้ร่วมกัน หรือ (ข) ลบ endpoint นี้ทิ้งจนกว่าจะพร้อมทำจริง

#### QUAL-7 ⚪ ต่ำ — เขียนไฟล์ไม่ atomic ใน 2 store
`data_store.save()` ทำถูกแล้ว (เขียน `.tmp` แล้ว `os.replace`) แต่ `agent_store._save()`
กับ `knowledge_store.save()` เขียนทับไฟล์ตรง ๆ — ถ้าโปรแกรม/เครื่องดับกลางคัน
`data/agents.json` หรือ `.knowledge_base.json` จะพังทั้งไฟล์ → ใช้ pattern เดียวกับ
`data_store` ให้ครบทุก store

#### QUAL-8 ⚪ ต่ำ — `run_agent` ไม่กัน JSON arguments เพี้ยนจากโมเดล
**ไฟล์:** `server.py` บรรทัด ~477: `args = ... json.loads(raw or "{}")`

โมเดล local เล็ก ๆ ส่ง arguments ที่ไม่ใช่ JSON ได้บ่อย — ตอนนี้ `json.loads` ระเบิด
→ do_POST จับได้ก็จริงแต่กลายเป็น 500 ทั้ง request แชตหลุดทั้งตา แทนที่จะส่ง
"arguments ไม่ถูกต้อง" กลับเป็น tool result ให้โมเดลลองใหม่เอง (ซึ่ง `MAX_STEPS`
รองรับอยู่แล้ว)

#### QUAL-9 ⚪ ต่ำ — เศษเล็กจาก ruff
`winproc.py` มี `except Exception: pass` ไม่มี log (ผิดกฎที่ `AGENTS.md` ตั้งเอง),
`B007` unused loop var ใน `server.py`/`skills_loader.py`, `B905` `zip()` ไม่ระบุ `strict=`

### ⚡ ประสิทธิภาพ + Observability

#### OBS-1 🟡 กลาง — build `--windowed` แล้ว log ทั้งหมดหายเงียบ
logging ตั้ง handler ไปคอนโซลอย่างเดียว แต่ .exe ไม่มีคอนโซล — แปลว่า **audit trail
ของ SEC-3, warning ของ chat-stream, traceback ของ D2 ทั้งหมดไม่มีใครเห็นเลย**
ในโหมดที่ผู้ใช้จริงใช้ → เพิ่ม `RotatingFileHandler` เขียน `data/app.log`
(จำกัดขนาด ~1MB × 3 ไฟล์) แล้ว observability กลับมาครบทั้งระบบ

#### PERF-1 ⚪ ต่ำ — tool บางตัวบล็อก request thread นานมาก
`check_audio_integrity` (timeout 600s/ไฟล์) และ `install_ffmpeg` (900s) เมื่อถูกเรียก
เป็น tool call จะบล็อก `/api/chat` ทั้ง request นานได้หลักนาที — เวอร์ชันปุ่ม UI
มี background worker + progress แล้ว (`_audio_worker`) แต่เวอร์ชัน tool call ยังบล็อก
→ ทางเลือก: ให้ tool call รายงานว่า "เริ่มสแกนแล้ว ดูผลที่แท็บ Files" แล้ว reuse worker เดิม

### 🧪 เทสต์ / 📄 เอกสาร / repo

#### TEST-4 ⚪ ต่ำ — `mcp_client.py` เป็นโมดูลเดียวที่ไม่มี test เลย
240 บรรทัด มี protocol logic (JSON-RPC, threading, timeout) — โมดูลแบบนี้พังเงียบง่าย
→ เพิ่ม smoke test ด้วย fake subprocess (echo JSON-RPC กลับ) อย่างน้อย 2–3 เคส
(initialize สำเร็จ, timeout, error response)

#### DOC-3 ⚪ ต่ำ — สุขภาพ repo
- git มีแค่ **commit เดียว** (Initial commit) และมีไฟล์แก้ค้าง uncommitted อยู่ ~14 ไฟล์
  → commit เป็นระยะ ไม่งั้นย้อนเวอร์ชัน/ไล่บั๊กด้วย git ไม่ได้เลย (ทั้งที่มี CI รออยู่แล้ว)
- `workspace/thai-official-documents.rar` (binary) ถูก track ใน git — ควรย้ายออก/ignore
- เอกสารหลัก (`README.md`, `AGENTS.md`) ตรงกับโค้ดจริง ✅ — `AGENTS.md` เขียนดีมาก
  โดยเฉพาะบันทึกบั๊ก `import time` ซ้ำ + เหตุผลที่ CI ต้องมี ruff F823

---

## 4) สรุปภาพรวม

| ระดับ | จำนวน | รหัส |
|---|---|---|
| 🔴 สูง | 0 | — |
| 🟡 กลาง | 4 | SEC-6, QUAL-5, QUAL-6, OBS-1 |
| ⚪ ต่ำ/ข้อมูล | 8 | SEC-7, QUAL-7, QUAL-8, QUAL-9, PERF-1, TEST-4, DOC-3, TEST-3(คงเดิม) |

จุดแข็งรอบนี้: ข้อค้นพบระดับสูงของรอบก่อน**ถูกปิดหมดและมี test รองรับจริง** ทำให้รอบนี้
ไม่พบช่องโหว่ระดับสูงใหม่เลย ประเด็นที่เหลือเป็นการเก็บความทนทาน (mcp_client, atomic
write, arguments เพี้ยน) และ observability (log file) เป็นหลัก

**ลำดับที่แนะนำถ้าจะแก้:** OBS-1 → QUAL-5 → SEC-6 → QUAL-8 → ที่เหลือตามสะดวก
(บอกรหัสได้เลย เช่น "แก้ OBS-1 กับ QUAL-5")
