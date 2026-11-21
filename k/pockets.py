from __future__ import annotations
import math
import numpy as np

def find_pockets(nav) -> list[dict]:
    from .terrain import _fp_reduce
    T, p = (nav.T, nav.p.pockets)
    need = nav.m['height'] * p.min_wall_rise_mult
    big = int(np.argmax(T.comp_size))
    nx2 = T.nx2
    k = max(1, round(p.drop_clear_m / 2.0))
    offs = [(dx, dy) for dy in range(-k, k + 1) for dx in range(-k, k + 1) if dx * dx + dy * dy <= k * k]
    near_drop = _fp_reduce(T.dropc.astype(np.uint8), offs, np.maximum).astype(bool)
    wk = T.walk2
    bnd = wk.copy()
    inner = wk.copy()
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
        inner &= np.roll(np.roll(wk, dy, 0), dx, 1)
    bnd &= ~inner
    jj, ii = np.nonzero(wk)
    comps = T.comp[jj, ii]
    out = []
    order = np.argsort(comps, kind='stable')
    cuts = np.nonzero(np.diff(comps[order]))[0] + 1
    for grp in np.split(order, cuts):
        c = int(comps[grp[0]])
        if c == big or len(grp) * 4.0 < p.min_area_m2:
            continue
        gj, gi = (jj[grp], ii[grp])
        if near_drop[gj, gi].mean() > 0.1:
            continue
        b = bnd[gj, gi]
        xs = T.x0 + 2.0 * gi
        ys = T.y0 + 2.0 * gj
        z0 = T.S2[gj, gi].astype(float)
        rises, dropped = _wall_rises(T, xs[b] if b.any() else xs, ys[b] if b.any() else ys, z0[b] if b.any() else z0, p.ray_m)
        if dropped or not len(rises):
            continue
        wall = float(np.percentile(rises, p.wall_percentile))
        if wall < need:
            continue
        bx, by = (xs[b], ys[b])
        if len(bx):
            depth = np.array([np.min(np.hypot(bx - x, by - y)) for x, y in zip(xs, ys, strict=True)])
        else:
            depth = np.zeros(len(xs))
        best = int(np.argmax(depth))
        z = float(T.S2[gj[best], gi[best]])
        vis = nav.ground_can_see((float(xs[best]), float(ys[best]), z))
        if vis > p.max_visible_share:
            continue
        area = _floor_area(T, xs, ys, float(np.mean(z0)))
        out.append({'comp': c, 'area': area, 'wall': wall, 'nodes': (gj * nx2 + gi).astype(np.int64), 'xs': xs, 'ys': ys, 'depth': depth, 'x': float(xs[best]), 'y': float(ys[best]), 'z': z, 'ground_vis': vis, 'score': area * min(wall, 3.0 * nav.m['height']) / nav.m['height'] * (1.0 - vis)})
    out.sort(key=lambda d: -d['score'])
    return out

def _floor_area(T, xs, ys, z) -> float:
    from .terrain import _disc_offsets, _fp_reduce
    i0 = int(xs.min() - T.x0) - 5
    i1 = int(xs.max() - T.x0) + 6
    j0 = int(ys.min() - T.y0) - 5
    j1 = int(ys.max() - T.y0) + 6
    i0, j0 = (max(i0, 0), max(j0, 0))
    i1, j1 = (min(i1, T.nx), min(j1, T.ny))
    m = np.zeros((j1 - j0, i1 - i0), np.uint8)
    m[np.floor(ys - T.y0 + 0.5).astype(int) - j0, np.floor(xs - T.x0 + 0.5).astype(int) - i0] = 1
    m = _fp_reduce(m, _disc_offsets(T.radius + T.p.grid.clearance_margin_m + 0.5), np.maximum)
    low = T.S[j0:j1, i0:i1] <= z + T.step_up + 0.001
    return float((m.astype(bool) & low).sum())

def _wall_rises(T, xs, ys, z0, reach_m, n_dir=16):
    ang = np.linspace(0.0, 2.0 * np.pi, n_dir, endpoint=False)
    steps = np.arange(1, int(reach_m) + 1, dtype=float)
    px = xs[:, None, None] + np.cos(ang)[None, :, None] * steps[None, None, :]
    py = ys[:, None, None] + np.sin(ang)[None, :, None] * steps[None, None, :]
    ii = np.clip(np.floor(px - T.x0 + 0.5).astype(np.intp), 0, T.nx - 1)
    jj = np.clip(np.floor(py - T.y0 + 0.5).astype(np.intp), 0, T.ny - 1)
    s = T.S[jj, ii].astype(float)
    prev = np.concatenate([np.broadcast_to(z0[:, None, None], (len(xs), n_dir, 1)), s[:, :, :-1]], axis=2)
    d = s - prev
    brk = np.abs(d) > T.step_up + 0.001
    first = np.argmax(brk, axis=2)
    has = brk.any(axis=2)
    ev = np.take_along_axis(d, first[:, :, None], axis=2)[:, :, 0]
    if (has & (ev < 0)).any():
        return (np.zeros(0), True)
    top = np.maximum.accumulate(s[:, :, ::-1], axis=2)[:, :, ::-1]
    hi = np.take_along_axis(top, first[:, :, None], axis=2)[:, :, 0]
    rise = (hi - z0[:, None])[has]
    return (rise, False)

def nearest(nav, me, circle):
    from .nav import Spot
    pk = nav.pockets()
    if not pk:
        return None
    T = nav.T
    cx, cy, r = circle
    px, py = (float(v) for v in me['pos'][:2])
    na, _ = T.snap(px, py, 10.0)
    if na is None:
        return None
    ca = int(T.compf[na])
    budget = nav.climb_height(me, energy=me.get('max_energy', nav.m['energy'])) * nav.p.grid.climb_safety
    best = None
    for q in pk:
        inside = np.hypot(q['xs'] - cx, q['ys'] - cy) <= r
        if not inside.any() or not T.comp_reach(ca, q['comp'], budget, True):
            continue
        k = int(np.argmax(np.where(inside, q['depth'], -1.0)))
        est = 1.2 * math.hypot(q['xs'][k] - px, q['ys'][k] - py)
        if best is None or est < best[0]:
            best = (est, q, k)
    if best is None:
        return None
    est, q, k = best
    return Spot(q['xs'][k], q['ys'][k], float(T.S2.ravel()[q['nodes'][k]]), area=q['area'], wall=q['wall'], depth=float(q['depth'][k]), cost=est, ground_vis=q['ground_vis'])
