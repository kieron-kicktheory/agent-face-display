#!/usr/bin/env python3
"""
Clawdbot Activity Watcher → ESP32 Face Display
Tails the gateway log file and sends status updates over USB serial.
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

SERIAL_PORT = "/dev/cu.usbmodem21101"
BAUD_RATE = 115200
LOG_DIR = "/tmp/clawdbot"
STATUS_HINT_FILE = "/tmp/clawdbot/status-hint.json"  # Agent writes context here
HINT_MAX_AGE = 30  # seconds before a hint is considered stale
IDLE_TIMEOUT = 300    # 5 minutes before idle phrases
SLEEPY_TIMEOUT = 300  # (same as idle — they fire together)
ASLEEP_TIMEOUT = 600  # 10 minutes before fully asleep
SCREEN_OFF_TIMEOUT = 900  # 15 minutes before screen off

# Colors driven by expression changes on ESP32 (E: command)
# Tool → expression mapping
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
    "message": "normal",
    "tts": "normal",
}
SUSTAINED_WORK_THRESHOLD = 600  # 10 minutes of continuous work → stressed

# Fun idle phrases — one picked at random each time we go idle
IDLE_PHRASES = [
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
    def __init__(self):
        self.ser = None
        self.current_status = ""
        self.last_activity = time.time()
        self.idle_sent = False
        self.sleepy_sent = False
        self.asleep_sent = False
        self.current_expr = "normal"
        self.screen_off = False
        self._idle_phrase = ""
        self._idle_dots = 0
        self._last_dot_time = 0
        self._last_phrase_time = 0
        self._tool_streak = ""   # Current tool being repeated
        self._streak_count = 0   # How many times in a row
        self._work_start = 0     # When sustained work began
        self._connect_serial()

    def _read_hint(self) -> str | None:
        """Read the status hint file if it exists and is fresh (< HINT_MAX_AGE seconds)."""
        hint_path = Path(STATUS_HINT_FILE)
        if not hint_path.exists():
            return None
        try:
            data = json.loads(hint_path.read_text())
            ts = data.get("ts", 0)
            text = data.get("text", "").strip()
            if not text:
                return None
            if time.time() - ts > HINT_MAX_AGE:
                return None  # Stale hint, ignore
            return text
        except (json.JSONDecodeError, OSError):
            return None

    def _connect_serial(self):
        """Connect to ESP32 without resetting it"""
        try:
            self.ser = serial.Serial()
            self.ser.port = SERIAL_PORT
            self.ser.baudrate = BAUD_RATE
            self.ser.timeout = 1
            self.ser.dtr = False
            self.ser.rts = False
            self.ser.open()
            log(f"Connected to {SERIAL_PORT}")
        except serial.SerialException as e:
            log(f"Serial error: {e}")
            self.ser = None

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
        """Turn screen backlight on/off"""
        cmd = "SCREEN:ON" if on else "SCREEN:OFF"
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(f"{cmd}\n".encode())
                self.ser.flush()
                self.screen_off = not on
                log(f"  💡 {'ON' if on else 'OFF'}")
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
        # Pad to 25 chars (300px > 240px = guaranteed scroll)
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
        today = datetime.now().strftime("%Y-%m-%d")
        return Path(LOG_DIR) / f"clawdbot-{today}.log"

    def _parse_line(self, line: str) -> dict | None:
        """Parse a JSON log line, return relevant info"""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None

        msg = data.get("1", "")
        if not msg or not isinstance(msg, str):
            return None

        # Tool start
        m = re.search(r"tool start:.*tool=(\w+)", msg)
        if m:
            return {"event": "tool_start", "tool": m.group(1)}

        # Tool end
        m = re.search(r"tool end:.*tool=(\w+)", msg)
        if m:
            return {"event": "tool_end", "tool": m.group(1)}

        # Run start (thinking)
        if "run start:" in msg:
            return {"event": "run_start"}

        # Run end
        if "run end:" in msg or "run complete" in msg:
            return {"event": "run_end"}

        return None

    def run(self):  # pragma: no cover
        """Main loop — tail log file and send status updates"""
        log("Activity watcher started")
        self.send_status("Online")

        log_path = self._get_log_path()
        log(f"Watching: {log_path}")

        # Start at end of file
        if log_path.exists():
            f = open(log_path, "r")
            f.seek(0, 2)  # Seek to end
        else:
            log(f"Waiting for log: {log_path}")
            while not log_path.exists():
                time.sleep(1)
            f = open(log_path, "r")
            f.seek(0, 2)

        current_day = datetime.now().day

        try:
            while True:
                # Check for day rollover
                if datetime.now().day != current_day:
                    f.close()
                    log_path = self._get_log_path()
                    while not log_path.exists():
                        time.sleep(1)
                    f = open(log_path, "r")
                    current_day = datetime.now().day

                line = f.readline()
                if line:
                    info = self._parse_line(line.strip())
                    if info:
                        self._handle_event(info)
                else:
                    self._check_idle()
                    time.sleep(0.1)

        except KeyboardInterrupt:
            log("Stopping")
            self.clear_status()
            if f:
                f.close()

    def _check_idle(self):
        """Check idle timers and transition through sleepy → asleep → screen off"""
        if self.last_activity <= 0:
            return
        idle_secs = time.time() - self.last_activity
        if not self.idle_sent and idle_secs > IDLE_TIMEOUT:
            # Go sleepy + idle together — eyes droop and text dims at once
            self.send_expression("sleepy")
            self.sleepy_sent = True
            self._work_start = 0  # Reset sustained work timer
            self._idle_phrase = _choice(IDLE_PHRASES)
            self._idle_dots = 0
            self._last_dot_time = time.time()
            self._last_phrase_time = time.time()
            self._send_idle_status(self._idle_phrase)
            self.idle_sent = True
        if self.idle_sent and not self.asleep_sent:
            # New phrase every 8 seconds — scrolling is the animation
            if time.time() - self._last_phrase_time >= 8:
                self._idle_phrase = _choice(IDLE_PHRASES)
                self._last_phrase_time = time.time()
                self._send_idle_status(self._idle_phrase)
        if not self.asleep_sent and idle_secs > ASLEEP_TIMEOUT:
            self.send_expression("asleep")
            self.send_status("Zzzz  Zzzzz  Zzzz  Zzzzzzz  Zzzzz")
            self.asleep_sent = True
        if not self.screen_off and idle_secs > SCREEN_OFF_TIMEOUT:
            self.send_screen(False)

    def _handle_event(self, info: dict):
        """Handle a parsed log event"""
        event = info["event"]
        self.last_activity = time.time()
        self.idle_sent = False
        if self.screen_off:
            self.send_screen(True)
        if self.sleepy_sent or self.asleep_sent:
            self.sleepy_sent = False
            self.asleep_sent = False
            self.send_expression("normal")

        # Track sustained work duration
        if self._work_start == 0:
            self._work_start = time.time()

        if event == "tool_start":
            tool = info["tool"]
            # Skip noisy/internal tools
            if tool in ("process",):
                return
            
            # Track streaks for richer messages
            if tool == self._tool_streak:
                self._streak_count += 1
            else:
                self._tool_streak = tool
                self._streak_count = 1
            
            # Set eye expression based on tool
            work_duration = time.time() - self._work_start
            if work_duration > SUSTAINED_WORK_THRESHOLD:
                self.send_expression("stressed")
            else:
                expr = TOOL_EXPRESSIONS.get(tool, "normal")
                self.send_expression(expr)
            
            # Check for a rich context hint from the agent first
            hint = self._read_hint()
            if hint:
                self.send_status(f"{hint}...")
            else:
                labels = TOOL_LABELS.get(tool, [f"Using {tool}"])
                label = _choice(labels)
                
                # Add streak context for repeated tools
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
            self.send_expression("done")
            self.send_status(_choice(["Done", "Finished", "All done", "Wrapped up"]))


def main():
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    watcher = ActivityWatcher()
    watcher.run()


if __name__ == "__main__":
    main()
