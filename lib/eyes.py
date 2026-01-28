"""
Animated Eyes for ESP32-S3-LCD-1.69
Optimized with fast blinks to minimize flicker
"""
import time
from random import randint

# Moods
DEFAULT = 0
HAPPY = 1
ANGRY = 2
TIRED = 3
CURIOUS = 4

# Colors (RGB888)
BLACK = 0x000000
WHITE = 0xFFFFFF
PUPIL_COLOR = 0x000000
EYE_WHITE = 0xFFFFFF
IRIS_COLOR = 0x2288FF

class Eyes:
    def __init__(self, display):
        self.display = display
        self.width = display.width
        self.height = display.height
        
        # Eye parameters
        self.eye_width = 70
        self.eye_height = 80
        self.eye_spacing = 20
        self.pupil_size = 20
        self.iris_size = 40
        
        # Eye positions
        self.left_eye_x = self.width // 2 - self.eye_width // 2 - self.eye_spacing // 2
        self.right_eye_x = self.width // 2 + self.eye_width // 2 + self.eye_spacing // 2
        self.eye_y = self.height // 2 - 20
        
        # Pupil offset
        self.pupil_offset_x = 0
        self.pupil_offset_y = 0
        self.prev_offset_x = 0
        self.prev_offset_y = 0
        
        # Mood
        self.mood = DEFAULT
        
        # Auto-blink timer
        self.last_blink = time.ticks_ms()
        self.next_blink = randint(3000, 6000)
        
        # Idle movement
        self.idle_mode = True
        self.last_move = time.ticks_ms()
        self.next_move = randint(2000, 4000)
        
        # Initialize display
        self.display.fill(BLACK)
        self.draw_eyes_open()
    
    def draw_eye_white(self, cx, cy):
        """Draw the white of one eye"""
        ew = self.eye_width
        eh = self.eye_height
        y_start = cy - eh // 2
        self.display.fill_rect(cx - ew//2, y_start, ew, eh, EYE_WHITE)
    
    def draw_iris_pupil(self, cx, cy):
        """Draw iris and pupil"""
        iris_x = cx + self.pupil_offset_x - self.iris_size // 2
        iris_y = cy + self.pupil_offset_y - self.iris_size // 2
        self.display.fill_rect(iris_x, iris_y, self.iris_size, self.iris_size, IRIS_COLOR)
        
        pupil_x = cx + self.pupil_offset_x - self.pupil_size // 2
        pupil_y = cy + self.pupil_offset_y - self.pupil_size // 2
        self.display.fill_rect(pupil_x, pupil_y, self.pupil_size, self.pupil_size, PUPIL_COLOR)
        
        # Highlight
        self.display.fill_rect(pupil_x + 4, pupil_y + 4, 5, 5, WHITE)
    
    def draw_eyes_open(self):
        """Draw both eyes fully open"""
        self.draw_eye_white(self.left_eye_x, self.eye_y)
        self.draw_eye_white(self.right_eye_x, self.eye_y)
        self.draw_iris_pupil(self.left_eye_x, self.eye_y)
        self.draw_iris_pupil(self.right_eye_x, self.eye_y)
        self.prev_offset_x = self.pupil_offset_x
        self.prev_offset_y = self.pupil_offset_y
    
    def draw_eyes_closed(self):
        """Draw both eyes closed (just lines)"""
        ew = self.eye_width
        cy = self.eye_y
        
        # Left eye closed
        lx = self.left_eye_x
        self.display.fill_rect(lx - ew//2, cy - self.eye_height//2, 
                              ew, self.eye_height, BLACK)
        self.display.fill_rect(lx - ew//2, cy - 3, ew, 6, EYE_WHITE)
        
        # Right eye closed
        rx = self.right_eye_x
        self.display.fill_rect(rx - ew//2, cy - self.eye_height//2,
                              ew, self.eye_height, BLACK)
        self.display.fill_rect(rx - ew//2, cy - 3, ew, 6, EYE_WHITE)
    
    def blink_fast(self):
        """Quick blink - close then open"""
        self.draw_eyes_closed()
        time.sleep_ms(80)  # Brief pause with eyes closed
        self.draw_eyes_open()
    
    def update_pupils(self):
        """Update only the pupil positions if they moved"""
        if (self.pupil_offset_x == self.prev_offset_x and 
            self.pupil_offset_y == self.prev_offset_y):
            return
        
        # Clear old iris area (draw white over it)
        clear_size = self.iris_size + 4
        for cx in [self.left_eye_x, self.right_eye_x]:
            old_x = cx + self.prev_offset_x - clear_size // 2
            old_y = self.eye_y + self.prev_offset_y - clear_size // 2
            self.display.fill_rect(old_x, old_y, clear_size, clear_size, EYE_WHITE)
        
        # Draw new iris/pupil
        self.draw_iris_pupil(self.left_eye_x, self.eye_y)
        self.draw_iris_pupil(self.right_eye_x, self.eye_y)
        
        self.prev_offset_x = self.pupil_offset_x
        self.prev_offset_y = self.pupil_offset_y
    
    def look_at(self, x, y):
        """Look in direction (-1 to 1)"""
        max_offset = 12
        self.pupil_offset_x = int(x * max_offset)
        self.pupil_offset_y = int(y * max_offset)
    
    def look_random(self):
        """Random direction"""
        x = randint(-10, 10) / 10
        y = randint(-5, 5) / 10
        self.look_at(x, y)
    
    def look_center(self):
        """Look straight ahead"""
        self.look_at(0, 0)
    
    def set_mood(self, mood):
        """Set mood"""
        self.mood = mood
    
    def update(self):
        """Main update loop"""
        now = time.ticks_ms()
        
        # Check for blink
        if time.ticks_diff(now, self.last_blink) > self.next_blink:
            self.blink_fast()
            self.last_blink = time.ticks_ms()
            self.next_blink = randint(3000, 6000)
            return
        
        # Idle movement
        if self.idle_mode:
            if time.ticks_diff(now, self.last_move) > self.next_move:
                self.look_random()
                self.last_move = now
                self.next_move = randint(2000, 4000)
        
        # Update pupils if moved
        self.update_pupils()


def run(display):
    """Run eyes continuously"""
    eyes = Eyes(display)
    print("Eyes running... Ctrl+C to stop")
    
    try:
        while True:
            eyes.update()
            time.sleep_ms(30)
    except KeyboardInterrupt:
        print("Stopped")


def demo(display):
    """Demo"""
    eyes = Eyes(display)
    print("Demo running for 15 seconds...")
    
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < 15000:
        eyes.update()
        time.sleep_ms(30)
    
    print("Demo done!")
