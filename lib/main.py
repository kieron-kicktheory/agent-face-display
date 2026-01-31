"""
Agent Face Display - Main Loop
Eyes + Status Ticker + USB Serial Listener
Config-driven: reads /config.json on boot for personality
Auto-restarts on any crash.
"""
import time
import gc
import json
import select
import sys
from lib.st7789_lcd169 import ST7789
from lib.eyes import Eyes
from lib.ticker import StatusTicker


def _load_config():
    """Load config from /config.json on the ESP32 filesystem"""
    try:
        with open("/config.json", "r") as f:
            cfg = json.load(f)
        print("Config loaded:", cfg.get("agent", {}).get("name", "unknown"))
        return cfg
    except OSError:
        print("No /config.json — using defaults")
        return {}
    except ValueError as e:
        print("Config parse error:", e, "— using defaults")
        return {}


def run():
    # Load config
    config = _load_config()
    eye_cfg = config.get("eyes", {})
    ticker_cfg = config.get("ticker", {})
    
    # Init display
    display = ST7789()
    
    # Init components with config
    gc.collect()
    eyes = Eyes(display, eye_cfg)
    gc.collect()
    
    # Ticker color from config
    ticker_default_color = 0xFFFFFF
    colors_cfg = ticker_cfg.get("colors", {})
    if "active" in colors_cfg:
        ticker_default_color = int(colors_cfg["active"], 16) if isinstance(colors_cfg["active"], str) else colors_cfg["active"]
    
    ticker = StatusTicker(display, color=ticker_default_color)
    
    # Apply scroll speed from config
    if "scrollSpeed" in ticker_cfg:
        ticker.scroll_speed = ticker_cfg["scrollSpeed"]
    
    gc.collect()
    
    # Build ticker color map from config
    ticker_colors = {}
    for expr_name, default_hex in [
        ("waiting", 0x888888), ("idle", 0x2288FF), ("sleepy", 0x2288FF),
        ("asleep", 0x114488), ("stressed", 0xFF4444), ("focused", 0x44FF44),
        ("terminal", 0x44FF44), ("thinking", 0xFFAA00), ("searching", 0xFF88FF),
        ("reading", 0x88DDFF)
    ]:
        if expr_name in colors_cfg:
            val = colors_cfg[expr_name]
            ticker_colors[expr_name] = int(val, 16) if isinstance(val, str) else val
        else:
            ticker_colors[expr_name] = default_hex
    
    # Non-blocking serial input
    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)
    line_buf = ""
    
    agent_name = config.get("agent", {}).get("name", "Agent")
    print(f"{agent_name} face running...")
    print("Free mem:", gc.mem_free())
    
    while True:
        try:
            # Check for serial input (non-blocking)
            events = poll.poll(0)
            if events:
                ch = sys.stdin.read(1)
                if ch == '\n' or ch == '\r':
                    if line_buf:
                        _handle_line(line_buf, ticker, eyes, display, ticker_colors)
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


def _handle_line(line, ticker, eyes, display, ticker_colors):
    """Process a serial command line"""
    line = line.strip()
    
    if line.startswith("S:"):
        # Status update
        text = line[2:]
        if text.startswith(" "):
            text = text.lstrip()
        ticker.set_text(text)
    
    elif line.startswith("CLEAR"):
        ticker.set_text("")
    
    elif line.startswith("SCREEN:"):
        cmd = line[7:].strip().upper()
        if cmd == "OFF":
            display.backlight(False)
        elif cmd == "ON":
            display.backlight(True)
        elif cmd.startswith("DIM:"):
            try:
                pct = int(cmd[4:])
                display.brightness(pct)
            except ValueError:
                pass
    
    elif line.startswith("E:"):
        # Expression change — also adjusts ticker color from config
        expr = line[2:].strip().lower()
        eyes.set_expression(expr)
        
        # Set ticker color from config map
        color = ticker_colors.get(expr, 0xFFFFFF)
        ticker.set_color(color)


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
