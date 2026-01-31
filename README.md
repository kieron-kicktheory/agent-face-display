# Agent Face Display

Animated eyes + status ticker for ESP32-S3-LCD-1.69, driven by Clawdbot activity logs over USB serial.

**Config-driven** — same codebase, different personalities. Each agent gets its own face, phrases, colors, and eye style via a simple JSON config.

## Hardware

- **Device:** Waveshare ESP32-S3-LCD-1.69 (non-touch, V2)
- **Display:** 240×280 ST7789V2
- **Connection:** USB serial to Mac

## Quick Start

### 1. Flash MicroPython to ESP32

```bash
esptool.py --chip esp32s3 erase_flash
esptool.py --chip esp32s3 write_flash -z 0x0 firmware.bin
```

### 2. Upload code to ESP32

```bash
mpremote cp lib/main.py :lib/main.py
mpremote cp lib/eyes.py :lib/eyes.py
mpremote cp lib/ticker.py :lib/ticker.py
mpremote cp lib/font16.py :lib/font16.py
mpremote cp lib/st7789_lcd169.py :lib/st7789_lcd169.py
```

### 3. Upload personality config to ESP32

Create a `config.json` for the agent and upload it:

```bash
# Use the example as a starting point
mpremote cp config/example.json :/config.json
```

Or for Bobby:
```bash
mpremote cp config/bobby.example.json :/config.json
```

### 4. Set up the Mac-side watcher

```bash
# Create local config (not in git)
mkdir -p ~/.agent-face
cp config/example.json ~/.agent-face/config.json
# Edit with your agent's serial port, name, phrases, etc.

# Run the watcher
python3 scripts/activity_watcher.py
```

## Configuration

All customisation is via JSON config files:

| File | Purpose |
|------|---------|
| `~/.agent-face/config.json` | Mac-side config (serial port, phrases, timeouts, colors) |
| `/config.json` (on ESP32) | Eye appearance (iris color, size, eyebrows, expressions) |
| `config/schema.json` | Full schema with all configurable fields |
| `config/example.json` | Kieron's defaults |
| `config/bobby.example.json` | Bobby's personality |

### Key Config Options

**Eyes (ESP32):**
- `irisColor` — hex color for iris (e.g. `"0x2288FF"`)
- `eyeWidth`, `eyeHeight` — eye dimensions
- `eyebrows` — `{ thickness, gap, color }` or omit for no eyebrows
- `crowsFeet` — `true` for smile lines
- `happySquint` — default eyelid droop % (0 = none, 15-25 = friendly squint)
- `defaultExpression` — `"normal"` or `"happy"`

**Ticker (Mac):**
- `ticker.colors` — color per expression state
- `ticker.scrollSpeed` — ms per scroll step

**Phrases (Mac):**
- `phrases.waiting` — shown when idle 10s-3min
- `phrases.idle` — shown when idle 3min+

**Timeouts (Mac):**
- `timeouts.waiting` — seconds before "waiting" state (default: 10)
- `timeouts.idle` — seconds before idle phrases (default: 180)
- `timeouts.sleepy` — seconds before eyes droop (default: 300)
- `timeouts.asleep` — seconds before fully asleep (default: 600)
- `timeouts.screenOff` — seconds before screen dims (default: 900)

## Project Structure

```
agent-face-display/
├── lib/                     # ESP32 MicroPython code
│   ├── main.py              # Main loop (loads config, serial listener)
│   ├── eyes.py              # Animated eyes (config-driven)
│   ├── ticker.py            # Scrolling status ticker
│   ├── font16.py            # SF Mono 12×24 bitmap font
│   └── st7789_lcd169.py     # Display driver
├── scripts/
│   ├── activity_watcher.py  # Mac-side log watcher (config-driven)
│   ├── send_status.py       # Manual status sender
│   └── set_status_hint.py   # Rich status hint helper
├── config/
│   ├── schema.json          # Full config schema
│   ├── example.json         # Default config (Kieron)
│   └── bobby.example.json   # Bobby's personality
├── tests/
│   └── test_activity_watcher.py
└── README.md
```

## Multi-Agent Setup

1. Clone this repo on each Mac
2. Create `~/.agent-face/config.json` with agent-specific settings
3. Flash each ESP32 with its own `/config.json`
4. Run `activity_watcher.py` — it reads from `~/.agent-face/config.json`

Same code, different personality. No repo changes needed per agent.
