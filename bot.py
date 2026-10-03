import math
import statistics
import sys
import time
from collections import deque

import capture
import config
from keys import VirtualKeys, is_down

MODES = ("hold", "tap", "jack", "spam")

RESET = "\x1b[0m"
GREEN = "\x1b[92m"
CYAN = "\x1b[96m"
DIM = "\x1b[90m"


def pick_glyphs():
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    if "utf" in encoding:
        return "\u2588\u2588", "\u2593\u2593", "\u2500\u2500"
    return "##", "==", "--"


class NullKeys:
    def down(self, key):
        pass

    def up(self, key):
        pass

    def tap(self, key, hold_ms=0):
        pass

    def release_all(self):
        pass


def brightness(color):
    return (color[0] + color[1] + color[2]) / 3.0


class Track:
    def __init__(self, off):
        self.bg = tuple(off)
        self.idle = deque(maxlen=240)
        self.recent = deque(maxlen=300)
        self.confirmed = False
        self.color = (0, 0, 0)
        self.covered = False
        self.saw_cover = False
        self.active_since = 0.0


class Lane:
    def __init__(self, spec, off):
        self.key = spec["key"]
        self.name = spec.get("name", spec["key"])
        self.points = [(int(p[0]), int(p[1])) for p in spec["points"]]
        if not self.points:
            raise ValueError(f"lane {self.name} has no sample points")
        self.trigger = min(max(int(spec.get("trigger", 0)), 0), len(self.points) - 1)
        self.tracks = [Track(off) for _ in self.points]
        self.active = False
        self.any_covered = False
        self.down = False
        self.press_at = 0.0
        self.off_since = None
        self.last_press = -1e9
        self.hits = 0

    def contrast(self):
        track = self.tracks[self.trigger]
        dr = track.color[0] - track.bg[0]
        dg = track.color[1] - track.bg[1]
        db = track.color[2] - track.bg[2]
        return math.sqrt(dr * dr + dg * dg + db * db)


