import json
import subprocess
import threading
import uuid
import os
import logging

import winproc  # ซ่อนหน้าต่าง console ของ MCP subprocess บน Windows

_log = logging.getLogger(__name__)

class MCPClient:
    def __init__(self, command, args, env=None):
        self.command = command
        self.args = args
        self.env = env or os.environ.copy()
        self.process = None
        self.reader_thread = None
        self.responses = {}
        self.response_events = {}
        self.is_running = False
        
        # Tools info
        self.server_name = ""
        self.server_version = ""
        self.tools = []

    def start(self):
        try:
            self.process = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                text=True,
                bufsize=1, # line buffered
                encoding='utf-8',
                **winproc.no_window_kwargs()
            )
            self.is_running = True
            self.reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self.reader_thread.start()
            # QUAL-5: ต้องมีคนอ่าน stderr เสมอ — ไม่งั้น MCP server ที่พ่น log มากๆ
            # จะเต็ม pipe buffer แล้วค้างทั้งโปรเซส (deadlock คลาสสิก)
            threading.Thread(target=self._stderr_loop, daemon=True).start()
            
            # Send initialize
            init_res = self.send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "lm-co-work",
                    "version": "1.0.0"
                }
            })
            
            if init_res:
                self.server_name = init_res.get("serverInfo", {}).get("name", "unknown")
                self.server_version = init_res.get("serverInfo", {}).get("version", "unknown")
                
                # Send initialized notification
                self.send_notification("notifications/initialized", {})
                
                # Fetch tools
                tools_res = self.send_request("tools/list", {})
                if tools_res:
                    self.tools = tools_res.get("tools", [])
                    _log.info(f"Loaded {len(self.tools)} tools from MCP server {self.server_name}")
                return True
        except Exception as e:
            _log.error(f"Failed to start MCP client {self.command}: {e}")
            return False

    def stop(self):
        self.is_running = False
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def _stderr_loop(self):
        """QUAL-5: อ่าน stderr ของ MCP server ทิ้ง (พร้อม log debug) กัน pipe เต็มจน deadlock."""
        try:
            for line in self.process.stderr:
                line = line.strip()
                if line:
                    _log.debug("MCP[%s] stderr: %s", self.server_name or self.command, line)
        except Exception:  # noqa: BLE001 — pipe ปิดตอนโปรเซสจบเป็นเรื่องปกติ
            pass

    def _read_loop(self):
        while self.is_running and self.process and self.process.poll() is None:
            try:
                line = self.process.stdout.readline()
                if not line:
                    break

                line = line.strip()
                if not line:
                    continue

                data = json.loads(line)

                if "id" in data and ("result" in data or "error" in data):
                    req_id = data["id"]
                    # QUAL-5: เก็บเฉพาะ response ที่ยังมีคนรอ — response ที่มาช้ากว่า
                    # timeout (event ถูกถอดไปแล้ว) ทิ้งเลย ไม่งั้นค้างใน dict ตลอดชีพ
                    if req_id in self.response_events:
                        self.responses[req_id] = data
                        self.response_events[req_id].set()
                    else:
                        _log.debug("MCP response ตกค้าง (เลย timeout) ถูกทิ้ง: %s", req_id)
                elif "method" in data:
                    # Handle notifications from server
                    _log.debug(f"Received MCP notification: {data}")

            except Exception as e:
                _log.error(f"Error reading MCP response: {e}")

    def send_request(self, method, params, timeout=10.0):
        if not self.is_running or not self.process:
            return None
            
        req_id = str(uuid.uuid4())
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params
        }
        
        event = threading.Event()
        self.response_events[req_id] = event
        
        try:
            self.process.stdin.write(json.dumps(msg) + "\n")
            self.process.stdin.flush()
        except Exception as e:
            _log.error(f"Error sending MCP request: {e}")
            return None
            
        if event.wait(timeout):
            res = self.responses.pop(req_id, None)
            self.response_events.pop(req_id, None)
            
            if res and "error" in res:
                _log.error(f"MCP server error: {res['error']}")
                return {"_error": res["error"]}
            elif res and "result" in res:
                return res["result"]
        else:
            _log.error(f"MCP request timeout: {method}")
            self.response_events.pop(req_id, None)
            self.responses.pop(req_id, None)   # QUAL-5: กัน response ที่มาช้าค้างใน dict

        return None

    def send_notification(self, method, params):
        if not self.is_running or not self.process:
            return
            
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        try:
            self.process.stdin.write(json.dumps(msg) + "\n")
            self.process.stdin.flush()
        except Exception as e:
            _log.error(f"Error sending MCP notification: {e}")

    def call_tool(self, name, arguments, timeout=30.0):
        res = self.send_request("tools/call", {
            "name": name,
            "arguments": arguments
        }, timeout=timeout)
        
        if res and "_error" in res:
            return f"Error: {res['_error']}"
            
        if res and "content" in res:
            # Usually content is a list of objects like {"type": "text", "text": "..."}
            texts = []
            for item in res["content"]:
                if item.get("type") == "text":
                    texts.append(item.get("text", ""))
            return "\n".join(texts)
            
        return str(res)

