# Evolved

A 2D top-down **cell-stage evolution game** inspired by the opening (microbial / "tide pool")
stage of *Spore*. You start as a single microscopic cell in a primordial ocean. Eat, survive,
collect DNA, bolt on new organelles in the evolution editor, and grow from a lone cell toward a
**multicellular** organism — all while competing against rival cells that are driven by a **local
LLM** (Ollama / `qwen3:4b`) and are trying to do exactly the same thing: forage, hunt, evolve,
and eat *you*.

All graphics are **drawn procedurally in Python** (pygame) — no external image assets, nothing to
license. Everything you see is generated from shapes and math at runtime.

---

## Features

- **Top-down swimming** in a large scrolling ocean; the camera zooms out as you grow.
- **Three diets** — herbivore, carnivore, omnivore — each with its own mouth part and food source.
- **Eating & nutrition** — graze on plant matter (green) and/or devour meat chunks and smaller cells (red).
- **Evolution editor** — spend DNA to attach organelles that change how your cell plays:
  flagella (speed), cilia (turning), spikes (attack/defense), poison, electric jets, and the three mouths.
- **Combat** — ram rival cells; spikes and offensive mouths deal damage, defensive parts punish attackers.
  Killed cells burst into meat chunks that carnivores can eat.
- **LLM-controlled rivals** — each AI cell asks a local LLM (`qwen3:4b` on Ollama) for a strategy
  (forage / hunt / flee, which parts to evolve, what diet to pursue). A fast heuristic executes that
  strategy every frame, so the game never stalls waiting on the model, and rivals evolve toward
  multicellularity right alongside you.
- **Growth loop** — enough DNA lets you grow larger and more complex; reach the complexity threshold to
  trigger the **multicellular** transition.

## Controls

| Input | Action |
|-------|--------|
| `W` `A` `S` `D` or Arrow keys | Swim (accelerate in that direction) |
| Movement into another cell | Auto-attack on collision (if you have an offensive part) |
| `E` | Open / close the **Evolution Editor** (when you have DNA to spend) |
| `Space` | (Editor) confirm / reproduce & continue |
| `Esc` | Pause / quit |
| `Tab` | Toggle debug / AI overlay |

Eating is automatic: swim your mouth into compatible food.

## Install

```bash
python -m pip install -r requirements.txt
```

Requires Python 3.11+ (tested on 3.14) and [`pygame-ce`](https://pyga.me/).

## Run

```bash
python main.py
```

Useful flags:

```bash
python main.py --no-llm            # disable the LLM; rivals use pure heuristics
python main.py --ai-cells 8        # number of rival cells
python main.py --ollama-host 192.168.15.38 --ollama-port 21434 --model qwen3:4b
python main.py --screenshot out.png --frames 300   # headless: simulate then save a PNG
python main.py --demo              # let the AI play the "player" cell (hands-off testing)
```

## The LLM

Rivals are steered by an Ollama endpoint (default `http://192.168.15.38:21434`, model `qwen3:4b`).
Requests run on a background thread with a queue and timeout so gameplay stays smooth even when the
model is slow. If the endpoint is unreachable the game logs a warning and rivals fall back to
heuristics automatically.

## Project layout

```
main.py              entry point / CLI
evolved/
  config.py          tuning constants, colors
  parts.py           organelle catalog (effects & costs)
  entities.py        Food chunks, part meteors
  cell.py            the Cell: stats, parts, movement, eating, combat, evolution
  world.py           spawning, updating, collisions
  camera.py          follow + zoom
  player.py          keyboard control
  llm.py             Ollama client + threaded request manager
  ai.py              LLM-backed brain + heuristic executor
  editor.py          evolution editor UI
  hud.py             HUD, minimap, event log, rendering helpers
  game.py            game states & main loop
```

## Credits

Design inspired by *Spore*'s Cell Stage (Maxis, 2008). This is an original, non-commercial
fan reimplementation; no assets from the original game are used.
