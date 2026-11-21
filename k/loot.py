from __future__ import annotations
from .memory import Memory, PickupRec
from .params import Params
from .util import dist2

def rank_of(kind: str, p: Params) -> int:
    order = list(p.weapons.rank)
    return order.index(kind) if kind in order else len(order)

def held_kinds(weapons: list) -> list[str]:
    return [w['kind'] for w in weapons if w]

def worst_slot(weapons: list, p: Params) -> int | None:
    best = None
    for i, w in enumerate(weapons):
        if not w or (w['kind'] == 'laser' and w.get('charge', 0.0) > 0.0):
            continue
        if best is None or rank_of(w['kind'], p) > rank_of(weapons[best]['kind'], p):
            best = i
    return best

def take_plan(kind: str, weapons: list, p: Params, laser_threat: bool) -> tuple[bool, int | None]:
    if kind in held_kinds(weapons) or kind not in list(p.weapons.rank):
        return (False, None)
    if kind == 'mortar' and laser_threat:
        return (False, None)
    if any((w is None for w in weapons)):
        return (True, None)
    slot = worst_slot(weapons, p)
    if slot is not None and rank_of(kind, p) < rank_of(weapons[slot]['kind'], p):
        return (True, slot)
    return (False, None)

def mortar_drop_slot(weapons: list, laser_threat: bool) -> int | None:
    if not laser_threat:
        return None
    for i, w in enumerate(weapons):
        if w and w['kind'] == 'mortar':
            return i
    return None

def best_pickup(me: dict, mem: Memory, p: Params, laser_threat: bool, enemy_points: list) -> PickupRec | None:
    q = p.loot
    pos = me['pos']
    best, best_cost = (None, 1e+18)
    for rec in mem.pickups.values():
        if mem.take_block.get(rec.id, -1000000000.0) > mem.t or mem.kind_block.get(rec.kind, -1000000000.0) > mem.t:
            continue
        want, _ = take_plan(rec.kind, me['weapons'], p, laser_threat)
        d = dist2(rec.pos, pos)
        if not want or d > q.search_radius_m or rec.pos[2] - pos[2] > q.max_climb_m:
            continue
        if any((dist2(rec.pos, e) < q.enemy_clear_m for e in enemy_points)):
            continue
        cost = d - q.rank_bonus_m * (len(list(p.weapons.rank)) - rank_of(rec.kind, p))
        if cost < best_cost:
            best, best_cost = (rec, cost)
    return best

def best_cube(me: dict, mem: Memory, p: Params, enemy_points: list):
    q = p.loot
    pos = me['pos']
    best, best_d = (None, 1e+18)
    for rec in mem.cubes.values():
        d = dist2(rec.pos, pos)
        if d > q.cube_radius_m or abs(rec.pos[2] - pos[2]) > q.max_climb_m:
            continue
        if any((dist2(rec.pos, e) < q.enemy_clear_m for e in enemy_points)):
            continue
        if d < best_d:
            best, best_d = (rec, d)
    return best
