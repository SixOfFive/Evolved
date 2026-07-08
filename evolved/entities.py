"""Passive world entities: food chunks and part meteors.

Food comes in two flavors: 'plant' (green, herbivore/omnivore food) and 'meat'
(red, carnivore/omnivore food, spawned when a cell dies). Part meteors drift in
and, when consumed, hand out a burst of DNA and unlock a random organelle for
whoever cracks them open.
"""

import math
import random

import pygame

from . import config as C
from . import parts as P


class Food:
    __slots__ = ("pos", "radius", "kind", "dna", "energy", "ttl", "phase", "alive")

    def __init__(self, pos, kind):
        self.pos = pygame.Vector2(pos)
        self.kind = kind  # 'plant', 'meat' or 'algae'
        self.alive = True
        self.phase = random.uniform(0, math.tau)
        if kind == "plant":
            self.radius = C.PLANT_RADIUS * random.uniform(0.8, 1.25)
            self.dna = C.PLANT_DNA
            self.energy = C.PLANT_ENERGY
            self.ttl = None
        elif kind == "algae":
            self.radius = C.ALGAE_RADIUS * random.uniform(0.85, 1.3)
            self.dna = C.ALGAE_DNA
            self.energy = C.ALGAE_ENERGY
            self.ttl = None
        else:  # meat
            self.radius = C.MEAT_RADIUS * random.uniform(0.8, 1.3)
            self.dna = C.MEAT_DNA
            self.energy = C.MEAT_ENERGY
            self.ttl = C.MEAT_DECAY

    def update(self, dt):
        if self.ttl is not None:
            self.ttl -= dt
            if self.ttl <= 0:
                self.alive = False

    def draw(self, surface, cam, t):
        if not cam.is_visible(self.pos, self.radius + 4):
            return
        sx, sy = cam.world_to_screen(self.pos)
        wob = 1.0 + 0.12 * math.sin(t * 3.0 + self.phase)
        r = max(2, self.radius * cam.zoom * wob)
        if self.kind == "algae":
            # a lumpy cluster of fronds
            for i in range(5):
                a = self.phase + i * math.tau / 5
                lx = sx + math.cos(a) * r * 0.55
                ly = sy + math.sin(a) * r * 0.55
                pygame.draw.circle(surface, C.C_ALGAE, (lx, ly), max(2, r * 0.55))
            pygame.draw.circle(surface, C.C_ALGAE_CORE, (sx, sy), max(2, r * 0.5))
            return
        if self.kind == "plant":
            col, core = C.C_PLANT, C.C_PLANT_CORE
        else:
            col, core = C.C_MEAT, C.C_MEAT_CORE
            # meat fades as it decays
            if self.ttl is not None and self.ttl < 5:
                fade = max(0.25, self.ttl / 5.0)
                col = tuple(int(c * fade) for c in col)
        pygame.draw.circle(surface, col, (sx, sy), r)
        pygame.draw.circle(surface, core, (sx, sy), max(1, r * 0.45))


class Meteor:
    """A drifting shard that grants DNA and unlocks a random part when eaten."""

    __slots__ = ("pos", "vel", "radius", "dna", "part_id", "phase", "spin", "alive")

    def __init__(self, pos, part_id=None):
        self.pos = pygame.Vector2(pos)
        ang = random.uniform(0, math.tau)
        spd = random.uniform(6, 20)
        self.vel = pygame.Vector2(math.cos(ang), math.sin(ang)) * spd
        self.radius = C.METEOR_RADIUS
        self.dna = C.METEOR_DNA
        self.part_id = part_id or random.choice(P.PART_ORDER)
        self.phase = random.uniform(0, math.tau)
        self.spin = random.uniform(-1.2, 1.2)
        self.alive = True

    def update(self, dt):
        self.pos += self.vel * dt
        # gentle bounce off world edges
        if self.pos.x < 0 or self.pos.x > C.WORLD_W:
            self.vel.x *= -1
        if self.pos.y < 0 or self.pos.y > C.WORLD_H:
            self.vel.y *= -1
        self.pos.x = max(0, min(C.WORLD_W, self.pos.x))
        self.pos.y = max(0, min(C.WORLD_H, self.pos.y))
        self.phase += self.spin * dt

    def draw(self, surface, cam, t):
        if not cam.is_visible(self.pos, self.radius + 6):
            return
        sx, sy = cam.world_to_screen(self.pos)
        r = max(3, self.radius * cam.zoom)
        # a rough crystalline shard: a rotating hexagon
        pts = []
        for i in range(6):
            a = self.phase + i * math.tau / 6
            rr = r * (1.0 + 0.18 * math.sin(a * 2 + t))
            pts.append((sx + math.cos(a) * rr, sy + math.sin(a) * rr))
        pygame.draw.polygon(surface, C.C_METEOR, pts)
        pygame.draw.polygon(surface, C.C_METEOR_CORE, pts, max(1, int(r * 0.25)))
        pygame.draw.circle(surface, C.C_METEOR_CORE, (sx, sy), max(1, r * 0.35))
