#!/bin/bash
# Start the face display activity watcher (kill existing first)
pkill -f activity_watcher 2>/dev/null
sleep 1
nohup /opt/homebrew/bin/python3 -u /Users/kieron/Documents/Apps/agent-face-display/scripts/activity_watcher.py >> /tmp/clawdbot/face-watcher.log 2>&1 &
echo "Face watcher started (PID: $!)"
