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


class EpicBrain:
    """The Leviathan's mind: no LLM, no evolution - relentless hunting.

    It stalks the largest organism it can sense, favoring big prey over
    near prey, and cruises the pond when nothing is worth chasing.
    """

    def __init__(self, cell, world):
        self.cell = cell
        self.world = world
        self.goal = "hunt"
        self.reason = "apex predator"
        self.intended_diet = "carnivore"
        self.awaiting_spawn_choice = False
        self._target = None
        self._retimer = 0.0
        self._cruise = pygame.Vector2(C.WORLD_W / 2, C.WORLD_H / 2)

    def update(self, dt):
        cell = self.cell
        self._retimer -= dt
        if (self._retimer <= 0 or self._target is None
                or not self._target.alive):
            self._retimer = 2.0
            best, best_score = None, -1e9
            for o in self.world.cells:
                if o is cell or not o.alive or o.is_epic:
                    continue
                if not self.world.can_see(cell, o):
                    continue
                d = (o.pos - cell.pos).length()
                if d > 1700:
                    continue
                score = o.radius - d * 0.02
                if score > best_score:
                    best, best_score = o, score
            self._target = best
            if best is None:
                # nothing worth chasing - lurk around the dark trench
                if random.random() < 0.7:
                    self._cruise = (pygame.Vector2(self.world.trench_c)
                                    + pygame.Vector2(random.uniform(-450, 450),
                                                     random.uniform(-450, 450)))
                else:
                    self._cruise = pygame.Vector2(
                        random.uniform(400, C.WORLD_W - 400),
                        random.uniform(400, C.WORLD_H - 400))
        aim = self._target.pos if self._target is not None else self._cruise
        d = pygame.Vector2(aim) - cell.pos
        if d.length_squared() > 1:
            cell.thrust = d.normalize()

    def apply_policy(self, policy):
        pass  # the deep does not take advice

_SYSTEM_PROMPT = (
    "You are the brain of a microbe competing to survive and evolve in a 2D "
    "primordial pond - like the water stages of Spore. You control ONE "
    "organism. Your aims: eat, avoid being eaten, gather DNA, evolve new "
    "parts, GROW, and climb the stages: single cell -> multicellular -> FISH. "
    "The fish stage is the endgame: there is no leaving the pond - fish keep "
    "leveling forever with DNA, growing ever larger and stronger. "
    "Reply with ONLY compact JSON, no prose, of the form: "
    '{"goal":"forage|hunt|flee|wander","diet":"herbivore|carnivore|omnivore",'
    '"evolve":["part_id",...],"grow":true|false,"reason":"few words",'
    '"say":"optional short in-character remark, 6 words max"}. '
    "Only choose evolve parts from allowed_parts. Herbivores eat plants/algae "
    "and must flee or defend; carnivores hunt smaller organisms and eat meat; "
    "omnivores do both. In the multicellular and fish stages prefer body "
    "segments, muscles, stingers, armor and sensors. Flee when threats are "
    "bigger than you. Be decisive and keep growing."
)

_SPAWN_PROMPT = (
    "You are a newly spawned single-celled microbe in a 2D primordial ocean "
    "(the cell stage of Spore). Decide your survival strategy for this life: "
    "will you HUNT other cells (carnivore), HARVEST plants (herbivore), or do "
    "BOTH (omnivore)? Consider the food and threats around you. Reply with "
    'ONLY compact JSON: {"strategy":"hunt|harvest|both","reason":"few words"}.'
)

# default evolution priority when the LLM has no opinion
_DEFAULT_PRIORITY = ["flagellum", "spike", "cilia", "eye", "poison",
                     "proboscis", "electric"]
_MULTI_PRIORITY = ["segment", "muscle", "stinger", "armor", "sensor",
                   "photo_cell", "electric"]

_STRATEGY_TO_DIET = {"hunt": "carnivore", "harvest": "herbivore",
                     "both": "omnivore"}

# Each rival gets a temperament, injected into its prompts and reflected in
# its reflexes. Same model, visibly different characters.
PERSONALITIES = {
    "aggressive": "you pick fights and hunt whenever possible",
    "cautious": "you avoid all risk and flee at the first sign of danger",
    "greedy": "food, algae and meteors matter more to you than anything",
    "vengeful": "you remember who hurt you and you strike back",
    "territorial": "you defend your patch of the pond and rarely stray",
    "curious": "you roam widely and investigate everything you sense",
}
_FLEE_FRAC = {"aggressive": 0.35, "vengeful": 0.45, "cautious": 0.8}


