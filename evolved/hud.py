"""Heads-up display, water background, minimap and overhead cell labels.

All rendering here is procedural: a gradient ocean, a faint drifting grid and
particle field for a sense of motion, stat bars, an event log and a minimap.
"""

import math
import random

import pygame

from . import config as C


def _lerp_color(a, b, f):
    return (int(a[0] + (b[0] - a[0]) * f),
            int(a[1] + (b[1] - a[1]) * f),
            int(a[2] + (b[2] - a[2]) * f))


class HUD:
    def __init__(self, screen_w, screen_h):
        pygame.font.init()
        self.font_s = pygame.font.SysFont("consolas", 15)
        self.font_m = pygame.font.SysFont("consolas", 19)
        self.font_l = pygame.font.SysFont("consolas", 30, bold=True)
        self.font_xl = pygame.font.SysFont("consolas", 54, bold=True)
        self._bg_size = None
        self._bg = None
        # background particle field in world space
        self.particles = [(random.uniform(0, C.WORLD_W),
                           random.uniform(0, C.WORLD_H),
                           random.uniform(0.6, 1.8)) for _ in range(420)]

    # ---------------------------------------------------------- background
    def _ensure_bg(self, size):
        if self._bg_size == size:
            return
        w, h = size
        surf = pygame.Surface(size)
        for y in range(h):
            f = y / max(1, h - 1)
            surf.fill(_lerp_color(C.C_WATER_TOP, C.C_WATER_BOT, f), (0, y, w, 1))
        self._bg = surf
        self._bg_size = size

    def draw_background(self, surface, cam, t):
        size = surface.get_size()
        self._ensure_bg(size)
        surface.blit(self._bg, (0, 0))

        # faint world grid
        grid = 240
        z = cam.zoom
        left = cam.center.x - size[0] / 2 / z
        right = cam.center.x + size[0] / 2 / z
        top = cam.center.y - size[1] / 2 / z
        bot = cam.center.y + size[1] / 2 / z
        gx0 = int(left // grid) * grid
        gy0 = int(top // grid) * grid
        gcol = (20, 42, 66)
        x = gx0
        while x <= right:
            sx = cam.world_to_screen((x, 0))[0]
            pygame.draw.line(surface, gcol, (sx, 0), (sx, size[1]))
            x += grid
        y = gy0
        while y <= bot:
            sy = cam.world_to_screen((0, y))[1]
            pygame.draw.line(surface, gcol, (0, sy), (size[0], sy))
            y += grid

        # drifting particles (motes of drifting matter)
        drift = t * 6.0
        for (px, py, pr) in self.particles:
            wx = px + math.sin(drift * 0.1 + px) * 6
            wy = py + drift * 0.2
            wy = wy % C.WORLD_H
            if not cam.is_visible((wx, wy), 4):
                continue
            sx, sy = cam.world_to_screen((wx, wy))
            pygame.draw.circle(surface, (40, 70, 96), (int(sx), int(sy)),
                               max(1, int(pr * cam.zoom)))

        # world border
        tl = cam.world_to_screen((0, 0))
        br = cam.world_to_screen((C.WORLD_W, C.WORLD_H))
        pygame.draw.rect(surface, (60, 100, 140),
                         pygame.Rect(tl[0], tl[1], br[0] - tl[0], br[1] - tl[1]),
                         max(1, int(2 * cam.zoom)))

    # --------------------------------------------------------------- widgets
    def _bar(self, surface, x, y, w, h, frac, color, label=None):
        frac = max(0.0, min(1.0, frac))
        pygame.draw.rect(surface, (30, 30, 40), (x, y, w, h), border_radius=3)
        pygame.draw.rect(surface, color, (x, y, int(w * frac), h), border_radius=3)
        pygame.draw.rect(surface, C.C_PANEL_LINE, (x, y, w, h), 1, border_radius=3)
        if label:
            txt = self.font_s.render(label, True, C.C_TEXT)
            surface.blit(txt, (x + 6, y + (h - txt.get_height()) // 2))

    def _text(self, surface, text, x, y, font=None, color=C.C_TEXT):
        font = font or self.font_s
        surface.blit(font.render(text, True, color), (x, y))

    # ------------------------------------------------------------ overhead
    def draw_overhead(self, surface, world, cam):
        for cell in world.cells:
            if not cell.alive:
                continue
            if not cam.is_visible(cell.pos, cell.radius + 60):
                continue
            sx, sy = cam.world_to_screen(cell.pos)
            r = cell.radius * cam.zoom

            # health bar with hp/total below it - for everyone, player too
            w = max(28, r * 2)
            x = sx - w / 2
            bar_y = sy - r - 26
            pygame.draw.rect(surface, (20, 20, 28), (x, bar_y, w, 5))
            frac = max(0.0, cell.health / cell.max_health)
            pygame.draw.rect(surface, C.C_HEALTH, (x, bar_y, int(w * frac), 5))
            pygame.draw.rect(surface, (60, 60, 72), (x, bar_y, w, 5), 1)
            hp = self.font_s.render(
                f"{int(cell.health)}/{int(cell.max_health)}", True, C.C_TEXT)
            surface.blit(hp, (sx - hp.get_width() / 2, bar_y + 6))

            # name + diet tag above the bar; * = multicellular, FISH = fish
            tag = {"herbivore": "H", "carnivore": "C", "omnivore": "O",
                   "none": "-"}[cell.diet]
            badge = {"cell": "", "multi": "*", "fish": " FISH"}[cell.stage]
            name = "You" if cell.is_player else cell.name
            label = self.font_s.render(f"{name} {tag}{cell.growth_level}{badge}",
                                       True, cell.color)
            surface.blit(label, (sx - label.get_width() / 2, bar_y - 18))

            # speech bubble: the organism's own words, straight from the LLM
            if cell.speech_t > 0 and cell.speech:
                txt = self.font_s.render(cell.speech, True, C.C_TEXT)
                bw, bh = txt.get_width() + 14, txt.get_height() + 8
                bx = sx - bw / 2
                by = bar_y - 26 - bh
                fade = min(1.0, cell.speech_t / 0.6)
                bubble = pygame.Surface((bw, bh), pygame.SRCALPHA)
                bubble.fill((14, 26, 44, int(215 * fade)))
                surface.blit(bubble, (bx, by))
                pygame.draw.rect(surface, cell.color, (bx, by, bw, bh), 1,
                                 border_radius=4)
                txt.set_alpha(int(255 * fade))
                surface.blit(txt, (bx + 7, by + 4))
                pygame.draw.polygon(surface, cell.color, [
                    (sx - 4, by + bh), (sx + 4, by + bh), (sx, by + bh + 6)])

    # ------------------------------------------------------- threat arrows
    def draw_threat_arrows(self, surface, world, cam, t):
        """Edge-of-screen arrows toward off-screen predators closing in."""
        p = world.player
        if not p.alive:
            return
        W, H = surface.get_size()
        margin = 34
        for cell in world.cells:
            if cell is p or not cell.alive:
                continue
            armed = cell.can_bite_cells or cell.n_spike or cell.n_sting
            if not (cell.is_epic
                    or (cell.radius >= p.radius * 1.15 and armed)):
                continue
            dist = (cell.pos - p.pos).length()
            if dist > (2400 if cell.is_epic else 1100):
                continue
            sx, sy = cam.world_to_screen(cell.pos)
            if -40 <= sx <= W + 40 and -40 <= sy <= H + 40:
                continue  # already visible - no arrow needed
            # clamp the arrow to the screen edge along the threat direction
            dx, dy = sx - W / 2, sy - H / 2
            scale = min((W / 2 - margin) / abs(dx) if dx else 9e9,
                        (H / 2 - margin) / abs(dy) if dy else 9e9)
            ax, ay = W / 2 + dx * scale, H / 2 + dy * scale
            ang = math.atan2(dy, dx)
            pulse = 0.6 + 0.4 * math.sin(t * (9 if cell.is_epic else 6))
            color = C.C_EPIC if cell.is_epic else C.C_BAD
            color = tuple(min(255, int(c * (0.7 + 0.6 * pulse))) for c in color)
            size = (22 if cell.is_epic else 15) * (0.85 + 0.15 * pulse)
            tip = (ax + math.cos(ang) * size * 0.6,
                   ay + math.sin(ang) * size * 0.6)
            b1 = (ax + math.cos(ang + 2.55) * size,
                  ay + math.sin(ang + 2.55) * size)
            b2 = (ax + math.cos(ang - 2.55) * size,
                  ay + math.sin(ang - 2.55) * size)
            pygame.draw.polygon(surface, color, [tip, b1, b2])

        # the worst news in the pond, delivered plainly
        epic = world.epic
        if (epic is not None and epic.alive
                and getattr(epic.brain, "_target", None) is p
                and int(t * 2) % 2 == 0):
            warn = self.font_m.render("THE LEVIATHAN HUNTS YOU", True,
                                      (235, 140, 255))
            wx = (W - warn.get_width()) // 2
            pygame.draw.rect(surface, (30, 8, 40),
                             (wx - 12, 66, warn.get_width() + 24, 30),
                             border_radius=6)
            surface.blit(warn, (wx, 70))

    # ----------------------------------------------------------------- HUD
    def draw(self, surface, world, cam, fps, manager, t, autopilot=False):
        p = world.player
        W, H = surface.get_size()

        # ---- left stat panel ----
        panel = pygame.Surface((250, 196), pygame.SRCALPHA)
        panel.fill((10, 22, 38, 210))
        surface.blit(panel, (12, 12))
        pygame.draw.rect(surface, C.C_PANEL_LINE, (12, 12, 250, 196), 1, border_radius=5)

        x, y = 22, 22
        self._bar(surface, x, y, 230, 20, p.health / p.max_health, C.C_HEALTH,
                  f"HP {int(p.health)}/{int(p.max_health)}")
        y += 26
        self._bar(surface, x, y, 230, 20, p.energy / p.max_energy, C.C_ENERGY,
                  f"Energy {int(p.energy)}")
        y += 26
        grow_cost = p.grow_cost()
        self._bar(surface, x, y, 230, 20, min(1.0, p.dna / grow_cost), C.C_DNA,
                  f"DNA {int(p.dna)} / grow {int(grow_cost)}")
        y += 26
        if p.stage == "fish":
            # fish level forever; the bar tracks DNA toward the next level
            frac = min(1.0, p.dna / p.grow_cost())
            self._bar(surface, x, y, 230, 16, frac, C.C_MULTI,
                      f"FISH  Lv {p.growth_level}  (+{int(p.damage_mult * 100 - 100)}% dmg)")
        else:
            stage_name = "Multicellular" if p.stage == "multi" else "Cell stage"
            self._bar(surface, x, y, 230, 16, p.growth_level / C.STAGE_MAX_LEVEL,
                      C.C_MULTI, f"{stage_name} {p.growth_level}/{C.STAGE_MAX_LEVEL}")
        y += 24
        segs = f"   Segs: {p.n_segments()}" if p.stage != "cell" else ""
        self._text(surface, f"Diet: {p.diet}   Size: {int(p.radius)}{segs}   Gen: {p.generation}",
                   x, y, self.font_s, C.C_TEXT)
        y += 18
        parts = ", ".join(f"{v}x {k}" for k, v in p.part_counts().items()) or "none"
        self._text(surface, f"Parts: {parts}", x, y, self.font_s, C.C_TEXT_DIM)

        # ---- evolve / advance prompts ----
        if p.can_advance_stage() or p.can_evolve_brain():
            label = ("Press M to become MULTICELLULAR" if p.can_advance_stage()
                     else "Press M to evolve a BRAIN (become a fish)")
            prompt = self.font_m.render(label, True, C.C_MULTI)
            pygame.draw.rect(surface, (14, 40, 30),
                             (12, 214, prompt.get_width() + 20, 30), border_radius=5)
            surface.blit(prompt, (22, 219))
        elif p.dna >= 9:
            prompt = self.font_m.render("Press E to evolve", True, C.C_MULTI)
            pygame.draw.rect(surface, (10, 30, 24),
                             (12, 214, prompt.get_width() + 20, 30), border_radius=5)
            surface.blit(prompt, (22, 219))

        # ---- minimap ----
        self._draw_minimap(surface, world, cam, W)

        # ---- event log ----
        ey = H - 26
        for text, ttl, color in reversed(world.events):
            alpha = max(60, min(255, int(255 * ttl / 3)))
            surf = self.font_s.render(text, True, color)
            surf.set_alpha(alpha)
            surface.blit(surf, (16, ey - surf.get_height()))
            ey -= surf.get_height() + 3

        # ---- top-right status ----
        rivals = sum(1 for c in world.cells if not c.is_player and c.alive)
        llm = "LLM off"
        lcol = C.C_TEXT_DIM
        if manager.enabled:
            if manager.throttled():
                llm = f"LLM throttled (429) ok:{manager.stats['ok']}"
                lcol = C.C_ENERGY
            else:
                llm = f"LLM ok:{manager.stats['ok']} fail:{manager.stats['fail']}"
                lcol = (C.C_GOOD if manager.stats["ok"] >= manager.stats["fail"]
                        else C.C_BAD)
        status = f"Rivals: {rivals}   FPS: {int(fps)}"
        self._text(surface, status, W - 250, H - 44, self.font_s, C.C_TEXT_DIM)
        self._text(surface, llm, W - 250, H - 26, self.font_s, lcol)

        # controls hint (top center)
        hint = ("WASD/Arrows swim   Space dash   Ram to attack   E evolve   "
                "M advance   P autopilot   Tab overlay   Esc pause")
        hsurf = self.font_s.render(hint, True, C.C_TEXT_DIM)
        surface.blit(hsurf, ((W - hsurf.get_width()) // 2, 10))

        # autopilot badge, front and center so it's obvious who's driving
        if autopilot:
            mode = "LLM" if manager.enabled else "heuristics"
            badge = self.font_m.render(f"AUTOPILOT ({mode})  -  P to take control",
                                       True, C.C_MULTI)
            bx = (W - badge.get_width()) // 2
            pygame.draw.rect(surface, (12, 36, 28),
                             (bx - 10, 30, badge.get_width() + 20, 28),
                             border_radius=6)
            surface.blit(badge, (bx, 34))

    def _draw_minimap(self, surface, world, cam, W):
        mw, mh = 200, int(200 * C.WORLD_H / C.WORLD_W)
        mx, my = W - mw - 12, 12
        panel = pygame.Surface((mw, mh), pygame.SRCALPHA)
        panel.fill((8, 18, 32, 200))
        surface.blit(panel, (mx, my))
        pygame.draw.rect(surface, C.C_PANEL_LINE, (mx, my, mw, mh), 1)

        def to_map(pos):
            return (mx + pos[0] / C.WORLD_W * mw, my + pos[1] / C.WORLD_H * mh)

        for m in world.meteors:
            if m.alive:
                pygame.draw.circle(surface, C.C_METEOR, to_map(m.pos), 2)
        for cell in world.cells:
            if not cell.alive:
                continue
            if cell.is_epic:
                # the Leviathan pulses on the minimap - you want to know
                mp = to_map(cell.pos)
                pygame.draw.circle(surface, C.C_EPIC, mp, 7)
                pygame.draw.circle(surface, (230, 190, 255), mp, 7, 1)
                continue
            col = C.C_PLAYER if cell.is_player else cell.color
            rad = 4 if cell.is_player else max(2, int(cell.radius / 12))
            pygame.draw.circle(surface, col, to_map(cell.pos), rad)
        # viewport box
        z = cam.zoom
        vw = surface.get_width() / z / C.WORLD_W * mw
        vh = surface.get_height() / z / C.WORLD_H * mh
        vx = mx + (cam.center.x / C.WORLD_W * mw) - vw / 2
        vy = my + (cam.center.y / C.WORLD_H * mh) - vh / 2
        pygame.draw.rect(surface, (200, 220, 240), (vx, vy, vw, vh), 1)
