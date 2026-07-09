"""Game shell: window, states, main loop, and the screenshot/demo harness."""

import math
import os

import pygame

from . import config as C
from .ai import AIBrain
from .camera import Camera
from .world import World
from .player import PlayerController
from .hud import HUD
from .editor import Editor
from .llm import OllamaClient, LLMManager

STATE_PLAYING = "playing"
STATE_EDITOR = "editor"
STATE_PAUSED = "paused"
STATE_GAMEOVER = "gameover"
STATE_PROMPT = "prompt"   # modal yes/no question (stage advancement)


class Mate:
    """A friendly cell you swim into to open the editor (reproduction)."""
    def __init__(self, pos, color):
        self.pos = pygame.Vector2(pos)
        self.color = color
        self.radius = 12.0
        self.timer = 0.0

    def update(self, dt, target):
        d = pygame.Vector2(target) - self.pos
        if d.length_squared() > 1:
            self.pos += d.normalize() * 240 * dt
        self.timer += dt

    def draw(self, surface, cam, t):
        sx, sy = cam.world_to_screen(self.pos)
        r = self.radius * cam.zoom
        pygame.draw.circle(surface, self.color, (sx, sy), max(2, int(r)))
        pygame.draw.circle(surface, (255, 255, 255), (sx, sy), max(2, int(r)), 1)
        # pulsing heart above
        pr = 5 + 1.5 * math.sin(t * 6)
        hy = sy - r - 14
        pink = (255, 120, 160)
        pygame.draw.circle(surface, pink, (int(sx - pr * 0.5), int(hy)), int(pr))
        pygame.draw.circle(surface, pink, (int(sx + pr * 0.5), int(hy)), int(pr))
        pygame.draw.polygon(surface, pink, [
            (sx - pr, hy + pr * 0.3), (sx + pr, hy + pr * 0.3), (sx, hy + pr * 1.6)])


