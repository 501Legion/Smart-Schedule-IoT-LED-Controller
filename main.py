# -*- coding: utf-8 -*-
import math
import time
import os
import datetime
import threading
import subprocess

import requests
from rpi_ws281x import PixelStrip, Color

import config

# ===================== CONFIGURATION =====================
n = config.PIXELS
p = config.GPIOPIN
barcolourlist = config.BARCOL           # ¯  Work color [(r,g,b), ...]
eventcolourlist = config.EVENTCOL       # Event color
schedule = config.SCHEDULE              # Hardcoded schedule
googlecalbool = config.GOOGLECALBOOL
ignorehardcoded = config.IGNORE_HARDCODED
displayevents = config.DISPLAY_EVENTS
twocolor = getattr(config, 'TWOCOL', True)
flip_display = getattr(config, 'FLIP', False)

calendar_id = config.CALENDAR
api_key = config.APIKEY
timezone_str = "Asia/Seoul"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TZ_KST = datetime.timezone(datetime.timedelta(hours=9))

# TTS mode: "gtts" (mp3 + CLI output) / "local" (espeak-ng)
# Default to "gtts" so it behaves like test.py unless overridden in config.py.
TTS_MODE = getattr(config, 'TTS_MODE', 'gtts')
tts_lock = threading.Lock()  # serialize audio playback so ALSA device isn't busy

# --- Calculate meeting/bar overlap color ---
try:
    c1 = barcolourlist[0]
    c2 = eventcolourlist[0]
    overcol = tuple(
        map(lambda x: min(255, int(2 * sum(x) / len(x))), zip(*[c1, c2]))
    )
except Exception:
    overcol = (255, 255, 255)

# Refresh Settings
checkevery = 0.1               # Main loop sleep time (seconds)
google_refresh_interval = 5    # Google Calendar refresh interval (seconds)

# Global State
# Appointments: [{"start": "HH:MM:SS+09:00", "end": "...", "summary": "Title"}, ...]
current_appointment_times = []
last_google_check = 0
calendar_thread_running = False
processed_alarms = set()       # Alarms already triggered (5 min/start time)

# [NEW] Hide events already started from display (cleared at 00:00)
hidden_events = set()          # ev["start"] string

# [NEW] Flag to control rainbow animation (once per day)
rainbow_done_for_today = False
last_rainbow_date = None

anim_offset = 0.0              # Wave animation phase

# Track calendar refresh so LED updates as soon as data changes
calendar_update_event = threading.Event()
last_event_signature = None

# Alarm/flash tuning
START_ALARM_WINDOW_MIN = 1.5   # Tolerance window around start time (minutes)
ALARM_FLASHES = 5
ALARM_ON_DURATION = 0.28
ALARM_OFF_DURATION = 0.24

# ===================== SINGLETON LOCK =====================
# Prevent multiple instances (test.py then main.py) from fighting over the LED driver.
LOCK_PATH = "/tmp/iot_main.lock"


def acquire_lock():
    """Create a lock file to ensure only one instance runs."""
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        return fd
    except FileExistsError:
        raise RuntimeError(
            f"Another instance may be running (lock: {LOCK_PATH}). "
            "If not, remove the file and retry."
        )


def release_lock(fd):
    """Release the lock file."""
    try:
        os.close(fd)
    except Exception:
        pass
    try:
        os.remove(LOCK_PATH)
    except Exception:
        pass


def off(strip):
    """Clear all pixels immediately."""
    for i in range(n):
        strip.setPixelColor(i, Color(0, 0, 0))
    strip.show()


def flip_strip(strip):
    """Reverses the strip content if FLIP config is active."""
    pixel_data = [strip.getPixelColor(i) for i in range(n)]
    for i in range(n):
        strip.setPixelColor(i, pixel_data[n - 1 - i])


# ===================== TTS UTILS =====================
def format_time_korean(dt=None):
    """Format time as 'HH시 MM분' (Korean)."""
    if dt is None:
        dt = datetime.datetime.now()
    return f"{dt.hour}시 {dt.minute}분"


# NOTE: Need to specify an ALSA device that supports audio output.
# Test with speaker-test to find the correct device!
# The user confirmed 'hw:2,0' works (same as test.py).
ALSA_DEVICE = "hw:2,0"

