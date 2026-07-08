"""Central tuning constants and the color palette for Evolved.

Everything is procedural, so colors live here as plain RGB tuples. Gameplay
numbers are gathered here so the whole feel of the game can be tuned in one
place rather than hunting through the simulation code.
"""

# ---------------------------------------------------------------------------
# Window / world
# ---------------------------------------------------------------------------
SCREEN_W = 1280
SCREEN_H = 800
FPS = 60
TITLE = "Evolved - Cell Stage"

WORLD_W = 3200
WORLD_H = 2400

# Camera zoom: 1.0 = neutral. The camera zooms out (smaller value) as the
# player's cell grows so the world stays readable.
ZOOM_MIN = 0.42
ZOOM_MAX = 1.6
ZOOM_BASE = 1.35          # zoom when the player is at starting size
ZOOM_REF_RADIUS = 15.0    # player radius that maps to ZOOM_BASE

# ---------------------------------------------------------------------------
# Cell base stats
# ---------------------------------------------------------------------------
START_RADIUS = 15.0
MAX_RADIUS = 70.0

BASE_SPEED = 135.0        # px/s of top speed with no movement parts
FLAGELLUM_SPEED = 58.0    # + top speed per flagellum
BASE_TURN = 3.1           # rad/s turn rate with no movement parts
CILIA_TURN = 2.6          # + turn rate per cilia
ACCEL_RESPONSE = 4.5      # how quickly velocity chases the desired velocity
DRAG = 0.86               # velocity retained per second when coasting

# Health / energy
BASE_HEALTH = 100.0
HEALTH_PER_RADIUS = 4.0   # extra max health per px of radius over START_RADIUS
HEALTH_REGEN = 3.5        # hp/s regen when well fed
BASE_ENERGY = 100.0
ENERGY_DRAIN = 1.4        # energy/s at rest (metabolism)
ENERGY_MOVE_DRAIN = 2.2   # extra energy/s at full throttle
STARVE_DAMAGE = 4.0       # hp/s lost when energy is empty
WELL_FED_ENERGY = 45.0    # energy above which health regenerates

# ---------------------------------------------------------------------------
# Growth / evolution / stages
# ---------------------------------------------------------------------------
BASE_SLOTS = 5            # part slots at growth level 0
SLOTS_PER_LEVEL = 2
GROW_RADIUS_MULT = 1.13
BASE_GROW_COST = 18.0     # DNA to grow; scales up with each level
STAGE_MAX_LEVEL = 5       # levels per stage; filling the bar offers the next stage

# Multicellular stage
MULTI_GROW_COST = 30.0    # DNA per growth level in the multicellular stage
MULTI_GROW_RADIUS_MULT = 1.10
SEGMENT_HP = 32.0         # extra max health per body segment
SEGMENT_SLOTS = 2         # extra part slots per body segment
SEGMENT_SPACING = 0.72    # segment spacing as a fraction of head radius
MUSCLE_SPEED = 46.0       # + top speed per muscle cell
MUSCLE_TURN = 0.6         # + turn rate per muscle cell
SENSOR_RANGE = 210.0      # + detect range per sensory cell
STING_DMG = 30.0          # dmg/s per stinger on contact (no facing needed)
ARMOR_REDUCE = 0.16       # damage reduction per armor plate (multiplicative)
ARMOR_REDUCE_FLOOR = 0.30 # armor can never reduce damage below this fraction
PHOTO_ENERGY = 1.5        # energy/s generated per photosynthetic cell
SWALLOW_RATIO = 0.45      # prey this much smaller than you is swallowed whole

# ---------------------------------------------------------------------------
# Food / world density
# ---------------------------------------------------------------------------
PLANT_COUNT = 210
MEAT_DECAY = 22.0         # seconds before an un-eaten meat chunk dissolves
METEOR_COUNT = 12
ALGAE_COUNT = 45          # big plant clusters, food for large/multicellular life
PLANT_RADIUS = 5.0
MEAT_RADIUS = 6.5
METEOR_RADIUS = 9.0
ALGAE_RADIUS = 15.0
ALGAE_MIN_EATER = 24.0    # a cell must be at least this big to graze algae

PLANT_DNA = 2.4
PLANT_ENERGY = 13.0
MEAT_DNA = 3.2
MEAT_ENERGY = 19.0
METEOR_DNA = 11.0
ALGAE_DNA = 7.0
ALGAE_ENERGY = 34.0

# ---------------------------------------------------------------------------
# Combat
# ---------------------------------------------------------------------------
SPIKE_DMG = 26.0          # dmg/s per spike facing the target on contact
BITE_DMG = 34.0           # dmg/s from a carnivore/omnivore mouth on smaller prey
BITE_FEED = 0.55          # energy gained per point of bite damage dealt
POISON_DMG = 18.0         # dmg/s aura to anything touching a poison cell
ELECTRIC_DMG = 22.0       # dmg per electric pulse
ELECTRIC_RANGE = 95.0
ELECTRIC_COOLDOWN = 1.6   # seconds between pulses
CONTACT_PUSH = 260.0      # separation force when two cells overlap
EAT_SIZE_RATIO = 1.12     # you can bite prey whose radius <= yours * this

# ---------------------------------------------------------------------------
# AI population
# ---------------------------------------------------------------------------
AI_CELL_COUNT = 7
AI_MIN_POP = 4            # respawn rivals to keep at least this many
AI_RESPAWN_DELAY = 6.0

# LLM policy cadence (seconds between strategy refreshes per brain, staggered)
LLM_POLICY_INTERVAL = 6.0
LLM_TIMEOUT = 22.0

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
C_WATER_TOP = (7, 26, 48)
C_WATER_BOT = (3, 12, 26)
C_GRID = (255, 255, 255)     # drawn very faint
C_PLANT = (86, 200, 120)
C_PLANT_CORE = (170, 245, 190)
C_ALGAE = (48, 150, 92)
C_ALGAE_CORE = (110, 205, 140)
C_MEAT = (206, 78, 78)
C_MEAT_CORE = (250, 150, 140)
C_METEOR = (150, 130, 230)
C_METEOR_CORE = (215, 205, 255)

C_PLAYER = (90, 220, 210)
C_PLAYER_CORE = (210, 255, 250)

# A palette of distinct hues for rival cells
AI_COLORS = [
    (232, 150, 90),
    (210, 120, 200),
    (120, 160, 240),
    (230, 210, 100),
    (150, 220, 130),
    (240, 130, 130),
    (170, 150, 235),
    (110, 210, 200),
    (235, 175, 120),
    (190, 130, 235),
]

C_TEXT = (225, 235, 245)
C_TEXT_DIM = (150, 168, 184)
C_HUD_BG = (10, 22, 38)
C_HEALTH = (224, 84, 92)
C_ENERGY = (240, 200, 90)
C_DNA = (150, 130, 230)
C_MULTI = (120, 220, 170)
C_PANEL = (16, 30, 50)
C_PANEL_LINE = (44, 70, 100)
C_GOOD = (120, 220, 150)
C_BAD = (235, 110, 110)
