"""Procedural sound: every effect is synthesized from raw math at startup.

No audio files anywhere - in keeping with the game's no-assets philosophy,
each sound is built sample-by-sample from three primitive oscillators
(sine, square, white noise) with per-sample frequency sweeps and
attack/decay envelopes, then handed to pygame's mixer as a 16-bit buffer.

The SoundManager plays them positionally (volume falls off with distance
from the camera) and rate-limits each effect so a feeding frenzy doesn't
become a machine gun.
"""

import array
import math
import random

import pygame

SR = 22050          # sample rate the mixer is opened at
_MASTER = 0.6


def _tone(f0, f1, ms, vol=0.6, shape="sine", decay=2.0, vibrato=0.0):
    """One oscillator note: frequency glides f0->f1 over `ms` milliseconds."""
    n = max(1, int(SR * ms / 1000))
    out = array.array("f", bytes(4 * n))
    phase = 0.0
    for i in range(n):
        t = i / n
        f = f0 + (f1 - f0) * t
        if vibrato:
            f *= 1.0 + 0.03 * math.sin(2 * math.pi * vibrato * i / SR)
        phase += 2 * math.pi * f / SR
        if shape == "sine":
            s = math.sin(phase)
        elif shape == "square":
            s = 0.6 if math.sin(phase) >= 0 else -0.6  # softened square
        else:  # noise
            s = random.uniform(-1.0, 1.0)
        env = (1.0 - t) ** decay
        attack = min(1.0, i / (SR * 0.004))  # 4 ms ramp kills the click
        out[i] = s * vol * env * attack
    return out


def _gated_noise(ms, gate_hz, ring_hz, vol=0.6):
    """Noise chopped by a square gate with a metallic ring - the zap."""
    n = max(1, int(SR * ms / 1000))
    out = array.array("f", bytes(4 * n))
    for i in range(n):
        t = i / n
        gate = 1.0 if math.sin(2 * math.pi * gate_hz * i / SR) > -0.3 else 0.15
        ring = 0.5 * math.sin(2 * math.pi * ring_hz * i / SR)
        s = (random.uniform(-1, 1) * 0.7 + ring) * gate
        out[i] = s * vol * (1.0 - t) ** 1.5 * min(1.0, i / (SR * 0.004))
    return out


def _mix(*layers):
    """Overlay float buffers (they may differ in length)."""
    n = max(len(b) for b in layers)
    out = array.array("f", bytes(4 * n))
    for b in layers:
        for i, s in enumerate(b):
            out[i] += s
    return out


def _seq(*steps):
    """Concatenate (buffer, overlap_ms) steps into one phrase."""
    out = array.array("f")
    for buf, overlap_ms in steps:
        cut = int(SR * overlap_ms / 1000)
        start = max(0, len(out) - cut)
        # overlap-add the tail
        for i in range(min(cut, len(out) - start, len(buf))):
            out[start + i] += buf[i]
        out.extend(buf[min(cut, len(buf)):])
    return out


