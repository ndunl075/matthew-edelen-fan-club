from __future__ import annotations
import itertools
import math
from dataclasses import dataclass
from .params import Params

@dataclass(frozen=True)
class Band:
    lo: float
    hi: float
    hold: float
    ratio: float
    weapon: str
    ok: bool

def interp(table: list, x: float) -> float:
    if x <= table[0][0]:
        return table[0][1]
    for (x0, y0), (x1, y1) in itertools.pairwise(table):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / max(x1 - x0, 1e-09)
    return table[-1][1] * table[-1][0] / x

class Derived:

    def __init__(self, cfg: dict, params: Params):
        self.cfg = cfg
        self.p = params
        self.dt = cfg['match']['decide_every'] / cfg['match']['tick_hz']
        self._band_cache: dict = {}

    def enemy_hit_rate(self, dist: float, *, speed: float | None=None, state: str='ground', mult: float=1.0) -> float:
        q = self.p.derive
        speed = speed or self.cfg['weapons']['gun']['speed']
        eq = dist * q.ref_bullet_speed / max(speed, 1e-06)
        rate = interp(q.hit_table, eq)
        rate *= {'air': q.air_mult, 'sprint': q.sprint_mult}.get(state, 1.0)
        return min(1.0, max(0.0, rate * mult))

    def laser_window_s(self, charge_s: float | None=None) -> float:
        charge = self.cfg['weapons']['laser']['charge'] if charge_s is None else charge_s
        return max(0.0, charge - self.dt - self.p.derive.react_s)

    def sidestep_time(self, dist: float, st: dict | None=None) -> float:
        m = st or self.cfg['mech']
        a = max(m.get('ground_accel', self.cfg['mech']['ground_accel']), 1e-06)
        v = max(m.get('walk_speed', self.cfg['mech']['walk_speed']), 1e-06)
        ramp = v * v / (2.0 * a)
        if dist <= ramp:
            return math.sqrt(2.0 * dist / a)
        return v / a + (dist - ramp) / v

    def dodge_possible(self, remaining_s: float, radius: float, st: dict | None=None) -> bool:
        need = radius + self.p.derive.beam_margin_m
        return self.sidestep_time(need, st) + self.dt + self.p.derive.react_s <= remaining_s

    def cube_mult(self, cubes: int) -> float:
        c = self.cfg['cubes']
        return 1.0 + c['damage_per_cube'] * min(max(cubes, 0), c['stat_cap'])

    def weapon_dps(self, kind: str, st: dict, cubes: int=0) -> float:
        mult = self.cube_mult(cubes)
        q = self.p.derive
        if kind == 'gun':
            mag = max(st.get('mag', 1), 1)
            return st['damage'] * mult * mag / (mag * st['cooldown'] + st.get('reload', 0.0))
        if kind == 'laser':
            charge = st.get('charge', self.cfg['weapons']['laser']['charge'])
            return st['damage'] * mult / (charge + st['cooldown'])
        if kind == 'mortar':
            return (st['damage'] + q.splash_share * st.get('splash', 0.0)) * mult / max(st['cooldown'], 0.001)
        if kind == 'lightning':
            return st['damage'] * mult / max(st['cooldown'], 0.001)
        return 0.0

    def weapon_range(self, kind: str, st: dict) -> float:
        if kind == 'mortar':
            return st['speed'] ** 2 / st.get('gravity', self.cfg['weapons']['mortar']['gravity'])
        return st.get('range', self.cfg['weapons'].get(kind, {}).get('range', 0.0))

    def usable(self, kind: str, st: dict, dist: float) -> bool:
        f = self.p.fire
        if kind == 'mortar':
            return f.mortar_min_range_m <= dist <= self.weapon_range(kind, st) * f.mortar_range_frac
        return dist <= self.weapon_range(kind, st) * f.range_frac

    def our_hit_rate(self, kind: str, dist: float, st: dict, mult: float=1.0) -> float:
        q = self.p.derive
        if kind == 'gun':
            rate = interp(q.hit_table, dist * q.ref_bullet_speed / max(st['speed'], 1e-06)) * q.gun_skill
        elif kind == 'mortar':
            rate = interp(q.hit_table, dist * q.ref_bullet_speed / max(st['speed'], 1e-06)) * q.mortar_skill
        elif kind == 'laser':
            rate = q.laser_hit
        else:
            rate = q.lightning_hit
        return min(1.0, max(0.0, rate * mult))

    def my_dps_at(self, weapons: list, dist: float, cubes: int=0, laser_mult: float=1.0) -> tuple[float, str]:
        best, kind = (0.0, '')
        for w in weapons:
            if not w:
                continue
            k, st = (w['kind'], w['stats'])
            if not self.usable(k, st, dist):
                continue
            rate = self.weapon_dps(k, st, cubes) * self.our_hit_rate(k, dist, st, laser_mult if k == 'laser' else 1.0)
            if rate > best:
                best, kind = (rate, k)
        return (best, kind)

    def time_to_kill(self, weapons: list, dist: float, hp: float, cubes: int=0) -> float:
        dps, _ = self.my_dps_at(weapons, dist, cubes)
        return math.inf if dps <= 0 else hp / dps

    def enemy_dps_at(self, holds: set, dist: float, cubes: int=0, hit_mult: float=1.0) -> float:
        q = self.p.derive
        w = self.cfg['weapons']
        best = 0.0
        for kind in (holds or {'gun'}) | {'gun'}:
            st = w.get(kind)
            if not st:
                continue
            if kind == 'lightning' and dist > st['range']:
                continue
            if kind == 'laser':
                if dist > st['range']:
                    continue
                rate = q.enemy_laser_hit * hit_mult
            elif kind == 'lightning':
                rate = q.lightning_hit
            else:
                rate = self.enemy_hit_rate(dist, speed=st['speed'], mult=hit_mult)
            best = max(best, self.weapon_dps(kind, st, cubes) * rate)
        return best

    def safe_range(self, holds: set, cubes: int=0, hit_mult: float=1.0) -> float:
        regen = self.cfg['mech']['regen_per_s'] * self.p.derive.safe_regen_frac
        step = self.p.derive.band_step_m
        d = step
        top = 1.0
        for k in (holds or {'gun'}) | {'gun'}:
            top = max(top, self.cfg['weapons'].get(k, {}).get('range', 0.0))
        while d < top:
            if self.enemy_dps_at(holds, d, cubes, hit_mult) <= regen:
                return d
            d += step
        return top

    def view_cap(self, st: dict) -> float:
        v = self.cfg['vision']
        return v['range'] + st.get('vision_bonus', 0.0) - self.p.derive.view_margin_m

    def band(self, weapons: list, st: dict, cubes: int, holds: set, enemy_cubes: int=0, *, enemy_mult: float=1.0, laser_mult: float=1.0) -> Band:
        q = self.p.derive
        key = (tuple(((w['kind'], tuple(sorted(w['stats'].items()))) if w else None for w in weapons)), round(self.view_cap(st), 1), cubes, tuple(sorted(holds)), enemy_cubes, round(enemy_mult, 2), round(laser_mult, 2))
        hit = self._band_cache.get(key)
        if hit is not None:
            return hit
        reach = 0.0
        for w in weapons:
            if w:
                reach = max(reach, self.weapon_range(w['kind'], w['stats']) * self.p.fire.range_frac)
        top = min(reach, self.view_cap(st))
        radius = st.get('radius', self.cfg['mech']['radius'])
        floor = q.lo_radii * radius
        grid = []
        d = max(q.band_step_m, 1.0)
        while d <= top:
            mine, kind = self.my_dps_at(weapons, d, cubes, laser_mult)
            theirs = max(self.enemy_dps_at(holds, d, enemy_cubes, enemy_mult), 1e-06)
            grid.append((d, mine, kind, mine / theirs))
            d += q.band_step_m
        best_mine = max((g[1] for g in grid), default=0.0)
        viable = [g for g in grid if g[1] >= q.viable_frac * best_mine and g[1] > 0 and (g[0] >= floor)]
        if not viable:
            out = Band(floor, max(top, floor), max(top, floor), 0.0, '', False)
        else:
            ok = [g for g in viable if g[3] >= q.band_ratio]
            if ok:
                arg = max(ok, key=lambda g: g[3])
                lo = hi = arg[0]
                grid_d = {g[0]: g for g in ok}
                while lo - q.band_step_m in grid_d:
                    lo -= q.band_step_m
                while hi + q.band_step_m in grid_d:
                    hi += q.band_step_m
                hold = min(hi, max(lo, lo + (hi - lo) * q.hold_pos))
                at = grid_d.get(round(hold / q.band_step_m) * q.band_step_m, arg)
                out = Band(lo, hi, hold, at[3], at[2], True)
            else:
                arg = max(viable, key=lambda g: (round(g[3], 1), g[0]))
                out = Band(arg[0], arg[0], arg[0], arg[3], arg[2], False)
        if len(self._band_cache) > 64:
            self._band_cache.clear()
        self._band_cache[key] = out
        return out

    def run_range_m(self, energy: float, st: dict | None=None) -> float:
        m = st or self.cfg['mech']
        return m.get('boost_speed', self.cfg['mech']['boost_speed']) * energy / max(m.get('boost_drain', self.cfg['mech']['boost_drain']), 1e-06)

    def climb_m(self, energy: float, st: dict | None=None) -> float:
        m = {**self.cfg['mech'], **(st or {})}
        hop = m['jump_speed'] ** 2 / (2.0 * m['gravity'])
        t = energy / max(m['jet_drain'], 1e-06)
        net = max(m['jet_accel'] - m['gravity'], 1e-06)
        vmax = m['max_rise_speed']
        ramp = vmax / net
        rise = 0.5 * net * t * t if t < ramp else vmax * t - 0.5 * vmax * ramp
        return hop + rise

    def jump_only_climb_m(self, st: dict | None=None) -> float:
        m = {**self.cfg['mech'], **(st or {})}
        return m['step_up'] + m['jump_speed'] ** 2 / (2.0 * m['gravity'])

    def travel_time(self, dist: float, energy: float, st: dict | None=None, sprint: bool=True) -> float:
        m = {**self.cfg['mech'], **(st or {})}
        walk = max(m['walk_speed'], 1e-06)
        if not sprint or energy <= 0:
            return dist / walk
        far = self.run_range_m(energy, m)
        if dist <= far:
            return dist / max(m['boost_speed'], 1e-06)
        return energy / max(m['boost_drain'], 1e-06) + (dist - far) / walk

    def can_cross(self, need_m: float, exposure_s: float, energy: float, st: dict | None=None) -> tuple[bool, bool, float]:
        walk_t = self.travel_time(need_m, 0.0, st, sprint=False)
        run_t = self.travel_time(need_m, energy, st)
        return (walk_t <= exposure_s, run_t <= exposure_s, exposure_s - run_t)

    def charge_step_time(self, dist: float, st: dict | None=None) -> float:
        m = {**self.cfg['mech'], **(st or {})}
        return dist / max(m['walk_speed'] * m['charge_move_mult'], 1e-06)
