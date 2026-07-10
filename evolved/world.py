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
from .ai import AIBrain, EpicBrain
from .particles import ParticleSystem


def _stack(n):
    """Diminishing-returns stacking for duplicate offensive parts."""
    return n ** C.STACK_EXP if n > 0 else 0.0


class World:
    def __init__(self, manager, ai_count=C.AI_CELL_COUNT, demo=False,
                 sound=None):
        self.manager = manager
        self.demo = demo
        self.sound = sound
        self.fx = ParticleSystem()
        self.cells = []
        self.foods = []
        self.meteors = []
        self.events = []          # bottom feed: [text, ttl, color], last 5
        self.time = 0.0
        self._atk_log = {}        # (attacker id, defender id) -> last log time
        self._food_timer = 0.0
        self._respawn_timer = 0.0
        self.ai_count = ai_count
        self.player_dead = False
        self.epic = None
        self._epic_timer = C.EPIC_MIN_AGE
        self._epic_life = 0.0
        self.player_epic_kills = 0

        # --- biomes ---
        self.trench_c = pygame.Vector2(C.WORLD_W * C.TRENCH_CENTER_FRAC[0],
                                       C.WORLD_H * C.TRENCH_CENTER_FRAC[1])
        self.current_band = (C.WORLD_H * C.CURRENT_Y_FRAC[0],
                             C.WORLD_H * C.CURRENT_Y_FRAC[1])
        self.weeds = []
        for _ in range(C.WEED_COUNT):
            r = random.uniform(*C.WEED_RADIUS)
            pos = pygame.Vector2(random.uniform(r, C.WORLD_W - r),
                                 random.uniform(r, C.WORLD_H - r))
            fronds = []
            for _ in range(int(r / 11)):
                a = random.uniform(0, math.tau)
                d = r * math.sqrt(random.random()) * 0.92
                fronds.append((pos.x + math.cos(a) * d,
                               pos.y + math.sin(a) * d,
                               random.uniform(10, 26),
                               random.uniform(0, math.tau)))
            self.weeds.append((pos, r, fronds))

        # --- food & meteors first, so spawn-time LLM snapshots see the world ---
        for _ in range(C.PLANT_COUNT):
            self.foods.append(Food(self._rand_pos(), "plant"))
        for _ in range(C.ALGAE_COUNT):
            self.foods.append(Food(self._rich_pos(), "algae"))
        for _ in range(C.METEOR_COUNT):
            self.meteors.append(Meteor(self._rich_pos()))

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
            if 600 < (p - self.player.pos).length() < 2000:
                return p
        return pygame.Vector2(self._rand_pos())

    def _spawn_rival(self, idx):
        color = C.AI_COLORS[idx % len(C.AI_COLORS)]
        name = f"Rival-{idx + 1}"
        cell = Cell(self._far_spawn(), is_player=False, color=color, name=name)
        brain = AIBrain(cell, self, self.manager)
        cell.brain = brain
        cell.name = f"{name} ({brain.personality})"
        cell.add_part("flagellum", spend=False)
        # The LLM decides at spawn whether this rival will hunt, harvest, or
        # both; the mouth is equipped when its answer arrives. Without the LLM
        # the brain's random intent equips a mouth immediately.
        if self.manager.enabled:
            brain.begin_spawn_choice()
        else:
            brain.equip_starting_mouth()
        cell.energy = cell.max_energy
        self.cells.append(cell)
        return cell

    def play(self, name, pos=None, volume=1.0):
        if self.sound is not None:
            self.sound.play(name, pos=pos, volume=volume)

    # ---------------------------------------------------------------- biomes
    def in_weeds(self, pos):
        for wpos, r, _ in self.weeds:
            if (pos[0] - wpos.x) ** 2 + (pos[1] - wpos.y) ** 2 < r * r:
                return True
        return False

    def in_current(self, pos):
        return self.current_band[0] <= pos[1] <= self.current_band[1]

    def in_trench(self, pos):
        return (pygame.Vector2(pos) - self.trench_c).length_squared() \
            < C.TRENCH_RADIUS ** 2

    def can_see(self, viewer, target):
        """Weed thickets hide their occupants from AI eyes."""
        if not self.in_weeds(target.pos):
            return True
        if (viewer.pos - target.pos).length() < C.WEED_HIDE_DIST:
            return True
        return self.in_weeds(viewer.pos)

    def _trench_point(self):
        a = random.uniform(0, math.tau)
        r = C.TRENCH_RADIUS * math.sqrt(random.random())
        return (min(C.WORLD_W - 30, max(30, self.trench_c.x + math.cos(a) * r)),
                min(C.WORLD_H - 30, max(30, self.trench_c.y + math.sin(a) * r)))

    def _rich_pos(self):
        """Spawn point for algae/meteors - the trench is the motherlode."""
        if random.random() < C.TRENCH_FOOD_BIAS:
            return self._trench_point()
        return self._rand_pos()

    # --------------------------------------------------------------- logging
    def log(self, text, color=C.C_TEXT):
        self.events.append([text, 6.0, color])
        if len(self.events) > 5:
            self.events.pop(0)

    def log_attack(self, attacker, defender, kind):
        """Feed line for combat involving the player, throttled per pair."""
        if not (attacker.is_player or defender.is_player):
            return  # rival-on-rival scraps stay out of the feed
        key = (attacker.id, defender.id)
        if self.time - self._atk_log.get(key, -99.0) < 4.0:
            return
        self._atk_log[key] = self.time
        color = C.C_BAD if defender.is_player else C.C_GOOD
        self.log(f"[atk] {attacker.name} -> {defender.name}: {kind}", color)

    # ---------------------------------------------------------------- update
    def update(self, dt, t):
        self.time += dt
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

        # biome effects: weeds slow you down, the current sweeps you along
        for cell in self.cells:
            if not cell.alive:
                continue
            cell.zone_speed = C.WEED_SLOW if self.in_weeds(cell.pos) else 1.0
            if self.in_current(cell.pos):
                cell.vel.x += C.CURRENT_FORCE * dt

        # integrate motion
        for cell in self.cells:
            cell.update(dt)

        # loose food drifts with the current (algae is anchored)
        lo, hi = self.current_band
        for f in self.foods:
            if f.alive and f.kind != "algae" and lo <= f.pos.y <= hi:
                f.pos.x += C.CURRENT_FOOD_DRIFT * dt
                if f.pos.x > C.WORLD_W:
                    f.pos.x -= C.WORLD_W

        # entity upkeep
        for f in self.foods:
            f.update(dt)
        for m in self.meteors:
            m.update(dt)
        self.fx.update(dt)

        # fast swimmers shed bubbles
        for cell in self.cells:
            if cell.alive and cell.vel.length_squared() > 190 * 190:
                if random.random() < dt * 9:
                    tail = cell.seg_pos[-1] if cell.seg_pos else cell.pos
                    self.fx.bubble(tail, -cell.vel)

        # interactions
        self._resolve_eating(dt)
        self._resolve_combat(dt)
        self._resolve_electric()

        # deaths
        self._cleanup(dt)

        # restock
        self._restock(dt)

        # the Leviathan
        self._update_epic(dt)

        # event fade
        for e in self.events:
            e[1] -= dt
        self.events = [e for e in self.events if e[1] > 0]

    def _resolve_eating(self, dt):
        for cell in self.cells:
            if not cell.alive:
                continue
            mouth = cell.mouth_pos()
            reach = cell.radius * 0.75
            # multicellular+ heads generate suction: edible food within
            # head_radius * 2 of the head is pulled toward the mouth
            vac = (cell.radius * C.VACUUM_RANGE_MULT
                   if cell.stage != "cell" else 0.0)
            # cheap axis rejection first - the pond holds ~1000 items.
            # Centered on the head so it covers both mouth reach and vacuum.
            cx, cy = cell.pos.x, cell.pos.y
            lim = max(reach + cell.radius, vac) + 22.0
            for f in self.foods:
                if not f.alive:
                    continue
                fp = f.pos
                if abs(fp.x - cx) > lim or abs(fp.y - cy) > lim:
                    continue
                if f.kind in ("plant", "algae") and not cell.can_eat_plant:
                    continue
                if f.kind == "algae" and cell.radius < C.ALGAE_MIN_EATER:
                    continue
                if f.kind == "meat" and not cell.can_eat_meat:
                    continue
                if vac and (fp - cell.pos).length() < vac + f.radius:
                    # suck toward the mouth, faster the closer it gets
                    pull = mouth - fp
                    d = pull.length()
                    if d > 1e-6:
                        closeness = 1.0 - min(1.0, d / vac)
                        speed = C.VACUUM_PULL * (0.35 + 1.8 * closeness * closeness)
                        f.pos = fp + pull * min(1.0, speed * dt / d)
                        fp = f.pos
                if (fp - mouth).length() < reach + f.radius:
                    f.alive = False
                    cell.feed(f.dna, f.energy,
                              "plant" if f.kind == "algae" else f.kind)
                    self.play(f"eat_{f.kind}", pos=fp)
                    self.fx.burst(fp, (C.C_MEAT_CORE if f.kind == "meat"
                                       else C.C_PLANT_CORE),
                                  n=4, speed=60, size=1.8, life=0.45)
            # meteors (any cell can crack one)
            for m in self.meteors:
                if not m.alive:
                    continue
                if (m.pos - cell.pos).length() < cell.radius + m.radius:
                    m.alive = False
                    cell.feed(m.dna, 8.0, "meteor")
                    self.play("meteor", pos=m.pos)
                    self.fx.burst(m.pos, C.C_METEOR_CORE, n=12, speed=130,
                                  size=2.5, life=0.8)
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
                if overlap > 0:
                    # separation push (bigger cell shoves less)
                    if dist > 1e-6:
                        nrm = d / dist
                        total = a.radius + b.radius
                        a.pos -= nrm * overlap * (b.radius / total) * 0.5
                        b.pos += nrm * overlap * (a.radius / total) * 0.5

                    self._bite(a, b, dt)
                    self._bite(b, a, dt)
                    # poison auras (sacs stack)
                    if a.has_poison and b.alive:
                        b.take_damage(C.POISON_DMG * _stack(a.n_poison)
                                      * a.damage_mult * dt, a)
                    if b.has_poison and a.alive:
                        a.take_damage(C.POISON_DMG * _stack(b.n_poison)
                                      * b.damage_mult * dt, b)
                else:
                    # heads apart - but trailing tails are fair game too
                    self._tail_contact(a, b, dt)
                    self._tail_contact(b, a, dt)

    def _tail_contact(self, attacker, defender, dt):
        """The attacker's head touching the defender's body segments.

        Tail chewing ignores the size gate (you're gnawing tissue, not
        swallowing the organism) at reduced bite damage - so a long tail is
        DNA in the bank for whoever catches it. The tail fights back with
        stingers and poison, which is exactly what those parts are for.
        """
        if not attacker.alive or not defender.alive or not defender.seg_pos:
            return
        for sp, sr in zip(defender.seg_pos, defender.seg_radius):
            off = attacker.pos - sp
            dd = off.length()
            pen = attacker.radius + sr - dd
            if pen <= 0:
                continue
            # nudge the attacker's head out of the segment
            if dd > 1e-6:
                attacker.pos += (off / dd) * pen * 0.5

            # all weapons work at reduced effect on tails (glancing tissue
            # hits, not vital strikes) - otherwise packs of spiked chewers
            # cascade into pond-wide wipeouts. Duplicates stack here too.
            f = C.TAIL_BITE_FACTOR
            dmg = 0.0
            if attacker.can_bite_cells:
                bite = C.BITE_DMG * _stack(attacker.n_bite) * f * dt
                dmg += bite
                attacker.feed(0.0, bite * C.BITE_FEED, "cell")
            facing = attacker.spikes_facing(sp)
            if facing:
                dmg += C.SPIKE_DMG * _stack(facing) * f * dt
            if attacker.n_sting:
                dmg += C.STING_DMG * _stack(attacker.n_sting) * f * dt
            if attacker.has_poison:
                dmg += C.POISON_DMG * _stack(attacker.n_poison) * f * dt
            if dmg > 0:
                defender.take_damage(dmg * attacker.damage_mult, attacker)
                self.log_attack(attacker, defender, "tail chew")

            # tail defenses bite back at the same reduced effect
            back = 0.0
            if defender.n_sting:
                back += C.STING_DMG * _stack(defender.n_sting) * f * dt
            if defender.has_poison:
                back += C.POISON_DMG * _stack(defender.n_poison) * f * dt
            if back > 0:
                attacker.take_damage(back * defender.damage_mult, defender)
                self.log_attack(defender, attacker, "tail defense")
            return  # one segment contact per frame is plenty

    def _bite(self, attacker, defender, dt):
        if not attacker.alive or not defender.alive:
            return
        # much smaller prey is swallowed whole
        if (attacker.can_bite_cells
                and defender.radius <= attacker.radius * C.SWALLOW_RATIO):
            defender.health = 0
            defender.alive = False
            defender.swallowed = True
            attacker.kills += 1
            attacker.feed(5.0 + defender.radius * 0.35, 30.0, "cell")
            if attacker.is_player or defender.is_player:
                color = C.C_GOOD if attacker.is_player else C.C_BAD
                self.log(f"[atk] {attacker.name} swallowed {defender.name} "
                         "whole!", color)
            self.play("swallow", pos=defender.pos)
            self.fx.burst(defender.pos, defender.color, n=10, speed=110,
                          size=2.2, life=0.6)
            return
        dmg = 0.0
        pieces = []
        # mouth bite: prey smaller than you (extra jaws stack)
        if (attacker.can_bite_cells
                and defender.radius <= attacker.radius * C.EAT_SIZE_RATIO):
            bite = C.BITE_DMG * _stack(attacker.n_bite) * dt
            dmg += bite
            attacker.feed(0.0, bite * C.BITE_FEED, "cell")
            pieces.append("bite")
        # spikes that face the defender (all of them count)
        facing = attacker.spikes_facing(defender.pos)
        if facing:
            dmg += C.SPIKE_DMG * _stack(facing) * dt
            pieces.append(f"{facing} spike" + ("s" if facing > 1 else ""))
        # stinger tentacles hurt on any contact, no facing needed
        if attacker.n_sting:
            dmg += C.STING_DMG * _stack(attacker.n_sting) * dt
            pieces.append("sting")
        if dmg > 0:
            defender.take_damage(dmg * attacker.damage_mult, attacker)
            self.log_attack(attacker, defender, " + ".join(pieces))
            self.play("bite" if pieces and pieces[0] == "bite" else "sting",
                      pos=defender.pos, volume=0.8)

    def _resolve_electric(self):
        for cell in self.cells:
            if not cell.alive or not cell.did_pulse:
                continue
            # more jets: wider pulse and harder hit, both diminishing
            dmg = (C.ELECTRIC_DMG * (cell.n_electric ** C.ELECTRIC_DMG_EXP)
                   * cell.damage_mult)
            hits = 0
            hit_player = False
            for other in self.cells:
                if other is cell or not other.alive:
                    continue
                if (other.pos - cell.pos).length() <= cell.electric_range + other.radius:
                    other.take_damage(dmg, cell)
                    hits += 1
                    hit_player = hit_player or other.is_player
            if hits:
                self.play("zap", pos=cell.pos)
            if hits and (cell.is_player or hit_player):
                key = (cell.id, -1)
                if self.time - self._atk_log.get(key, -99.0) >= 4.0:
                    self._atk_log[key] = self.time
                    color = C.C_GOOD if cell.is_player else C.C_BAD
                    self.log(f"[atk] {cell.name} zaps {hits} "
                             f"organism{'s' if hits > 1 else ''}", color)

    def _cleanup(self, dt):
        survivors = []
        for cell in self.cells:
            if cell.alive:
                survivors.append(cell)
                continue
            # death: burst into meat (unless swallowed whole)
            if not cell.swallowed:
                self._spawn_meat(cell)
                self.play("death", pos=cell.pos,
                          volume=1.0 if cell.is_player else 0.7)
                self.fx.burst(cell.pos, cell.color,
                              n=min(24, 8 + int(cell.radius / 3)),
                              speed=150, size=3.0, life=0.9)
            # slaying the Leviathan pays out a legendary jackpot
            if cell.is_epic:
                killer = cell.last_attacker
                if killer is not None and getattr(killer, "alive", False):
                    killer.feed(C.EPIC_DNA_JACKPOT, 60.0, "cell")
                    killer.kills += 1
                    if killer.is_player:
                        self.player_epic_kills += 1
                    self.log(f"{killer.name} SLEW THE LEVIATHAN! "
                             f"+{int(C.EPIC_DNA_JACKPOT)} DNA!", C.C_MULTI)
                    self.fx.ripple(cell.pos, C.C_MULTI, max_radius=320,
                                   life=1.4)
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
        # counting ~1000 food items is not free - take stock a few times a
        # second and top up in small batches
        self._food_timer -= dt
        if self._food_timer <= 0:
            self._food_timer = 0.3
            plants = sum(1 for f in self.foods if f.alive and f.kind == "plant")
            for _ in range(min(18, C.PLANT_COUNT - plants)):
                self.foods.append(Food(self._rand_pos(), "plant"))
            algae = sum(1 for f in self.foods if f.alive and f.kind == "algae")
            if algae < C.ALGAE_COUNT and random.random() < 0.3:
                self.foods.append(Food(self._rich_pos(), "algae"))
        # drop the occasional meteor
        live_meteors = sum(1 for m in self.meteors if m.alive)
        if live_meteors < C.METEOR_COUNT and random.random() < 0.004:
            self.meteors.append(Meteor(self._rich_pos()))
        # prune dead entities occasionally
        if len(self.foods) > C.PLANT_COUNT + C.ALGAE_COUNT + 500:
            self.foods = [f for f in self.foods if f.alive]
        self.meteors = [m for m in self.meteors if m.alive]

        # keep the rival population up (the Leviathan doesn't count)
        alive_rivals = sum(1 for c in self.cells
                           if not c.is_player and c.alive and not c.is_epic)
        if alive_rivals < C.AI_MIN_POP:
            self._respawn_timer -= dt
            if self._respawn_timer <= 0:
                self._respawn_timer = C.AI_RESPAWN_DELAY
                self._spawn_rival(random.randint(0, 99))
                self.log("A new rival drifts in.", C.C_TEXT_DIM)
        else:
            self._respawn_timer = C.AI_RESPAWN_DELAY

    # ------------------------------------------------------------ leviathan
    def _update_epic(self, dt):
        if self.epic is not None and self.epic.alive:
            self._epic_life -= dt
            if self._epic_life <= 0:
                # it loses interest and sinks away (no corpse, no meat)
                self.epic.alive = False
                self.epic.swallowed = True
                self.log("The LEVIATHAN sinks back into the deep.",
                         C.C_EPIC)
                self.epic = None
            return
        if self.epic is not None and not self.epic.alive:
            self.epic = None  # slain - _cleanup handled the spoils
            return
        self._epic_timer -= dt
        if self._epic_timer <= 0:
            self._epic_timer = C.EPIC_CHECK_INTERVAL
            if random.random() < C.EPIC_SPAWN_CHANCE:
                self._spawn_epic()

    def _spawn_epic(self):
        # rises from a pond edge, far from the player
        edges = [(60.0, random.uniform(0, C.WORLD_H)),
                 (C.WORLD_W - 60.0, random.uniform(0, C.WORLD_H)),
                 (random.uniform(0, C.WORLD_W), 60.0),
                 (random.uniform(0, C.WORLD_W), C.WORLD_H - 60.0)]
        pos = max(edges, key=lambda e: (pygame.Vector2(e) - self.player.pos)
                  .length_squared())
        epic = Cell(pos, is_player=False, color=C.C_EPIC, name="LEVIATHAN")
        epic.is_epic = True
        epic.stage = "fish"
        epic.growth_level = 8
        epic.radius = C.EPIC_RADIUS
        for pid in ("jaw", "jaw", "spike", "spike", "spike", "stinger",
                    "stinger", "armor", "armor", "sensor",
                    "photo_cell", "photo_cell", "photo_cell"):
            epic.add_part(pid, spend=False)
        epic.recompute()
        # an epic is beyond normal biology - stat overrides come last
        epic.max_health = C.EPIC_HP
        epic.health = C.EPIC_HP
        epic.speed = C.EPIC_SPEED
        epic.turn_rate = C.EPIC_TURN
        epic.energy = epic.max_energy
        epic.brain = EpicBrain(epic, self)
        self.cells.append(epic)
        self.epic = epic
        self._epic_life = C.EPIC_LIFETIME
        self.log("Something VAST stirs at the edge of the pond...", C.C_EPIC)
        self.play("epic", volume=1.0)

    # ----------------------------------------------------------------- draw
    def draw_zones(self, surface, cam, t):
        """Biomes: the dark trench, the flowing current, the weed thickets."""
        # trench: a darker disc of deep water with a faint rim
        if cam.is_visible(self.trench_c, C.TRENCH_RADIUS + 40):
            sx, sy = cam.world_to_screen(self.trench_c)
            r = int(C.TRENCH_RADIUS * cam.zoom)
            pygame.draw.circle(surface, C.C_TRENCH, (sx, sy), r)
            pygame.draw.circle(surface, (16, 34, 58), (sx, sy), r,
                               max(1, int(2 * cam.zoom)))
        # current: drifting streaks across the band
        lo, hi = self.current_band
        mid = (lo + hi) / 2
        if cam.is_visible((cam.center.x, mid), (hi - lo)):
            for k in range(16):
                x = (k * 431.7 + t * 120) % C.WORLD_W
                y = lo + ((k * 953.3) % (hi - lo))
                a = cam.world_to_screen((x, y))
                b = cam.world_to_screen((x + 110, y))
                pygame.draw.line(surface, (28, 58, 88), a, b,
                                 max(1, int(2 * cam.zoom)))
        # weeds: dark beds with gently swaying fronds
        for wpos, r, fronds in self.weeds:
            if not cam.is_visible(wpos, r + 30):
                continue
            sx, sy = cam.world_to_screen(wpos)
            pygame.draw.circle(surface, C.C_WEED_DARK, (sx, sy),
                               max(2, int(r * cam.zoom)))
            for fx_, fy_, ln, ph in fronds:
                base = cam.world_to_screen((fx_, fy_))
                sway = math.sin(t * 1.7 + ph) * 0.5
                tip = cam.world_to_screen((fx_ + math.sin(sway) * ln,
                                           fy_ - math.cos(sway) * ln))
                pygame.draw.line(surface, C.C_WEED, base, tip,
                                 max(1, int(2 * cam.zoom)))

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
        self.fx.draw(surface, cam)
