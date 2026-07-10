"""Game shell: window, states, main loop, and the screenshot/demo harness."""

import math
import os

import pygame

from . import config as C
from . import records
from .ai import AIBrain
from .camera import Camera
from .world import World
from .player import PlayerController
from .hud import HUD
from .editor import Editor
from .llm import OllamaClient, LLMManager
from .sound import SoundManager

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
        # low-latency mono mixer for the synthesized effects
        try:
            pygame.mixer.pre_init(22050, -16, 1, 512)
        except pygame.error:
            pass
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
        disabled_reason = ""
        if args.no_llm:
            disabled_reason = "--no-llm flag set"
        else:
            if not client.available():
                disabled_reason = f"Ollama unreachable at {client.base}"
                print(f"[Evolved] {disabled_reason} - rivals will use "
                      f"heuristics only.")
                enabled = False
            else:
                print(f"[Evolved] Ollama connected: {client.base} model={args.model}")
        self.manager = LLMManager(client, enabled=enabled,
                                  disabled_reason=disabled_reason)
        self.manager.start()
        # heuristics-mode notices for the feed
        self._llm_check_timer = 2.0
        self._llm_next_notice = 8.0
        self._llm_was_down = False

        self.hud = HUD(C.SCREEN_W, C.SCREEN_H)
        self.editor = Editor(self.hud)
        # (sound manager is created below and handed to the editor + world)
        self.camera = Camera(*self.screen.get_size())
        self.sound = SoundManager()
        self.sound.start_music()
        self.editor.sound = self.sound
        self._last_player_hp = None
        # the game opens with the AI driving (LLM if connected, heuristics
        # otherwise); press P to take control. P toggles it back any time.
        self.autopilot = True
        # U on the pause screen: the AI keeps managing upgrades (parts and
        # growth) while the human steers
        self.auto_evolve = False
        self.world = World(self.manager, ai_count=args.ai_cells, demo=True,
                           sound=self.sound)
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
        self._retry_timer = 2.0       # autopilot auto-retry countdown on death
        self._death_summary = None    # (run_stats, records, new_flags)

    # ------------------------------------------------------------- lifecycle
    def _new_world(self):
        # drop queued LLM work from the previous population so the new
        # rivals' spawn decisions aren't stuck behind stale requests
        self.manager.clear_pending()
        self.world = World(self.manager, ai_count=self.args.ai_cells,
                           demo=self.autopilot, sound=self.sound)
        self.controller = PlayerController(self.world.player)
        self._sync_player_brain()
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
        elif event.key == pygame.K_q and self.state == STATE_PAUSED:
            self.running = False
        elif event.key == pygame.K_l and self.state == STATE_PAUSED:
            if self.manager.enabled:
                self.manager.disable()
                self.world.log("[LLM] switched off - heuristics driving "
                               "(L on the pause screen to re-enable)",
                               C.C_ENERGY)
            else:
                self.manager.enable()
                self.world.log(f"[LLM] switched on - contacting "
                               f"{self.manager.client.base}...", C.C_LLM)
                self._llm_was_down = True   # so recovery gets announced
                self._llm_next_notice = self.t + 15.0
        elif event.key == pygame.K_u and self.state == STATE_PAUSED:
            self.auto_evolve = not self.auto_evolve
            self._sync_player_brain()
            if self.auto_evolve:
                self.world.log("AI now handles your upgrades while you drive "
                               "(U on pause to stop).", C.C_LLM)
            else:
                self.world.log("Upgrades back in your hands.", C.C_TEXT_DIM)
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
        elif (event.key == pygame.K_SPACE and self.state == STATE_PLAYING
                and not self.autopilot):
            p = self.world.player
            if p.dash():
                self.sound.play("dash")
                tail = p.seg_pos[-1] if p.seg_pos else p.pos
                self.world.fx.burst(tail, (170, 220, 235), n=7, speed=120,
                                    size=2.0, life=0.5)

    def _sync_player_brain(self):
        """Give the player cell the right brain for the current modes."""
        p = self.world.player
        if self.autopilot:
            return  # autopilot owns the brain
        if self.auto_evolve:
            diet = p.diet if p.diet != "none" else None
            p.brain = AIBrain(p, self.world, self.manager,
                              intended_diet=diet, evolve_only=True)
        else:
            p.brain = None

    def _toggle_autopilot(self):
        p = self.world.player
        self.autopilot = not self.autopilot
        if self.autopilot:
            # keep playing the build the player was going for
            diet = p.diet if p.diet != "none" else None
            p.brain = AIBrain(p, self.world, self.manager, intended_diet=diet)
            mode = ("-> LLM now controls the player" if self.manager.enabled
                    else "-> heuristics now control the player")
            self.world.log(f"{mode}  (P to take back control)", C.C_LLM)
        else:
            p.thrust = pygame.Vector2(0, 0)
            self._sync_player_brain()
            tail = (" (AI still handles upgrades)" if self.auto_evolve else "")
            self.world.log(f"Autopilot off - you have control.{tail}",
                           C.C_TEXT_DIM)

    def _call_mate(self):
        p = self.world.player
        ang = p.angle + math.pi
        spawn = p.pos + pygame.Vector2(math.cos(ang), math.sin(ang)) * 240
        spawn.x = max(20, min(C.WORLD_W - 20, spawn.x))
        spawn.y = max(20, min(C.WORLD_H - 20, spawn.y))
        self.mate = Mate(spawn, p.color)
        self.sound.play("mate")
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
        self.sound.play("stage")
        self.world.fx.ripple(p.pos, C.C_MULTI, max_radius=200, life=0.9)
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
        self.sound.play("stage")
        self.world.fx.ripple(p.pos, C.C_MULTI, max_radius=260, life=1.1)
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
        elif self.state == STATE_GAMEOVER and self.autopilot:
            # nobody will press R either - retry automatically
            self._retry_timer -= dt
            if self._retry_timer <= 0:
                self._new_world()
        # editor / paused / gameover (manual): world is frozen

    def _update_playing(self, dt):
        if not self.autopilot:
            keys = pygame.key.get_pressed()
            self.controller.update(keys)
        self.world.update(dt, self.t)
        self.camera.follow(self.world.player.pos, self.world.player.radius, dt)

        # audio + juice: listener follows the camera; thud & shake when hurt;
        # heartbeat while critical
        p = self.world.player
        self.sound.listener = self.camera.center
        if self._last_player_hp is not None and p.alive:
            lost = self._last_player_hp - p.health
            if lost > 3.5:
                self.sound.play("hurt")
                self.camera.shake(min(11.0, 4.0 + lost * 0.5))
        self._last_player_hp = p.health if p.alive else None
        self.sound.heartbeat(p.alive and p.health < p.max_health * 0.25)

        # every 30s, tell the feed WHY heuristics are driving (and announce
        # recovery the moment the LLM comes back)
        self._llm_check_timer -= dt
        if self._llm_check_timer <= 0:
            self._llm_check_timer = 2.0
            reason = self.manager.heuristics_reason()
            if reason:
                self._llm_was_down = True
                if self.t >= self._llm_next_notice:
                    self._llm_next_notice = self.t + 30.0
                    self.world.log(f"[LLM] heuristics driving: {reason}",
                                   C.C_ENERGY)
            elif self._llm_was_down and self.manager.enabled:
                self._llm_was_down = False
                avg = self.manager.avg_latency()
                tail = f" (avg round-trip {avg:.1f}s)" if avg else ""
                self.world.log(f"[LLM] back online{tail}", C.C_GOOD)

        if self.mate is not None:
            self.mate.update(dt, self.world.player.pos)
            if ((self.mate.pos - self.world.player.pos).length()
                    < self.world.player.radius + self.mate.radius + 4
                    or self.mate.timer > 9.0):
                self._open_editor()

        if self.world.player_dead:
            self.state = STATE_GAMEOVER
            self._retry_timer = 2.0
            p = self.world.player
            run = {"survived": p.time_alive, "stage": p.stage,
                   "level": p.growth_level, "kills": p.kills,
                   "eaten": p.food_eaten, "dna": p.lifetime_dna,
                   "leviathans": self.world.player_epic_kills}
            rec, new = records.update_on_death(p, self.world.player_epic_kills)
            self._death_summary = (run, rec, new)
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
        self.world.draw_zones(s, self.camera, self.t)
        self.world.draw_entities(s, self.camera, self.t)
        if self.mate is not None:
            self.mate.draw(s, self.camera, self.t)
        if self.show_overlay:
            self.hud.draw_overhead(s, self.world, self.camera)
        self.hud.draw_threat_arrows(s, self.world, self.camera, self.t)
        self.hud.draw(s, self.world, self.camera, self.clock.get_fps(),
                      self.manager, self.t, autopilot=self.autopilot,
                      auto_evolve=self.auto_evolve)

        if self.state == STATE_EDITOR:
            self.editor.draw(s, self.world.player, self.t)
        elif self.state == STATE_PROMPT and self.prompt is not None:
            self._draw_prompt(s)
        elif self.state == STATE_PAUSED:
            now = "LLM" if self.manager.enabled else "heuristics"
            nxt = "heuristics" if self.manager.enabled else "LLM"
            self._overlay_text(
                s, "PAUSED",
                f"Esc: resume    L: switch AI to {nxt} (now: {now})    "
                "Q: quit the game\n"
                f"U: AI handles upgrades while you drive "
                f"(now: {'ON' if self.auto_evolve else 'OFF'})")
        elif self.state == STATE_GAMEOVER:
            self._draw_gameover(s)

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

    def _draw_gameover(self, surface):
        W, H = surface.get_size()
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 170))
        surface.blit(overlay, (0, 0))
        title = self.hud.font_xl.render("You were consumed", True, C.C_BAD)
        surface.blit(title, ((W - title.get_width()) // 2, H // 2 - 220))

        if self._death_summary is None:
            return
        run, rec, new = self._death_summary
        star = "  * NEW RECORD *"
        left = [
            ("THIS RUN", None),
            (f"Survived {int(run['survived'])}s"
             + (star if "survival" in new else ""), C.C_TEXT),
            (f"Reached {records.stage_name(run['stage'])} level {run['level']}"
             + (star if "stage" in new or "fish" in new else ""), C.C_TEXT),
            (f"Kills: {run['kills']}", C.C_TEXT),
            (f"Food eaten: {run['eaten']}", C.C_TEXT),
            (f"DNA earned: {int(run['dna'])}"
             + (star if "dna" in new else ""), C.C_TEXT),
            (f"Leviathans slain: {run['leviathans']}", C.C_TEXT),
        ]
        right = [
            ("ALL-TIME", None),
            (f"Best survival: {int(rec['best_survival'])}s", C.C_TEXT_DIM),
            (f"Best stage: {records.stage_name(rec['best_stage'])} "
             f"level {rec['best_level']}", C.C_TEXT_DIM),
            (f"Best fish level: {rec['best_fish_level']}", C.C_TEXT_DIM),
            (f"Best DNA in a run: {int(rec['best_dna'])}", C.C_TEXT_DIM),
            (f"Total kills: {rec['total_kills']}", C.C_TEXT_DIM),
            (f"Leviathans slain: {rec['total_leviathans']}", C.C_TEXT_DIM),
            (f"Runs: {rec['runs']}", C.C_TEXT_DIM),
        ]
        for col_x, rows in ((W // 2 - 330, left), (W // 2 + 60, right)):
            y = H // 2 - 140
            for text, color in rows:
                if color is None:
                    ts = self.hud.font_m.render(text, True, C.C_MULTI)
                else:
                    hot = star in text
                    ts = self.hud.font_s.render(text, True,
                                                C.C_ENERGY if hot else color)
                surface.blit(ts, (col_x, y))
                y += 26 if color is None else 22

        tail = ("R: try again    Esc: quit" if not self.autopilot else
                f"Auto-retry in {max(0, math.ceil(self._retry_timer))}s...")
        ts = self.hud.font_m.render(tail, True, C.C_TEXT)
        surface.blit(ts, ((W - ts.get_width()) // 2, H // 2 + 90))

    def _overlay_text(self, surface, title, subtitle, color=C.C_TEXT):
        W, H = surface.get_size()
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 150))
        surface.blit(overlay, (0, 0))
        ts = self.hud.font_xl.render(title, True, color)
        surface.blit(ts, ((W - ts.get_width()) // 2, H // 2 - 70))
        y = H // 2 + 6
        for line in subtitle.split("\n"):
            ss = self.hud.font_m.render(line, True, C.C_TEXT)
            surface.blit(ss, ((W - ss.get_width()) // 2, y))
            y += 28

    # ------------------------------------------------------- screenshot mode
    def run_screenshot(self, path, frames=300):
        """Headless: simulate a while, then save a gameplay PNG (+ an editor PNG)."""
        dt = 1.0 / C.FPS
        # let the demo player drive so there is visible action
        if self.world.player.brain is None:
            self.world.player.brain = AIBrain(self.world.player, self.world,
                                              self.manager)
        self.autopilot = True
        for _ in range(frames):
            self.t += dt
            self.world.update(dt, self.t)
            self.camera.follow(self.world.player.pos, self.world.player.radius, dt)
            if self.world.player_dead:
                # respawn so the screenshot always shows a living cell
                self._new_world()
                if self.world.player.brain is None:
                    self.world.player.brain = AIBrain(self.world.player,
                                                      self.world, self.manager)
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
