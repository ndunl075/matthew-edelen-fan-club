from __future__ import annotations
import math
from collections import deque
from typing import NamedTuple
from .params import Params
from .util import rot2, unit2

class Steer(NamedTuple):
    move: tuple
    jump: bool = False
    jet: bool = False

class NullWorld:

    def los(self, a, b) -> bool | None:
        return None

    def cover_dir(self, pos, threat_pos):
        return

class Nav:

    def __init__(self, params: Params, rng):
        self.p = params
        self.rng = rng
        self._hist: deque = deque()
        self._last_t = -1000000000.0
        self._last_heading: tuple | None = None
        self._clock = 0.0
        self._rec_since: float | None = None
        self._rec_from: tuple = (0.0, 0.0)
        self._rec_side = 1.0
        self._cooldown_until = -1000000000.0

    def reset(self) -> None:
        self._hist.clear()
        self._rec_since = None
        self._last_heading = None

    @property
    def recovering(self) -> bool:
        return self._rec_since is not None

    def steer(self, me: dict, goal_xy, speed: float=1.0, t: float | None=None) -> Steer:
        p = self.p.nav
        if t is None:
            self._clock += 0.1
            t = self._clock
        x, y = (me['pos'][0], me['pos'][1])
        dx, dy = (goal_xy[0] - x, goal_xy[1] - y)
        dist = math.hypot(dx, dy)
        if t - self._last_t > p.gap_reset_s:
            self._hist.clear()
            self._rec_since = None
        self._last_t = t
        if dist < p.arrive_m:
            self._hist.clear()
            self._rec_since = None
            return Steer((0.0, 0.0))
        heading = unit2(dx, dy)
        lh = self._last_heading
        self._last_heading = heading
        if lh is not None and heading[0] * lh[0] + heading[1] * lh[1] < math.cos(math.radians(p.heading_reset_deg)):
            self._hist.clear()
            self._rec_since = None
        scale = speed * min(1.0, dist / p.slow_m)
        walk = me.get('stats', {}).get('walk_speed', 0.0)
        self._hist.append((t, x, y))
        while self._hist and t - self._hist[0][0] > p.stuck_window_s:
            self._hist.popleft()
        if self._rec_since is None:
            span = t - self._hist[0][0]
            if span >= p.stuck_window_s * p.stuck_span_frac and t >= self._cooldown_until:
                moved = math.hypot(x - self._hist[0][1], y - self._hist[0][2])
                if moved < p.stuck_frac * speed * walk * span:
                    self._rec_since = t
                    self._rec_from = (x, y)
                    self._rec_side = 1.0 if self.rng.random() < 0.5 else -1.0
            if self._rec_since is None:
                return Steer((heading[0] * scale, heading[1] * scale))
        el = t - self._rec_since
        if math.hypot(x - self._rec_from[0], y - self._rec_from[1]) >= p.unstick_m and el > p.slide_s or el > p.recover_max_s:
            self._rec_since = None
            self._hist.clear()
            self._cooldown_until = t + p.cooldown_s
            return Steer((heading[0] * scale, heading[1] * scale))
        stage = int(el // (p.slide_s + p.jet_s))
        within = el - stage * (p.slide_s + p.jet_s)
        side = self._rec_side * (1.0 if stage % 2 == 0 else -1.0)
        if within < p.slide_s:
            v = rot2(heading, side * math.radians(p.slide_deg))
            return Steer((v[0] * speed, v[1] * speed))
        grounded = bool(me.get('grounded', True))
        energy_ok = me.get('energy', 0.0) >= p.jet_min_energy_frac * me.get('max_energy', 0.0)
        return Steer((heading[0] * speed, heading[1] * speed), jump=grounded, jet=not grounded and energy_ok)

def steer(nav: Nav, self_state: dict, goal_xy, speed: float=1.0, t: float | None=None) -> tuple:
    return nav.steer(self_state, goal_xy, speed, t).move
