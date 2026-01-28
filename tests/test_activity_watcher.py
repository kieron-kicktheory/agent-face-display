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
    """Create a watcher with mocked serial"""
    with patch.object(aw.ActivityWatcher, '_connect_serial'):
        w = aw.ActivityWatcher()
        w.ser = MagicMock()
        w.ser.is_open = True
        return w


@pytest.fixture
def hint_file(tmp_path):
    """Temp hint file"""
    hint = tmp_path / "status-hint.json"
    original = aw.STATUS_HINT_FILE
    aw.STATUS_HINT_FILE = str(hint)
    yield hint
    aw.STATUS_HINT_FILE = original


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
        """Hint exactly at max age should still be valid"""
        hint_file.write_text(json.dumps({"text": "Boundary", "ts": time.time() - aw.HINT_MAX_AGE + 1}))
        assert watcher._read_hint() == "Boundary"

    def test_hint_just_past_boundary(self, watcher, hint_file):
        hint_file.write_text(json.dumps({"text": "Expired", "ts": time.time() - aw.HINT_MAX_AGE - 1}))
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
        watcher.ser.write.assert_called_with(b"SCREEN:OFF\n")
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
        # "Hi..." = 5 chars, padded to 25 — sent as "S:Hi...                    \n"
        call_args = watcher.ser.write.call_args[0][0].decode()
        # Full payload between "S:" and "\n"
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
        # Should have sent normal expression
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
        """Every tool in TOOL_LABELS should have at least one label"""
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
        # First set a different expression so normal isn't deduped
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
        watcher.last_activity = time.time() - aw.IDLE_TIMEOUT - 1
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
# Idle phrases
# ---------------------------------------------------------------------------

class TestIdlePhrases:
    def test_phrases_exist(self):
        assert len(aw.IDLE_PHRASES) > 10

    def test_phrases_are_strings(self):
        for phrase in aw.IDLE_PHRASES:
            assert isinstance(phrase, str)
            assert len(phrase) > 0


# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------

class TestTimeouts:
    def test_idle_timeout_is_5_minutes(self):
        assert aw.IDLE_TIMEOUT == 300

    def test_asleep_timeout_is_10_minutes(self):
        assert aw.ASLEEP_TIMEOUT == 600

    def test_screen_off_timeout_is_15_minutes(self):
        assert aw.SCREEN_OFF_TIMEOUT == 900

    def test_timeout_ordering(self):
        assert aw.IDLE_TIMEOUT <= aw.ASLEEP_TIMEOUT <= aw.SCREEN_OFF_TIMEOUT


# ---------------------------------------------------------------------------
# Log path
# ---------------------------------------------------------------------------

class TestGetLogPath:
    def test_returns_dated_path(self, watcher):
        path = watcher._get_log_path()
        assert "clawdbot-" in str(path)
        assert str(path).endswith(".log")
        assert "/tmp/clawdbot/" in str(path)


# ---------------------------------------------------------------------------
# Serial connection
# ---------------------------------------------------------------------------

class TestConnectSerial:
    def test_connect_sets_serial_params(self):
        with patch('activity_watcher.serial.Serial') as MockSerial:
            mock_ser = MagicMock()
            MockSerial.return_value = mock_ser
            w = aw.ActivityWatcher.__new__(aw.ActivityWatcher)
            w.ser = None
            w._connect_serial()
            assert mock_ser.port == aw.SERIAL_PORT
            assert mock_ser.baudrate == aw.BAUD_RATE
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
        # Should not raise
        watcher.send_expression("sleepy")

    def test_send_expression_serial_error(self, watcher):
        from serial import SerialException
        watcher.ser.write.side_effect = SerialException("gone")
        with patch.object(watcher, '_connect_serial') as mock:
            watcher.current_expr = ""  # Reset to allow send
            watcher.send_expression("sleepy")
            mock.assert_called_once()

    def test_send_screen_when_serial_none(self, watcher):
        watcher.ser = None
        # When serial is None, the `if self.ser and self.ser.is_open` guard is False
        # so it silently does nothing (no reconnect in send_screen)
        watcher.send_screen(False)
        # No crash is the test

    def test_clear_status_serial_error(self, watcher):
        from serial import SerialException
        watcher.ser.write.side_effect = SerialException("gone")
        watcher.clear_status()  # Should not raise


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
# Run loop integration (partial — test day rollover logic etc.)
# ---------------------------------------------------------------------------