class Game:
    def __init__(self, args):
        self.args = args
        pygame.init()
        pygame.display.set_caption(C.TITLE)
        flags = pygame.RESIZABLE
        if getattr(args, "headless", False):
            self.screen = pygame.Surface((C.SCREEN_W, C.SCREEN_H))
        else:
            self.screen = pygame.display.set_mode((C.SCREEN_W, C.SCREEN_H), flags)
        self.clock = pygame.time.Clock()
        self.t = 0.0

        client = OllamaClient(args.ollama_host, args.ollama_port, args.model)
        enabled = (not args.no_llm)
        if enabled:
            ok = client.available()
            if not ok:
                print(f"[Evolved] Ollama at {client.base} not reachable - "
                      f"rivals will use heuristics only.")
                enabled = False
            else:
                print(f"[Evolved] Ollama connected: {client.base} model={args.model}")
        self.manager = LLMManager(client, enabled=enabled)
        self.manager.start()

        self.hud = HUD(C.SCREEN_W, C.SCREEN_H)
        self.editor = Editor(self.hud)
        self.camera = Camera(*self.screen.get_size())
        self.demo = getattr(args, "demo", False)
        # autopilot: the AI plays the player cell (LLM if connected, else
        # heuristics). --demo starts with it on; P toggles it any time.
        self.autopilot = self.demo
        self.world = World(self.manager, ai_count=args.ai_cells, demo=self.demo)
        self.controller = PlayerController(self.world.player)
        self.camera.snap(self.world.player.pos, self.world.player.radius)

        self.state = STATE_PLAYING
        self.mate = None
        self.show_overlay = True
        self.running = True
        # stage-advancement prompt bookkeeping
        self.prompt = None            # {"title","sub","yes","no"} when asking
        self._prompt_rects = None     # (yes_rect, no_rect) from the last draw
        self.advance_declined = False # player said "not yet" to multicellular
        self.brain_declined = False   # player said "not yet" to the brain

    # ------------------------------------------------------------- lifecycle
    def _new_world(self):
        # drop queued LLM work from the previous population so the new
        # rivals' spawn decisions aren't stuck behind stale requests
        self.manager.clear_pending()
        self.world = World(self.manager, ai_count=self.args.ai_cells,
                           demo=self.autopilot)
        self.controller = PlayerController(self.world.player)
        self.camera.snap(self.world.player.pos, self.world.player.radius)
        self.mate = None
        self.prompt = None
        self.advance_declined = False
        self.brain_declined = False
        self.state = STATE_PLAYING

    def run(self):
        autoquit = float(getattr(self.args, "autoquit", 0) or 0)
        while self.running:
            dt = self.clock.tick(C.FPS) / 1000.0
            dt = min(dt, 0.05)  # clamp big hitches
            self.t += dt
            self._handle_events()
            self._update(dt)
            self._draw()
            if not getattr(self.args, "headless", False):
                pygame.display.flip()
            if autoquit and self.t >= autoquit:
                self.running = False
        if os.environ.get("EVOLVED_DEBUG"):
            p = self.world.player
            print(f"[Evolved] LLM stats: {self.manager.stats}")
            print(f"[Evolved] player level={p.growth_level} energy={int(p.energy)} "
                  f"alive={p.alive} rivals={sum(1 for c in self.world.cells if not c.is_player and c.alive)}")
        self.manager.stop()
        pygame.quit()

    # -------------------------------------------------------------- events
    def _handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.VIDEORESIZE:
                self.screen = pygame.display.set_mode((event.w, event.h),
                                                      pygame.RESIZABLE)
                self.camera.resize(event.w, event.h)
            elif self.state == STATE_EDITOR:
                result = self.editor.handle_event(event, self.world.player)
                if result == "close":
                    self._close_editor()
            elif self.state == STATE_PROMPT:
                self._handle_prompt_event(event)
            elif event.type == pygame.KEYDOWN:
                self._on_keydown(event)

    def _on_keydown(self, event):
        if event.key == pygame.K_ESCAPE:
            if self.state == STATE_PLAYING:
                self.state = STATE_PAUSED
            elif self.state == STATE_PAUSED:
                self.state = STATE_PLAYING
            else:
                self.running = False
        elif event.key == pygame.K_TAB:
            self.show_overlay = not self.show_overlay
        elif event.key == pygame.K_r and self.state == STATE_GAMEOVER:
            self._new_world()
        elif event.key == pygame.K_e and self.state == STATE_PLAYING:
            if self.mate is not None:
                self._open_editor()          # shortcut: skip the swim
            elif self.world.player.alive:
                self._call_mate()
        elif event.key == pygame.K_m and self.state == STATE_PLAYING:
            # re-offer stage advancement any time the player is eligible
            p = self.world.player
            if p.can_advance_stage():
                self._offer_advance()
            elif p.can_evolve_brain():
                self._offer_brain()
        elif event.key == pygame.K_p and self.state == STATE_PLAYING:
            self._toggle_autopilot()

    def _toggle_autopilot(self):
        p = self.world.player
        self.autopilot = not self.autopilot
        if self.autopilot:
            # keep playing the build the player was going for
            diet = p.diet if p.diet != "none" else None
            p.brain = AIBrain(p, self.world, self.manager, intended_diet=diet)
            mode = "LLM" if self.manager.enabled else "heuristics"
            self.world.log(f"Autopilot engaged ({mode}) - press P to take "
                           "back control.", C.C_MULTI)
        else:
            p.brain = None
            p.thrust = pygame.Vector2(0, 0)
            self.world.log("Autopilot off - you have control.", C.C_TEXT_DIM)

    def _call_mate(self):
        p = self.world.player
        ang = p.angle + math.pi
        spawn = p.pos + pygame.Vector2(math.cos(ang), math.sin(ang)) * 240
        spawn.x = max(20, min(C.WORLD_W - 20, spawn.x))
        spawn.y = max(20, min(C.WORLD_H - 20, spawn.y))
        self.mate = Mate(spawn, p.color)
        self.world.log("You called a mate - swim into it to reproduce & evolve.",
                       (255, 150, 180))

    def _open_editor(self):
        self.mate = None
        self.state = STATE_EDITOR
        self.editor.open(self.screen, self.world.player)

    def _close_editor(self):
        p = self.world.player
        p.generation += 1
        p.energy = p.max_energy
        p.health = min(p.max_health, p.health + p.max_health * 0.25)
        # a full stage bar puts the next stage on offer (once, unless declined)
        if p.can_advance_stage() and not self.advance_declined:
            self._offer_advance()
        elif p.can_evolve_brain() and not self.brain_declined:
            self._offer_brain()
        else:
            self.state = STATE_PLAYING

    # -------------------------------------------------- stage advancement
    def _offer_advance(self):
        self.prompt = {
            "title": "BECOME MULTICELLULAR?",
            "sub": ("Your cell has filled its evolution bar. Advance to the "
                    "multicellular stage - body segments, muscles, stingers, "
                    "armor and more? You can stay single-celled and keep "
                    "playing if you prefer (press M later to advance)."),
            "yes": self._do_advance,
            "no": self._decline_advance,
        }
        self.state = STATE_PROMPT

    def _do_advance(self):
        p = self.world.player
        p.advance_stage()
        self.world.log("You are now a MULTICELLULAR organism! New parts await "
                       "in the editor.", C.C_MULTI)
        self.prompt = None
        self.state = STATE_PLAYING

    def _decline_advance(self):
        self.advance_declined = True
        self.world.log("You remain single-celled. Press M when ready to "
                       "advance.", C.C_TEXT_DIM)
        self.prompt = None
        self.state = STATE_PLAYING

    def _offer_brain(self):
        self.prompt = {
            "title": "EVOLVE A BRAIN?",
            "sub": ("Your organism has mastered the multicellular stage. "
                    "Growing a brain makes you a FISH - the apex of the pond. "
                    "Fish never stop evolving: every level of DNA makes you "
                    "larger and stronger, forever. You can also stay as you "
                    "are and say yes later (press M)."),
            "yes": self._do_brain,
            "no": self._decline_brain,
        }
        self.state = STATE_PROMPT

    def _do_brain(self):
        p = self.world.player
        p.become_fish()
        self.world.log("You grew a brain - you are now a FISH! Keep eating "
                       "and growing; the pond is yours to rule.", C.C_MULTI)
        self.prompt = None
        self.state = STATE_PLAYING

    def _decline_brain(self):
        self.brain_declined = True
        self.world.log("You keep swimming. Press M when ready to evolve a "
                       "brain.", C.C_TEXT_DIM)
        self.prompt = None
        self.state = STATE_PLAYING

    def _handle_prompt_event(self, event):
        if self.prompt is None:
            self.state = STATE_PLAYING
            return
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_y, pygame.K_RETURN):
                self.prompt["yes"]()
            elif event.key in (pygame.K_n, pygame.K_ESCAPE):
                self.prompt["no"]()
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._prompt_rects:
                yes_rect, no_rect = self._prompt_rects
                if yes_rect.collidepoint(event.pos):
                    self.prompt["yes"]()
                elif no_rect.collidepoint(event.pos):
                    self.prompt["no"]()

    # -------------------------------------------------------------- update
    def _update(self, dt):
        if self.state == STATE_PLAYING:
            self._update_playing(dt)
        elif self.state == STATE_PROMPT and self.autopilot and self.prompt:
            # the AI is playing: nobody will press Y, so it always says yes
            self.prompt["yes"]()
        # editor / paused / gameover: world is frozen

    def _update_playing(self, dt):
        if not self.autopilot:
            keys = pygame.key.get_pressed()
            self.controller.update(keys)
        self.world.update(dt, self.t)
        self.camera.follow(self.world.player.pos, self.world.player.radius, dt)

        if self.mate is not None:
            self.mate.update(dt, self.world.player.pos)
            if ((self.mate.pos - self.world.player.pos).length()
                    < self.world.player.radius + self.mate.radius + 4
                    or self.mate.timer > 9.0):
                self._open_editor()

        if self.world.player_dead:
            self.state = STATE_GAMEOVER
        elif self.mate is None:
            # first time a stage bar fills mid-swim, offer what comes next
            p = self.world.player
            if p.can_advance_stage() and not self.advance_declined:
                self._offer_advance()
            elif p.can_evolve_brain() and not self.brain_declined:
                self._offer_brain()

    # ---------------------------------------------------------------- draw
    def _draw(self):
        s = self.screen
        self.hud.draw_background(s, self.camera, self.t)
        self.world.draw_entities(s, self.camera, self.t)
        if self.mate is not None:
            self.mate.draw(s, self.camera, self.t)
        if self.show_overlay:
            self.hud.draw_overhead(s, self.world, self.camera)
        self.hud.draw(s, self.world, self.camera, self.clock.get_fps(),
                      self.manager, self.t, autopilot=self.autopilot)

        if self.state == STATE_EDITOR:
            self.editor.draw(s, self.world.player, self.t)
        elif self.state == STATE_PROMPT and self.prompt is not None:
            self._draw_prompt(s)
        elif self.state == STATE_PAUSED:
            self._overlay_text(s, "PAUSED", "Esc to resume")
        elif self.state == STATE_GAMEOVER:
            p = self.world.player
            stage = {"cell": "cell", "multi": "multicellular",
                     "fish": "fish"}[p.stage]
            self._overlay_text(
                s, "You were consumed",
                f"Survived {int(p.time_alive)}s as a {stage} (level {p.growth_level})"
                f"  -  {p.food_eaten} eaten.   R: try again   Esc: quit",
                color=C.C_BAD)

    def _draw_prompt(self, surface):
        W, H = surface.get_size()
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 170))
        surface.blit(overlay, (0, 0))

        pw, ph = min(760, W - 80), 260
        px, py = (W - pw) // 2, (H - ph) // 2
        pygame.draw.rect(surface, C.C_PANEL, (px, py, pw, ph), border_radius=10)
        pygame.draw.rect(surface, C.C_MULTI, (px, py, pw, ph), 2, border_radius=10)

        title = self.hud.font_l.render(self.prompt["title"], True, C.C_MULTI)
        surface.blit(title, (px + (pw - title.get_width()) // 2, py + 22))

        # word-wrap the subtitle
        words = self.prompt["sub"].split()
        lines, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if self.hud.font_m.size(trial)[0] > pw - 60:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        if cur:
            lines.append(cur)
        ty = py + 74
        for line in lines[:5]:
            ls = self.hud.font_m.render(line, True, C.C_TEXT)
            surface.blit(ls, (px + 30, ty))
            ty += 26

        bw, bh = 190, 52
        gap = 40
        yes_rect = pygame.Rect(px + pw // 2 - bw - gap // 2, py + ph - bh - 22, bw, bh)
        no_rect = pygame.Rect(px + pw // 2 + gap // 2, py + ph - bh - 22, bw, bh)
        mouse = pygame.mouse.get_pos()
        for rect, label, col in ((yes_rect, "YES  (Y)", (26, 66, 48)),
                                 (no_rect, "NOT YET  (N)", (60, 40, 34))):
            bg = tuple(min(255, c + 18) for c in col) if rect.collidepoint(mouse) else col
            pygame.draw.rect(surface, bg, rect, border_radius=8)
            pygame.draw.rect(surface, C.C_PANEL_LINE, rect, 1, border_radius=8)
            ls = self.hud.font_m.render(label, True, C.C_TEXT)
            surface.blit(ls, (rect.centerx - ls.get_width() // 2,
                              rect.centery - ls.get_height() // 2))
        self._prompt_rects = (yes_rect, no_rect)

    def _overlay_text(self, surface, title, subtitle, color=C.C_TEXT):
        W, H = surface.get_size()
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 150))
        surface.blit(overlay, (0, 0))
        ts = self.hud.font_xl.render(title, True, color)
        surface.blit(ts, ((W - ts.get_width()) // 2, H // 2 - 70))
        ss = self.hud.font_m.render(subtitle, True, C.C_TEXT)
        surface.blit(ss, ((W - ss.get_width()) // 2, H // 2 + 6))

    # ------------------------------------------------------- screenshot mode
    def run_screenshot(self, path, frames=300):
        """Headless: simulate a while, then save a gameplay PNG (+ an editor PNG)."""
        dt = 1.0 / C.FPS
        # let the demo player drive so there is visible action
        if self.world.player.brain is None:
            self.world.player.brain = AIBrain(self.world.player, self.world,
                                              self.manager)
        self.demo = True
        self.autopilot = True
        for _ in range(frames):
            self.t += dt
            self.world.update(dt, self.t)
            self.camera.follow(self.world.player.pos, self.world.player.radius, dt)
            if self.world.player_dead:
                # respawn so the screenshot always shows a living cell
                self._new_world()
                if self.world.player.brain is None:
                    from .ai import AIBrain
                    self.world.player.brain = AIBrain(self.world.player, self.world,
                                                      self.manager)
        self._draw()
        pygame.image.save(self.screen, path)
        print(f"[Evolved] saved gameplay screenshot -> {path}")

        # a second shot of the editor, with some DNA to spend
        self.world.player.dna = max(self.world.player.dna, 80)
        self.state = STATE_EDITOR
        self.editor.open(self.screen, self.world.player)
        self._draw()
        editor_path = path.rsplit(".", 1)[0] + "_editor.png"
        pygame.image.save(self.screen, editor_path)
        print(f"[Evolved] saved editor screenshot -> {editor_path}")
        self.manager.stop()
        pygame.quit()
        return path, editor_path
