from __future__ import annotations
import math
import numpy as np
from .util import unit2

def _tp(nav):
    h = nav.m['height']
    return (h * 0.5, h)

def _hidden_mask(nav, threats, xs, ys, zs, trees=False):
    w = nav.world
    mid, top = _tp(nav)
    n = len(xs)
    hidden = np.ones(n, bool)
    for t in threats:
        if not hidden.any():
            break
        idx = np.nonzero(hidden)[0]
        eye = np.array([t[0], t[1], t[2] + nav.m['eye_height']])
        A = np.broadcast_to(eye, (len(idx), 3))
        B = np.column_stack([xs[idx], ys[idx], zs[idx] + top])
        blocked = w.sights_blocked(A, B, trees=trees)
        keep = idx[blocked]
        hidden[:] = False
        hidden[keep] = True
        if keep.size:
            A2 = np.broadcast_to(eye, (len(keep), 3))
            B2 = np.column_stack([xs[keep], ys[keep], zs[keep] + mid])
            b2 = w.sights_blocked(A2, B2, trees=trees)
            hidden[keep[~b2]] = False
    return hidden

def _robust_hidden(nav, threats, x, y, z, margin):
    pts = []
    for t in threats:
        ux, uy = unit2(x - t[0], y - t[1])
        for s in (-margin, margin):
            pts.append((x - uy * s, y + ux * s))
    xs = np.array([q[0] for q in pts] + [x])
    ys = np.array([q[1] for q in pts] + [y])
    zs = np.full(len(xs), z)
    return bool(_hidden_mask(nav, threats, xs, ys, zs).all())

def _window(T, x, y, radius, cover_only=False, stride=1, comp=None):
    I0 = round((x - T.x0) / 2.0)
    J0 = round((y - T.y0) / 2.0)
    r = math.ceil(radius / 2.0) + 1
    i0, i1 = (max(0, I0 - r), min(T.nx2, I0 + r + 1))
    j0, j1 = (max(0, J0 - r), min(T.ny2, J0 + r + 1))
    m = T.walk2[j0:j1, i0:i1]
    if cover_only:
        m = m & T.cover2[j0:j1, i0:i1]
    if comp is not None:
        m = m & (T.comp[j0:j1, i0:i1] == comp)
    jj, ii = np.nonzero(m)
    jj = jj + j0
    ii = ii + i0
    if stride > 1:
        keep = (ii % stride == 0) & (jj % stride == 0)
        jj, ii = (jj[keep], ii[keep])
    px = T.x0 + 2.0 * ii
    py = T.y0 + 2.0 * jj
    d = np.hypot(px - x, py - y)
    keep = d <= radius
    order = np.argsort(d[keep], kind='stable')
    jj, ii, px, py, d = (jj[keep][order], ii[keep][order], px[keep][order], py[keep][order], d[keep][order])
    return (jj * T.nx2 + ii, px, py, T.S2[jj, ii].astype(float), d)

def _spot(cls, x, y, z, **info):
    return cls(x, y, z, **info)

