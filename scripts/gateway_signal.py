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
                # Only write signal if state changed or signal is getting stale
                if not was_active or (now - last_write_time > STALE_SIGNAL_AGE):
                    if not was_active:
                        log(f"  ⚡ Active (CPU: {cpu:.1f}%, log_changed: {log_changed})")
                    write_signal(agent_name, "thinking", "Working...")
                    last_write_time = now
                was_active = True

            elif was_active:
                # Was active, now idle — keep signal fresh for a bit then remove
                idle_secs = now - last_active_time
                if idle_secs > IDLE_AFTER:
                    if was_active:
                        log(f"  💤 Idle after {idle_secs:.0f}s")
                    remove_signal()
                    was_active = False
                elif now - last_write_time > STALE_SIGNAL_AGE:
                    # Still in grace period, keep signal fresh
                    write_signal(agent_name, "thinking", "Working...")
                    last_write_time = now

        except Exception as e:
            log(f"Error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
