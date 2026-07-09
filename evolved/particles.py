"""A tiny particle system: sparkles, bursts, ripples, and bubbles.

Everything is a short-lived circle in world space. The system is capped so
even a massacre can't hurt the frame rate.
"""

import math
import random

import pygame

_MAX = 600


class ParticleSystem:
    def __init__(self):
        self.parts = []   # dicts: pos, vel, life, max_life, color, size, kind

    # ---------------------------------------------------------------- emit
    def _add(self, p):
        if len(self.parts) >= _MAX:
            self.parts.pop(0)
        self.parts.append(p)

    def burst(self, pos, color, n=8, speed=90.0, size=2.5, life=0.7):
        """Radial spray - eating sparkles, death bursts, meteor cracks."""
        for _ in range(n):
            a = random.uniform(0, math.tau)
            s = speed * random.uniform(0.3, 1.0)
            self._add({
                "pos": pygame.Vector2(pos),
                "vel": pygame.Vector2(math.cos(a) * s, math.sin(a) * s),
                "life": life * random.uniform(0.6, 1.0), "max_life": life,
                "color": color, "size": size * random.uniform(0.7, 1.3),
                "kind": "dot",
            })

    def ripple(self, pos, color, max_radius=90.0, life=0.6):
        """An expanding ring - growth spurts, stage advancement."""
        self._add({"pos": pygame.Vector2(pos), "vel": pygame.Vector2(),
                   "life": life, "max_life": life, "color": color,
                   "size": max_radius, "kind": "ring"})

    def bubble(self, pos, drift):
        """A slow bubble shed by a fast swimmer."""
        self._add({"pos": pygame.Vector2(pos),
                   "vel": pygame.Vector2(drift) * 0.15
                   + pygame.Vector2(random.uniform(-8, 8),
                                    random.uniform(-8, 8)),
                   "life": random.uniform(0.8, 1.6), "max_life": 1.6,
                   "color": (150, 200, 230),
                   "size": random.uniform(1.5, 3.5), "kind": "bubble"})

    # -------------------------------------------------------------- update
    def update(self, dt):
        alive = []
        for p in self.parts:
            p["life"] -= dt
            if p["life"] <= 0:
                continue
            p["pos"] += p["vel"] * dt
            p["vel"] *= (1.0 - 2.2 * dt) if p["kind"] == "dot" else 1.0
            alive.append(p)
        self.parts = alive

    # ---------------------------------------------------------------- draw
    def draw(self, surface, cam):
        for p in self.parts:
            fade = p["life"] / p["max_life"]
            pos = p["pos"]
            if not cam.is_visible(pos, p["size"] + 4):
                continue
            sx, sy = cam.world_to_screen(pos)
            col = tuple(int(c * (0.35 + 0.65 * fade)) for c in p["color"])
            if p["kind"] == "ring":
                r = p["size"] * (1.0 - fade) + 6
                pygame.draw.circle(surface, col, (sx, sy),
                                   max(2, int(r * cam.zoom)),
                                   max(1, int(2 * cam.zoom)))
            else:
                r = max(1, p["size"] * cam.zoom * (0.5 + 0.5 * fade))
                if p["kind"] == "bubble":
                    pygame.draw.circle(surface, col, (sx, sy), r,
                                       1 if r > 2 else 0)
                else:
                    pygame.draw.circle(surface, col, (sx, sy), r)