class Bot:
    def __init__(self, cfg):
        self.cfg = cfg
        self.mode = cfg.get("mode", "hold")
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.region = config.region_of(cfg)
        specs = config.relative_points(cfg, self.region)
        self.off = tuple(int(c) for c in cfg["off_color"])
        self.lanes = [Lane(spec, self.off) for spec in specs]
        self.tolerance = float(cfg["tolerance"])
        self.note_floor = float(cfg.get("note_floor", 0))
        self.radius = int(cfg.get("sample_radius", 2))
        self.tap_hold_ms = float(cfg.get("tap_hold_ms", 35))
        self.hold_release_ms = float(cfg.get("hold_release_ms", 0))
        self.jack_window_ms = float(cfg.get("jack_window_ms", 40))
        self.spam_interval_ms = float(cfg.get("spam_interval_ms", 45))
        self.target_fps = float(cfg.get("target_fps", 0))
        self.auto_bg = bool(cfg.get("auto_background", True))
        self.learn_seconds = float(cfg.get("learn_seconds", 5.0))
        self.capture = capture.Capture(self.region)
        self.keys = NullKeys() if cfg.get("dry_run") else VirtualKeys(cfg.get("input_mode", "vk"))
        self.frames = 0
        self.latencies = deque(maxlen=4096)
        self.frame_times = deque(maxlen=4096)
        self.hud_on = bool(cfg.get("hud", True))
        self.glyph_on, self.glyph_held, self.glyph_idle = pick_glyphs()
        self._last_hud = 0.0
        self._running = False
        self._prev_frame = 0.0
        self._all_active = 0
        self._warned = None
        self._next_sanity = 0.0
        self._started_at = 0.0
        self._last_active_at = 0.0
        self._saturated_since = 0.0
        self.seconds = None

    def is_note(self, color, bg):
        dr = color[0] - bg[0]
        dg = color[1] - bg[1]
        db = color[2] - bg[2]
        if max(color) < self.note_floor:
            return False
        return math.sqrt(dr * dr + dg * dg + db * db) > self.tolerance

    def learn_background(self, now):
        learned = [track.bg for lane in self.lanes for track in lane.tracks if track.confirmed]
        for lane in self.lanes:
            for index, track in enumerate(lane.tracks):
                if track.confirmed:
                    continue
                if track.recent:
                    track.bg = min(track.recent, key=brightness)
                elif learned:
                    track.bg = min(learned, key=brightness)
                track.idle.clear()
                track.confirmed = True
                track.active_since = now
                print(f"  {lane.name} point {index}: background learned as {list(track.bg)}")

    def press(self, lane, now):
        lane.hits += 1
        lane.press_at = now
        lane.last_press = now
        lane.off_since = None
        lane.down = True
        self.latencies.append((now - self._prev_frame) * 1000.0)

    def release(self, lane):
        lane.down = False
        lane.off_since = None
        self.keys.up(lane.key)

    def update(self, now):
        buffer = self.capture.grab()
        for lane in self.lanes:
            for track, (x, y) in zip(lane.tracks, lane.points):
                color = capture.sample(buffer, self.capture.width, self.capture.height,
                                       x, y, self.radius)
                track.color = color
                track.recent.append(color)
                if self.auto_bg and not track.covered:
                    track.idle.append(color)
                    track.bg = min(track.idle, key=brightness)
                    track.confirmed = True
                covered = self.is_note(color, track.bg)
                if covered:
                    track.saw_cover = True
                if covered and not track.covered:
                    track.active_since = now
                track.covered = covered
            lane.active = lane.tracks[lane.trigger].covered
            lane.any_covered = any(track.covered for track in lane.tracks)

            if self.mode == "spam":
                if lane.active:
                    if lane.down and (now - lane.last_press) * 1000.0 >= self.spam_interval_ms:
                        self.keys.up(lane.key)
                        self.keys.down(lane.key)
                        lane.last_press = now
                        lane.hits += 1
                    elif not lane.down:
                        self.keys.down(lane.key)
                        self.press(lane, now)
                elif lane.down:
                    self.release(lane)
                continue

            if lane.active and not lane.down:
                self.keys.down(lane.key)
                self.press(lane, now)

            if not lane.down:
                continue

            held_ms = (now - lane.press_at) * 1000.0
            if self.mode == "tap":
                if held_ms >= self.tap_hold_ms:
                    self.release(lane)
                continue

            if not lane.any_covered:
                if lane.off_since is None:
                    lane.off_since = now
                off_ms = (now - lane.off_since) * 1000.0
                needed = self.hold_release_ms if self.mode == "hold" else self.jack_window_ms
                if off_ms >= needed:
                    self.release(lane)
                continue

            reconnected = lane.off_since is not None
            lane.off_since = None
            if self.mode == "jack" and reconnected:
                self.keys.up(lane.key)
                self.keys.down(lane.key)
                self.press(lane, now)

        self._all_active += sum(1 for lane in self.lanes if lane.active)
        if self.auto_bg:
            for lane in self.lanes:
                stuck = [track for track in lane.tracks
                         if track.covered and not track.confirmed
                         and now - track.active_since > self.learn_seconds]
                if stuck:
                    self.learn_background(now)
                    break

    def region_average(self):
        buffer = self.capture.grab()
        total = [0, 0, 0]
        pixels = 0
        for index in range(0, len(buffer) - 4, 4 * 7):
            total[0] += buffer[index + 2]
            total[1] += buffer[index + 1]
            total[2] += buffer[index]
            pixels += 1
        if not pixels:
            return (0, 0, 0)
        return tuple(value // pixels for value in total)

    def hud(self, now):
        if not self.hud_on:
            return
        if now - self._last_hud < 1.0 / 30.0:
            return
        self._last_hud = now
        cells = []
        for lane in self.lanes:
            if lane.active:
                cells.append(f"{GREEN}{self.glyph_on}{RESET}")
            elif lane.down:
                cells.append(f"{CYAN}{self.glyph_held}{RESET}")
            else:
                cells.append(f"{DIM}{self.glyph_idle}{RESET}")
        fps = 1000.0 / statistics.fmean(self.frame_times) if self.frame_times else 0.0
        hits = sum(lane.hits for lane in self.lanes)
        lat = statistics.fmean(self.latencies) if self.latencies else 0.0
        sys.stdout.write(
            f"\r{' '.join(cells)}  {self.mode:<5} {fps:6.0f} fps  hits {hits:<5} "
            f"lat {lat:4.1f} ms  [{self.panic_key} quit]"
        )
        sys.stdout.flush()

    def sanity_check(self, now):
        if now < self._next_sanity:
            return
        self._next_sanity = now + 2.0
        if self._all_active:
            self._last_active_at = now
            self._saturated_since = self._saturated_since or now
        else:
            self._saturated_since = 0.0
        if self._saturated_since and now - self._saturated_since > 2.0:
            message = "warning: every lane reads as a note; recalibrate or lower tolerance"
        elif self._last_active_at and now - self._last_active_at > 10.0:
            message = "warning: no lane read a note for 10s; raise tolerance or check the points"
        elif not self._last_active_at and now - self._started_at > 10.0:
            message = "warning: no lane ever read as a note; raise tolerance or check the points"
        else:
            message = None
        if message == self._warned:
            return
        self._warned = message
        if message is None:
            return
        if self.hud_on:
            sys.stdout.write("\r\x1b[2K")
        sys.stdout.flush()
        print(message)

    def handle_keys(self):
        if is_down(self.panic_key):
            self._running = False
        elif is_down(self.hud_key):
            self.hud_on = not self.hud_on
            if not self.hud_on:
                sys.stdout.write("\r\x1b[2K")
                sys.stdout.flush()

    def countdown(self, ms):
        end = time.perf_counter() + ms / 1000.0
        while time.perf_counter() < end:
            remaining = max(0.0, end - time.perf_counter())
            sys.stdout.write(f"\rstarting in {remaining:4.1f}s   ")
            sys.stdout.flush()
            time.sleep(0.05)
        sys.stdout.write("\r" + " " * 32 + "\r")
        sys.stdout.flush()

    def run(self):
        delay = float(self.cfg.get("start_delay_ms", 3000))
        if delay > 0:
            self.countdown(delay)
        interval = 1.0 / self.target_fps if self.target_fps > 0 else 0.0
        self._running = True
        self._started_at = time.perf_counter()
        self._next_sanity = self._started_at + 2.0
        self._prev_frame = self._started_at
        deadline = self._prev_frame + interval
        started = self._prev_frame
        try:
            while self._running:
                now = time.perf_counter()
                if self.seconds is not None and now - started >= self.seconds:
                    break
                self.update(now)
                self.frame_times.append((now - self._prev_frame) * 1000.0)
                self._prev_frame = now
                self.frames += 1
                self.handle_keys()
                self.hud(now)
                self.sanity_check(now)
                if interval:
                    deadline += interval
                    remaining = deadline - time.perf_counter()
                    if remaining > 0.0002:
                        time.sleep(remaining - 0.0002)
                    else:
                        deadline = time.perf_counter()
                else:
                    time.sleep(0)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            self.summary(time.perf_counter() - started)

    def stop(self):
        self.keys.release_all()

    def summary(self, duration):
        if self.hud_on:
            sys.stdout.write("\r\x1b[2K")
        print(f"ran {duration:.1f}s  frames {self.frames}  "
              f"avg fps {self.frames / duration if duration else 0:.0f}")
        for lane in self.lanes:
            print(f"  {lane.name} ({lane.key}) hits {lane.hits}")
        if self.latencies:
            lat = sorted(self.latencies)
            print(f"  detect->key latency  min {min(lat):.2f} ms  "
                  f"avg {statistics.fmean(lat):.2f} ms  max {max(lat):.2f} ms")
        if self.frame_times:
            ft = sorted(self.frame_times)
            p95 = ft[int(len(ft) * 0.95)]
            print(f"  frame interval  min {min(ft):.2f} ms  "
                  f"avg {statistics.fmean(ft):.2f} ms  p95 {p95:.2f} ms")

    @property
    def panic_key(self):
        return self.cfg.get("panic_key", "f12")

    @property
    def hud_key(self):
        return self.cfg.get("hud_key", "f11")