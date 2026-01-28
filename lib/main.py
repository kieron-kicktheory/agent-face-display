"""
Agent Face Display - Main Loop
Eyes + Status Ticker + USB Serial Listener
Auto-restarts on any crash.
"""
import time
import gc
import select
import sys
from lib.st7789_lcd169 import ST7789
from lib.eyes import Eyes
from lib.ticker import StatusTicker


def run():
    # Init display
    display = ST7789()
    
    # Init components
    gc.collect()
    eyes = Eyes(display)
    gc.collect()
    ticker = StatusTicker(display)
    gc.collect()
    
    # Non-blocking serial input
    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)
    line_buf = ""
    
    print("Agent face running...")
    print("Free mem:", gc.mem_free())
    
    while True:
        try:
            # Check for serial input (non-blocking)
            events = poll.poll(0)
            if events:
                ch = sys.stdin.read(1)
                if ch == '\n' or ch == '\r':
                    if line_buf:
                        _handle_line(line_buf, ticker, eyes, display)
                        line_buf = ""
                else:
                    line_buf += ch
            
            # Update components
            eyes.update()
            ticker.update()
        except MemoryError:
            gc.collect()
            print("MEM:", gc.mem_free())
        except Exception as e:
            print("ERR:", e)
        
        time.sleep_ms(20)


def _handle_line(line, ticker, eyes, display):
    """Process a serial command line"""
    line = line.strip()
    
    if line.startswith("S:"):
        # Status update — preserve trailing spaces for scroll padding
        text = line[2:]
        if text.startswith(" "):
            text = text.lstrip()
        ticker.set_text(text)
    
    elif line.startswith("CLEAR"):
        # Clear ticker
        ticker.set_text("")
    
    elif line.startswith("SCREEN:"):
        # Backlight control
        cmd = line[7:].strip().upper()
        if cmd == "OFF":
            display.backlight(False)
        elif cmd == "ON":
            display.backlight(True)
    
    elif line.startswith("E:"):
        # Expression change — also adjusts ticker color
        expr = line[2:].strip().lower()
        eyes.set_expression(expr)
        # Ticker color matches mood
        if expr in ("sleepy",):
            ticker.set_color(0x2288FF)   # Blue when idle
        elif expr in ("asleep",):
            ticker.set_color(0x114488)   # Dark blue when asleep
        elif expr in ("stressed",):
            ticker.set_color(0xFF4444)   # Red-ish when stressed
        elif expr in ("focused", "terminal"):
            ticker.set_color(0x44FF44)   # Green when coding/terminal
        elif expr in ("thinking",):
            ticker.set_color(0xFFAA00)   # Amber when thinking
        elif expr in ("searching",):
            ticker.set_color(0xFF88FF)   # Pink when searching
        elif expr in ("reading",):
            ticker.set_color(0x88DDFF)   # Light blue when reading
        else:
            ticker.set_color(0xFFFFFF)   # White default


# Auto-restart loop — survive any crash
while True:
    try:
        run()
    except KeyboardInterrupt:
        print("Stopped")
        break
    except MemoryError:
        gc.collect()
        print("HARD MEM - restarting:", gc.mem_free())
        time.sleep(1)
    except Exception as e:
        print("CRASH:", e, "- restarting")
        time.sleep(1)
    except:
        print("UNKNOWN CRASH - restarting")
        time.sleep(1)
