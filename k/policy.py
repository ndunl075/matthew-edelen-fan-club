from __future__ import annotations
import dataclasses
import math
import sys
import time
import traceback
from dataclasses import dataclass, field
from . import aim as aimmod
from . import loot as lootmod
from . import skirmish as skirm
from . import zone as zonemod
from .derive import Band, Derived
from .fire import Gunnery
from .kite import KitePlan, Kiter
from .memory import Memory
from .mode import ModeDetector
from .nav_stub import make_nav
from .params import Params, load_params
from .peek import Peeker, PeekPlan
from .profile import ProfileBook
from .threat import Assessor, Enemy, View
from .upgrades import UpgradePicker
from .util import clamp, dist2, rot2, sane_config, sane_stats, unit2, wrap

@dataclass
class Mode:
    name: str
    target: Enemy | None = None
    goal: tuple | None = None
    direction: tuple | None = None
    desperate: bool = False
    note: str = ''
    peek: PeekPlan | None = None
    kite: KitePlan | None = None
    band: Band | None = None
    extra: dict = field(default_factory=dict)

class Bot:
    name = 'k3'

    def __init__(self, hello: dict, params: Params | None=None, nav=None):
        hello = {**hello, 'config': sane_config(hello.get('config'))}
        self.hello = hello
        self.cfg = hello['config']
        self.p = params or load_params()
        self.mem = Memory(hello, self.p)
        self.rng = self.mem.rng
        self.nav = nav or make_nav(hello, self.p)
        self.dv = Derived(self.cfg, self.p)
        self.book = ProfileBook(self.cfg, self.p, self.dv)
        self.detect = ModeDetector(self.cfg, self.p)
        self.kiter = Kiter(self.cfg, self.p, self.rng)
        self.peek = Peeker(self.cfg, self.p, self.dv)
        self.assess = Assessor(self.cfg, self.p, self.mem, self.dv, self.book)
        self.gun = Gunnery(self.cfg, self.p, self.mem, self.nav, self.book)
        self.picker = UpgradePicker(self.cfg['upgrades'], self.p)
        self.strict = False
        self.mode = 'start'
        self.last: Mode = Mode('start')
        self.view: View | None = None
        self.profile_name = ''
        self.prof = None
        self._apply_profile('unknown')
        self._errors = 0
        self._dis_since: float | None = None
        self._cornered_until = -1000000000.0
        self._fight_id: int | None = None
        self._dodge_side = 1.0
        self._dodge_until = -1000000000.0
        self._spot: tuple | None = None
        self._spot_t = -1000000000.0
        self._pick_offer = None
        self._pick_t = -1000000000.0
        self._lim: tuple | None = None
        self._wait_spot: tuple | None = None
        self._wait_t = -1000000000.0
        self._ground_z: float | None = None
        self._roof_spot: tuple | None = None
        self._roof_t = -1000000000.0
        self._roof_ban: tuple | None = None
        self.on_roof = False
        self._wd: tuple | None = None
        self._detour: tuple | None = None
        self._detour_until = -1000000000.0
        self._t0 = 0.0
        self._last_act: dict | None = None
        self._skips = 0
        self._cover_cache: dict = {}
        self._safe: tuple | None = None
        self._reach_cache: dict = {}
        self._loot_last: tuple | None = None
        self._losing_until = -1000000000.0
        self._last_target: tuple | None = None
        self._strafe_side = 1.0
        self._strafe_until = -1000000000.0
        self._last_heading: tuple | None = None
        self._engage_note = ''
        self._hit_cache: tuple | None = None
        self.mem.set_sight(self.nav.has_los, getattr(self.nav, 'real', True))

    def _apply_profile(self, name: str) -> None:
        self.p.revert()
        prof = getattr(self.p.profiles, self.profile_for(name))
        self.p.overlay(prof.set.as_dict())
        self.prof = prof
        self.profile_name = self.profile_for(name)

    def profile_for(self, mode: str) -> str:
        return str(self.p.practice_profile) if mode == 'practice' else mode

    def act(self, obs: dict) -> dict:
        self._t0 = time.perf_counter()
        try:
            act = self._act(obs)
            self._last_act = act
            return act
        except Exception:
            if self.strict:
                raise
            self._errors += 1
            if self._errors <= 5 or self._errors % 100 == 0:
                print(f"policy failed at tick {obs.get('tick')} ({self._errors} so far):\n{traceback.format_exc()}", file=sys.stderr, flush=True)
            return self._fallback(obs)

    def _fallback(self, obs: dict) -> dict:
        me, z = (obs['self'], obs['zone'])
        yaw = math.atan2(z['next_center'][1] - me['pos'][1], z['next_center'][0] - me['pos'][0])
        act = {'tick': obs['tick'], 'move': [math.cos(yaw), math.sin(yaw)], 'aim': [yaw, 0.0]}
        if obs.get('offer'):
            act['pick'] = 0
        return act

    def over_budget(self) -> bool:
        return (time.perf_counter() - self._t0) * 1000.0 > self.p.budget.decision_ms

    def _out_of_time(self) -> bool:
        return self._last_act is not None and self._skips < int(self.p.budget.max_skips) and self.over_budget()

    def _cheap_step(self, obs: dict, v: View) -> dict:
        self._skips += 1
        last = self._last_act
        act: dict = {'tick': obs['tick'], 'move': list(last.get('move', [0.0, 0.0])), 'boost': bool(last.get('boost', False)), 'jump': False, 'fire': [False, False], 'reload': False}
        if v.zone.urgent:
            goal = self._zone_goal(v, v.zone.goal)
            self._stalled(v, goal)
            move, boost, _ = self.go(v, goal, 1.0, v.zone.hurry)
            act['move'] = [round(move[0], 4), round(move[1], 4)]
            act['boost'] = bool(boost)
        if obs.get('offer'):
            act['pick'] = self.picker.choose(obs['offer'], v.held())
            self._pick_offer, self._pick_t = (obs['offer'].get('id'), v.t)
        return act

    def _sane_obs(self, obs: dict) -> dict:
        me = obs['self']
        weapons = [dict(w, stats=sane_stats(w.get('stats'), self.cfg['weapons'].get(w.get('kind')))) if w else w for w in me.get('weapons', [])]
        return {**obs, 'self': {**me, 'stats': sane_stats(me.get('stats'), self.cfg['mech']), 'weapons': weapons}}

    def _act(self, obs: dict) -> dict:
        obs = self._sane_obs(obs)
        self.mem.update(obs)
        self.book.update(obs, self.mem)
        name = self.detect.update(obs, self.mem, self.book)
        if self.profile_for(name) != self.profile_name:
            self._apply_profile(name)
        self.assess.allow_props = self.farming_allowed(self.mem.t, obs['self']['hp'] / max(obs['self']['max_hp'], 1.0))
        self.gun.observe(obs['self']['weapons'])
        v = self.assess.build(obs)
        self.view = v
        if self._out_of_time():
            return self._cheap_step(obs, v)
        self._skips = 0
        self._lim = None
        v.seen = self.seen_enemies(v)
        self.update_roof_state(v)
        mode = self.choose_mode(v)
        self.last = mode
        self.mode = mode.name
        if self._out_of_time():
            return self._cheap_step(obs, v)
        move, boost, jump = self.legs(v, mode)
        target = self.aim_target(v, mode)
        ctl = mode.peek.laser if mode.peek is not None else None
        fp = self.gun.plan(v, mode.name, target, self.fire_allowed(v, mode, target), move, laser_ctl=ctl)
        act: dict = {'tick': obs['tick'], 'move': [round(move[0], 4), round(move[1], 4)], 'boost': bool(boost), 'jump': bool(jump), 'aim': [round(fp.aim[0], 4), round(fp.aim[1], 4)], 'fire': [bool(fp.fire[0]), bool(fp.fire[1])], 'reload': bool(fp.reload)}
        if obs.get('offer') and (obs['offer'].get('id') != self._pick_offer or v.t - self._pick_t > self.p.upgrades.repick_s):
            act['pick'] = self.picker.choose(obs['offer'], v.held())
            self._pick_offer, self._pick_t = (obs['offer'].get('id'), v.t)
        self._loot_actions(v, act)
        if self.p.debug.enabled:
            act['debug'] = self._debug(v, mode, target)
        return act

    def eye(self, v: View) -> tuple:
        return (v.pos[0], v.pos[1], v.pos[2] + v.stats.get('eye_height', self.cfg['mech']['eye_height']))

    def seen_enemies(self, v: View) -> list:
        eye = self.eye(v)
        return [e for e in v.enemies if e.fresh and e.in_view and self.nav.has_los(eye, e.center)]

    def laser_slot(self, v: View) -> dict | None:
        for w in v.weapons:
            if w and w['kind'] == 'laser':
                return w if self.mem.t >= self.mem.shorted_until or w.get('charge', 0.0) > 0.0 else None
        return None

    def band_for(self, v: View, e: Enemy) -> Band:
        return self.dv.band(v.weapons, v.stats, v.me.get('cubes', 0), e.holds, e.tr.cubes, enemy_mult=e.hit_mult, laser_mult=e.laser_mult)

    def safe_range(self, v: View) -> float:
        if self._safe is not None and self._safe[0] == v.t:
            return self._safe[1]
        holds: set = set()
        cubes = 0
        for e in v.enemies:
            if e.age <= self.p.loot.enemy_age_s:
                holds |= e.holds
                cubes = max(cubes, e.tr.cubes)
        val = self.dv.safe_range(holds, cubes)
        self._safe = (v.t, val)
        return val

    def in_opening(self, t: float) -> bool:
        return t <= self.prof.opening_s

    def unseen_s(self, t: float | None=None) -> float:
        last = max((tr.last_seen for tr in self.mem.tracks.values() if tr.kind == 'mech'), default=-1000000000.0)
        return (self.mem.t if t is None else t) - last

    def farming_allowed(self, t: float, hp_frac: float=1.0) -> bool:
        prof = self.prof
        if prof.skirmish:
            q = self.p.skirmish
            return hp_frac > q.farm_hp_frac and self.unseen_s(t) >= q.farm_unseen_s
        if t <= prof.farm_until_s:
            return True
        return prof.farm_idle_s > 0 and t - self.mem.last_contact_t >= prof.farm_idle_s

    def choose_mode(self, v: View) -> Mode:
        prof = self.prof
        if prof.skirmish:
            return self.choose_mode_skirmish(v)
        if v.zone.urgent:
            return Mode('zone', goal=v.zone.goal)
        dodge = self.dodge_plan(v)
        if dodge is not None:
            return dodge
        if prof.trade_at_center and v.zone.center_mode:
            tgt = v.attackers[0] if v.attackers else v.seen[0] if v.seen else None
            if tgt is not None:
                return Mode('fight', tgt, note='last circle')
        intruder = self.roof_intruder(v)
        if intruder is not None:
            return Mode('fight', intruder, note='intruder')
        cover = self.cover_plan(v)
        if cover is not None:
            return cover
        if self.should_disengage(v):
            if self.on_roof:
                pt = self.cover_point(v, v.attackers or v.seen)
                if pt is not None:
                    return Mode('cover', (v.attackers or v.seen or [None])[0], goal=pt, note='on roof')
            seen = [e for e in v.enemies if e.fresh and e.sees_us]
            return Mode('disengage', v.attackers[0] if v.attackers else seen[0] if seen else None)
        self._dis_since = None
        fight = self.fight_target(v)
        if fight is not None:
            self._fight_id = fight.id
            return Mode('fight', fight, desperate=v.t < self._cornered_until)
        self._fight_id = None
        self.peek.reset()
        goal = self.drop_goal(v)
        if goal is not None:
            return Mode('drop', goal=goal)
        if prof.scavenge_first and self.mem.t - self.mem.last_kill_t <= self.p.loot.scavenge_window_s:
            cube = self.cube_goal(v)
            if cube is not None:
                return Mode('loot', goal=cube, note="dead bot's cubes")
        fortress = self.fortress_active(v)
        if fortress and self.should_recover(v):
            return Mode('roof', goal=self._roof_spot, note='regen')
        if self.should_recover(v):
            return Mode('recover')
        if not fortress or self.in_opening(v.t):
            goal = self.sticky_loot_goal(v)
            if goal is not None:
                return Mode('loot', goal=goal)
            farm = self.farm_target(v)
            if farm is not None:
                return Mode('farm', farm)
        if fortress:
            return Mode('roof', goal=self._roof_spot)
        if prof.hunt and v.hp_frac >= self.p.recover.hp_frac:
            return Mode('hunt', goal=self.hunt_goal(v))
        if prof.wait_spot:
            spot = self.wait_goal(v)
            if spot is not None:
                return Mode('wait', goal=spot)
        return Mode('reposition')

    def choose_mode_skirmish(self, v: View) -> Mode:
        prof = self.prof
        if v.zone.urgent:
            return Mode('zone', goal=v.zone.goal)
        dodge = self.dodge_plan(v)
        if dodge is not None:
            return dodge
        target = self.skirm_target(v)
        if self.skirm_retreat(v, target):
            return Mode('retreat', target, note='losing')
        if target is not None:
            self._fight_id = target.id
            self._last_target = (target.id, v.t)
            return Mode('fight', target, note=self._engage_note)
        self._fight_id = None
        goal = self.drop_goal(v)
        if goal is not None:
            return Mode('drop', goal=goal)
        if prof.scavenge_first and self.mem.t - self.mem.last_kill_t <= self.p.loot.scavenge_window_s:
            cube = self.cube_goal(v)
            if cube is not None:
                return Mode('loot', goal=cube, note="dead bot's cubes")
        if self.should_recover(v):
            return Mode('recover')
        goal = self.sticky_loot_goal(v)
        if goal is not None:
            return Mode('loot', goal=goal)
        farm = self.farm_target(v)
        if farm is not None:
            return Mode('farm', farm)
        if prof.hunt and v.hp_frac >= self.p.recover.hp_frac:
            return Mode('hunt', goal=self.hunt_goal(v))
        return Mode('reposition')

    def hit_by(self, v: View) -> dict:
        if self._hit_cache is None or self._hit_cache[0] != v.t:
            self._hit_cache = (v.t, self.mem.damage_by(self.p.skirmish.engage_hit_s))
        return self._hit_cache[1]

    def skirm_candidates(self, v: View) -> list:
        q = self.p.skirmish
        cap = self.dv.view_cap(v.stats)
        return [e for e in v.enemies if not e.prop and e.age <= q.track_age_s and (e.d3 <= cap)]

    def skirm_target(self, v: View) -> Enemy | None:
        q = self.p.skirmish
        late = skirm.late_game(self.p, int(v.obs.get('alive', 16)), v.zone.next.r)
        hit = self.hit_by(v)
        keep_id = self._last_target[0] if self._last_target and v.t - self._last_target[1] <= q.target_keep_s else None
        best, best_s, note = (None, 1e+18, '')
        for e in self.skirm_candidates(v):
            why = skirm.engage_reason(self.p, dist=e.d3, hit_age_ok=hit.get(e.id, 0.0) > 0.0, closing=e.closing, hp_frac=e.hp_frac, hp_known=e.tr.hp is not None, aimed=e.aimed, late=late)
            if why is None:
                continue
            hp = e.tr.hp if e.tr.hp is not None else self.cfg['mech']['hp']
            sc = skirm.target_score(self.p, dist=e.d3, hp=hp, hp_frac=e.hp_frac, keep=e.id == keep_id, hit_recent=self._hit_within(e, v, q.target_hit_s), closing=e.closing)
            if sc < best_s:
                best, best_s, note = (e, sc, why)
        self._engage_note = note
        return best

    def _hit_within(self, e: Enemy, v: View, window: float) -> bool:
        return any((d.by == e.id for d in self.mem.recent_damage(window)))

    def skirm_retreat(self, v: View, target: Enemy | None) -> bool:
        q = self.p.skirmish
        near = [e for e in v.enemies if not e.prop and e.age <= q.retreat_age_s and (e.d3 <= q.retreat_near_m)]
        ref = target if target is not None else near[0] if near else None
        ref_hp = None if ref is None or ref.tr.hp is None else ref.hp_frac
        if skirm.is_finishing(self.p, n_near=len(near), hp_frac=v.hp_frac, target_hp_frac=ref_hp):
            self._losing_until = -1000000000.0
            return False
        if skirm.is_losing(self.p, n_near=len(near), hp_frac=v.hp_frac, target_hp_frac=ref_hp):
            self._losing_until = v.t + q.retreat_hold_s
        return v.t < self._losing_until

    def skirm_hold(self, v: View, e: Enemy) -> tuple:
        q = self.p.skirmish
        if q.range_source == 'derived':
            b = self.band_for(v, e)
            return (b.hold, b.lo, b.hi)
        hold = skirm.hold_range(self.p, has_laser=self.laser_slot(v) is not None, has_gun=v.slot_of('gun') is not None, has_lightning=v.slot_of('lightning') is not None, enemy_lightning='lightning' in e.holds, enemy_mortar='mortar' in e.holds, cap=self.dv.view_cap(v.stats))
        return (hold, hold * (1.0 - q.range_band_frac), hold * (1.0 + q.range_band_frac))

    def strafe_side(self, v: View) -> float:
        q = self.p.skirmish
        if v.t >= self._strafe_until:
            if self.rng.random() < q.strafe_flip_prob:
                self._strafe_side = -self._strafe_side
            self._strafe_until = v.t + self.rng.uniform(q.strafe_min_s, q.strafe_max_s)
        return self._strafe_side

    def fight_cube(self, v: View, e: Enemy):
        q = self.p.skirmish
        reach = q.cube_low_hp_m if v.hp_frac < q.cube_low_hp_frac else q.cube_m
        best, best_d = (None, reach)
        for rec in self.mem.cubes.values():
            d = dist2(rec.pos, v.pos)
            if d < best_d and (not self.mem.is_blocked(rec.pos)) and (dist2(rec.pos, e.pos) > q.cube_enemy_m):
                best, best_d = ((rec.pos[0], rec.pos[1]), d)
        return best

    def skirm_fight_legs(self, v: View, mode: Mode) -> tuple:
        e = mode.target
        hold, lo, hi = self.skirm_hold(v, e)
        band = Band(lo, hi, hold, 1.0, '', True)
        mode.band = band
        charging = self.laser_slot(v) is not None and (self.laser_slot(v) or {}).get('charge', 0.0) > 0.0
        hurt = self.mem.t - self.mem.last_hurt_t < self.p.kite.ground_s
        kp = self.kiter.plan(v, e, band, charging=charging, hit_recently=hurt)
        mode.kite = kp
        mode.note = f'{mode.note} {e.d:.0f}/{hold:.0f}'
        heading = None
        if not self.over_budget():
            eye_z = v.pos[2] + v.stats.get('eye_height', self.cfg['mech']['eye_height'])
            others = [e2.center for e2 in v.enemies if e2 is not e and (not e2.prop) and e2.fresh and (e2.d3 <= self.dv.view_cap(v.stats))]
            circle = self.safe_circle(v)
            m = self.p.zone.margin_m

            def sees(a, b):
                pa = (a[0], a[1], eye_z)
                pb = b if len(b) > 2 else (b[0], b[1], v.pos[2] + self.cfg['mech']['height'] * 0.5)
                return self.nav.has_los(pa, pb)
            heading = skirm.pick_heading(self.p, pos=v.pos, target=(e.pos[0], e.pos[1]), hold=hold, side=self.strafe_side(v), prev=self._last_heading, inside=lambda x, y: circle.contains((x, y), m), clear=self.nav.step_clear, sees=lambda p, t: sees(p, t), others=others, cube=self.fight_cube(v, e))
        if heading is None:
            heading = kp.direction
        self._last_heading = heading
        d = self.never_still(v, self.keep_inside(v, heading), e.pos)
        speed = 1.0
        boost = kp.boost and e.d < lo
        return self.go_dir(v, d, speed, boost, ground=True)

    def retreat_legs(self, v: View, mode: Mode) -> tuple:
        q = self.p.skirmish
        threats = [e for e in v.enemies if not e.prop and e.age <= q.retreat_age_s and (e.d3 <= q.retreat_near_m)]
        boost = v.energy_frac >= q.retreat_boost_energy_frac
        pt = self.cover_point(v, threats) if threats else None
        circle = self.safe_circle(v)
        if pt is not None and circle.contains(pt, self.p.zone.margin_m):
            mode.goal = pt
            mode.note += ' cover'
            self._stalled(v, pt)
            return self.go(v, pt, 1.0, boost, ground=True)
        if q.roof_escape:
            roof = self.roof_pick(v)
            if roof is not None and dist2(roof, v.pos) <= q.retreat_roof_m:
                mode.goal = roof
                mode.note += ' roof'
                self._stalled(v, roof)
                return self.go(v, roof, 1.0, boost)
        mode.note += ' run'
        return self.disengage_legs(v)

    def dodge_plan(self, v: View) -> Mode | None:
        q = self.p.dodge
        pos = v.pos
        best: Enemy | None = None
        laser_range = self.cfg['weapons']['laser']['range']
        for e in v.enemies:
            if not (e.charging and e.fresh and e.sees_us and (e.d3 <= laser_range)):
                continue
            if e.aim_off > math.radians(q.aim_deg) + math.atan2(v.radius, max(e.d3, 1.0)):
                continue
            left = self.book.charge_left(e.id, v.t)
            full = self.cfg['weapons']['laser']['charge']
            if not self.dv.dodge_possible(full if left is None else left, v.radius, v.stats):
                continue
            if best is None or e.aim_off < best.aim_off:
                best = e
        if best is not None:
            e = best
            to_us = math.atan2(pos[1] - e.pos[1], pos[0] - e.pos[0])
            off = wrap(e.tr.aim[0] - to_us)
            if v.t >= self._dodge_until:
                self._dodge_side = -math.copysign(1.0, off) if abs(off) > q.side_eps_rad else 1.0 if self.rng.random() < 0.5 else -1.0
                self._dodge_until = v.t + q.side_hold_s
            side = rot2((math.cos(to_us), math.sin(to_us)), self._dodge_side * math.pi / 2.0)
            away = (math.cos(to_us), math.sin(to_us))
            d = unit2(side[0] * (1.0 - q.retreat_frac) + away[0] * q.retreat_frac, side[1] * (1.0 - q.retreat_frac) + away[1] * q.retreat_frac)
            d = self.toward_cover(d, v, [e], mix=1.0 if self.on_roof else None)
            return Mode('dodge', e, direction=self.keep_inside(v, d), note='laser')
        for pr in self.mem.projectiles:
            kind = pr.get('kind')
            if kind == 'npc' and (not q.include_npc):
                continue
            hit = self._incoming(v, pr)
            if hit is not None:
                return Mode('dodge', None, direction=self.keep_inside(v, hit), note=kind)
        return None

    def toward_cover(self, d: tuple, v: View, threats: list, mix: float | None=None) -> tuple:
        pt = self.cover_point(v, threats)
        if pt is None:
            return d
        cover = unit2(pt[0] - v.pos[0], pt[1] - v.pos[1])
        if cover == (0.0, 0.0):
            return d
        mix = self.p.nav.cover_mix if mix is None else mix
        mixed = unit2(d[0] * (1.0 - mix) + cover[0] * mix, d[1] * (1.0 - mix) + cover[1] * mix)
        return mixed if mixed != (0.0, 0.0) else d

    def cover_point(self, v: View, threats: list):
        if not threats:
            return None
        q = self.p.cover
        threats = threats[:int(q.max_threats)]
        key = (tuple(((round(e.center[0] / q.cache_m), round(e.center[1] / q.cache_m)) for e in threats)), round(v.pos[0] / q.cache_m), round(v.pos[1] / q.cache_m))
        hit = self._cover_cache.get(key)
        if hit is not None and v.t - hit[0] <= q.cache_s:
            return hit[1]
        pt = self.nav.cover_from(v.me, [tuple(e.center) for e in threats], max_dist=v.walk * q.run_s)
        pt = None if pt is None else (pt[0], pt[1])
        if len(self._cover_cache) >= 8:
            self._cover_cache.clear()
        self._cover_cache[key] = (v.t, pt)
        return pt

    def _incoming(self, v: View, pr: dict) -> tuple | None:
        q = self.p.dodge
        pos, vel = (pr['pos'], pr['vel'])
        if pr['kind'] == 'mortar':
            hit = aimmod.mortar_impact(pos, vel, self.cfg['weapons']['mortar']['gravity'], v.center[2])
            if hit is None or hit[2] > q.shell_horizon_s:
                return None
            reach = self.cfg['weapons']['mortar']['splash_radius'] + q.shell_margin_m
            if dist2((hit[0], hit[1]), v.pos) > reach:
                return None
            d = unit2(v.pos[0] - hit[0], v.pos[1] - hit[1])
            return d if d != (0.0, 0.0) else (1.0, 0.0)
        speed2 = vel[0] ** 2 + vel[1] ** 2 + vel[2] ** 2
        if speed2 < 1e-09:
            return None
        rel = (v.center[0] - pos[0], v.center[1] - pos[1], v.center[2] - pos[2])
        t_hit = (rel[0] * vel[0] + rel[1] * vel[1] + rel[2] * vel[2]) / speed2
        if not 0.0 < t_hit < q.bullet_horizon_s:
            return None
        miss = (rel[0] - vel[0] * t_hit, rel[1] - vel[1] * t_hit, rel[2] - vel[2] * t_hit)
        if math.hypot(*miss) > v.radius + self.cfg['projectile']['gun_radius'] + q.bullet_margin_m:
            return None
        d = unit2(miss[0], miss[1])
        if d == (0.0, 0.0):
            d = rot2(unit2(vel[0], vel[1]), self._dodge_side * math.pi / 2.0)
        return d

    def cover_plan(self, v: View) -> Mode | None:
        prof = self.prof
        if not prof.cover_multi or len(v.seen) <= prof.max_seen:
            return None
        main = v.attackers[0] if v.attackers and v.attackers[0] in v.seen else v.seen[0]
        others = [e for e in v.seen if e is not main]
        pt = self.cover_point(v, others)
        if pt is None or self.mem.is_blocked(pt):
            return None
        return Mode('cover', main, goal=pt, note=f'{len(others)} extra')

    def should_disengage(self, v: View) -> bool:
        q = self.p.disengage
        if v.t < self._cornered_until:
            return False
        atk = v.attackers
        was = self.mode == 'disengage'
        low = v.hp_frac < (q.hp_exit if was else q.hp_enter)
        present = bool(atk) or any((e.fresh and e.sees_us and (e.d3 <= q.threat_range_m) for e in v.enemies))
        if len(atk) == 1 and atk[0].tr.hp is not None and (atk[0].hp_frac <= q.finish_hp_frac) and (atk[0].advantage >= 1.0) and (len(atk) < q.min_attackers):
            return False
        go = len(atk) >= q.min_attackers or (low and present)
        if go:
            if self._dis_since is None:
                self._dis_since = v.t
            elif v.t - self._dis_since > q.max_s and v.t - self.mem.last_hurt_t < q.hurt_window_s:
                self._cornered_until = v.t + q.cornered_s
                self._dis_since = None
                return False
        else:
            self._dis_since = None
        return go

    def in_drop_ring(self, e: Enemy, v: View) -> bool:
        for d in v.obs.get('drops', []):
            if d.get('lands_in', 1.0) <= 0.0 and dist2(d['pos'], e.pos) <= d['radius']:
                return True
        return False

    def fight_target(self, v: View) -> Enemy | None:
        q, prof = (self.p.fight, self.prof)
        atk = v.attackers
        if v.t < self._cornered_until and atk:
            return min(atk, key=lambda e: e.tr.hp if e.tr.hp is not None else 1000000000.0)
        if len(atk) == 1:
            return atk[0]
        if len(atk) > 1:
            return None
        cap = self.dv.view_cap(v.stats)
        seen = [e for e in v.seen if e.d3 <= cap]
        if not seen or len(seen) > prof.max_seen:
            return None
        e = seen[0]
        third = self.safe_range(v) * q.third_party_mult
        for o in v.enemies:
            if o is not e and o.age <= q.third_party_age_s and (o.d3 <= third or dist2(o.pos, e.pos) <= third):
                return None
        if e.id == self._fight_id:
            return e
        if prof.fight_start_any and e.advantage >= prof.start_advantage:
            return e
        if prof.fight_unaware and e.unaware:
            return e
        if prof.fight_busy and self.book.busy(e.id, v.t):
            return e
        if prof.fight_ring and self.in_drop_ring(e, v):
            return e
        if prof.fight_by_advantage and e.advantage >= q.min_advantage:
            return e
        return None

    def drop_goal(self, v: View) -> tuple | None:
        q = self.p.drop
        me_id = v.me['id']
        if self.prof.skirmish and self.unseen_s() < self.p.skirmish.drop_unseen_s:
            return None
        best, best_d = (None, 1e+18)
        clear = self.safe_range(v) * self.prof.clear_mult
        for d in v.obs.get('drops', []):
            if d.get('holder') not in (None, me_id):
                continue
            pos = d['pos']
            dist = dist2(pos, v.pos)
            if self.mem.is_blocked(pos):
                continue
            if d['lands_in'] > dist / (v.walk * q.speed_frac) + q.lead_s:
                continue
            if not (v.zone.next.contains(pos, self.p.zone.margin_m) and v.zone.now.contains(pos, self.p.zone.margin_m)):
                continue
            theirs = [dist2(e.pos, pos) for e in v.enemies if e.age <= q.enemy_age_s]
            if theirs and (min(theirs) < clear + d['radius'] or min(theirs) < dist * q.closest_factor):
                continue
            if v.attackers or dist > q.max_dist_m:
                continue
            if dist < best_d:
                best, best_d = ((pos[0], pos[1], d['radius']), dist)
        return best

    def should_recover(self, v: View) -> bool:
        q = self.p.recover
        if v.attackers:
            return False
        return v.hp_frac < (q.hp_done if self.mode == 'recover' else q.hp_frac)

    def enemy_points(self, v: View) -> list:
        pts = [(e.pos[0], e.pos[1]) for e in v.enemies if e.age <= self.p.loot.enemy_age_s]
        pts += [(d.pos[0], d.pos[1]) for d in v.drones]
        return pts

    def loot_limits(self, v: View) -> lootmod.Limits:
        if self._lim is not None:
            return self._lim[0]
        q, prof = (self.p.loot, self.prof)
        clear = self.safe_range(v) * prof.clear_mult
        weapon_r = v.walk * q.weapon_trip_s
        cube_r = v.walk * q.cube_trip_s
        if self.in_opening(v.t) and prof.opening_laser:
            weapon_r = max(weapon_r, v.walk * prof.opening_s * q.opening_walk_frac)
            if prof.skirmish:
                weapon_r = self.p.skirmish.opening_loot_m
        if self.mem.t - self.mem.last_kill_t <= q.scavenge_window_s:
            cube_r = max(cube_r, v.walk * q.scavenge_trip_s)
        free = self.dv.jump_only_climb_m(v.stats)
        reach_m = self.dv.climb_m(v.me['energy'], v.stats)
        circle_r = self.cfg['loot']['pickup_radius'] * q.roof_spot_frac
        budget = [int(q.max_reach_checks)]

        def reach(pos):
            if pos[2] - v.pos[2] <= free:
                return (pos[0], pos[1])
            key = (round(pos[0]), round(pos[1]), round(pos[2]))
            hit = self._reach_cache.get(key)
            if hit is not None and v.t - hit[0] <= q.reach_cache_s:
                return hit[1]
            if budget[0] <= 0:
                return None
            budget[0] -= 1
            pt = self.roof_point(self.nav.high_ground(v.me, (pos[0], pos[1], circle_r), max_climb=reach_m))
            pt = None if pt is None else (pt[0], pt[1])
            if len(self._reach_cache) >= 64:
                self._reach_cache.clear()
            self._reach_cache[key] = (v.t, pt)
            return pt
        lim = lootmod.Limits(weapon_r, cube_r, clear, reach)
        self._lim = (lim,)
        return lim

    def cube_goal(self, v: View) -> tuple | None:
        pts = self.enemy_points(v)
        near = self.loot_limits(v).clear_m * self.p.loot.close_frac
        if v.attackers or any((e.d3 <= near and e.age <= self.p.loot.enemy_age_s for e in v.enemies)):
            return None
        cube = lootmod.best_cube(v.me, self.mem, self.p, pts, self.loot_limits(v))
        if cube is None or not v.zone.now.contains(cube[1], self.p.zone.margin_m):
            return None
        return cube[1]

    def sticky_loot_goal(self, v: View) -> tuple | None:
        goal = self.loot_goal(v)
        q = self.p.loot
        if goal is not None:
            self._loot_last = (goal, v.t)
            return goal
        last = self._loot_last
        if last is None:
            return None
        if v.t - last[1] <= q.hold_s and (not self.mem.is_blocked(last[0])) and (not v.attackers) and (dist2(last[0], v.pos) > self.p.stuck.min_dist_m) and v.zone.now.contains(last[0], self.p.zone.margin_m):
            return last[0]
        self._loot_last = None
        return None

    def loot_goal(self, v: View) -> tuple | None:
        q = self.p.loot
        lim = self.loot_limits(v)
        if v.attackers or any((e.d3 <= lim.clear_m * q.close_frac and e.age <= q.enemy_age_s for e in v.enemies)):
            return None
        pts = self.enemy_points(v)
        goal, goal_d = (None, 1e+18)
        pk = None
        if self.in_opening(v.t) and self.prof.opening_laser and (v.slot_of('laser') is None):
            llim = dataclasses.replace(lim, weapon_radius=max(lim.weapon_radius, self.p.skirmish.opening_laser_m)) if self.prof.skirmish else lim
            pk = lootmod.best_pickup(v.me, self.mem, self.p, v.laser_threat, pts, llim, only='laser')
        if pk is None:
            pk = lootmod.best_pickup(v.me, self.mem, self.p, v.laser_threat, pts, lim)
        if pk is not None:
            goal, goal_d = (pk[1], dist2(pk[0].pos, v.pos) - q.weapon_bonus_m)
        cube = lootmod.best_cube(v.me, self.mem, self.p, pts, lim)
        if cube is not None and dist2(cube[0].pos, v.pos) < goal_d:
            goal = cube[1]
        if goal is not None and (not v.zone.now.contains(goal, self.p.zone.margin_m)):
            return None
        return goal

    def _loot_actions(self, v: View, act: dict) -> None:
        q = self.p.loot
        took = False
        if not v.attackers and (not any((e.fresh and e.d3 <= q.take_enemy_m for e in v.enemies))):
            reach = self.cfg['loot']['pickup_radius'] * q.take_reach_frac
            for rec in sorted(self.mem.pickups.values(), key=lambda r: dist2(r.pos, v.pos)):
                if dist2(rec.pos, v.pos) > reach:
                    break
                if abs(rec.pos[2] - v.pos[2]) > q.take_dz_m or self.mem.take_block.get(rec.id, -1000000000.0) > v.t or self.mem.kind_block.get(rec.kind, -1000000000.0) > v.t:
                    continue
                want, slot = lootmod.take_plan(rec.kind, v.weapons, self.p, v.laser_threat)
                if want:
                    act['take'] = rec.id
                    if slot is not None:
                        act['drop'] = slot
                    self.mem.take_block[rec.id] = v.t + q.retake_s
                    took = True
                    break
        if not took:
            slot = lootmod.mortar_drop_slot(v.weapons, v.laser_threat)
            if slot is not None:
                act['drop'] = slot
                self.mem.kind_block['mortar'] = v.t + q.mortar_block_s

    def farm_target(self, v: View) -> Enemy | None:
        if not v.props or v.attackers:
            return None
        pt = v.props[0]
        if not v.zone.now.contains(pt.pos, self.p.zone.margin_m) or self.mem.is_blocked(pt.pos):
            return None
        return pt

    @staticmethod
    def roof_point(hg):
        if hg is None:
            return None
        st = getattr(hg, 'stand', hg)
        z = getattr(st, 'z', None)
        if z is None and len(st) > 2:
            z = st[2]
        return (st[0], st[1]) if z is None else (st[0], st[1], z)

    def update_roof_state(self, v: View) -> None:
        q = self.p.roof
        grounded = bool(v.me.get('grounded', True))
        up = False
        if self._roof_spot is not None and grounded and (self._ground_z is not None):
            up = dist2(self._roof_spot, v.pos) <= q.on_m and v.pos[2] - self._ground_z >= q.on_frac * self.prof.roof_min_height_m
        self.on_roof = up
        if grounded and (not up):
            self._ground_z = v.pos[2]

    def roof_pick(self, v: View) -> tuple | None:
        q = self.p.roof
        if v.t - self._roof_t <= q.replan_s:
            return self._roof_spot
        self._roof_t = v.t
        nxt = v.zone.next
        circle = (nxt.cx, nxt.cy, max(nxt.r - nxt.margin_for(self.p.zone.margin_m), 1.0))
        pt = self.roof_point(self.nav.high_ground(v.me, circle, max_climb=self.dv.climb_m(v.me['energy'], v.stats)))
        spot = None
        if pt is not None:
            tall = len(pt) < 3 or self._ground_z is None or pt[2] - self._ground_z >= self.prof.roof_min_height_m
            ban = self._roof_ban
            banned = ban is not None and v.t < ban[2] and (dist2(ban, pt) <= q.ban_m) or self.mem.is_blocked(pt)
            if tall and (not banned):
                spot = (pt[0], pt[1])
        if spot is None and self.on_roof:
            return self._roof_spot
        self._roof_spot = spot
        return spot

    def fortress_active(self, v: View) -> bool:
        prof = self.prof
        if not prof.fortress:
            return False
        hunters = {e.id for e in v.enemies if e.age <= self.p.roof.hunter_age_s}
        if not (self.laser_slot(v) is not None or len(hunters) >= prof.fortress_outnumbered):
            return False
        return self.roof_pick(v) is not None

    def roof_intruder(self, v: View) -> Enemy | None:
        if not self.on_roof:
            return None
        q = self.p.roof
        near = self.cfg['vision']['near_range'] * q.intruder_frac
        for e in v.enemies:
            if e.fresh and e.d <= near and (e.pos[2] - v.pos[2] >= -q.intruder_dz_m):
                if self._roof_spot is not None:
                    self._roof_ban = (self._roof_spot[0], self._roof_spot[1], v.t + q.ban_s)
                    self._roof_spot = None
                return e
        return None

    def hunt_goal(self, v: View) -> tuple:
        q = self.p.hunt
        nxt = v.zone.next
        best = None
        for e in v.enemies:
            if e.age <= q.age_s and nxt.contains(e.pos, self.p.zone.margin_m) and (best is None or e.d < best.d) and (not self.mem.is_blocked(e.pos)):
                best = e
        if best is not None:
            return (best.pos[0], best.pos[1])
        snd = self.mem.recent_sounds(q.sound_s, tuple(q.sound_kinds))
        for s in reversed(snd):
            if nxt.contains((s.ex, s.ey), self.p.zone.margin_m) and (not self.mem.is_blocked((s.ex, s.ey))):
                return (s.ex, s.ey)
        return self._zone_goal(v, zonemod.clamp_into(nxt, (nxt.cx, nxt.cy), self.p.zone.margin_m))

    def wait_goal(self, v: View):
        q = self.p.wait
        nxt = v.zone.next
        if v.t - self._wait_t <= q.replan_s and (self._wait_spot is None or nxt.contains(self._wait_spot, self.p.zone.margin_m)):
            return self._wait_spot
        m = nxt.margin_for(self.p.zone.margin_m)
        circle = (nxt.cx, nxt.cy, max(nxt.r - m, 1.0))
        pt = self.nav.pocket(v.me, circle)
        if pt is None:
            pt = self.roof_point(self.nav.high_ground(v.me, circle))
        self._wait_t = v.t
        self._wait_spot = None if pt is None or self.mem.is_blocked(pt) else (pt[0], pt[1])
        return self._wait_spot

    def aim_target(self, v: View, mode: Mode) -> Enemy | None:
        if mode.target is not None and mode.name in ('fight', 'dodge', 'disengage', 'cover', 'farm', 'retreat'):
            return mode.target
        if v.attackers:
            return v.attackers[0]
        return v.props[0] if v.props and mode.name != 'zone' else None

    def fire_allowed(self, v: View, mode: Mode, target: Enemy | None) -> bool:
        if target is None or mode.name == 'disengage':
            return False
        if mode.name == 'cover':
            return target.engaged or self.prof.fight_start_any
        return mode.name in ('fight', 'retreat') or target.engaged or (target.prop and (not v.attackers))

    def opening_quiet(self, v: View) -> bool:
        return bool(self.prof.skirmish and self.in_opening(v.t))

    def quiet_speed(self, v: View) -> float:
        return clamp(self.cfg['sound']['step_speed'] * self.p.sound.quiet_frac / max(v.walk, 1e-06), self.p.sound.min_speed_frac, 1.0)

    def safe_circle(self, v: View) -> zonemod.Circle:
        m = self.p.zone.margin_m
        return v.zone.next if v.zone.next.contains(v.pos, m) else v.zone.now

    def keep_inside(self, v: View, d: tuple) -> tuple:
        look = self.p.zone.lookahead_m
        c = self.safe_circle(v)
        ahead = (v.pos[0] + d[0] * look, v.pos[1] + d[1] * look)
        if c.contains(ahead, self.p.zone.margin_m):
            return d
        inward = unit2(c.cx - v.pos[0], c.cy - v.pos[1])
        mixed = unit2(d[0] + inward[0] * self.p.zone.inward_mix, d[1] + inward[1] * self.p.zone.inward_mix)
        return mixed if mixed != (0.0, 0.0) else inward

    def nav_reset(self) -> None:
        reset = getattr(self.nav, 'reset', None)
        if reset is not None:
            reset()

    def never_still(self, v: View, d: tuple, ref=None) -> tuple:
        if d != (0.0, 0.0):
            return d
        if ref is not None:
            u = unit2(ref[0] - v.pos[0], ref[1] - v.pos[1])
            if u != (0.0, 0.0):
                return rot2(u, self._dodge_side * math.pi / 2.0)
        c = self.safe_circle(v)
        u = unit2(c.cx - v.pos[0], c.cy - v.pos[1])
        if u != (0.0, 0.0):
            return u
        a = self.rng.uniform(0.0, 2.0 * math.pi)
        return (math.cos(a), math.sin(a))

    def go(self, v: View, goal, speed: float, boost: bool=False, *, quiet: bool=False, ground: bool=False) -> tuple:
        st = self.nav.steer(v.me, goal, quiet=quiet)
        mv = st['move']
        move = (mv[0] * speed, mv[1] * speed)
        moving = math.hypot(*move) > 0.0
        grounded = bool(v.me.get('grounded', True))
        energy_ok = v.energy_frac >= self.p.nav.boost_min_energy_frac
        run = boost and energy_ok and grounded and moving and (not quiet)
        jet = bool(st['boost']) and (not quiet)
        jump = bool(st['jump']) and (not quiet)
        if ground and (not grounded):
            jet = False
        if ground and self.mem.t - self.mem.last_hurt_t < self.p.kite.ground_s:
            jump = False
            jet = False
        return (move, run or jet, jump)

    def go_dir(self, v: View, d, speed: float, boost: bool=False, *, quiet: bool=False, ground: bool=False) -> tuple:
        look = self.p.zone.lookahead_m
        return self.go(v, (v.pos[0] + d[0] * look, v.pos[1] + d[1] * look), speed, boost, quiet=quiet, ground=ground)

    def legs(self, v: View, mode: Mode) -> tuple:
        name = mode.name
        if name == 'zone':
            goal = self._zone_goal(v, mode.goal)
            self._stalled(v, goal)
            return self.go(v, goal, 1.0, v.zone.hurry)
        if name == 'dodge':
            return self.go_dir(v, mode.direction, 1.0, bool(self.p.dodge.boost and mode.note == 'laser'))
        if name == 'retreat':
            return self.retreat_legs(v, mode)
        if name == 'cover':
            self._stalled(v, mode.goal)
            return self.go(v, mode.goal, 1.0, ground=True)
        if name == 'disengage':
            return self.disengage_legs(v)
        if name == 'fight':
            return self.skirm_fight_legs(v, mode) if self.prof.skirmish else self.fight_legs(v, mode)
        if name == 'drop':
            x, y, r = mode.goal
            if dist2((x, y), v.pos) <= r * self.p.drop.ring_frac:
                self.nav_reset()
                return ((0.0, 0.0), False, False)
            self._stalled(v, (x, y))
            return self.go(v, (x, y), 1.0)
        if name == 'recover':
            return self.recover_legs(v)
        if name == 'loot':
            self._stalled(v, mode.goal)
            return self.go(v, mode.goal, self.p.loot.speed_frac, quiet=self.prof.quiet_loot or self.opening_quiet(v))
        if name == 'farm':
            return self.farm_legs(v, mode.target)
        if name == 'roof':
            return self.roof_legs(v, mode)
        if name == 'hunt':
            self._stalled(v, mode.goal)
            return self.go(v, mode.goal, 1.0, quiet=self.opening_quiet(v))
        if name == 'wait':
            if dist2(mode.goal, v.pos) <= self.p.reposition.arrive_m:
                self.nav_reset()
                return ((0.0, 0.0), False, False)
            self._stalled(v, mode.goal)
            return self.go(v, mode.goal, 1.0, quiet=True)
        return self.reposition_legs(v)

    def disengage_legs(self, v: View) -> tuple:
        q = self.p.disengage
        fx = fy = 0.0
        for e in v.enemies:
            if e.attacking or (e.fresh and e.d3 <= q.flee_range_m):
                ux, uy = unit2(v.pos[0] - e.pos[0], v.pos[1] - e.pos[1])
                w = 1.0 / max(e.d, q.min_dist_m)
                fx, fy = (fx + ux * w, fy + uy * w)
        d = unit2(fx, fy)
        if d == (0.0, 0.0):
            hurt = [r for r in self.mem.recent_damage(q.hurt_window_s) if r.bearing is not None]
            if hurt:
                d = (-math.cos(hurt[-1].bearing), -math.sin(hurt[-1].bearing))
        if v.attackers:
            d = self.toward_cover(d, v, v.attackers)
        d = self.keep_inside(v, d) if d != (0.0, 0.0) else d
        ref = (v.attackers or v.enemies or [None])[0]
        d = self.never_still(v, d, None if ref is None else ref.pos)
        goal = (v.pos[0] + d[0] * q.flee_m, v.pos[1] + d[1] * q.flee_m)
        goal = zonemod.clamp_into(self.safe_circle(v), goal, self.p.zone.margin_m)
        near = min((e.d3 for e in v.attackers), default=1000000000.0)
        return self.go(v, goal, 1.0, near <= q.boost_range_m and v.energy_frac >= q.boost_min_energy_frac)

    def fight_legs(self, v: View, mode: Mode) -> tuple:
        e = mode.target
        band = self.band_for(v, e)
        mode.band = band
        laser = self.laser_slot(v)
        if self.prof.peek and laser is not None and self.peek.fits(v, e, band.lo):
            pk = self.peek.plan(v, e, laser, self.nav)
            if pk is not None:
                mode.peek = pk
                mode.note = f'peek {pk.phase}'
                self._stalled(v, pk.goal)
                return self.go(v, pk.goal, 1.0, quiet=pk.phase in ('hide', 'back'), ground=True)
        else:
            self.peek.reset()
        if self.on_roof and mode.note != 'intruder':
            mode.note = 'roof hold'
            if self.mem.t - self.mem.last_hurt_t < self.p.kite.ground_s:
                pt = self.cover_point(v, [e])
                if pt is not None:
                    return self.go(v, pt, 1.0, ground=True)
                side = self.never_still(v, (0.0, 0.0), e.pos)
                return self.go_dir(v, side, self.p.roof.dodge_speed, ground=True)
            return ((0.0, 0.0), False, False)
        charging = laser is not None and laser.get('charge', 0.0) > 0.0
        hurt = self.mem.t - self.mem.last_hurt_t < self.p.kite.ground_s
        kp = self.kiter.plan(v, e, band, charging=charging, hit_recently=hurt)
        mode.kite = kp
        mode.note = f'kite {kp.state} {e.d:.0f}/{band.hold:.0f}'
        if kp.losing:
            pt = self.cover_point(v, [e])
            if pt is not None:
                mode.note += ' cover'
                self._stalled(v, pt)
                return self.go(v, pt, 1.0, ground=True)
        d = self.never_still(v, self.keep_inside(v, kp.direction), e.pos)
        speed = 1.0
        if e.unaware and (not e.engaged) and (e.d > band.hi):
            speed = self.quiet_speed(v)
        return self.go_dir(v, d, speed, kp.boost, ground=True)

    def roof_legs(self, v: View, mode: Mode) -> tuple:
        spot = mode.goal
        if not self.on_roof:
            self._stalled(v, spot)
            return self.go(v, spot, 1.0)
        eye = self.eye(v)
        known = [e for e in v.enemies if e.age <= self.p.recover.enemy_age_s and e.d3 <= self.dv.view_cap(v.stats)]
        watchers = [e for e in known if self.nav.has_los(e.center, eye)]
        if watchers:
            pt = self.cover_point(v, watchers)
            if pt is not None:
                mode.note += ' back from edge'
                self._stalled(v, pt)
                return self.go(v, pt, 1.0, ground=True)
        self.nav_reset()
        return ((0.0, 0.0), False, False)

    def farm_legs(self, v: View, prop: Enemy) -> tuple:
        stand = self.cfg['npc']['aggro_range'] * self.p.farm.stand_mult
        if prop.d <= stand:
            self.nav_reset()
            return ((0.0, 0.0), False, False)
        self._stalled(v, (prop.pos[0], prop.pos[1]))
        return self.go(v, (prop.pos[0], prop.pos[1]), 1.0, quiet=self.opening_quiet(v))

    def recover_legs(self, v: View) -> tuple:
        q = self.p.recover
        known = [e for e in v.enemies if e.age <= q.enemy_age_s]
        dmin = min((e.d for e in known), default=1000000000.0)
        safe = self.safe_range(v)
        noise = [s for s in self.mem.recent_sounds(q.sound_s, tuple(q.sound_kinds)) if dist2((s.ex, s.ey), v.pos) < q.sound_radius_m]
        if self.prof.hide_to_recover and known:
            eye = self.eye(v)
            watchers = [e for e in known if e.d3 <= self.dv.view_cap(v.stats) and self.nav.has_los(e.center, eye)]
            if watchers:
                pt = self.cover_point(v, watchers)
                if pt is not None:
                    self._stalled(v, pt)
                    return self.go(v, pt, 1.0, ground=True)
        if dmin >= safe * q.hold_safe_frac and (not noise):
            self.nav_reset()
            return ((0.0, 0.0), False, False)
        goal = self._spot_for(v, self.p.reposition.hazard_mult_recover)
        if self._stalled(v, goal):
            self._spot = None
        panic = dmin < safe * q.panic_frac
        return self.go(v, goal, 1.0, quiet=self.prof.quiet and (not panic))

    def hazards(self, v: View, mult: float=1.0) -> list:
        q = self.p.reposition
        out = []
        for e in v.enemies:
            w = q.enemy_weight * (1.0 if e.fresh else q.stale_weight) * mult
            out.append((e.pos[0], e.pos[1], w, q.enemy_radius_m))
        for s in self.mem.recent_sounds(q.sound_window_s):
            w = getattr(q.sound_weights, s.kind, q.sound_weights.default) * mult
            if w > 0:
                out.append((s.ex, s.ey, w, q.sound_radius_m))
        for d in v.drones:
            out.append((d.pos[0], d.pos[1], q.drone_weight * mult, q.drone_radius_m))
        for g in self.mem.goal_block:
            if g[2] >= v.t:
                out.append((g[0], g[1], q.enemy_weight, self.p.stuck.block_radius_m * 3.0))
        return out

    def _stalled(self, v: View, goal) -> bool:
        q = self.p.stuck
        d = dist2(goal, v.pos)
        wd = self._wd
        self._wd = (goal, d, v.t, v.t) if wd is None else wd[:3] + (v.t,)
        if wd is None or v.t - wd[3] > q.reset_gap_s or dist2(wd[0], goal) > q.goal_jump_m or (d <= q.min_dist_m):
            self._wd = (goal, d, v.t, v.t)
            return False
        if d < wd[1] - q.shrink_m:
            self._wd = (wd[0], d, v.t, v.t)
            return False
        if v.t - wd[2] < q.give_up_s:
            return False
        self.mem.block_goal(goal, q.block_s)
        self._wd = None
        self._forget_goals()
        return True

    def _forget_goals(self) -> None:
        self._wait_spot = None
        self._wait_t = -1000000000.0
        self._roof_spot = None
        self._roof_t = -1000000000.0
        self._loot_last = None
        self.peek.reset()

    def _zone_goal(self, v: View, goal: tuple) -> tuple:
        if not self.mem.is_blocked(goal):
            self._detour = None
            return goal
        if self._detour is None or v.t > self._detour_until or self.mem.is_blocked(self._detour):
            c = v.zone.next
            lim = max(0.0, c.r - c.margin_for(self.p.zone.margin_m))
            for _ in range(8):
                a, r = (self.rng.uniform(0.0, 2.0 * math.pi), lim * 0.8 * math.sqrt(self.rng.random()))
                self._detour = (c.cx + r * math.cos(a), c.cy + r * math.sin(a))
                if not self.mem.is_blocked(self._detour):
                    break
            self._detour_until = v.t + self.p.stuck.detour_s
        return self._detour

    def _spot_for(self, v: View, mult: float=1.0) -> tuple:
        q = self.p.reposition
        nxt = v.zone.next
        spot = self._spot
        if spot is not None and nxt.contains(spot, self.p.zone.margin_m) and (v.t - self._spot_t < q.min_replan_s or self.over_budget()):
            return spot
        haz = self.hazards(v, mult)
        stale = spot is None or v.t - self._spot_t > q.replan_s or (not nxt.contains(spot, self.p.zone.margin_m)) or (zonemod.hazard_at(haz, spot) > q.abort_hazard) or (zonemod.hazard_at(haz, v.pos) > q.leave_hazard)
        if stale:
            self._spot = zonemod.pick_spot(nxt, v.pos, haz, self.p)
            self._spot_t = v.t
        return self._spot

    def reposition_legs(self, v: View) -> tuple:
        q = self.p.reposition
        spot = self._spot_for(v)
        if dist2(spot, v.pos) <= q.arrive_m:
            self.nav_reset()
            return ((0.0, 0.0), False, False)
        if self._stalled(v, spot):
            self._spot = None
        chased = any((e.d3 <= q.hurry_range_m and e.fresh for e in v.enemies))
        return self.go(v, spot, 1.0, quiet=self.prof.quiet and (not chased) or self.opening_quiet(v))

    def _debug(self, v: View, mode: Mode, target: Enemy | None) -> dict:
        d: dict = {'label': f"{mode.name}{(':' + mode.note if mode.note else '')} hp{v.hp_frac:.0%} {self.detect.label}"}
        z = v.pos[2] + v.stats.get('muzzle_height', self.cfg['mech']['muzzle_height'])
        if target is not None:
            d['lines'] = [{'a': [v.pos[0], v.pos[1], z], 'b': [target.pos[0], target.pos[1], target.center[2]], 'color': '#ff5040'}]
        goal = mode.goal or (self._spot if mode.name == 'reposition' else None)
        if goal:
            d['points'] = [{'at': [goal[0], goal[1], v.pos[2]], 'color': '#40c0ff', 'size': 6}]
        return d
