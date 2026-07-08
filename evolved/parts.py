"""Organelle (cell part) catalog.

Each entry describes a part the player or an AI cell can bolt on in the
evolution editor: what it costs in DNA, which gameplay stats it grants, and
metadata used both by the simulation and the procedural renderer.

Categories: mouth, movement, offense, defense, sense.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PartDef:
    id: str
    name: str
    category: str
    cost: float
    desc: str
    # diet granted by a mouth part, if any
    diet: str = ""
    # preferred placement angle (radians) relative to heading; None = free.
    # 0 == front (nose), math.pi == rear.
    prefer_angle: float = None
    key: str = ""  # editor hotkey
    # growth level at which this part becomes available in the editor
    unlock_level: int = 0


# math.pi ~ 3.14159 used inline to avoid importing math at module import time
_PI = 3.141592653589793

PART_DEFS = {
    # --- Mouths (determine diet & how you feed) -----------------------------
    "filter_mouth": PartDef(
        id="filter_mouth", name="Filter Mouth", category="mouth", cost=12,
        diet="herbivore", prefer_angle=0.0, key="1",
        desc="Herbivore mouth. Graze on green plant matter.",
    ),
    "jaw": PartDef(
        id="jaw", name="Carnivore Jaw", category="mouth", cost=16,
        diet="carnivore", prefer_angle=0.0, key="2",
        desc="Carnivore mouth. Bite smaller cells and eat meat chunks.",
    ),
    "proboscis": PartDef(
        id="proboscis", name="Proboscis", category="mouth", cost=22,
        diet="omnivore", prefer_angle=0.0, key="3", unlock_level=3,
        desc="Omnivore mouth. Eat both plants and meat.",
    ),
    # --- Movement -----------------------------------------------------------
    "flagellum": PartDef(
        id="flagellum", name="Flagellum", category="movement", cost=10,
        prefer_angle=_PI, key="4",
        desc="A whipping tail. Increases top swimming speed.",
    ),
    "cilia": PartDef(
        id="cilia", name="Cilia", category="movement", cost=9,
        prefer_angle=None, key="5", unlock_level=2,
        desc="Fine hairs. Increases how sharply you can turn.",
    ),
    # --- Offense ------------------------------------------------------------
    "spike": PartDef(
        id="spike", name="Spike", category="offense", cost=13,
        prefer_angle=None, key="6", unlock_level=1,
        desc="A rigid barb. Damages cells you ram - and cells that ram you.",
    ),
    "electric": PartDef(
        id="electric", name="Electric Jet", category="offense", cost=20,
        prefer_angle=None, key="7", unlock_level=4,
        desc="Discharges a shock, damaging all nearby cells in pulses.",
    ),
    # --- Defense ------------------------------------------------------------
    "poison": PartDef(
        id="poison", name="Poison Sac", category="defense", cost=16,
        prefer_angle=None, key="8", unlock_level=2,
        desc="A toxic aura. Poisons any cell that touches you.",
    ),
    # --- Sense --------------------------------------------------------------
    "eye": PartDef(
        id="eye", name="Eye", category="sense", cost=7,
        prefer_angle=0.5, key="9",
        desc="A light-sensing spot. Widens how far you perceive the world.",
    ),
}

# Ordered list for the editor / LLM option menus
PART_ORDER = [
    "filter_mouth", "jaw", "proboscis",
    "flagellum", "cilia",
    "spike", "electric", "poison", "eye",
]

MOUTH_PARTS = {"filter_mouth", "jaw", "proboscis"}
DIET_OF_MOUTH = {
    "filter_mouth": "herbivore",
    "jaw": "carnivore",
    "proboscis": "omnivore",
}


def get(part_id):
    return PART_DEFS[part_id]


def all_ids():
    return list(PART_ORDER)
