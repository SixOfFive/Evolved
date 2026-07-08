"""Player input: translate the keyboard into a thrust vector for the cell."""

import pygame


class PlayerController:
    def __init__(self, cell):
        self.cell = cell

    def update(self, keys):
        dx = dy = 0.0
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            dy -= 1
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            dy += 1
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            dx -= 1
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            dx += 1
        v = pygame.Vector2(dx, dy)
        if v.length_squared() > 0:
            v = v.normalize()
        self.cell.thrust = v