class MCPManager:
    def __init__(self):
        self.clients = {} # id -> MCPClient
        self.tool_schemas = []
        self.tool_mapping = {} # tool_name -> client_id
        self.errors = {}  # id -> ข้อความ error ตอนสตาร์ต (F4: โชว์ใน UI ได้)

    def status(self) -> list[dict]:
        """F4: สถานะทุก server สำหรับแสดงใน UI."""
        out = []
        for sid, c in self.clients.items():
            running = bool(c.process and c.process.poll() is None)
            out.append({"id": sid, "name": c.server_name, "version": c.server_version,
                        "running": running,
                        "tools": [t.get("name", "") for t in c.tools]})
        for sid, err in self.errors.items():
            if sid not in self.clients:
                out.append({"id": sid, "name": "", "version": "", "running": False,
                            "tools": [], "error": err})
        return out

    def load_config(self, config_path):
        if not os.path.exists(config_path):
            return False
            
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                
            mcp_servers = config.get("mcpServers", {})
            for server_id, server_conf in mcp_servers.items():
                command = server_conf.get("command")
                args = server_conf.get("args", [])
                env_updates = server_conf.get("env", {})
                
                env = os.environ.copy()
                env.update(env_updates)
                
                client = MCPClient(command, args, env)
                if not client.start():
                    self.errors[server_id] = f"สตาร์ตไม่สำเร็จ (command: {command})"
                    client.stop()
                else:
                    self.clients[server_id] = client
                    _log.info(f"Started MCP server: {server_id}")
                    
                    # Convert schemas to OpenAI format
                    for tool in client.tools:
                        tool_name = f"{server_id}_{tool['name']}"
                        self.tool_mapping[tool_name] = server_id
                        
                        self.tool_schemas.append({
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "description": tool.get("description", ""),
                                "parameters": tool.get("inputSchema", {})
                            }
                        })
            return True
        except Exception as e:
            _log.error(f"Error loading MCP config {config_path}: {e}")
            return False

    def stop_all(self):
        for client in self.clients.values():
            client.stop()
        self.clients.clear()
        self.tool_schemas.clear()
        self.tool_mapping.clear()
        self.errors.clear()

    def call_tool(self, tool_name, arguments):
        client_id = self.tool_mapping.get(tool_name)
        if not client_id:
            return f"Error: Unknown MCP tool {tool_name}"
            
        client = self.clients.get(client_id)
        if not client:
            return f"Error: MCP client {client_id} not running"
            
        # Strip the prefix to get the original tool name
        original_name = tool_name[len(client_id)+1:]
        return client.call_tool(original_name, arguments)

mcp_manager = MCPManager()
