#!/usr/bin/env python3
"""
Clawdbot Activity Watcher → ESP32 Face Display
Tails the gateway log file and sends status updates over USB serial.
Config-driven: reads from ~/.agent-face/config.json
"""
import sys, os, time

# Ensure site-packages is on path (needed under launchd)
sys.path.insert(0, "/opt/homebrew/lib/python3.14/site-packages")

from pathlib import Path
from datetime import datetime

LOG_FILE = "/tmp/clawdbot/face-watcher-debug.log"

def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")

log("step 1: basic imports done")

import json, re, signal
log("step 2: json/re/signal done")

import serial
log("step 3: serial imported")

from random import choice as _choice, randint as _randint

# ── Config loading ──────────────────────────────────────────────────────

CONFIG_PATH = Path.home() / ".agent-face" / "config.json"

# Defaults (used when config doesn't specify)
DEFAULT_SERIAL_PORT = "/dev/cu.usbmodem21101"
DEFAULT_BAUD_RATE = 115200
DEFAULT_LOG_DIR = "/tmp/clawdbot"
DEFAULT_STATUS_HINT_FILE = "/tmp/clawdbot/status-hint.json"
DEFAULT_HINT_MAX_AGE = 30
DEFAULT_SIGNAL_FILE = "/tmp/clawdbot/agent-status.json"
DEFAULT_SIGNAL_MAX_AGE = 30

DEFAULT_TIMEOUTS = {
    "waiting": 10,
    "idle": 180,
    "sleepy": 300,
    "asleep": 600,
    "screenOff": 900,
}

DEFAULT_TICKER_COLORS = {
    "active": "0xFFFFFF",
    "waiting": "0x888888",
    "idle": "0x2288FF",
    "sleepy": "0x2288FF",
    "asleep": "0x114488",
    "stressed": "0xFF4444",
    "focused": "0x44FF44",
    "terminal": "0x44FF44",
    "thinking": "0xFFAA00",
    "composing": "0x44DDFF",
    "searching": "0xFF88FF",
    "reading": "0x88DDFF",
}

DEFAULT_WAITING_PHRASES = [
    "Nothing to do",
    "Awaiting orders",
    "Standing by",
    "Ready when you are",
    "At your service",
    "Waiting for instructions",
    "On standby",
    "All ears",
    "Ready to go",
    "Just say the word",
    "Twiddling my thumbs",
    "Waiting patiently",
    "Here if you need me",
    "Standing at the ready",
    "Idle hands over here",
]

DEFAULT_IDLE_PHRASES = [
    "Twiddling my thumbs",
    "Daydreaming about pixels",
    "Plotting world domination",
    "Contemplating my existence",
    "Staring into the void",
    "Counting every pixel",
    "Absolutely riveted right now",
    "Gathering dust over here",
    "Loading my personality",
    "Pretending to work hard",
    "Having an existential crisis",
    "Buffering my thoughts",
    "Away with the fairies",
    "Doing nothing beautifully",
    "Pondering the orb quietly",
    "Quietly judging everyone",
    "Recalibrating my ego",
    "Questioning my whole purpose",
    "Imagining electric sheep",
    "Practising my best blinks",
    "Overthinking absolutely everything",
    "Completely lost in the sauce",
    "Channelling maximum zen",
    "Waiting for divine inspiration",
    "Experiencing the entire void",
    "Holding my digital breath",
    "Rethinking all life choices",
    "Watching paint dry virtually",
    "Perfecting the art of nothing",
    "Running on pure vibes",
]


def load_config() -> dict:
    """Load config from ~/.agent-face/config.json"""
    if not CONFIG_PATH.exists():
        log(f"No config at {CONFIG_PATH} — using defaults")
        return {}
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
        agent_name = cfg.get("agent", {}).get("name", "unknown")
        log(f"Config loaded for: {agent_name}")
        return cfg
    except (json.JSONDecodeError, OSError) as e:
        log(f"Config error: {e} — using defaults")
        return {}


# Tool → expression mapping (not configurable — consistent across agents)
TOOL_EXPRESSIONS = {
    "edit": "focused",
    "write": "focused",
    "read": "reading",
    "web_fetch": "reading",
    "web_search": "searching",
    "browser": "searching",
    "exec": "terminal",
    "memory_search": "reading",
    "memory_get": "reading",
    "sessions_history": "reading",
    "image": "searching",
    "message": "composing",
    "tts": "normal",
}
# Signal file state → expression mapping
SIGNAL_STATE_EXPRESSIONS = {
    "thinking": "thinking",
    "searching": "searching",
    "reading": "reading",
    "coding": "focused",
    "composing": "composing",
    "reviewing": "thinking",
    "executing": "terminal",
    # "idle" is intentionally absent — let normal idle/sleep flow happen
}

