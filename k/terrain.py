from __future__ import annotations
import heapq
import json
import math
import os
from array import array
from types import SimpleNamespace
import numpy as np
try:
    from mapgeom import World
except ImportError:
    from mapgeom import World
DROP, JUMP, JET = (0, 1, 2)
DIRS = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
_FORWARD = (0, 1, 2, 7)
SQ2 = math.sqrt(2.0)
INF = float('inf')

def _ns(d):
    return SimpleNamespace(**{k: _ns(v) if isinstance(v, dict) else v for k, v in d.items()})

def load_nav_params(path: str | None=None, overrides: dict | None=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'nav_params.json')
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    for sect, vals in (overrides or {}).items():
        d.setdefault(sect, {}).update(vals)
    return _ns(d)

def load_world(hello: dict, cache_dir: str | None=None):
    mdir = hello.get('map', {}).get('dir', '/arena')
    for cd in ([cache_dir] if cache_dir else []) + ['/tmp/nc', None]:
        try:
            return World.load(mdir, cache_dir=cd)
        except OSError:
            continue
    return World.load(mdir)

def jump_height(m: dict) -> float:
    return m['jump_speed'] ** 2 / (2.0 * m['gravity'])

def jet_climb(m: dict, hz: float, energy: float | None=None) -> float:
    e = float(m['energy'] if energy is None else energy)
    dt = 1.0 / hz
    g, acc, cap, drain = (m['gravity'], m['jet_accel'], m['max_rise_speed'], m['jet_drain'])
    vz = m['jump_speed'] - g * dt
    z = vz * dt
    top = z
    for _ in range(int(60 * hz)):
        if e > 0:
            if vz < cap:
                vz = min(vz + acc * dt, cap)
            e = max(0.0, e - drain * dt)
        vz -= g * dt
        z += vz * dt
        top = max(top, z)
        if e <= 0 and vz <= 0:
            break
    return top

def jet_rise_time(m: dict, hz: float, rise: float) -> float:
    dt = 1.0 / hz
    e = float(m['energy'])
    g, acc, cap, drain = (m['gravity'], m['jet_accel'], m['max_rise_speed'], m['jet_drain'])
    vz = m['jump_speed'] - g * dt
    z = vz * dt
    t = dt
    while z < rise and t < 60:
        if e > 0:
            if vz < cap:
                vz = min(vz + acc * dt, cap)
            e = max(0.0, e - drain * dt)
        elif vz <= 0:
            return INF
        vz -= g * dt
        z += vz * dt
        t += dt
    return t

def _slices(shape, dx, dy):
    ny, nx = shape
    p = (slice(max(0, -dy), ny - max(0, dy)), slice(max(0, -dx), nx - max(0, dx)))
    q = (slice(max(0, dy), ny - max(0, -dy)), slice(max(0, dx), nx - max(0, -dx)))
    return (p, q)

def _fp_reduce(a, offs, op):
    out = a.copy()
    for dx, dy in offs:
        if dx == 0 and dy == 0:
            continue
        p, q = _slices(a.shape, dx, dy)
        op(out[p], a[q], out=out[p])
    return out

def _shift(a, dx, dy, fill=False):
    out = np.full(a.shape, fill, a.dtype)
    p, q = _slices(a.shape, dx, dy)
    out[p] = a[q]
    return out

def _disc_offsets(r: float):
    n = math.floor(r)
    return [(dx, dy) for dy in range(-n, n + 1) for dx in range(-n, n + 1) if dx * dx + dy * dy <= r * r + 1e-09]

def label_components(edges, shape):
    ny, nx = shape
    lab = np.arange(ny * nx, dtype=np.int64).reshape(ny, nx)
    pairs = []
    for dI, dJ, ok in edges:
        p, q = _slices(shape, dI, dJ)
        pairs.append((p, q, ok[p]))
    while True:
        old = lab.copy()
        for p, q, okp in pairs:
            m = np.minimum(lab[p], lab[q])
            lp = lab[p]
            lq = lab[q]
            lp[okp] = np.minimum(lp[okp], m[okp])
            lq[okp] = np.minimum(lq[okp], m[okp])
            lab[p] = lp
            lab[q] = lq
        flat = lab.ravel()
        lab = flat[flat].reshape(ny, nx)
        if np.array_equal(lab, old):
            return lab

