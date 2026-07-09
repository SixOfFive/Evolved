"""The Cell: the player's organism and every AI rival.

A cell is a bag of stats plus a list of attached parts. Its stats (speed, turn
rate, health, feeding ability, offense) are *derived* from its parts and growth
level, so evolving is just a matter of changing the part list and recomputing.

The cell also knows how to draw itself and all its organelles procedurally -
there are no image assets anywhere in this game.
"""

import math
import random

import pygame

from . import config as C
from . import parts as P


class AttachedPart:
    __slots__ = ("id", "angle", "phase")

    def __init__(self, part_id, angle, phase=0.0):
        self.id = part_id
        self.angle = angle      # placement angle relative to heading (radians)
        self.phase = phase      # animation phase offset


_next_id = 0


def _new_id():
    global _next_id
    _next_id += 1
    return _next_id


class Cell:
    def __init__(self, pos, is_player=False, color=None, name="Cell"):
        self.id = _new_id()
        self.pos = pygame.Vector2(pos)
        self.vel = pygame.Vector2(0, 0)
        self.angle = random.uniform(0, math.tau)
        self.thrust = pygame.Vector2(0, 0)   # desired move dir, magnitude 0..1
        self.radius = C.START_RADIUS
        self.is_player = is_player
        self.color = color or C.C_PLAYER
        self.name = name

        self.parts = []            # list[AttachedPart]
        self.discovered = set()    # part ids unlocked via meteors / kills
        self.growth_level = 0      # level within the CURRENT stage
        self.dna = 0.0
        self.generation = 1
        self.stage = "cell"        # "cell" -> "multi" -> "fish"
        self.slot_bonus = 0        # slots carried over from completed stages
        # multicellular body: world-space positions/radii of trailing segments
        self.seg_pos = []
        self.seg_radius = []

        self.health = C.BASE_HEALTH
        self.max_health = C.BASE_HEALTH
        self.energy = C.BASE_ENERGY
        self.max_energy = C.BASE_ENERGY

        self.alive = True
        self.swallowed = False     # eaten whole -> leaves no meat behind
        self.last_attacker = None  # who hurt us last (for the AI flee reflex)
        self.last_hit_time = -999.0
        self.time_alive = 0.0
        self.diet_meter = 0.0      # +1 herbivore .. -1 carnivore
        self.food_eaten = 0
        self.kills = 0

        self.electric_cd = C.ELECTRIC_COOLDOWN
        self.did_pulse = False     # set true the frame an electric pulse fires
        self.pulse_anim = 0.0      # >0 while drawing the pulse ring
        self.hurt_flash = 0.0      # >0 briefly after taking damage

        # brain / controller hooks (set externally)
        self.brain = None

        # derived stats, filled by recompute()
        self.speed = C.BASE_SPEED
        self.turn_rate = C.BASE_TURN
        self.can_eat_plant = False
        self.can_eat_meat = False
        self.can_bite_cells = False
        self.n_spike = 0
        self.n_sting = 0
        self.n_bite = 0
        self.n_poison = 0
        self.n_electric = 0
        self.armor_mult = 1.0      # incoming damage multiplier (<=1)
        self.damage_mult = 1.0     # outgoing damage multiplier (fish levels)
        self.photo_rate = 0.0      # passive energy/s from photosynthesis
        self.has_poison = False
        self.has_electric = False
        self.electric_range = C.ELECTRIC_RANGE
        self.detect_range = 360.0
        self.max_slots = C.BASE_SLOTS
        self.diet = "none"
        self.recompute()

    # ------------------------------------------------------------------ parts
    def part_counts(self):
        counts = {}
        for ap in self.parts:
            counts[ap.id] = counts.get(ap.id, 0) + 1
        return counts

    def has_part(self, part_id):
        return any(ap.id == part_id for ap in self.parts)

    def slots_used(self):
        return len(self.parts)

    def available_parts(self):
        """Part ids the cell is allowed to add right now (stage + unlocks)."""
        out = []
        for pid in P.PART_ORDER:
            pdef = P.PART_DEFS[pid]
            if pdef.stage == "multi" and self.stage == "cell":
                continue
            if pdef.unlock_level <= self.growth_level or pid in self.discovered:
                out.append(pid)
        return out

    def can_add(self, part_id, spend=True):
        pdef = P.PART_DEFS[part_id]
        if pdef.stage == "multi" and self.stage == "cell":
            return False
        if self.slots_used() >= self.max_slots:
            return False
        if spend and self.dna < pdef.cost:
            return False
        if pdef.unlock_level > self.growth_level and part_id not in self.discovered:
            return False
        return True

    def add_part(self, part_id, angle=None, spend=True):
        if not self.can_add(part_id, spend=spend):
            return False
        pdef = P.PART_DEFS[part_id]
        if spend:
            self.dna -= pdef.cost
        if angle is None:
            angle = self._auto_angle(pdef)
        self.parts.append(AttachedPart(part_id, angle, random.uniform(0, math.tau)))
        self.recompute()
        return True

    def remove_part(self, index, refund=True):
        if 0 <= index < len(self.parts):
            ap = self.parts.pop(index)
            if refund:
                self.dna += P.PART_DEFS[ap.id].cost
            self.recompute()
            return True
        return False

    def _auto_angle(self, pdef):
        if pdef.prefer_angle is not None:
            # spread multiples of the same preferred-angle part a little
            same = sum(1 for ap in self.parts
                       if P.PART_DEFS[ap.id].prefer_angle == pdef.prefer_angle)
            return pdef.prefer_angle + (same * 0.5 * (1 if same % 2 else -1))
        # otherwise distribute around the rim
        n = self.slots_used()
        return (n * 2.399963) % math.tau  # golden-angle scatter

    # -------------------------------------------------------------- evolution
    def grow_cost(self):
        if self.stage == "fish":
            return C.FISH_GROW_COST * (1 + self.growth_level * C.FISH_GROW_COST_SCALE)
        base = C.MULTI_GROW_COST if self.stage == "multi" else C.BASE_GROW_COST
        return base * (self.growth_level + 1)

    def can_grow(self):
        if self.dna < self.grow_cost():
            return False
        if self.stage == "fish":
            return True  # fish never stop growing stronger
        return (self.radius < C.MAX_RADIUS
                and self.growth_level < C.STAGE_MAX_LEVEL)

    def grow(self):
        if not self.can_grow():
            return False
        self.dna -= self.grow_cost()
        self.growth_level += 1
        if self.stage == "fish":
            # size caps out eventually; strength and health never do
            self.radius = min(C.FISH_MAX_RADIUS,
                              self.radius * C.FISH_GROW_RADIUS_MULT)
        else:
            mult = (C.MULTI_GROW_RADIUS_MULT if self.stage == "multi"
                    else C.GROW_RADIUS_MULT)
            self.radius = min(C.MAX_RADIUS, self.radius * mult)
        self.recompute()
        self.health = self.max_health  # a growth spurt restores you
        return True

    def stage_complete(self):
        """This stage's evolution bar is full - the next stage is on offer."""
        if self.stage == "fish":
            return False  # the fish stage is endless
        return self.growth_level >= C.STAGE_MAX_LEVEL

    def can_advance_stage(self):
        return self.stage == "cell" and self.stage_complete()

    def can_evolve_brain(self):
        return self.stage == "multi" and self.stage_complete()

    def advance_stage(self):
        """Become multicellular: reset the level bar, sprout body segments."""
        if not self.can_advance_stage():
            return False
        self.stage = "multi"
        # slots earned in the cell stage carry over - never regress capacity
        self.slot_bonus += C.STAGE_MAX_LEVEL * C.SLOTS_PER_LEVEL
        self.growth_level = 0
        # everything from the cell stage stays unlocked forever
        self.discovered.update(P.CELL_STAGE_PARTS)
        self.radius = min(C.MAX_RADIUS, self.radius * 1.15)
        self.recompute()
        self.health = self.max_health
        self.energy = self.max_energy
        return True

    def become_fish(self):
        """Grow a brain: the final form. Endless growth, same pond."""
        if not self.can_evolve_brain():
            return False
        self.stage = "fish"
        self.slot_bonus += C.STAGE_MAX_LEVEL * C.SLOTS_PER_LEVEL
        self.growth_level = 0
        # everything from the multicellular stage stays unlocked forever
        self.discovered.update(P.MULTI_STAGE_PARTS)
        self.radius = min(C.FISH_MAX_RADIUS, self.radius * 1.1)
        self.recompute()
        self.health = self.max_health
        self.energy = self.max_energy
        return True

    def n_segments(self):
        if self.stage == "multi":
            return 2 + self.part_counts().get("segment", 0)
        if self.stage == "fish":
            # fish lengthen as they level, up to a point
            return (3 + self.part_counts().get("segment", 0)
                    + min(6, self.growth_level // 2))
        return 0

    def recompute(self):
        counts = self.part_counts()
        n_flag = counts.get("flagellum", 0)
        n_cil = counts.get("cilia", 0)
        self.n_spike = counts.get("spike", 0)
        n_eye = counts.get("eye", 0)
        # real counts so duplicates stack (booleans kept for the renderer)
        self.n_bite = counts.get("jaw", 0) + counts.get("proboscis", 0)
        self.n_poison = counts.get("poison", 0)
        self.n_electric = counts.get("electric", 0)
        self.has_poison = self.n_poison > 0
        self.has_electric = self.n_electric > 0
        self.electric_range = (C.ELECTRIC_RANGE
                               * (self.n_electric ** C.ELECTRIC_RANGE_EXP)
                               if self.n_electric else C.ELECTRIC_RANGE)

        # multicellular tissue
        n_muscle = counts.get("muscle", 0)
        n_sensor = counts.get("sensor", 0)
        n_armor = counts.get("armor", 0)
        self.n_sting = counts.get("stinger", 0)
        self.photo_rate = counts.get("photo_cell", 0) * C.PHOTO_ENERGY
        self.armor_mult = max(C.ARMOR_REDUCE_FLOOR,
                              (1.0 - C.ARMOR_REDUCE) ** n_armor)

        has_filter = counts.get("filter_mouth", 0) > 0
        has_jaw = counts.get("jaw", 0) > 0
        has_prob = counts.get("proboscis", 0) > 0
        self.can_eat_plant = has_filter or has_prob
        self.can_eat_meat = has_jaw
        self.can_bite_cells = has_jaw or has_prob

        if has_prob or (has_jaw and has_filter):
            self.diet = "omnivore"
        elif has_jaw:
            self.diet = "carnivore"
        elif has_filter:
            self.diet = "herbivore"
        else:
            self.diet = "none"

        self.speed = (C.BASE_SPEED + n_flag * C.FLAGELLUM_SPEED
                      + n_muscle * C.MUSCLE_SPEED)
        self.turn_rate = (C.BASE_TURN + n_cil * C.CILIA_TURN
                          + n_muscle * C.MUSCLE_TURN)
        self.detect_range = 360.0 + n_eye * 150.0 + n_sensor * C.SENSOR_RANGE
        segs = self.n_segments()
        self.max_slots = (C.BASE_SLOTS + self.growth_level * C.SLOTS_PER_LEVEL
                          + segs * C.SEGMENT_SLOTS + self.slot_bonus)

        # fish grow ever stronger with each level
        self.damage_mult = (1.0 + self.growth_level * C.FISH_DMG_PER_LEVEL
                            if self.stage == "fish" else 1.0)

        new_max = (C.BASE_HEALTH
                   + (self.radius - C.START_RADIUS) * C.HEALTH_PER_RADIUS
                   + segs * C.SEGMENT_HP
                   + (self.growth_level * C.FISH_HP_PER_LEVEL
                      if self.stage == "fish" else 0.0))
        if new_max > self.max_health:
            # keep same fraction of health when max grows
            frac = self.health / self.max_health if self.max_health else 1.0
            self.health = new_max * frac
        self.max_health = new_max
        self.health = min(self.health, self.max_health)

        # Adjust the segment chain WITHOUT disturbing existing segments -
        # a full rebuild here made tails visibly jump every time an AI bought
        # a part or a fish leveled up. New segments sprout off the tail tip,
        # continuing the chain's current direction; removals trim the tip.
        if segs != len(self.seg_pos):
            while len(self.seg_pos) > segs:
                self.seg_pos.pop()
            while len(self.seg_pos) < segs:
                tail = (pygame.Vector2(self.seg_pos[-1]) if self.seg_pos
                        else pygame.Vector2(self.pos))
                prev = (pygame.Vector2(self.seg_pos[-2])
                        if len(self.seg_pos) >= 2
                        else (pygame.Vector2(self.pos) if self.seg_pos else None))
                if prev is not None:
                    d = tail - prev
                    ext = (d.normalize() if d.length_squared() > 1e-6
                           else -self.facing())
                else:
                    ext = -self.facing()
                self.seg_pos.append(tail + ext * self.radius * C.SEGMENT_SPACING)
        self.seg_radius = [max(4.0, self.radius * (0.85 - 0.08 * i))
                           for i in range(segs)]

    # ---------------------------------------------------------------- feeding
    def feed(self, dna, energy, kind):
        self.dna += dna
        self.energy = min(self.max_energy, self.energy + energy)
        # eating heals a little
        self.health = min(self.max_health, self.health + energy * 0.25)
        if kind == "plant":
            self.diet_meter = min(1.0, self.diet_meter + 0.06)
        elif kind in ("meat", "cell"):
            self.diet_meter = max(-1.0, self.diet_meter - 0.06)
        if kind != "cell":
            self.food_eaten += 1

    def take_damage(self, amount, source=None):
        if amount <= 0 or not self.alive:
            return
        amount *= self.armor_mult
        self.health -= amount
        if source is not None:
            self.last_attacker = source
            self.last_hit_time = self.time_alive
        self.hurt_flash = 0.25
        if self.health <= 0:
            self.health = 0
            self.alive = False

    # -------------------------------------------------------------- geometry
    def facing(self):
        return pygame.Vector2(math.cos(self.angle), math.sin(self.angle))

    def mouth_pos(self):
        return self.pos + self.facing() * self.radius

    def tier_of(self, other):
        """How `other` ranks relative to self: 'prey', 'peer', or 'predator'."""
        if self.radius >= other.radius * 1.22:
            return "prey"
        if other.radius >= self.radius * 1.22:
            return "predator"
        return "peer"

    def spikes_facing(self, target_pos):
        d = pygame.Vector2(target_pos) - self.pos
        if d.length_squared() < 1e-6:
            return 0
        target_ang = math.atan2(d.y, d.x)
        n = 0
        for ap in self.parts:
            if ap.id != "spike":
                continue
            wa = self.angle + ap.angle
            diff = (target_ang - wa + math.pi) % math.tau - math.pi
            if abs(diff) < 0.9:
                n += 1
        return n

    # ----------------------------------------------------------------- update
    def update(self, dt):
        if not self.alive:
            return
        self.time_alive += dt

        # --- steering: turn toward thrust, accelerate along facing ---
        thrust_mag = min(1.0, self.thrust.length())
        if thrust_mag > 1e-3:
            desired = math.atan2(self.thrust.y, self.thrust.x)
            diff = (desired - self.angle + math.pi) % math.tau - math.pi
            step = max(-self.turn_rate * dt, min(self.turn_rate * dt, diff))
            self.angle += step
            target_vel = self.facing() * self.speed * thrust_mag
        else:
            target_vel = pygame.Vector2(0, 0)
        self.vel += (target_vel - self.vel) * min(1.0, C.ACCEL_RESPONSE * dt)
        self.pos += self.vel * dt

        # --- world bounds (soft) ---
        margin = self.radius
        if self.pos.x < margin:
            self.pos.x = margin
            self.vel.x = abs(self.vel.x) * 0.4
        elif self.pos.x > C.WORLD_W - margin:
            self.pos.x = C.WORLD_W - margin
            self.vel.x = -abs(self.vel.x) * 0.4
        if self.pos.y < margin:
            self.pos.y = margin
            self.vel.y = abs(self.vel.y) * 0.4
        elif self.pos.y > C.WORLD_H - margin:
            self.pos.y = C.WORLD_H - margin
            self.vel.y = -abs(self.vel.y) * 0.4

        # --- body segments trail the head, follow-the-leader style ---
        if self.seg_pos:
            leader_pos = self.pos
            leader_r = self.radius
            for i, sp in enumerate(self.seg_pos):
                spacing = (leader_r + self.seg_radius[i]) * C.SEGMENT_SPACING
                d = sp - leader_pos
                dist = d.length()
                if dist > 1e-6:
                    sp += d * ((spacing - dist) / dist) * min(1.0, 14 * dt)
                else:
                    sp += pygame.Vector2(-spacing, 0)
                self.seg_pos[i] = sp
                leader_pos = sp
                leader_r = self.seg_radius[i]

        # --- metabolism (photosynthetic tissue offsets the drain) ---
        drain = C.ENERGY_DRAIN + C.ENERGY_MOVE_DRAIN * thrust_mag - self.photo_rate
        if drain >= 0:
            self.energy = max(0.0, self.energy - drain * dt)
        else:
            self.energy = min(self.max_energy, self.energy - drain * dt)
        if self.energy > C.WELL_FED_ENERGY and self.health < self.max_health:
            self.health = min(self.max_health, self.health + C.HEALTH_REGEN * dt)
        if self.energy <= 0:
            self.take_damage(C.STARVE_DAMAGE * dt)

        # --- electric pulse ---
        self.did_pulse = False
        if self.has_electric:
            self.electric_cd -= dt
            if self.electric_cd <= 0:
                self.electric_cd = C.ELECTRIC_COOLDOWN
                self.did_pulse = True
                self.pulse_anim = 0.35
        if self.pulse_anim > 0:
            self.pulse_anim -= dt
        if self.hurt_flash > 0:
            self.hurt_flash -= dt

    # ------------------------------------------------------------------ draw
    def draw(self, surface, cam, t):
        if not cam.is_visible(self.pos, self.radius * 2.2 + 30):
            return
        sx, sy = cam.world_to_screen(self.pos)
        r = self.radius * cam.zoom

        # multicellular body segments, tail-first so they stack toward the head
        if self.seg_pos:
            self._draw_segments(surface, cam, t)

        # parts drawn behind the body first (flagella, tails)
        for ap in self.parts:
            if ap.id == "flagellum":
                self._draw_flagellum(surface, sx, sy, r, ap, t)

        # poison aura
        if self.has_poison:
            self._draw_poison(surface, sx, sy, r, t)

        # electric pulse ring
        if self.pulse_anim > 0:
            prog = 1.0 - (self.pulse_anim / 0.35)
            pr = r + prog * self.electric_range * cam.zoom
            col = (120, 200, 255)
            width = max(1, int(3 * cam.zoom))
            pygame.draw.circle(surface, col, (sx, sy), max(1, int(pr)), width)

        # --- body ---
        body = self.color
        if self.hurt_flash > 0:
            body = (255, 180, 170)
        # soft outer membrane
        pygame.draw.circle(surface, self._shade(body, 0.55), (sx, sy), max(2, int(r + 2)))
        pygame.draw.circle(surface, body, (sx, sy), max(1, int(r)))
        pygame.draw.circle(surface, self._shade(body, 1.35), (sx, sy), max(1, int(r)),
                           max(1, int(2 * cam.zoom)))
        # nucleus
        nuc = self._shade(body, 0.6)
        noff = self.facing() * (-r * 0.15)
        pygame.draw.circle(surface, nuc, (sx + noff.x, sy + noff.y), max(1, int(r * 0.42)))
        pygame.draw.circle(surface, self._shade(body, 0.4),
                           (sx + noff.x, sy + noff.y), max(1, int(r * 0.42)),
                           max(1, int(1 * cam.zoom)))

        # foreground parts
        for ap in self.parts:
            if ap.id == "spike":
                self._draw_spike(surface, sx, sy, r, ap)
            elif ap.id == "cilia":
                self._draw_cilia(surface, sx, sy, r, ap, t)
            elif ap.id in ("filter_mouth", "jaw", "proboscis"):
                self._draw_mouth(surface, sx, sy, r, ap)
            elif ap.id == "eye":
                self._draw_eye(surface, sx, sy, r, ap)
            elif ap.id == "electric":
                self._draw_electric_node(surface, sx, sy, r, ap, t)
            elif ap.id == "sensor":
                self._draw_sensor(surface, sx, sy, r, ap, t)

        # fish get pectoral fins beside the head, always
        if self.stage == "fish":
            for side in (1, -1):
                fa = self.angle + side * 1.9
                flap = math.sin(t * 7 + side) * 0.3
                base = self._p(sx, sy, r, fa, r * 0.9)
                tip = self._p(sx, sy, r, fa + flap * side, r * 1.9)
                mid = self._p(sx, sy, r, fa + 0.5 * side, r * 1.1)
                pygame.draw.polygon(surface, self._shade(self.color, 1.25),
                                    [base, tip, mid])

        if self.is_player:
            # a bright ring so the player is always identifiable
            pygame.draw.circle(surface, (255, 255, 255), (sx, sy),
                               max(2, int(r + 4)), max(1, int(1 * cam.zoom)))

    # ---- part renderers ----
    def _p(self, sx, sy, r, world_angle, dist):
        return (sx + math.cos(world_angle) * dist, sy + math.sin(world_angle) * dist)

    def _draw_segments(self, surface, cam, t):
        counts = self.part_counts()
        n_muscle = counts.get("muscle", 0)
        n_armor = counts.get("armor", 0)
        n_photo = counts.get("photo_cell", 0)
        n_seg = len(self.seg_pos)
        for i in range(n_seg - 1, -1, -1):
            sp = self.seg_pos[i]
            if not cam.is_visible(sp, self.seg_radius[i] + 20):
                continue
            ssx, ssy = cam.world_to_screen(sp)
            sr = self.seg_radius[i] * cam.zoom
            body = self.color
            if self.hurt_flash > 0:
                body = (255, 180, 170)
            # photosynthetic segments show green tissue
            if n_photo > 0 and i < n_photo:
                body = tuple(min(255, int(c * 0.55 + g * 0.45))
                             for c, g in zip(body, (110, 220, 130)))
            # direction of travel of this segment (toward its leader)
            lead = self.seg_pos[i - 1] if i > 0 else self.pos
            d = pygame.Vector2(lead) - sp
            seg_ang = math.atan2(d.y, d.x) if d.length_squared() > 1e-6 else self.angle

            pygame.draw.circle(surface, self._shade(body, 0.55), (ssx, ssy),
                               max(2, int(sr + 2)))
            pygame.draw.circle(surface, body, (ssx, ssy), max(1, int(sr)))
            pygame.draw.circle(surface, self._shade(body, 1.3), (ssx, ssy),
                               max(1, int(sr)), max(1, int(2 * cam.zoom)))
            pygame.draw.circle(surface, self._shade(body, 0.6), (ssx, ssy),
                               max(1, int(sr * 0.35)))

            # muscle cells render as paired fins on the front segments
            if i < n_muscle:
                for side in (1, -1):
                    fa = seg_ang + side * (math.pi / 2)
                    flap = math.sin(t * 8 + i) * 0.35
                    tip = (ssx + math.cos(fa + flap) * sr * 1.7,
                           ssy + math.sin(fa + flap) * sr * 1.7)
                    b1 = (ssx + math.cos(seg_ang) * sr * 0.5,
                          ssy + math.sin(seg_ang) * sr * 0.5)
                    b2 = (ssx - math.cos(seg_ang) * sr * 0.5,
                          ssy - math.sin(seg_ang) * sr * 0.5)
                    pygame.draw.polygon(surface, self._shade(self.color, 1.25),
                                        [tip, b1, b2])
            # armor plates render as thick arcs on the back segments
            if n_armor > 0 and i >= n_seg - n_armor:
                rect = pygame.Rect(0, 0, int(sr * 2.6), int(sr * 2.6))
                rect.center = (ssx, ssy)
                pygame.draw.arc(surface, (200, 200, 190), rect,
                                seg_ang + 2.2, seg_ang - 2.2 + math.tau,
                                max(2, int(3 * cam.zoom)))

        # fish tail: a swaying forked caudal fin on the last segment
        if self.stage == "fish" and self.seg_pos:
            tail = self.seg_pos[-1]
            tsx, tsy = cam.world_to_screen(tail)
            tr = max(3.0, self.seg_radius[-1] * cam.zoom)
            lead = self.seg_pos[-2] if len(self.seg_pos) > 1 else self.pos
            back = pygame.Vector2(tail) - pygame.Vector2(lead)
            back_ang = (math.atan2(back.y, back.x)
                        if back.length_squared() > 1e-6 else self.angle + math.pi)
            sway = math.sin(t * 6) * 0.35
            for lobe in (0.55, -0.55):
                a = back_ang + lobe + sway
                tip = (tsx + math.cos(a) * tr * 3.0, tsy + math.sin(a) * tr * 3.0)
                b1 = (tsx + math.cos(back_ang + 1.5) * tr * 0.6,
                      tsy + math.sin(back_ang + 1.5) * tr * 0.6)
                b2 = (tsx + math.cos(back_ang - 1.5) * tr * 0.6,
                      tsy + math.sin(back_ang - 1.5) * tr * 0.6)
                pygame.draw.polygon(surface, self._shade(self.color, 1.2),
                                    [tip, b1, b2])

        # stingers: wavy tentacles trailing off the tail segment
        if self.n_sting > 0 and self.seg_pos:
            tail = self.seg_pos[-1]
            tsx, tsy = cam.world_to_screen(tail)
            tr = self.seg_radius[-1] * cam.zoom
            lead = self.seg_pos[-2] if len(self.seg_pos) > 1 else self.pos
            back = pygame.Vector2(tail) - pygame.Vector2(lead)
            back_ang = (math.atan2(back.y, back.x)
                        if back.length_squared() > 1e-6 else self.angle + math.pi)
            for k in range(self.n_sting):
                spread = (k - (self.n_sting - 1) / 2) * 0.35
                a = back_ang + spread
                pts = [(tsx, tsy)]
                for s in range(1, 7):
                    f = s / 6
                    wig = math.sin(t * 6 + k * 2 + f * 4) * tr * 0.5 * f
                    perp = a + math.pi / 2
                    pts.append((tsx + math.cos(a) * tr * 2.6 * f + math.cos(perp) * wig,
                                tsy + math.sin(a) * tr * 2.6 * f + math.sin(perp) * wig))
                pygame.draw.lines(surface, (235, 150, 200), False, pts,
                                  max(1, int(2 * cam.zoom)))

    def _draw_flagellum(self, surface, sx, sy, r, ap, t):
        wa = self.angle + ap.angle
        length = r * 1.9
        base = self._p(sx, sy, r, wa, r * 0.9)
        perp = wa + math.pi / 2
        pts = [base]
        segs = 8
        for i in range(1, segs + 1):
            f = i / segs
            wig = math.sin(t * 9 + ap.phase + f * 5) * (r * 0.35) * f
            px = base[0] + math.cos(wa) * length * f + math.cos(perp) * wig
            py = base[1] + math.sin(wa) * length * f + math.sin(perp) * wig
            pts.append((px, py))
        if len(pts) > 1:
            pygame.draw.lines(surface, self._shade(self.color, 1.1), False, pts,
                              max(1, int(2 * (r / self.radius))))

    def _draw_cilia(self, surface, sx, sy, r, ap, t):
        wa = self.angle + ap.angle
        for k in range(-2, 3):
            a = wa + k * 0.18
            wob = math.sin(t * 10 + ap.phase + k) * 0.12
            base = self._p(sx, sy, r, a, r * 0.98)
            tip = self._p(sx, sy, r, a + wob, r * 1.35)
            pygame.draw.line(surface, self._shade(self.color, 1.2), base, tip,
                             max(1, int(1.5)))

    def _draw_spike(self, surface, sx, sy, r, ap):
        wa = self.angle + ap.angle
        tip = self._p(sx, sy, r, wa, r * 1.7)
        perp = wa + math.pi / 2
        w = r * 0.32
        b1 = (sx + math.cos(wa) * r * 0.9 + math.cos(perp) * w,
              sy + math.sin(wa) * r * 0.9 + math.sin(perp) * w)
        b2 = (sx + math.cos(wa) * r * 0.9 - math.cos(perp) * w,
              sy + math.sin(wa) * r * 0.9 - math.sin(perp) * w)
        pygame.draw.polygon(surface, (235, 225, 210), [tip, b1, b2])
        pygame.draw.polygon(surface, (90, 80, 70), [tip, b1, b2], 1)

    def _draw_mouth(self, surface, sx, sy, r, ap):
        wa = self.angle + ap.angle
        cx, cy = self._p(sx, sy, r, wa, r * 0.82)
        if ap.id == "jaw":
            col = (210, 90, 90)
            perp = wa + math.pi / 2
            w = r * 0.5
            tip = self._p(sx, sy, r, wa, r * 1.25)
            b1 = (cx + math.cos(perp) * w, cy + math.sin(perp) * w)
            b2 = (cx - math.cos(perp) * w, cy - math.sin(perp) * w)
            pygame.draw.polygon(surface, col, [tip, b1, (cx, cy)])
            pygame.draw.polygon(surface, col, [b2, tip, (cx, cy)])
            pygame.draw.polygon(surface, (120, 40, 40), [tip, b1, b2], 1)
        elif ap.id == "filter_mouth":
            col = (120, 220, 150)
            pygame.draw.circle(surface, col, (int(cx), int(cy)), max(2, int(r * 0.4)))
            pygame.draw.circle(surface, (40, 110, 70), (int(cx), int(cy)),
                               max(2, int(r * 0.4)), 1)
        else:  # proboscis
            col = (200, 170, 120)
            tip = self._p(sx, sy, r, wa, r * 1.5)
            pygame.draw.line(surface, col, (cx, cy), tip, max(2, int(r * 0.22)))
            pygame.draw.circle(surface, (150, 120, 80), (int(tip[0]), int(tip[1])),
                               max(1, int(r * 0.16)))

    def _draw_eye(self, surface, sx, sy, r, ap):
        wa = self.angle + ap.angle
        cx, cy = self._p(sx, sy, r, wa, r * 0.55)
        er = max(2, r * 0.28)
        pygame.draw.circle(surface, (245, 245, 250), (int(cx), int(cy)), int(er))
        look = self.facing()
        pygame.draw.circle(surface, (20, 20, 30),
                           (int(cx + look.x * er * 0.35), int(cy + look.y * er * 0.35)),
                           max(1, int(er * 0.5)))

    def _draw_sensor(self, surface, sx, sy, r, ap, t):
        # a short antenna with a glowing bulb
        wa = self.angle + ap.angle
        base = self._p(sx, sy, r, wa, r * 0.95)
        tip = self._p(sx, sy, r, wa + 0.12 * math.sin(t * 5 + ap.phase), r * 1.45)
        pygame.draw.line(surface, self._shade(self.color, 1.25), base, tip,
                         max(1, int(1.5)))
        glow = 0.65 + 0.35 * math.sin(t * 7 + ap.phase)
        col = (int(160 + 80 * glow), int(200 + 40 * glow), 255)
        pygame.draw.circle(surface, col, (int(tip[0]), int(tip[1])),
                           max(2, int(r * 0.14)))

    def _draw_electric_node(self, surface, sx, sy, r, ap, t):
        wa = self.angle + ap.angle
        cx, cy = self._p(sx, sy, r, wa, r * 0.9)
        flick = 0.6 + 0.4 * math.sin(t * 20 + ap.phase)
        col = (int(120 + 100 * flick), int(180 + 60 * flick), 255)
        pygame.draw.circle(surface, col, (int(cx), int(cy)), max(1, int(r * 0.2)))

    def _draw_poison(self, surface, sx, sy, r, t):
        pr = int(r * 1.5 + 3 * math.sin(t * 4))
        col = (90, 200, 120)
        pygame.draw.circle(surface, col, (sx, sy), max(1, pr), max(1, int(2)))

    @staticmethod
    def _shade(color, factor):
        return tuple(max(0, min(255, int(c * factor))) for c in color)
