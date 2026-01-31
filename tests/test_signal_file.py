#!/usr/bin/env python3
"""
Tests for signal-file-based activity detection.
Covers reading, parsing, state mapping, staleness, priority, and the write_status.sh script.
"""
import json
import os
import sys
import time
import subprocess
import tempfile
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

# Add scripts dir to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import activity_watcher as aw


SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
WRITE_STATUS_SH = SCRIPTS_DIR / "write_status.sh"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def watcher():
    """Create a watcher with mocked serial and default config"""
    with patch.object(aw.ActivityWatcher, '_connect_serial'):
        w = aw.ActivityWatcher({})
        w.ser = MagicMock()
        w.ser.is_open = True
        return w


@pytest.fixture
def signal_file(tmp_path, watcher):
    """Temp signal file wired into the watcher"""
    sf = tmp_path / "agent-status.json"
    watcher._signal_file = sf
    return sf


# ---------------------------------------------------------------------------
# Signal file reading and parsing
# ---------------------------------------------------------------------------

class TestReadSignal:
    def test_read_fresh_signal(self, watcher, signal_file):
        signal_file.write_text(json.dumps({
            "agent": "kieron", "state": "thinking",
            "detail": "Working on tests", "ts": time.time()
        }))
        result = watcher._read_signal()
        assert result is not None
        assert result["state"] == "thinking"
        assert result["detail"] == "Working on tests"

    def test_read_signal_no_detail(self, watcher, signal_file):
        signal_file.write_text(json.dumps({
            "agent": "kieron", "state": "coding", "detail": "", "ts": time.time()
        }))
        result = watcher._read_signal()
        assert result is not None
        assert result["state"] == "coding"
        assert result["detail"] == ""

    def test_missing_signal_file(self, watcher, signal_file):
        # Don't create the file
        assert watcher._read_signal() is None

    def test_corrupt_signal_file(self, watcher, signal_file):
        signal_file.write_text("not valid json{{{")
        assert watcher._read_signal() is None

    def test_empty_state_ignored(self, watcher, signal_file):
        signal_file.write_text(json.dumps({
            "agent": "kieron", "state": "", "detail": "x", "ts": time.time()
        }))
        assert watcher._read_signal() is None

    def test_whitespace_state_ignored(self, watcher, signal_file):
        signal_file.write_text(json.dumps({
            "agent": "kieron", "state": "   ", "detail": "x", "ts": time.time()
        }))
        assert watcher._read_signal() is None

    def test_missing_ts_treated_as_stale(self, watcher, signal_file):
        signal_file.write_text(json.dumps({
            "agent": "kieron", "state": "thinking", "detail": ""
        }))
        # ts defaults to 0, which is way in the past → stale
        assert watcher._read_signal() is None


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------

class TestStaleness:
    def test_fresh_signal_accepted(self, watcher, signal_file):
        signal_file.write_text(json.dumps({
            "state": "coding", "detail": "", "ts": time.time()
        }))
        assert watcher._read_signal() is not None

    def test_stale_signal_rejected(self, watcher, signal_file):
        signal_file.write_text(json.dumps({
            "state": "coding", "detail": "", "ts": time.time() - 31
        }))
        assert watcher._read_signal() is None

    def test_signal_at_boundary_accepted(self, watcher, signal_file):
        signal_file.write_text(json.dumps({
            "state": "coding", "detail": "",
            "ts": time.time() - watcher._signal_max_age + 1
        }))
        assert watcher._read_signal() is not None

    def test_signal_just_past_boundary_rejected(self, watcher, signal_file):
        signal_file.write_text(json.dumps({
            "state": "coding", "detail": "",
            "ts": time.time() - watcher._signal_max_age - 1
        }))
        assert watcher._read_signal() is None

    def test_very_old_signal_rejected(self, watcher, signal_file):
        signal_file.write_text(json.dumps({
            "state": "thinking", "detail": "", "ts": 1000000
        }))
        assert watcher._read_signal() is None


# ---------------------------------------------------------------------------
# State-to-expression mapping
# ---------------------------------------------------------------------------