class Terrain:

    def __init__(self, world, mech: dict, hz: float, params):
        self.world = world
        self.m = dict(mech)
        self.hz = float(hz)
        self.p = params
        self.radius = float(mech['radius'])
        self.height = float(mech['height'])
        self.step_up = float(mech['step_up'])
        self.tan_slope = math.tan(math.radians(mech['max_slope_deg']))
        self.jump_h = jump_height(mech)
        self.climb_full = jet_climb(mech, hz)
        self._build_lattice()
        self._build_walk()
        self._build_coarse()
        self._build_components()
        self._reach_cache: dict = {}
        self._build_links()
        self._build_roofs()

    def _build_lattice(self):
        w = self.world
        minx, miny, maxx, maxy = w.bounds
        self.x0 = math.floor(minx + 1e-06)
        self.y0 = math.floor(miny + 1e-06)
        self.nx = math.ceil(maxx - 1e-06) - self.x0 + 1
        self.ny = math.ceil(maxy - 1e-06) - self.y0 + 1
        X = self.x0 + np.arange(self.nx, dtype=float)
        Y = self.y0 + np.arange(self.ny, dtype=float)
        self.G = np.empty((self.ny, self.nx), np.float32)
        self.S = np.empty((self.ny, self.nx), np.float32)
        self.ceil = np.empty((self.ny, self.nx), np.float32)
        for r0 in range(0, self.ny, 48):
            r1 = min(self.ny, r0 + 48)
            XX, YY = np.meshgrid(X, Y[r0:r1])
            g = w.terrain_z(XX, YY)
            t = w.solid_top(XX, YY)
            self.G[r0:r1] = g
            self.S[r0:r1] = t
            self.ceil[r0:r1] = w.ceiling_above(XX, YY, t)
        G, T = (self.G, self.S)
        P = getattr(w, '_P', None)
        if P is not None and np.shape(P) == G.shape:
            Pf = np.asarray(P)
            self.isB = np.isfinite(Pf) & (Pf >= G - 0.02)
            self.wall_top = np.where(np.isfinite(Pf), Pf, -np.inf).astype(np.float32)
        else:
            self.isB = T > G + 0.02
            self.wall_top = np.where(self.isB, T, -np.inf).astype(np.float32)
        self.minx, self.miny, self.maxx, self.maxy = (minx, miny, maxx, maxy)
        self._inb = ((X >= minx + self.radius) & (X <= maxx - self.radius))[None, :] & ((Y >= miny + self.radius) & (Y <= maxy - self.radius))[:, None]

    def _build_walk(self):
        p = self.p.grid
        r_eff = self.radius + p.clearance_margin_m
        offs = _disc_offsets(r_eff)
        S, isB = (self.S, self.isB)
        bt = self.wall_top
        self.maxB = _fp_reduce(bt, offs, np.maximum)
        minc = _fp_reduce(self.ceil, offs, np.minimum)
        ok = (self.maxB <= S + self.step_up + 0.001) & (minc - S >= self.height - 0.001) & self._inb
        self.walk1 = ok
        big = _fp_reduce(bt, _disc_offsets(r_eff + p.near_wall_extra_m), np.maximum)
        self.near1 = ok & ~(big <= S + self.step_up + 0.001)
        tall = (bt > self.G + self.height * 0.5)[0::2, 0::2]
        kk = math.ceil(p.cover_reach_m / 2.0)
        offc = [(dx, dy) for dy in range(-kk, kk + 1) for dx in range(-kk, kk + 1) if dx * dx + dy * dy <= kk * kk]
        self.cover2 = _fp_reduce(tall.astype(np.uint8), offc, np.maximum).astype(bool)
        ro = p.open_ground_radii * self.radius
        offo = _disc_offsets(ro)
        bigo = _fp_reduce(bt, offo, np.maximum)
        gmax = _fp_reduce(self.G, offo, np.maximum)
        gmin = _fp_reduce(self.G, offo, np.minimum)
        ceilo = _fp_reduce(self.ceil, offo, np.minimum)
        self.open1 = ok & (bigo <= S + self.step_up + 0.001) & ~np.isfinite(ceilo) & (gmax - gmin <= p.open_ground_flat_m)
        step, tan = (self.step_up, self.tan_slope)
        self.hop = {}
        for dx, dy in ((1, 0), (1, 1), (0, 1), (1, -1)):
            pp, qq = _slices(S.shape, dx, dy)
            ln = math.hypot(dx, dy)
            d = np.abs(S[qq] - S[pp])
            bld = isB[pp] | isB[qq]
            good = ok[pp] & ok[qq] & np.where(bld, d <= step + 0.001, d <= tan * ln + 0.001)
            arr = np.zeros(S.shape, bool)
            arr[pp] = good
            if dx != 0 and dy != 0:
                arr &= _shift(ok, dx, 0) & _shift(ok, 0, dy)
            self.hop[dx, dy] = arr

    def _build_coarse(self):
        ny, nx = (self.ny, self.nx)
        self.nx2 = (nx + 1) // 2
        self.ny2 = (ny + 1) // 2
        nx2, ny2 = (self.nx2, self.ny2)
        fwd = {}
        for k in _FORWARD:
            a, b = DIRS[k]
            h = self.hop[a, b]
            fwd[k] = (h & _shift(h, a, b))[0::2, 0::2]
        ok = dict(fwd)
        for k in range(8):
            if k in ok:
                continue
            a, b = DIRS[k]
            fk = next((i for i in _FORWARD if DIRS[i] == (-a, -b)))
            ok[k] = _shift(fwd[fk], a, b)
        self.ok = ok
        self.walk2 = self.walk1[0::2, 0::2].copy()
        self.near2 = self.near1[0::2, 0::2].copy()
        self.S2 = self.S[0::2, 0::2].copy()
        assert self.walk2.shape == (ny2, nx2)
        self.okf = [bytearray(ok[k].astype(np.uint8).ravel().tobytes()) for k in range(8)]
        self.nearf = bytearray(self.near2.astype(np.uint8).ravel().tobytes())
        self.offs = [dJ * nx2 + dI for dI, dJ in DIRS]
        self.base = [1.0 if dI == 0 or dJ == 0 else SQ2 for dI, dJ in DIRS]

    def _build_components(self):
        ny2, nx2 = (self.ny2, self.nx2)
        edges = [(DIRS[k][0], DIRS[k][1], self.ok[k]) for k in _FORWARD]
        lab = label_components(edges, (ny2, nx2))
        walk = self.walk2
        ids = np.full((ny2, nx2), -1, np.int32)
        uniq, inv = np.unique(lab[walk], return_inverse=True)
        ids[walk] = inv.astype(np.int32)
        self.comp = ids
        self.ncomp = len(uniq)
        self.comp_size = np.bincount(inv, minlength=len(uniq))
        self.compf = ids.ravel()

    def _build_links(self):
        p = self.p.grid
        ny2, nx2 = (self.ny2, self.nx2)
        nx, ny = (self.nx, self.ny)
        reach = max(1, round(p.link_reach_m / 2.0))
        S = self.S
        Sp = np.full((ny2 * 2 + 4, nx2 * 2 + 4), -np.inf, np.float32)
        Sp[:ny, :nx] = S
        Jn, In = np.nonzero(self.walk2)
        comp = self.comp
        cX = comp[Jn, In]
        sX = S[2 * Jn, 2 * In]
        ceilX = self.ceil[2 * Jn, 2 * In]
        clr = p.jet_clearance_m
        jet_ok = self.climb_full * p.climb_safety
        out_src, out_dst, out_cost, out_req, out_kind, out_need = ([], [], [], [], [], [])
        for dI, dJ in DIRS:
            diag = dI != 0 and dJ != 0
            unit = 2.0 * (SQ2 if diag else 1.0)
            resolved = np.zeros(len(In), bool)
            hw = np.full(len(In), -np.inf, np.float32)
            hl = np.full(len(In), np.inf, np.float32)
            fi = 2 * In
            fj = 2 * Jn
            for t in range(1, 2 * reach + 1):
                fi = fi + dI
                fj = fj + dJ
                inside = (fi >= 0) & (fi < nx) & (fj >= 0) & (fj < ny)
                sv = np.where(inside, Sp[np.clip(fj, 0, ny2 * 2 + 3), np.clip(fi, 0, nx2 * 2 + 3)], np.inf)
                if t % 2 == 1:
                    hw = np.maximum(hw, sv)
                    hl = np.minimum(hl, sv)
                    continue
                m = t // 2
                Yi, Yj = (In + dI * m, Jn + dJ * m)
                ins = (Yi >= 0) & (Yi < nx2) & (Yj >= 0) & (Yj < ny2)
                Yic, Yjc = (np.clip(Yi, 0, nx2 - 1), np.clip(Yj, 0, ny2 - 1))
                wY = ins & self.walk2[Yjc, Yic]
                cand = wY & (comp[Yjc, Yic] != cX) & ~resolved
                resolved |= cand | ~ins
                if cand.any():
                    idx = np.nonzero(cand)[0]
                    sY = self.S2[Yjc[idx], Yic[idx]]
                    peak = np.maximum(hw[idx], sY)
                    rise = peak - sX[idx]
                    kind = np.where(rise <= self.step_up + 0.001, DROP, np.where(rise + p.jump_margin_m <= self.jump_h, JUMP, JET))
                    need = rise + clr
                    hd = m * unit
                    eq = need + p.cross_m_per_m * max(0.0, hd - p.cross_free_m)
                    ok = np.where(kind == JET, (eq <= jet_ok) & (ceilX[idx] - sX[idx] >= need + self.height), True)
                    low = np.minimum(hl[idx], sY)
                    ok &= np.where(kind == DROP, (hw[idx] <= sX[idx] + self.step_up + 0.001) & (low <= sX[idx] - self.step_up - 0.05), True)
                    fall = np.maximum(0.0, sX[idx] - sY)
                    cost = np.where(kind == DROP, hd + 2.0 + 1.5 * np.sqrt(fall), np.where(kind == JUMP, hd + 0.5 * p.link_penalty_m, hd + p.jet_penalty_m + 1.5 * np.maximum(need, 0.0)))
                    sel = idx[ok]
                    out_src.append((Jn[sel] * nx2 + In[sel]).astype(np.int32))
                    out_dst.append((Yj[sel] * nx2 + Yi[sel]).astype(np.int32))
                    out_cost.append(cost[ok].astype(np.float32))
                    out_req.append(np.where(kind == JET, eq, np.maximum(need, 0.0))[ok].astype(np.float32))
                    out_need.append(np.maximum(need, 0.0)[ok].astype(np.float32))
                    out_kind.append(kind[ok].astype(np.int8))
                hw = np.maximum(hw, sv)
                hl = np.minimum(hl, sv)
        if out_src:
            src = np.concatenate(out_src)
            dst = np.concatenate(out_dst)
            cost = np.concatenate(out_cost)
            req = np.concatenate(out_req)
            kind = np.concatenate(out_kind)
            need_a = np.concatenate(out_need)
            out_src = out_dst = out_cost = out_req = out_kind = out_need = None
        else:
            src = dst = np.zeros(0, np.int64)
            cost = req = np.zeros(0)
            kind = np.zeros(0, np.int8)
            need_a = np.zeros(0)
        if len(src):
            key = src * (self.ncomp + 1) * 3 + self.compf[dst].astype(np.int64) * 3 + kind
            order = np.lexsort((cost, key))
            key_s = key[order]
            first = np.ones(len(order), bool)
            first[1:] = key_s[1:] != key_s[:-1]
            keep = order[first]
            src, dst, cost, req, kind, need_a = (src[keep], dst[keep], cost[keep], req[keep], kind[keep], need_a[keep])
        order = np.argsort(src, kind='stable')
        self.link_src, self.link_dst, self.link_cost = (src[order], dst[order], cost[order].astype(np.float32))
        self.link_req, self.link_kind = (req[order].astype(np.float32), kind[order])
        self.link_need = need_a[order].astype(np.float32)
        counts = np.bincount(self.link_src, minlength=self.nx2 * self.ny2 + 1) if len(src) else np.zeros(self.nx2 * self.ny2 + 1, np.int64)
        off = np.zeros(self.nx2 * self.ny2 + 2, np.int64)
        np.cumsum(counts[:self.nx2 * self.ny2 + 1], out=off[1:])
        self.lk_off = array('l', off.tolist())
        self.lk_dst = array('l', self.link_dst.tolist())
        self.lk_cost = array('f', self.link_cost.tolist())
        self.lk_req = array('f', self.link_req.tolist())
        self.lk_kind = array('b', self.link_kind.tolist())
        src, dst, cost, req, kind = (self.link_src, self.link_dst, self.link_cost, self.link_req, self.link_kind)
        self._arcs: dict[int, dict[int, tuple]] = {}
        csrc = self.compf[src] if len(src) else src
        cdst = self.compf[dst] if len(dst) else dst
        for a, b, r, k in zip(csrc.tolist(), cdst.tolist(), req.tolist(), kind.tolist(), strict=True):
            d = self._arcs.setdefault(a, {})
            cur = d.get(b)
            key = (0.0 if k != JET else r, k == JET)
            if cur is None or key < cur:
                d[b] = key
        self._into: dict[int, np.ndarray] = {}
        if len(dst):
            order = np.argsort(cdst, kind='stable')
            cs = cdst[order]
            cuts = np.nonzero(np.diff(cs))[0] + 1
            for grp in np.split(order, cuts):
                self._into[int(cdst[grp[0]])] = grp

    def _build_roofs(self):
        p = self.p.grid
        S = self.S
        low = np.zeros(S.shape, bool)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            low |= _shift(S, dx, dy, np.float32(np.inf)) < S - self.step_up - 0.001
        ny2, nx2 = (self.ny2, self.nx2)
        lp = np.zeros((ny2 * 2, nx2 * 2), bool)
        lp[:self.ny, :self.nx] = low
        dropc = lp.reshape(ny2, 2, nx2, 2).any(axis=(1, 3)).astype(np.uint8)
        self.dropc = dropc.astype(bool)
        depth = np.zeros((ny2, nx2), np.int8)
        for k in range(1, 9):
            offs = [(dx, dy) for dy in range(-k, k + 1) for dx in range(-k, k + 1) if dx * dx + dy * dy <= k * k]
            depth += (_fp_reduce(dropc, offs, np.maximum) == 0).astype(np.int8)
        self.depth2 = depth
        isB2 = self.isB[0::2, 0::2]
        G2 = self.G[0::2, 0::2]
        h2 = self.S2 - G2
        self.height2 = h2
        roofish = self.walk2 & isB2 & (h2 >= p.min_roof_height_m)
        jj, ii = np.nonzero(roofish)
        self.rn_node = (jj * nx2 + ii).astype(np.int64)
        self.rn_x = self.x0 + 2.0 * ii
        self.rn_y = self.y0 + 2.0 * jj
        self.rn_z = self.S2[jj, ii].astype(float)
        self.rn_h = h2[jj, ii].astype(float)
        self.rn_depth = depth[jj, ii].astype(float) * 2.0
        self.rn_comp = self.comp[jj, ii]
        gs = self.walk2 & ~isB2 & (h2 < 1.0)
        gs[:, :] &= False
        gs[::8, ::8] = (self.walk2 & ~isB2)[::8, ::8]
        gj, gi = np.nonzero(gs)
        self.gs_x = self.x0 + 2.0 * gi
        self.gs_y = self.y0 + 2.0 * gj
        self.gs_z = self.S2[gj, gi].astype(float)

    def node_of(self, x: float, y: float) -> int:
        i = round((x - self.x0) / 2.0)
        j = round((y - self.y0) / 2.0)
        i = min(max(i, 0), self.nx2 - 1)
        j = min(max(j, 0), self.ny2 - 1)
        return j * self.nx2 + i

    def xy_of(self, node: int) -> tuple:
        j, i = divmod(node, self.nx2)
        return (self.x0 + 2.0 * i, self.y0 + 2.0 * j)

    def z_of(self, node: int) -> float:
        j, i = divmod(node, self.nx2)
        return float(self.S2[j, i])

    def surface(self, x: float, y: float) -> float:
        i = min(max(math.floor(x - self.x0 + 0.5), 0), self.nx - 1)
        j = min(max(math.floor(y - self.y0 + 0.5), 0), self.ny - 1)
        return float(self.S[j, i])

    def snap(self, x: float, y: float, max_m: float | None=None):
        max_m = self.p.search.snap_max_m if max_m is None else max_m
        I0 = round((x - self.x0) / 2.0)
        J0 = round((y - self.y0) / 2.0)
        r = math.ceil(max_m / 2.0) + 1
        i0, i1 = (max(0, I0 - r), min(self.nx2, I0 + r + 1))
        j0, j1 = (max(0, J0 - r), min(self.ny2, J0 + r + 1))
        if i0 >= i1 or j0 >= j1:
            return (None, INF)
        win = self.walk2[j0:j1, i0:i1]
        if not win.any():
            return (None, INF)
        jj, ii = np.nonzero(win)
        px = self.x0 + 2.0 * (ii + i0)
        py = self.y0 + 2.0 * (jj + j0)
        d = np.hypot(px - x, py - y)
        k = int(np.argmin(d))
        if d[k] > max_m:
            return (None, INF)
        return (int((jj[k] + j0) * self.nx2 + ii[k] + i0), float(d[k]))

    def line_clear(self, ax, ay, bx, by) -> bool:
        d = math.hypot(bx - ax, by - ay)
        n = max(2, math.ceil(d / 0.5) + 1)
        t = np.linspace(0.0, 1.0, n)
        xs = ax + (bx - ax) * t
        ys = ay + (by - ay) * t
        ii = np.clip(np.floor(xs - self.x0 + 0.5).astype(np.intp), 0, self.nx - 1)
        jj = np.clip(np.floor(ys - self.y0 + 0.5).astype(np.intp), 0, self.ny - 1)
        if not self.walk1[jj, ii].all():
            return False
        s = self.S[jj, ii]
        ds = np.abs(np.diff(s))
        lim = np.where(self.isB[jj, ii][1:] | self.isB[jj, ii][:-1], self.step_up, self.tan_slope * (d / (n - 1)) + 0.05)
        return bool((ds <= lim + 0.001).all())

    def clear_around(self, x: float, y: float) -> bool:
        i = min(max(math.floor(x - self.x0 + 0.5), 0), self.nx - 1)
        j = min(max(math.floor(y - self.y0 + 0.5), 0), self.ny - 1)
        return bool(self.walk1[j, i])

    def comp_reach(self, ca: int, cb: int, climb: float=0.0, jets: bool=False) -> bool:
        if ca == cb:
            return True
        key = (ca, cb, round(climb, 1) if jets else 0.0, jets)
        hit = self._reach_cache.get(key)
        if hit is not None:
            return hit
        seen = {ca}
        stack = [ca]
        found = False
        while stack and (not found):
            c = stack.pop()
            for nb, (req, is_jet) in self._arcs.get(c, {}).items():
                if is_jet and (not jets or req > climb):
                    continue
                if nb == cb:
                    found = True
                    break
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        self._reach_cache[key] = found
        return found

    def foot_reach(self, ca: int, cb: int) -> bool:
        return self.comp_reach(ca, cb, 0.0, False)

    def search(self, start: int, goal: int, *, climb: float=0.0, jets: bool=True, blocked: dict | None=None, weight: float | None=None) -> Search:
        return Search(self, start, goal, climb, jets, blocked or {}, self.p.search.weight if weight is None else weight)

    def heuristic(self, a: int, b: int) -> float:
        ja, ia = divmod(a, self.nx2)
        jb, ib = divmod(b, self.nx2)
        dx, dy = (abs(ia - ib), abs(ja - jb))
        return 2.0 * (dx + dy + (SQ2 - 2.0) * min(dx, dy))

    def smooth(self, nodes: list, hard: set) -> list:
        if len(nodes) <= 2:
            return list(nodes)
        pts = [self.xy_of(n) for n in nodes]
        keep = [0]
        a = 0
        n = len(nodes)
        look = 20
        while a < n - 1:
            nxt = a + 1
            hi = min(n - 1, a + look)
            for k in range(hi, a + 1, -1):
                if any((h in hard for h in nodes[a + 1:k])):
                    continue
                if self.line_clear(pts[a][0], pts[a][1], pts[k][0], pts[k][1]):
                    nxt = k
                    break
            keep.append(nxt)
            a = nxt
        return [nodes[i] for i in keep]

