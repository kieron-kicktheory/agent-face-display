#!/usr/bin/env python3
"""
Gateway Activity Signal Writer
Monitors the Clawdbot gateway process for activity and writes the
agent-status signal file for the face watcher to consume.

Two detection methods run together:
1. Log tailing — reads new lines from stdout + stderr logs in real-time
   for specific tool/activity detection (gives detailed state info)
2. CPU polling — checks gateway process CPU usage every few seconds
   as a safety net (catches activity with no log output)

Runs as a launchd daemon alongside the face watcher.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure site-packages available under launchd
sys.path.insert(0, "/opt/homebrew/lib/python3.14/site-packages")

# ── Config ──
CONFIG_PATH = Path.home() / ".agent-face" / "config.json"
STATUS_DIR = "/tmp/clawdbot"
STATUS_FILE = f"{STATUS_DIR}/agent-status.json"
LOG_FILE = f"{STATUS_DIR}/gateway-signal-debug.log"
LOG_DIR = "/tmp/clawdbot"
ERR_LOG_PATH = Path.home() / ".clawdbot/logs/gateway.err.log"

CPU_POLL_INTERVAL = 3.0     # seconds between CPU checks
CPU_ACTIVE_THRESHOLD = 0.5  # % CPU above this = active
IDLE_AFTER = 15             # seconds of no activity before removing signal
STALE_SIGNAL_AGE = 25       # refresh signal if older than this while active
LOOP_SLEEP = 0.15           # main loop sleep (fast enough to tail logs)

# Log line → (state, detail) mapping
# Checked in order — put most specific patterns first
ACTIVITY_PATTERNS = [
    ("web_search", "searching", "Searching the web"),
    ("web_fetch", "reading", "Reading a webpage"),
    ("[tools] message", "composing", "Writing on Discord"),
    ("chat.send", "composing", "Sending a reply"),
    ("chat.reply", "composing", "Sending a reply"),
    ("[tools] exec", "executing", "Running a command"),
    ("[tools] read", "reading", "Reading a file"),
    ("[tools] write", "coding", "Writing a file"),
    ("[tools] edit", "coding", "Editing a file"),
    ("memory_search", "searching", "Searching memory"),
    ("memory_get", "reading", "Reading memory"),
    ("[tools] browser", "searching", "Using the browser"),
    ("[tools] cron", "thinking", "Managing schedules"),
    ("sessions_spawn", "thinking", "Spawning a sub-agent"),
    ("[tools] image", "thinking", "Analysing an image"),
    ("Slow listener.*Discord", "composing", "Processing a response"),
    ("DiscordMessageListener", "composing", "Replying on Discord"),
    ("discord-auto-reply", "reading", "Reading Discord"),
]


def log(msg):
    os.makedirs(STATUS_DIR, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")


def load_config():
    agent_name = "unknown"
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
            agent_name = cfg.get("agent", {}).get("name", "unknown")
        except Exception:
            pass
    return agent_name


def find_gateway_pid():
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "clawdbot-gateway" in line and "grep" not in line:
                return int(line.split()[1])
    except Exception:
        pass
    return None


def get_cpu_usage(pid):
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "%cpu="],
            capture_output=True, text=True, timeout=5
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def get_dated_log_path():
    today = datetime.now().strftime("%Y-%m-%d")
    return f"{LOG_DIR}/clawdbot-{today}.log"


def match_activity(line):
    """Match a log line against known activity patterns.
    Returns (state, detail) or None."""
    for pattern, state, detail in ACTIVITY_PATTERNS:
        if pattern in line:
            return state, detail
    return None


def write_signal(agent_name, state, detail=""):
    os.makedirs(STATUS_DIR, exist_ok=True)
    ts = int(time.time())
    data = {"agent": agent_name, "state": state, "detail": detail, "ts": ts}
    tmp = f"{STATUS_FILE}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, STATUS_FILE)


def remove_signal():
    try:
        os.remove(STATUS_FILE)
    except OSError:
        pass


def open_tail(path):
    """Open a file for tailing (seek to end). Returns file handle or None."""
    p = Path(path)
    if p.exists():
        f = open(p, "r")
        f.seek(0, 2)
        return f
    return None


def main():
    agent_name = load_config()
    log(f"Gateway signal writer started for {agent_name}")

    # State tracking
    last_active_time = 0
    was_active = False
    last_write_time = 0
    last_state = ""
    last_detail = ""
    last_cpu_check = 0
    current_day = datetime.now().day

    # Open log files for tailing
    stdout_log = open_tail(get_dated_log_path())
    stderr_log = open_tail(str(ERR_LOG_PATH))

    if stdout_log:
        log(f"Tailing stdout: {get_dated_log_path()}")
    if stderr_log:
        log(f"Tailing stderr: {ERR_LOG_PATH}")

    while True:
        try:
            now = time.time()

            # ── Day rollover: re-open dated log ──
            if datetime.now().day != current_day:
                if stdout_log:
                    stdout_log.close()
                stdout_log = None
                current_day = datetime.now().day
                new_path = get_dated_log_path()
                # Wait briefly for new log to appear
                for _ in range(10):
                    if Path(new_path).exists():
                        stdout_log = open_tail(new_path)
                        log(f"Day rollover — now tailing: {new_path}")
                        break
                    time.sleep(1)

            # ── Tail log files for specific activity ──
            log_activity = None

            if stdout_log:
                line = stdout_log.readline()
                if line:
                    match = match_activity(line)
                    if match:
                        log_activity = match

            if stderr_log:
                err_line = stderr_log.readline()
                if err_line:
                    match = match_activity(err_line)
                    if match:
                        log_activity = match
            else:
                # Try to open stderr if it wasn't available at start
                if ERR_LOG_PATH.exists():
                    stderr_log = open_tail(str(ERR_LOG_PATH))

            # ── CPU check (safety net, runs every CPU_POLL_INTERVAL) ──
            cpu_active = False
            if now - last_cpu_check >= CPU_POLL_INTERVAL:
                last_cpu_check = now
                pid = find_gateway_pid()
                if pid:
                    cpu = get_cpu_usage(pid)
                    cpu_active = cpu > CPU_ACTIVE_THRESHOLD

            # ── Determine if active ──
            is_active = log_activity is not None or cpu_active

            if is_active:
                last_active_time = now

                # Use log-detected detail if available, otherwise generic
                if log_activity:
                    state, detail = log_activity
                else:
                    state, detail = "thinking", "Working"

                # Check if someone else wrote a more specific signal recently
                # (e.g. write_status.sh called directly) — don't overwrite it
                try:
                    if Path(STATUS_FILE).exists():
                        existing = json.loads(Path(STATUS_FILE).read_text())
                        ext_ts = existing.get("ts", 0)
                        ext_age = now - ext_ts
                        ext_detail = existing.get("detail", "")
                        # If external signal is fresh (<STALE_SIGNAL_AGE) and more
                        # specific than "Working", don't overwrite it
                        if (ext_age < STALE_SIGNAL_AGE
                                and ext_detail != "Working"
                                and ext_detail != ""
                                and state == "thinking"
                                and detail == "Working"):
                            # Respect the more specific external signal
                            was_active = True
                            continue
                except Exception:
                    pass

                # Write signal if state changed, or signal getting stale
                state_changed = (state != last_state or detail != last_detail)
                signal_stale = (now - last_write_time > STALE_SIGNAL_AGE)

                if not was_active or state_changed or signal_stale:
                    if not was_active:
                        log(f"  ⚡ Active")
                    if state_changed:
                        log(f"  → {state}: {detail}")
                    write_signal(agent_name, state, detail)
                    last_write_time = now
                    last_state = state
                    last_detail = detail

                was_active = True

            elif was_active:
                # Grace period before going idle
                idle_secs = now - last_active_time
                if idle_secs > IDLE_AFTER:
                    log(f"  💤 Idle after {idle_secs:.0f}s")
                    remove_signal()
                    was_active = False
                    last_state = ""
                    last_detail = ""
                elif now - last_write_time > STALE_SIGNAL_AGE:
                    # Keep signal fresh during grace period
                    write_signal(agent_name, last_state, last_detail)
                    last_write_time = now

        except Exception as e:
            log(f"Error: {e}")

        time.sleep(LOOP_SLEEP)


if __name__ == "__main__":
    main()