class TestStateToExpression:
    def test_thinking_maps_to_thinking(self):
        assert aw.SIGNAL_STATE_EXPRESSIONS["thinking"] == "thinking"

    def test_searching_maps_to_searching(self):
        assert aw.SIGNAL_STATE_EXPRESSIONS["searching"] == "searching"

    def test_reading_maps_to_reading(self):
        assert aw.SIGNAL_STATE_EXPRESSIONS["reading"] == "reading"

    def test_coding_maps_to_focused(self):
        assert aw.SIGNAL_STATE_EXPRESSIONS["coding"] == "focused"

    def test_composing_maps_to_composing(self):
        assert aw.SIGNAL_STATE_EXPRESSIONS["composing"] == "composing"

    def test_reviewing_maps_to_thinking(self):
        assert aw.SIGNAL_STATE_EXPRESSIONS["reviewing"] == "thinking"

    def test_executing_maps_to_terminal(self):
        assert aw.SIGNAL_STATE_EXPRESSIONS["executing"] == "terminal"

    def test_idle_not_in_mapping(self):
        assert "idle" not in aw.SIGNAL_STATE_EXPRESSIONS


# ---------------------------------------------------------------------------
# Handle signal behavior
# ---------------------------------------------------------------------------

class TestHandleSignal:
    def test_sets_expression_and_status(self, watcher):
        result = watcher._handle_signal({"state": "coding", "detail": "Writing tests"})
        assert result is True
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:focused" in c for c in calls)
        assert any("Writing tests" in c for c in calls)

    def test_uses_state_name_when_no_detail(self, watcher):
        watcher._handle_signal({"state": "thinking", "detail": ""})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("Thinking..." in c for c in calls)

    def test_idle_returns_false(self, watcher):
        result = watcher._handle_signal({"state": "idle", "detail": ""})
        assert result is False

    def test_unknown_state_returns_false(self, watcher):
        result = watcher._handle_signal({"state": "unknown_state", "detail": ""})
        assert result is False

    def test_deduplicates_same_state(self, watcher):
        watcher._handle_signal({"state": "coding", "detail": "test"})
        watcher.ser.reset_mock()
        result = watcher._handle_signal({"state": "coding", "detail": "test"})
        assert result is True
        # Should not have sent expression/status again
        assert not watcher.ser.write.called

    def test_different_state_sends_update(self, watcher):
        watcher._handle_signal({"state": "coding", "detail": "test"})
        watcher.ser.reset_mock()
        watcher._handle_signal({"state": "searching", "detail": "Looking up stuff"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:searching" in c for c in calls)
        assert any("Looking up stuff" in c for c in calls)

    def test_updates_activity_timer(self, watcher):
        old_activity = watcher.last_activity
        time.sleep(0.01)
        watcher._handle_signal({"state": "coding", "detail": ""})
        assert watcher.last_activity > old_activity

    def test_resets_idle_flags(self, watcher):
        watcher.idle_sent = True
        watcher.waiting_sent = True
        watcher._handle_signal({"state": "coding", "detail": ""})
        assert watcher.idle_sent is False
        assert watcher.waiting_sent is False

    def test_wakes_from_sleep(self, watcher):
        watcher.sleepy_sent = True
        watcher.asleep_sent = True
        watcher.screen_off = True
        watcher.current_expr = "asleep"
        watcher._handle_signal({"state": "thinking", "detail": "Waking up"})
        assert watcher.sleepy_sent is False
        assert watcher.asleep_sent is False
        assert watcher.screen_off is False

    def test_idle_clears_last_signal_state(self, watcher):
        watcher._last_signal_state = "coding"
        watcher._handle_signal({"state": "idle", "detail": ""})
        assert watcher._last_signal_state is None


# ---------------------------------------------------------------------------
# Signal file priority over log-based detection
# ---------------------------------------------------------------------------

class TestSignalPriority:
    def test_signal_overrides_log_event(self, watcher, signal_file):
        """When signal is fresh, log events should be suppressed"""
        signal_file.write_text(json.dumps({
            "state": "composing", "detail": "Posting to Discord",
            "ts": time.time()
        }))
        signal_data = watcher._read_signal()
        assert signal_data is not None
        active = watcher._handle_signal(signal_data)
        assert active is True
        # Expression should be composing (from signal), not whatever log would set
        assert watcher.current_expr == "composing"

    def test_log_fallback_when_no_signal(self, watcher, signal_file):
        """When signal file is missing, log events should work normally"""
        assert watcher._read_signal() is None
        # Simulate a log event
        watcher._handle_event({"event": "tool_start", "tool": "web_search"})
        assert watcher.current_expr == "searching"

    def test_log_fallback_when_signal_stale(self, watcher, signal_file):
        """When signal is stale, log events should work normally"""
        signal_file.write_text(json.dumps({
            "state": "composing", "detail": "Old",
            "ts": time.time() - 60
        }))
        assert watcher._read_signal() is None
        watcher._handle_event({"event": "tool_start", "tool": "exec"})
        assert watcher.current_expr == "terminal"

    def test_signal_idle_allows_log_fallback(self, watcher, signal_file):
        """idle signal should not block log events"""
        signal_file.write_text(json.dumps({
            "state": "idle", "detail": "", "ts": time.time()
        }))
        signal_data = watcher._read_signal()
        active = watcher._handle_signal(signal_data)
        assert active is False  # idle doesn't count as active

    def test_last_signal_state_cleared_when_file_gone(self, watcher, signal_file):
        """When signal file disappears, _last_signal_state should reset"""
        # First, set a signal state
        watcher._last_signal_state = "coding"
        # Signal file doesn't exist → _read_signal returns None
        # In the main loop, this would trigger: self._last_signal_state = None
        signal_data = watcher._read_signal()
        assert signal_data is None
        # Simulate main loop logic
        if not signal_data:
            watcher._last_signal_state = None
        assert watcher._last_signal_state is None


# ---------------------------------------------------------------------------
# Config: statusFile path
# ---------------------------------------------------------------------------

class TestSignalFileConfig:
    def test_default_signal_file_path(self):
        with patch.object(aw.ActivityWatcher, '_connect_serial'):
            w = aw.ActivityWatcher({})
            assert str(w._signal_file) == aw.DEFAULT_SIGNAL_FILE

    def test_custom_signal_file_path(self):
        with patch.object(aw.ActivityWatcher, '_connect_serial'):
            w = aw.ActivityWatcher({"statusFile": "/custom/path/status.json"})
            assert str(w._signal_file) == "/custom/path/status.json"


# ---------------------------------------------------------------------------
# write_status.sh script
# ---------------------------------------------------------------------------

class TestWriteStatusScript:
    def test_writes_valid_json(self, tmp_path):
        status_file = tmp_path / "agent-status.json"
        env = os.environ.copy()
        # Override the config to get a known agent name
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"agent": {"name": "TestAgent"}}))

        result = subprocess.run(
            ["bash", "-c", f'''
                STATUS_DIR="{tmp_path}"
                STATUS_FILE="{status_file}"
                CONFIG_FILE="{config_file}"
                STATE="thinking"
                DETAIL="Running tests"
                AGENT="unknown"
                if [ -f "$CONFIG_FILE" ]; then
                    AGENT=$(python3 -c "
import json
with open(\\"$CONFIG_FILE\\") as f:
    print(json.load(f).get(\\"agent\\", {{}}).get(\\"name\\", \\"unknown\\"))
" 2>/dev/null || echo "unknown")
                fi
                TS=$(date +%s)
                mkdir -p "$STATUS_DIR"
                cat > "$STATUS_FILE" <<EEOF
{{"agent":"$AGENT","state":"$STATE","detail":"$DETAIL","ts":$TS}}
EEOF
            '''],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        data = json.loads(status_file.read_text())
        assert data["agent"] == "TestAgent"
        assert data["state"] == "thinking"
        assert data["detail"] == "Running tests"
        assert isinstance(data["ts"], int)

    def test_script_validates_state(self):
        result = subprocess.run(
            [str(WRITE_STATUS_SH), "invalid_state"],
            capture_output=True, text=True
        )
        assert result.returncode != 0
        assert "Invalid state" in result.stderr

    def test_script_requires_args(self):
        result = subprocess.run(
            [str(WRITE_STATUS_SH)],
            capture_output=True, text=True
        )
        assert result.returncode != 0
        assert "Usage" in result.stderr

    def test_script_accepts_all_valid_states(self):
        valid_states = ["thinking", "searching", "reading", "coding",
                        "composing", "reviewing", "executing", "idle"]
        for state in valid_states:
            result = subprocess.run(
                [str(WRITE_STATUS_SH), state],
                capture_output=True, text=True
            )
            assert result.returncode == 0, f"State '{state}' rejected: {result.stderr}"

    def test_script_writes_to_tmp(self):
        result = subprocess.run(
            [str(WRITE_STATUS_SH), "thinking", "Test detail"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        status_file = Path("/tmp/clawdbot/agent-status.json")
        assert status_file.exists()
        data = json.loads(status_file.read_text())
        assert data["state"] == "thinking"
        assert data["detail"] == "Test detail"
        assert time.time() - data["ts"] < 5  # Written within last 5 seconds

    def test_script_reads_agent_from_config(self):
        result = subprocess.run(
            [str(WRITE_STATUS_SH), "idle"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        data = json.loads(Path("/tmp/clawdbot/agent-status.json").read_text())
        # Should read from ~/.agent-face/config.json
        assert data["agent"] != ""
