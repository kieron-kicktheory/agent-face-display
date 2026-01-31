"""
Status ticker - SF Mono 16x24 native font
Pre-rendered buffer for fast scrolling, no per-frame character loops
Icon support: 24x24 bitmap rendered at left edge before text
"""
import time
import gc
from lib.font16 import DATA, WIDTH, HEIGHT, FIRST, LAST, ROW_BYTES


# Icon dimensions
ICON_W = 24
ICON_PAD = 4   # Gap between icon and text


class StatusTicker:
    def __init__(self, display, y=205, color=0xFFFFFF):
        self.display = display
        self.dw = display.width         # 240
        self.ch_w = WIDTH               # 12
        self.ch_h = HEIGHT              # 24
        self.row_h = self.ch_h + 2      # 26 (1px pad)
        self.y = y
        self.text = ""
        
        # Default color
        self._set_color(color)
        
        # Icon state
        self._icon_data = None    # Current icon bitmap (bytes) or None
        self._icon_w = 0          # Width of icon area (icon + padding)
        
        # Icon buffer (rendered separately, left of text)
        self._icon_buf = bytearray(ICON_W * self.row_h * 2)
        
        # Display buffer (what gets blitted — text portion only)
        self.disp_buf = bytearray(self.dw * self.row_h * 2)
        
        # Pre-allocate max text buffer (41 chars × 12px = 492px)
        self._max_text_px = 41 * self.ch_w
        self._full_buf_fixed = bytearray(self._max_text_px * self.row_h * 2)
        
        # Pre-rendered full text buffer (built on set_text)
        self._full_buf = None
        self._full_w = 0
        
        # Scroll state
        self.scroll_x = 0
        self.scroll_pause = 0
        self.last_scroll = 0
        self.scroll_speed = 30   # ms per step (faster)
        self.scroll_step = 3     # pixels per step
        self.needs_scroll = False
        self.force_scroll = False  # Always scroll, even short text
        
        self._clear()
    
    def _set_color(self, color):
        """Set text color from RGB888"""
        r = (color >> 16) & 0xFF
        g = (color >> 8) & 0xFF
        b = color & 0xFF
        c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        self._hi = (c >> 8) & 0xFF
        self._lo = c & 0xFF
    
    def set_color(self, color):
        """Change color — re-renders text buffer, visible on next scroll/window"""
        self._set_color(color)
        if self.text:
            self._prerender()
    
    def set_icon(self, icon_data):
        """Set icon bitmap (24x24, 3 bytes/row) or None to clear"""
        if icon_data is None:
            if self._icon_data is not None:
                self._icon_data = None
                self._icon_w = 0
                # Clear icon area
                self._clear_icon()
            return
        self._icon_data = icon_data
        self._icon_w = ICON_W + ICON_PAD
        self._render_icon()
        self._blit_icon()
    
    def _render_icon(self):
        """Render icon bitmap into icon buffer"""
        buf = self._icon_buf
        hi = self._hi
        lo = self._lo
        icon = self._icon_data
        iw = ICON_W
        rh = self.row_h
        pad = 1  # Vertical pad (matches text)
        
        # Clear icon buffer
        for i in range(len(buf)):
            buf[i] = 0
        
        if icon is None:
            return
        
        # Render 24x24 icon from bitmap
        for row in range(24):
            if row + pad >= rh:
                break
            dy = row + pad
            b0 = icon[row * 3]
            b1 = icon[row * 3 + 1]
            b2 = icon[row * 3 + 2]
            
            for col in range(8):
                if b0 & (1 << (7 - col)):
                    idx = (dy * iw + col) * 2
                    buf[idx] = hi
                    buf[idx + 1] = lo
            for col in range(8):
                if b1 & (1 << (7 - col)):
                    idx = (dy * iw + 8 + col) * 2
                    buf[idx] = hi
                    buf[idx + 1] = lo
            for col in range(8):
                if b2 & (1 << (7 - col)):
                    idx = (dy * iw + 16 + col) * 2
                    buf[idx] = hi
                    buf[idx + 1] = lo
    
    def _blit_icon(self):
        """Blit icon buffer to display at left edge"""
        iw = ICON_W
        self.display.set_window(0, self.y, iw - 1, self.y + self.row_h - 1)
        self.display.write_data(self._icon_buf)
    
    def _clear_icon(self):
        """Clear icon area on display"""
        for i in range(len(self._icon_buf)):
            self._icon_buf[i] = 0
        self._blit_icon()
    
    def _clear(self):
        for i in range(len(self.disp_buf)):
            self.disp_buf[i] = 0
        self._blit()
    
    def _blit(self):
        """Blit text to display (full width)"""
        self.display.set_window(0, self.y, self.dw - 1, self.y + self.row_h - 1)
        self.display.write_data(self.disp_buf)
    
    def set_text(self, text):
        self.text = text.strip()
        if not self.text:
            self._full_buf = None
            self._full_w = 0
            self._clear()
            return
        
        text_px = len(self.text) * self.ch_w
        text_dw = self.dw - self._icon_w
        self.needs_scroll = self.force_scroll or text_px > text_dw
        
        # Limit text width
        max_chars = 41
        if len(self.text) > max_chars:
            self.text = self.text[:max_chars]
            text_px = max_chars * self.ch_w
        
        # Use pre-allocated buffer (zero it out, no allocation)
        self._full_w = text_px
        self._full_buf = self._full_buf_fixed
        used = text_px * self.row_h * 2
        for i in range(used):
            self._full_buf[i] = 0
        self._prerender()
        
        # Reset scroll
        self.scroll_x = 0
        self.scroll_pause = 2000
        self.last_scroll = time.ticks_ms()
        
        # Show first frame
        self._window()
    
    def _prerender(self):
        """Render all characters into full-width buffer (runs once per set_text)"""
        buf = self._full_buf
        fw = self._full_w
        hi = self._hi
        lo = self._lo
        pad = 1
        
        for ci, ch in enumerate(self.text):
            code = ord(ch)
            if code < FIRST or code > LAST:
                continue
            
            cx = ci * self.ch_w
            font_off = (code - FIRST) * self.ch_h * ROW_BYTES
            
            for row in range(self.ch_h):
                byte_hi = DATA[font_off + row * 2]
                byte_lo = DATA[font_off + row * 2 + 1]
                
                if byte_hi == 0 and byte_lo == 0:
                    continue
                
                dy = row + pad
                buf_row = dy * fw * 2
                
                # First 8 columns
                if byte_hi:
                    for col in range(8):
                        if byte_hi & (1 << (7 - col)):
                            px = cx + col
                            idx = buf_row + px * 2
                            buf[idx] = hi
                            buf[idx + 1] = lo
                
                # Columns 8-15
                if byte_lo:
                    for col in range(8):
                        if byte_lo & (1 << (7 - col)):
                            px = cx + 8 + col
                            idx = buf_row + px * 2
                            buf[idx] = hi
                            buf[idx + 1] = lo
    
    def _window(self):
        """Copy visible window from pre-rendered buffer — flicker-free row copy"""
        db = self.disp_buf
        fb = self._full_buf
        dw = self.dw
        fw = self._full_w
        rh = self.row_h
        iw = self._icon_w
        text_dw = dw - iw
        row_bytes = dw * 2  # full row in display buffer
        z = b'\x00'  # for zeroing margins
        
        if fb is None:
            for i in range(len(db)):
                db[i] = 0
            self._blit()
            return
        
        if self.needs_scroll:
            src_x = self.scroll_x
        else:
            src_x = -(text_dw - fw) // 2
        
        copy_start = max(0, src_x)
        copy_end = min(fw, src_x + text_dw)
        
        if copy_start >= copy_end:
            for i in range(len(db)):
                db[i] = 0
            self._blit()
            return
        
        dst_offset = iw * 2 + (copy_start - src_x) * 2
        copy_bytes = (copy_end - copy_start) * 2
        dst_end = dst_offset + copy_bytes
        
        # Per-row: zero left margin, copy text, zero right margin — no full clear
        for row in range(rh):
            rb = row * row_bytes
            si = row * fw * 2 + copy_start * 2
            di = rb + dst_offset
            # Left margin (icon area + gap before text)
            if dst_offset > 0:
                db[rb:rb + dst_offset] = z * dst_offset
            # Text pixels
            db[di:di + copy_bytes] = fb[si:si + copy_bytes]
            # Right margin
            re = rb + row_bytes
            de = rb + dst_end
            if de < re:
                db[de:re] = z * (re - de)
        
        self._blit()
    
    def update(self):
        if not self.needs_scroll or not self.text:
            return
        
        now = time.ticks_ms()
        elapsed = time.ticks_diff(now, self.last_scroll)
        
        if self.scroll_pause > 0:
            if elapsed < self.scroll_pause:
                return
            self.scroll_pause = 0
            self.last_scroll = now
            return
        
        if elapsed < self.scroll_speed:
            return
        
        self.scroll_x += self.scroll_step
        
        text_dw = self.dw - self._icon_w
        if self.scroll_x > self._full_w + 40:
            self.scroll_x = -text_dw
        
        self._window()
        self.last_scroll = now
