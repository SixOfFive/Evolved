"""Evolved - entry point.

A 2D top-down Spore-style cell-stage game. Swim, eat, evolve, and outlast rival
cells driven by a local LLM (Ollama / qwen3:4b).

    python main.py                       # play
    python main.py --no-llm              # rivals use heuristics only
    python main.py --ai-cells 8          # more rivals
    python main.py --demo                # AI plays the player cell
    python main.py --screenshot out.png  # headless: save a PNG and exit
"""

import argparse
import os

from evolved import config as C


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
    ap.add_argument("--demo", action="store_true",
                    help="let an AI brain drive the player cell (hands-off)")
    ap.add_argument("--screenshot", metavar="PATH", default=None,
                    help="headless: simulate then save a PNG to PATH and exit")
    ap.add_argument("--frames", type=int, default=300,
                    help="frames to simulate before a screenshot")
    return ap.parse_args()


def main():
    args = parse_args()
    if args.screenshot:
        # run without a real window/audio device
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        args.headless = True

    from evolved.game import Game
    game = Game(args)
    if args.screenshot:
        game.run_screenshot(args.screenshot, args.frames)
    else:
        game.run()


if __name__ == "__main__":
    main()
