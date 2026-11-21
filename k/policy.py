from __future__ import annotations
import math
import sys
import traceback
from dataclasses import dataclass, field
from . import aim as aimmod
from . import loot as lootmod
from . import zone as zonemod
from .fire import Gunnery
from .memory import Memory
from .nav import Nav, NullWorld
from .params import Params, load_params
from .threat import Assessor, Enemy, View
from .upgrades import UpgradePicker
from .util import clamp, dist2, rot2, unit2, wrap

@dataclass
class Mode:
    name: str
    target: Enemy | None = None
    goal: tuple | None = None
    direction: tuple | None = None
    desperate: bool = False
    note: str = ''
    extra: dict = field(default_factory=dict)

class Bot:
    name = 'k2'

    def __init__(self, hello: dict, params: Params | None=None, world=None):
        self.hello = hello
        self.cfg = hello['config']
        self.p = params or load_params()
        self.mem = Memory(hello, self.p)
        self.rng = self.mem.rng
        self.world = world or NullWorld()
        self.nav = Nav(self.p, self.rng)
        self.assess = Assessor(self.cfg, self.p, self.mem)
        self.gun = Gunnery(self.cfg, self.p, self.mem, self.world)
        self.picker = UpgradePicker(self.cfg['upgrades'], self.p)
        self.strict = False
        self.mode = 'start'
        self.last: Mode = Mode('start')
        self.view: View | None = None
        self._errors = 0
        self._dis_since: float | None = None
        self._cornered_until = -1000000000.0
        self._fight_id: int | None = None
        self._strafe = 1.0
        self._strafe_until = -1000000000.0
        self._dodge_side = 1.0
        self._dodge_until = -1000000000.0
        self._spot: tuple | None = None
        self._spot_t = -1000000000.0
        self._pick_offer = None
        self._pick_t = -1000000000.0

    def act(self, obs: dict) -> dict:
        try:
            return self._act(obs)
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

    def _act(self, obs: dict) -> dict:
        self.mem.update(obs)
        self.gun.observe(obs['self']['weapons'])
        v = self.assess.build(obs)
        self.view = v
        mode = self.choose_mode(v)
        self.last = mode
        self.mode = mode.name
        move, boost, jump = self.legs(v, mode)
        target = self.aim_target(v, mode)
        fp = self.gun.plan(v, mode.name, target, self.fire_allowed(v, mode, target), move)
        act: dict = {'tick': obs['tick'], 'move': [round(move[0], 4), round(move[1], 4)], 'boost': bool(boost), 'jump': bool(jump), 'aim': [round(fp.aim[0], 4), round(fp.aim[1], 4)], 'fire': [bool(fp.fire[0]), bool(fp.fire[1])], 'reload': bool(fp.reload)}
        if obs.get('offer') and (obs['offer'].get('id') != self._pick_offer or v.t - self._pick_t > self.p.upgrades.repick_s):
            act['pick'] = self.picker.choose(obs['offer'], v.held())
            self._pick_offer, self._pick_t = (obs['offer'].get('id'), v.t)
        self._loot_actions(v, act)
        if self.p.debug.enabled:
            act['debug'] = self._debug(v, mode, target)
        return act

    def choose_mode(self, v: View) -> Mode:
        if v.zone.urgent:
            return Mode('zone', goal=v.zone.goal)
        dodge = self.dodge_plan(v)
        if dodge is not None:
            return dodge
        if self.should_disengage(v):
            seen = [e for e in v.enemies if e.fresh and e.sees_us]
            return Mode('disengage', v.attackers[0] if v.attackers else seen[0] if seen else None)
        self._dis_since = None
        fight = self.fight_target(v)
        if fight is not None:
            self._fight_id = fight.id
            return Mode('fight', fight, desperate=v.t < self._cornered_until)
        self._fight_id = None
        goal = self.drop_goal(v)
        if goal is not None:
            return Mode('drop', goal=goal)
        if self.should_recover(v):
            return Mode('recover')
        goal = self.loot_goal(v)
        if goal is not None:
            return Mode('loot', goal=goal)
        return Mode('reposition')

    def dodge_plan(self, v: View) -> Mode | None:
        q = self.p.dodge
        pos = v.pos
        best: Enemy | None = None
        for e in v.enemies:
            if not (e.charging and e.fresh and e.sees_us and (e.d3 <= q.laser_range_m)):
                continue
            if e.aim_off > math.radians(q.aim_deg) + math.atan2(v.radius, max(e.d3, 1.0)):
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
            d = self.toward_cover(d, pos, e.pos)
            return Mode('dodge', e, direction=self.keep_inside(v, d), note='laser')
        for pr in self.mem.projectiles:
            kind = pr.get('kind')
            if kind == 'npc' and (not q.include_npc):
                continue
            hit = self._incoming(v, pr)
            if hit is not None:
                return Mode('dodge', None, direction=self.keep_inside(v, hit), note=kind)
        return None

    def toward_cover(self, d: tuple, pos, threat_pos) -> tuple:
        cover = self.world.cover_dir(pos, threat_pos)
        if not cover:
            return d
        mix = self.p.nav.cover_mix
        mixed = unit2(d[0] * (1.0 - mix) + cover[0] * mix, d[1] * (1.0 - mix) + cover[1] * mix)
        return mixed if mixed != (0.0, 0.0) else d

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

    def fight_target(self, v: View) -> Enemy | None:
        q = self.p.fight
        atk = v.attackers
        if v.t < self._cornered_until and atk:
            return min(atk, key=lambda e: e.tr.hp if e.tr.hp is not None else 1000000000.0)
        if len(atk) == 1:
            return atk[0]
        if len(atk) > 1:
            return None
        seen = [e for e in v.enemies if e.fresh and e.in_view and (e.d3 <= q.consider_range_m)]
        if len(seen) != 1:
            return None
        e = seen[0]
        for o in v.enemies:
            if o is not e and o.age <= q.third_party_age_s and (o.d3 <= q.third_party_m or dist2(o.pos, e.pos) <= q.third_party_m):
                return None
        if e.unaware or e.advantage >= q.min_advantage or e.id == self._fight_id:
            return e
        return None

    def drop_goal(self, v: View) -> tuple | None:
        q = self.p.drop
        me_id = v.me['id']
        best, best_d = (None, 1e+18)
        for d in v.obs.get('drops', []):
            if d.get('holder') not in (None, me_id):
                continue
            pos = d['pos']
            dist = dist2(pos, v.pos)
            if d['lands_in'] > dist / (v.walk * q.speed_frac) + q.lead_s:
                continue
            if not (v.zone.next.contains(pos, self.p.zone.margin_m) and v.zone.now.contains(pos, self.p.zone.margin_m)):
                continue
            theirs = [dist2(e.pos, pos) for e in v.enemies if e.age <= q.enemy_age_s]
            if theirs and (min(theirs) < q.enemy_clear_m or min(theirs) < dist * q.closest_factor):
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

    def loot_goal(self, v: View) -> tuple | None:
        q = self.p.loot
        if v.attackers or any((e.d3 <= q.enemy_close_m and e.age <= q.enemy_age_s for e in v.enemies)):
            return None
        pts = self.enemy_points(v)
        goal, goal_d = (None, 1e+18)
        pk = lootmod.best_pickup(v.me, self.mem, self.p, v.laser_threat, pts)
        if pk is not None:
            goal, goal_d = ((pk.pos[0], pk.pos[1]), dist2(pk.pos, v.pos) - q.weapon_bonus_m)
        cube = lootmod.best_cube(v.me, self.mem, self.p, pts)
        if cube is not None and dist2(cube.pos, v.pos) < goal_d:
            goal = (cube.pos[0], cube.pos[1])
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

    def aim_target(self, v: View, mode: Mode) -> Enemy | None:
        if mode.target is not None and mode.name in ('fight', 'dodge', 'disengage'):
            return mode.target
        if v.attackers:
            return v.attackers[0]
        return v.props[0] if v.props and mode.name != 'zone' else None

    def fire_allowed(self, v: View, mode: Mode, target: Enemy | None) -> bool:
        if target is None or mode.name == 'disengage':
            return False
        return mode.name == 'fight' or target.engaged or (target.prop and (not v.attackers))

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

    def go(self, v: View, goal, speed: float, boost: bool=False) -> tuple:
        st = self.nav.steer(v.me, goal, speed, v.t)
        energy_ok = v.energy_frac >= self.p.nav.boost_min_energy_frac
        run = boost and energy_ok and v.me.get('grounded', True) and (math.hypot(*st.move) > 0.0)
        return (st.move, run or st.jet, st.jump)

    def go_dir(self, v: View, d, speed: float, boost: bool=False) -> tuple:
        look = self.p.zone.lookahead_m
        return self.go(v, (v.pos[0] + d[0] * look, v.pos[1] + d[1] * look), speed, boost)

    def legs(self, v: View, mode: Mode) -> tuple:
        name = mode.name
        if name == 'zone':
            return self.go(v, mode.goal, 1.0, v.zone.hurry)
        if name == 'dodge':
            return self.go_dir(v, mode.direction, 1.0)
        if name == 'disengage':
            return self.disengage_legs(v)
        if name == 'fight':
            return self.fight_legs(v, mode.target)
        if name == 'drop':
            x, y, r = mode.goal
            if dist2((x, y), v.pos) <= r * self.p.drop.ring_frac:
                self.nav.reset()
                return ((0.0, 0.0), False, False)
            return self.go(v, (x, y), 1.0)
        if name == 'recover':
            return self.recover_legs(v)
        if name == 'loot':
            return self.go(v, mode.goal, self.p.loot.speed_frac)
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
            d = self.toward_cover(d, v.pos, v.attackers[0].pos)
        d = self.keep_inside(v, d) if d != (0.0, 0.0) else d
        goal = (v.pos[0] + d[0] * q.flee_m, v.pos[1] + d[1] * q.flee_m)
        goal = zonemod.clamp_into(self.safe_circle(v), goal, self.p.zone.margin_m)
        near = min((e.d3 for e in v.attackers), default=1000000000.0)
        return self.go(v, goal, 1.0, near <= q.boost_range_m and v.energy_frac >= q.boost_min_energy_frac)

    def fight_legs(self, v: View, e: Enemy) -> tuple:
        q = self.p.fight
        band = self._band(v, e)
        lo, hi = band
        mid = 0.5 * (lo + hi)
        fw = clamp((e.d - mid) / max(0.5 * (hi - lo), 1.0), -1.0, 1.0)
        toward = unit2(e.pos[0] - v.pos[0], e.pos[1] - v.pos[1])
        if v.t >= self._strafe_until:
            if self.rng.random() < q.strafe_flip_prob:
                self._strafe = -self._strafe
            self._strafe_until = v.t + self.rng.uniform(q.strafe_min_s, q.strafe_max_s)
        side = rot2(toward, self._strafe * math.pi / 2.0)
        d = unit2(toward[0] * fw * q.radial_w + side[0] * q.strafe_w, toward[1] * fw * q.radial_w + side[1] * q.strafe_w)
        d = self.keep_inside(v, d)
        speed = 1.0
        if e.unaware and (not e.engaged) and (e.d > q.sneak_range_m):
            speed = self.quiet_speed(v)
        return self.go_dir(v, d, speed)

    def _band(self, v: View, e: Enemy) -> tuple:
        q = self.p.fight
        kinds = [w['kind'] for w in v.weapons if w]
        pick = None
        if 'lightning' in kinds and 'laser' in e.holds:
            pick = 'lightning'
        else:
            for k in self.p.weapons.rank:
                if k in kinds:
                    pick = k
                    break
        b = getattr(q.bands, pick or 'gun', q.bands.gun)
        return (b[0], b[1])

    def recover_legs(self, v: View) -> tuple:
        q = self.p.recover
        known = [e for e in v.enemies if e.age <= q.enemy_age_s]
        dmin = min((e.d for e in known), default=1000000000.0)
        noise = [s for s in self.mem.recent_sounds(q.sound_s, tuple(q.sound_kinds)) if dist2((s.ex, s.ey), v.pos) < q.sound_radius_m]
        if dmin >= q.hold_safe_m and (not noise):
            self.nav.reset()
            return ((0.0, 0.0), False, False)
        goal = self._spot_for(v, self.p.reposition.hazard_mult_recover)
        panic = dmin < q.panic_m
        return self.go(v, goal, 1.0 if panic else self.quiet_speed(v))

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
        return out

    def _spot_for(self, v: View, mult: float=1.0) -> tuple:
        q = self.p.reposition
        haz = self.hazards(v, mult)
        nxt = v.zone.next
        spot = self._spot
        stale = spot is None or v.t - self._spot_t > q.replan_s or (not nxt.contains(spot, self.p.zone.margin_m)) or (zonemod.hazard_at(haz, spot) > q.abort_hazard) or (zonemod.hazard_at(haz, v.pos) > q.leave_hazard)
        if stale:
            self._spot = zonemod.pick_spot(nxt, v.pos, haz, self.p)
            self._spot_t = v.t
        return self._spot

    def reposition_legs(self, v: View) -> tuple:
        q = self.p.reposition
        spot = self._spot_for(v)
        if dist2(spot, v.pos) <= q.arrive_m:
            self.nav.reset()
            return ((0.0, 0.0), False, False)
        chased = any((e.d3 <= q.hurry_range_m and e.fresh for e in v.enemies))
        return self.go(v, spot, 1.0 if chased else self.quiet_speed(v))

    def _debug(self, v: View, mode: Mode, target: Enemy | None) -> dict:
        d: dict = {'label': f"{mode.name}{(':' + mode.note if mode.note else '')} hp{v.hp_frac:.0%}"}
        z = v.pos[2] + v.stats.get('muzzle_height', self.cfg['mech']['muzzle_height'])
        if target is not None:
            d['lines'] = [{'a': [v.pos[0], v.pos[1], z], 'b': [target.pos[0], target.pos[1], target.center[2]], 'color': '#ff5040'}]
        goal = mode.goal or (self._spot if mode.name == 'reposition' else None)
        if goal:
            d['points'] = [{'at': [goal[0], goal[1], v.pos[2]], 'color': '#40c0ff', 'size': 6}]
        return d