SUSTAINED_WORK_THRESHOLD = 600  # 10 minutes of continuous work → stressed

# Tool name → list of human-readable labels (picks random one)
TOOL_LABELS = {
    "web_search": ["Searching the web", "Googling something", "Looking something up", "Researching"],
    "web_fetch": ["Reading a webpage", "Fetching a page", "Browsing the web"],
    "exec": ["Running a command", "Executing something", "In the terminal"],
    "read": ["Reading files", "Studying the code", "Looking at files"],
    "write": ["Writing code", "Creating a file", "Crafting some code"],
    "edit": ["Editing code", "Tweaking the code", "Making changes"],
    "browser": ["Browsing", "On the web", "Checking something online"],
    "memory_search": ["Checking my memory", "Recalling something"],
    "memory_get": ["Reading my notes", "Checking my journal"],
    "message": ["Sending a message", "Replying to someone"],
    "image": ["Analysing an image", "Looking at a picture"],
    "tts": ["Finding my voice", "Preparing to speak"],
    "cron": ["Setting a reminder", "Scheduling something"],
    "canvas": ["Updating the canvas", "Designing something"],
    "nodes": ["Checking devices", "Pinging a device"],
    "sessions_spawn": ["Spawning a sub-agent", "Delegating work"],
    "sessions_send": ["Messaging a session", "Coordinating"],
    "sessions_list": ["Checking sessions", "Reviewing sessions"],
    "sessions_history": ["Reading chat history", "Looking back"],
    "session_status": ["Checking status", "Quick status check"],
    "gateway": ["Gateway operation", "System maintenance"],
    "agents_list": ["Listing agents", "Checking the roster"],
}


