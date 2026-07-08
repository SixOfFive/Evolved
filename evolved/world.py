"""The ocean: spawning, updating and all interactions between entities.

The world owns the player cell, the rival cells (each with an AIBrain), the
food, and the meteors. Every frame it advances brains, integrates motion, then
resolves eating, combat and pickups, handles death, and keeps the ecosystem
stocked. It also drains finished LLM policies and hands them to the right brain.
"""

import math
import random

import pygame

from . import config as C
from . import parts as P
from .cell import Cell
from .entities import Food, Meteor
from .ai import AIBrain


class World:
    def __init__(self, manager, ai_count=C.AI_CELL_COUNT, demo=False):
        self.manager = manager
        self.demo = demo
        self.cells = []
        self.foods = []
        self.meteors = []
        self.events = []          # [text, ttl, color]
        self._food_timer = 0.0
        self._respawn_timer = 0.0
        self.ai_count = ai_count
        self.player_dead = False

        # --- player ---
        self.player = Cell(self._center_spawn(), is_player=True,
                           color=C.C_PLAYER, name="You")
        self.player.add_part("filter_mouth", spend=False)
        self.player.add_part("flagellum", spend=False)
        self.player.add_part("eye", spend=False)
        self.player.energy = self.player.max_energy
        self.cells.append(self.player)
        if demo:
            self.player.brain = AIBrain(self.player, self, manager)

        # --- rivals ---
        for i in range(ai_count):
            self._spawn_rival(i)

        # --- food & meteors ---
        for _ in range(C.PLANT_COUNT):
            self.foods.append(Food(self._rand_pos(), "plant"))
        for _ in range(C.METEOR_COUNT):
            self.meteors.append(Meteor(self._rand_pos()))

    # ------------------------------------------------------------- spawning
    def _rand_pos(self):
        return (random.uniform(40, C.WORLD_W - 40),
                random.uniform(40, C.WORLD_H - 40))

    def _center_spawn(self):
        return (C.WORLD_W / 2, C.WORLD_H / 2)

    def _far_spawn(self):
        """A position away from the player, for fresh rivals."""
        for _ in range(20):
            p = pygame.Vector2(self._rand_pos())
            if (p - self.player.pos).length() > 700:
                return p
        return pygame.Vector2(self._rand_pos())

    def _spawn_rival(self, idx):
        color = C.AI_COLORS[idx % len(C.AI_COLORS)]
        name = f"Rival-{idx + 1}"
        cell = Cell(self._far_spawn(), is_player=False, color=color, name=name)
        brain = AIBrain(cell, self, self.manager)
        cell.brain = brain
        # starting kit for the intended diet
        if brain.intended_diet == "carnivore":
            cell.add_part("jaw", spend=False)
        elif brain.intended_diet == "omnivore":
            cell.add_part("jaw", spend=False)
            cell.add_part("filter_mouth", spend=False)
        else:
            cell.add_part("filter_mouth", spend=False)
        cell.add_part("flagellum", spend=False)
        cell.energy = cell.max_energy
        self.cells.append(cell)
        return cell

    # --------------------------------------------------------------- logging
    def log(self, text, color=C.C_TEXT):
        self.events.append([text, 5.0, color])
        if len(self.events) > 6:
            self.events.pop(0)

    # ---------------------------------------------------------------- update
    def update(self, dt, t):
        # apply any finished LLM policies
        for cell_id, policy in self.manager.drain_results():
            if policy is None:
                continue
            for cell in self.cells:
                if cell.id == cell_id and cell.brain is not None:
                    cell.brain.apply_policy(policy)
                    break

        # brains choose thrust
        for cell in self.cells:
            if cell.brain is not None and cell.alive:
                cell.brain.update(dt)

        # integrate motion
        for cell in self.cells:
            cell.update(dt)

        # entity upkeep
        for f in self.foods:
            f.update(dt)
        for m in self.meteors:
            m.update(dt)

        # interactions
        self._resolve_eating()
        self._resolve_combat(dt)
        self._resolve_electric()

        # deaths
        self._cleanup(dt)

        # restock
        self._restock(dt)

        # event fade
        for e in self.events:
            e[1] -= dt
        self.events = [e for e in self.events if e[1] > 0]

    def _resolve_eating(self):
        for cell in self.cells:
            if not cell.alive:
                continue
            mouth = cell.mouth_pos()
            reach = cell.radius * 0.75
            # food
            for f in self.foods:
                if not f.alive:
                    continue
                if f.kind == "plant" and not cell.can_eat_plant:
                    continue
                if f.kind == "meat" and not cell.can_eat_meat:
                    continue
                if (f.pos - mouth).length() < reach + f.radius:
                    f.alive = False
                    cell.feed(f.dna, f.energy, f.kind)
                    if cell.is_player:
                        pass
            # meteors (any cell can crack one)
            for m in self.meteors:
                if not m.alive:
                    continue
                if (m.pos - cell.pos).length() < cell.radius + m.radius:
                    m.alive = False
                    cell.feed(m.dna, 8.0, "meteor")
                    if m.part_id not in cell.discovered:
                        cell.discovered.add(m.part_id)
                        if cell.is_player:
                            self.log(f"Meteor cracked: unlocked {P.PART_DEFS[m.part_id].name}!",
                                     C.C_METEOR_CORE)

    def _resolve_combat(self, dt):
        cells = [c for c in self.cells if c.alive]
        n = len(cells)
        for i in range(n):
            a = cells[i]
            for j in range(i + 1, n):
                b = cells[j]
                d = b.pos - a.pos
                dist = d.length()
                overlap = a.radius + b.radius - dist
                if overlap <= 0:
                    continue
                # separation push (bigger cell shoves less)
                if dist > 1e-6:
                    nrm = d / dist
                    total = a.radius + b.radius
                    a.pos -= nrm * overlap * (b.radius / total) * 0.5
                    b.pos += nrm * overlap * (a.radius / total) * 0.5

                self._bite(a, b, dt)
                self._bite(b, a, dt)
                # poison auras
                if a.has_poison and b.alive:
                    b.take_damage(C.POISON_DMG * dt, a)
                if b.has_poison and a.alive:
                    a.take_damage(C.POISON_DMG * dt, b)

    def _bite(self, attacker, defender, dt):
        if not attacker.alive or not defender.alive:
            return
        dmg = 0.0
        # mouth bite: prey smaller than you
        if (attacker.can_bite_cells
                and defender.radius <= attacker.radius * C.EAT_SIZE_RATIO):
            bite = C.BITE_DMG * dt
            dmg += bite
            attacker.feed(0.0, bite * C.BITE_FEED, "cell")
        # spikes that face the defender
        facing = attacker.spikes_facing(defender.pos)
        if facing:
            dmg += C.SPIKE_DMG * facing * dt
        if dmg > 0:
            defender.take_damage(dmg, attacker)

    def _resolve_electric(self):
        for cell in self.cells:
            if not cell.alive or not cell.did_pulse:
                continue
            for other in self.cells:
                if other is cell or not other.alive:
                    continue
                if (other.pos - cell.pos).length() <= C.ELECTRIC_RANGE + other.radius:
                    other.take_damage(C.ELECTRIC_DMG, cell)
            if cell.is_player:
                self.log("Zap! Electric discharge.", (150, 210, 255))

    def _cleanup(self, dt):
        survivors = []
        for cell in self.cells:
            if cell.alive:
                survivors.append(cell)
                continue
            # death: burst into meat, hand a part to the last attacker unknown
            self._spawn_meat(cell)
            if cell.is_player:
                self.player_dead = True
                survivors.append(cell)  # keep for the game-over screen
            else:
                self.log(f"{cell.name} died.", C.C_TEXT_DIM)
        self.cells = survivors

    def _spawn_meat(self, cell):
        chunks = max(2, int(cell.radius / 4))
        for _ in range(chunks):
            off = pygame.Vector2(random.uniform(-1, 1), random.uniform(-1, 1))
            if off.length_squared() > 0:
                off = off.normalize() * random.uniform(0, cell.radius * 1.4)
            self.foods.append(Food(cell.pos + off, "meat"))

    def _restock(self, dt):
        # keep plants topped up
        self._food_timer -= dt
        plants = sum(1 for f in self.foods if f.alive and f.kind == "plant")
        if plants < C.PLANT_COUNT and self._food_timer <= 0:
            self._food_timer = 0.25
            self.foods.append(Food(self._rand_pos(), "plant"))
        # drop the occasional meteor
        live_meteors = sum(1 for m in self.meteors if m.alive)
        if live_meteors < C.METEOR_COUNT and random.random() < 0.004:
            self.meteors.append(Meteor(self._rand_pos()))
        # prune dead entities occasionally
        if len(self.foods) > C.PLANT_COUNT * 3:
            self.foods = [f for f in self.foods if f.alive]
        self.meteors = [m for m in self.meteors if m.alive]

        # keep the rival population up
        alive_rivals = sum(1 for c in self.cells if not c.is_player and c.alive)
        if alive_rivals < C.AI_MIN_POP:
            self._respawn_timer -= dt
            if self._respawn_timer <= 0:
                self._respawn_timer = C.AI_RESPAWN_DELAY
                self._spawn_rival(random.randint(0, 99))
                self.log("A new rival drifts in.", C.C_TEXT_DIM)
        else:
            self._respawn_timer = C.AI_RESPAWN_DELAY

    # ----------------------------------------------------------------- draw
    def draw_entities(self, surface, cam, t):
        for f in self.foods:
            if f.alive:
                f.draw(surface, cam, t)
        for m in self.meteors:
            if m.alive:
                m.draw(surface, cam, t)
        # draw rivals first, player last (on top)
        for cell in self.cells:
            if cell.alive and not cell.is_player:
                cell.draw(surface, cam, t)
        if self.player.alive:
            self.player.draw(surface, cam, t)
