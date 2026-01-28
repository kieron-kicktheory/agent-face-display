# Agent Face Display 🦞👀

Animated face display for AI agents running on Waveshare ESP32-S3-LCD-1.69.

## Hardware

- **Board:** Waveshare ESP32-S3-LCD-1.69 (non-touch)
- **Display:** 240x280 ST7789V2 IPS LCD
- **MCU:** ESP32-S3R8 with 8MB PSRAM, 16MB Flash

## Pin Configuration

| Function | GPIO |
|----------|------|
| DC       | 4    |
| CS       | 5    |
| SCK      | 6    |
| MOSI     | 7    |
| RST      | 8    |
| BL       | 15   |

## Setup

### 1. Flash MicroPython

```bash
pip install esptool mpremote

# Put board in bootloader mode (hold BOOT while plugging in USB)
esptool.py --port /dev/cu.usbmodem* write_flash -z 0 micropython-esp32s3.bin
```

### 2. Upload Driver

```bash
mpremote connect /dev/cu.usbmodem* cp lib/st7789_lcd169.py :/lib/st7789_lcd169.py
```

### 3. Test

```python
from st7789_lcd169 import ST7789, RED, GREEN, BLUE

display = ST7789()
display.fill(RED)
```

## Project Structure

```
agent-face-display/
├── lib/
│   └── st7789_lcd169.py    # Display driver
├── src/
│   ├── main.py             # Main application
│   ├── eyes.py             # Animated eyes (RoboEyes)
│   └── status.py           # Status ticker display
├── assets/
│   └── icons/              # Activity icons (16x16 bitmaps)
└── README.md
```

## Phases

- [x] **Phase 1:** Display driver working
- [ ] **Phase 2:** Animated eyes (RoboEyes)
- [ ] **Phase 3:** Status ticker + health indicator
- [ ] **Phase 4:** WiFi polling from Clawdbot

## License

MIT
