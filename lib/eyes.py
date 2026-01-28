"""
Animated Eyes for ESP32-S3-LCD-1.69
Pre-rendered buffer blit, non-blocking blink, expressions
"""
import time
import gc
from random import randint

# Colors (RGB888)
BLACK = 0x000000
WHITE = 0xFFFFFF
EYE_WHITE = 0xFFFFFF
IRIS_COLOR = 0x2288FF
PUPIL_COLOR = 0x000000

def _to565(color):
    r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
    c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return (c >> 8) & 0xFF, c & 0xFF

# Blink states
_IDLE = 0
_CLOSE_1 = 1
_CLOSE_2 = 2
_CLOSE_3 = 3
_CLOSED = 4

# Expression modes
EXPR_NORMAL = 0
EXPR_SLEEPY = 1
EXPR_ASLEEP = 2
EXPR_FOCUSED = 3    # Coding/editing — squinted, pupils center
EXPR_READING = 4    # Reading files/pages — pupils track left-right
EXPR_SEARCHING = 5  # Web search — pupils dart around quickly
EXPR_THINKING = 6   # LLM thinking — pupils look up
EXPR_TERMINAL = 7   # Running commands — slight squint, fixed center
EXPR_STRESSED = 8   # Long sustained work — wider eyes, faster blinks


