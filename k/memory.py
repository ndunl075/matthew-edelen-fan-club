from __future__ import annotations
import math
import random
from collections import deque
from dataclasses import dataclass, field
from .params import Params
from .util import dist2
FIRST_CRATE_ID = 1000

@dataclass
class Track:
    id: int
    kind: str
    pos: list
    vel: list
    aim: list
    hp: float | None = None
    max_hp: float | None = None
    cubes: int = 0
    weapons: set = field(default_factory=set)
    charging: bool = False
    first_seen: float = 0.0
    last_seen: float = 0.0
    listed: bool = True

    def age(self, t: float) -> float:
        return t - self.last_seen

@dataclass
class DamageRec:
    t: float
    amount: float
    weapon: str
    bearing: float | None
    by: int | None
    rear: bool

@dataclass
class SoundRec:
    t: float
    kind: str
    bearing: float
    dist: str
    x: float
    y: float
    ex: float
    ey: float

@dataclass
class PickupRec:
    id: int
    kind: str
    pos: list
    last_seen: float

@dataclass
class ItemRec:
    id: int
    pos: list
    hp: float | None
    last_seen: float

@dataclass
class BeamRec:
    t: float
    by: int
    kind: str
    path: list

class Memory:

    def __init__(self, hello: dict, params: Params):
        self.p = params
        self.cfg = hello['config']
        self.slot = int(hello['slot'])
        self.rng = random.Random(int(hello.get('seed', 0)))
        self.t = 0.0
        self.tick = 0
        self.pos = [0.0, 0.0, 0.0]
        self.tracks: dict[int, Track] = {}
        self.damage_log: deque[DamageRec] = deque(maxlen=64)
        self.hit_log: deque[tuple] = deque(maxlen=64)
        self.sounds: list[SoundRec] = []
        self.pickups: dict[int, PickupRec] = {}
        self.cubes: dict[int, ItemRec] = {}
        self.crates: dict[int, ItemRec] = {}
        self.drops: list[dict] = []
        self.projectiles: list[dict] = []
        self.beams: list[BeamRec] = []
        self.take_block: dict[int, float] = {}
        self.kind_block: dict[str, float] = {}
        self.last_combat_t = -1000000000.0
        self.last_hurt_t = -1000000000.0
        self.shorted_until = -1000000000.0
        self.kills = 0
        self._blind: dict[tuple, list] = {}

    def update(self, obs: dict) -> None:
        self.t = float(obs['t'])
        self.tick = int(obs['tick'])
        self.pos = list(obs['self']['pos'])
        self._events(obs.get('events', []))
        self._mechs(obs.get('mechs', []))
        self._sounds(obs.get('sounds', []))
        self._items(obs)
        self._beams(obs.get('beams', []))
        self.drops = list(obs.get('drops', []))
        self.projectiles = list(obs.get('projectiles', []))
        self._prune()

    def _events(self, events: list) -> None:
        t = self.t
        for e in events:
            kind = e.get('type')
            if kind == 'damaged':
                by = e.get('by')
                self.damage_log.append(DamageRec(t, float(e['amount']), e.get('weapon', ''), e.get('bearing'), None if by is None else int(by), bool(e.get('rear', False))))
                self.last_hurt_t = t
                if by is not None:
                    self.last_combat_t = t
            elif kind == 'hit':
                tgt = int(e['target'])
                if tgt < FIRST_CRATE_ID:
                    self.last_combat_t = t
                self.hit_log.append((t, tgt, float(e['amount']), e.get('weapon', '')))
                self.record_hit(tgt)
            elif kind == 'kill':
                self.kills += 1
                self.tracks.pop(int(e['target']), None)
            elif kind == 'took':
                self.pickups.pop(int(e['id']), None)
            elif kind == 'dropped':
                self.pickups[int(e['id'])] = PickupRec(int(e['id']), e['kind'], list(self.pos), t)
                self.take_block[int(e['id'])] = t + self.p.memory.dropped_block_s
            elif kind == 'take_failed':
                pid = int(e['id'])
                if e.get('reason') == 'gone':
                    self.pickups.pop(pid, None)
                else:
                    self.take_block[pid] = t + self.p.memory.take_block_s
            elif kind == 'effect' and e.get('effect') == 'short':
                self.shorted_until = t + float(self.cfg['weapons']['lightning']['short_s'])

    def _mechs(self, mechs: list) -> None:
        t = self.t
        for tr in self.tracks.values():
            tr.listed = False
        for m in mechs:
            mid = int(m['id'])
            tr = self.tracks.get(mid)
            if tr is None:
                tr = Track(mid, m.get('kind', 'mech'), list(m['pos']), list(m['vel']), list(m['aim']), first_seen=t)
                self.tracks[mid] = tr
            tr.pos, tr.vel, tr.aim = (list(m['pos']), list(m['vel']), list(m['aim']))
            tr.hp = m.get('hp', tr.hp)
            tr.max_hp = m.get('max_hp', tr.max_hp)
            tr.cubes = int(m.get('cubes', tr.cubes) or 0)
            if 'weapons' in m:
                tr.weapons = set(m['weapons'])
            tr.charging = bool(m.get('charging', False))
            tr.last_seen = t
            tr.listed = True

    def _sounds(self, sounds: list) -> None:
        dists = self.p.memory.sound_dist_m
        x, y = (self.pos[0], self.pos[1])
        for s in sounds:
            d = getattr(dists, s['dist'], dists.mid)
            b = float(s['bearing'])
            self.sounds.append(SoundRec(self.t - float(s.get('age', 0.0)), s['kind'], b, s['dist'], x, y, x + d * math.cos(b), y + d * math.sin(b)))

    def _items(self, obs: dict) -> None:
        t = self.t
        x, y = (self.pos[0], self.pos[1])
        listed = set()
        for pk in obs.get('pickups', []):
            listed.add(int(pk['id']))
            self.pickups[int(pk['id'])] = PickupRec(int(pk['id']), pk['kind'], list(pk['pos']), t)
        for pid in [i for i, r in self.pickups.items() if i not in listed and dist2(r.pos, (x, y)) < self.p.memory.pickup_forget_m and (r.last_seen < t)]:
            del self.pickups[pid]
        for store, key, forget in ((self.cubes, 'cubes', self.p.memory.cube_forget_m), (self.crates, 'crates', self.p.memory.crate_forget_m)):
            seen = set()
            for it in obs.get(key, []):
                seen.add(int(it['id']))
                store[int(it['id'])] = ItemRec(int(it['id']), list(it['pos']), it.get('hp'), t)
            for iid in [i for i, r in store.items() if i not in seen and dist2(r.pos, (x, y)) < forget and (r.last_seen < t)]:
                del store[iid]

    def _beams(self, beams: list) -> None:
        for b in beams:
            by = int(b['by'])
            self.beams.append(BeamRec(self.t - float(b.get('age', 0.0)), by, b['kind'], list(b['path'])))
            tr = self.tracks.get(by)
            if tr is not None:
                tr.weapons.add(b['kind'])

    def _prune(self) -> None:
        t, p = (self.t, self.p.memory)
        self.sounds = [s for s in self.sounds if t - s.t <= p.sound_keep_s]
        self.beams = [b for b in self.beams if t - b.t <= p.beam_keep_s]
        for tid in [i for i, tr in self.tracks.items() if t - tr.last_seen > p.track_keep_s]:
            del self.tracks[tid]
        for pid in [i for i, until in self.take_block.items() if until < t]:
            del self.take_block[pid]
        for k in [k for k, until in self.kind_block.items() if until < t]:
            del self.kind_block[k]

    def est_pos(self, tr: Track, t: float | None=None) -> list:
        t = self.t if t is None else t
        age = max(0.0, t - tr.last_seen)
        if age <= 0.0:
            return list(tr.pos)
        a = min(age, self.p.memory.stale_drift_s)
        return [tr.pos[0] + tr.vel[0] * a, tr.pos[1] + tr.vel[1] * a, tr.pos[2]]

    def mechs(self, max_age: float | None=None) -> list[Track]:
        max_age = self.p.memory.track_keep_s if max_age is None else max_age
        return [tr for tr in self.tracks.values() if tr.kind == 'mech' and self.t - tr.last_seen <= max_age]

    def drones(self, max_age: float | None=None) -> list[Track]:
        max_age = self.p.memory.drone_keep_s if max_age is None else max_age
        return [tr for tr in self.tracks.values() if tr.kind == 'npc' and self.t - tr.last_seen <= max_age]

    def recent_damage(self, window: float) -> list[DamageRec]:
        return [d for d in self.damage_log if self.t - d.t <= window]

    def damage_by(self, window: float) -> dict[int, float]:
        out: dict[int, float] = {}
        for d in self.recent_damage(window):
            if d.by is not None:
                out[d.by] = out.get(d.by, 0.0) + d.amount
        return out

    def recent_sounds(self, window: float, kinds: tuple | None=None) -> list[SoundRec]:
        return [s for s in self.sounds if self.t - s.t <= window and (kinds is None or s.kind in kinds)]

    def hit_recently(self, target: int, window: float) -> bool:
        return any((h[1] == target and self.t - h[0] <= window for h in self.hit_log))

    def laser_threat(self) -> bool:
        p = self.p.loot
        me = self.pos
        for tr in self.mechs(p.laser_memory_s):
            if 'laser' in tr.weapons and dist2(self.est_pos(tr), me) <= p.laser_threat_range_m:
                return True
        for s in self.recent_sounds(p.laser_sound_s, ('laser', 'laser_charge')):
            if s.dist in ('near', 'mid'):
                return True
        return any((self.t - b.t <= p.laser_memory_s and b.kind == 'laser' for b in self.beams))

    def record_shot(self, target: int, kind: str) -> None:
        rec = self._blind.setdefault((target, kind), [0, -1000000000.0])
        rec[0] += 1
        if rec[0] >= getattr(self.p.fire.blind_shots, kind, self.p.fire.blind_shots.gun):
            rec[0] = 0
            rec[1] = self.t + self.p.fire.blind_pause_s

    def record_hit(self, target: int) -> None:
        for (tid, _kind), rec in self._blind.items():
            if tid == target:
                rec[0] = 0
                rec[1] = -1000000000.0

    def blind_paused(self, target: int, kind: str) -> bool:
        rec = self._blind.get((target, kind))
        return bool(rec and self.t < rec[1])
