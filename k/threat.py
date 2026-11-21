from __future__ import annotations
import math
from dataclasses import dataclass, field
from . import zone as zonemod
from .memory import Memory, Track
from .params import Params
from .util import angdiff, angle_between, dir3, dist2, dist3, unit2

@dataclass
class Enemy:
    tr: Track
    pos: list
    center: tuple
    radius: float
    d: float
    d3: float
    bearing: float
    age: float
    fresh: bool
    in_view: bool
    sees_us: bool
    aim_off: float
    aimed: bool
    unaware: bool
    charging: bool
    holds: set
    hp_frac: float
    closing: float
    dmg_recent: float
    engaged: bool
    attacking: bool
    threat: float = 0.0
    advantage: float = 1.0
    prop: bool = False

    @property
    def id(self) -> int:
        return self.tr.id

@dataclass
class View:
    obs: dict
    t: float
    me: dict
    pos: list
    vel: list
    center: tuple
    aim: list
    stats: dict
    hp_frac: float
    energy_frac: float
    weapons: list
    walk: float
    radius: float
    zone: zonemod.ZonePlan
    enemies: list = field(default_factory=list)
    attackers: list = field(default_factory=list)
    drones: list = field(default_factory=list)
    props: list = field(default_factory=list)
    laser_threat: bool = False

    def held(self) -> set:
        return {w['kind'] for w in self.weapons if w}

    def slot_of(self, kind: str) -> int | None:
        for i, w in enumerate(self.weapons):
            if w and w['kind'] == kind:
                return i
        return None

