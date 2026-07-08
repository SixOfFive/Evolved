"""The evolution editor (a.k.a. the Cell Creator).

A modal overlay reached by reproducing (calling a mate). The player spends
banked DNA to bolt on organelles, remove them for a refund, and trigger a
growth spurt that enlarges the cell, adds part slots and unlocks new parts.
"""

import pygame

from . import config as C
from . import parts as P


class _PreviewCam:
    """A tiny camera shim so Cell.draw can render into the preview panel."""
    def __init__(self, center_screen, world_pos, zoom):
        self.center_screen = center_screen
        self.world_pos = world_pos
        self.zoom = zoom

    def world_to_screen(self, pos):
        return (self.center_screen[0] + (pos[0] - self.world_pos[0]) * self.zoom,
                self.center_screen[1] + (pos[1] - self.world_pos[1]) * self.zoom)

    def is_visible(self, pos, radius):
        return True


class Editor:
    def __init__(self, hud):
        self.hud = hud
        self.buttons = []       # (rect, part_id)
        self.chip_rects = []    # (rect, part_index)
        self.grow_rect = None
        self.done_rect = None
        self._size = None
        self.message = ""

    def open(self, surface, player):
        self.message = ""
        self.layout(surface.get_size())

    def layout(self, size):
        self._size = size
        W, H = size
        self.buttons = []
        # right-hand part grid
        col_x = int(W * 0.52)
        grid_w = W - col_x - 40
        bw = (grid_w - 20) // 2
        bh = 62
        x0 = col_x
        y0 = 120
        for i, pid in enumerate(P.PART_ORDER):
            r = i // 2
            c = i % 2
            rect = pygame.Rect(x0 + c * (bw + 20), y0 + r * (bh + 12), bw, bh)
            self.buttons.append((rect, pid))
        # grow + done buttons along the bottom
        self.grow_rect = pygame.Rect(x0, H - 120, grid_w // 2 - 10, 54)
        self.done_rect = pygame.Rect(x0 + grid_w // 2 + 10, H - 120,
                                     grid_w // 2 - 10, 54)

    # ------------------------------------------------------------- events
    def handle_event(self, event, player):
        if self._size is None:
            return None
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_e, pygame.K_SPACE, pygame.K_ESCAPE):
                return "close"
            # hotkeys 1..9 map to parts by their `key`
            for rect, pid in self.buttons:
                if event.unicode and event.unicode == P.PART_DEFS[pid].key:
                    self._try_add(player, pid)
                    return None
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mp = event.pos
            if self.grow_rect and self.grow_rect.collidepoint(mp):
                if not player.grow():
                    self.message = "Not enough DNA to grow."
                else:
                    self.layout(self._size)
                    self.message = f"Grew to evolution level {player.growth_level}!"
                return None
            if self.done_rect and self.done_rect.collidepoint(mp):
                return "close"
            for rect, pid in self.buttons:
                if rect.collidepoint(mp):
                    self._try_add(player, pid)
                    return None
            for rect, idx in self.chip_rects:
                if rect.collidepoint(mp):
                    player.remove_part(idx)
                    self.message = "Removed a part (DNA refunded)."
                    return None
        return None

    def _try_add(self, player, pid):
        pdef = P.PART_DEFS[pid]
        if pid not in player.available_parts():
            self.message = f"{pdef.name} unlocks at evolution level {pdef.unlock_level}."
            return
        if player.slots_used() >= player.max_slots:
            self.message = "No free part slots - grow to get more."
            return
        if player.dna < pdef.cost:
            self.message = f"Need {int(pdef.cost)} DNA for {pdef.name}."
            return
        if player.add_part(pid):
            self.message = f"Added {pdef.name}."

    # -------------------------------------------------------------- drawing
    def draw(self, surface, player, t):
        if self._size != surface.get_size():
            self.layout(surface.get_size())
        W, H = surface.get_size()
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((4, 10, 20, 235))
        surface.blit(overlay, (0, 0))

        hud = self.hud
        # title
        surface.blit(hud.font_l.render("EVOLUTION EDITOR", True, C.C_MULTI), (40, 30))
        surface.blit(hud.font_m.render(
            f"DNA: {int(player.dna)}    Slots: {player.slots_used()}/{player.max_slots}"
            f"    Level: {player.growth_level}/{C.MULTICELLULAR_LEVEL}    Diet: {player.diet}",
            True, C.C_TEXT), (40, 76))

        # preview panel (left)
        pv_rect = pygame.Rect(40, 120, int(W * 0.52) - 80, H - 260)
        pygame.draw.rect(surface, (10, 22, 38), pv_rect, border_radius=8)
        pygame.draw.rect(surface, C.C_PANEL_LINE, pv_rect, 1, border_radius=8)
        zoom = min(pv_rect.w, pv_rect.h) * 0.32 / max(player.radius, 8)
        cam = _PreviewCam(pv_rect.center, player.pos, zoom)
        player.draw(surface, cam, t)

        # attached-part chips (removable) along the bottom of the preview
        self.chip_rects = []
        cx, cy = pv_rect.left + 12, pv_rect.bottom - 34
        for idx, ap in enumerate(player.parts):
            name = P.PART_DEFS[ap.id].name
            label = hud.font_s.render(f"x {name}", True, C.C_TEXT)
            cw = label.get_width() + 14
            if cx + cw > pv_rect.right - 12:
                cx = pv_rect.left + 12
                cy -= 26
            chip = pygame.Rect(cx, cy, cw, 22)
            pygame.draw.rect(surface, (40, 30, 40), chip, border_radius=4)
            pygame.draw.rect(surface, (120, 80, 80), chip, 1, border_radius=4)
            surface.blit(label, (cx + 7, cy + 3))
            self.chip_rects.append((chip, idx))
            cx += cw + 8
        surface.blit(hud.font_s.render("(click a part to remove & refund)", True,
                                       C.C_TEXT_DIM), (pv_rect.left + 12, pv_rect.bottom - 60))

        # part buttons (right)
        mouse = pygame.mouse.get_pos()
        for rect, pid in self.buttons:
            pdef = P.PART_DEFS[pid]
            locked = pid not in player.available_parts()
            afford = player.dna >= pdef.cost
            full = player.slots_used() >= player.max_slots
            if locked:
                bg, fg = (24, 26, 34), C.C_TEXT_DIM
            elif not afford or full:
                bg, fg = (26, 34, 44), C.C_TEXT_DIM
            else:
                bg, fg = (24, 44, 60), C.C_TEXT
            if rect.collidepoint(mouse) and not locked:
                bg = (34, 60, 82)
            pygame.draw.rect(surface, bg, rect, border_radius=6)
            pygame.draw.rect(surface, C.C_PANEL_LINE, rect, 1, border_radius=6)
            surface.blit(hud.font_m.render(pdef.name, True, fg), (rect.x + 10, rect.y + 6))
            if locked:
                info = f"[{pdef.key}] locked - level {pdef.unlock_level}"
            else:
                info = f"[{pdef.key}] {int(pdef.cost)} DNA - {pdef.category}"
            surface.blit(hud.font_s.render(info, True, fg), (rect.x + 10, rect.y + 32))

        # grow button
        can_grow = player.can_grow()
        gcol = (26, 60, 46) if can_grow else (30, 30, 38)
        if self.grow_rect.collidepoint(mouse) and can_grow:
            gcol = (36, 84, 62)
        pygame.draw.rect(surface, gcol, self.grow_rect, border_radius=6)
        pygame.draw.rect(surface, C.C_PANEL_LINE, self.grow_rect, 1, border_radius=6)
        gtxt = (f"GROW  ({int(player.grow_cost())} DNA)" if player.growth_level < C.MULTICELLULAR_LEVEL
                else "MULTICELLULAR!")
        gs = hud.font_m.render(gtxt, True, C.C_MULTI if can_grow else C.C_TEXT_DIM)
        surface.blit(gs, (self.grow_rect.centerx - gs.get_width() // 2,
                          self.grow_rect.centery - gs.get_height() // 2))

        # done button
        dcol = (44, 40, 30)
        if self.done_rect.collidepoint(mouse):
            dcol = (70, 62, 40)
        pygame.draw.rect(surface, dcol, self.done_rect, border_radius=6)
        pygame.draw.rect(surface, C.C_PANEL_LINE, self.done_rect, 1, border_radius=6)
        ds = hud.font_m.render("DONE  (E / Space)", True, C.C_TEXT)
        surface.blit(ds, (self.done_rect.centerx - ds.get_width() // 2,
                          self.done_rect.centery - ds.get_height() // 2))

        # message line
        if self.message:
            surface.blit(hud.font_m.render(self.message, True, C.C_ENERGY),
                         (40, H - 150))