# ---- 1) gTTS (mp3 + CLI output via mpg123) ----
def _speak_gtts_worker(text: str):
    try:
        from gtts import gTTS
        print("[TTS gTTS worker]", text)
        audio_path = os.path.join(BASE_DIR, "tts_alert.mp3")

        with tts_lock:
            tts = gTTS(text=text, lang='ko')
            
            print("[TTS] mp3 generating...")
            tts.save(audio_path)
            print("[TTS] mp3 saved:", audio_path)

            # Play exactly like test.py: mpg123 with -a hw:2,0
            print(f"[TTS] Playing audio via mpg123 to device {ALSA_DEVICE}...")
            subprocess.run(
                ["mpg123", "-q", "-a", ALSA_DEVICE, audio_path],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print("[TTS] Playback finished.")
        
    except FileNotFoundError:
        print("[TTS gTTS ERROR] mpg123 not found. Please run 'sudo apt install mpg123'.")
    except subprocess.CalledProcessError as e:
        print(f"[TTS gTTS ERROR] mpg123 failed to play audio (check device/permissions): {e}")
    except Exception as e:
        print("[TTS gTTS ERROR] Other Exception:", e)


def speak_gtts_async(text: str):
    """Runs gTTS save and play in a background thread."""
    try:
        t = threading.Thread(target=_speak_gtts_worker, args=(text,), daemon=True)
        t.start()
    except Exception as e:
        print("[TTS gTTS Thread ERROR]", e)


# ---- 2) espeak-ng (local TTS, fast) ----
# The ALSA_DEVICE constant is now defined above for use here and in gTTS worker.
# ALSA_DEVICE is already defined as "hw:2,0"
def speak_espeak(text: str):
    try:
        print(f"[TTS espeak] '{text}' -> {ALSA_DEVICE}")

        with tts_lock:
            # espeak-ng generates WAV data to stdout
            p1 = subprocess.Popen(
                ["espeak-ng", "-v", "ko", "-s", "170", "--stdout", text],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )

            # aplay plays the WAV data to the specified ALSA device
            p2 = subprocess.Popen(
                ["aplay", "-q", "-D", ALSA_DEVICE],
                stdin=p1.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            p1.stdout.close()
            p1.wait()
            p2.wait()

    except FileNotFoundError:
        print("[TTS espeak ERROR] espeak-ng not found. sudo apt install espeak-ng")
    except Exception as e:
        print("[TTS espeak ERROR]", e)


def speak(text: str):
    """
    Unified speak() function:
    - TTS_MODE == "local"  -> espeak-ng (Fast, local / CLI setup needed)
    - Otherwise ("gtts")  -> gTTS + mp3 (Better quality, CLI setup needed)
    """
    mode = (TTS_MODE or "gtts").lower()
    print(f"[TTS] mode={mode}, text={text}")
    if mode == "local":
        speak_espeak(text)
    else:
        speak_gtts_async(text)


# ===================== ANIMATIONS =====================
def startup_test(strip):
    """Diagnostic RGB Wipe on boot."""
    print("--- DIAGNOSTIC: Testing LEDs ---")
    colors = [Color(255, 0, 0), Color(0, 255, 0), Color(0, 0, 255)]
    for color in colors:
        for i in range(n):
            strip.setPixelColor(i, color)
        strip.show()
        time.sleep(0.1)
    off(strip)


def flash_alarm(strip, flashes=ALARM_FLASHES):
    """Flashes the entire strip with a nice Blue color to signal meeting start."""
    print("\n!!! ALARM: Meeting Starting !!!")
    nice_blue = Color(0, 140, 255)

    for _ in range(flashes):
        # ON
        for i in range(n):
            strip.setPixelColor(i, nice_blue)
        if flip_display:
            flip_strip(strip)
        strip.show()
        time.sleep(ALARM_ON_DURATION)

        # OFF
        off(strip)
        time.sleep(ALARM_OFF_DURATION)


def wheel(pos):
    """Rainbow color generator."""
    if pos < 85:
        return Color(pos * 3, 255 - pos * 3, 0)
    elif pos < 170:
        pos -= 85
        return Color(255 - pos * 3, 0, pos * 3)
    else:
        pos -= 170
        return Color(0, pos * 3, 255 - pos * 3)


def rainbow_cycle(strip, wait_ms=20, iterations=3):
    """Plays a rainbow animation (Good for 'Hometime')."""
    print("--- ANIMATION: Rainbow ---")
    for j in range(256 * iterations):
        for i in range(n):
            strip.setPixelColor(i, wheel((int(i * 256 / n) + j) & 255))
        strip.show()
        time.sleep(wait_ms / 1000.0)
    off(strip)


def anim_restore(strip, hoursin, clockin, clockout):
    """Wipes the bar from 0 to current time (Good for 'Work Start')."""
    print("--- ANIMATION: Work Start Wipe ---")
    target_index = hourtoindex(hoursin, clockin, clockout)
    col = barcolourlist[0]

    for i in range(target_index):
        if valid(i):
            strip.setPixelColor(i, Color(*col))
            if flip_display:
                flip_strip(strip)
            strip.show()
            time.sleep(0.02)


# ===================== TIME UTILS =====================
def timetohour(time_string):
    try:
        clean_time = time_string.split('+')[0].split('-')[0]
        h, m, s = map(int, clean_time.split(":"))
        return h + m / 60 + s / 3600
    except Exception:
        return 0


def hourtoindex(hoursin, clockin, clockout):
    if clockout == clockin:
        return 0
    percentage = (hoursin - clockin) / (clockout - clockin)
    index = int(math.floor(n * percentage))
    if index < 0:
        index = -1
    if index >= n:
        return n
    return index


def valid(index):
    return 0 <= index < n


def whatday(weekday):
    nameofday = ['monday', 'tuesday', 'wednesday', 'thursday',
                 'friday', 'saturday', 'sunday']
    return nameofday[int(weekday)]


# ===================== GOOGLE CALENDAR THREAD =====================
def fetch_calendar_data():
    """
    Fetches calendar events for today from the Google Calendar API.
    Updates start/end/summary in current_appointment_times.
    """
    global current_appointment_times, calendar_thread_running
    global last_event_signature, calendar_update_event
    calendar_thread_running = True
    try:
        # Use explicit +09:00 timezone so midnight-to-midnight in Korea is fetched correctly.
        now = datetime.datetime.now(TZ_KST)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()

        url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
        params = {
            'key': api_key,
            'timeMin': start_of_day,
            'timeMax': end_of_day,
            'singleEvents': 'True',
            'orderBy': 'startTime',
            'timeZone': timezone_str
        }

        print("[CAL] Requesting:", url)
        resp = requests.get(url, params=params, timeout=20)
        data = resp.json()

        if "error" in data:
            print("[CAL ERROR]", data['error']['message'])
        else:
            items = data.get("items", [])
            print(f"[CAL] Total events found: {len(items)}")

            events = []
            for item in items:
                if item.get("status") == "cancelled":
                    continue

                start_obj = item.get("start", {})
                end_obj = item.get("end", {})
                summary = item.get("summary", "No Title Event")

                start_raw = start_obj.get("dateTime") or start_obj.get("date")
                end_raw = end_obj.get("dateTime") or end_obj.get("date")

                # Only process timed events (ignore all-day events)
                if isinstance(start_raw, str) and 'T' in start_raw and isinstance(end_raw, str) and 'T' in end_raw:
                    start_time = start_raw.split('T')[1]
                    end_time = end_raw.split('T')[1]
                    events.append({
                        "start": start_time,
                        "end": end_time,
                        "summary": summary
                    })

            print(f"[CAL] Timed events processed: {len(events)}")
            # Detect actual changes so we can refresh LEDs immediately.
            new_sig = [(ev["start"], ev["end"], ev.get("summary", "")) for ev in events]
            changed = (new_sig != last_event_signature)

            current_appointment_times = events
            last_event_signature = new_sig

            if changed:
                calendar_update_event.set()
                print("[CAL] Events changed, display refresh requested.")
            else:
                print("[CAL] No calendar changes detected.")

    except Exception as e:
        print("[CAL Thread ERROR]", e)
    calendar_thread_running = False


def trigger_calendar_update():
    if not calendar_thread_running:
        t = threading.Thread(target=fetch_calendar_data)
        t.daemon = True
        t.start()


# ===================== RENDER & ALARMS =====================
def check_alarms(strip, events, hoursin):
    """
    Alarm Logic:
    - 5 minutes before: TTS announcement.
    - At start time: TTS announcement + LED flash.
    Started events are marked as processed to prevent re-triggering.
    """
    global processed_alarms, hidden_events

    for ev in events:
        try:
            start_str = ev["start"]
            start_h = timetohour(start_str)
            title = ev.get("summary", "No Title Event")

            # Time difference in minutes
            diff_min = (start_h - hoursin) * 60.0

            # Debug near alarm window to verify timing
            if -2 <= diff_min <= 7:
                print(f"[ALARM DEBUG] '{title}' start={start_str}, diff_min={diff_min:.2f}")

            # 1) 5 Minutes Before Alarm (4 to 6 minutes range)
            key_5 = f"{start_str}_5"
            if key_5 not in processed_alarms and 4 <= diff_min <= 6:
                now_dt = datetime.datetime.now(TZ_KST)
                t_text = format_time_korean(now_dt)
                speak(f"{title}, 현재 시간 {t_text}, 5분 후 시작합니다.")
                processed_alarms.add(key_5)

            # 2) At Start Alarm (window widened slightly to handle late fetch)
            key_0 = f"{start_str}_0"
            if key_0 not in processed_alarms and -START_ALARM_WINDOW_MIN <= diff_min <= START_ALARM_WINDOW_MIN:
                now_dt = datetime.datetime.now(TZ_KST)
                t_text = format_time_korean(now_dt)
                speak(f"{title}, {t_text}, 지금 시작합니다.")
                flash_alarm(strip)
                processed_alarms.add(key_0)

                # Mark the event to be hidden from the bar display
                hidden_events.add(start_str)

        except Exception as e:
            print("[ALARM ERROR]", e)


def addevents(strip, events, clockin, clockout, current_hour):
    """Draws event markers onto the strip."""
    if not events:
        return

    color_index = 0
    for ev in events:
        try:
            # Skip events that have just started and are hidden
            if ev["start"] in hidden_events:
                continue

            start_t = timetohour(ev["start"])
            end_t = timetohour(ev["end"])
            idx = hourtoindex(start_t, clockin, clockout)

            if not valid(idx):
                continue

            # Use overlap color if the event is currently active, otherwise cycle colors
            if start_t <= current_hour <= end_t:
                rgb = overcol
            else:
                rgb = eventcolourlist[color_index % len(eventcolourlist)]

            strip.setPixelColor(idx, Color(*rgb))
            color_index += 1
        except Exception:
            pass


def render_strip(strip, hoursin, clockin, clockout, event_times, anim_phase):
    """
    Renders the progress bar (with wave animation) and event markers.
    """
    # 1. Clear Buffer
    for i in range(n):
        strip.setPixelColor(i, Color(0, 0, 0))

    base_color = barcolourlist[0]
    r, g, b = base_color

    # Calculate bar fill index
    bar_upto = hourtoindex(hoursin, clockin, clockout)
    if bar_upto < 0:
        bar_upto = 0
    if bar_upto > n:
        bar_upto = n

    # Wave parameters
    wave_count = 1.6         # Number of waves (1-2 is typical)
    min_brightness = 0.35    # Minimum brightness for the wave
    max_brightness = 1.3     # Maximum brightness (can exceed 1.0 for a brighter peak)
    secondary_mix = 0.35     # Blends a slower secondary wave for a "water" feel

    for i in range(bar_upto):
        if not valid(i):
            continue

        if bar_upto <= 1:
            pos = 0.0
        else:
            pos = i / (bar_upto - 1)

        # Two blended sine waves to create a flowing ripple
        angle = 2 * math.pi * wave_count * pos + anim_phase
        primary = 0.5 + 0.5 * math.sin(angle)
        secondary = 0.5 + 0.5 * math.sin(angle * 0.6 - anim_phase * 0.4)
        wave = (1 - secondary_mix) * primary + secondary_mix * secondary

        # Apply brightness modulation with a slight leading taper
        brightness = min_brightness + (max_brightness - min_brightness) * wave
        brightness *= (0.85 + 0.15 * (1 - pos))
        brightness = max(min_brightness, min(max_brightness, brightness))

        rr = int(min(255, r * brightness))
        gg = int(min(255, g * brightness))
        bb = int(min(255, b * brightness))

        strip.setPixelColor(i, Color(rr, gg, bb))

    # Add event markers
    if displayevents:
        addevents(strip, event_times, clockin, clockout, hoursin)

    # Indicate calendar update in progress (first pixel is blue)
    if calendar_thread_running:
        strip.setPixelColor(0, Color(0, 0, 255))

    if flip_display:
        flip_strip(strip)

    strip.show()


# ===================== MAIN LOOP =====================
def progress_bar(strip):
    global last_google_check, anim_offset
    global rainbow_done_for_today, last_rainbow_date
    global processed_alarms, hidden_events

    startup_test(strip)
    print("Starting Main Loop...")
    print("TTS mode:", TTS_MODE)

    # Initial TTS test
    try:
        speak("시스템을 시작합니다. TTS 테스트 완료.")
    except Exception as e:
        print("[TTS STARTUP TEST ERROR]", e)

    trigger_calendar_update()
    last_google_check = time.time()

    clockin, clockout = 9.0, 18.0
    was_working = False

    while True:
        try:
            now = datetime.datetime.now(TZ_KST)
            hoursin = now.hour + now.minute / 60 + now.second / 3600
            dayname = whatday(now.weekday())

            # Check for day change and reset daily states
            today_str = now.date().isoformat()
            if last_rainbow_date != today_str:
                print("[DAY CHANGE] Reset daily states")
                rainbow_done_for_today = False
                processed_alarms = set()
                hidden_events = set()
                last_rainbow_date = today_str

            # Detect if calendar data was refreshed in the background
            events_updated = False
            if calendar_update_event.is_set():
                events_updated = True
                calendar_update_event.clear()
                print("[CAL] New events received; refreshing display.")

            # --- 1. Calendar Update Check ---
            if googlecalbool and (time.time() - last_google_check) > google_refresh_interval:
                trigger_calendar_update()
                last_google_check = time.time()

            local_events = list(current_appointment_times)
            if events_updated:
                current_start_times = {
                    ev.get("start") for ev in local_events
                    if isinstance(ev, dict) and ev.get("start")
                }
                processed_alarms = {
                    key for key in processed_alarms
                    if key.rsplit("_", 1)[0] in current_start_times
                }
                hidden_events = {s for s in hidden_events if s in current_start_times}

            # --- 2. Determine Work Hours ---
            if ignorehardcoded and local_events:
                # Use first/last event times to define the work bar span
                try:
                    first_ev = local_events[0]
                    last_ev = local_events[-1]
                    clockin = timetohour(first_ev["start"])
                    clockout = timetohour(last_ev["end"])
                except Exception:
                    pass
            else:
                # Use hardcoded schedule from config
                try:
                    clockin = float(schedule[dayname][0]['clockin'])
                    clockout = float(schedule[dayname][0]['clockout'])
                except Exception:
                    clockin, clockout = 9.0, 18.0

            # --- 3. Alarm Checks ---
            check_alarms(strip, local_events, hoursin)

            # --- 4. Animation Phase Update ---
            anim_offset += 0.08

            # --- 5. Event Completion Check ---
            last_event_end = None
            if local_events:
                try:
                    last_event_end = max(timetohour(ev["end"]) for ev in local_events)
                except Exception:
                    last_event_end = None

            all_events_done = (last_event_end is not None and hoursin >= last_event_end)

            # --- 6. Render & State Transition ---
            is_working = (clockin <= hoursin < clockout)

            if is_working:
                if not was_working:
                    # Transition from OFF to ON (Work Start)
                    anim_restore(strip, hoursin, clockin, clockout)
                    was_working = True

                render_strip(strip, hoursin, clockin, clockout, local_events, anim_offset)
            else:
                # Outside work hours
                if was_working:
                    # Transition from ON to OFF (Work End)
                    if not local_events:
                        print("Hometime detected by schedule. Playing Rainbow.")
                        rainbow_cycle(strip)
                    was_working = False

                off(strip)

            # --- 7. Event Completion Animation (only once per day) ---
            if local_events and all_events_done and not rainbow_done_for_today:
                print("All calendar events finished. Playing Rainbow.")
                rainbow_cycle(strip)
                rainbow_done_for_today = True

            # Throttle loop for stable animation speed
            time.sleep(checkevery)
        except Exception as e:
            # Log and keep running so we don't exit silently
            print("[LOOP ERROR]", e)
            time.sleep(1)


if __name__ == "__main__":
    lock_fd = None
    try:
        lock_fd = acquire_lock()
        strip = PixelStrip(n, p, brightness=100, invert=False, channel=0)
        strip.begin()
        progress_bar(strip)
    except KeyboardInterrupt:
        if "strip" in locals():
            off(strip)
        print("Exiting by user interrupt.")
    except Exception as e:
        if "strip" in locals():
            off(strip)
        print("[FATAL]", e)
    finally:
        if lock_fd is not None:
            release_lock(lock_fd)
