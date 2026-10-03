import math
import time

import capture
import config
from keys import is_down

DEFAULTS_KEYS = ["d", "f", "j", "k"]


WATCHED = ("enter", "escape")


class Prompts:
    def __init__(self):
        self._state = {key: False for key in WATCHED}

    def _sample(self, key):
        down = is_down(key)
        previous = self._state[key]
        self._state[key] = down
        return down, previous

    def release(self):
        while True:
            held = [key for key in WATCHED if is_down(key)]
            for key in held:
                self._state[key] = True
            if not held:
                break
            time.sleep(0.004)

    def wait_enter(self, prompt):
        self.release()
        print(prompt)
        while True:
            down, previous = self._sample("enter")
            if down and not previous:
                return True
            escape_down, escape_previous = self._sample("escape")
            if escape_down and not escape_previous:
                raise KeyboardInterrupt
            time.sleep(0.004)

    def wait_yes_no(self, prompt):
        self.release()
        print(prompt)
        while True:
            down, previous = self._sample("enter")
            if down and not previous:
                return True
            escape_down, escape_previous = self._sample("escape")
            if escape_down and not escape_previous:
                return False
            time.sleep(0.004)


def sample_region(region, radius):
    cap = capture.Capture(region)
    try:
        return cap.grab()
    finally:
        cap.close()


def median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def calibrate_background(cfg, lanes, region, frames=12):
    samples = []
    for _ in range(frames):
        buffer = sample_region(region, cfg["sample_radius"])
        for lane in lanes:
            color = capture.sample_points(
                buffer, region["width"], region["height"], lane["points"], cfg["sample_radius"]
            )
            samples.append(color)
        time.sleep(1 / 60)
    off = (
        median([s[0] for s in samples]),
        median([s[1] for s in samples]),
        median([s[2] for s in samples]),
    )
    noise = 0.0
    for sample in samples:
        dr = sample[0] - off[0]
        dg = sample[1] - off[1]
        db = sample[2] - off[2]
        noise = max(noise, math.sqrt(dr * dr + dg * dg + db * db))
    return list(off), noise


def run(cfg):
    prompts = Prompts()
    print("rhythm bot calibration")
    print("each prompt waits for Enter to be pressed *and released*")
    print(f"config: {cfg.get('_path', 'defaults (no file)')}")
    try:
        cap = capture.Capture({"left": 0, "top": 0, "width": 1, "height": 1})
        cap.close()
    except Exception as exc:
        print(f"could not read monitors: {exc}")

    keys = list(cfg.get("lane_keys") or DEFAULTS_KEYS)
    lanes = []
    for index, key in enumerate(keys, start=1):
        prompts.wait_enter(
            f"lane {index} (key {key.upper()}): hover the judgment line where notes are "
            f"hit, press Enter")
        x, y = capture.mouse_position()
        points = [[x, y]]
        print(f"  point {x},{y}")
        lanes.append({"key": key, "name": f"Lane {index}", "points": points,
                      "trigger": 0})

    if prompts.wait_yes_no(
            "add an extra watch point per lane (a higher point on the lane, so long "
            "notes are tracked)? Enter=yes Esc=no "):
        for index, lane in enumerate(lanes, start=1):
            prompts.wait_enter(
                f"lane {index} (key {lane['key'].upper()}): second point, press Enter")
            x, y = capture.mouse_position()
            lane["points"].append([x, y])
            print(f"  point {x},{y}")

    cfg["lanes"] = lanes
    region = config.region_of(cfg)
    pad = 40
    sample_region_rect = {
        "left": max(0, region["left"] - pad),
        "top": max(0, region["top"] - pad),
        "width": region["width"] + pad * 2,
        "height": region["height"] + pad * 2,
    }

    print("")
    prompts.wait_enter("clear the playfield (no notes visible under any lane), press Enter")
    off_color, noise = calibrate_background(cfg, lanes, sample_region_rect)
    print(f"  background {off_color}  noise {noise:.1f}")

    print("")
    prompts.wait_enter("show a bright note covering the lane points, press Enter")
    note_samples = []
    buffer = sample_region(sample_region_rect, cfg["sample_radius"])
    contrasts = []
    for lane in lanes:
        color = capture.sample_points(
            buffer, sample_region_rect["width"], sample_region_rect["height"],
            [[p[0] - sample_region_rect["left"], p[1] - sample_region_rect["top"]]
             for p in lane["points"]],
            cfg["sample_radius"],
        )
        note_samples.append(color)
        dr = color[0] - off_color[0]
        dg = color[1] - off_color[1]
        db = color[2] - off_color[2]
        contrast = math.sqrt(dr * dr + dg * dg + db * db)
        contrasts.append(contrast)
        print(f"  {lane['name']} ({lane['key'].upper()}): color {list(color)}  contrast {contrast:.0f}")

    weakest = min(contrasts) if contrasts else 0.0
    tolerance = int(max(12, min(90, round(noise * 3 + 12), round(weakest * 0.45))))
    print("")
    print(f"recommended tolerance: {tolerance} "
          f"(noise {noise:.1f}, weakest contrast {weakest:.0f})")

    cfg["off_color"] = off_color
    cfg["tolerance"] = tolerance
    cfg["region"] = None
    path = config.save(cfg)
    print(f"saved {path}")
    print(f"region auto-derived: {region}")
    print("run 'python main.py check' to verify detection, then 'python main.py run --mode hold'")
    return path