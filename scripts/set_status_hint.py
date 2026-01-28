#!/usr/bin/env python3
"""
Set a status hint for the activity watcher.

Usage:
  python3 set_status_hint.py "Researching Arsenal's 2023 season"
  python3 set_status_hint.py --clear

The watcher picks this up and shows it on the ESP32 display
instead of generic tool labels like "Searching the web".
One agent per machine, one hint file. Simple.
"""
import json, os, sys, time
from pathlib import Path

STATUS_HINT_FILE = "/tmp/clawdbot/status-hint.json"

def main():
    os.makedirs(os.path.dirname(STATUS_HINT_FILE), exist_ok=True)

    if "--clear" in sys.argv:
        Path(STATUS_HINT_FILE).unlink(missing_ok=True)
        print("Hint cleared")
        return

    text = next((a for a in sys.argv[1:] if not a.startswith("--")), None)
    if not text:
        print("Usage: set_status_hint.py \"Your status text\"")
        sys.exit(1)

    Path(STATUS_HINT_FILE).write_text(json.dumps({"text": text, "ts": time.time()}))
    print(f"Hint set: {text}")


if __name__ == "__main__":
    main()
