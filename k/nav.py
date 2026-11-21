from __future__ import annotations
import math
from collections import deque
from typing import NamedTuple
from .params import Params
from .util import rot2, unit2

class Steer(NamedTuple):
    move: tuple
    jump: bool = False
    jet: bool = False

class NullWorld:

    def los(self, a, b) -> bool | None:
        return None

    def cover_dir(self, pos, threat_pos):
        return

class LegacyNav:

    def __init__(self, params: Params, rng):
        self.p = params
        self.rng = rng
        self._hist: deque = deque()
        self._last_t = -1000000000.0
        self._last_heading: tuple | None = None
        self._clock = 0.0
        self._rec_since: float | None = None
        self._rec_from: tuple = (0.0, 0.0)
        self._rec_side = 1.0
        self._cooldown_until = -1000000000.0

    def reset(self) -> None:
        self._hist.clear()
        self._rec_since = None
        self._last_heading = None

    @property
    def recovering(self) -> bool:
        return self._rec_since is not None

    def steer(self, me: dict, goal_xy, speed: float=1.0, t: float | None=None) -> Steer:
        p = self.p.nav
        if t is None:
            self._clock += 0.1
            t = self._clock
        x, y = (me['pos'][0], me['pos'][1])
        dx, dy = (goal_xy[0] - x, goal_xy[1] - y)
        dist = math.hypot(dx, dy)
        if t - self._last_t > p.gap_reset_s:
            self._hist.clear()
            self._rec_since = None
        self._last_t = t
        if dist < p.arrive_m:
            self._hist.clear()
            self._rec_since = None
            return Steer((0.0, 0.0))
        heading = unit2(dx, dy)
        lh = self._last_heading
        self._last_heading = heading
        if lh is not None and heading[0] * lh[0] + heading[1] * lh[1] < math.cos(math.radians(p.heading_reset_deg)):
            self._hist.clear()
            self._rec_since = None
        scale = speed * min(1.0, dist / p.slow_m)
        walk = me.get('stats', {}).get('walk_speed', 0.0)
        self._hist.append((t, x, y))
        while self._hist and t - self._hist[0][0] > p.stuck_window_s:
            self._hist.popleft()
        if self._rec_since is None:
            span = t - self._hist[0][0]
            if span >= p.stuck_window_s * p.stuck_span_frac and t >= self._cooldown_until:
                moved = math.hypot(x - self._hist[0][1], y - self._hist[0][2])
                if moved < p.stuck_frac * speed * walk * span:
                    self._rec_since = t
                    self._rec_from = (x, y)
                    self._rec_side = 1.0 if self.rng.random() < 0.5 else -1.0
            if self._rec_since is None:
                return Steer((heading[0] * scale, heading[1] * scale))
        el = t - self._rec_since
        if math.hypot(x - self._rec_from[0], y - self._rec_from[1]) >= p.unstick_m and el > p.slide_s or el > p.recover_max_s:
            self._rec_since = None
            self._hist.clear()
            self._cooldown_until = t + p.cooldown_s
            return Steer((heading[0] * scale, heading[1] * scale))
        stage = int(el // (p.slide_s + p.jet_s))
        within = el - stage * (p.slide_s + p.jet_s)
        side = self._rec_side * (1.0 if stage % 2 == 0 else -1.0)
        if within < p.slide_s:
            v = rot2(heading, side * math.radians(p.slide_deg))
            return Steer((v[0] * speed, v[1] * speed))
        grounded = bool(me.get('grounded', True))
        energy_ok = me.get('energy', 0.0) >= p.jet_min_energy_frac * me.get('max_energy', 0.0)
        return Steer((heading[0] * speed, heading[1] * speed), jump=grounded, jet=not grounded and energy_ok)

def steer(nav: LegacyNav, self_state: dict, goal_xy, speed: float=1.0, t: float | None=None) -> tuple:
    return nav.steer(self_state, goal_xy, speed, t).move

class Spot(tuple):

    def __new__(cls, x, y, z=0.0, **info):
        s = super().__new__(cls, (float(x), float(y)))
        s.z = float(z)
        s.info = info
        return s

    @property
    def xyz(self):
        return (self[0], self[1], self.z)

class HighGround(NamedTuple):
    stand: Spot
    takeoff: Spot
    roof_z: float
    height: float = 0.0
    back_m: float = 0.0
    ground_vis: float = 0.0
    cost_m: float = 0.0
    climb_m: float = 0.0

def _xy(p):
    return (float(p[0]), float(p[1]))

class _Plan:
    __slots__ = ('born', 'built', 'direct', 'goal', 'jets', 'k', 'kinds', 'nodes', 'search')

    def __init__(self, goal, born, jets):
        self.goal = goal
        self.born = born
        self.jets = jets
        self.direct = False
        self.search = None
        self.nodes: list = []
        self.kinds: list = []
        self.k = 0
        self.built = False

class Nav:

    def __new__(cls, *args, **kw):
        if args and (not isinstance(args[0], dict)):
            return LegacyNav(*args, **kw)
        return object.__new__(cls)

    def __init__(self, hello: dict, *, params=None, world=None, cache_dir: str | None=None):
        from .terrain import Terrain, load_nav_params, load_world
        cfg = hello['config']
        self.cfg = cfg
        self.m = dict(cfg['mech'])
        self.hz = float(cfg['match']['tick_hz'])
        self.dt = cfg['match']['decide_every'] / self.hz
        self.vision = cfg['vision']
        self.sound = cfg['sound']
        self.p = params or load_nav_params()
        self.world = world or load_world(hello, cache_dir)
        self.T = Terrain(self.world, self.m, self.hz, self.p)
        self._climb_cache: dict = {}
        self._clock = 0.0
        self._plan: _Plan | None = None
        self._link: dict | None = None
        self._blocked: dict = {}
        self._hist: deque = deque()
        self._recover_until = -1.0
        self._recover_side = 1.0
        self._bad_until = -1.0
        self.last_info = ''
        self._cache: dict = {}
        self._pockets = None
        from . import sight
        self._roof_sites = sight.roof_sites(self)

    def mech_now(self, me: dict) -> dict:
        st = me.get('stats')
        return {**self.m, **st} if st else self.m

    def climb_height(self, me: dict | None=None, energy: float | None=None) -> float:
        from .terrain import jet_climb
        m = self.mech_now(me) if me else self.m
        e = energy if energy is not None else me['energy'] if me else m['energy']
        key = (round(float(e), 1), m['jet_drain'], m['jet_accel'], m['max_rise_speed'], m['jump_speed'], m['gravity'])
        v = self._climb_cache.get(key)
        if v is None:
            v = jet_climb(m, self.hz, e)
            if len(self._climb_cache) > 512:
                self._climb_cache.clear()
            self._climb_cache[key] = v
        return v

    def climb_budget(self, me: dict, max_climb: float | None=None) -> float:
        b = self.climb_height(me) * self.p.grid.climb_safety
        return min(b, max_climb) if max_climb is not None else b

    def _quiet_frac(self, m: dict) -> float:
        return min(1.0, 0.9 * self.sound['step_speed'] / max(m['walk_speed'], 1e-06))

    def open_ground(self, xy) -> bool:
        T = self.T
        i = min(max(math.floor(xy[0] - T.x0 + 0.5), 0), T.nx - 1)
        j = min(max(math.floor(xy[1] - T.y0 + 0.5), 0), T.ny - 1)
        return bool(T.open1[j, i])

    def step_clear(self, a_xy, b_xy) -> bool:
        return bool(self.T.line_clear(a_xy[0], a_xy[1], b_xy[0], b_xy[1]))

    def path_length(self, a_xyz, b_xy) -> float:
        T = self.T
        key = (T.node_of(a_xyz[0], a_xyz[1]), T.node_of(b_xy[0], b_xy[1]))
        hit = self._cache.get(('len',) + key)
        if hit is not None:
            return hit
        v = self._path_length(a_xyz, b_xy)
        if len(self._cache) > 4096:
            self._cache.clear()
        self._cache[('len',) + key] = v
        return v

    def _path_length(self, a, b) -> float:
        T = self.T
        na, da = T.snap(a[0], a[1])
        nb, db = T.snap(b[0], b[1])
        if na is None or nb is None:
            return math.inf
        if na == nb:
            return da + db + math.hypot(a[0] - b[0], a[1] - b[1]) * 0.0
        ca, cb = (int(T.compf[na]), int(T.compf[nb]))
        if not T.comp_reach(ca, cb, 0.0, False):
            return math.inf
        ax, ay = T.xy_of(na)
        bx, by = T.xy_of(nb)
        if T.line_clear(ax, ay, bx, by):
            return da + db + math.hypot(ax - bx, ay - by)
        s = T.search(na, nb, climb=0.0, jets=False, weight=1.0)
        s.run(self.p.search.length_budget)
        if s.status == 'found':
            nodes = [n for n, _ in s.path()]
            kept = T.smooth(nodes, set())
            return da + db + self._polyline(kept)
        if s.status == 'none':
            return math.inf
        bx0, by0 = T.xy_of(s.best)
        part = [n for n, _ in s.partial()]
        got = self._polyline(part)
        rest = math.hypot(bx0 - bx, by0 - by)
        return da + db + got + 1.25 * rest

    def _polyline(self, nodes) -> float:
        T = self.T
        pts = [T.xy_of(n) for n in nodes]
        return sum((math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1)))

    def has_los(self, a_xyz, b_xyz) -> bool:
        d = math.dist(a_xyz, b_xyz)
        trees = d > self.vision['near_range'] + self.m.get('near_range_bonus', 0.0)
        return not self.world.sight_blocked(tuple(a_xyz), tuple(b_xyz), trees=trees)

    def shot_clear(self, a_xyz, b_xyz) -> bool:
        return self.world.segment_hit(tuple(a_xyz), tuple(b_xyz)) is None

    def on_roof(self, me: dict) -> bool:
        T = self.T
        x, y, z = me['pos'][:3]
        i = min(max(math.floor(x - T.x0 + 0.5), 0), T.nx - 1)
        j = min(max(math.floor(y - T.y0 + 0.5), 0), T.ny - 1)
        return bool(T.isB[j, i] and T.S[j, i] - T.G[j, i] >= self.p.grid.min_roof_height_m and (z > T.G[j, i] + 1.0))

    def route(self, me: dict, goal_xy, *, jets: bool=True, budget: int=1500, max_climb=None):
        T = self.T
        pos = me['pos']
        na, _ = T.snap(pos[0], pos[1])
        nb, _ = T.snap(goal_xy[0], goal_xy[1])
        if na is None or nb is None:
            return None
        s = T.search(na, nb, climb=self.climb_budget(me, max_climb) if jets else 0.0, jets=jets)
        if s.run(budget) != 'found':
            return None
        path = s.path()
        links = []
        first = None
        for i, (n, k) in enumerate(path):
            if k >= 0:
                a = path[i - 1][0]
                links.append((k, T.xy_of(a), T.xy_of(n)))
                if first is None and k != 0:
                    first = (*T.xy_of(a), T.z_of(a))
        return (s.g[nb], first, links)

    def cover_from(self, me, threats_xyz, *, max_dist=60.0):
        from . import sight
        return sight.cover_from(self, me, threats_xyz, max_dist)

    def peek_spot(self, me, target_xyz, *, max_dist=40.0, max_range=None):
        from . import sight
        return sight.peek_spot(self, me, target_xyz, max_dist, max_range)

    def roof_edge_peek(self, me, target_xyz):
        from . import sight
        return sight.roof_edge_peek(self, me, target_xyz)

    def ground_can_see(self, xyz, *, min_range=0.0, max_range=150.0) -> float:
        from . import sight
        return sight.ground_can_see(self, xyz, min_range, max_range)

    def high_ground(self, me, circle, *, max_climb=None, min_height=12.0, stand_back=6.0):
        from . import sight
        return sight.high_ground(self, me, circle, max_climb, min_height, stand_back)

    def pockets(self) -> list:
        if self._pockets is None:
            from . import pockets
            self._pockets = pockets.find_pockets(self)
        return self._pockets

    def pocket(self, me, circle):
        from . import pockets
        return pockets.nearest(self, me, circle)

    def reset(self) -> None:
        self._plan = None
        self._link = None
        self._hist.clear()

    def steer(self, me: dict, goal_xy, *, quiet: bool=False, t: float | None=None) -> dict:
        if isinstance(goal_xy, HighGround):
            goal_xy = goal_xy.stand
        self._clock = self._clock + self.dt if t is None else float(t)
        T, p = (self.T, self.p.steer)
        m = self.mech_now(me)
        pos = me['pos']
        px, py = (float(pos[0]), float(pos[1]))
        gx, gy = _xy(goal_xy)
        grounded = bool(me.get('grounded', True))
        qf = self._quiet_frac(m)
        energy_frac = me.get('energy', 0.0) / max(me.get('max_energy', 1.0), 1e-06)
        if self._link is not None:
            out = self._run_link(me, m, quiet)
            if out is not None:
                return out
        dist_goal = math.hypot(gx - px, gy - py)
        if dist_goal < p.arrive_m:
            self._hist.clear()
            self.last_info = 'arrived'
            return self._out(0.0, 0.0)
        if self._clock < self._recover_until:
            u = unit2(gx - px, gy - py)
            v = rot2(u, self._recover_side * math.radians(80))
            self.last_info = 'recover'
            return self._out(v[0] * (qf if quiet else 1.0), v[1] * (qf if quiet else 1.0), jump=grounded and (not quiet))
        plan = self._plan
        jets = not quiet
        if plan is None or plan.jets != jets or math.hypot(plan.goal[0] - gx, plan.goal[1] - gy) > p.replan_goal_m or (self._clock - plan.born > p.replan_every_s) or self._off_path(plan, px, py):
            plan = self._new_plan(me, m, (px, py), (gx, gy), jets)
            self._plan = plan
        if plan.direct and (not T.line_clear(px, py, gx, gy)):
            plan = self._new_plan(me, m, (px, py), (gx, gy), jets)
            self._plan = plan
        if plan.search is not None and (not plan.built):
            st = plan.search.run(self.p.search.steer_budget)
            if st == 'found':
                self._adopt(plan, plan.search.path())
            elif st == 'none':
                plan.search = None
                plan.direct = True
                self.last_info = 'no path'
        nodes = plan.nodes if plan.built else None
        provisional = False
        if nodes is None and plan.search is not None:
            part = plan.search.partial()
            nodes = [n for n, _ in part]
            kinds = [k for _, k in part]
            provisional = True
        else:
            kinds = plan.kinds
        vx, vy, _tx, _ty, remaining, link_start = self._follow(plan, nodes, kinds, provisional, px, py, gx, gy, grounded, me, m, quiet)
        if link_start:
            out = self._run_link(me, m, quiet)
            if out is not None:
                return out
        scale = qf if quiet else 1.0
        if remaining is not None and remaining < p.slow_m:
            scale = min(scale, max(0.25, remaining / p.slow_m))
        boost = False
        if not quiet and grounded and (energy_frac >= p.boost_min_energy_frac) and (remaining is not None) and (remaining >= p.boost_min_remaining_m) and (not self._jet_ahead(plan)):
            boost = True
        self._note_motion(px, py, scale if vx or vy else 0.0, grounded, m)
        if self._stuck(m):
            self._on_stuck(px, py, vx, vy, quiet)
        n = math.hypot(vx, vy)
        if n > 1e-09:
            vx, vy = (vx / n * scale, vy / n * scale)
        return self._out(vx, vy, boost=boost)

    @staticmethod
    def _out(vx, vy, boost=False, jump=False) -> dict:
        return {'move': [round(float(vx), 4), round(float(vy), 4)], 'boost': bool(boost), 'jump': bool(jump)}

    def _new_plan(self, me, m, start, goal, jets) -> _Plan:
        T = self.T
        plan = _Plan(goal, self._clock, jets)
        self._link = None
        if T.line_clear(start[0], start[1], goal[0], goal[1]):
            plan.direct = True
            return plan
        na, _ = T.snap(start[0], start[1])
        nb, _ = T.snap(goal[0], goal[1])
        if na is None or nb is None:
            plan.direct = True
            return plan
        self._blocked = {n: e for n, e in self._blocked.items() if e > self._clock}
        blocked = {n: e for n, e in self._blocked.items() if n != na and n != nb}
        plan.search = T.search(na, nb, climb=self.climb_budget(me) if jets else 0.0, jets=jets, blocked=blocked)
        return plan

    def _adopt(self, plan: _Plan, path: list) -> None:
        T = self.T
        nodes = [n for n, _ in path]
        kind_of = {n: k for n, k in path if k >= 0}
        hard = set()
        for i, (n, k) in enumerate(path):
            if k >= 0:
                hard.add(n)
                hard.add(path[i - 1][0])
        kept = T.smooth(nodes, hard)
        plan.nodes = kept
        plan.kinds = [kind_of.get(n, -1) for n in kept]
        plan.k = 1 if len(kept) > 1 else 0
        plan.built = True

    def _off_path(self, plan: _Plan, px, py) -> bool:
        if plan.direct or not plan.built or (not plan.nodes):
            return False
        T = self.T
        lo, hi = (max(0, plan.k - 2), min(len(plan.nodes), plan.k + 2))
        d = min((math.hypot(T.xy_of(n)[0] - px, T.xy_of(n)[1] - py) for n in plan.nodes[lo:hi]))
        return d > self.p.steer.replan_off_path_m

    def _jet_ahead(self, plan: _Plan) -> bool:
        from .terrain import JET
        return bool(plan.built and any((k == JET for k in plan.kinds[plan.k:])))

    def _follow(self, plan, nodes, kinds, provisional, px, py, gx, gy, grounded, me, m, quiet):
        T, p = (self.T, self.p.steer)
        if plan.direct or not nodes:
            self.last_info = 'direct'
            return (gx - px, gy - py, gx, gy, math.hypot(gx - px, gy - py), False)
        n = len(nodes)
        k = plan.k if not provisional else 1
        while k <= n - 1:
            nx_, ny_ = T.xy_of(nodes[k])
            reach = p.node_reach_m
            nxt_is_link = k + 1 < n and kinds[k + 1] >= 0
            if k >= 1 and kinds[k] >= 0:
                k -= 1
                continue
            if nxt_is_link:
                reach = p.link_reach_m
            if math.hypot(nx_ - px, ny_ - py) < reach:
                if nxt_is_link and (not provisional):
                    plan.k = k
                    if grounded and self._begin_link(nodes[k], nodes[k + 1], kinds[k + 1], me, m):
                        return (0.0, 0.0, nx_, ny_, None, True)
                    return (0.0, 0.0, nx_, ny_, None, False)
                k += 1
                continue
            break
        if not provisional:
            plan.k = min(k, n)
        if k >= n:
            self.last_info = 'final'
            return (gx - px, gy - py, gx, gy, math.hypot(gx - px, gy - py), False)
        limit = n - 1
        for j in range(k, n - 1):
            if kinds[j + 1] >= 0:
                limit = j
                break
        best = k
        acc = 0.0
        prev = T.xy_of(nodes[k])
        for j in range(k + 1, limit + 1):
            q = T.xy_of(nodes[j])
            acc += math.hypot(q[0] - prev[0], q[1] - prev[1])
            prev = q
            if acc > p.lookahead_m:
                break
            if T.line_clear(px, py, q[0], q[1]):
                best = j
        tx, ty = T.xy_of(nodes[best])
        if best == n - 1 and (not provisional):
            tx, ty = (gx, gy)
        rem = self._remaining(nodes, k, px, py) + math.hypot(T.xy_of(nodes[-1])[0] - gx, T.xy_of(nodes[-1])[1] - gy)
        self.last_info = ('search ' if provisional else 'path ') + f'{k}/{n}'
        return (tx - px, ty - py, tx, ty, rem, False)

    def _remaining(self, nodes, k, px, py) -> float:
        T = self.T
        pts = [(px, py)] + [T.xy_of(n) for n in nodes[k:]]
        return sum((math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1)))

    def _begin_link(self, src: int, dst: int, kind: int, me, m) -> bool:
        T = self.T
        info = None
        for q in range(T.lk_off[src], T.lk_off[src + 1]):
            if T.lk_dst[q] == dst and T.lk_kind[q] == kind:
                info = (float(T.link_req[q]), float(T.link_need[q]))
                break
        if info is None:
            return False
        dx, dy = T.xy_of(dst)
        self._link = {'kind': kind, 'src': src, 'dst': dst, 'req': info[0], 'need': info[1], 'z0': T.z_of(src), 'dst_xy': (dx, dy), 'dst_z': T.z_of(dst), 't0': self._clock, 'jumped': False, 'wait0': None}
        return True

    def _surface(self, x, y):
        return self.T.surface(x, y)

    def _run_link(self, me, m, quiet):
        from .terrain import DROP, JUMP
        T = self.T
        L = self._link
        px, py, pz = (float(me['pos'][0]), float(me['pos'][1]), float(me['pos'][2]))
        grounded = bool(me.get('grounded', True))
        el = self._clock - L['t0']
        dx, dy = (L['dst_xy'][0] - px, L['dst_xy'][1] - py)
        dist = math.hypot(dx, dy)
        u = unit2(dx, dy)
        kind = L['kind']
        s_here = self._surface(px, py)
        at_level = abs(s_here - L['dst_z']) <= T.step_up + 0.3 and T.clear_around(px, py)
        self.last_info = f"link {('drop', 'jump', 'jet')[kind]} {el:.1f}s"

        def finish(ok: bool):
            plan = self._plan
            if ok and plan is not None and plan.built:
                try:
                    plan.k = plan.nodes.index(L['dst']) + 1
                except ValueError:
                    self._plan = None
            else:
                self._blocked[L['dst']] = self._clock + 30.0
                self._plan = None
            self._link = None
            self._hist.clear()
        if kind == DROP:
            if grounded and el > 0.4 and at_level:
                finish(True)
                return None
            if el > 8.0:
                finish(False)
                return None
            f = self.p.steer.drop_speed_frac
            if quiet:
                f = min(f, self._quiet_frac(m))
            if not grounded and dist < 2.5:
                f = 0.0
            return self._out(u[0] * f, u[1] * f)
        if kind == JUMP:
            if grounded and L['jumped'] and (el > 0.3):
                if at_level or dist < 3.5:
                    finish(True)
                else:
                    finish(False)
                return None
            if el > 6.0:
                finish(False)
                return None
            jump = grounded and (not L['jumped'])
            L['jumped'] = L['jumped'] or jump
            f = self._quiet_frac(m) if quiet else 1.0
            return self._out(u[0] * f, u[1] * f, jump=jump)
        if el > 14.0:
            finish(False)
            return None
        if grounded and (not L['jumped']):
            have = self.climb_height(me)
            if have < L['req'] - 1e-06:
                if L['wait0'] is None:
                    L['wait0'] = self._clock
                if self._clock - L['wait0'] > 10.0:
                    finish(False)
                    return None
                L['t0'] = self._clock
                return self._out(0.0, 0.0)
            L['jumped'] = True
            return self._out(u[0], u[1], jump=True)
        if grounded and L['jumped'] and (el > 0.5):
            if at_level:
                finish(True)
            else:
                finish(False)
            return None
        peak = L['z0'] + L['need'] - self.p.grid.jet_clearance_m
        need_z = peak + 0.8
        over = at_level
        energy = me.get('energy', 0.0)
        boost = not over and pz < need_z and (energy > 0.0)
        return self._out(u[0], u[1], boost=boost)

    def _note_motion(self, px, py, frac, grounded, m):
        self._hist.append((self._clock, px, py, frac, grounded))
        w = self.p.steer.stuck_window_s
        while self._hist and self._clock - self._hist[0][0] > w + 1e-09:
            self._hist.popleft()

    def _stuck(self, m) -> bool:
        p = self.p.steer
        h = self._hist
        if len(h) < 3 or self._clock - h[0][0] < p.stuck_window_s * 0.9:
            return False
        if not all((x[4] for x in h)):
            return False
        frac = sum((x[3] for x in h)) / len(h)
        if frac < 0.3:
            return False
        span = h[-1][0] - h[0][0]
        moved = math.hypot(h[-1][1] - h[0][1], h[-1][2] - h[0][2])
        return moved < p.stuck_frac * frac * m['walk_speed'] * span

    def _on_stuck(self, px, py, vx, vy, quiet):
        T = self.T
        u = unit2(vx, vy)
        ttl = self._clock + self.p.steer.block_ttl_s
        for d in (3.0, 5.0):
            n = T.node_of(px + u[0] * d, py + u[1] * d)
            self._blocked[n] = ttl
        self._plan = None
        self._hist.clear()
        self._recover_until = self._clock + self.p.steer.recover_s
        self._recover_side = -self._recover_side