def _build_all():
    """The full effect bank, keyed by name."""
    fx = {}
    # eating: bright blips, pitch varied so grazing doesn't drone
    fx["eat_plant"] = [_tone(620 * v, 900 * v, 70, 0.5) for v in (0.92, 1.0, 1.1)]
    fx["eat_meat"] = [_tone(240, 150, 90, 0.6), _tone(220, 140, 90, 0.6)]
    fx["eat_algae"] = [_mix(_tone(300, 420, 110, 0.4), _tone(150, 210, 110, 0.35))]
    fx["meteor"] = [_seq((_tone(523, 523, 80, 0.45), 0),
                         (_tone(659, 659, 80, 0.45), 30),
                         (_tone(784, 784, 110, 0.5), 30))]
    # combat
    fx["bite"] = [_tone(320, 260, 45, 0.5, shape="square", decay=3.0)]
    fx["hurt"] = [_mix(_tone(0, 0, 90, 0.5, shape="noise", decay=3.0),
                       _tone(120, 80, 90, 0.7))]
    fx["zap"] = [_gated_noise(220, 55, 1400, 0.6)]
    fx["swallow"] = [_tone(420, 110, 260, 0.7, decay=1.2)]
    fx["sting"] = [_tone(900, 500, 60, 0.35, shape="square", decay=3.0)]
    # progression
    fx["grow"] = [_seq((_tone(392, 460, 140, 0.5), 0),
                       (_tone(523, 660, 190, 0.5), 50))]
    fx["stage"] = [_seq((_tone(262, 262, 130, 0.5, shape="square"), 0),
                        (_tone(330, 330, 130, 0.5, shape="square"), 45),
                        (_tone(392, 392, 130, 0.5, shape="square"), 45),
                        (_tone(523, 523, 260, 0.55, shape="square"), 45))]
    fx["death"] = [_tone(300, 70, 800, 0.7, decay=1.0, vibrato=7.0)]
    fx["mate"] = [_seq((_tone(880, 880, 120, 0.4), 0),
                       (_tone(880, 880, 120, 0.18), -140))]
    fx["click"] = [_tone(1000, 900, 30, 0.35, shape="square", decay=3.0)]
    fx["dash"] = [_mix(_tone(0, 0, 180, 0.35, shape="noise", decay=1.5),
                       _tone(220, 900, 180, 0.3))]
    # heartbeat: a double thump, meant to loop while HP is critical
    thump = _tone(55, 45, 140, 0.9, decay=1.6)
    fx["heartbeat"] = [_seq((thump, 0), (thump, -180),
                            (_tone(1, 1, 350, 0.0), 0))]
    return fx


def _to_sound(fbuf):
    """Float buffer -> pygame Sound, honoring the mixer's actual format."""
    init = pygame.mixer.get_init()
    channels = init[2] if init else 1
    out = array.array("h")
    for s in fbuf:
        v = int(32767 * max(-1.0, min(1.0, s)))
        for _ in range(channels):
            out.append(v)
    return pygame.mixer.Sound(buffer=out.tobytes())


class SoundManager:
    # minimum seconds between plays of the same effect
    _COOLDOWN = {"eat_plant": 0.06, "eat_meat": 0.08, "eat_algae": 0.1,
                 "bite": 0.22, "sting": 0.3, "hurt": 0.25, "zap": 0.4,
                 "swallow": 0.15, "death": 0.2, "click": 0.04}

    def __init__(self):
        self.enabled = pygame.mixer.get_init() is not None
        self.listener = pygame.Vector2(0, 0)
        self._last = {}
        self._sounds = {}
        self._heart_channel = None
        if not self.enabled:
            print("[Evolved] no audio device - running silent.")
            return
        for name, variants in _build_all().items():
            self._sounds[name] = [_to_sound(v) for v in variants]

    def play(self, name, pos=None, volume=1.0):
        """Play an effect; `pos` attenuates it by distance from the camera."""
        if not self.enabled or name not in self._sounds:
            return
        now = pygame.time.get_ticks() / 1000.0
        if now - self._last.get(name, -9.0) < self._COOLDOWN.get(name, 0.0):
            return
        vol = volume
        if pos is not None:
            dist = (pygame.Vector2(pos) - self.listener).length()
            vol *= max(0.0, 1.0 - dist / 1300.0)
            if vol < 0.05:
                return
        self._last[name] = now
        snd = random.choice(self._sounds[name])
        snd.set_volume(min(1.0, vol * _MASTER))
        snd.play()

    def heartbeat(self, critical):
        """Loop the heartbeat while the player's health is critical."""
        if not self.enabled:
            return
        if critical and self._heart_channel is None:
            snd = self._sounds["heartbeat"][0]
            snd.set_volume(0.8 * _MASTER)
            self._heart_channel = snd.play(loops=-1)
        elif not critical and self._heart_channel is not None:
            self._heart_channel.stop()
            self._heart_channel = None