class TestCheckIdle:
    def test_no_activity_yet(self, watcher):
        """No activity timestamp → no idle transition"""
        watcher.last_activity = 0
        watcher._check_idle()
        assert not watcher.idle_sent

    def test_becomes_idle_after_timeout(self, watcher):
        watcher.last_activity = time.time() - aw.IDLE_TIMEOUT - 1
        watcher._check_idle()
        assert watcher.idle_sent
        assert watcher.sleepy_sent
        # Should have sent sleepy expression and idle status
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:sleepy" in c for c in calls)

    def test_becomes_asleep_after_timeout(self, watcher):
        watcher.last_activity = time.time() - aw.ASLEEP_TIMEOUT - 1
        watcher.idle_sent = True
        watcher.sleepy_sent = True
        watcher.current_expr = "sleepy"
        watcher._check_idle()
        assert watcher.asleep_sent
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("E:asleep" in c for c in calls)
        assert any("Zzzz" in c for c in calls)

    def test_screen_off_after_timeout(self, watcher):
        watcher.last_activity = time.time() - aw.SCREEN_OFF_TIMEOUT - 1
        watcher.idle_sent = True
        watcher.sleepy_sent = True
        watcher.asleep_sent = True
        watcher.current_expr = "asleep"
        watcher._check_idle()
        assert watcher.screen_off
        calls = [c[0][0].decode() for c in watcher.ser.write.call_args_list]
        assert any("SCREEN:OFF" in c for c in calls)

    def test_idle_phrase_rotation(self, watcher):
        """After 8 seconds idle, phrase should rotate"""
        watcher.last_activity = time.time() - aw.IDLE_TIMEOUT - 1
        watcher.idle_sent = True
        watcher.sleepy_sent = True
        watcher.current_expr = "sleepy"
        watcher._last_phrase_time = time.time() - 9  # 9 seconds ago
        watcher._check_idle()
        # Should have sent a new idle phrase
        assert watcher.ser.write.called

    def test_no_phrase_rotation_too_soon(self, watcher):
        """Within 8 seconds, no phrase rotation"""
        watcher.last_activity = time.time() - aw.IDLE_TIMEOUT - 1
        watcher.idle_sent = True
        watcher.sleepy_sent = True
        watcher.current_expr = "sleepy"
        watcher._last_phrase_time = time.time() - 3  # Only 3 seconds ago
        watcher._check_idle()
        assert not watcher.ser.write.called

    def test_full_idle_sequence(self, watcher):
        """Test the full idle → sleepy → asleep → screen off progression"""
        # Step 1: Go idle
        watcher.last_activity = time.time() - aw.IDLE_TIMEOUT - 1
        watcher._check_idle()
        assert watcher.idle_sent and watcher.sleepy_sent

        # Step 2: Go asleep
        watcher.last_activity = time.time() - aw.ASLEEP_TIMEOUT - 1
        watcher._check_idle()
        assert watcher.asleep_sent

        # Step 3: Screen off
        watcher.last_activity = time.time() - aw.SCREEN_OFF_TIMEOUT - 1
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

class TestMain:
    def test_main_creates_and_runs_watcher(self):
        with patch.object(aw.ActivityWatcher, '__init__', return_value=None) as mock_init:
            with patch.object(aw.ActivityWatcher, 'run') as mock_run:
                mock_run.return_value = None
                aw.main()
                mock_init.assert_called_once()
                mock_run.assert_called_once()
