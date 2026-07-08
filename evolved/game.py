"""Game shell: window, states, main loop, and the screenshot/demo harness."""

import math
import os

import pygame

from . import config as C
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
STATE_WIN = "win"


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
        self.world = World(self.manager, ai_count=args.ai_cells, demo=self.demo)
        self.controller = PlayerController(self.world.player)
        self.camera.snap(self.world.player.pos, self.world.player.radius)

        self.state = STATE_PLAYING
        self.mate = None
        self.show_overlay = True
        self.running = True

    # ------------------------------------------------------------- lifecycle
    def _new_world(self):
        self.world = World(self.manager, ai_count=self.args.ai_cells, demo=self.demo)
        self.controller = PlayerController(self.world.player)
        self.camera.snap(self.world.player.pos, self.world.player.radius)
        self.mate = None
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
        elif event.key == pygame.K_r and self.state in (STATE_GAMEOVER, STATE_WIN):
            self._new_world()
        elif event.key == pygame.K_e and self.state == STATE_PLAYING:
            if self.mate is not None:
                self._open_editor()          # shortcut: skip the swim
            elif self.world.player.alive:
                self._call_mate()

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
        self.state = STATE_WIN if p.multicellular else STATE_PLAYING

    # -------------------------------------------------------------- update
    def _update(self, dt):
        if self.state == STATE_PLAYING:
            self._update_playing(dt)
        # editor / paused / gameover / win: world is frozen

    def _update_playing(self, dt):
        if not self.demo:
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
        elif self.world.player.multicellular:
            self.state = STATE_WIN

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
                      self.manager, self.t)

        if self.state == STATE_EDITOR:
            self.editor.draw(s, self.world.player, self.t)
        elif self.state == STATE_PAUSED:
            self._overlay_text(s, "PAUSED", "Esc to resume")
        elif self.state == STATE_GAMEOVER:
            p = self.world.player
            self._overlay_text(
                s, "You were consumed",
                f"Survived {int(p.time_alive)}s  -  reached level {p.growth_level}  -  "
                f"{p.food_eaten} eaten.   R: try again   Esc: quit",
                color=C.C_BAD)
        elif self.state == STATE_WIN:
            self._overlay_text(
                s, "MULTICELLULAR!",
                "Your cell evolved into a multicellular organism.   "
                "R: keep playing   Esc: quit",
                color=C.C_MULTI)

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
            from .ai import AIBrain
            self.world.player.brain = AIBrain(self.world.player, self.world, self.manager)
            self.demo = True
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
