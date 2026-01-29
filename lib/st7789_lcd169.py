"""
ST7789 Driver for Waveshare ESP32-S3-LCD-1.69
240x280 display, BGR color order
"""
from machine import Pin, SPI
import time

class ST7789:
    def __init__(self):
        # Pin configuration for ESP32-S3-LCD-1.69
        self.LCD_DC = 4
        self.LCD_CS = 5
        self.LCD_SCK = 6
        self.LCD_MOSI = 7
        self.LCD_RST = 8
        self.LCD_BL = 15
        self.width = 240
        self.height = 280
        
        # Setup pins — PWM on backlight for dimming
        from machine import PWM
        self.bl = PWM(Pin(self.LCD_BL), freq=1000, duty_u16=65535)
        self.rst = Pin(self.LCD_RST, Pin.OUT)
        self.cs = Pin(self.LCD_CS, Pin.OUT)
        self.dc = Pin(self.LCD_DC, Pin.OUT)
        
        # Hardware SPI
        self.spi = SPI(2, baudrate=40000000, polarity=0, phase=0,
                       sck=Pin(self.LCD_SCK), mosi=Pin(self.LCD_MOSI))
        
        self.init()
    
    def write_cmd(self, cmd):
        self.cs.value(0)
        self.dc.value(0)
        self.spi.write(bytes([cmd]))
        self.cs.value(1)
    
    def write_data(self, data):
        self.cs.value(0)
        self.dc.value(1)
        if isinstance(data, int):
            self.spi.write(bytes([data]))
        else:
            self.spi.write(data)
        self.cs.value(1)
    
    def init(self):
        # Reset
        self.rst.value(0)
        time.sleep_ms(100)
        self.rst.value(1)
        time.sleep_ms(150)
        
        # Backlight on
        self.bl.value(1)
        
        # Init sequence
        self.write_cmd(0x01)  # Software reset
        time.sleep_ms(150)
        
        self.write_cmd(0x11)  # Sleep out
        time.sleep_ms(120)
        
        self.write_cmd(0x36)  # MADCTL - BGR color order
        self.write_data(0x08)
        
        self.write_cmd(0x3A)  # Color mode - 16-bit
        self.write_data(0x55)
        
        self.write_cmd(0xB2)  # Porch control
        self.write_data(bytes([0x0C, 0x0C, 0x00, 0x33, 0x33]))
        
        self.write_cmd(0xB7)  # Gate control
        self.write_data(0x35)
        
        self.write_cmd(0xBB)  # VCOMS
        self.write_data(0x28)
        
        self.write_cmd(0xC0)  # LCM control
        self.write_data(0x0C)
        
        self.write_cmd(0xC2)  # VDV/VRH enable
        self.write_data(bytes([0x01, 0xFF]))
        
        self.write_cmd(0xC3)  # VRH set
        self.write_data(0x10)
        
        self.write_cmd(0xC4)  # VDV set
        self.write_data(0x20)
        
        self.write_cmd(0xC6)  # Frame rate
        self.write_data(0x0F)
        
        self.write_cmd(0xD0)  # Power control
        self.write_data(bytes([0xA4, 0xA1]))
        
        self.write_cmd(0x21)  # Inversion on
        
        self.write_cmd(0x29)  # Display on
        time.sleep_ms(50)
    
    def set_window(self, x0, y0, x1, y1):
        # Add 20 pixel offset for 240x280 in 240x320 RAM
        y0 += 20
        y1 += 20
        
        self.write_cmd(0x2A)
        self.write_data(bytes([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))
        self.write_cmd(0x2B)
        self.write_data(bytes([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))
        self.write_cmd(0x2C)
    
    def fill(self, color):
        """Fill entire screen with color (RGB888)"""
        r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
        c565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        high = (c565 >> 8) & 0xFF
        low = c565 & 0xFF
        
        self.set_window(0, 0, self.width - 1, self.height - 1)
        chunk = bytes([high, low] * 120)
        for _ in range(self.height):
            self.write_data(chunk)
            self.write_data(chunk)
    
    def fill_rect(self, x, y, w, h, color):
        """Fill rectangle with color (RGB888)"""
        r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
        c565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        high = (c565 >> 8) & 0xFF
        low = c565 & 0xFF
        
        self.set_window(x, y, x + w - 1, y + h - 1)
        chunk = bytes([high, low] * min(w, 120))
        rows = (w * 2) // len(chunk)
        extra = (w * 2) % len(chunk)
        
        for _ in range(h):
            for _ in range(rows):
                self.write_data(chunk)
            if extra:
                self.write_data(bytes([high, low] * (extra // 2)))
    
    def pixel(self, x, y, color):
        """Set single pixel"""
        self.fill_rect(x, y, 1, 1, color)
    
    def backlight(self, on=True):
        """on=True full brightness, on=False off"""
        self.bl.duty_u16(65535 if on else 0)

    def brightness(self, pct):
        """Set backlight brightness 0-100%"""
        duty = int(65535 * max(0, min(100, pct)) / 100)
        self.bl.duty_u16(duty)

# Color constants (RGB888)
BLACK = 0x000000
WHITE = 0xFFFFFF
RED = 0xFF0000
GREEN = 0x00FF00
BLUE = 0x0000FF
YELLOW = 0xFFFF00
CYAN = 0x00FFFF
MAGENTA = 0xFF00FF
PURPLE = 0x800080


def fill_circle(display, cx, cy, r, color):
    """Draw a filled circle using horizontal lines"""
    for y in range(-r, r + 1):
        # Calculate width at this y position
        x_width = int((r * r - y * y) ** 0.5)
        if x_width > 0:
            display.fill_rect(cx - x_width, cy + y, x_width * 2, 1, color)


def fill_rounded_rect(display, x, y, w, h, r, color):
    """Draw a filled rounded rectangle"""
    # Main body (without corners)
    display.fill_rect(x + r, y, w - 2*r, h, color)
    display.fill_rect(x, y + r, w, h - 2*r, color)
    
    # Four corners
    fill_circle(display, x + r, y + r, r, color)          # Top-left
    fill_circle(display, x + w - r - 1, y + r, r, color)  # Top-right
    fill_circle(display, x + r, y + h - r - 1, r, color)  # Bottom-left
    fill_circle(display, x + w - r - 1, y + h - r - 1, r, color)  # Bottom-right
