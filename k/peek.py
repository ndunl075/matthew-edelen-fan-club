from __future__ import annotations
import math
from dataclasses import dataclass
from .derive import Derived
from .params import Params
from .util import dist2

@dataclass
class PeekPlan:
    goal: tuple
    phase: str
    laser: str
    note: str = ''

class Peeker:

    def __init__(self, cfg: dict, params: Params, derived: Derived):
        self.cfg = cfg
        self.p = params
        self.dv = derived
        self.phase = 'idle'
        self._pair: tuple | None = None
        self._pair_t = -1000000000.0
        self._fired_t = -1000000000.0
        self._out_since = -1000000000.0
        self._was_charging = False

    def reset(self) -> None:
        self.phase = 'idle'
        self._pair = None
        self._was_charging = False

    def active(self) -> bool:
        return self.phase != 'idle'

    def plan(self, v, enemy, laser: dict | None, nav) -> PeekPlan | None:
        q = self.p.peek
        if laser is None or enemy is None:
            self.reset()
            return None
        t = v.t
        charge = laser.get('charge', 0.0)
        cooldown = laser.get('cooldown', 0.0)
        full = laser['stats'].get('charge', self.cfg['weapons']['laser']['charge'])
        if self._was_charging and charge <= 0.0 and (cooldown > 0.0):
            self._fired_t = t
            self.phase = 'back'
        self._was_charging = charge > 0.0
        if self._pair is None or t - self._pair_t > q.replan_s:
            pair = nav.peek_spot(v.me, enemy.center, max_dist=q.max_dist_m)
            self._pair_t = t
            if pair is None:
                self.reset()
                return None
            self._pair = (tuple(pair[0]), tuple(pair[1]))
        hide, fire = self._pair
        at_hide = dist2(hide, v.pos) <= q.arrive_m
        at_fire = dist2(fire, v.pos) <= q.arrive_m
        step_m = dist2(hide, fire)
        step_s = self.dv.charge_step_time(step_m, v.stats) + q.margin_s
        left = max(full - charge, 0.0)
        ready = cooldown <= self.p.fire.laser_start_cooldown_s
        if self.phase in ('idle', 'back'):
            if self.phase == 'back' and (not at_hide):
                return PeekPlan(hide, 'back', 'auto' if charge > 0 else 'wait', 'back to hide')
            self.phase = 'hide'
        if self.phase == 'hide':
            if not at_hide:
                return PeekPlan(hide, 'hide', 'wait', 'to hide')
            if charge <= 0.0 and (not ready):
                return PeekPlan(hide, 'hide', 'wait', 'cooling down')
            if charge > 0.0 and left <= step_s:
                self.phase = 'out'
                self._out_since = t
            else:
                return PeekPlan(hide, 'hide', 'hold', f'charging {charge:.2f}/{full:.2f}')
        if self.phase == 'out':
            if at_fire and t - self._out_since > step_s + q.fire_wait_s:
                self.phase = 'back'
                return PeekPlan(hide, 'back', 'auto', 'peek failed')
            return PeekPlan(fire, 'out', 'auto', 'stepping out')
        return None

    def fits(self, v, enemy, band_lo: float) -> bool:
        return enemy.d >= band_lo * self.p.peek.min_band_frac and (not math.isnan(enemy.d))