class Search:

    def __init__(self, terr: Terrain, start: int, goal: int, climb: float, jets: bool, blocked: dict, weight: float):
        self.t = terr
        self.start, self.goal = (start, goal)
        self.climb = climb
        self.jets = jets
        self.blocked = blocked
        self.w = weight
        self.g = {start: 0.0}
        self.parent: dict = {}
        self.heap = [(weight * terr.heuristic(start, goal), 0, start)]
        self.counter = 1
        self.closed: set = set()
        self.status = 'open'
        self.expanded = 0
        self.best = start
        self.best_h = terr.heuristic(start, goal)
        ca = int(terr.compf[start])
        cb = int(terr.compf[goal])
        if ca < 0 or cb < 0 or (not terr.comp_reach(ca, cb, climb, jets)):
            self.status = 'none'

    def run(self, budget: int) -> str:
        if self.status != 'open':
            return self.status
        t = self.t
        g, parent, heap, closed = (self.g, self.parent, self.heap, self.closed)
        okf, offs, base, nearf = (t.okf, t.offs, t.base, t.nearf)
        pen = t.p.grid.near_wall_cost
        goal, w = (self.goal, self.w)
        lk_off, lk_dst, lk_cost, lk_req, lk_kind = (t.lk_off, t.lk_dst, t.lk_cost, t.lk_req, t.lk_kind)
        blocked = self.blocked
        nx2 = t.nx2
        jg, ig = divmod(goal, nx2)
        climb, jets = (self.climb, self.jets)
        push, pop = (heapq.heappush, heapq.heappop)
        counter = self.counter
        n = 0
        while heap and n < budget:
            _f, _, idx = pop(heap)
            if idx in closed:
                continue
            if idx == goal:
                self.status = 'found'
                self.counter = counter
                self.expanded += n
                return self.status
            closed.add(idx)
            n += 1
            gi = g[idx]
            j, i = divmod(idx, nx2)
            for k in range(8):
                if not okf[k][idx]:
                    continue
                nidx = idx + offs[k]
                if nidx in closed or nidx in blocked:
                    continue
                c = base[k] * 2.0
                if nearf[nidx]:
                    c *= 1.0 + pen
                ng = gi + c
                old = g.get(nidx)
                if old is None or ng < old:
                    g[nidx] = ng
                    parent[nidx] = (idx, -1)
                    jn, in_ = divmod(nidx, nx2)
                    dx = abs(in_ - ig)
                    dy = abs(jn - jg)
                    h = 2.0 * (dx + dy - 0.5857864376269049 * min(dx, dy))
                    push(heap, (ng + w * h, counter, nidx))
                    counter += 1
            q0 = lk_off[idx]
            q1 = lk_off[idx + 1]
            for q in range(q0, q1):
                kind = lk_kind[q]
                if kind == JET and (not jets or lk_req[q] > climb):
                    continue
                d = lk_dst[q]
                if d in closed or d in blocked:
                    continue
                ng = gi + lk_cost[q]
                old = g.get(d)
                if old is None or ng < old:
                    g[d] = ng
                    parent[d] = (idx, kind)
                    jn, in_ = divmod(d, nx2)
                    dx = abs(in_ - ig)
                    dy = abs(jn - jg)
                    h = 2.0 * (dx + dy - 0.5857864376269049 * min(dx, dy))
                    push(heap, (ng + w * h, counter, d))
                    counter += 1
            jn, in_ = (j, i)
            dx = abs(in_ - ig)
            dy = abs(jn - jg)
            h0 = 2.0 * (dx + dy - 0.5857864376269049 * min(dx, dy))
            if h0 < self.best_h:
                self.best_h = h0
                self.best = idx
        self.counter = counter
        self.expanded += n
        if not heap:
            self.status = 'none'
        return self.status

    def path(self) -> list:
        if self.status != 'found':
            return []
        out = []
        cur = self.goal
        while cur != self.start:
            par, kind = self.parent[cur]
            out.append((cur, kind))
            cur = par
        out.append((self.start, -1))
        out.reverse()
        return out

    def partial(self) -> list:
        cur = self.best
        out = []
        while cur != self.start and cur in self.parent:
            par, kind = self.parent[cur]
            out.append((cur, kind))
            cur = par
        out.append((self.start, -1))
        out.reverse()
        return out
