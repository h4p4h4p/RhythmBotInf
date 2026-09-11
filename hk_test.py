import sys
import threading
import time
import keyboard

fired = []

def bind(name):
    def cb():
        fired.append(name)
        print("FIRED", name, flush=True)
    try:
        keyboard.add_hotkey(name, cb)
        print("BOUND", name, flush=True)
    except Exception as e:
        print("BIND FAIL", name, repr(e), flush=True)

for n in ["]", "[", "=", "-", "z"]:
    bind(n)

time.sleep(2)
print("ready", flush=True)
import queue
try:
    import msvcrt
except Exception:
    msvcrt = None
deadline = time.time() + 25
while time.time() < deadline:
    if fired:
        break
    time.sleep(0.05)
print("done", fired, flush=True)