class Assessor:

    def __init__(self, cfg: dict, params: Params, mem: Memory):
        self.cfg = cfg
        self.p = params
        self.mem = mem
        self.phases = cfg['zone']['phases']

    def weapon_dps(self, kind: str, st: dict) -> float:
        if kind == 'gun':
            return st['damage'] / max(st['cooldown'], 0.001)
        if kind == 'laser':
            return st['damage'] / (st['cooldown'] + st.get('charge', self.cfg['weapons']['laser']['charge']))
        if kind == 'mortar':
            return (st['damage'] + self.p.power.splash_weight * st.get('splash', 0.0)) / max(st['cooldown'], 0.001)
        if kind == 'lightning':
            return st['damage'] / max(st['cooldown'], 0.001)
        return 0.0

    def my_dps(self, v: View) -> float:
        total = sum((self.weapon_dps(w['kind'], w['stats']) for w in v.weapons if w))
        return total * self.p.power.hit_rate

    def enemy_dps(self, holds: set, cubes: int) -> float:
        kinds = holds or {'gun'}
        total = sum((self.weapon_dps(k, self.cfg['weapons'][k]) for k in kinds if k in self.cfg['weapons']))
        return total * self.p.power.hit_rate * (1.0 + self.cfg['cubes']['damage_per_cube'] * cubes)

    def build(self, obs: dict) -> View:
        me = obs['self']
        t = float(obs['t'])
        st = me['stats']
        pos = me['pos']
        height = st.get('height', self.cfg['mech']['height'])
        v = View(obs=obs, t=t, me=me, pos=pos, vel=me['vel'], center=(pos[0], pos[1], pos[2] + height / 2.0), aim=me['aim'], stats=st, hp_frac=me['hp'] / max(me['max_hp'], 1.0), energy_frac=me['energy'] / max(me['max_energy'], 1.0), weapons=me['weapons'], walk=st.get('walk_speed', self.cfg['mech']['walk_speed']), radius=st.get('radius', self.cfg['mech']['radius']), zone=zonemod.plan(obs['zone'], t, pos, st.get('walk_speed', self.cfg['mech']['walk_speed']), self.phases, self.p, bool(me.get('in_zone', True)), float(self.cfg['match']['duration_s'])))
        v.laser_threat = self.mem.laser_threat()
        dps_me = self.my_dps(v)
        for tr in self.mem.mechs(self.p.threat.max_age_s):
            v.enemies.append(self._enemy(v, tr, dps_me))
        v.enemies.sort(key=lambda e: e.d3)
        v.attackers = [e for e in v.enemies if e.attacking]
        for e in v.enemies:
            e.threat = self._threat(e)
        v.attackers.sort(key=lambda e: -e.threat)
        v.drones = self.mem.drones()
        v.props = self._props(v)
        return v

    def _props(self, v: View) -> list:
        f = self.p.fire
        cfg = self.cfg
        out: list = []
        if f.shoot_drones:
            for tr in v.drones:
                if tr.listed:
                    out.append(self._prop(v, tr.id, tr.pos, tr.vel, cfg['npc']['radius'], cfg['npc']['height'], tr))
        if f.shoot_crates:
            for c in v.obs.get('crates', []):
                out.append(self._prop(v, c['id'], c['pos'], [0.0, 0.0, 0.0], cfg['crates']['radius'], cfg['crates']['height'], None))
        out = [e for e in out if e.d3 <= f.prop_range_m]
        out.sort(key=lambda e: e.d3)
        return out

    def _prop(self, v: View, pid: int, pos, vel, radius: float, height: float, tr) -> Enemy:
        tr = tr or Track(int(pid), 'prop', list(pos), list(vel), [0.0, 0.0], first_seen=v.t, last_seen=v.t)
        center = (pos[0], pos[1], pos[2] + height / 2.0)
        return Enemy(tr, list(pos), center, radius, dist2(pos, v.pos), dist3(v.center, center), math.atan2(pos[1] - v.pos[1], pos[0] - v.pos[0]), 0.0, True, True, False, math.pi, False, False, False, set(), 1.0, 0.0, 0.0, False, False, prop=True)

    def _enemy(self, v: View, tr: Track, dps_me: float) -> Enemy:
        q, cfg = (self.p.threat, self.cfg)
        mem = self.mem
        pos = mem.est_pos(tr)
        scale = 1.0 + cfg['cubes']['size_per_cube'] * tr.cubes
        h, r = (cfg['mech']['height'] * scale, cfg['mech']['radius'] * scale)
        center = (pos[0], pos[1], pos[2] + h / 2.0)
        d3 = dist3(v.center, center)
        d = dist2(pos, v.pos)
        bearing = math.atan2(pos[1] - v.pos[1], pos[0] - v.pos[0])
        age = mem.t - tr.last_seen
        vis = cfg['vision']
        near = vis['near_range'] + v.stats.get('near_range_bonus', 0.0) + q.view_margin_m
        rng = vis['range'] + v.stats.get('vision_bonus', 0.0)
        half_fov = math.radians(vis['fov_deg']) / 2.0 + math.radians(q.view_margin_deg)
        in_view = d3 <= near or (d3 <= rng and abs(angdiff(bearing, v.aim[0])) <= half_fov)
        to_us = bearing + math.pi
        sees_us = d3 <= vis['near_range'] + q.view_margin_m or (d3 <= vis['range'] and abs(angdiff(to_us, tr.aim[0])) <= half_fov)
        eye = (pos[0], pos[1], pos[2] + cfg['mech']['muzzle_height'] * scale)
        line = (v.center[0] - eye[0], v.center[1] - eye[1], v.center[2] - eye[2])
        aim_off = angle_between(dir3(tr.aim[0], tr.aim[1]), line)
        tol = math.atan2(v.radius + q.aim_margin_m, max(d3, 1.0)) + math.radians(q.aim_slack_deg)
        aimed = aim_off <= tol and sees_us
        unaware = abs(angdiff(tr.aim[0], to_us)) > math.radians(q.unaware_deg)
        ux, uy = unit2(v.pos[0] - pos[0], v.pos[1] - pos[1])
        closing = (tr.vel[0] - v.vel[0]) * ux + (tr.vel[1] - v.vel[1]) * uy
        dmg = mem.damage_by(q.engage_window_s).get(tr.id, 0.0)
        mutual = mem.hit_recently(tr.id, q.engage_window_s)
        recent = age <= q.attacker_age_s
        engaged = dmg > 0 or (recent and (mutual or (aimed and d3 <= q.aim_engage_range_m and bool(tr.weapons))))
        attacking = engaged or (recent and tr.listed and sees_us and (closing >= q.closing_mps) and (d3 <= q.closing_range_m))
        hp_frac = tr.hp / tr.max_hp if tr.hp is not None and tr.max_hp else 1.0
        e = Enemy(tr, pos, center, r, d, d3, bearing, age, tr.listed, in_view, sees_us, aim_off, aimed, unaware and dmg <= 0, tr.charging and tr.listed, set(tr.weapons), hp_frac, closing, dmg, engaged, attacking)
        mult = cfg['mech']['rear_mult'] if e.unaware else 1.0
        my = max(dps_me * mult * (1.0 + cfg['cubes']['damage_per_cube'] * v.me.get('cubes', 0)), 1e-06)
        theirs = max(self.enemy_dps(e.holds, tr.cubes), 1e-06)
        e_hp = tr.hp if tr.hp is not None else cfg['mech']['hp']
        e.advantage = v.me['hp'] / theirs / max(e_hp / my, 1e-06)
        return e

    def _threat(self, e: Enemy) -> float:
        q = self.p.threat
        s = q.w_damage * min(e.dmg_recent, q.damage_cap) / q.damage_cap
        s += q.w_aimed * e.aimed + q.w_charging * (e.charging and e.aimed) + q.w_attacking * e.attacking
        s += q.w_laser * ('laser' in e.holds) + q.w_near * max(0.0, 1.0 - e.d3 / q.near_ref_m)
        s -= q.w_stale * min(e.age, q.stale_cap_s)
        return s
