from __future__ import annotations
import math
from dataclasses import dataclass, field
from . import aim as aimmod
from .loot import rank_of
from .memory import Memory
from .params import Params
from .profile import ProfileBook
from .threat import Enemy, View
from .util import aim_error, aim_to, wrap

@dataclass
class WPlan:
    slot: int
    kind: str
    st: dict
    sol: aimmod.Solution | None
    tol: float
    in_range: bool
    ready: bool
    utility: float = -1.0

@dataclass
class FirePlan:
    aim: list
    fire: list = field(default_factory=lambda: [False, False])
    reload: bool = False
    primary: str | None = None
    note: str = ''

class Gunnery:

    def __init__(self, cfg: dict, params: Params, mem: Memory, nav, book: ProfileBook | None=None):
        self.cfg = cfg
        self.p = params
        self.mem = mem
        self.nav = nav
        self.book = book
        self.dt = cfg['match']['decide_every'] / cfg['match']['tick_hz']
        self._prev: list | None = None
        self._prev_target: int | None = None
        self._sweep_base = 0.0

    def observe(self, weapons: list) -> None:
        prev = self._prev
        self._prev = [dict(w) if w else None for w in weapons]
        if prev is None or self._prev_target is None:
            return
        for old, new in zip(prev, weapons, strict=False):
            if not old or not new or old['kind'] != new['kind']:
                continue
            if new['kind'] == 'gun' and new.get('ammo', 0) < old.get('ammo', 0):
                self.mem.record_shot(self._prev_target, 'gun')
            if new['kind'] == 'laser' and old.get('charge', 0) > 0 and (new.get('charge', 0) == 0):
                self.mem.record_shot(self._prev_target, 'laser')
                if self.book is not None:
                    self.book.our_shot(self._prev_target, 'laser')

    def idle_aim(self, v: View, move) -> list:
        q = self.p.sweep
        for d in reversed(self.mem.recent_damage(q.face_damage_s)):
            if d.by is not None and d.bearing is not None:
                return [d.bearing, 0.0]
        if math.hypot(move[0], move[1]) > q.move_eps:
            self._sweep_base = math.atan2(move[1], move[0])
        snd = self.mem.recent_sounds(q.sound_s, tuple(q.sound_kinds))
        base = snd[-1].bearing if snd else self._sweep_base
        return [wrap(base + math.radians(q.amp_deg) * math.sin(2.0 * math.pi * v.t / q.period_s)), 0.0]

    def _plan_weapon(self, v: View, slot: int, w: dict, e: Enemy) -> WPlan:
        q = self.p.fire
        kind, st = (w['kind'], w['stats'])
        origin_h = v.stats.get('muzzle_height', self.cfg['mech']['muzzle_height'])
        off = v.radius + q.barrel_extra_m
        vel = e.tr.vel
        sol, tol, in_range, ready = (None, q.min_tol_rad, False, False)
        if kind == 'gun':
            sol = aimmod.gun_solution(v.pos, origin_h, off, e.center, vel, st['speed'])
            tol = aimmod.hit_tolerance(e.d3, e.radius + self.cfg['projectile']['gun_radius'], q.gun_tol_frac, q.min_tol_rad, q.max_tol_rad)
            in_range = e.d3 <= st['range'] * q.range_frac
            ready = w.get('ammo', 1) > 0 and w.get('reload', 0) <= 0
        elif kind == 'laser':
            lead = q.laser_lead_s
            pt = (e.center[0] + vel[0] * lead, e.center[1] + vel[1] * lead, e.center[2] + vel[2] * lead)
            yaw, pitch = aim_to((v.pos[0], v.pos[1], v.pos[2] + origin_h), pt)
            sol = aimmod.Solution(yaw, pitch, 0.0, pt)
            tol = aimmod.hit_tolerance(e.d3, e.radius, q.laser_tol_frac, q.min_tol_rad, q.max_tol_rad)
            in_range = e.d3 <= st['range'] * q.range_frac
            ready = self.mem.t >= self.mem.shorted_until and (w.get('cooldown', 0) <= q.laser_ready_s or w.get('charge', 0) > 0)
        elif kind == 'lightning':
            yaw, pitch = aim_to((v.pos[0], v.pos[1], v.pos[2] + origin_h), e.center)
            sol = aimmod.Solution(yaw, pitch, 0.0, e.center)
            cone = math.radians(st.get('cone_deg', self.cfg['weapons']['lightning']['cone_deg']))
            tol = max(q.min_tol_rad, cone * q.cone_frac)
            in_range = e.d3 <= st['range'] * q.range_frac
            ready = v.me['energy'] >= q.lightning_min_energy
        elif kind == 'mortar':
            g = st.get('gravity', self.cfg['weapons']['mortar']['gravity'])
            tube = v.stats.get('height', self.cfg['mech']['height']) + 0.5
            sol = aimmod.mortar_solution(v.pos, tube, off, e.center, vel, st['speed'], g, v.stats.get('min_pitch', self.cfg['mech']['min_pitch']), v.stats.get('max_pitch', self.cfg['mech']['max_pitch']), st.get('ttl', self.cfg['weapons']['mortar']['ttl']), high=q.mortar_high_arc)
            splash = st.get('splash_radius', self.cfg['weapons']['mortar']['splash_radius'])
            tol = aimmod.hit_tolerance(e.d, splash, q.mortar_tol_frac, q.min_tol_rad, q.max_tol_rad)
            in_range = sol is not None and q.mortar_min_range_m <= e.d <= st['speed'] ** 2 / g * q.mortar_range_frac
            ready = w.get('cooldown', 0) <= q.mortar_ready_s
        plan = WPlan(slot, kind, st, sol, tol, in_range and sol is not None, ready)
        charging = kind == 'laser' and w.get('charge', 0) > 0
        if plan.in_range and (ready or charging):
            order = list(self.p.weapons.rank)
            plan.utility = float(len(order) - rank_of(kind, self.p))
            if kind == 'lightning' and 'laser' in e.holds:
                plan.utility += q.tri_bonus
            if kind == 'laser' and 'mortar' in e.holds:
                plan.utility += q.tri_small
            if kind == 'mortar' and 'lightning' in e.holds:
                plan.utility += q.tri_small
            if charging:
                plan.utility += q.charge_stick
        return plan

    def _predicted_aim(self, v: View, cmd: list) -> list:
        s, m = (v.stats, self.cfg['mech'])
        return aimmod.step_aim(v.aim, cmd, s.get('turn_rate', m['turn_rate']), s.get('pitch_rate', m['pitch_rate']), self.p.fire.aim_lag_s, s.get('min_pitch', m['min_pitch']), s.get('max_pitch', m['max_pitch']))

    def plan(self, v: View, mode: str, target: Enemy | None, allowed: bool, move, laser_ctl: str | None=None) -> FirePlan:
        q = self.p.fire
        out = FirePlan(self.idle_aim(v, move))
        gun = v.slot_of('gun')
        if gun is not None:
            w = v.weapons[gun]
            quiet = not v.attackers and mode != 'fight'
            low = w.get('ammo', 0) < w['stats']['mag'] * q.reload_frac
            out.reload = w.get('reload', 0) <= 0 and (w.get('ammo', 0) <= 0 or (low and quiet))
        self._prev_target = target.id if target else None
        if target is None:
            return out
        plans = [self._plan_weapon(v, i, w, target) for i, w in enumerate(v.weapons) if w]
        usable = [p for p in plans if p.utility >= 0]
        primary = max(usable, key=lambda p: p.utility) if usable else None
        if primary is not None:
            out.aim = list(primary.sol.aim)
            out.primary = primary.kind
        else:
            eye = (v.pos[0], v.pos[1], v.pos[2] + v.stats.get('eye_height', self.cfg['mech']['eye_height']))
            yaw, pitch = aim_to(eye, target.center)
            out.aim = [yaw, pitch]
        if not allowed:
            return out
        pred = self._predicted_aim(v, out.aim)
        muzzle = (v.pos[0], v.pos[1], v.pos[2] + v.stats.get('muzzle_height', self.cfg['mech']['muzzle_height']))
        eye = (v.pos[0], v.pos[1], v.pos[2] + v.stats.get('eye_height', self.cfg['mech']['eye_height']))
        visible = target.fresh and target.in_view and self.nav.has_los(eye, target.center)
        clear = self.nav.shot_clear(muzzle, target.center)
        for p in plans:
            w = v.weapons[p.slot]
            if p.sol is None or not p.in_range:
                continue
            aligned = visible and clear and (aim_error(pred, p.sol.aim) <= p.tol)
            paused = self.mem.blind_paused(target.id, p.kind)
            if p.kind == 'laser':
                out.fire[p.slot] = self._laser(v, w, p, target, aligned, paused, visible, clear, laser_ctl)
            elif not p.ready or paused or (not aligned):
                continue
            elif p.kind == 'lightning':
                out.fire[p.slot] = self._lane_clear(v, target)
            else:
                out.fire[p.slot] = True
        return out

    def _laser(self, v: View, w: dict, p: WPlan, e: Enemy, aligned: bool, paused: bool, visible: bool, clear: bool=True, ctl: str | None=None) -> bool:
        q = self.p.fire
        laser = self.cfg['weapons']['laser']
        charge = w.get('charge', 0.0)
        full = p.st.get('charge', laser['charge'])
        threatened = e.charging and e.aimed
        auto_fire = full + laser['hold_s'] - self.dt - q.laser_slack_s
        if ctl == 'hold':
            if charge > 0:
                return charge < auto_fire
            return p.ready and (not paused) and (w.get('cooldown', 0) <= q.laser_start_cooldown_s)
        if ctl == 'wait' and charge <= 0:
            return False
        if charge > 0:
            if aligned and charge >= laser['min_charge'] and self.partial_kills(v, e, charge, full):
                return False
            extra = q.laser_max_extra_s if clear else laser['hold_s'] - self.dt - q.laser_slack_s
            release = aimmod.laser_release(charge, full, laser['min_charge'], aligned, threatened, q.laser_slack_s, q.laser_early_frac, extra)
            return not release
        if paused or not p.ready or w.get('cooldown', 0) > q.laser_start_cooldown_s:
            return False
        known = visible or e.age <= q.precharge_age_s
        stats = v.stats
        m = self.cfg['mech']
        turn = aimmod.slew_time(v.aim, p.sol.aim, stats.get('turn_rate', m['turn_rate']), stats.get('pitch_rate', m['pitch_rate']), stats.get('min_pitch', m['min_pitch']), stats.get('max_pitch', m['max_pitch']))
        return known and (aligned or turn <= full * q.precharge_slew_frac)

    def laser_damage_at(self, v: View, charge: float, full: float, st: dict) -> float:
        laser = self.cfg['weapons']['laser']
        frac = laser['min_frac'] + (1.0 - laser['min_frac']) * min(max(charge / max(full, 1e-06), 0.0), 1.0)
        c = self.cfg['cubes']
        mult = 1.0 + c['damage_per_cube'] * min(max(v.me.get('cubes', 0), 0), c['stat_cap'])
        return st.get('damage', laser['damage']) * mult * frac

    def partial_kills(self, v: View, e: Enemy, charge: float, full: float) -> bool:
        hp = e.tr.hp
        if hp is None or e.prop or charge >= full:
            return False
        laser = next((w for w in v.weapons if w and w['kind'] == 'laser'), None)
        st = laser['stats'] if laser else self.cfg['weapons']['laser']
        return self.p.fire.laser_partial_conf * self.laser_damage_at(v, charge, full, st) >= hp

    def _lane_clear(self, v: View, target: Enemy) -> bool:
        cone = math.radians(self.cfg['weapons']['lightning']['cone_deg'])
        yaw = v.aim[0]
        for dr in v.drones:
            if dr.listed and abs(wrap(math.atan2(dr.pos[1] - v.pos[1], dr.pos[0] - v.pos[0]) - yaw)) <= cone and (math.hypot(dr.pos[0] - v.pos[0], dr.pos[1] - v.pos[1]) < target.d):
                return False
        for c in v.obs.get('crates', []):
            dx, dy = (c['pos'][0] - v.pos[0], c['pos'][1] - v.pos[1])
            if abs(wrap(math.atan2(dy, dx) - yaw)) <= cone and math.hypot(dx, dy) < target.d:
                return False
        return True
