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
        self.growth_level = 0
        self.dna = 0.0
        self.generation = 1
        self.multicellular = False

        self.health = C.BASE_HEALTH
        self.max_health = C.BASE_HEALTH
        self.energy = C.BASE_ENERGY
        self.max_energy = C.BASE_ENERGY

        self.alive = True
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
        self.has_poison = False
        self.has_electric = False
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
        """Part ids the cell is allowed to add right now (unlocked + free slot)."""
        out = []
        for pid in P.PART_ORDER:
            pdef = P.PART_DEFS[pid]
            if pdef.unlock_level <= self.growth_level or pid in self.discovered:
                out.append(pid)
        return out

    def can_add(self, part_id, spend=True):
        pdef = P.PART_DEFS[part_id]
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
        return C.BASE_GROW_COST * (self.growth_level + 1)

    def can_grow(self):
        return (self.dna >= self.grow_cost()
                and self.radius < C.MAX_RADIUS
                and self.growth_level < C.MULTICELLULAR_LEVEL)

    def grow(self):
        if not self.can_grow():
            return False
        self.dna -= self.grow_cost()
        self.growth_level += 1
        self.radius = min(C.MAX_RADIUS, self.radius * C.GROW_RADIUS_MULT)
        self.recompute()
        self.health = self.max_health  # a growth spurt restores you
        if self.growth_level >= C.MULTICELLULAR_LEVEL:
            self.multicellular = True
        return True

    def recompute(self):
        counts = self.part_counts()
        n_flag = counts.get("flagellum", 0)
        n_cil = counts.get("cilia", 0)
        self.n_spike = counts.get("spike", 0)
        n_eye = counts.get("eye", 0)
        self.has_poison = counts.get("poison", 0) > 0
        self.has_electric = counts.get("electric", 0) > 0

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

        self.speed = C.BASE_SPEED + n_flag * C.FLAGELLUM_SPEED
        self.turn_rate = C.BASE_TURN + n_cil * C.CILIA_TURN
        self.detect_range = 360.0 + n_eye * 150.0
        self.max_slots = C.BASE_SLOTS + self.growth_level * C.SLOTS_PER_LEVEL

        new_max = C.BASE_HEALTH + (self.radius - C.START_RADIUS) * C.HEALTH_PER_RADIUS
        if new_max > self.max_health:
            # keep same fraction of health when max grows
            frac = self.health / self.max_health if self.max_health else 1.0
            self.health = new_max * frac
        self.max_health = new_max
        self.health = min(self.health, self.max_health)

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
        self.health -= amount
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
        return min(n, 3)

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

        # --- metabolism ---
        drain = C.ENERGY_DRAIN + C.ENERGY_MOVE_DRAIN * thrust_mag
        self.energy = max(0.0, self.energy - drain * dt)
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
            pr = r + prog * C.ELECTRIC_RANGE * cam.zoom
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

        if self.is_player:
            # a bright ring so the player is always identifiable
            pygame.draw.circle(surface, (255, 255, 255), (sx, sy),
                               max(2, int(r + 4)), max(1, int(1 * cam.zoom)))

    # ---- part renderers ----
    def _p(self, sx, sy, r, world_angle, dist):
        return (sx + math.cos(world_angle) * dist, sy + math.sin(world_angle) * dist)

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
