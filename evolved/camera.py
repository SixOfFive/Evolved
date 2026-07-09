"""Follow camera with smooth zoom.

World coordinates are converted to screen coordinates through the camera's
center position and zoom factor. The zoom shrinks as the followed cell grows,
so the player always sees a sensible slice of the ocean.
"""

import random

import pygame

from . import config as C


class Camera:
    def __init__(self, screen_w, screen_h):
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.center = pygame.Vector2(C.WORLD_W / 2, C.WORLD_H / 2)
        self.zoom = C.ZOOM_BASE
        self._target_zoom = C.ZOOM_BASE
        self._shake_t = 0.0
        self._shake_mag = 0.0
        self._shake_off = pygame.Vector2(0, 0)

    def shake(self, magnitude=7.0, duration=0.3):
        self._shake_mag = max(self._shake_mag, magnitude)
        self._shake_t = max(self._shake_t, duration)

    def resize(self, w, h):
        self.screen_w = w
        self.screen_h = h

    def follow(self, target_pos, target_radius, dt):
        # Smoothly chase the target position.
        self.center += (pygame.Vector2(target_pos) - self.center) * min(1.0, dt * 6.0)
        # Desired zoom is inversely related to the followed radius.
        ratio = C.ZOOM_REF_RADIUS / max(1.0, target_radius)
        self._target_zoom = max(C.ZOOM_MIN, min(C.ZOOM_MAX, C.ZOOM_BASE * (ratio ** 0.55)))
        self.zoom += (self._target_zoom - self.zoom) * min(1.0, dt * 2.5)
        # screen shake: random jitter that decays with its timer
        if self._shake_t > 0:
            self._shake_t -= dt
            f = max(0.0, self._shake_t / 0.3)
            m = self._shake_mag * f
            self._shake_off.update(random.uniform(-m, m), random.uniform(-m, m))
            if self._shake_t <= 0:
                self._shake_mag = 0.0
                self._shake_off.update(0, 0)

    def snap(self, target_pos, target_radius):
        self.center = pygame.Vector2(target_pos)
        ratio = C.ZOOM_REF_RADIUS / max(1.0, target_radius)
        self.zoom = max(C.ZOOM_MIN, min(C.ZOOM_MAX, C.ZOOM_BASE * (ratio ** 0.55)))

    def world_to_screen(self, world_pos):
        x = (world_pos[0] - self.center.x) * self.zoom + self.screen_w / 2
        y = (world_pos[1] - self.center.y) * self.zoom + self.screen_h / 2
        return (x + self._shake_off.x, y + self._shake_off.y)

    def screen_to_world(self, screen_pos):
        x = (screen_pos[0] - self.screen_w / 2) / self.zoom + self.center.x
        y = (screen_pos[1] - self.screen_h / 2) / self.zoom + self.center.y
        return pygame.Vector2(x, y)

    def scale(self, length):
        return length * self.zoom

    def is_visible(self, world_pos, radius):
        sx, sy = self.world_to_screen(world_pos)
        r = radius * self.zoom
        return (-r <= sx <= self.screen_w + r) and (-r <= sy <= self.screen_h + r)
