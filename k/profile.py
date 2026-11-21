from __future__ import annotations
import bisect
import math
from collections import deque
from dataclasses import dataclass, field
from .derive import Derived, interp
from .memory import Memory
from .params import Params
from .util import angdiff, unit2
MAX_BANDS = 8

@dataclass
class EnemyProfile:
    id: int
    first_seen: float = 0.0
    last_seen: float = 0.0
    shots: list = field(default_factory=lambda: [0.0] * MAX_BANDS)
    hits: list = field(default_factory=lambda: [0.0] * MAX_BANDS)
    closing_ema: float = 0.0
    closing_n: int = 0
    weapons: set = field(default_factory=set)
    charges: deque = field(default_factory=lambda: deque(maxlen=8))
    charge_start: float | None = None
    last_aim_yaw: float = 0.0
    last_aim_t: float = -1000000000.0
    faces_us: bool = False
    busy_until: float = -1000000000.0
    burst: int = 0
    last_shot_t: float = -1000000000.0
    empty_until: float = -1000000000.0
    our_laser_shots: float = 0.0
    our_laser_hits: float = 0.0

class ProfileBook:

    def __init__(self, cfg: dict, params: Params, derived: Derived):
        self.cfg = cfg
        self.p = params
        self.dv = derived
        self.profiles: dict[int, EnemyProfile] = {}
        self._seen_proj: deque = deque(maxlen=256)
        self._seen_set: set = set()
        self._hit_t = -1000000000.0
        self.shots_now: list = []

    def get(self, eid: int) -> EnemyProfile | None:
        return self.profiles.get(eid)

    def _make(self, eid: int, t: float) -> EnemyProfile:
        pr = self.profiles.get(eid)
        if pr is None:
            if len(self.profiles) >= int(self.p.profile.bound):
                oldest = min(self.profiles.values(), key=lambda r: r.last_seen)
                del self.profiles[oldest.id]
            pr = self.profiles[eid] = EnemyProfile(eid, first_seen=t, last_seen=t)
        return pr

    def band_of(self, dist: float, speed: float | None=None) -> int:
        q = self.p.derive
        eq = dist * q.ref_bullet_speed / max(speed or self.cfg['weapons']['gun']['speed'], 1e-06)
        return bisect.bisect(list(self.p.profile.band_edges_m), eq)

    def _prior(self, band: int) -> float:
        edges = list(self.p.profile.band_edges_m)
        lo = edges[band - 1] if band > 0 else 0.0
        hi = edges[band] if band < len(edges) else edges[-1] * 1.5
        return max(interp(self.p.derive.hit_table, 0.5 * (lo + hi)), 0.001)

    def hit_mult(self, eid: int, dist: float, speed: float | None=None) -> float:
        pr = self.profiles.get(eid)
        if pr is None:
            return 1.0
        b = min(self.band_of(dist, speed), MAX_BANDS - 1)
        n0 = self.p.profile.prior_n
        prior = self._prior(b)
        shots = pr.shots[b]
        hits = min(pr.hits[b], shots)
        est = (hits + prior * n0) / (shots + n0)
        return min(self.p.profile.mult_cap, max(1.0 / self.p.profile.mult_cap, est / prior))

    def laser_mult(self, eid: int) -> float:
        pr = self.profiles.get(eid)
        if pr is None:
            return 1.0
        n0 = self.p.profile.prior_n
        prior = self.p.derive.laser_hit
        est = (min(pr.our_laser_hits, pr.our_laser_shots) + prior * n0) / (pr.our_laser_shots + n0)
        return min(1.0 / max(prior, 0.001), max(0.2, est / prior))

    def closes(self, eid: int) -> bool:
        pr = self.profiles.get(eid)
        if pr is None or pr.closing_n < self.p.profile.closing_samples:
            return False
        return pr.closing_ema >= self.p.profile.closer_frac * self.cfg['mech']['walk_speed']

    def laser_always_full(self, eid: int) -> bool:
        pr = self.profiles.get(eid)
        if pr is None or len(pr.charges) < self.p.profile.laser_samples:
            return False
        full = self.cfg['weapons']['laser']['charge']
        return min(pr.charges) >= self.p.profile.laser_full_frac * full

    def charge_left(self, eid: int, t: float) -> float | None:
        pr = self.profiles.get(eid)
        if pr is None or pr.charge_start is None:
            return None
        return max(0.0, self.cfg['weapons']['laser']['charge'] - (t - pr.charge_start))

    def reloading(self, eid: int, t: float) -> bool:
        pr = self.profiles.get(eid)
        return bool(pr and t < pr.empty_until)

    def busy(self, eid: int, t: float) -> bool:
        pr = self.profiles.get(eid)
        return bool(pr and t < pr.busy_until)

    def last_facing(self, eid: int) -> tuple | None:
        pr = self.profiles.get(eid)
        return None if pr is None or pr.last_aim_t < 0 else (pr.last_aim_t, pr.last_aim_yaw, pr.faces_us)

    def our_shot(self, eid: int, kind: str) -> None:
        pr = self.profiles.get(eid)
        if pr is not None and kind == 'laser':
            pr.our_laser_shots += 1.0

    def update(self, obs: dict, mem: Memory) -> None:
        q = self.p.profile
        t = mem.t
        self.shots_now = []
        me = obs['self']
        mypos = me['pos']
        myvel = me['vel']
        center = (mypos[0], mypos[1], mypos[2] + me.get('stats', {}).get('height', self.cfg['mech']['height']) / 2.0)
        seen_now = [tr for tr in mem.tracks.values() if tr.kind == 'mech' and tr.listed]
        for tr in seen_now:
            pr = self._make(tr.id, t)
            pr.last_seen = t
            pr.weapons |= tr.weapons
            ux, uy = unit2(mypos[0] - tr.pos[0], mypos[1] - tr.pos[1])
            closing = (tr.vel[0] - myvel[0]) * ux + (tr.vel[1] - myvel[1]) * uy
            pr.closing_ema += q.closing_alpha * (closing - pr.closing_ema) if pr.closing_n else closing - pr.closing_ema
            pr.closing_n += 1
            bearing = math.atan2(mypos[1] - tr.pos[1], mypos[0] - tr.pos[0])
            pr.last_aim_yaw, pr.last_aim_t = (tr.aim[0], t)
            pr.faces_us = abs(angdiff(tr.aim[0], bearing)) <= math.radians(q.facing_deg)
            if tr.charging and pr.charge_start is None:
                pr.charge_start = t
            elif not tr.charging and pr.charge_start is not None:
                pr.charges.append(t - pr.charge_start)
                pr.charge_start = None
            pr.busy_until = max(pr.busy_until, t + q.busy_hold_s) if self._aims_at_other(tr, seen_now, mypos) else pr.busy_until
        for tr in mem.tracks.values():
            pr = self.profiles.get(tr.id)
            if pr is not None and (not tr.listed) and (pr.charge_start is not None) and (t - pr.last_seen > q.charge_lost_s):
                pr.charge_start = None
        self._projectiles(obs, mem, seen_now, center)
        self._beams(obs, mem, center)
        self._events(obs, mem)
        for pr in list(self.profiles.values()):
            if t - pr.last_seen > q.forget_s:
                del self.profiles[pr.id]

    def _aims_at_other(self, tr, seen: list, mypos) -> bool:
        q = self.p.profile
        tol = math.radians(q.busy_deg)
        to_us = math.atan2(mypos[1] - tr.pos[1], mypos[0] - tr.pos[0])
        if abs(angdiff(tr.aim[0], to_us)) <= tol:
            return False
        for o in seen:
            if o.id == tr.id:
                continue
            yaw = math.atan2(o.pos[1] - tr.pos[1], o.pos[0] - tr.pos[0])
            if abs(angdiff(tr.aim[0], yaw)) <= tol and math.hypot(o.pos[0] - tr.pos[0], o.pos[1] - tr.pos[1]) <= q.busy_range_m:
                return True
        return False

    def _projectiles(self, obs: dict, mem: Memory, seen: list, center) -> None:
        q = self.p.profile
        t = mem.t
        gun = self.cfg['weapons']['gun']
        for pj in obs.get('projectiles', [])[:int(q.max_per_tick)]:
            pid = int(pj['id'])
            if pid in self._seen_set:
                continue
            if len(self._seen_proj) == self._seen_proj.maxlen:
                self._seen_set.discard(self._seen_proj[0])
            self._seen_proj.append(pid)
            self._seen_set.add(pid)
            if pj.get('kind') != 'gun':
                continue
            vel, pos = (pj['vel'], pj['pos'])
            speed = math.sqrt(vel[0] ** 2 + vel[1] ** 2 + vel[2] ** 2)
            if speed < 1e-06:
                continue
            u = (vel[0] / speed, vel[1] / speed, vel[2] / speed)
            shooter, best = (None, 1000000000.0)
            for tr in seen:
                m = (tr.pos[0], tr.pos[1], tr.pos[2] + self.cfg['mech']['muzzle_height'])
                r = (pos[0] - m[0], pos[1] - m[1], pos[2] - m[2])
                along = r[0] * u[0] + r[1] * u[1] + r[2] * u[2]
                if along < 0 or along > speed * (self.dv.dt + q.attr_slack_s) + self.cfg['mech']['radius'] + 0.5:
                    continue
                lat = math.sqrt(max(r[0] ** 2 + r[1] ** 2 + r[2] ** 2 - along * along, 0.0))
                if lat < best and lat <= q.attr_lateral_m:
                    shooter, best = (tr, lat)
            if shooter is None:
                continue
            self.shots_now.append((shooter, pj, u))
            pr = self._make(shooter.id, t)
            if t - pr.last_shot_t > gun['cooldown'] * q.burst_gap_mult:
                pr.burst = 0
            pr.burst += 1
            pr.last_shot_t = t
            if pr.burst >= gun['mag']:
                pr.empty_until = t + gun['reload']
                pr.burst = 0
            rel = (center[0] - pos[0], center[1] - pos[1], center[2] - pos[2])
            tt = rel[0] * u[0] + rel[1] * u[1] + rel[2] * u[2]
            if tt < 0:
                continue
            miss = math.sqrt(max(rel[0] ** 2 + rel[1] ** 2 + rel[2] ** 2 - tt * tt, 0.0))
            if miss <= q.at_us_m:
                d = math.hypot(shooter.pos[0] - center[0], shooter.pos[1] - center[1])
                pr.shots[min(self.band_of(d, speed), MAX_BANDS - 1)] += 1.0

    def _beams(self, obs: dict, mem: Memory, center) -> None:
        q = self.p.profile
        for b in obs.get('beams', [])[:int(self.p.profile.max_per_tick)]:
            by = int(b['by'])
            pr = self.profiles.get(by)
            if pr is None or b.get('kind') != 'laser' or len(b['path']) < 2:
                continue
            a, e = (b['path'][0], b['path'][-1])
            d = (e[0] - a[0], e[1] - a[1], e[2] - a[2])
            n = math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
            if n < 1e-06:
                continue
            rel = (center[0] - a[0], center[1] - a[1], center[2] - a[2])
            tt = (rel[0] * d[0] + rel[1] * d[1] + rel[2] * d[2]) / n
            miss = math.sqrt(max(rel[0] ** 2 + rel[1] ** 2 + rel[2] ** 2 - tt * tt, 0.0))
            if miss <= q.at_us_m:
                pr.weapons.add('laser')

    def _events(self, obs: dict, mem: Memory) -> None:
        t = mem.t
        for e in obs.get('events', []):
            if e.get('type') == 'damaged' and e.get('by') is not None and (e.get('weapon') == 'gun'):
                pr = self.profiles.get(int(e['by']))
                tr = mem.tracks.get(int(e['by']))
                if pr is not None and tr is not None:
                    d = math.hypot(tr.pos[0] - mem.pos[0], tr.pos[1] - mem.pos[1])
                    b = min(self.band_of(d), MAX_BANDS - 1)
                    pr.hits[b] += 1.0
                    pr.shots[b] = max(pr.shots[b], pr.hits[b])
        for h in mem.hit_log:
            if h[0] > self._hit_t and h[3] == 'laser':
                pr = self.profiles.get(int(h[1]))
                if pr is not None:
                    pr.our_laser_hits += 1.0
        self._hit_t = t
