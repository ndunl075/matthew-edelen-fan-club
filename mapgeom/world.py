from __future__ import annotations
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
import numpy as np
CACHE_VERSION = 4
EPS = 1e-06
SOLID_TOL = 0.001
GAP_MIN = 0.25
_INF = np.float32(np.inf)
TREE_CELL = 8.0
TREE_SAMPLE = 4.0
TREE_MARGIN = 2.5
SLIDE_MAX = 0.6
HSTEP = 0.5

def _scalar_or_array(v, like):
    return float(v) if np.ndim(like) == 0 else v

class World:

    @classmethod
    def load(cls, map_dir, cache_dir=None) -> World:
        map_dir = Path(map_dir)
        if not (map_dir / 'world.json').exists() and (map_dir / 'v2' / 'world.json').exists():
            map_dir = map_dir / 'v2'
        wj, tf = (map_dir / 'world.json', map_dir / 'terrain.i16')
        h = hashlib.sha1(f'mechworld-v{CACHE_VERSION}'.encode())
        h.update(wj.read_bytes())
        h.update(tf.read_bytes())
        digest = h.hexdigest()[:16]
        cdir = _cache_dir(cache_dir)
        cache = cdir / f'mechworld-{digest}.npz' if cdir else None
        if cache is not None and cache.exists():
            try:
                with np.load(cache) as z:
                    return cls(**{k: z[k] for k in z.files})
            except Exception:
                pass
        data = _build(json.loads(wj.read_text()), np.fromfile(tf, '<i2'))
        if cache is not None:
            try:
                cdir.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_suffix(f'.{os.getpid()}.tmp.npz')
                np.savez(tmp, **data)
                os.replace(tmp, cache)
            except OSError:
                pass
        return cls(**data)

    def __init__(self, *, ground, top, top_kind, ob, ot, grid, bounds, rules, trees, overhang_conflicts):
        self._G = ground
        self._P = top
        self._kind = top_kind
        self._OB = ob
        self._OT = ot
        self._ny, self._nx = ground.shape
        g = np.asarray(grid, float)
        self._x0, self._y0, self._step = (float(g[0]), float(g[1]), float(g[2]))
        self.bounds = tuple((float(v) for v in bounds))
        r = np.asarray(rules, float)
        self.canopy_density, self.canopy_block_t = (float(r[0]), float(r[1]))
        self.overhang_conflicts = int(overhang_conflicts)
        self._Gf = ground.ravel()
        self._Pf = top.ravel()
        self._OBf = ob.ravel()
        self._OTf = ot.ravel()
        self._has_over = bool(np.isfinite(ob).any())
        self._trees = np.asarray(trees, np.float64).reshape(-1, 10)
        self._build_tree_index()
        self._offsets: dict[float, np.ndarray] = {}
        self._roofs: np.ndarray | None = None

    def _cell(self, x, y):
        i = np.minimum(np.maximum((np.asarray(x) - self._x0) / self._step + 0.5, 0.0), self._nx - 1).astype(np.intp)
        j = np.minimum(np.maximum((np.asarray(y) - self._y0) / self._step + 0.5, 0.0), self._ny - 1).astype(np.intp)
        return j * self._nx + i

    def _c1(self, x, y):
        i = min(max(int((x - self._x0) / self._step + 0.5), 0), self._nx - 1)
        j = min(max(int((y - self._y0) / self._step + 0.5), 0), self._ny - 1)
        return j * self._nx + i

    def _t1(self, x, y):
        fx = min(max((x - self._x0) / self._step, 0.0), self._nx - 1 - 1e-09)
        fy = min(max((y - self._y0) / self._step, 0.0), self._ny - 1 - 1e-09)
        i = int(fx)
        j = int(fy)
        tx = fx - i
        ty = fy - j
        k = j * self._nx + i
        g = self._Gf.item
        n = self._nx
        return (g(k) * (1 - tx) + g(k + 1) * tx) * (1 - ty) + (g(k + n) * (1 - tx) + g(k + n + 1) * tx) * ty

    def _terrain(self, x, y):
        fx = np.minimum(np.maximum((x - self._x0) / self._step, 0.0), self._nx - 1 - 1e-09)
        fy = np.minimum(np.maximum((y - self._y0) / self._step, 0.0), self._ny - 1 - 1e-09)
        i = fx.astype(np.intp)
        j = fy.astype(np.intp)
        tx = fx - i
        ty = fy - j
        k = j * self._nx + i
        G = self._Gf
        a = G[k]
        b = G[k + 1]
        c = G[k + self._nx]
        d = G[k + self._nx + 1]
        return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty

    def terrain_z(self, x, y):
        xa = np.asarray(x, float)
        out = self._terrain(xa, np.asarray(y, float))
        return float(out) if np.ndim(x) == 0 and np.ndim(y) == 0 else out

    def solid_top(self, x, y):
        xa, ya = np.broadcast_arrays(np.asarray(x, float), np.asarray(y, float))
        out = np.maximum(self._terrain(xa, ya), self._Pf[self._cell(xa, ya)])
        return float(out) if out.ndim == 0 else out

    def surface_below(self, x, y, z):
        xa, ya, za = np.broadcast_arrays(*(np.asarray(v, float) for v in (x, y, z)))
        c = self._cell(xa, ya)
        ground = self._terrain(xa, ya)
        out = ground.astype(float)
        pt = self._Pf[c].astype(float)
        out = np.maximum(out, np.where(pt <= za + EPS, pt, -np.inf))
        if self._has_over:
            ot = self._OTf[c].astype(float)
            out = np.maximum(out, np.where(ot <= za + EPS, ot, -np.inf))
        out = np.where(ground <= za + EPS, out, -np.inf)
        return float(out) if out.ndim == 0 else out

    def ceiling_above(self, x, y, z):
        xa, ya, za = np.broadcast_arrays(*(np.asarray(v, float) for v in (x, y, z)))
        ob = self._OBf[self._cell(xa, ya)].astype(float)
        out = np.where(ob >= za - EPS, ob, np.inf)
        return float(out) if out.ndim == 0 else out

    def is_solid(self, x, y, z):
        xa, ya, za = np.broadcast_arrays(*(np.asarray(v, float) for v in (x, y, z)))
        out = self._solid(xa, ya, za)
        return bool(out) if out.ndim == 0 else out

    def _solid(self, x, y, z):
        c = self._cell(x, y)
        s = (z < self._terrain(x, y) - SOLID_TOL) | (z < self._Pf[c] - SOLID_TOL)
        if self._has_over:
            s |= (z > self._OBf[c] + SOLID_TOL) & (z < self._OTf[c] - SOLID_TOL)
        return s

    def segments_hit(self, A, B, step: float=1.0) -> np.ndarray:
        A = np.atleast_2d(np.asarray(A, float))
        B = np.atleast_2d(np.asarray(B, float))
        if A.shape != B.shape or A.ndim != 2 or A.shape[1] != 3:
            raise ValueError(f'segments_hit: A and B must both be N x 3, got {A.shape}, {B.shape}')
        if not (math.isfinite(step) and step > 0):
            raise ValueError(f'segments_hit: step must be positive and finite, got {step}')
        D = B - A
        if not (np.isfinite(A).all() and np.isfinite(B).all() and np.isfinite(D).all()):
            bad = np.nonzero(~(np.isfinite(A).all(1) & np.isfinite(B).all(1) & np.isfinite(D).all(1)))[0]
            raise ValueError(f'segments_hit: non-finite (nan, inf or overflowing) coordinates in segment(s) {bad[:5].tolist()}')
        n_seg = len(A)
        out = np.full(n_seg, np.nan)
        if n_seg == 0:
            return out
        lo, hi = self._clip_to_bounds(A, D)
        live = np.nonzero(lo <= hi)[0]
        if live.size == 0:
            return out
        A, D, lo, hi = (A[live], D[live], lo[live], hi[live])
        span = hi - lo
        A2 = A + D * lo[:, None]
        D2 = D * span[:, None]
        L = np.sqrt((D2 * D2).sum(1))
        n = np.maximum(np.ceil(L / step), 1.0)
        res = np.full(len(live), np.nan)
        order = np.argsort(n)
        start = 0
        while start < len(live):
            end = start + 1
            while end < len(live) and (end - start + 1) * (n[order[end]] + 1) <= 600000:
                end += 1
            idx = order[start:end]
            res[idx] = self._march(A2[idx], D2[idx], n[idx], int(n[idx].max()))
            start = end
        out[live] = lo + res * span
        return out

    def _clip_to_bounds(self, A, D):
        minx, miny, maxx, maxy = self.bounds
        lo = np.zeros(len(A))
        hi = np.ones(len(A))
        for ax, (b0, b1) in enumerate(((minx, maxx), (miny, maxy))):
            a, d = (A[:, ax], D[:, ax])
            moving = d != 0
            safe = np.where(moving, d, 1.0)
            t0, t1 = ((b0 - a) / safe, (b1 - a) / safe)
            within = (a >= b0) & (a <= b1)
            lo = np.maximum(lo, np.where(moving, np.minimum(t0, t1), np.where(within, 0.0, 2.0)))
            hi = np.minimum(hi, np.where(moving, np.maximum(t0, t1), np.where(within, 1.0, -1.0)))
        end = A + D
        in_a = (A[:, 0] >= minx) & (A[:, 0] <= maxx) & (A[:, 1] >= miny) & (A[:, 1] <= maxy)
        in_b = (end[:, 0] >= minx) & (end[:, 0] <= maxx) & (end[:, 1] >= miny) & (end[:, 1] <= maxy)
        return (np.where(in_a, 0.0, lo), np.where(in_b, 1.0, hi))

    def _march(self, A, D, n, nmax):
        k = np.arange(nmax + 1.0)
        t = np.minimum(k[None, :] / n[:, None], 1.0)
        x = A[:, 0:1] + D[:, 0:1] * t
        y = A[:, 1:2] + D[:, 1:2] * t
        z = A[:, 2:3] + D[:, 2:3] * t
        inside = self._solid(x, y, z)
        any_hit = inside.any(1)
        res = np.full(len(A), np.nan)
        if not any_hit.any():
            return res
        h = np.nonzero(any_hit)[0]
        first = inside[h].argmax(1)
        t1 = t[h, first]
        t0 = np.where(first > 0, t[h, np.maximum(first - 1, 0)], 0.0)
        Ah, Dh = (A[h], D[h])
        for _ in range(7):
            tm = 0.5 * (t0 + t1)
            s = self._solid(Ah[:, 0] + Dh[:, 0] * tm, Ah[:, 1] + Dh[:, 1] * tm, Ah[:, 2] + Dh[:, 2] * tm)
            t1 = np.where(s, tm, t1)
            t0 = np.where(s, t0, tm)
        res[h] = np.where(first == 0, 0.0, t1)
        return res

    def segment_hit(self, a, b):
        r = self.segments_hit(np.asarray(a, float)[None], np.asarray(b, float)[None])[0]
        return None if np.isnan(r) else float(r)

    def sights_blocked(self, A, B, trees: bool=True) -> np.ndarray:
        A = np.atleast_2d(np.asarray(A, float))
        B = np.atleast_2d(np.asarray(B, float))
        blocked = ~np.isnan(self.segments_hit(A, B))
        if trees and len(self._trees) and (not blocked.all()):
            rest = np.nonzero(~blocked)[0]
            blocked[rest] = self._tree_blocked(A[rest], B[rest])
        return blocked

    def sight_blocked(self, a, b, trees: bool=True) -> bool:
        return bool(self.sights_blocked(np.asarray(a, float)[None], np.asarray(b, float)[None], trees)[0])

    def _build_tree_index(self):
        T = self._trees
        self._tree_cells = None
        if len(T) == 0:
            return
        minx, miny, maxx, maxy = self.bounds
        self._tcx0, self._tcy0 = (minx - TREE_CELL, miny - TREE_CELL)
        self._tnx = int((maxx - minx) / TREE_CELL) + 3
        self._tny = int((maxy - miny) / TREE_CELL) + 3
        cells: dict[int, list[int]] = {}
        for ti, (x, y, _z, h, cr, cb, tr, _o, cx, cy) in enumerate(T):
            seen: set[int] = set()
            for px, py, rad in ((cx, cy, cr), (x, y, tr)):
                rad += TREE_MARGIN
                i0 = int((px - rad - self._tcx0) // TREE_CELL)
                i1 = int((px + rad - self._tcx0) // TREE_CELL)
                j0 = int((py - rad - self._tcy0) // TREE_CELL)
                j1 = int((py + rad - self._tcy0) // TREE_CELL)
                for j in range(max(j0, 0), min(j1, self._tny - 1) + 1):
                    for i in range(max(i0, 0), min(i1, self._tnx - 1) + 1):
                        seen.add(j * self._tnx + i)
            for c in seen:
                cells.setdefault(c, []).append(ti)
        counts = np.zeros(self._tnx * self._tny + 1, np.int64)
        for c, lst in cells.items():
            counts[c + 1] = len(lst)
        self._tptr = np.cumsum(counts)
        self._tlist = np.zeros(int(self._tptr[-1]), np.int64)
        for c, lst in cells.items():
            self._tlist[self._tptr[c]:self._tptr[c + 1]] = lst

    def _tree_blocked(self, A, B):
        n_seg = len(A)
        D = B - A
        Lxy = np.hypot(D[:, 0], D[:, 1])
        ns = np.maximum(np.ceil(Lxy / TREE_SAMPLE), 1).astype(int)
        nmax = int(ns.max())
        t = np.minimum(np.arange(nmax + 1.0)[None, :] / ns[:, None], 1.0)
        ci = np.floor((A[:, 0:1] + D[:, 0:1] * t - self._tcx0) / TREE_CELL).astype(np.int64)
        cj = np.floor((A[:, 1:2] + D[:, 1:2] * t - self._tcy0) / TREE_CELL).astype(np.int64)
        ok = (ci >= 0) & (ci < self._tnx) & (cj >= 0) & (cj < self._tny)
        cell = np.where(ok, cj * self._tnx + ci, 0)
        seg = np.broadcast_to(np.arange(n_seg)[:, None], cell.shape)
        key = np.unique(seg[ok].astype(np.int64) * (self._tnx * self._tny) + cell[ok])
        seg_u = key // (self._tnx * self._tny)
        cell_u = key % (self._tnx * self._tny)
        cnt = self._tptr[cell_u + 1] - self._tptr[cell_u]
        total = int(cnt.sum())
        blocked = np.zeros(n_seg, bool)
        if total == 0:
            return blocked
        pseg = np.repeat(seg_u, cnt)
        offs = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        ptree = self._tlist[np.repeat(self._tptr[cell_u], cnt) + offs]
        pair = np.unique(pseg * len(self._trees) + ptree)
        pseg = pair // len(self._trees)
        ptree = pair % len(self._trees)
        T = self._trees[ptree]
        a = A[pseg]
        d = D[pseg]
        trunk = _hits_cylinder(a, d, T[:, 0], T[:, 1], T[:, 6], T[:, 2], T[:, 2] + T[:, 5])
        hz = np.maximum((T[:, 3] - T[:, 5]) * 0.5, 0.001)
        cz = T[:, 2] + T[:, 5] + hz
        axes = np.stack([T[:, 4], T[:, 4], hz], 1)
        ap = (a - np.stack([T[:, 8], T[:, 9], cz], 1)) / axes
        dp = d / axes
        qa = (dp * dp).sum(1)
        qb = 2 * (ap * dp).sum(1)
        qc = (ap * ap).sum(1) - 1
        disc = qb * qb - 4 * qa * qc
        good = (disc > 0) & (qa > 1e-12)
        sq = np.sqrt(np.where(good, disc, 0.0))
        s1 = np.clip((-qb - sq) / np.where(good, 2 * qa, 1), 0, 1)
        s2 = np.clip((-qb + sq) / np.where(good, 2 * qa, 1), 0, 1)
        chord = np.where(good, s2 - s1, 0.0) * np.sqrt((d * d).sum(1))
        path = np.bincount(pseg, weights=chord, minlength=n_seg)
        blocked |= np.bincount(pseg, weights=trunk.astype(float), minlength=n_seg) > 0
        blocked |= np.exp(-self.canopy_density * path) < self.canopy_block_t
        return blocked

    def _disc(self, radius):
        key = round(float(radius), 4)
        off = self._offsets.get(key)
        if off is None:
            r = float(radius)
            g = np.arange(-math.ceil(r), math.ceil(r) + 1) * 1.0
            gx, gy = np.meshgrid(g, g)
            m = gx * gx + gy * gy <= r * r + 1e-09
            pts = [np.stack([gx[m], gy[m]], 1)]
            ang = np.linspace(0, 2 * np.pi, 20, endpoint=False)
            pts.append(np.stack([r * np.cos(ang), r * np.sin(ang)], 1))
            off = np.concatenate(pts)
            self._offsets[key] = off
        return off

    def _footprint(self, x, y, radius):
        off = self._disc(radius)
        px = x + off[:, 0]
        py = y + off[:, 1]
        return (px, py, self._cell(px, py))

    def move_cylinder(self, pos, delta, radius, height, step_up, max_slope_deg):
        p = np.array(pos, float)
        want = np.array(delta, float)
        cyl = _Cyl(float(radius), float(height), float(step_up), math.tan(math.radians(max_slope_deg)))
        x, y, z = p
        blocked = np.zeros(3, bool)
        was_grounded = self._support(x, y, z, cyl)[2]
        z, vblocked, ceil_hit = self._vertical(x, y, z, want[2], cyl)
        blocked[2] = vblocked
        hx, hy = (want[0], want[1])
        dist = math.hypot(hx, hy)
        moved = np.zeros(2)
        if dist > 1e-12:
            n = max(1, math.ceil(dist / HSTEP))
            sub = np.array([hx, hy]) / n
            for _ in range(n):
                nx_, ny_, z, _m = self._horizontal(x, y, z, sub[0], sub[1], cyl)
                moved += (nx_ - x, ny_ - y)
                x, y = (nx_, ny_)
            blocked[0] = abs(moved[0] - hx) > 1e-06
            blocked[1] = abs(moved[1] - hy) > 1e-06
        _edge, cen, grounded = self._support(x, y, z, cyl)
        if was_grounded and want[2] <= 0 and (z - cen > 0.0001) and (z - cen <= cyl.step_up + EPS):
            z = cen
            _edge, cen, grounded = self._support(x, y, z, cyl)
        if grounded and z - cen > 0.0001:
            x, y, z = self._edge_slide(x, y, z, hx, hy, cyl)
            _edge, cen, grounded = self._support(x, y, z, cyl)
        if ceil_hit is not None:
            x, y, z = self._eave_slide(x, y, z, ceil_hit, cyl)
        return (np.array([x, y, z]), blocked, bool(grounded))

    def _support(self, x, y, z, cyl):
        c = self._c1(x, y)
        cen = max(self._t1(x, y), self._Pf.item(c))
        if self._has_over and self._OTf.item(c) <= z + EPS:
            cen = max(cen, self._OTf.item(c))
        if cen > z + EPS:
            cen = -np.inf
        _px, _py, fc = self._footprint(x, y, cyl.r)
        tops = self._Pf[fc]
        edge = float(tops[tops <= z + EPS].max(initial=-np.inf))
        if self._has_over:
            ot = self._OTf[fc]
            edge = max(edge, float(ot[ot <= z + EPS].max(initial=-np.inf)))
        edge = max(edge, cen)
        grounded = z - edge <= 0.0001
        return (edge, cen, grounded)

    def _vertical(self, x, y, z, dz, cyl):
        if dz == 0:
            return (z, False, None)
        px, py, fc = self._footprint(x, y, cyl.r)
        if dz > 0:
            if not self._has_over:
                return (z + dz, False, None)
            ob = self._OBf[fc]
            head = z + cyl.h
            hit = (ob >= head - 0.0001) & (ob < head + dz)
            if not hit.any():
                return (z + dz, False, None)
            lim = float(ob[hit].min()) - cyl.h
            return (max(lim, z), True, (px[hit], py[hit]))
        c = self._c1(x, y)
        cand = np.concatenate([[self._t1(x, y), self._Pf.item(c)], self._Pf[fc]])
        if self._has_over:
            cand = np.concatenate([cand, self._OTf[fc]])
        cand = cand[(cand <= z + EPS) & (cand >= z + dz - EPS)]
        if cand.size:
            return (float(cand.max()), True, None)
        return (z + dz, False, None)

    def _horizontal(self, x, y, z, dx, dy, cyl):
        ok, nz, _n = self._try(x, y, z, x + dx, y + dy, cyl)
        if ok:
            return (x + dx, y + dy, nz, True)
        lo, hi = (0.0, 1.0)
        for _ in range(5):
            mid = 0.5 * (lo + hi)
            if self._try(x, y, z, x + dx * mid, y + dy * mid, cyl)[0]:
                lo = mid
            else:
                hi = mid
        bx, by, bz = (x, y, z)
        if lo > 0:
            _ok, bz, _n = self._try(x, y, z, x + dx * lo, y + dy * lo, cyl)
            bx, by = (x + dx * lo, y + dy * lo)
        _ok, _nz, nrm = self._try(x, y, z, x + dx, y + dy, cyl)
        rx, ry = (dx * (1 - lo), dy * (1 - lo))
        tried = []
        if nrm is not None:
            dn = rx * nrm[0] + ry * nrm[1]
            if dn > 0:
                tried.append((rx - dn * nrm[0], ry - dn * nrm[1]))
        tried += [(rx, 0.0), (0.0, ry)]
        for sx, sy in tried:
            if abs(sx) + abs(sy) < 1e-09:
                continue
            ok, nz, _n = self._try(bx, by, bz, bx + sx, by + sy, cyl)
            if ok:
                return (bx + sx, by + sy, nz, True)
        if lo == 0 and self._embedded(x, y, z, cyl):
            now = self._wall_cells(x, y, z, cyl)
            for sx, sy in [(dx, dy), (dx, 0.0), (0.0, dy)]:
                if (sx or sy) and self._wall_cells(x + sx, y + sy, z, cyl) < now:
                    return (x + sx, y + sy, z, True)
        return (bx, by, bz, lo > 0)

    def _wall_cells(self, x, y, z, cyl):
        _px, _py, fc = self._footprint(x, y, cyl.r)
        return int((self._Pf[fc] > z + cyl.step_up + EPS).sum())

    def _embedded(self, x, y, z, cyl):
        return self._wall_cells(x, y, z, cyl) > 0

    def _try(self, x, y, z, nx_, ny_, cyl):
        minx, miny, maxx, maxy = self.bounds
        r = cyl.r
        ex = -1.0 if nx_ < minx + r else 1.0 if nx_ > maxx - r else 0.0
        ey = -1.0 if ny_ < miny + r else 1.0 if ny_ > maxy - r else 0.0
        if (ex or ey) and (nx_ - x) * ex + (ny_ - y) * ey > 0:
            return (False, z, _unit((ex, ey)))
        c = self._c1(nx_, ny_)
        ground = self._t1(nx_, ny_)
        ptop = self._Pf.item(c)
        top = max(ground, ptop)
        reach = z + cyl.step_up + EPS
        nz = z
        otop = self._OTf.item(c) if self._has_over else -np.inf
        if otop <= reach:
            top = max(top, otop)
        if top > reach:
            return (False, z, self._dir_to_wall(nx_, ny_, x, y, ground, top))
        if top > z:
            d = math.hypot(nx_ - x, ny_ - y)
            on_prism = ptop > ground + 0.0001 or otop > ground + 0.0001
            if not on_prism and d > 1e-09 and (top - z > cyl.tan_slope * d + 1e-09):
                return (False, z, self._slope_dir(nx_, ny_))
            nz = top
        _px, _py, fc = self._footprint(nx_, ny_, r)
        pt = self._Pf[fc]
        wall = pt > reach
        if wall.any():
            return (False, z, self._dir_to_cells(nx_, ny_, _px[wall], _py[wall]))
        if self._has_over:
            ob = self._OBf[fc]
            ot = self._OTf[fc]
            bad = (ob < nz + cyl.h - 0.0001) & (ot > nz + 0.0001)
            if bad.any():
                return (False, z, self._dir_to_cells(nx_, ny_, _px[bad], _py[bad]))
        return (True, nz, None)

    def _slope_dir(self, x, y):
        h = 0.5
        gx = self._t1(x + h, y) - self._t1(x - h, y)
        gy = self._t1(x, y + h) - self._t1(x, y - h)
        return _unit((gx, gy))

    def _dir_to_wall(self, nx_, ny_, x, y, ground, top):
        if ground >= top - 0.0001:
            return self._slope_dir(nx_, ny_)
        return _unit((nx_ - x, ny_ - y))

    @staticmethod
    def _dir_to_cells(x, y, cx, cy):
        return _unit((float(cx.mean()) - x, float(cy.mean()) - y))

    def _edge_slide(self, x, y, z, hx, hy, cyl):
        px, py, fc = self._footprint(x, y, cyl.r)
        tops = self._Pf[fc]
        if self._has_over:
            tops = np.maximum(tops, np.where(self._OTf[fc] <= z + EPS, self._OTf[fc], -np.inf))
        sup = (tops <= z + EPS) & (tops >= z - 0.001)
        if not sup.any():
            return (x, y, z)
        ux, uy = _unit((float(px[sup].mean()) - x, float(py[sup].mean()) - y))
        if hx * ux + hy * uy > 1e-09:
            return (x, y, z)
        dmin = float(np.hypot(px[sup] - x, py[sup] - y).min())
        amt = min(SLIDE_MAX, max(cyl.r - dmin, 0.0) + 0.05)
        sx, sy = (-ux * amt, -uy * amt)
        nx_, ny_, nz, _m = self._horizontal(x, y, z, sx, sy, cyl)
        return (nx_, ny_, max(z, nz))

    def _eave_slide(self, x, y, z, hit, cyl):
        ux, uy = _unit((float(hit[0].mean()) - x, float(hit[1].mean()) - y))
        nx_, ny_, _nz, _m = self._horizontal(x, y, z, -ux * SLIDE_MAX, -uy * SLIDE_MAX, cyl)
        return (nx_, ny_, z)

    def open_ground(self, x, y, clearance_radius: float, flat_tol: float=1.0) -> bool:
        minx, miny, maxx, maxy = self.bounds
        r = float(clearance_radius)
        if x < minx + r or x > maxx - r or y < miny + r or (y > maxy - r):
            return False
        px, py, fc = self._footprint(float(x), float(y), r)
        h = self._terrain(px, py)
        if h.max() - h.min() > flat_tol:
            return False
        if (self._Pf[fc] > h.min() - 0.01).any() and (self._Pf[fc] > h - 0.01).any():
            return False
        return not (self._has_over and np.isfinite(self._OBf[fc]).any())

    def roof_cells(self) -> np.ndarray:
        if self._roofs is None:
            P = np.where(np.isfinite(self._P), self._P, -1000000000.0)
            is_b = (self._kind == 1) & (self._P > self._G + 3.0)
            flat = is_b.copy()
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    sh = np.roll(np.roll(P, dj, 0), di, 1)
                    shb = np.roll(np.roll(is_b, dj, 0), di, 1)
                    flat &= shb & (np.abs(sh - P) < 0.25)
            flat[[0, -1], :] = False
            flat[:, [0, -1]] = False
            j, i = np.nonzero(flat)
            self._roofs = np.stack([self._x0 + i * self._step, self._y0 + j * self._step, self._P[j, i].astype(float)], 1)
        return self._roofs

class _Cyl:
    __slots__ = ('h', 'r', 'step_up', 'tan_slope')

    def __init__(self, r, h, step_up, tan_slope):
        self.r, self.h, self.step_up, self.tan_slope = (r, h, step_up, tan_slope)

def _unit(v):
    n = math.hypot(v[0], v[1])
    return (v[0] / n, v[1] / n) if n > 1e-12 else (0.0, 0.0)

def _hits_cylinder(a, d, cx, cy, r, z0, z1):
    fx = a[:, 0] - cx
    fy = a[:, 1] - cy
    qa = d[:, 0] ** 2 + d[:, 1] ** 2
    qb = 2 * (fx * d[:, 0] + fy * d[:, 1])
    qc = fx * fx + fy * fy - r * r
    moving = qa > 1e-12
    disc = qb * qb - 4 * qa * qc
    sq = np.sqrt(np.maximum(disc, 0))
    den = np.where(moving, 2 * qa, 1.0)
    lo = np.where(moving, (-qb - sq) / den, 0.0)
    hi = np.where(moving, (-qb + sq) / den, 1.0)
    ok = np.where(moving, disc >= 0, qc <= 0)
    lo = np.maximum(lo, 0.0)
    hi = np.minimum(hi, 1.0)
    dz = d[:, 2]
    az = a[:, 2]
    nz = np.abs(dz) > 1e-12
    sz0 = np.where(nz, (z0 - az) / np.where(nz, dz, 1), -np.inf)
    sz1 = np.where(nz, (z1 - az) / np.where(nz, dz, 1), np.inf)
    zlo = np.minimum(sz0, sz1)
    zhi = np.maximum(sz0, sz1)
    flat_ok = (az >= z0) & (az <= z1)
    zlo = np.where(nz, zlo, np.where(flat_ok, -np.inf, np.inf))
    zhi = np.where(nz, zhi, np.where(flat_ok, np.inf, -np.inf))
    return ok & (np.maximum(lo, zlo) <= np.minimum(hi, zhi)) & (lo <= hi)

def _cache_dir(cache_dir):
    if cache_dir is not None:
        return Path(cache_dir)
    base = os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache'
    try:
        return Path(base) / 'k3'
    except Exception:
        return Path(tempfile.gettempdir()) / 'k3'

def _inside(ring, px, py):
    r = np.asarray(ring, float).reshape(-1, 2)
    xs, ys = (r[:, 0], r[:, 1])
    inside = np.zeros(px.shape, bool)
    for x1, y1, x2, y2 in zip(np.roll(xs, 1), np.roll(ys, 1), xs, ys):
        if y1 == y2:
            continue
        m = (y1 > py) != (y2 > py)
        if m.any():
            inside ^= m & (px < (x2 - x1) * (py - y1) / (y2 - y1) + x1)
    return inside
_TREE_FIELDS = ('x', 'y', 'z', 'height', 'crownR', 'crownBase', 'trunkR', 'osm', 'crownX', 'crownY')

def _tree_table(tr):
    f = tr['fields']
    a = np.asarray(tr['data'], float).reshape(-1, len(f))
    return np.stack([a[:, f.index(n)] for n in _TREE_FIELDS], 1)

def _build(world, raw):
    t = world['terrain']
    nx, ny = (int(t['nx']), int(t['ny']))
    x0, y0, step = (float(t['minX']), float(t['minY']), float(t['step']))
    G = (raw.astype(np.float32) * np.float32(t['scale'])).reshape(ny, nx)
    P = np.full((ny, nx), -np.inf, np.float32)
    kind = np.zeros((ny, nx), np.uint8)
    OB = np.full((ny, nx), np.inf, np.float32)
    OT = np.full((ny, nx), -np.inf, np.float32)
    conflicts = 0
    codes = {'building': 1, 'stadium': 2, 'bridge': 3}
    for pr in world['prisms']:
        ring = np.asarray(pr['ring'], float).reshape(-1, 2)
        i0 = max(0, math.floor((ring[:, 0].min() - x0) / step))
        i1 = min(nx - 1, math.ceil((ring[:, 0].max() - x0) / step))
        j0 = max(0, math.floor((ring[:, 1].min() - y0) / step))
        j1 = min(ny - 1, math.ceil((ring[:, 1].max() - y0) / step))
        if i0 > i1 or j0 > j1:
            continue
        ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
        px, py = (x0 + ii * step, y0 + jj * step)
        m = _inside(pr['ring'], px, py)
        for hole in pr.get('holes') or []:
            m &= ~_inside(hole, px, py)
        if not m.any():
            ci = round((ring[:, 0].mean() - x0) / step)
            cj = round((ring[:, 1].mean() - y0) / step)
            if 0 <= ci < nx and 0 <= cj < ny and _inside(pr['ring'], np.array([ring[:, 0].mean()]), np.array([ring[:, 1].mean()]))[0]:
                m = np.zeros_like(m)
                m[min(max(cj - j0, 0), m.shape[0] - 1), min(max(ci - i0, 0), m.shape[1] - 1)] = True
            else:
                continue
        sj, si = (jj[m], ii[m])
        g = G[sj, si]
        base, top = (float(pr['base']), float(pr['top']))
        ground_based = base <= g + (0.05 if pr['kind'] == 'bridge' else GAP_MIN)
        gj, gi = (sj[ground_based], si[ground_based])
        better = np.float32(top) > P[gj, gi]
        P[gj[better], gi[better]] = np.float32(top)
        kind[gj[better], gi[better]] = codes.get(pr['kind'], 1)
        oj, oi = (sj[~ground_based], si[~ground_based])
        if oj.size:
            cb, ct = (OB[oj, oi], OT[oj, oi])
            empty = ~np.isfinite(cb)
            overlap = ~empty & (base <= ct) & (top >= cb)
            conflicts += int((~empty & ~overlap).sum())
            lower = ~empty & ~overlap & (base < cb)
            put = empty | lower
            nb = np.where(put, base, np.where(overlap, np.minimum(cb, base), cb))
            nt = np.where(put, top, np.where(overlap, np.maximum(ct, top), ct))
            OB[oj, oi] = nb.astype(np.float32)
            OT[oj, oi] = nt.astype(np.float32)
    b = world['bounds']
    rules = world['rules']
    tr = world['trees']
    return {'ground': G, 'top': P, 'top_kind': kind, 'ob': OB, 'ot': OT, 'grid': np.array([x0, y0, step]), 'bounds': np.array([b['minX'], b['minY'], b['maxX'], b['maxY']]), 'rules': np.array([rules['canopyDensity'], rules['canopyBlockT']]), 'trees': _tree_table(tr), 'overhang_conflicts': np.array(conflicts)}