def cover_from(nav, me, threats_xyz, max_dist):
    from .nav import Spot
    T, c = (nav.T, nav.p.cover)
    pos = me['pos']
    px, py, pz = (float(pos[0]), float(pos[1]), float(pos[2]))
    thr = [tuple((float(v) for v in t[:3])) for t in threats_xyz]
    if not thr:
        return Spot(px, py, pz, dist=0.0)
    key = ('cover', T.node_of(px, py), tuple(sorted((T.node_of(t[0], t[1]) // 2 for t in thr))), round(max_dist))
    hit = nav._cache.get(key)
    if hit is not None and nav._clock - hit[0] < 0.5:
        return hit[1]
    res = _cover(nav, px, py, pz, thr, max_dist, c, T, Spot)
    if len(nav._cache) > 4096:
        nav._cache.clear()
    nav._cache[key] = (nav._clock, res)
    return res

def _cover(nav, px, py, pz, thr, max_dist, c, T, Spot):
    if _hidden_mask(nav, thr, np.array([px]), np.array([py]), np.array([pz]))[0]:
        return Spot(px, py, pz, dist=0.0)
    na, _ = T.snap(px, py, 8.0)
    ca = int(T.compf[na]) if na is not None else None
    nodes, xs, ys, zs, d = _window(T, px, py, max_dist, cover_only=True, stride=2, comp=ca)
    spent = 0
    hit = None
    for s0 in range(0, len(nodes), c.chunk):
        sl = slice(s0, s0 + c.chunk)
        spent += 2 * len(thr) * len(xs[sl])
        hid = _hidden_mask(nav, thr, xs[sl], ys[sl], zs[sl])
        for k in np.nonzero(hid)[0]:
            i = s0 + int(k)
            if _robust_hidden(nav, thr, xs[i], ys[i], zs[i], c.margin_m):
                hit = i
                break
        if hit is not None or spent >= c.max_rays:
            break
    if hit is None:
        return None
    best = [(float(d[hit]), float(xs[hit]), float(ys[hit]), float(zs[hit]))]
    _nn, nxs, nys, nzs, _nd = _window(T, xs[hit], ys[hit], 3.0)
    keep = ~((np.abs(nxs - xs[hit]) < 0.5) & (np.abs(nys - ys[hit]) < 0.5))
    if keep.any():
        nxs, nys, nzs = (nxs[keep], nys[keep], nzs[keep])
        hid = _hidden_mask(nav, thr, nxs, nys, nzs)
        for k in np.nonzero(hid)[0]:
            if _robust_hidden(nav, thr, nxs[k], nys[k], nzs[k], c.margin_m):
                best.append((math.hypot(nxs[k] - px, nys[k] - py), float(nxs[k]), float(nys[k]), float(nzs[k])))
    best.sort()
    for _dd, x, y, z in best[:c.max_checks]:
        pl = nav.path_length((px, py, pz), (x, y))
        if pl <= max_dist * c.path_slack:
            return Spot(x, y, z, dist=pl)
    return None

def peek_spot(nav, me, target, max_dist, max_range):
    from .nav import Spot
    if nav.on_roof(me):
        r = roof_edge_peek(nav, me, target)
        if r is not None:
            return r
    T, c = (nav.T, nav.p.peek)
    pos = me['pos']
    px, py = (float(pos[0]), float(pos[1]))
    tgt = tuple((float(v) for v in target[:3]))
    key = ('peek', T.node_of(px, py), T.node_of(tgt[0], tgt[1]) // 2, round(max_dist), max_range)
    hit = nav._cache.get(key)
    if hit is not None and nav._clock - hit[0] < 0.5:
        return hit[1]
    res = _peek(nav, px, py, tgt, max_dist, max_range, c, T, Spot)
    if len(nav._cache) > 4096:
        nav._cache.clear()
    nav._cache[key] = (nav._clock, res)
    return res

def _peek(nav, px, py, tgt, max_dist, max_range, c, T, Spot):
    na, _ = T.snap(px, py, 8.0)
    ca = int(T.compf[na]) if na is not None else None
    nodes, xs, ys, zs, d = _window(T, px, py, max_dist, cover_only=True, stride=c.stride, comp=ca)
    if not len(nodes):
        return None
    hid = np.nonzero(_hidden_mask(nav, [tgt], xs, ys, zs))[0][:c.max_hidden]
    if not len(hid):
        return None
    mid, top = _tp(nav)
    hi, fx, fy, fz = ([], [], [], [])
    for n, i in enumerate(hid):
        _nn, wx, wy, wz, _wd = _window(T, xs[i], ys[i], c.step_m, comp=ca)
        dd = np.hypot(wx - xs[i], wy - ys[i])
        k = dd > 1.0
        hi.extend([n] * int(k.sum()))
        fx.extend(wx[k])
        fy.extend(wy[k])
        fz.extend(wz[k])
    if not hi:
        return None
    hi = np.array(hi)
    fx, fy, fz = (np.array(fx), np.array(fy), np.array(fz))
    ok = np.ones(len(hi), bool)
    for t in (0.34, 0.67):
        mx = xs[hid][hi] + (fx - xs[hid][hi]) * t
        my = ys[hid][hi] + (fy - ys[hid][hi]) * t
        ii = np.clip(np.floor(mx - T.x0 + 0.5).astype(np.intp), 0, T.nx - 1)
        jj = np.clip(np.floor(my - T.y0 + 0.5).astype(np.intp), 0, T.ny - 1)
        ok &= T.walk1[jj, ii]
    if max_range is not None:
        ok &= np.hypot(fx - tgt[0], fy - tgt[1]) <= max_range
    idx = np.nonzero(ok)[0]
    if not len(idx):
        return None
    w = nav.world
    tc = np.array([tgt[0], tgt[1], tgt[2] + mid])
    tt = np.array([tgt[0], tgt[1], tgt[2] + top])
    muzzle = np.column_stack([fx[idx], fy[idx], fz[idx] + nav.m['muzzle_height']])
    clear = np.isnan(w.segments_hit(muzzle, np.broadcast_to(tc, muzzle.shape)))
    idx = idx[clear]
    if not len(idx):
        return None
    eye = np.column_stack([fx[idx], fy[idx], fz[idx] + nav.m['eye_height']])
    sees = ~(w.sights_blocked(eye, np.broadcast_to(tc, eye.shape), trees=False) & w.sights_blocked(eye, np.broadcast_to(tt, eye.shape), trees=False))
    good = idx[sees]
    if not len(good):
        return None
    for n, i in enumerate(hid):
        mine = good[hi[good] == n]
        mine = mine[np.argsort(np.hypot(fx[mine] - xs[i], fy[mine] - ys[i]), kind='stable')]
        for j in mine[:3]:
            e = (fx[j], fy[j], fz[j] + nav.m['eye_height'])
            if nav.has_los(e, tc) or nav.has_los(e, tt):
                return (Spot(xs[i], ys[i], zs[i], dist=float(d[i])), Spot(fx[j], fy[j], fz[j]))
    return None

def roof_edge_peek(nav, me, target):
    from .nav import Spot
    if not nav.on_roof(me):
        return None
    T, c = (nav.T, nav.p.roofs)
    pos = me['pos']
    px, py = (float(pos[0]), float(pos[1]))
    tgt = tuple((float(v) for v in target[:3]))
    na, _ = T.snap(px, py, 8.0)
    if na is None:
        return None
    comp = int(T.compf[na])
    nodes, xs, ys, zs, d = _window(T, px, py, c.peek_radius_m)
    inroof = T.compf[nodes] == comp
    nodes, xs, ys, zs, d = (nodes[inroof], xs[inroof], ys[inroof], zs[inroof], d[inroof])
    if not len(nodes):
        return None
    mid, top = _tp(nav)
    eye = nav.m['eye_height']
    w = nav.world
    A = np.column_stack([xs, ys, zs + eye])
    Bc = np.broadcast_to(np.array([tgt[0], tgt[1], tgt[2] + mid]), (len(xs), 3))
    Bt = np.broadcast_to(np.array([tgt[0], tgt[1], tgt[2] + top]), (len(xs), 3))
    sees = ~(w.sights_blocked(A, Bc) & w.sights_blocked(A, Bt))
    cand = np.nonzero(sees)[0][:c.peek_tries]
    if not len(cand):
        return None
    behind = list(c.peek_behind_m)
    hs, meta = ([], [])
    for i in cand:
        ux, uy = unit2(tgt[0] - xs[i], tgt[1] - ys[i])
        for b in behind:
            hx, hy = (xs[i] - ux * b, ys[i] - uy * b)
            if not T.clear_around(hx, hy):
                continue
            hz = T.surface(hx, hy)
            if abs(hz - zs[i]) > T.step_up or not T.line_clear(hx, hy, xs[i], ys[i]):
                continue
            hs.append((hx, hy, hz))
            meta.append(i)
    if not hs:
        return None
    H = np.array(hs)
    hid = _hidden_mask(nav, [tgt], H[:, 0], H[:, 1], H[:, 2])
    for k in np.nonzero(hid)[0]:
        i = meta[k]
        return (Spot(H[k, 0], H[k, 1], H[k, 2], dist=float(d[i])), Spot(xs[i], ys[i], zs[i], dist=float(d[i])))
    return None

def ground_can_see(nav, xyz, min_range, max_range):
    T = nav.T
    x, y, z = (float(xyz[0]), float(xyz[1]), float(xyz[2]))
    key = ('gview', round(x / 2.0), round(y / 2.0), round(z, 1), min_range, max_range)
    hit = nav._cache.get(key)
    if hit is not None:
        return hit
    d = np.hypot(T.gs_x - x, T.gs_y - y)
    sel = (d >= min_range) & (d <= max_range)
    if not sel.any():
        nav._cache[key] = 0.0
        return 0.0
    mid, top = _tp(nav)
    w = nav.world
    A = np.column_stack([T.gs_x[sel], T.gs_y[sel], T.gs_z[sel] + nav.m['eye_height']])
    n = len(A)
    seen = ~w.sights_blocked(A, np.broadcast_to(np.array([x, y, z + top]), (n, 3)), trees=False)
    rest = np.nonzero(~seen)[0]
    if len(rest):
        seen[rest] = ~w.sights_blocked(A[rest], np.broadcast_to(np.array([x, y, z + mid]), (len(rest), 3)), trees=False)
    share = float(seen.mean())
    if len(nav._cache) > 4096:
        nav._cache.clear()
    nav._cache[key] = share
    return share

def roof_sites(nav):
    T, c = (nav.T, nav.p.roofs)
    sel = (T.rn_h >= c.site_min_height_m) & (T.rn_depth >= c.stand_back_m)
    idx = np.nonzero(sel)[0]
    out = {}
    if not len(idx):
        return out
    comps = T.rn_comp[idx]
    order = np.argsort(comps, kind='stable')
    cuts = np.nonzero(np.diff(comps[order]))[0] + 1
    for grp in np.split(order, cuts):
        members = idx[grp]
        picks = [int(members[int(np.argmax(T.rn_depth[members]))])]
        floor = max(c.stand_back_m, 0.5 * float(T.rn_depth[picks[0]]))
        pool = members[T.rn_depth[members] >= floor]
        for _ in range(c.site_points - 1):
            px = np.array([T.rn_x[k] for k in picks])
            py = np.array([T.rn_y[k] for k in picks])
            dd = np.min(np.hypot(T.rn_x[pool][:, None] - px[None, :], T.rn_y[pool][:, None] - py[None, :]), axis=1)
            if dd.max() < 6.0:
                break
            picks.append(int(pool[int(np.argmax(dd))]))
        out[int(T.rn_comp[picks[0]])] = [(k, ground_can_see(nav, (float(T.rn_x[k]), float(T.rn_y[k]), float(T.rn_z[k])), 0.0, 150.0)) for k in picks]
    return out

def high_ground(nav, me, circle, max_climb, min_height, stand_back):
    from .nav import HighGround, Spot
    from .terrain import JET
    T, c = (nav.T, nav.p.roofs)
    cx, cy, r = circle
    pos = me['pos']
    px, py, pz = (float(pos[0]), float(pos[1]), float(pos[2]))
    budget = nav.climb_budget(me, max_climb)
    na, _ = T.snap(px, py, 10.0)
    if na is None:
        return None
    ca = int(T.compf[na])
    sel = (T.rn_h >= min_height) & (T.rn_depth >= stand_back) & (np.hypot(T.rn_x - cx, T.rn_y - cy) <= r)
    if not sel.any():
        return None
    idx_sel = np.nonzero(sel)[0]
    cands = []
    for cb in np.unique(T.rn_comp[idx_sel]):
        cb = int(cb)
        if cb == ca:
            cands.append((0.0, cb, None))
            continue
        ids = T._into.get(cb)
        if ids is None:
            continue
        kinds = T.link_kind[ids]
        req = T.link_req[ids]
        ok = (kinds != JET) | (req <= budget)
        if not ok.any():
            continue
        ids = ids[ok]
        src = T.link_src[ids]
        best = None
        for q, s in zip(ids.tolist(), src.tolist(), strict=True):
            if not T.comp_reach(ca, int(T.compf[s]), 0.0, False):
                continue
            sx, sy = T.xy_of(s)
            est = 1.2 * math.hypot(sx - px, sy - py) + float(T.link_cost[q])
            if best is None or est < best[0]:
                best = (est, q)
        if best is not None:
            cands.append((best[0], cb, best[1]))
    if not cands:
        return None
    cands.sort(key=lambda t: t[0])
    results = []
    for est, cb, q in cands[:c.candidates]:
        members = idx_sel[T.rn_comp[idx_sel] == cb]
        dst_xy = T.xy_of(int(T.link_dst[q])) if q is not None else (px, py)
        best = None
        vis = None
        pre = nav._roof_sites.get(cb) if stand_back >= c.stand_back_m else None
        if pre:
            ok_pre = [(v, k) for k, v in pre if sel[k] and k in set(members.tolist())]
            if ok_pre:
                vis, best = min(ok_pre)
        if best is None:
            dd = np.hypot(T.rn_x[members] - dst_xy[0], T.rn_y[members] - dst_xy[1])
            depth = np.minimum(T.rn_depth[members], c.depth_bonus_cap_m)
            best = members[int(np.argmax(depth * 1000.0 - dd))]
        stand = (float(T.rn_x[best]), float(T.rn_y[best]), float(T.rn_z[best]))
        if q is not None:
            s_node = int(T.link_src[q])
            tk = T.xy_of(s_node)
            tz = T.z_of(s_node)
            walk = nav.path_length((px, py, pz), tk)
            link_c = float(T.link_cost[q])
            climb = float(T.link_need[q])
        else:
            tk, tz, walk, link_c, climb = ((px, py), pz, 0.0, 0.0, 0.0)
        if not math.isfinite(walk):
            continue
        if vis is None:
            vis = ground_can_see(nav, stand, 0.0, 150.0)
        back = float(T.rn_depth[best])
        score = walk + link_c + c.vis_weight_m * vis - c.depth_bonus_m * min(back, c.depth_bonus_cap_m)
        results.append((score, HighGround(Spot(stand[0], stand[1], stand[2], area_comp=cb), Spot(tk[0], tk[1], tz), stand[2], float(T.rn_h[best]), back, vis, walk + link_c, climb)))
    if not results:
        return None
    results.sort(key=lambda t: t[0])
    return results[0][1]