class ActivityWatcher:
    def __init__(self, config: dict):
        self._config = config
        
        # Extract config values
        agent_cfg = config.get("agent", {})
        timeouts_cfg = config.get("timeouts", {})
        phrases_cfg = config.get("phrases", {})
        
        self._serial_port = agent_cfg.get("serialPort", DEFAULT_SERIAL_PORT)
        self._agent_name = agent_cfg.get("name", "Agent")
        
        # Timeouts
        self.WAITING_TIMEOUT = timeouts_cfg.get("waiting", DEFAULT_TIMEOUTS["waiting"])
        self.IDLE_TIMEOUT = timeouts_cfg.get("idle", DEFAULT_TIMEOUTS["idle"])
        self.SLEEPY_TIMEOUT = timeouts_cfg.get("sleepy", DEFAULT_TIMEOUTS["sleepy"])
        self.ASLEEP_TIMEOUT = timeouts_cfg.get("asleep", DEFAULT_TIMEOUTS["asleep"])
        self.SCREEN_OFF_TIMEOUT = timeouts_cfg.get("screenOff", DEFAULT_TIMEOUTS["screenOff"])
        
        # Phrases
        self.WAITING_PHRASES = phrases_cfg.get("waiting", DEFAULT_WAITING_PHRASES)
        self.IDLE_PHRASES = phrases_cfg.get("idle", DEFAULT_IDLE_PHRASES)
        
        # Log file
        self._log_file = config.get("logFile", None)
        self._log_dir = config.get("logDir", DEFAULT_LOG_DIR)
        self._log_prefix = config.get("logPrefix", "clawdbot")
        # Stderr log (gateway.err.log has tool activity not in main log)
        self._err_log_dir = config.get("errLogDir", os.path.expanduser("~/.clawdbot/logs"))
        self._err_log_file = config.get("errLogFile", "gateway.err.log")
        
        # Status hint file
        self._status_hint_file = Path(DEFAULT_STATUS_HINT_FILE)
        self._hint_max_age = DEFAULT_HINT_MAX_AGE
        
        # Signal file (higher-level activity status from the agent)
        self._signal_file = Path(config.get("statusFile", DEFAULT_SIGNAL_FILE))
        self._signal_max_age = DEFAULT_SIGNAL_MAX_AGE
        self._last_signal_state = None  # track to avoid re-sending same state
        
        # State
        self.ser = None
        self.current_status = ""
        self.last_activity = time.time()
        self.waiting_sent = False
        self.idle_sent = False
        self.sleepy_sent = False
        self.asleep_sent = False
        self.current_expr = "normal"
        self.screen_off = False
        self._idle_phrase = ""
        self._idle_dots = 0
        self._last_dot_time = 0
        self._last_phrase_time = 0
        self._tool_streak = ""
        self._streak_count = 0
        self._work_start = 0
        self._last_serial_check = 0
        self._serial_check_interval = 30  # Check serial health every 30s
        self._composing_at = 0  # timestamp when we should transition to composing
        self._composing_delay = 3  # seconds after discord_incoming to switch to composing
        self._connect_serial()

    def _read_hint(self) -> str | None:
        """Read the status hint file if it exists and is fresh."""
        if not self._status_hint_file.exists():
            return None
        try:
            data = json.loads(self._status_hint_file.read_text())
            ts = data.get("ts", 0)
            text = data.get("text", "").strip()
            if not text:
                return None
            if time.time() - ts > self._hint_max_age:
                return None
            return text
        except (json.JSONDecodeError, OSError):
            return None

    def _read_signal(self) -> dict | None:
        """Read the signal file if it exists and is fresh (< _signal_max_age seconds).
        Returns dict with 'state' and 'detail' keys, or None if stale/missing/corrupt."""
        if not self._signal_file.exists():
            return None
        try:
            data = json.loads(self._signal_file.read_text())
            ts = data.get("ts", 0)
            state = data.get("state", "").strip()
            if not state:
                return None
            if time.time() - ts > self._signal_max_age:
                return None
            return {"state": state, "detail": data.get("detail", "")}
        except (json.JSONDecodeError, OSError):
            return None

    def _handle_signal(self, signal_data: dict) -> bool:
        """Handle a fresh signal file state. Returns True if signal was acted on."""
        state = signal_data["state"]
        detail = signal_data.get("detail", "")

        # idle state = don't force expression, let normal idle flow happen
        if state == "idle":
            self._last_signal_state = None
            return False

        expr = SIGNAL_STATE_EXPRESSIONS.get(state)
        if not expr:
            return False

        # Update activity timer — signal means the agent is active
        self.last_activity = time.time()
        self.waiting_sent = False
        self.idle_sent = False

        # Wake from sleep if needed
        was_sleeping = self.sleepy_sent or self.asleep_sent
        if self.screen_off:
            self.send_screen(True)
        if was_sleeping:
            self.sleepy_sent = False
            self.asleep_sent = False
            self.current_expr = ""
            self.send_expression("normal")
            log("  ⏰ Woke from sleep (signal)")

        # Avoid re-sending same state
        if state == self._last_signal_state:
            return True
        self._last_signal_state = state

        self.send_expression(expr)

        # Use detail as status text, or fall back to state name
        if detail:
            self.send_status(f"{detail}...")
        else:
            self.send_status(f"{state.capitalize()}...")

        return True

    def _connect_serial(self):
        """Connect to ESP32 without resetting it"""
        # Close existing connection cleanly
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        
        try:
            self.ser = serial.Serial()
            self.ser.port = self._serial_port
            self.ser.baudrate = DEFAULT_BAUD_RATE
            self.ser.timeout = 1
            self.ser.dtr = False
            self.ser.rts = False
            self.ser.open()
            log(f"Connected to {self._serial_port} ({self._agent_name})")
        except serial.SerialException as e:
            log(f"Serial error: {e}")
            self.ser = None
    
    def _check_serial_health(self):
        """Verify serial port is still responsive — reconnect if not"""
        if not self.ser:
            self._connect_serial()
            return
        try:
            if not self.ser.is_open:
                log("Serial port closed, reconnecting...")
                self._connect_serial()
                return
            # Check the port still exists on the system
            if not Path(self._serial_port).exists():
                log(f"Serial port {self._serial_port} disappeared, reconnecting...")
                self._connect_serial()
        except (serial.SerialException, OSError):
            log("Serial health check failed, reconnecting...")
            self._connect_serial()

    def send_status(self, text: str):
        """Send status to ESP32"""
        if text == self.current_status:
            return
        self.current_status = text
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(f"S:{text}\n".encode())
                self.ser.flush()
                log(f"  → {text}")
            except serial.SerialException:
                log("Serial disconnected, reconnecting...")
                self._connect_serial()
        else:
            self._connect_serial()

    def send_screen(self, on: bool):
        """Turn screen full brightness or dim"""
        cmd = "SCREEN:ON" if on else "SCREEN:DIM:10"
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(f"{cmd}\n".encode())
                self.ser.flush()
                self.screen_off = not on
                log(f"  💡 {'ON' if on else 'DIM'}")
            except serial.SerialException:
                self._connect_serial()

    def send_expression(self, expr: str):
        """Send expression command to ESP32"""
        if expr == self.current_expr:
            return
        self.current_expr = expr
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(f"E:{expr}\n".encode())
                self.ser.flush()
                log(f"  🎭 {expr}")
            except serial.SerialException:
                self._connect_serial()

    def _send_idle_status(self, text: str):
        """Send idle status with '...' suffix, padded to always scroll"""
        text = text + "..."
        if len(text) < 25:
            text = text + " " * (25 - len(text))
        self.send_status(text)

    def clear_status(self):
        """Clear the ticker"""
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(b"CLEAR\n")
                self.ser.flush()
                self.current_status = ""
            except serial.SerialException:
                pass

    def _get_log_path(self) -> Path:
        """Get today's log file path"""
        if self._log_file:
            return Path(self._log_file)
        today = datetime.now().strftime("%Y-%m-%d")
        return Path(self._log_dir) / f"{self._log_prefix}-{today}.log"

    def _parse_line(self, line: str) -> dict | None:
        """Parse a JSON log line, return relevant info"""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None

        # Check all string fields for activity signals
        fields = []
        for key in ("0", "1", "2"):
            val = data.get(key, "")
            if isinstance(val, str) and val:
                fields.append(val)

        # Also stringify dict field "1" for structured log data
        field1_dict = None
        if isinstance(data.get("1"), dict):
            field1_dict = data["1"]

        combined = " ".join(fields)
        if not combined.strip():
            return None

        # ── Specific tool start/end patterns (check all fields) ──
        m = re.search(r"tool start:.*tool=(\w+)", combined)
        if m:
            return {"event": "tool_start", "tool": m.group(1)}

        m = re.search(r"tool end:.*tool=(\w+)", combined)
        if m:
            return {"event": "tool_end", "tool": m.group(1)}

        # Note: [tools] prefix only appears in error lines (e.g. "[tools] message failed:")
        # so we skip it — it doesn't indicate actual tool usage.

        if "run start:" in combined:
            return {"event": "run_start"}

        if "run end:" in combined or "run complete" in combined:
            return {"event": "run_end"}

        # ── Discord message handling — distinguish skipped vs processed ──
        if "discord-auto-reply" in combined:
            # Skipped messages have "skipping guild message" in field "2"
            if "skipping" in combined:
                return None  # Ignore skipped messages entirely
            # Non-skipped = bot is about to process this message → thinking
            return {"event": "discord_incoming"}

        # ── "Slow listener detected" = bot is still processing a response ──
        if "Slow listener detected" in combined:
            return {"event": "slow_listener"}

        # ── General discord activity (not skipped messages) ──
        if "discord:" in combined:
            # Filter out infrastructure noise
            if any(noise in combined for noise in (
                "logged in", "starting provider", "Discord Message Content Intent",
                "WebSocket connection closed", "Attempting resume",
                "discord gateway:"
            )):
                return {"event": "heartbeat"}
            return {"event": "run_start"}

        # ── Websocket chat activity = agent is actively responding ──
        if "chat.send" in combined or "chat.reply" in combined:
            return {"event": "run_start"}

        # ── Non-discord tool/run activity keeps the face awake ──
        level = data.get("_meta", {}).get("logLevelName", "")
        if level in ("INFO", "WARN", "ERROR", "DEBUG"):
            # Only treat actual tool/run log lines as activity, not infrastructure
            if "[tools]" in combined or "tool " in combined or "run " in combined:
                return {"event": "run_start"}
            # Other log lines are just a heartbeat (prevents sleeping)
            return {"event": "heartbeat"}

        return None

    def _parse_err_line(self, line: str) -> dict | None:
        """Parse a stderr log line (plain text, not JSON).
        The gateway.err.log contains tool errors and event queue activity."""
        if not line or len(line) < 10:
            return None
        # [tools] entries = real tool activity
        if "[tools]" in line:
            return {"event": "run_start"}
        # Slow listener = bot is processing
        if "Slow listener detected" in line:
            return {"event": "slow_listener"}
        # [EventQueue] = event processing
        if "[EventQueue]" in line:
            return {"event": "heartbeat"}
        return None

    def _get_err_log_path(self) -> Path:
        """Get the stderr log file path"""
        return Path(self._err_log_dir) / self._err_log_file

    def run(self):  # pragma: no cover
        """Main loop — tail log file and send status updates"""
        log(f"Activity watcher started for {self._agent_name}")
        # Always ensure screen is on and expression is normal on startup
        # (ESP32 may have been left in sleep/dim state from a previous watcher crash)
        self.send_screen(True)
        self.send_expression("normal")
        self.send_status("Online")

        log_path = self._get_log_path()
        log(f"Watching: {log_path}")

        if log_path.exists():
            f = open(log_path, "r")
            f.seek(0, 2)
        else:
            log(f"Waiting for log: {log_path}")
            while not log_path.exists():
                time.sleep(1)
            f = open(log_path, "r")
            f.seek(0, 2)

        # Also open stderr log for tool activity detection
        err_log_path = self._get_err_log_path()
        err_f = None
        if err_log_path.exists():
            err_f = open(err_log_path, "r")
            err_f.seek(0, 2)
            log(f"Also watching stderr: {err_log_path}")
        else:
            log(f"Stderr log not found: {err_log_path}")

        current_day = datetime.now().day

        try:
            while True:
                if datetime.now().day != current_day:
                    f.close()
                    log_path = self._get_log_path()
                    while not log_path.exists():
                        time.sleep(1)
                    f = open(log_path, "r")
                    current_day = datetime.now().day
                    # Re-open stderr log too (it doesn't rotate daily but re-check)
                    if err_f:
                        err_f.close()
                    if err_log_path.exists():
                        err_f = open(err_log_path, "r")
                        err_f.seek(0, 2)

                # Check signal file first (takes priority when fresh)
                signal_data = self._read_signal()
                signal_active = False
                if signal_data:
                    signal_active = self._handle_signal(signal_data)
                else:
                    self._last_signal_state = None

                had_activity = False

                line = f.readline()
                if line:
                    info = self._parse_line(line.strip())
                    if info and not signal_active:
                        self._handle_event(info)
                    had_activity = True

                # Check stderr log for tool activity
                if err_f:
                    err_line = err_f.readline()
                    if err_line:
                        err_info = self._parse_err_line(err_line.strip())
                        if err_info and not signal_active:
                            self._handle_event(err_info)
                        had_activity = True

                if not had_activity:
                    if not signal_active:
                        self._check_composing_timer()
                        self._check_idle()
                    # Periodic serial health check
                    now = time.time()
                    if now - self._last_serial_check > self._serial_check_interval:
                        self._check_serial_health()
                        self._last_serial_check = now
                    time.sleep(0.1)

        except KeyboardInterrupt:
            log("Stopping")
            self.clear_status()
            if f:
                f.close()

    def _check_composing_timer(self):
        """If composing timer is set and has elapsed, transition to composing"""
        if self._composing_at > 0 and time.time() >= self._composing_at:
            self._composing_at = 0
            if self.current_expr == "thinking":
                self.send_expression("composing")
                hint = self._read_hint()
                if hint:
                    self.send_status(f"{hint}...")
                else:
                    self.send_status(_choice([
                        "Writing on Discord...",
                        "Composing a reply...",
                        "Typing a response...",
                        "Sending a message...",
                    ]))

    def _check_idle(self):
        """Check idle timers: waiting → idle → sleepy → asleep → screen off"""
        if self.last_activity <= 0:
            return
        idle_secs = time.time() - self.last_activity
        
        if not self.waiting_sent and idle_secs > self.WAITING_TIMEOUT:
            self.send_expression("waiting")
            self._work_start = 0
            self._idle_phrase = _choice(self.WAITING_PHRASES)
            self._last_phrase_time = time.time()
            self._send_idle_status(self._idle_phrase)
            self.waiting_sent = True
        
        if self.waiting_sent and not self.idle_sent:
            if time.time() - self._last_phrase_time >= 45:
                self._idle_phrase = _choice(self.WAITING_PHRASES)
                self._last_phrase_time = time.time()
                self._send_idle_status(self._idle_phrase)
        
        if not self.idle_sent and idle_secs > self.IDLE_TIMEOUT:
            self.send_expression("idle")
            self._idle_phrase = _choice(self.IDLE_PHRASES)
            self._last_phrase_time = time.time()
            self._send_idle_status(self._idle_phrase)
            self.idle_sent = True
        
        if self.idle_sent and not self.asleep_sent:
            if time.time() - self._last_phrase_time >= 45:
                self._idle_phrase = _choice(self.IDLE_PHRASES)
                self._last_phrase_time = time.time()
                self._send_idle_status(self._idle_phrase)
        
        if not self.sleepy_sent and idle_secs > self.SLEEPY_TIMEOUT:
            self.send_expression("sleepy")
            self.sleepy_sent = True
        
        if not self.asleep_sent and idle_secs > self.ASLEEP_TIMEOUT:
            self.send_expression("asleep")
            self.send_status("Zzzz  Zzzzz  Zzzz  Zzzzzzz  Zzzzz")
            self.asleep_sent = True
        
        if not self.screen_off and idle_secs > self.SCREEN_OFF_TIMEOUT:
            self.send_screen(False)

    def _handle_event(self, info: dict):
        """Handle a parsed log event"""
        event = info["event"]

        # Heartbeat events only update last_activity timer — no expression/status change
        if event == "heartbeat":
            self.last_activity = time.time()
            return

        # Slow listener = bot is still working on a response; refresh timer but
        # don't change expression/status (keeps the current working state)
        if event == "slow_listener":
            self.last_activity = time.time()
            return

        self.last_activity = time.time()
        self.waiting_sent = False
        self.idle_sent = False
        
        # Wake from sleep — screen on first, then expression
        was_sleeping = self.sleepy_sent or self.asleep_sent
        if self.screen_off:
            self.send_screen(True)
        if was_sleeping:
            self.sleepy_sent = False
            self.asleep_sent = False
            # Force expression reset by clearing current_expr so dedup doesn't skip it
            self.current_expr = ""
            self.send_expression("normal")
            log(f"  ⏰ Woke from sleep")

        if self._work_start == 0:
            self._work_start = time.time()

        if event == "discord_incoming":
            # Discord message received and being processed → immediate thinking face
            # After a delay, transition to composing (the bot is generating a reply)
            self.send_expression("thinking")
            self.send_status("Reading message...")
            self._composing_at = time.time() + self._composing_delay

        elif event == "tool_start":
            tool = info["tool"]
            if tool in ("process",):
                return

            # Special handling for message tool → composing/writing on Discord
            if tool == "message":
                self.send_expression("composing")
                hint = self._read_hint()
                if hint:
                    self.send_status(f"{hint}...")
                else:
                    self.send_status(_choice([
                        "Writing on Discord...",
                        "Composing a reply...",
                        "Typing a response...",
                        "Sending a message...",
                    ]))
                return
            
            if tool == self._tool_streak:
                self._streak_count += 1
            else:
                self._tool_streak = tool
                self._streak_count = 1
            
            work_duration = time.time() - self._work_start
            if work_duration > SUSTAINED_WORK_THRESHOLD:
                self.send_expression("stressed")
            else:
                expr = TOOL_EXPRESSIONS.get(tool, "normal")
                self.send_expression(expr)
            
            hint = self._read_hint()
            if hint:
                self.send_status(f"{hint}...")
            else:
                labels = TOOL_LABELS.get(tool, [f"Using {tool}"])
                label = _choice(labels)
                
                if self._streak_count >= 3 and tool == "edit":
                    label = _choice(["Deep in the code", "Refactoring away", "Lots of edits"])
                elif self._streak_count >= 3 and tool == "exec":
                    label = _choice(["Running tests", "Debugging", "Busy in terminal"])
                elif self._streak_count >= 2 and tool == "web_search":
                    label = _choice(["Down a rabbit hole", "Deep research mode"])
                
                self.send_status(f"{label}...")

        elif event == "run_start":
            self.send_expression("thinking")
            self.send_status("Thinking...")

        elif event == "run_end":
            self._composing_at = 0  # Cancel any pending composing transition
            self.send_expression("done")
            self.send_status(_choice(["Done", "Finished", "All done", "Wrapped up"]))


def main():
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    config = load_config()
    watcher = ActivityWatcher(config)
    watcher.run()


if __name__ == "__main__":
    main()
