**🤝 LM Co-work**

An AI Agent that runs entirely locally on your machine via LM Studio, supporting tool calling — the model autonomously decides which tools to invoke (calculations, checking the time, reading/writing files, fetching web data, scanning for corrupt audio files), and uses the results to formulate a response. No internet connection to external APIs is required.

The app communicates with LM Studio via an OpenAI-compatible server at `http://localhost:1234/v1` and will automatically start the server in headless mode when the app is launched — there is no need to open the LM Studio window manually.

### Files in the Project

* **`server.py`**: The main program — web server + agent loop + LM Studio integration + UI window.
* **`index.html`**: Claude-style chat interface (blue theme).
* **`agents.py`**: Defines each agent's persona (add/edit agents here).
* **`tools.py`**: Tools that agents can call (add new tools here).
* **`agent_store.py`**: Saves/loads custom agents created by the user.
* **`skills_loader.py`**: System for loading skills from the `skills/` folder.
* **`data_store.py`**: Stores local settings/data (in `data/`).
* **`requirements.txt`**: Required Python libraries (`pywebview`, `pythonnet`).
* **`run-ui.bat`**: Opens the UI from the script (double-click to run).
* **`build-ui.bat` / `LM Co-work.spec**`: Builds the `dist\LM Co-work.exe` executable file.
* **`icon.ico` / `icon.png**`: Application icons.

---

### Available Agents

| Persona Name | Role | Tools Used |
| --- | --- | --- |
| **general** | 🧠 General Assistant | All tools (including corrupt audio scanner) |
| **coder** | 💻 Coding Assistant | Read/write files, calculate |
| **writer** | ✍️ Writing Assistant | Read/write files |
| **tutor** | 📚 Tutor | Calculate, time |
| **skill_builder** | 🛠️ Skill Builder | Read/write files |
| **translator** | 🌐 Translator | — |

---

### Installation

