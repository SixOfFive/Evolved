"""AI brain for rival cells.

Two layers:

* **Heuristics** run every frame and every few seconds - they steer the cell,
  flee predators, chase prey/food, buy parts, and grow. This layer alone plays a
  competent game, so rivals work with the LLM disabled or unreachable.
* **LLM policy** (from `llm.LLMManager`) periodically overrides the *strategy*:
  which goal to pursue, which diet to chase, what to evolve next. It makes the
  rivals smarter and more varied without ever stalling the frame loop.
"""

import json
import math
import random

import pygame

from . import config as C
from . import parts as P

GOALS = ("forage", "hunt", "flee", "wander")

_SYSTEM_PROMPT = (
    "You are the brain of a single-celled microbe competing to survive and "
    "evolve in a 2D primordial ocean - the cell stage of Spore. You control ONE "
    "cell. Your aims: eat, avoid being eaten, gather DNA, evolve new organelles, "
    "and GROW until you become multicellular. "
    "Reply with ONLY compact JSON, no prose, of the form: "
    '{"goal":"forage|hunt|flee|wander","diet":"herbivore|carnivore|omnivore",'
    '"evolve":["part_id",...],"grow":true|false,"reason":"few words"}. '
    "Only choose evolve parts from allowed_parts. Herbivores eat plants and must "
    "flee or defend; carnivores hunt smaller cells and eat meat; omnivores do "
    "both. Flee when threats are bigger than you. Be decisive and keep growing."
)

# default evolution priority when the LLM has no opinion
_DEFAULT_PRIORITY = ["flagellum", "spike", "cilia", "eye", "poison",
                     "proboscis", "electric"]


