#!/usr/bin/env python3
"""
Gateway Activity Signal Writer
Monitors the Clawdbot gateway process for activity (CPU usage, network connections,
log file changes) and writes the agent-status signal file for the face watcher.

This bridges the gap between gateway activity and face display state — the gateway
doesn't log most agent activity (tool calls, responses), so the watcher needs
another signal source.

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
ERR_LOG = str(Path.home() / ".clawdbot/logs/gateway.err.log")

POLL_INTERVAL = 2.0        # seconds between checks
CPU_ACTIVE_THRESHOLD = 0.5 # % CPU above this = active
IDLE_AFTER = 15            # seconds of no activity before writing idle
STALE_SIGNAL_AGE = 25      # write signal if older than this (keep it fresh while active)

# State → detail mapping for signal file
STATE_DETAILS = {
    "web_search": ("searching", "Searching the web"),
    "web_fetch": ("reading", "Reading a webpage"),
    "message": ("composing", "Writing on Discord"),
    "chat.send": ("composing", "Sending a reply"),
    "chat.reply": ("composing", "Sending a reply"),
    "exec": ("executing", "Running a command"),
    "read": ("reading", "Reading a file"),
    "write": ("coding", "Writing a file"),
    "edit": ("coding", "Editing a file"),
    "memory_search": ("searching", "Searching memory"),
    "memory_get": ("reading", "Reading memory"),
    "browser": ("searching", "Using the browser"),
    "cron": ("thinking", "Managing cron jobs"),
    "sessions_spawn": ("thinking", "Spawning a sub-agent"),
    "image": ("thinking", "Analysing an image"),
    "discord": ("composing", "Working in Discord"),
    "Slow listener": ("thinking", "Processing a response"),
}


def log(msg):
    os.makedirs(STATUS_DIR, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")


def load_config():
    """Load agent name from config"""
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
    """Find the clawdbot-gateway process PID"""
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
    """Get CPU % for a process"""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "%cpu="],
            capture_output=True, text=True, timeout=5
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def get_log_mtime():
    """Get the most recent modification time across gateway logs"""
    latest = 0
    today = datetime.now().strftime("%Y-%m-%d")
    paths = [
        f"{LOG_DIR}/clawdbot-{today}.log",
        ERR_LOG,
    ]
    for p in paths:
        try:
            mt = os.path.getmtime(p)
            if mt > latest:
                latest = mt
        except OSError:
            pass
    return latest


def get_active_connections(pid):
    """Count active network connections (API calls to Anthropic etc.)"""
    try:
        result = subprocess.run(
            ["lsof", "-p", str(pid), "-iTCP", "-sTCP:ESTABLISHED", "-Fn"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.count("\nn")
    except Exception:
        return 0


def detect_activity_detail(log_dir, err_log):
    """Read recent log entries to determine what the agent is currently doing.
    Returns (state, detail) tuple."""
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = f"{log_dir}/clawdbot-{today}.log"

    # Check both logs, most recent entries first
    recent_lines = []

    # Read last few lines of stderr (has tool info)
    try:
        with open(err_log, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", errors="replace")
            recent_lines.extend(tail.strip().splitlines()[-5:])
    except OSError:
        pass

    # Read last few lines of dated log
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", errors="replace")
            recent_lines.extend(tail.strip().splitlines()[-5:])
    except OSError:
        pass

    # Filter to entries from the last 30 seconds
    now = time.time()
    fresh_lines = []
    for line in recent_lines:
        # Try to extract timestamp
        m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", line)
        if m:
            try:
                from datetime import timezone
                ts_str = m.group(1)
                dt = datetime.fromisoformat(ts_str + "+00:00")
                age = now - dt.timestamp()
                if age < 30:
                    fresh_lines.append(line)
            except Exception:
                fresh_lines.append(line)  # Include if can't parse
        else:
            fresh_lines.append(line)

    # Search fresh lines for activity indicators (check most specific first)
    combined = " ".join(fresh_lines)
    for keyword, (state, detail) in STATE_DETAILS.items():
        if keyword in combined:
            return state, detail

    return "thinking", "Working"


def write_signal(agent_name, state, detail=""):
    """Write the agent status signal file atomically"""
    os.makedirs(STATUS_DIR, exist_ok=True)
    ts = int(time.time())
    data = {"agent": agent_name, "state": state, "detail": detail, "ts": ts}
    tmp = f"{STATUS_FILE}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, STATUS_FILE)


def remove_signal():
    """Remove signal file to let the watcher handle idle on its own"""
    try:
        os.remove(STATUS_FILE)
    except OSError:
        pass


def main():
    agent_name = load_config()
    log(f"Gateway signal writer started for {agent_name}")

    last_active_time = 0
    last_log_mtime = get_log_mtime()
    was_active = False
    last_write_time = 0
    last_state = ""

    while True:
        try:
            pid = find_gateway_pid()
            if not pid:
                if was_active:
                    log("Gateway process not found — removing signal")
                    remove_signal()
                    was_active = False
                time.sleep(POLL_INTERVAL * 5)
                continue

            # Check multiple activity indicators
            cpu = get_cpu_usage(pid)
            log_mtime = get_log_mtime()
            log_changed = log_mtime > last_log_mtime
            last_log_mtime = log_mtime

            # Consider active if CPU is above threshold or log just changed
            is_active = cpu > CPU_ACTIVE_THRESHOLD or log_changed

            now = time.time()

            if is_active:
                last_active_time = now
                # Detect what the agent is actually doing
                state, detail = detect_activity_detail(LOG_DIR, ERR_LOG)
                # Only write signal if state changed or signal is getting stale
                if not was_active or (now - last_write_time > STALE_SIGNAL_AGE) or state != last_state:
                    if not was_active:
                        log(f"  ⚡ Active (CPU: {cpu:.1f}%, log_changed: {log_changed})")
                    log(f"  → {state}: {detail}")
                    write_signal(agent_name, state, detail)
                    last_write_time = now
                    last_state = state
                was_active = True

            elif was_active:
                # Was active, now idle — keep signal fresh for a bit then remove
                idle_secs = now - last_active_time
                if idle_secs > IDLE_AFTER:
                    if was_active:
                        log(f"  💤 Idle after {idle_secs:.0f}s")
                    remove_signal()
                    was_active = False
                    last_state = ""
                elif now - last_write_time > STALE_SIGNAL_AGE:
                    # Still in grace period, keep signal fresh
                    state, detail = detect_activity_detail(LOG_DIR, ERR_LOG)
                    write_signal(agent_name, state, detail)
                    last_write_time = now
                    last_state = state

        except Exception as e:
            log(f"Error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
