"""Evolved - entry point.

A 2D top-down Spore-style cell-stage game. Swim, eat, evolve, and outlast rival
cells driven by a local LLM (Ollama / qwen3:4b).

    python main.py                       # play (starts on autopilot; P = take control)
    python main.py --no-llm              # rivals use heuristics only
    python main.py --ai-cells 8          # more rivals
    python main.py --screenshot out.png  # headless: save a PNG and exit
"""

import argparse
import atexit
import os
import signal
import sys
import time

from evolved import config as C

_LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          ".evolved.pid")


def _is_python_process(pid):
    """Best-effort check that `pid` is a live Python process (not a reused id)."""
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            ok = k32.QueryFullProcessImageNameW(handle, 0, buf,
                                                ctypes.byref(size))
            return bool(ok) and "python" in buf.value.lower()
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"python" in f.read().lower()
    except OSError:
        return True


def _release_lock():
    try:
        with open(_LOCK_PATH) as f:
            if int(f.read().strip()) == os.getpid():
                os.remove(_LOCK_PATH)
    except (OSError, ValueError):
        pass


def ensure_single_instance():
    """Launching the game again kills the previous instance, if any."""
    old = None
    try:
        with open(_LOCK_PATH) as f:
            old = int(f.read().strip())
    except (OSError, ValueError):
        pass
    if old and old != os.getpid() and _is_python_process(old):
        try:
            os.kill(old, signal.SIGTERM)
            print(f"[Evolved] closed the previous game instance (PID {old}).")
            time.sleep(0.5)  # let it release the window/audio device
        except OSError:
            pass
    try:
        with open(_LOCK_PATH, "w") as f:
            f.write(str(os.getpid()))
        atexit.register(_release_lock)
    except OSError:
        pass  # read-only mount etc. - single-instance is best-effort


def parse_args():
    ap = argparse.ArgumentParser(description="Evolved - cell-stage evolution game")
    ap.add_argument("--ollama-host", default="192.168.15.38",
                    help="Ollama host (default 192.168.15.38)")
    ap.add_argument("--ollama-port", type=int, default=21434,
                    help="Ollama port (default 21434)")
    ap.add_argument("--model", default="qwen3:4b",
                    help="Ollama model for rival brains (default qwen3:4b)")
    ap.add_argument("--no-llm", action="store_true",
                    help="disable the LLM; rivals use heuristics only")
    ap.add_argument("--ai-cells", type=int, default=C.AI_CELL_COUNT,
                    help="number of rival cells")
    ap.add_argument("--screenshot", metavar="PATH", default=None,
                    help="headless: simulate then save a PNG to PATH and exit")
    ap.add_argument("--frames", type=int, default=300,
                    help="frames to simulate before a screenshot")
    ap.add_argument("--autoquit", type=float, default=0.0,
                    help="auto-close the window after N seconds (for testing)")
    return ap.parse_args()


def main():
    args = parse_args()
    if args.screenshot:
        # run without a real window/audio device
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        args.headless = True
    else:
        # a fresh launch replaces any game that is already running
        ensure_single_instance()

    from evolved.game import Game
    game = Game(args)
    if args.screenshot:
        game.run_screenshot(args.screenshot, args.frames)
    else:
        game.run()


if __name__ == "__main__":
    main()
