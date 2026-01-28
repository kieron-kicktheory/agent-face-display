"""
Status ticker - SF Mono 16x24 native font
Pre-rendered buffer for fast scrolling, no per-frame character loops
"""
import time
import gc
from lib.font16 import DATA, WIDTH, HEIGHT, FIRST, LAST, ROW_BYTES


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
        
        # Display buffer (what gets blitted)
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
        """Change color and re-render current text"""
        self._set_color(color)
        if self.text:
            self._prerender()
            self._window()
    
    def _clear(self):
        for i in range(len(self.disp_buf)):
            self.disp_buf[i] = 0
        self._blit()
    
    def _blit(self):
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
        self.needs_scroll = self.force_scroll or text_px > self.dw
        
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
        """Copy visible window from pre-rendered buffer — fast row-slice copy"""
        db = self.disp_buf
        fb = self._full_buf
        dw = self.dw
        fw = self._full_w
        rh = self.row_h
        
        # Clear display buffer
        for i in range(len(db)):
            db[i] = 0
        
        if fb is None:
            self._blit()
            return
        
        if self.needs_scroll:
            src_x = self.scroll_x
        else:
            src_x = -(dw - fw) // 2
        
        # Calculate visible overlap
        # src_x is where display pixel 0 maps to in the full buffer
        copy_start = max(0, src_x)           # first src pixel to copy
        copy_end = min(fw, src_x + dw)       # last src pixel (exclusive)
        
        if copy_start >= copy_end:
            self._blit()
            return
        
        dst_offset = (copy_start - src_x) * 2   # where in display row to start
        copy_bytes = (copy_end - copy_start) * 2 # bytes to copy per row
        
        # Row-based slice copy (26 iterations, not 6240)
        for row in range(rh):
            si = row * fw * 2 + copy_start * 2
            di = row * dw * 2 + dst_offset
            db[di:di + copy_bytes] = fb[si:si + copy_bytes]
        
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
        
        if self.scroll_x > self._full_w + 40:
            self.scroll_x = -self.dw
        
        self._window()
        self.last_scroll = now