class Eyes:
    def __init__(self, display):
        self.display = display
        self.width = display.width
        self.height = display.height
        
        # Eye parameters
        self.eye_width = 70
        self.eye_height = 80
        self.eye_spacing = 20
        self.corner_radius = 15
        self.pupil_size = 20
        self.iris_size = 40
        
        # Eye center positions
        self.left_eye_x = self.width // 2 - self.eye_width // 2 - self.eye_spacing // 2
        self.right_eye_x = self.width // 2 + self.eye_width // 2 + self.eye_spacing // 2
        self.eye_y = self.height // 2 - 20
        
        # Pupil offset
        self.pupil_offset_x = 0
        self.pupil_offset_y = 0
        self.prev_offset_x = 0
        self.prev_offset_y = 0
        
        # Expression
        self.expression = EXPR_NORMAL
        self._eyelid_pct = 0      # 0-100, how much eyelid covers from top
        self._target_lid = 0      # Target eyelid position
        self._lid_speed = 2       # % per frame for smooth transition
        self._prev_lid_pct = -1   # Track if lid changed (force rebuild)
        
        # Pre-compute colors
        self._c_white = _to565(EYE_WHITE)
        self._c_iris = _to565(IRIS_COLOR)
        self._c_pupil = _to565(PUPIL_COLOR)
        self._c_highlight = _to565(WHITE)
        self._c_black = _to565(BLACK)
        
        # Corner mask + eye buffers (all pre-allocated, never reallocated)
        self._corner_mask = self._build_corner_mask()
        buf_size = self.eye_width * self.eye_height * 2
        self._base_buf = bytearray(buf_size)    # Eye without eyelid
        self._eye_buf = bytearray(buf_size)     # Eye with eyelid (for blitting)
        self._black_row = bytes([_to565(BLACK)[0], _to565(BLACK)[1]] * self.eye_width)
        self._rebuild_base()
        self._apply_eyelid()
        
        # Blink state machine
        self._blink_state = _IDLE
        self._blink_time = 0
        
        # Blink timer
        self.last_blink = time.ticks_ms()
        self.next_blink = randint(3000, 6000)
        
        # Idle movement
        self.idle_mode = True
        self.last_move = time.ticks_ms()
        self.next_move = randint(2000, 4000)
        
        # Reading animation state
        self._read_dir = 1       # 1 = left-to-right, -1 = right-to-left
        self._read_pos = -10     # Current x position (-10 to 10)
        self._read_speed = 200   # ms per step
        self._last_read = time.ticks_ms()
        
        # Searching animation state
        self._search_target_x = 0
        self._search_target_y = 0
        self._last_search = time.ticks_ms()
        self._search_speed = 300  # ms per dart
        
        # Eyelid geometry
        ew = self.eye_width
        eh = self.eye_height
        self._lx = self.left_eye_x - ew // 2
        self._rx = self.right_eye_x - ew // 2
        self._top = self.eye_y - eh // 2
        self._half = eh // 2
        
        # Initialize display
        self.display.fill(BLACK)
        self._blit_both()
    
    def set_expression(self, expr):
        """Set expression based on activity"""
        if expr == 'asleep':
            self.expression = EXPR_ASLEEP
            self._target_lid = 100
            self._lid_speed = 1
            self.look_at(0, 1.0)
        elif expr == 'sleepy':
            self.expression = EXPR_SLEEPY
            self._target_lid = 45
            self._lid_speed = 1
            self.look_at(0, 1.0)
        elif expr == 'focused':
            # Coding/editing — squinted, looking straight ahead
            self.expression = EXPR_FOCUSED
            self._target_lid = 25     # Slight squint from top
            self._lid_speed = 2
            self.look_at(0, 0.1)      # Slightly down, focused
            self.next_blink = randint(5000, 10000)  # Blink less when focused
        elif expr == 'reading':
            # Reading — eyes track left to right
            self.expression = EXPR_READING
            self._target_lid = 10     # Slightly narrowed
            self._lid_speed = 2
            self._read_pos = -10      # Start from left
            self._read_dir = 1
            self._last_read = time.ticks_ms()
        elif expr == 'searching':
            # Searching — pupils dart around curiously
            self.expression = EXPR_SEARCHING
            self._target_lid = 0      # Wide open, curious
            self._lid_speed = 3
            self._last_search = time.ticks_ms()
            self.next_blink = randint(2000, 4000)  # Normal blinking
        elif expr == 'thinking':
            # Thinking — look up and to the side
            self.expression = EXPR_THINKING
            self._target_lid = 0
            self._lid_speed = 2
            self.look_at(0.3, -0.8)   # Up and slightly right
            self.next_blink = randint(4000, 8000)  # Thoughtful, less blinking
        elif expr == 'terminal':
            # Running commands — slight squint, fixed stare
            self.expression = EXPR_TERMINAL
            self._target_lid = 20     # Slight squint
            self._lid_speed = 2
            self.look_at(0, 0)        # Dead center
            self.next_blink = randint(6000, 12000)  # Barely blinks
        elif expr == 'stressed':
            # Long work session — wider eyes, faster blinks
            self.expression = EXPR_STRESSED
            self._target_lid = 0      # Eyes wide
            self._lid_speed = 3
            self.next_blink = randint(1500, 3000)  # Blinks a lot
        elif expr == 'done':
            # Task complete — normal eyes, slight look down (satisfied)
            self.expression = EXPR_NORMAL
            self._target_lid = 0
            self._lid_speed = 3
            self.look_at(0, 0.2)
        else:
            self.expression = EXPR_NORMAL
            self._target_lid = 0
            self._lid_speed = 3
    
    def _build_corner_mask(self):
        r = self.corner_radius
        ew = self.eye_width
        eh = self.eye_height
        mask = []
        for cy in range(r):
            for cx in range(r):
                if (r - cx) ** 2 + (r - cy) ** 2 > r * r:
                    mask.append(cy * ew + cx)
                    mask.append(cy * ew + (ew - 1 - cx))
                    mask.append((eh - 1 - cy) * ew + cx)
                    mask.append((eh - 1 - cy) * ew + (ew - 1 - cx))
        return mask
    
    def _rebuild_base(self):
        """Rebuild base eye buffer in-place (no allocation)"""
        ew = self.eye_width
        eh = self.eye_height
        buf = self._base_buf
        
        b_hi, b_lo = self._c_black
        w_hi, w_lo = self._c_white
        
        # Fill with white
        for i in range(0, len(buf), 2):
            buf[i] = w_hi
            buf[i + 1] = w_lo
        
        # Black out corner pixels
        for offset in self._corner_mask:
            idx = offset * 2
            buf[idx] = b_hi
            buf[idx + 1] = b_lo
        
        # Draw iris
        i_hi, i_lo = self._c_iris
        ix = ew // 2 + self.pupil_offset_x - self.iris_size // 2
        iy = eh // 2 + self.pupil_offset_y - self.iris_size // 2
        for y in range(max(0, iy), min(eh, iy + self.iris_size)):
            row_off = y * ew
            for x in range(max(0, ix), min(ew, ix + self.iris_size)):
                idx = (row_off + x) * 2
                buf[idx] = i_hi
                buf[idx + 1] = i_lo
        
        # Draw pupil
        p_hi, p_lo = self._c_pupil
        px = ew // 2 + self.pupil_offset_x - self.pupil_size // 2
        py = eh // 2 + self.pupil_offset_y - self.pupil_size // 2
        for y in range(max(0, py), min(eh, py + self.pupil_size)):
            row_off = y * ew
            for x in range(max(0, px), min(ew, px + self.pupil_size)):
                idx = (row_off + x) * 2
                buf[idx] = p_hi
                buf[idx + 1] = p_lo
        
        # Draw highlight
        h_hi, h_lo = self._c_highlight
        hx = px + 4
        hy = py + 4
        for y in range(max(0, hy), min(eh, hy + 5)):
            row_off = y * ew
            for x in range(max(0, hx), min(ew, hx + 5)):
                idx = (row_off + x) * 2
                buf[idx] = h_hi
                buf[idx + 1] = h_lo
    
    def _apply_eyelid(self):
        """Apply eyelid to base buffer in-place (no allocation)"""
        ew = self.eye_width
        eh = self.eye_height
        lid_rows = (eh * self._eyelid_pct) // 100
        
        # Copy base into eye buffer
        self._eye_buf[:] = self._base_buf
        
        # Black out top rows for eyelid
        if lid_rows > 0:
            row_bytes = ew * 2
            for y in range(lid_rows):
                start = y * row_bytes
                self._eye_buf[start:start + row_bytes] = self._black_row
        
        self._prev_lid_pct = self._eyelid_pct
    
    def _blit_eye(self, cx, cy):
        x = cx - self.eye_width // 2
        y = cy - self.eye_height // 2
        self.display.set_window(x, y, x + self.eye_width - 1, y + self.eye_height - 1)
        self.display.write_data(self._eye_buf)
    
    def _blit_both(self):
        """Blit both eyes — buffer already includes eyelid, so each is atomic"""
        self._blit_eye(self.left_eye_x, self.eye_y)
        self._blit_eye(self.right_eye_x, self.eye_y)
        self.prev_offset_x = self.pupil_offset_x
        self.prev_offset_y = self.pupil_offset_y
    
    def _draw_eyelids(self, step):
        """Draw blink eyelids at step (1-3) — additive black on both eyes"""
        ew = self.eye_width
        lid = (self._half * step) // 3
        cy = self.eye_y
        # Top eyelids (both eyes together, then bottom together)
        self.display.fill_rect(self._lx, self._top, ew, lid, BLACK)
        self.display.fill_rect(self._rx, self._top, ew, lid, BLACK)
        self.display.fill_rect(self._lx, cy + self._half - lid, ew, lid, BLACK)
        self.display.fill_rect(self._rx, cy + self._half - lid, ew, lid, BLACK)
    
    def _draw_closed_line(self):
        """Draw closed-eye line at the bottom of the eye area"""
        ew = self.eye_width
        eh = self.eye_height
        # Bottom of eye area (where the eyelid finishes closing)
        bottom_y = self.eye_y + eh // 2 - 4
        self.display.fill_rect(self._lx, bottom_y, ew, 4, EYE_WHITE)
        self.display.fill_rect(self._rx, bottom_y, ew, 4, EYE_WHITE)
    
    def _start_blink(self):
        self._blink_state = _CLOSE_1
        self._blink_time = time.ticks_ms()
        self._draw_eyelids(1)
    
    def _update_blink(self):
        if self._blink_state == _IDLE:
            return False
        
        now = time.ticks_ms()
        elapsed = time.ticks_diff(now, self._blink_time)
        
        if self._blink_state == _CLOSE_1:
            if elapsed >= 15:
                self._draw_eyelids(2)
                self._blink_state = _CLOSE_2
                self._blink_time = now
        
        elif self._blink_state == _CLOSE_2:
            if elapsed >= 15:
                self._draw_eyelids(3)
                self._blink_state = _CLOSE_3
                self._blink_time = now
        
        elif self._blink_state == _CLOSE_3:
            if elapsed >= 15:
                ew = self.eye_width
                eh = self.eye_height
                self.display.fill_rect(self._lx, self._top, ew, eh, BLACK)
                self.display.fill_rect(self._rx, self._top, ew, eh, BLACK)
                self._draw_closed_line()
                self._blink_state = _CLOSED
                self._blink_time = now
        
        elif self._blink_state == _CLOSED:
            if elapsed >= 60:
                # Eyes open — buffer already has eyelid baked in
                self._blit_both()
                self._blink_state = _IDLE
                self.last_blink = time.ticks_ms()
                if self.expression == EXPR_SLEEPY:
                    self.next_blink = randint(6000, 12000)
                else:
                    self.next_blink = randint(3000, 6000)
        
        return self._blink_state != _IDLE
    
    def _update_eyelid(self):
        """Smoothly transition eyelid to target position"""
        if self._eyelid_pct == self._target_lid:
            return False
        
        if self._eyelid_pct < self._target_lid:
            self._eyelid_pct = min(self._eyelid_pct + self._lid_speed, self._target_lid)
        else:
            self._eyelid_pct = max(self._eyelid_pct - self._lid_speed, self._target_lid)
        
        # Apply eyelid to cached base buffer (cheap — no pixel loops)
        self._apply_eyelid()
        self._blit_both()
        
        # When fully closed, draw the sleeping line
        if self._eyelid_pct >= 100:
            gc.collect()
            self._draw_closed_line()
        
        return True
    
    def _needs_rebuild(self):
        """Check if eye buffer needs rebuilding"""
        return (self.pupil_offset_x != self.prev_offset_x or
                self.pupil_offset_y != self.prev_offset_y or
                self._eyelid_pct != self._prev_lid_pct)
    
    def update_pupils(self):
        if (self.pupil_offset_x == self.prev_offset_x and
            self.pupil_offset_y == self.prev_offset_y):
            return
        self._rebuild_base()
        self._apply_eyelid()
        self._blit_both()
    
    def look_at(self, x, y):
        max_x = 12
        max_y = 20  # More vertical range for looking up/down
        self.pupil_offset_x = int(x * max_x)
        self.pupil_offset_y = int(y * max_y)
    
    def look_random(self):
        x = randint(-10, 10) / 10
        y = randint(-5, 5) / 10
        self.look_at(x, y)
    
    def update(self):
        """Non-blocking update — call every frame"""
        if self._update_blink():
            return
        
        # Smooth eyelid transitions
        if self._update_eyelid():
            return
        
        now = time.ticks_ms()
        
        # No blinking or movement when fully asleep
        if self.expression == EXPR_ASLEEP and self._eyelid_pct >= 100:
            return
        
        # Trigger blink?
        if time.ticks_diff(now, self.last_blink) > self.next_blink:
            self._start_blink()
            return
        
        # Expression-specific animations
        if self.expression == EXPR_READING:
            self._update_reading(now)
        elif self.expression == EXPR_SEARCHING:
            self._update_searching(now)
        elif self.expression == EXPR_THINKING:
            self._update_thinking(now)
        elif self.expression == EXPR_STRESSED:
            self._update_stressed(now)
        elif self.expression == EXPR_FOCUSED or self.expression == EXPR_TERMINAL:
            # Focused: slow deliberate scanning, like reading code
            if time.ticks_diff(now, self.last_move) > 1500:
                x = randint(-6, 6) / 10
                y = randint(-2, 3) / 10
                self.look_at(x, y)
                self.last_move = now
        elif self.expression == EXPR_SLEEPY:
            if time.ticks_diff(now, self.last_move) > self.next_move:
                x = randint(-2, 2) / 10
                self.look_at(x, 1.0)
                self.next_move = randint(4000, 8000)
                self.last_move = now
        elif self.idle_mode:
            if time.ticks_diff(now, self.last_move) > self.next_move:
                self.look_random()
                self.next_move = randint(2000, 4000)
                self.last_move = now
        
        self.update_pupils()
    
    def _update_reading(self, now):
        """Reading animation — pupils sweep left to right like reading text"""
        if time.ticks_diff(now, self._last_read) < self._read_speed:
            return
        self._last_read = now
        
        self._read_pos += self._read_dir * 2
        
        # At end of line, pause briefly then sweep back
        if self._read_pos >= 10:
            self._read_dir = -1
            self._read_speed = 80    # Quick snap back (saccade)
        elif self._read_pos <= -10:
            self._read_dir = 1
            self._read_speed = 200   # Slow read left-to-right
            # Small downward shift each "line"
        
        self.look_at(self._read_pos / 10, 0.1)
    
    def _update_searching(self, now):
        """Searching animation — pupils dart around curiously"""
        if time.ticks_diff(now, self._last_search) < self._search_speed:
            return
        self._last_search = now
        
        # Quick dart to a new position
        self._search_target_x = randint(-8, 8) / 10
        self._search_target_y = randint(-4, 4) / 10
        self.look_at(self._search_target_x, self._search_target_y)
        
        # Vary timing — sometimes quick darts, sometimes a pause
        self._search_speed = randint(200, 600)
    
    def _update_thinking(self, now):
        """Thinking animation — eyes drift up-right, occasional slow movement"""
        if time.ticks_diff(now, self.last_move) > 3000:
            # Gentle drift while thinking — mostly looking up
            x = randint(1, 5) / 10
            y = randint(-10, -5) / 10  # Always looking up
            self.look_at(x, y)
            self.last_move = now
    
    def _update_stressed(self, now):
        """Stressed animation — slightly erratic movement, wider eyes"""
        if time.ticks_diff(now, self.last_move) > 1500:
            # Quick, slightly jittery movements
            x = randint(-6, 6) / 10
            y = randint(-3, 3) / 10
            self.look_at(x, y)
            self.last_move = now


def run(display):
    eyes = Eyes(display)
    print("Eyes running... Ctrl+C to stop")
    try:
        while True:
            eyes.update()
            time.sleep_ms(20)
    except KeyboardInterrupt:
        print("Stopped")
