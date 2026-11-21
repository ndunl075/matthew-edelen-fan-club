from __future__ import annotations
import math
from collections import deque
from .memory import Memory
from .params import Params
from .profile import ProfileBook
from .util import angdiff
MODES = ('unknown', 'practice', 'ffa')

class ModeDetector:

    def __init__(self, cfg: dict, params: Params):
        self.cfg = cfg
        self.p = params
        self.mode = 'unknown'
        self.ffa = 0.0
        self.practice = 0.0
        self.notes: deque = deque(maxlen=6)
        self.start_alive: int | None = None
        self._deaths_seen = 0
        self._last_change_t = 0.0
        self._counted_unexplained = 0
        self._straight: dict[int, float] = {}
        self._counted_practice: set = set()
        self._hp: dict[int, tuple] = {}
        self._counted_hurt: dict[int, float] = {}
        self._counted_other: set = set()
        phases = cfg['zone']['phases']
        self.first_zone_s = float(phases[0]['hold_s']) if phases else 0.0

    @property
    def label(self) -> str:
        note = f' {self.notes[-1]}' if self.notes else ''
        return f'{self.mode}[f{self.ffa:.0f}/p{self.practice:.0f}]{note}'

    def _note(self, text: str, t: float) -> None:
        self.notes.append(f'{text}@{t:.0f}')

    def update(self, obs: dict, mem: Memory, book: ProfileBook) -> str:
        q = self.p.mode
        t = mem.t
        alive = int(obs.get('alive', 0))
        if self.start_alive is None:
            self.start_alive = max(alive, int(self.cfg['match'].get('mechs', alive)))
        deaths = self.start_alive - alive
        if deaths != self._deaths_seen:
            self._last_change_t = t
            self._deaths_seen = deaths
        self._unexplained_deaths(t, deaths, mem)
        self._shots_at_others(obs, mem, book, t)
        self._beams_at_others(obs, mem, t)
        self._hp_drops(obs, mem, t)
        self._hunters(obs, mem, deaths, t)
        if self.ffa >= q.ffa_score:
            self.mode = 'ffa'
        elif self.practice >= q.practice_score and self.ffa <= 0.0 and (self.mode != 'ffa'):
            self.mode = 'practice'
        elif self.mode != 'ffa':
            self.mode = 'unknown'
        return self.mode

    def _unexplained_deaths(self, t: float, deaths: int, mem: Memory) -> None:
        q = self.p.mode
        if t - self._last_change_t < q.confirm_s:
            return
        unexplained = deaths - mem.kills
        if unexplained > self._counted_unexplained:
            new = unexplained - self._counted_unexplained
            self._counted_unexplained = unexplained
            if self._last_change_t < self.first_zone_s:
                self.ffa += q.ffa_score * new
                self._note('death before the zone', t)
            else:
                self.ffa += q.ffa_score / max(q.ffa_deaths, 1) * new
                self._note('death not ours', t)

    def _visible_mechs(self, mem: Memory) -> list:
        return [tr for tr in mem.tracks.values() if tr.kind == 'mech' and tr.listed]

    def _shots_at_others(self, obs: dict, mem: Memory, book: ProfileBook, t: float) -> None:
        q = self.p.mode
        mechs = self._visible_mechs(mem)
        me = obs['self']['pos']
        h = self.cfg['mech']['height'] / 2.0
        center = (me[0], me[1], me[2] + h)
        for shooter, pj, u in book.shots_now:
            if shooter.id in self._counted_other:
                continue
            pos = pj['pos']
            rel = (center[0] - pos[0], center[1] - pos[1], center[2] - pos[2])
            tt = rel[0] * u[0] + rel[1] * u[1] + rel[2] * u[2]
            miss_us = math.sqrt(max(rel[0] ** 2 + rel[1] ** 2 + rel[2] ** 2 - tt * tt, 0.0)) if tt >= 0 else 1000000000.0
            if miss_us <= q.us_far_m:
                continue
            for o in mechs:
                if o.id == shooter.id:
                    continue
                r = (o.pos[0] - pos[0], o.pos[1] - pos[1], o.pos[2] + h - pos[2])
                ot = r[0] * u[0] + r[1] * u[1] + r[2] * u[2]
                if ot < 0:
                    continue
                miss = math.sqrt(max(r[0] ** 2 + r[1] ** 2 + r[2] ** 2 - ot * ot, 0.0))
                if miss <= q.other_miss_m:
                    self._counted_other.add(shooter.id)
                    self.ffa += q.ffa_score
                    self._note(f'{shooter.id} shot at {o.id}', t)
                    break

    def _beams_at_others(self, obs: dict, mem: Memory, t: float) -> None:
        q = self.p.mode
        mechs = {tr.id: tr for tr in self._visible_mechs(mem)}
        for b in obs.get('beams', [])[:int(self.p.profile.max_per_tick)]:
            by = int(b['by'])
            if by not in mechs or not b.get('path'):
                continue
            end = b['path'][-1]
            for o in mechs.values():
                if o.id == by:
                    continue
                if math.hypot(end[0] - o.pos[0], end[1] - o.pos[1]) <= q.other_miss_m and abs(end[2] - (o.pos[2] + self.cfg['mech']['height'] / 2.0)) <= self.cfg['mech']['height']:
                    if by not in self._counted_other:
                        self._counted_other.add(by)
                        self.ffa += q.ffa_score
                        self._note(f'{by} beamed {o.id}', t)
                    break

    def _hp_drops(self, obs: dict, mem: Memory, t: float) -> None:
        q = self.p.mode
        zone = obs.get('zone', {})
        drones = mem.drones(q.drone_age_s)
        for tr in self._visible_mechs(mem):
            if tr.hp is None:
                continue
            prev = self._hp.get(tr.id)
            self._hp[tr.id] = (tr.hp, t)
            if prev is None or tr.hp >= prev[0] - q.hp_drop_min or t - prev[1] > q.hp_gap_s:
                continue
            if mem.hit_recently(tr.id, q.hp_gap_s + 0.2):
                continue
            if zone and math.hypot(tr.pos[0] - zone['center'][0], tr.pos[1] - zone['center'][1]) > zone['radius']:
                continue
            if any((math.hypot(d.pos[0] - tr.pos[0], d.pos[1] - tr.pos[1]) <= q.drone_clear_m for d in drones)):
                continue
            if t - self._counted_hurt.get(tr.id, -1000000000.0) < q.hp_gap_s * 3:
                continue
            self._counted_hurt[tr.id] = t
            self.ffa += q.hp_drop_score
            self._note(f'{tr.id} hurt by others', t)

    def _hunters(self, obs: dict, mem: Memory, deaths: int, t: float) -> None:
        q = self.p.mode
        me = obs['self']
        pos = me['pos']
        walk = me.get('stats', {}).get('walk_speed', self.cfg['mech']['walk_speed'])
        need = q.closing_frac * walk
        for tr in self._visible_mechs(mem):
            speed = math.hypot(tr.vel[0], tr.vel[1])
            to_us = math.atan2(pos[1] - tr.pos[1], pos[0] - tr.pos[0])
            heading = math.atan2(tr.vel[1], tr.vel[0])
            if speed >= need and abs(angdiff(heading, to_us)) <= math.radians(q.straight_deg) and (math.hypot(pos[0] - tr.pos[0], pos[1] - tr.pos[1]) > 0):
                since = self._straight.setdefault(tr.id, t)
                if t - since >= q.straight_s and tr.id not in self._counted_practice and (deaths - mem.kills <= 0):
                    self._counted_practice.add(tr.id)
                    self.practice += 1.0
                    self._note(f'{tr.id} came straight', t)
            else:
                self._straight.pop(tr.id, None)
