from __future__ import annotations
import importlib.util
import math
import random
from collections import deque
from .params import Params, load_params
from .util import rot2, unit2

class NavStub:
    real = False

    def __init__(self, hello: dict, params: Params | None=None):
        self.cfg = hello['config']
        self.p = params or load_params()
        self.rng = random.Random(int(hello.get('seed', 0)) ^ 24301)
        self.dt = self.cfg['match']['decide_every'] / self.cfg['match']['tick_hz']
        self._clock = 0.0
        self._hist: deque = deque()
        self._last_t = -1000000000.0
        self._last_heading: tuple | None = None
        self._rec_since: float | None = None
        self._rec_from: tuple = (0.0, 0.0)
        self._rec_side = 1.0
        self._cooldown_until = -1000000000.0

    def steer(self, me: dict, goal_xy, *, quiet: bool=False) -> dict:
        self._clock += self.dt
        move, boost, jump = self._steer(me, goal_xy, self._clock, quiet)
        return {'move': [move[0], move[1]], 'boost': bool(boost and (not quiet)), 'jump': bool(jump and (not quiet))}

    def path_length(self, a_xyz, b_xy) -> float:
        return math.hypot(b_xy[0] - a_xyz[0], b_xy[1] - a_xyz[1])

    def has_los(self, a_xyz, b_xyz) -> bool:
        return True

    def shot_clear(self, a_xyz, b_xyz) -> bool:
        return True

    def cover_from(self, me, threats_xyz, *, max_dist: float=60.0):
        return None

    def peek_spot(self, me, target_xyz, *, max_dist: float=40.0):
        return None

    def high_ground(self, me, circle, *, max_climb=None):
        return None

    def pocket(self, me, circle):
        return None

    def open_ground(self, xy) -> bool:
        return True

    def reset(self) -> None:
        self._hist.clear()
        self._rec_since = None
        self._last_heading = None

    @property
    def quiet_scale(self) -> float:
        walk = self.cfg['mech']['walk_speed']
        return min(1.0, self.cfg['sound']['step_speed'] * self.p.sound.quiet_frac / max(walk, 1e-06))

    def _steer(self, me: dict, goal_xy, t: float, quiet: bool) -> tuple:
        p = self.p.nav
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
            return ((0.0, 0.0), False, False)
        heading = unit2(dx, dy)
        lh = self._last_heading
        self._last_heading = heading
        if lh is not None and heading[0] * lh[0] + heading[1] * lh[1] < math.cos(math.radians(p.heading_reset_deg)):
            self._hist.clear()
            self._rec_since = None
        speed = self.quiet_scale if quiet else 1.0
        scale = speed * min(1.0, dist / p.slow_m)
        walk = me.get('stats', {}).get('walk_speed', self.cfg['mech']['walk_speed'])
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
                return ((heading[0] * scale, heading[1] * scale), False, False)
        el = t - self._rec_since
        if math.hypot(x - self._rec_from[0], y - self._rec_from[1]) >= p.unstick_m and el > p.slide_s or el > p.recover_max_s:
            self._rec_since = None
            self._hist.clear()
            self._cooldown_until = t + p.cooldown_s
            return ((heading[0] * scale, heading[1] * scale), False, False)
        stage = int(el // (p.slide_s + p.jet_s))
        within = el - stage * (p.slide_s + p.jet_s)
        side = self._rec_side * (1.0 if stage % 2 == 0 else -1.0)
        if within < p.slide_s:
            v = rot2(heading, side * math.radians(p.slide_deg))
            return ((v[0] * speed, v[1] * speed), False, False)
        grounded = bool(me.get('grounded', True))
        energy_ok = me.get('energy', 0.0) >= p.jet_min_energy_frac * me.get('max_energy', 0.0)
        return ((heading[0] * speed, heading[1] * speed), not grounded and energy_ok, grounded)

def make_nav(hello: dict, params: Params | None=None):
    pkg = __package__ or 'k'
    if importlib.util.find_spec(f'{pkg}.terrain') is not None:
        try:
            from .nav import Nav
            return Nav(hello)
        except Exception:
            import sys
            import traceback
            print(f'real Nav failed to load, using the stand-in:\n{traceback.format_exc()}', file=sys.stderr, flush=True)
    return NavStub(hello, params)
