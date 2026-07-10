"""Persistent all-time records, kept in records.json next to main.py."""

import json
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "records.json")

_STAGE_RANK = {"cell": 0, "multi": 1, "fish": 2}
_STAGE_NAME = {"cell": "cell", "multi": "multicellular", "fish": "fish"}

DEFAULTS = {
    "runs": 0,
    "best_survival": 0.0,
    "best_stage": "cell",
    "best_level": 0,
    "best_fish_level": 0,
    "best_dna": 0.0,
    "total_kills": 0,
    "total_leviathans": 0,
}


def load():
    try:
        with open(PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    rec = dict(DEFAULTS)
    rec.update({k: data[k] for k in DEFAULTS if k in data})
    return rec


def update_on_death(player, leviathans_this_run):
    """Fold a finished run into the records. Returns (records, new_flags)."""
    rec = load()
    new = set()
    rec["runs"] += 1
    rec["total_kills"] += player.kills
    rec["total_leviathans"] += leviathans_this_run

    if player.time_alive > rec["best_survival"]:
        rec["best_survival"] = round(player.time_alive, 1)
        new.add("survival")
    if ((_STAGE_RANK[player.stage], player.growth_level)
            > (_STAGE_RANK[rec["best_stage"]], rec["best_level"])):
        rec["best_stage"] = player.stage
        rec["best_level"] = player.growth_level
        new.add("stage")
    if player.stage == "fish" and player.growth_level > rec["best_fish_level"]:
        rec["best_fish_level"] = player.growth_level
        new.add("fish")
    if player.lifetime_dna > rec["best_dna"]:
        rec["best_dna"] = round(player.lifetime_dna, 1)
        new.add("dna")

    try:
        with open(PATH, "w") as f:
            json.dump(rec, f, indent=2)
    except OSError:
        pass
    return rec, new


def stage_name(stage):
    return _STAGE_NAME.get(stage, stage)
