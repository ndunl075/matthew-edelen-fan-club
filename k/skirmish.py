from __future__ import annotations
import math
from .util import rot2, unit2

def late_game(p, alive: int, next_radius: float) -> bool:
    q = p.skirmish
    return alive <= q.late_alive or next_radius <= q.late_radius_m

def engage_reason(p, *, dist: float, hit_age_ok: bool, closing: float, hp_frac: float, hp_known: bool, aimed: bool, late: bool) -> str | None:
    q = p.skirmish
    if dist < q.engage_near_m:
        return 'near'
    if hit_age_ok:
        return 'hit us'
    if closing > q.engage_closing_mps and dist < q.engage_closing_m:
        return 'closing'
    if hp_known and hp_frac < q.engage_low_hp_frac and (dist < q.engage_low_hp_m):
        return 'weak'
    if aimed and dist < q.engage_aimed_m:
        return 'aims at us'
    if late:
        return 'late game'
    return None

def target_score(p, *, dist: float, hp: float, hp_frac: float, keep: bool, hit_recent: bool, closing: float) -> float:
    q = p.skirmish
    s = dist + q.target_w_hp * hp
    if hp_frac < q.target_low_hp_frac:
        s -= q.target_low_hp_bonus
    if keep:
        s -= q.target_keep_bonus
    if hit_recent:
        s -= q.target_hit_bonus
    if closing > q.target_closing_mps:
        s -= q.target_closing_bonus
    return s

def is_losing(p, *, n_near: int, hp_frac: float, target_hp_frac: float | None) -> bool:
    q = p.skirmish
    if n_near >= q.retreat_n_low and hp_frac < q.retreat_hp_low:
        return True
    if n_near >= q.retreat_n_high and hp_frac < q.retreat_hp_high:
        return True
    return hp_frac < q.retreat_hp_solo and target_hp_frac is not None and (target_hp_frac > hp_frac)

def is_finishing(p, *, n_near: int, hp_frac: float, target_hp_frac: float | None) -> bool:
    q = p.skirmish
    return target_hp_frac is not None and target_hp_frac < q.finish_target_hp and (hp_frac > q.finish_own_hp) and (n_near <= q.finish_max_enemies)

def hold_range(p, *, has_laser: bool, has_gun: bool, has_lightning: bool, enemy_lightning: bool, enemy_mortar: bool, cap: float) -> float:
    q = p.skirmish
    if has_laser:
        h = q.hold_laser_m
    elif has_gun:
        h = q.hold_gun_m
    elif has_lightning:
        h = q.hold_lightning_m
    else:
        h = q.hold_gun_m
    if h > q.hold_lightning_m:
        if enemy_lightning:
            h = max(h, q.min_from_lightning_m)
        if enemy_mortar:
            h = max(h, q.min_from_mortar_m)
    return min(h, cap)

def pick_heading(p, *, pos, target, hold: float, side: float, prev, inside, clear, sees, others, cube, sight_calls: int=8):
    q = p.skirmish
    n = max(int(q.headings), 4)
    step = q.step_m
    tx, ty = target
    toward = unit2(tx - pos[0], ty - pos[1])
    tang = rot2(toward, side * math.pi / 2.0)
    d0 = math.hypot(tx - pos[0], ty - pos[1])
    band = q.range_band_frac * hold
    err0 = max(0.0, abs(d0 - hold) - band)
    cube_d0 = math.hypot(cube[0] - pos[0], cube[1] - pos[1]) if cube is not None else 0.0
    cands = []
    for k in range(n):
        a = 2.0 * math.pi * k / n
        u = (math.cos(a), math.sin(a))
        p = (pos[0] + u[0] * step, pos[1] + u[1] * step)
        if not clear(pos, p):
            continue
        s = q.w_range * (err0 - max(0.0, abs(math.hypot(tx - p[0], ty - p[1]) - hold) - band)) / step
        s += q.w_strafe * (u[0] * tang[0] + u[1] * tang[1])
        if cube is not None:
            s += q.w_cube * (cube_d0 - math.hypot(cube[0] - p[0], cube[1] - p[1])) / step
        if not inside(p[0], p[1]):
            s -= q.w_zone
        if prev is not None:
            s += q.w_keep * (u[0] * prev[0] + u[1] * prev[1])
        cands.append([s, u, p])
    if not cands:
        return None
    cands.sort(key=lambda c: -c[0])
    for c in cands[:max(int(sight_calls), 1)]:
        if sees(c[2], target):
            c[0] += q.w_los
        c[0] -= q.w_cover * sum((1 for o in others[:2] if sees(c[2], o)))
    return max(cands[:max(int(sight_calls), 1)], key=lambda c: c[0])[1]
