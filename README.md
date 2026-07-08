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
  Big organisms can graze algae clusters and swallow much smaller cells whole.
- **Evolution editor** — spend DNA to attach organelles that change how your cell plays:
  flagella (speed), cilia (turning), spikes (attack/defense), poison, electric jets, and the three mouths.
- **Combat** — ram rival cells; spikes and offensive mouths deal damage, defensive parts punish attackers.
  Killed cells burst into meat chunks that carnivores can eat.
- **Two stages** — fill the cell stage's evolution bar and the game *asks* whether you want to become
  **multicellular** (YES / NOT YET — declining lets you keep playing, press `M` to advance later).
  The multicellular stage adds trailing **body segments**, **muscle cells** (speed/turning),
  **sensory cells** (awareness), **stingers** (contact damage), **armor plates** (damage reduction)
  and **photo cells** (passive energy). Master it and choose to **evolve a brain** to finish the journey.
- **LLM-controlled rivals** — each new rival asks a local LLM (`qwen3:4b` on Ollama) whether it will
  *hunt*, *harvest*, or *both* — then keeps consulting the model for strategy (forage / hunt / flee,
  which parts to evolve, when to grow). A fast heuristic executes the plan every frame, so the game
  never stalls, and rivals advance to multicellular right alongside you (watch for the `*` badge).

## Controls

| Input | Action |
|-------|--------|
| `W` `A` `S` `D` or Arrow keys | Swim (accelerate in that direction) |
| Movement into another cell | Auto-attack on collision (if you have an offensive part) |
| `E` | Call a mate / open the **Evolution Editor** |
| `M` | Advance to the next stage (when your evolution bar is full) |
| `Y` / `N` | Answer a stage-advancement question |
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