class AIBrain:
    def __init__(self, cell, world, manager, intended_diet=None):
        self.cell = cell
        self.world = world
        self.manager = manager
        self.intended_diet = intended_diet or random.choices(
            ["herbivore", "carnivore", "omnivore"], weights=[45, 35, 20])[0]
        self.goal = "forage"
        self.wishlist = []          # part ids the LLM wants next
        self.want_grow = False
        self.reason = ""
        # stagger timers so brains don't all fire at once
        self._llm_timer = random.uniform(0.5, C.LLM_POLICY_INTERVAL)
        self._evolve_timer = random.uniform(1.0, 3.0)
        self._wander_angle = random.uniform(0, math.tau)
        self._target = None

    # ------------------------------------------------------------- perception
    def _nearest_cell(self, predicate, max_dist):
        best, bestd = None, max_dist * max_dist
        for other in self.world.cells:
            if other is self.cell or not other.alive:
                continue
            if not predicate(other):
                continue
            d2 = (other.pos - self.cell.pos).length_squared()
            if d2 < bestd:
                best, bestd = other, d2
        return best

    def _nearest_food(self, kinds, max_dist):
        best, bestd = None, max_dist * max_dist
        for f in self.world.foods:
            if not f.alive or f.kind not in kinds:
                continue
            d2 = (f.pos - self.cell.pos).length_squared()
            if d2 < bestd:
                best, bestd = f, d2
        return best

    def _nearest_meteor(self, max_dist):
        best, bestd = None, max_dist * max_dist
        for m in self.world.meteors:
            if not m.alive:
                continue
            d2 = (m.pos - self.cell.pos).length_squared()
            if d2 < bestd:
                best, bestd = m, d2
        return best

    def _nearest_predator(self, max_dist):
        cell = self.cell
        return self._nearest_cell(
            lambda o: o.radius >= cell.radius * 1.15
            and (o.can_bite_cells or o.n_spike > 0),
            max_dist)

    # ------------------------------------------------------------------ update
    def update(self, dt):
        cell = self.cell
        if not cell.alive:
            return

        # periodic self-directed evolution (works with or without the LLM)
        self._evolve_timer -= dt
        if self._evolve_timer <= 0:
            self._evolve_timer = random.uniform(2.5, 4.0)
            self._auto_evolve()

        # periodic LLM strategy refresh
        self._llm_timer -= dt
        if self._llm_timer <= 0:
            self._llm_timer = C.LLM_POLICY_INTERVAL + random.uniform(-1.0, 1.5)
            self._request_policy()

        self._steer(dt)

    # ------------------------------------------------------------------ steer
    def _steer(self, dt):
        cell = self.cell
        detect = cell.detect_range

        # reflex: a close, bigger threat overrides everything
        threat = self._nearest_predator(detect * 0.7)
        if threat is not None:
            td = (threat.pos - cell.pos).length()
            danger = threat.radius + cell.radius + 70
            if td < danger:
                self._flee_from(threat.pos)
                return

        goal = self.goal
        if goal == "flee" and threat is not None:
            self._flee_from(threat.pos)
            return

        target = None
        if goal == "hunt" and cell.can_bite_cells:
            target = self._nearest_cell(
                lambda o: cell.tier_of(o) == "prey", detect)
            if target is None and cell.can_eat_meat:
                target = self._nearest_food(("meat",), detect)
        elif goal == "forage" or target is None:
            kinds = []
            if cell.can_eat_plant:
                kinds.append("plant")
            if cell.can_eat_meat:
                kinds.append("meat")
            if kinds:
                target = self._nearest_food(tuple(kinds), detect)
            if target is None and cell.can_bite_cells:
                target = self._nearest_cell(
                    lambda o: cell.tier_of(o) == "prey", detect)

        # meteors are always worth grabbing if close
        meteor = self._nearest_meteor(detect * 0.6)
        if meteor is not None and (target is None or
                                   (meteor.pos - cell.pos).length()
                                   < (pygame.Vector2(target.pos) - cell.pos).length()):
            target = meteor

        if target is not None:
            self._seek(target.pos)
        else:
            self._wander(dt)

    def _seek(self, pos):
        d = pygame.Vector2(pos) - self.cell.pos
        if d.length_squared() > 1e-6:
            self.cell.thrust = d.normalize()

    def _flee_from(self, pos):
        d = self.cell.pos - pygame.Vector2(pos)
        if d.length_squared() > 1e-6:
            self.cell.thrust = d.normalize()

    def _wander(self, dt):
        self._wander_angle += random.uniform(-1.5, 1.5) * dt
        self.cell.thrust = pygame.Vector2(math.cos(self._wander_angle),
                                          math.sin(self._wander_angle)) * 0.6
        # keep away from the world edges
        m = 220
        p = self.cell.pos
        if p.x < m:
            self.cell.thrust.x += 1
        elif p.x > C.WORLD_W - m:
            self.cell.thrust.x -= 1
        if p.y < m:
            self.cell.thrust.y += 1
        elif p.y > C.WORLD_H - m:
            self.cell.thrust.y -= 1

    # -------------------------------------------------------------- evolution
    def _auto_evolve(self):
        cell = self.cell
        # 1) ensure a mouth matching the intended diet
        if cell.diet == "none":
            self._buy_mouth_for(self.intended_diet)
            return
        # upgrade toward the intended diet (e.g. herbivore -> omnivore)
        if self.intended_diet == "omnivore" and cell.diet != "omnivore":
            if cell.diet == "herbivore":
                self._try_buy("jaw")
            elif cell.diet == "carnivore":
                self._try_buy("filter_mouth")
            return
        if self.intended_diet == "carnivore" and cell.diet == "herbivore":
            self._try_buy("jaw")
            return

        # 2) make sure it can move
        if cell.part_counts().get("flagellum", 0) == 0:
            if self._try_buy("flagellum"):
                return

        # 3) predators want a weapon
        if (self.intended_diet in ("carnivore", "omnivore")
                and cell.n_spike == 0 and "spike" in cell.available_parts()):
            if self._try_buy("spike"):
                return

        # 4) spend from the LLM wishlist, then defaults
        for pid in list(self.wishlist) + _DEFAULT_PRIORITY:
            if pid in cell.available_parts() and self._try_buy(pid):
                break

        # 5) grow when we can afford to and still keep a buffer
        buffer = 12.0
        if cell.can_grow() and (self.want_grow or cell.dna >= cell.grow_cost() + buffer):
            cell.grow()

    def _buy_mouth_for(self, diet):
        if diet == "herbivore":
            self._try_buy("filter_mouth")
        elif diet == "carnivore":
            self._try_buy("jaw")
        else:  # omnivore -> start with whichever is available/affordable
            if not self._try_buy("proboscis"):
                self._try_buy("jaw") or self._try_buy("filter_mouth")

    def _try_buy(self, part_id):
        cell = self.cell
        if part_id in cell.available_parts() and cell.can_add(part_id):
            return cell.add_part(part_id)
        return False

    # ---------------------------------------------------------------- the LLM
    def _request_policy(self):
        if not self.manager.enabled or self.manager.busy(self.cell.id):
            return
        state = self._snapshot()
        self.manager.request(self.cell.id, _SYSTEM_PROMPT, json.dumps(state))

    def _snapshot(self):
        cell = self.cell
        detect = cell.detect_range
        threats, prey = [], []
        plants = meats = meteors = 0
        for o in self.world.cells:
            if o is cell or not o.alive:
                continue
            d = (o.pos - cell.pos).length()
            if d > detect:
                continue
            if o.radius >= cell.radius * 1.15:
                threats.append({"dist": round(d), "size_ratio": round(o.radius / cell.radius, 2)})
            elif cell.tier_of(o) == "prey":
                prey.append({"dist": round(d)})
        for f in self.world.foods:
            d = (f.pos - cell.pos).length()
            if d > detect:
                continue
            if f.kind == "plant":
                plants += 1
            else:
                meats += 1
        for m in self.world.meteors:
            if (m.pos - cell.pos).length() <= detect:
                meteors += 1

        allowed = []
        for pid in cell.available_parts():
            pdef = P.PART_DEFS[pid]
            allowed.append({"id": pid, "cost": pdef.cost, "cat": pdef.category})

        return {
            "me": {
                "diet": cell.diet,
                "intended_diet": self.intended_diet,
                "growth_level": cell.growth_level,
                "radius": round(cell.radius, 1),
                "health_pct": round(100 * cell.health / cell.max_health),
                "energy_pct": round(100 * cell.energy / cell.max_energy),
                "dna": round(cell.dna, 1),
                "parts": cell.part_counts(),
                "can_grow": cell.can_grow(),
                "grow_cost": round(cell.grow_cost(), 1),
            },
            "allowed_parts": allowed,
            "threats": threats[:5],
            "prey": prey[:5],
            "plants_near": plants,
            "meat_near": meats,
            "meteors_near": meteors,
            "multicellular_at_level": C.MULTICELLULAR_LEVEL,
        }

    def apply_policy(self, policy):
        """Apply an LLM policy dict (called from the main thread)."""
        if not isinstance(policy, dict):
            return
        goal = policy.get("goal")
        if goal in GOALS:
            self.goal = goal
        diet = policy.get("diet")
        if diet in ("herbivore", "carnivore", "omnivore"):
            self.intended_diet = diet
        evolve = policy.get("evolve")
        if isinstance(evolve, list):
            self.wishlist = [p for p in evolve if isinstance(p, str) and p in P.PART_DEFS]
        self.want_grow = bool(policy.get("grow"))
        r = policy.get("reason")
        if isinstance(r, str):
            self.reason = r[:40]