class AIBrain:
    def __init__(self, cell, world, manager, intended_diet=None):
        self.cell = cell
        self.world = world
        self.manager = manager
        self.intended_diet = intended_diet or random.choices(
            ["herbivore", "carnivore", "omnivore"], weights=[45, 35, 20])[0]
        self.personality = random.choice(list(PERSONALITIES))
        self.flee_frac = _FLEE_FRAC.get(self.personality, 0.6)
        self.goal = "hunt" if self.personality == "aggressive" else "forage"
        self.wishlist = []          # part ids the LLM wants next
        self.want_grow = False
        self.reason = ""
        # spawn-time strategy choice (hunt/harvest/both) made by the LLM
        self.awaiting_spawn_choice = False
        self._spawn_choice_timer = 0.0
        # stagger timers so brains don't all fire at once
        self._llm_timer = random.uniform(0.5, C.LLM_POLICY_INTERVAL)
        self._evolve_timer = random.uniform(1.0, 3.0)
        self._wander_angle = random.uniform(0, math.tau)
        # cached food target: scanning every food item every frame doesn't
        # scale to a big pond, so brains re-scan a few times a second
        self._food_target = None
        self._retarget_timer = 0.0
        # anti-orbit: fast cells with weak turning have a wide turning
        # circle and can loop around close targets forever. Track closing
        # progress and brake out of a detected orbit.
        self._seek_pos = None
        self._seek_best = float("inf")
        self._stall = 0.0
        self._brake = 0.0

    # ---------------------------------------------------------- spawn choice
    def begin_spawn_choice(self):
        """Ask the LLM whether this new cell will hunt, harvest, or both."""
        self.awaiting_spawn_choice = True
        self._spawn_choice_timer = 8.0   # heuristic fallback deadline
        state = self._snapshot()
        prompt = (_SPAWN_PROMPT + f" Your personality: {self.personality} - "
                  f"{PERSONALITIES[self.personality]}.")
        if self.manager.request(self.cell.id, prompt, json.dumps(state)):
            self.world.log(f"-> LLM [{self.cell.name}]: hunt, harvest or both?",
                           C.C_LLM)
        else:
            self.equip_starting_mouth()

    def equip_starting_mouth(self):
        """Equip the free starting mouth for the intended diet."""
        self.awaiting_spawn_choice = False
        cell = self.cell
        if cell.diet != "none":
            return
        if self.intended_diet == "carnivore":
            cell.add_part("jaw", spend=False)
        elif self.intended_diet == "omnivore":
            cell.add_part("jaw", spend=False)
            cell.add_part("filter_mouth", spend=False)
        else:
            cell.add_part("filter_mouth", spend=False)

    # ------------------------------------------------------------- perception
    def _nearest_cell(self, predicate, max_dist):
        best, bestd = None, max_dist * max_dist
        for other in self.world.cells:
            if other is self.cell or not other.alive:
                continue
            if not predicate(other):
                continue
            if not self.world.can_see(self.cell, other):
                continue  # hidden in the weeds
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
            and (o.can_bite_cells or o.n_spike > 0 or o.n_sting > 0),
            max_dist)

    def _edible_kinds(self):
        cell = self.cell
        kinds = []
        if cell.can_eat_plant:
            kinds.append("plant")
            if cell.radius >= C.ALGAE_MIN_EATER:
                kinds.append("algae")
        if cell.can_eat_meat:
            kinds.append("meat")
        return tuple(kinds)

    def _food_target_get(self, kinds, max_dist):
        """The nearest edible food, cached between periodic re-scans."""
        t = self._food_target
        if (self._retarget_timer <= 0 or t is None or not t.alive
                or t.kind not in kinds):
            self._food_target = self._nearest_food(kinds, max_dist)
            self._retarget_timer = random.uniform(0.25, 0.4)
        return self._food_target

    # ------------------------------------------------------------------ update
    def update(self, dt):
        cell = self.cell
        if not cell.alive:
            return

        # waiting on the LLM's hunt/harvest/both call: fall back if it's slow
        if self.awaiting_spawn_choice:
            self._spawn_choice_timer -= dt
            if self._spawn_choice_timer <= 0:
                self.equip_starting_mouth()

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
        self._retarget_timer -= dt
        self._brake = max(0.0, self._brake - dt)

        # reflex: whoever is actively hurting us comes first - tail chewers
        # can be smaller than us and would never register as "predators".
        # How soon we run depends on personality; the vengeful hit back.
        la = getattr(cell, "last_attacker", None)
        if (la is not None and getattr(la, "alive", False)
                and cell.time_alive - cell.last_hit_time < 1.5):
            if (self.personality == "vengeful" and cell.can_bite_cells
                    and cell.health > cell.max_health * 0.5
                    and la.radius < cell.radius * 1.25):
                self._seek(la.pos, dt)   # revenge!
                return
            if cell.health < cell.max_health * self.flee_frac:
                self._flee_from(la.pos)
                return

        # reflex: a close, bigger threat overrides everything else
        threat = self._nearest_predator(detect * 0.7)
        if threat is not None:
            td = (threat.pos - cell.pos).length()
            danger = threat.radius + cell.radius + 70
            if td < danger:
                self._flee_from(threat.pos)
                return

        # hunger: when energy runs low, drop everything and go eat
        if cell.energy < cell.max_energy * 0.35:
            kinds = self._edible_kinds()
            food = self._food_target_get(kinds, detect * 1.6) if kinds else None
            if food is None and cell.can_bite_cells:
                food = self._nearest_cell(lambda o: cell.tier_of(o) == "prey", detect)
            if food is not None:
                self._seek(food.pos, dt)
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
                target = self._food_target_get(("meat",), detect)
        elif goal == "forage" or target is None:
            kinds = self._edible_kinds()
            if kinds:
                target = self._food_target_get(kinds, detect)
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
            self._seek(target.pos, dt)
        else:
            self._wander(dt)

    def _seek(self, pos, dt=0.0):
        cell = self.cell
        d = pygame.Vector2(pos) - cell.pos
        dist = d.length()
        if dist < 1e-6:
            return
        dirv = d / dist

        # orbit detection: same (near-static) target, but the gap has
        # stopped closing -> we're circling it on a too-wide turning arc
        if (self._seek_pos is not None
                and (pygame.Vector2(pos) - self._seek_pos).length_squared() < 1600):
            if dist < self._seek_best - 6.0:
                self._seek_best = dist
                self._stall = 0.0
            else:
                self._stall += dt
        else:
            self._seek_pos = pygame.Vector2(pos)
            self._seek_best = dist
            self._stall = 0.0
        if self._stall > 1.4 and dist < 420:
            self._brake = 0.7   # evade the loop: kill speed, then re-approach
            self._stall = 0.0

        # slow into sharp turns - turning radius is speed/turn_rate, so
        # easing off the throttle is what makes tight approaches possible
        desired = math.atan2(dirv.y, dirv.x)
        diff = abs((desired - cell.angle + math.pi) % math.tau - math.pi)
        mag = 1.0
        if diff > 0.5:
            mag = max(0.3, math.cos(min(diff, math.pi / 2)))
        if self._brake > 0:
            mag = 0.18          # crawl: tiny speed = tiny turning circle
        cell.thrust = dirv * mag

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
        if self.awaiting_spawn_choice:
            return  # no strategy yet - wait for the LLM (or the fallback)

        # 0) stage advancement: rivals push on when ready
        if cell.can_advance_stage():
            cell.advance_stage()
            self.world.log(f"{cell.name} evolved into a multicellular organism!",
                           (200, 160, 255))
            return
        if cell.can_evolve_brain():
            cell.become_fish()
            self.world.log(f"{cell.name} grew a brain and became a FISH!",
                           (150, 220, 255))
            return

        # buy at most one part per tick...
        self._buy_one_part()

        # ...but ALWAYS consider growing: a failed purchase must never block
        # growth, because growing is what raises the slot cap.
        buffer = 6.0
        if cell.can_grow() and (self.want_grow or cell.dna >= cell.grow_cost() + buffer):
            cell.grow()

    def _buy_one_part(self):
        cell = self.cell
        counts = cell.part_counts()

        # 1) a mouth matching the intended diet comes first
        if cell.diet == "none":
            return self._buy_mouth_for(self.intended_diet)
        # upgrade toward the intended diet (e.g. herbivore -> omnivore)
        if self.intended_diet == "omnivore" and cell.diet != "omnivore":
            if self._try_buy("filter_mouth" if cell.diet == "carnivore" else "jaw"):
                return True
        elif self.intended_diet == "carnivore" and cell.diet == "herbivore":
            if self._try_buy("jaw"):
                return True

        # 2) make sure it can move
        if counts.get("flagellum", 0) == 0 and self._try_buy("flagellum"):
            return True

        # 3) predators want a weapon
        if (self.intended_diet in ("carnivore", "omnivore")
                and cell.n_spike == 0 and "spike" in cell.available_parts()
                and self._try_buy("spike")):
            return True

        # 4) spend from the LLM wishlist, then stage-appropriate defaults.
        # No hard caps - duplicates stack (with diminishing returns), so the
        # pick is weighted instead: the more copies owned, the less likely
        # another one is bought, and the LLM's wishes weigh triple.
        defaults = (_MULTI_PRIORITY if cell.stage in ("multi", "fish")
                    else _DEFAULT_PRIORITY)
        available = cell.available_parts()
        candidates = []
        for pid in dict.fromkeys(list(self.wishlist) + defaults):
            if pid not in available or not cell.can_add(pid):
                continue
            weight = 1.0 / ((counts.get(pid, 0) + 1) ** 1.5)
            if pid in self.wishlist:
                weight *= 3.0
            candidates.append((pid, weight))
        if not candidates:
            return False
        r = random.random() * sum(w for _, w in candidates)
        for pid, w in candidates:
            r -= w
            if r <= 0:
                if self._try_buy(pid):
                    if pid in self.wishlist:
                        self.wishlist.remove(pid)
                    return True
                return False
        return False

    def _buy_mouth_for(self, diet):
        if diet == "herbivore":
            return self._try_buy("filter_mouth")
        if diet == "carnivore":
            return self._try_buy("jaw")
        # omnivore -> start with whichever is available/affordable
        return (self._try_buy("proboscis") or self._try_buy("jaw")
                or self._try_buy("filter_mouth"))

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
        prompt = (_SYSTEM_PROMPT + f" Your personality: {self.personality} - "
                  f"{PERSONALITIES[self.personality]}.")
        if self.manager.request(self.cell.id, prompt, json.dumps(state)):
            what = ("control the player" if self.cell.is_player
                    else "what's my move?")
            self.world.log(f"-> LLM [{self.cell.name}]: {what}", C.C_LLM)

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
                "stage": cell.stage,
                "diet": cell.diet,
                "intended_diet": self.intended_diet,
                "growth_level": cell.growth_level,
                "body_segments": cell.n_segments(),
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
            "stage_max_level": C.STAGE_MAX_LEVEL,
            "next_stage": {"cell": "multicellular",
                           "multi": "fish (grow a brain)",
                           "fish": "none - keep leveling forever"}[cell.stage],
        }

    def apply_policy(self, policy):
        """Apply an LLM policy dict (called from the main thread)."""
        if not isinstance(policy, dict):
            return
        # spawn-time strategy answer: hunt / harvest / both
        strategy = policy.get("strategy")
        if isinstance(strategy, str):
            diet = _STRATEGY_TO_DIET.get(strategy.strip().lower())
            if diet and self.awaiting_spawn_choice:
                self.intended_diet = diet
                self.equip_starting_mouth()
                r = policy.get("reason")
                if isinstance(r, str):
                    self.reason = r[:40]
                extra = f' ("{self.reason}")' if self.reason else ""
                self.world.log(
                    f"<- LLM [{self.cell.name}]: "
                    f"{strategy.strip().lower()}{extra}", C.C_LLM)
                if self.reason:
                    self.cell.say(self.reason)
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
        say = policy.get("say")
        if isinstance(say, str) and say.strip():
            self.cell.say(say.strip())
        # feed line: what the LLM decided
        bits = [self.goal, self.intended_diet]
        if self.wishlist:
            bits.append("evolve " + ",".join(self.wishlist[:2]))
        if self.want_grow:
            bits.append("grow")
        self.world.log(f"<- LLM [{self.cell.name}]: {', '.join(bits)}", C.C_LLM)
