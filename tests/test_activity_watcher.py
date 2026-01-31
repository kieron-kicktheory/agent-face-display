#!/usr/bin/env python3
"""
Tests for ActivityWatcher — the Mac-side log watcher that drives the ESP32 display.
Mocks serial and filesystem to test all logic paths.
"""
import json
import os
import sys
import time
import tempfile
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

# Add scripts dir to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import activity_watcher as aw


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
def custom_watcher():
    """Create a watcher with custom config"""
    config = {
        "agent": {"name": "Bobby", "serialPort": "/dev/cu.usbmodem99999"},
        "timeouts": {"waiting": 5, "idle": 60, "sleepy": 120, "asleep": 240, "screenOff": 360},
        "phrases": {
            "waiting": ["Ready for the whistle"],
            "idle": ["The first ninety minutes are the most important"],
        },
        "logFile": "/tmp/custom-log.log",
    }
    with patch.object(aw.ActivityWatcher, '_connect_serial'):
        w = aw.ActivityWatcher(config)
        w.ser = MagicMock()
        w.ser.is_open = True
        return w


@pytest.fixture
def hint_file(tmp_path, watcher):
    """Temp hint file"""
    hint = tmp_path / "status-hint.json"
    watcher._status_hint_file = hint
    yield hint


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestConfigLoading:
    def test_default_config_values(self, watcher):
        assert watcher.WAITING_TIMEOUT == aw.DEFAULT_TIMEOUTS["waiting"]
        assert watcher.IDLE_TIMEOUT == aw.DEFAULT_TIMEOUTS["idle"]
        assert watcher.SLEEPY_TIMEOUT == aw.DEFAULT_TIMEOUTS["sleepy"]
        assert watcher.ASLEEP_TIMEOUT == aw.DEFAULT_TIMEOUTS["asleep"]
        assert watcher.SCREEN_OFF_TIMEOUT == aw.DEFAULT_TIMEOUTS["screenOff"]
        assert watcher._agent_name == "Agent"
        assert watcher._serial_port == aw.DEFAULT_SERIAL_PORT

    def test_custom_config_values(self, custom_watcher):
        assert custom_watcher.WAITING_TIMEOUT == 5
        assert custom_watcher.IDLE_TIMEOUT == 60
        assert custom_watcher.SLEEPY_TIMEOUT == 120
        assert custom_watcher.ASLEEP_TIMEOUT == 240
        assert custom_watcher.SCREEN_OFF_TIMEOUT == 360
        assert custom_watcher._agent_name == "Bobby"
        assert custom_watcher._serial_port == "/dev/cu.usbmodem99999"
        assert custom_watcher.WAITING_PHRASES == ["Ready for the whistle"]
        assert custom_watcher.IDLE_PHRASES == ["The first ninety minutes are the most important"]

    def test_custom_log_file(self, custom_watcher):
        path = custom_watcher._get_log_path()
        assert str(path) == "/tmp/custom-log.log"

    def test_load_config_missing_file(self, tmp_path):
        with patch.object(aw, 'CONFIG_PATH', tmp_path / "nonexistent.json"):
            cfg = aw.load_config()
            assert cfg == {}

    def test_load_config_valid_file(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"agent": {"name": "Test", "serialPort": "/dev/test"}}))
        with patch.object(aw, 'CONFIG_PATH', cfg_file):
            cfg = aw.load_config()
            assert cfg["agent"]["name"] == "Test"

    def test_load_config_invalid_json(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("not json{{{")
        with patch.object(aw, 'CONFIG_PATH', cfg_file):
            cfg = aw.load_config()
            assert cfg == {}


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

class TestParseLine:
    def test_tool_start(self, watcher):
        line = json.dumps({"1": "tool start: session=abc tool=web_search"})
        result = watcher._parse_line(line)
        assert result == {"event": "tool_start", "tool": "web_search"}

    def test_tool_end(self, watcher):
        line = json.dumps({"1": "tool end: session=abc tool=exec"})
        result = watcher._parse_line(line)
        assert result == {"event": "tool_end", "tool": "exec"}

    def test_run_start(self, watcher):
        line = json.dumps({"1": "run start: session=abc model=claude"})
        result = watcher._parse_line(line)
        assert result == {"event": "run_start"}

    def test_run_end(self, watcher):
        line = json.dumps({"1": "run end: session=abc"})
        result = watcher._parse_line(line)
        assert result == {"event": "run_end"}

    def test_run_complete(self, watcher):
        line = json.dumps({"1": "run complete"})
        result = watcher._parse_line(line)
        assert result == {"event": "run_end"}

    def test_irrelevant_line(self, watcher):
        line = json.dumps({"1": "some random log message"})
        result = watcher._parse_line(line)
        assert result is None

    def test_invalid_json(self, watcher):
        result = watcher._parse_line("not json at all")
        assert result is None

    def test_empty_message(self, watcher):
        line = json.dumps({"1": ""})
        result = watcher._parse_line(line)
        assert result is None

    def test_missing_message_key(self, watcher):
        line = json.dumps({"level": "info", "msg": "something"})
        result = watcher._parse_line(line)
        assert result is None

    def test_non_string_message(self, watcher):
        line = json.dumps({"1": 12345})
        result = watcher._parse_line(line)
        assert result is None


# ---------------------------------------------------------------------------
# Status hint system
# ---------------------------------------------------------------------------

class TestStatusHints:
    def test_read_fresh_hint(self, watcher, hint_file):
        hint_file.write_text(json.dumps({"text": "Reviewing PR #35", "ts": time.time()}))
        assert watcher._read_hint() == "Reviewing PR #35"

    def test_stale_hint_ignored(self, watcher, hint_file):
        hint_file.write_text(json.dumps({"text": "Old stuff", "ts": time.time() - 60}))
        assert watcher._read_hint() is None

    def test_missing_hint_file(self, watcher, hint_file):
        # Don't create the file
        assert watcher._read_hint() is None

    def test_empty_text_hint(self, watcher, hint_file):
        hint_file.write_text(json.dumps({"text": "", "ts": time.time()}))
        assert watcher._read_hint() is None

    def test_whitespace_only_hint(self, watcher, hint_file):
        hint_file.write_text(json.dumps({"text": "   ", "ts": time.time()}))
        assert watcher._read_hint() is None

    def test_invalid_json_hint(self, watcher, hint_file):
        hint_file.write_text("not json")
        assert watcher._read_hint() is None

    def test_hint_at_boundary(self, watcher, hint_file):
        hint_file.write_text(json.dumps({"text": "Boundary", "ts": time.time() - watcher._hint_max_age + 1}))
        assert watcher._read_hint() == "Boundary"

    def test_hint_just_past_boundary(self, watcher, hint_file):
        hint_file.write_text(json.dumps({"text": "Expired", "ts": time.time() - watcher._hint_max_age - 1}))
        assert watcher._read_hint() is None


# ---------------------------------------------------------------------------
# Serial communication
# ---------------------------------------------------------------------------

class TestSendStatus:
    def test_sends_status(self, watcher):
        watcher.send_status("Hello")
        watcher.ser.write.assert_called_with(b"S:Hello\n")
        watcher.ser.flush.assert_called()

    def test_deduplicates_same_status(self, watcher):
        watcher.send_status("Hello")
        watcher.send_status("Hello")
        assert watcher.ser.write.call_count == 1

    def test_sends_different_status(self, watcher):
        watcher.send_status("Hello")
        watcher.send_status("World")
        assert watcher.ser.write.call_count == 2

    def test_reconnects_on_serial_error(self, watcher):
        from serial import SerialException
        watcher.ser.write.side_effect = SerialException("disconnected")
        with patch.object(watcher, '_connect_serial') as mock_connect:
            watcher.send_status("Test")
            mock_connect.assert_called_once()


class TestSendExpression:
    def test_sends_expression(self, watcher):
        watcher.send_expression("sleepy")
        watcher.ser.write.assert_called_with(b"E:sleepy\n")

    def test_deduplicates_same_expression(self, watcher):
        watcher.send_expression("sleepy")
        watcher.send_expression("sleepy")
        assert watcher.ser.write.call_count == 1

    def test_sends_different_expression(self, watcher):
        watcher.send_expression("sleepy")
        watcher.send_expression("normal")
        assert watcher.ser.write.call_count == 2


class TestSendScreen:
    def test_screen_off(self, watcher):
        watcher.send_screen(False)
        watcher.ser.write.assert_called_with(b"SCREEN:DIM:10\n")
        assert watcher.screen_off is True

    def test_screen_on(self, watcher):
        watcher.screen_off = True
        watcher.send_screen(True)
        watcher.ser.write.assert_called_with(b"SCREEN:ON\n")
        assert watcher.screen_off is False

    def test_reconnects_on_error(self, watcher):
        from serial import SerialException
        watcher.ser.write.side_effect = SerialException("gone")
        with patch.object(watcher, '_connect_serial') as mock_connect:
            watcher.send_screen(False)
            mock_connect.assert_called_once()


class TestSendIdleStatus:
    def test_pads_short_text(self, watcher):
        watcher._send_idle_status("Hi")
        call_args = watcher.ser.write.call_args[0][0].decode()
        payload = call_args[2:-1]
        assert payload.startswith("Hi...")
        assert len(payload) >= 25

    def test_long_text_not_padded(self, watcher):
        long_text = "A" * 30
        watcher._send_idle_status(long_text)
        call_args = watcher.ser.write.call_args[0][0].decode()
        payload = call_args[2:-1]
        assert payload == long_text + "..."


class TestClearStatus:
    def test_clears(self, watcher):
        watcher.clear_status()
        watcher.ser.write.assert_called_with(b"CLEAR\n")
        assert watcher.current_status == ""

    def test_handles_closed_serial(self, watcher):
        watcher.ser.is_open = False
        watcher.clear_status()  # Should not raise


# ---------------------------------------------------------------------------
# Event handling
# ---------------------------------------------------------------------------

class TestHandleEvent:
    def test_tool_start_sends_status(self, watcher):
        watcher._handle_event({"event": "tool_start", "tool": "web_search"})
        assert watcher.ser.write.called
        call = watcher.ser.write.call_args[0][0].decode()
        assert call.startswith("S:")
        assert call.endswith("...\n")

    def test_tool_start_with_hint(self, watcher, hint_file):
        hint_file.write_text(json.dumps({"text": "Scouting Arsenal", "ts": time.time()}))
        watcher._handle_event({"event": "tool_start", "tool": "web_search"})
        call = watcher.ser.write.call_args[0][0].decode()
        assert "Scouting Arsenal" in call

    def test_run_start_shows_thinking(self, watcher):
        watcher._handle_event({"event": "run_start"})
        watcher.ser.write.assert_called_with(b"S:Thinking...\n")

    def test_run_end_sends_done(self, watcher):
        watcher.current_expr = "thinking"
        watcher._handle_event({"event": "run_end"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        status_calls = [c for c in calls if c.startswith("S:")]
        assert any(w in status_calls[0] for w in ["Done", "Finished", "All done", "Wrapped up"])

    def test_skips_process_tool(self, watcher):
        watcher._handle_event({"event": "tool_start", "tool": "process"})
        assert not watcher.ser.write.called

    def test_unknown_tool_uses_name(self, watcher):
        watcher._handle_event({"event": "tool_start", "tool": "unknown_tool"})
        call = watcher.ser.write.call_args[0][0].decode()
        assert "unknown_tool" in call

    def test_resets_idle_on_activity(self, watcher):
        watcher.idle_sent = True
        watcher._handle_event({"event": "tool_start", "tool": "read"})
        assert watcher.idle_sent is False

    def test_wakes_from_sleep(self, watcher):
        watcher.sleepy_sent = True
        watcher.asleep_sent = True
        watcher.current_expr = "asleep"
        watcher._handle_event({"event": "tool_start", "tool": "read"})
        assert watcher.sleepy_sent is False
        assert watcher.asleep_sent is False
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:normal" in c for c in calls)

    def test_turns_screen_on_when_off(self, watcher):
        watcher.screen_off = True
        watcher._handle_event({"event": "tool_start", "tool": "read"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("SCREEN:ON" in c for c in calls)
        assert watcher.screen_off is False


# ---------------------------------------------------------------------------
# Streak detection
# ---------------------------------------------------------------------------

class TestStreaks:
    def test_edit_streak(self, watcher):
        for _ in range(3):
            watcher._handle_event({"event": "tool_start", "tool": "edit"})
        call = watcher.ser.write.call_args[0][0].decode()
        text = call[2:].rstrip(".\n").rstrip(".")
        assert text in ["Deep in the code", "Refactoring away", "Lots of edits"]

    def test_exec_streak(self, watcher):
        for _ in range(3):
            watcher._handle_event({"event": "tool_start", "tool": "exec"})
        call = watcher.ser.write.call_args[0][0].decode()
        text = call[2:].rstrip(".\n").rstrip(".")
        assert text in ["Running tests", "Debugging", "Busy in terminal"]

    def test_search_streak(self, watcher):
        for _ in range(2):
            watcher._handle_event({"event": "tool_start", "tool": "web_search"})
        call = watcher.ser.write.call_args[0][0].decode()
        text = call[2:].rstrip(".\n").rstrip(".")
        assert text in ["Down a rabbit hole", "Deep research mode"]

    def test_streak_resets_on_different_tool(self, watcher):
        watcher._handle_event({"event": "tool_start", "tool": "edit"})
        watcher._handle_event({"event": "tool_start", "tool": "edit"})
        watcher._handle_event({"event": "tool_start", "tool": "read"})
        assert watcher._tool_streak == "read"
        assert watcher._streak_count == 1


# ---------------------------------------------------------------------------
# Tool labels
# ---------------------------------------------------------------------------

class TestToolLabels:
    def test_all_known_tools_have_labels(self):
        for tool, labels in aw.TOOL_LABELS.items():
            assert len(labels) >= 1, f"{tool} has no labels"
            for label in labels:
                assert isinstance(label, str) and len(label) > 0

    def test_known_tool_uses_label(self, watcher):
        watcher._handle_event({"event": "tool_start", "tool": "tts"})
        call = watcher.ser.write.call_args[0][0].decode()
        text = call[2:].rstrip(".\n").rstrip(".")
        assert text in aw.TOOL_LABELS["tts"]


# ---------------------------------------------------------------------------
# Expression mapping
# ---------------------------------------------------------------------------

class TestExpressionMapping:
    def test_edit_sends_focused(self, watcher):
        watcher._handle_event({"event": "tool_start", "tool": "edit"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:focused" in c for c in calls)

    def test_read_sends_reading(self, watcher):
        watcher._handle_event({"event": "tool_start", "tool": "read"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:reading" in c for c in calls)

    def test_web_search_sends_searching(self, watcher):
        watcher._handle_event({"event": "tool_start", "tool": "web_search"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:searching" in c for c in calls)

    def test_exec_sends_terminal(self, watcher):
        watcher._handle_event({"event": "tool_start", "tool": "exec"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:terminal" in c for c in calls)

    def test_run_start_sends_thinking(self, watcher):
        watcher._handle_event({"event": "run_start"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:thinking" in c for c in calls)

    def test_run_end_sends_done(self, watcher):
        watcher.current_expr = "thinking"
        watcher._handle_event({"event": "run_end"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:done" in c for c in calls)

    def test_unknown_tool_sends_normal(self, watcher):
        watcher.current_expr = "focused"
        watcher._handle_event({"event": "tool_start", "tool": "some_new_tool"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:normal" in c for c in calls)

    def test_sustained_work_sends_stressed(self, watcher):
        watcher._work_start = time.time() - aw.SUSTAINED_WORK_THRESHOLD - 1
        watcher._handle_event({"event": "tool_start", "tool": "edit"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:stressed" in c for c in calls)

    def test_work_timer_starts_on_first_event(self, watcher):
        assert watcher._work_start == 0
        watcher._handle_event({"event": "tool_start", "tool": "read"})
        assert watcher._work_start > 0

    def test_work_timer_resets_on_idle(self, watcher):
        watcher._work_start = time.time() - 100
        watcher.last_activity = time.time() - watcher.IDLE_TIMEOUT - 1
        watcher._check_idle()
        assert watcher._work_start == 0

    def test_web_fetch_sends_reading(self, watcher):
        watcher._handle_event({"event": "tool_start", "tool": "web_fetch"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:reading" in c for c in calls)

    def test_memory_search_sends_reading(self, watcher):
        watcher._handle_event({"event": "tool_start", "tool": "memory_search"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:reading" in c for c in calls)

    def test_browser_sends_searching(self, watcher):
        watcher._handle_event({"event": "tool_start", "tool": "browser"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:searching" in c for c in calls)

    def test_write_sends_focused(self, watcher):
        watcher._handle_event({"event": "tool_start", "tool": "write"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:focused" in c for c in calls)


# ---------------------------------------------------------------------------
# Idle phrases — use config-driven phrases
# ---------------------------------------------------------------------------

class TestIdlePhrases:
    def test_default_phrases_exist(self, watcher):
        assert len(watcher.IDLE_PHRASES) > 10
        assert len(watcher.WAITING_PHRASES) > 5

    def test_phrases_are_strings(self, watcher):
        for phrase in watcher.IDLE_PHRASES:
            assert isinstance(phrase, str) and len(phrase) > 0
        for phrase in watcher.WAITING_PHRASES:
            assert isinstance(phrase, str) and len(phrase) > 0

    def test_custom_phrases_used(self, custom_watcher):
        assert custom_watcher.IDLE_PHRASES == ["The first ninety minutes are the most important"]
        assert custom_watcher.WAITING_PHRASES == ["Ready for the whistle"]


# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------

class TestTimeouts:
    def test_default_timeout_ordering(self, watcher):
        assert watcher.WAITING_TIMEOUT < watcher.IDLE_TIMEOUT
        assert watcher.IDLE_TIMEOUT <= watcher.SLEEPY_TIMEOUT
        assert watcher.SLEEPY_TIMEOUT <= watcher.ASLEEP_TIMEOUT
        assert watcher.ASLEEP_TIMEOUT <= watcher.SCREEN_OFF_TIMEOUT

    def test_custom_timeouts(self, custom_watcher):
        assert custom_watcher.WAITING_TIMEOUT == 5
        assert custom_watcher.IDLE_TIMEOUT == 60
        assert custom_watcher.SLEEPY_TIMEOUT == 120
        assert custom_watcher.ASLEEP_TIMEOUT == 240
        assert custom_watcher.SCREEN_OFF_TIMEOUT == 360


# ---------------------------------------------------------------------------
# Log path
# ---------------------------------------------------------------------------

class TestGetLogPath:
    def test_returns_dated_path(self, watcher):
        path = watcher._get_log_path()
        assert "clawdbot-" in str(path)
        assert str(path).endswith(".log")
        assert "/tmp/clawdbot/" in str(path)

    def test_custom_log_path(self, custom_watcher):
        path = custom_watcher._get_log_path()
        assert str(path) == "/tmp/custom-log.log"


# ---------------------------------------------------------------------------
# Serial connection
# ---------------------------------------------------------------------------

class TestConnectSerial:
    def test_connect_sets_serial_params(self):
        with patch('activity_watcher.serial.Serial') as MockSerial:
            mock_ser = MagicMock()
            MockSerial.return_value = mock_ser
            w = aw.ActivityWatcher.__new__(aw.ActivityWatcher)
            w._serial_port = "/dev/cu.test"
            w._agent_name = "Test"
            w.ser = None
            w._connect_serial()
            assert mock_ser.port == "/dev/cu.test"
            assert mock_ser.baudrate == aw.DEFAULT_BAUD_RATE
            assert mock_ser.dtr is False
            assert mock_ser.rts is False
            mock_ser.open.assert_called_once()

    def test_handles_connection_failure(self):
        with patch('activity_watcher.serial.Serial') as MockSerial:
            from serial import SerialException
            mock_ser = MagicMock()
            mock_ser.open.side_effect = SerialException("not found")
            MockSerial.return_value = mock_ser
            w = aw.ActivityWatcher.__new__(aw.ActivityWatcher)
            w._serial_port = "/dev/cu.test"
            w._agent_name = "Test"
            w.ser = None
            w._connect_serial()
            assert w.ser is None


# ---------------------------------------------------------------------------
# Edge cases — serial not open
# ---------------------------------------------------------------------------

class TestSerialNotOpen:
    def test_send_status_reconnects_when_not_open(self, watcher):
        watcher.ser.is_open = False
        with patch.object(watcher, '_connect_serial') as mock:
            watcher.send_status("Test")
            mock.assert_called_once()

    def test_send_expression_reconnects_when_not_open(self, watcher):
        watcher.ser = None
        watcher.current_expr = ""
        watcher.send_expression("sleepy")

    def test_send_expression_serial_error(self, watcher):
        from serial import SerialException
        watcher.ser.write.side_effect = SerialException("gone")
        with patch.object(watcher, '_connect_serial') as mock:
            watcher.current_expr = ""
            watcher.send_expression("sleepy")
            mock.assert_called_once()

    def test_send_screen_when_serial_none(self, watcher):
        watcher.ser = None
        watcher.send_screen(False)

    def test_clear_status_serial_error(self, watcher):
        from serial import SerialException
        watcher.ser.write.side_effect = SerialException("gone")
        watcher.clear_status()


# ---------------------------------------------------------------------------
# set_status_hint.py helper
# ---------------------------------------------------------------------------

class TestSetStatusHintScript:
    def test_set_hint(self, tmp_path):
        hint_file = tmp_path / "status-hint.json"
        with patch('set_status_hint.STATUS_HINT_FILE', str(hint_file)):
            import set_status_hint
            set_status_hint.STATUS_HINT_FILE = str(hint_file)
            sys.argv = ["set_status_hint.py", "Testing hint"]
            set_status_hint.main()
            data = json.loads(hint_file.read_text())
            assert data["text"] == "Testing hint"
            assert "ts" in data

    def test_clear_hint(self, tmp_path):
        hint_file = tmp_path / "status-hint.json"
        hint_file.write_text(json.dumps({"text": "old", "ts": 0}))
        import set_status_hint
        set_status_hint.STATUS_HINT_FILE = str(hint_file)
        sys.argv = ["set_status_hint.py", "--clear"]
        set_status_hint.main()
        assert not hint_file.exists()

    def test_no_args_exits(self, tmp_path):
        import set_status_hint
        set_status_hint.STATUS_HINT_FILE = str(tmp_path / "hint.json")
        sys.argv = ["set_status_hint.py"]
        with pytest.raises(SystemExit):
            set_status_hint.main()


# ---------------------------------------------------------------------------
# Idle state machine
# ---------------------------------------------------------------------------

class TestCheckIdle:
    def test_no_activity_yet(self, watcher):
        watcher.last_activity = 0
        watcher._check_idle()
        assert not watcher.idle_sent

    def test_becomes_idle_after_timeout(self, watcher):
        watcher.last_activity = time.time() - watcher.IDLE_TIMEOUT - 1
        watcher._check_idle()
        assert watcher.idle_sent
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:idle" in c for c in calls)

    def test_becomes_sleepy_after_timeout(self, watcher):
        watcher.last_activity = time.time() - watcher.SLEEPY_TIMEOUT - 1
        watcher.idle_sent = True
        watcher.current_expr = "idle"
        watcher._check_idle()
        assert watcher.sleepy_sent
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:sleepy" in c for c in calls)

    def test_becomes_asleep_after_timeout(self, watcher):
        watcher.last_activity = time.time() - watcher.ASLEEP_TIMEOUT - 1
        watcher.idle_sent = True
        watcher.sleepy_sent = True
        watcher.current_expr = "sleepy"
        watcher._check_idle()
        assert watcher.asleep_sent
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:asleep" in c for c in calls)
        assert any("Zzzz" in c for c in calls)

    def test_screen_off_after_timeout(self, watcher):
        watcher.last_activity = time.time() - watcher.SCREEN_OFF_TIMEOUT - 1
        watcher.idle_sent = True
        watcher.sleepy_sent = True
        watcher.asleep_sent = True
        watcher.current_expr = "asleep"
        watcher._check_idle()
        assert watcher.screen_off
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("SCREEN:DIM:10" in c for c in calls)

    def test_idle_phrase_rotation(self, watcher):
        watcher.last_activity = time.time() - watcher.IDLE_TIMEOUT - 1
        watcher.idle_sent = True
        watcher.sleepy_sent = True
        watcher.current_expr = "sleepy"
        watcher._last_phrase_time = time.time() - 46
        watcher._check_idle()
        assert watcher.ser.write.called

    def test_no_phrase_rotation_too_soon(self, watcher):
        """Within 45 seconds, no phrase rotation — but sleepy transition may fire"""
        watcher.last_activity = time.time() - watcher.SLEEPY_TIMEOUT - 1
        watcher.idle_sent = True
        watcher.sleepy_sent = True
        watcher.asleep_sent = False
        watcher.current_expr = "sleepy"
        watcher._last_phrase_time = time.time() - 3
        watcher._check_idle()
        # No phrase rotation (too soon), but asleep won't fire either (not past ASLEEP_TIMEOUT)
        # Re-test with shorter idle time to truly isolate phrase rotation
        watcher2_activity = time.time() - watcher.IDLE_TIMEOUT - 1
        watcher.last_activity = watcher2_activity
        watcher.ser.reset_mock()
        watcher._last_phrase_time = time.time() - 3
        watcher._check_idle()
        # Should not have sent any status (phrase rotation needs 45s)
        status_calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list if c[0][0].decode().startswith("S:")]
        assert len(status_calls) == 0

    def test_full_idle_sequence(self, watcher):
        """Test the full waiting → idle → sleepy → asleep → screen off progression"""
        # Step 1: Go waiting
        watcher.last_activity = time.time() - watcher.WAITING_TIMEOUT - 1
        watcher._check_idle()
        assert watcher.waiting_sent

        # Step 2: Go idle
        watcher.last_activity = time.time() - watcher.IDLE_TIMEOUT - 1
        watcher._check_idle()
        assert watcher.idle_sent

        # Step 3: Go sleepy
        watcher.last_activity = time.time() - watcher.SLEEPY_TIMEOUT - 1
        watcher._check_idle()
        assert watcher.sleepy_sent

        # Step 4: Go asleep
        watcher.last_activity = time.time() - watcher.ASLEEP_TIMEOUT - 1
        watcher._check_idle()
        assert watcher.asleep_sent

        # Step 5: Screen off
        watcher.last_activity = time.time() - watcher.SCREEN_OFF_TIMEOUT - 1
        watcher._check_idle()
        assert watcher.screen_off


class TestRunLoopHelpers:
    def test_log_path_includes_date(self, watcher):
        from datetime import datetime
        path = watcher._get_log_path()
        today = datetime.now().strftime("%Y-%m-%d")
        assert today in str(path)


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# New event types: discord_incoming, slow_listener, heartbeat, composing
# ---------------------------------------------------------------------------

class TestDiscordIncoming:
    def test_parse_skipped_discord_message_returns_none(self, watcher):
        """Skipped discord messages should be ignored entirely"""
        line = json.dumps({
            "0": '{"module":"discord-auto-reply"}',
            "1": {"channelId": "123", "reason": "no-mention"},
            "2": "discord: skipping guild message"
        })
        result = watcher._parse_line(line)
        assert result is None

    def test_parse_processed_discord_message(self, watcher):
        """Non-skipped discord-auto-reply = incoming message being processed"""
        line = json.dumps({
            "0": '{"module":"discord-auto-reply"}',
            "1": {"channelId": "123"},
            "2": "discord: processing guild message"
        })
        result = watcher._parse_line(line)
        assert result == {"event": "discord_incoming"}

    def test_discord_incoming_shows_thinking(self, watcher):
        """discord_incoming should immediately set thinking expression"""
        watcher._handle_event({"event": "discord_incoming"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:thinking" in c for c in calls)
        assert any("Reading message" in c for c in calls)

    def test_discord_incoming_wakes_from_sleep(self, watcher):
        """A processed discord message should wake the face from sleep"""
        watcher.sleepy_sent = True
        watcher.asleep_sent = True
        watcher.screen_off = True
        watcher.current_expr = "asleep"
        watcher._handle_event({"event": "discord_incoming"})
        assert watcher.sleepy_sent is False
        assert watcher.asleep_sent is False
        assert watcher.screen_off is False


class TestSlowListener:
    def test_parse_slow_listener(self, watcher):
        line = json.dumps({
            "0": '{"subsystem":"discord/monitor"}',
            "1": {"listener": "DiscordMessageListener", "event": "MESSAGE_CREATE",
                  "durationMs": 45294, "duration": "45.3 seconds"},
            "2": "Slow listener detected"
        })
        result = watcher._parse_line(line)
        assert result == {"event": "slow_listener"}

    def test_slow_listener_keeps_expression(self, watcher):
        """Slow listener should NOT change expression, only refresh timer"""
        watcher.send_expression("focused")
        watcher.ser.reset_mock()
        old_activity = watcher.last_activity
        time.sleep(0.01)
        watcher._handle_event({"event": "slow_listener"})
        assert watcher.last_activity > old_activity
        # Should not have sent any serial commands
        assert not watcher.ser.write.called


class TestHeartbeatEvent:
    def test_heartbeat_only_refreshes_timer(self, watcher):
        """Heartbeat events should not change expression or status"""
        watcher.send_expression("idle")
        watcher.ser.reset_mock()
        old_expr = watcher.current_expr
        watcher._handle_event({"event": "heartbeat"})
        assert watcher.current_expr == old_expr
        assert not watcher.ser.write.called

    def test_discord_infrastructure_is_heartbeat(self, watcher):
        """Discord login/gateway messages should be heartbeat only"""
        line = json.dumps({
            "0": '{"subsystem":"gateway/channels/discord"}',
            "1": "logged in to discord as 1465003863013593220",
            "_meta": {"logLevelName": "INFO"}
        })
        result = watcher._parse_line(line)
        assert result == {"event": "heartbeat"}


class TestComposingExpression:
    def test_message_tool_sends_composing(self, watcher):
        """Message tool should trigger composing expression"""
        watcher._handle_event({"event": "tool_start", "tool": "message"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:composing" in c for c in calls)
        status_calls = [c for c in calls if c.startswith("S:")]
        assert len(status_calls) > 0
        status_text = status_calls[0]
        assert any(phrase in status_text for phrase in [
            "Writing on Discord", "Composing a reply",
            "Typing a response", "Sending a message"
        ])

    def test_message_tool_with_hint(self, watcher, hint_file):
        """Message tool with a hint should use the hint text"""
        hint_file.write_text(json.dumps({"text": "Replying to Niall", "ts": time.time()}))
        watcher._handle_event({"event": "tool_start", "tool": "message"})
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("Replying to Niall" in c for c in calls)

    def test_composing_in_tool_expressions(self):
        """message tool should map to composing expression"""
        assert aw.TOOL_EXPRESSIONS["message"] == "composing"

    def test_composing_in_ticker_colors(self):
        """composing should have a ticker color defined"""
        assert "composing" in aw.DEFAULT_TICKER_COLORS


class TestMain:
    def test_main_creates_and_runs_watcher(self):
        with patch.object(aw, 'load_config', return_value={}):
            with patch.object(aw.ActivityWatcher, '__init__', return_value=None) as mock_init:
                with patch.object(aw.ActivityWatcher, 'run') as mock_run:
                    mock_run.return_value = None
                    aw.main()
                    mock_init.assert_called_once_with({})
                    mock_run.assert_called_once()