1. **Install LM Studio** — Download from [https://lmstudio.ai](https://lmstudio.ai) and launch it at least once.
2. **Download a tool-calling supported model** — In the Discover page of LM Studio, download a model like `Qwen2.5-7B-Instruct` or `Llama-3.1-8B-Instruct` (models that support tool calling work best with this app).
3. **Install the lms CLI (One-time)** — To allow the app to automatically start the headless server:
```bash
npx lmstudio install-cli

```


*(Opening LM Studio usually installs the lms CLI automatically. This step just ensures it is there.)*
4. **Install Python packages and run the program:**
```bash
py -m pip install -r requirements.txt
py server.py

```


> **⚠️ Note:** Use `py`, not `python` — as `python` on this machine points to another app's venv.



---

### No Need to Open LM Studio Manually

When opening the LM Co-work app, it will:

* Check if the LM Studio server (`localhost:1234`) is running.
* If not → Automatically execute `lms server start` in the background (headless, no console window popping up).
* The model will be automatically loaded (JIT) during the first chat interaction.
* If connection fails during chat, the system will attempt to auto-start and retry (starts only once per run to avoid duplicate processes).
* **Prerequisites:** `lms` CLI installed on the machine + at least 1 downloaded model in LM Studio.

---

### UI Version — Standalone `.exe` Window

**Build as `.exe` (Recommended):**

* Double-click `build-ui.bat` → Generates `dist\LM Co-work.exe`.
* Double-click `LM Co-work.exe` to open the actual standalone program window (you can drag a shortcut to the Desktop).
* Requires Microsoft Edge WebView2 Runtime (pre-installed on Windows 11; if missing, the program will prompt you to install it for free from Microsoft) — The `.exe` mode will **never** open a browser.

**Or run from script (also opens as a standalone window):**

* Double-click `run-ui.bat` (It will install `pywebview` and open the window).

---

### What's in the UI

* **Standalone Window** via `pywebview` (WebView2) — Not a browser tab.
* **Chat / Cowork / Code Mode Tabs** at the top (mapped to agents) in a Claude-like style.
* **Chat History / Multiple Chats** — Left sidebar stores all chats. Click "✚ New Chat" / click to switch / 🗑 delete (stored locally).
* **Projects** — Workspace folder hub. Add/search/select to set as a workspace.
* **Path Management** — 📂 button opens the actual Windows folder picker.
* **File Panel (Right Sidebar)** — 🗂 button to browse files within the workspace. Click to view contents.
* **Model Selector** in the input field (Automatically fetches the list from LM Studio).
* **Agent Selector** via dropdown.
* **Markdown Responses** (Headers, lists, bold text, code blocks + copy button).
* Press `Enter` to send / `Shift+Enter` for a new line.

---

### 🎵 Corrupt Audio File Detection

The app provides a tool for the AI to scan for "corrupt" audio files within a folder. It decodes the entire file using `ffmpeg`. If decoding results in an error (or the file is empty/unopenable), it gets reported as corrupt — catching issues even if the header looks normal but the audio data is broken halfway (e.g., incomplete downloads, bad sectors).

Simply type in the chat, e.g., *"Check corrupt music files in folder E:\music\Wav for me"* (Switch to Co-Work mode to scan folders outside the workspace).

**Related Tools:**

* `check_audio_integrity`: Scans the folder + decodes all audio files with ffmpeg and reports corrupt ones.
* `check_ffmpeg`: Checks if ffmpeg exists on the machine.
* `install_ffmpeg`: Automatically installs ffmpeg via winget (Windows) if missing.
* Supports mp3, flac, wav, m4a, aac, ogg, opus, wma, aiff, etc.
* Requires `ffmpeg` on the machine. If you don't have it, just tell the AI to *"install ffmpeg for me"*, and it will automatically run `winget install --id Gyan.FFmpeg -e`.

---

### 🧩 Skills — Create Your Own Agent Abilities

Agents have "skills" that you can create yourself. There are 2 types (or a mix of both):

1. **Code Skill**: Python functions the agent actually calls (e.g., word count, API calls, unit conversions).
2. **Prompt Skill**: Specific instructions/domain knowledge injected for the agent to follow.

**How to create:** Place 1 folder inside `skills/<name>/` containing `skill.json` (mandatory) + `tool.py` and/or `prompt.md`. See the full format and examples in `skills/README.md`.

* The UI sidebar displays a list of skills. Click the ⟳ button to reload after adding/editing a skill, or use the 🛠️ **Skill Builder** agent to help write the skill files.
* If using the `.exe` version, place the `skills/` folder next to the `.exe` file (`build-ui.bat` automatically copies it to `dist\skills`).

---

### Co-Work Features

* **Workspace Folders** — Set a folder for the AI to read/write. The AI operates exclusively within this folder for safety (Enable Co-Work mode to unlock access to any folder, like other drives).
* **Attach Files 📎** — Click the paperclip button to attach text files into the workspace, allowing the AI to read the content instantly.
* **Confirm Before Writing** — When the AI attempts to create or edit a file, it displays a preview card with ✅ **Save** / ❌ **Do Not Save** buttons. The file is written *only* when you confirm (if overwriting, the system automatically backs up the old file as `.bak`).

---

### Change / Configure Models

Can be set via environment variables:

```powershell
# Windows PowerShell
$env:LMSTUDIO_MODEL="qwen2.5-7b-instruct"; py server.py

```

| Variable | Description |
| --- | --- |
| **LMSTUDIO_BASE_URL** | LM Studio server address (Default: `http://localhost:1234/v1`) |
| **LMSTUDIO_MODEL** | The model to use (Leave blank = Auto-uses the first model loaded in LM Studio) |
| **LMSTUDIO_API_KEY** | LM Studio does not verify keys — enter anything |

*(Alternatively, you can select the model directly via the UI, which automatically fetches the list from LM Studio).*

---

### Other External Providers (Optional)

Aside from LM Studio, you can add other OpenAI-compatible providers in the "Manage Models / Provider" page, such as OpenAI, OpenRouter, Groq, vLLM — simply input the Base URL ending in `/v1` + API key.

---

### Adding Custom Tools

Open `tools.py` and do 3 things:

1. Write the function:
```python
def get_weather(city: str) -> str:
    return f"The weather in {city} is clear"  # Can connect to a real API

```


2. Add to `TOOLS`: `"get_weather": get_weather`
3. Add the schema to `TOOL_SCHEMAS` (copy an existing one and modify the name/parameters).

The agent will immediately recognize the new tool on the next run.

---

### Adding Custom Agents

Open `agents.py`, copy a block inside `AGENTS`, and modify the values, for example:

```python
"lawyer": {
    "title": "⚖️ Legal Assistant",
    "description": "Explains basic legal matters",
    "system": "You are a legal assistant. Reply in Thai...",
    "tools": ["read_file"],   # None = all tools, [] = none
},

```

Run `py server.py` again, and the new agent will instantly appear in the menu.

---

### Notes

* Everything runs locally on your machine. No data leaves your computer.
* The first time you download a model in LM Studio, it will take some time (several GBs).
* `read_file` / `write_file` are restricted to the workspace folder for security.
* **You must have a tool-calling supported model in LM Studio.** This app heavily relies on tool calling.

# 🤝 LM Co-work

AI Agent ที่รันโลคอลทั้งหมดบนเครื่องคุณ ผ่าน [LM Studio](https://lmstudio.ai)
รองรับ **tool calling** — โมเดลตัดสินใจเองว่าจะเรียกเครื่องมือไหน (คำนวณ, ดูเวลา,
อ่าน/เขียนไฟล์, ดึงข้อมูลเว็บ, ตรวจไฟล์เพลงเสีย) แล้วเอาผลมาตอบต่อ ไม่ต้องต่อเน็ตหา API ภายนอก

> แอปคุยกับ LM Studio ผ่านเซิร์ฟเวอร์แบบ **OpenAI-compatible** ที่ `http://localhost:1234/v1`
> และจะ **สตาร์ตเซิร์ฟเวอร์ให้อัตโนมัติแบบ headless** ตอนเปิดแอป — ไม่ต้องเปิดหน้าต่าง LM Studio เอง

## ไฟล์ในโปรเจกต์

| ไฟล์ | หน้าที่ |
|------|--------|
| `server.py` | **โปรแกรมหลัก** — เว็บเซิร์ฟเวอร์ + agent loop + เชื่อม LM Studio + หน้าต่าง UI |
| `index.html` | หน้าจอแชตสไตล์ Claude (ธีมน้ำเงิน) |
| `agents.py` | นิยาม agent แต่ละบุคลิก (เพิ่ม/แก้ agent ที่นี่) |
| `tools.py` | เครื่องมือที่ agent เรียกได้ (เพิ่ม tool ใหม่ที่นี่) |
| `agent_store.py` | เก็บ/โหลด agent ที่ผู้ใช้สร้างเอง |
| `skills_loader.py` | ระบบโหลด skills จากโฟลเดอร์ `skills/` |
| `data_store.py` | เก็บการตั้งค่า/ข้อมูลในเครื่อง (`data/`) |
| `requirements.txt` | ไลบรารี Python ที่ต้องใช้ (pywebview, pythonnet) |
| `run-ui.bat` | เปิด UI จากสคริปต์ (ดับเบิลคลิก) |
| `build-ui.bat` / `LM Co-work.spec` | สร้างไฟล์ `dist\LM Co-work.exe` |
| `icon.ico` / `icon.png` | ไอคอนแอป |

## Agent ที่มีให้

| ชื่อ | บุคลิก | เครื่องมือที่ใช้ |
|------|--------|------------------|
| `general` | 🧠 ผู้ช่วยทั่วไป | ครบทุกอย่าง (รวมตรวจไฟล์เพลงเสีย) |
| `coder` | 💻 ผู้ช่วยเขียนโค้ด | อ่าน/เขียนไฟล์, คำนวณ |
| `writer` | ✍️ ผู้ช่วยนักเขียน | อ่าน/เขียนไฟล์ |
| `tutor` | 📚 ติวเตอร์ | คำนวณ, เวลา |
| `skill_builder` | 🛠️ Skill Builder | อ่าน/เขียนไฟล์ |
| `translator` | 🌐 นักแปล | — |

## ติดตั้ง

**1. ติดตั้ง LM Studio** — ดาวน์โหลดจาก https://lmstudio.ai แล้วเปิดสักครั้ง

**2. ดาวน์โหลดโมเดลที่รองรับ tool calling** — ในหน้า Discover ของ LM Studio
ดาวน์โหลดโมเดล เช่น **Qwen2.5-7B-Instruct** หรือ **Llama-3.1-8B-Instruct**
(โมเดลที่รองรับ tool calling จะทำงานกับแอปนี้ได้ดี)

**3. ติดตั้ง `lms` CLI ครั้งเดียว** — เพื่อให้แอปสตาร์ตเซิร์ฟเวอร์ headless ให้อัตโนมัติได้:

```bash
npx lmstudio install-cli
```

> เปิด LM Studio สักครั้งก็มักจะติดตั้ง `lms` CLI ให้อยู่แล้ว ขั้นนี้แค่ทำให้ชัวร์

**4. ติดตั้ง Python package แล้วรันโปรแกรม:**

```bash
py -m pip install -r requirements.txt
py server.py
```

> ⚠️ ใช้ `py` ไม่ใช่ `python` — เพราะ `python` ในเครื่องนี้ชี้ไป venv ของแอปอื่น

## ไม่ต้องเปิด LM Studio เอง

ตอนเปิดแอป `LM Co-work` จะ:

1. เช็คว่าเซิร์ฟเวอร์ LM Studio (`localhost:1234`) ทำงานอยู่ไหม
2. ถ้ายัง → สั่ง `lms server start` สตาร์ตแบบ **headless** ในพื้นหลังให้อัตโนมัติ (ไม่มีหน้าต่าง console เด้ง)
3. โมเดลจะถูกโหลดอัตโนมัติแบบ **JIT** ตอนแชตครั้งแรก

ถ้าตอนแชตเชื่อมไม่ได้ ระบบจะลองสตาร์ตให้แล้วลองใหม่อัตโนมัติ (สตาร์ตแค่ครั้งเดียวต่อการรัน เพื่อไม่ให้เปิด process ซ้ำ)

> เงื่อนไขที่ต้องมี: `lms` CLI ในเครื่อง + มีโมเดลดาวน์โหลดไว้ใน LM Studio อย่างน้อย 1 ตัว

## เวอร์ชัน UI — โปรแกรม .exe หน้าต่างจริง

### ทำเป็น .exe (แนะนำ)

ดับเบิลคลิก **`build-ui.bat`** → ได้ `dist\LM Co-work.exe`
จากนั้นดับเบิลคลิก `LM Co-work.exe` เปิดเป็น **หน้าต่างโปรแกรมจริง** (ลาก shortcut ไป Desktop ได้)

> ต้องมี **Microsoft Edge WebView2 Runtime** ในเครื่อง (Windows 11 มีติดมาอยู่แล้ว;
> ถ้าไม่มี โปรแกรมจะแจ้งให้ไปติดตั้งฟรีจาก Microsoft) — โหมด .exe จะ **ไม่เปิดเบราว์เซอร์** เด็ดขาด

### หรือรันจากสคริปต์ (ก็เป็นหน้าต่างเหมือนกัน)

ดับเบิลคลิก **`run-ui.bat`** (จะติดตั้ง pywebview ให้แล้วเปิดเป็นหน้าต่าง)

### สิ่งที่มีใน UI

- **หน้าต่างโปรแกรมจริง** ผ่าน pywebview (WebView2) — ไม่ใช่แท็บเบราว์เซอร์
- **แถบโหมด Chat / Cowork / Code** ด้านบน (แมปกับ agent) แบบ Claude
- **ประวัติแชต/หลายแชต** — แถบซ้ายเก็บแชตทั้งหมด กด "✚ แชตใหม่" / คลิกสลับ / 🗑 ลบ (เก็บในเครื่อง)
- **Projects** — หน้ารวมโฟลเดอร์งาน เพิ่ม/ค้นหา/เลือก เลือกแล้วตั้งเป็น workspace
- **จัดการ path** — ปุ่ม 📂 เปิดหน้าต่างเลือกโฟลเดอร์จริงของ Windows
- **พาเนลไฟล์ด้านขวา** (ปุ่ม 🗂) — เบราว์ไฟล์ในโฟลเดอร์งาน คลิกเปิดดูเนื้อหาได้
- **เลือกโมเดล** ในช่องพิมพ์ (ดึงรายการจาก LM Studio อัตโนมัติ)
- **เลือก agent** จาก dropdown / **คำตอบ markdown** (หัวข้อ ลิสต์ ตัวหนา โค้ดบล็อก + ปุ่มคัดลอก)
- กด Enter ส่ง / Shift+Enter ขึ้นบรรทัดใหม่

## 🎵 ตรวจไฟล์เพลง/ไฟล์เสียงที่เสียหาย

แอปมีเครื่องมือให้ AI ตรวจหาไฟล์เสียงที่ "เสีย" (corrupt) ในโฟลเดอร์ได้ โดย **decode ทั้งไฟล์จริง**
ด้วย `ffmpeg` ไฟล์ไหน decode แล้ว error (หรือว่าง/เปิดไม่ได้) จะถูกรายงานว่าเสีย —
จับได้แม้ header ดูปกติแต่ข้อมูลเสียงพังกลางทาง (เช่นโหลดมาไม่ครบ, sector เสีย)

แค่พิมพ์ในแชต เช่น *"ตรวจไฟล์เพลงเสียในโฟลเดอร์ E:\\music\\Wav ให้หน่อย"* (เปิดโหมด Co-Work เพื่อตรวจโฟลเดอร์นอก workspace)

เครื่องมือที่เกี่ยวข้อง:

| เครื่องมือ | หน้าที่ |
|-----------|--------|
| `check_audio_integrity` | สแกนโฟลเดอร์ + decode ทุกไฟล์เสียงด้วย ffmpeg แล้วรายงานไฟล์ที่เสีย |
| `check_ffmpeg` | ตรวจว่ามี ffmpeg ในเครื่องหรือไม่ |
| `install_ffmpeg` | ติดตั้ง ffmpeg อัตโนมัติผ่าน winget (Windows) เมื่อยังไม่มี |

รองรับ mp3, flac, wav, m4a, aac, ogg, opus, wma, aiff และอื่น ๆ

> ต้องมี **ffmpeg** ในเครื่อง ถ้ายังไม่มี แค่บอก AI ว่า "ติดตั้ง ffmpeg ให้หน่อย" มันจะรัน
> `winget install --id Gyan.FFmpeg -e` ให้เอง

### 🧩 Skills — สร้างความสามารถใหม่ของ agent เอง

agent มี "skills" เสริมที่คุณสร้างเองได้ มี 2 แบบ (หรือผสมกัน):

- **code skill** — ฟังก์ชัน Python ที่ agent เรียกใช้จริง (เช่น นับคำ, เรียก API, แปลงหน่วย)
- **prompt skill** — ชุดคำสั่ง/ความรู้เฉพาะทางที่ฉีดให้ agent ทำตาม

วิธีสร้าง: วาง 1 โฟลเดอร์ใน `skills/<ชื่อ>/` มี `skill.json` (บังคับ) + `tool.py` และ/หรือ `prompt.md`
ดูรูปแบบเต็มและตัวอย่างได้ใน `skills/README.md`

ใน UI จะเห็นรายการ skills ที่ sidebar กดปุ่ม **⟳** เพื่อโหลดใหม่หลังเพิ่ม/แก้ skill
หรือใช้ agent **🛠️ Skill Builder** ให้ช่วยเขียนไฟล์ skill ให้

> ถ้าใช้เป็น `.exe` ให้วางโฟลเดอร์ `skills/` ไว้ข้าง ๆ ไฟล์ `.exe` (build-ui.bat ก็อปให้อัตโนมัติที่ `dist\skills`)

### ฟีเจอร์ Co-Work

- **โฟลเดอร์งาน** — ตั้งโฟลเดอร์ที่ให้ AI อ่าน/เขียนได้ AI จะทำงานได้เฉพาะในโฟลเดอร์นี้เพื่อความปลอดภัย
  (เปิดโหมด Co-Work เพื่อปลดล็อกให้เลือกโฟลเดอร์ที่ไหนก็ได้ เช่นไดรฟ์อื่น)
- **แนบไฟล์** 📎 — กดปุ่มคลิปเพื่อแนบไฟล์ข้อความเข้าโฟลเดอร์งาน แล้ว AI อ่านเนื้อหาได้ทันที
- **ยืนยันก่อนเขียน** — เมื่อ AI จะสร้างหรือแก้ไฟล์ จะแสดง **การ์ดตัวอย่าง** พร้อมปุ่ม *✅ บันทึกไฟล์ / ❌ ไม่บันทึก*
  ไฟล์จะถูกเขียนก็ต่อเมื่อคุณกดยืนยันเท่านั้น (ถ้าเขียนทับของเดิม ระบบสำรองไฟล์เก่าเป็น `.bak` ให้อัตโนมัติ)

## เปลี่ยน/ตั้งค่าโมเดล

ตั้งผ่าน environment variable ได้:

```bash
# Windows PowerShell
$env:LMSTUDIO_MODEL="qwen2.5-7b-instruct"; py server.py
```

| ตัวแปร | ความหมาย |
|--------|----------|
| `LMSTUDIO_BASE_URL` | ที่อยู่เซิร์ฟเวอร์ LM Studio (ดีฟอลต์ `http://localhost:1234/v1`) |
| `LMSTUDIO_MODEL` | โมเดลที่ใช้ (เว้นว่าง = ใช้โมเดลตัวแรกที่ LM Studio โหลดอยู่อัตโนมัติ) |
| `LMSTUDIO_API_KEY` | LM Studio ไม่ตรวจ key — ใส่อะไรก็ได้ |

หรือเลือกโมเดลในหน้า UI ได้โดยตรง (ดึงรายการจาก LM Studio ให้อัตโนมัติ)

### Provider ภายนอกอื่น ๆ (ออปชัน)

นอกจาก LM Studio ยังเพิ่ม provider แบบ OpenAI-compatible อื่นได้ในหน้า "จัดการโมเดล / Provider"
เช่น OpenAI, OpenRouter, Groq, vLLM — ใส่ Base URL ที่ลงท้าย `/v1` + API key

## เพิ่มเครื่องมือใหม่เอง

เปิด `tools.py` แล้วทำ 3 อย่าง:

1. เขียนฟังก์ชัน เช่น
   ```python
   def get_weather(city: str) -> str:
       return f"อากาศที่ {city} แจ่มใส"  # ต่อ API จริงได้
   ```
2. เพิ่มลง `TOOLS`: `"get_weather": get_weather`
3. เพิ่ม schema ใน `TOOL_SCHEMAS` (ก็อปของเดิมมาแก้ชื่อ/พารามิเตอร์)

agent จะรู้จักเครื่องมือใหม่ทันทีในการรันครั้งถัดไป

## เพิ่ม agent ใหม่เอง

เปิด `agents.py` ก็อปบล็อกใน `AGENTS` แล้วแก้ค่า เช่น:

```python
"lawyer": {
    "title": "⚖️ ผู้ช่วยกฎหมาย",
    "description": "อธิบายข้อกฎหมายเบื้องต้น",
    "system": "คุณคือผู้ช่วยด้านกฎหมาย ตอบเป็นภาษาไทย...",
    "tools": ["read_file"],   # None=ทุกเครื่องมือ, []=ไม่ใช้เลย
},
```

รัน `py server.py` ใหม่ จะเห็น agent ใหม่ในเมนูทันที

## หมายเหตุ

- ทุกอย่างรันบนเครื่องคุณ ข้อมูลไม่ออกไปไหน
- ครั้งแรกที่ดาวน์โหลดโมเดลใน LM Studio จะใช้เวลา (หลาย GB)
- `read_file` / `write_file` จำกัดให้ทำงานเฉพาะในโฟลเดอร์งานเพื่อความปลอดภัย
- ต้องมีโมเดลที่รองรับ **tool calling** ใน LM Studio แอปนี้พึ่ง tool calling เป็นหลัก
