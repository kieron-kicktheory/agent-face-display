#!/usr/bin/env python3
"""
Send status text to Agent Face Display over USB serial.

Usage:
    python3 send_status.py "Checking emails..."
    python3 send_status.py --clear
    echo "Thinking..." | python3 send_status.py
"""
import sys
import serial
import argparse

SERIAL_PORT = "/dev/cu.usbmodem21101"
BAUD_RATE = 115200


def _open_serial(port: str) -> serial.Serial:
    """Open serial without resetting ESP32 (no DTR/RTS toggle)"""
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = BAUD_RATE
    ser.timeout = 1
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser


def send(text: str, port: str = SERIAL_PORT):
    """Send a status line to the display"""
    try:
        ser = _open_serial(port)
        line = f"S:{text}\n"
        ser.write(line.encode())
        ser.flush()
        ser.close()
    except serial.SerialException as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def clear(port: str = SERIAL_PORT):
    """Clear the ticker"""
    try:
        ser = _open_serial(port)
        ser.write(b"CLEAR\n")
        ser.flush()
        ser.close()
    except serial.SerialException as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Send status to agent face display")
    parser.add_argument("text", nargs="?", help="Status text to display")
    parser.add_argument("--clear", action="store_true", help="Clear the ticker")
    parser.add_argument("--port", default=SERIAL_PORT, help="Serial port")
    args = parser.parse_args()

    if args.clear:
        clear(args.port)
        return

    text = args.text
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()

    if not text:
        parser.print_help()
        sys.exit(1)

    send(text, args.port)


if __name__ == "__main__":
    main()
