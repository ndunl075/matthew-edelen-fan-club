from __future__ import annotations
import math
from dataclasses import dataclass
from .derive import Band
from .params import Params
from .util import clamp, rot2, unit2

@dataclass
class KitePlan:
    direction: tuple
    boost: bool
    state: str
    losing: bool
    speed: float = 1.0

class Kiter:

    def __init__(self, cfg: dict, params: Params, rng):
        self.cfg = cfg
        self.p = params
        self.rng = rng
        self._side = 1.0
        self._side_until = -1000000000.0

    def plan(self, v, e, band: Band, *, charging: bool, hit_recently: bool) -> KitePlan:
        q = self.p.kite
        walk = v.walk
        mult = v.stats.get('charge_move_mult', self.cfg['mech']['charge_move_mult']) if charging else 1.0
        my_speed = walk * mult
        toward = unit2(e.pos[0] - v.pos[0], e.pos[1] - v.pos[1])
        half = max(0.5 * (band.hi - band.lo), q.min_half_band_radii * v.radius)
        radial = clamp((e.d - band.hold) / half, -1.0, 1.0)
        if abs(radial) < q.dead_zone:
            radial = 0.0
        if v.t >= self._side_until:
            if self.rng.random() < q.strafe_flip_prob:
                self._side = -self._side
            self._side_until = v.t + self.rng.uniform(q.strafe_min_s, q.strafe_max_s)
        side = rot2(toward, self._side * math.pi / 2.0)
        w_side = q.strafe_w * (1.0 - min(abs(radial), q.strafe_fade))
        d = unit2(toward[0] * radial + side[0] * w_side, toward[1] * radial + side[1] * w_side)
        state = 'back' if radial < 0 else 'follow' if radial > 0 else 'strafe'
        losing = radial < 0 and e.closing > q.losing_frac * my_speed
        boost = (radial < 0 and (e.closing > 0.0 or e.d < band.lo)) and v.energy_frac >= q.sprint_energy_frac
        if hit_recently and radial >= 0:
            boost = False
        return KitePlan(d, bool(boost), state, losing)
