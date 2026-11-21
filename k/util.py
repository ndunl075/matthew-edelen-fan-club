from __future__ import annotations
import copy
import math
from .kitdefaults import DEFAULT_CONFIG
TAU = 2.0 * math.pi
POSITIVE = frozenset({'tick_hz', 'decide_every', 'duration_s', 'gravity', 'walk_speed', 'boost_speed', 'speed', 'cooldown', 'range', 'turn_rate', 'pitch_rate', 'mag', 'charge', 'ttl', 'hp', 'radius', 'height', 'eye_height', 'muzzle_height', 'fov_deg', 'near_range', 'step_speed', 'splash_radius', 'cone_deg', 'pickup_radius', 'energy'})

def wrap(a: float) -> float:
    a = (a + math.pi) % TAU - math.pi
    return math.pi if a == -math.pi else a

def angdiff(a: float, b: float) -> float:
    return wrap(a - b)

def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else min(x, hi)

def lerp(a: float, b: float, s: float) -> float:
    return a + (b - a) * s

def dist2(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])

def dist3(a, b) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)

def yaw_to(a, b) -> float:
    return math.atan2(b[1] - a[1], b[0] - a[0])

def unit2(x: float, y: float) -> tuple[float, float]:
    n = math.hypot(x, y)
    return (0.0, 0.0) if n < 1e-09 else (x / n, y / n)

def rot2(v, ang: float) -> tuple[float, float]:
    c, s = (math.cos(ang), math.sin(ang))
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)

def dir3(yaw: float, pitch: float) -> tuple[float, float, float]:
    cp = math.cos(pitch)
    return (cp * math.cos(yaw), cp * math.sin(yaw), math.sin(pitch))

def angle_between(u, v) -> float:
    nu = math.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)
    nv = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if nu < 1e-09 or nv < 1e-09:
        return 0.0
    return math.acos(clamp((u[0] * v[0] + u[1] * v[1] + u[2] * v[2]) / (nu * nv), -1.0, 1.0))

def aim_to(origin, target) -> tuple[float, float]:
    dx, dy, dz = (target[0] - origin[0], target[1] - origin[1], target[2] - origin[2])
    return (math.atan2(dy, dx), math.atan2(dz, math.hypot(dx, dy)))

def aim_error(a, b) -> float:
    return angle_between(dir3(a[0], a[1]), dir3(b[0], b[1]))

def num(d, key: str, default: float, positive: bool | None=None) -> float:
    try:
        v = d[key]
    except (KeyError, IndexError, TypeError):
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (not math.isfinite(v)):
        return default
    if (key in POSITIVE if positive is None else positive) and v <= 0:
        return default
    return v

def fill_numbers(have, defaults: dict) -> dict:
    out = dict(have) if isinstance(have, dict) else {}
    for key, dv in defaults.items():
        if isinstance(dv, dict):
            out[key] = fill_numbers(out.get(key), dv)
        elif isinstance(dv, bool):
            out[key] = out[key] if isinstance(out.get(key), bool) else dv
        elif isinstance(dv, (int, float)):
            out[key] = num(out, key, dv)
        elif not isinstance(out.get(key), type(dv)):
            out[key] = copy.deepcopy(dv)
    return out

def sane_config(cfg) -> dict:
    return fill_numbers(cfg, DEFAULT_CONFIG)

def sane_stats(stats, defaults) -> dict:
    base = {k: v for k, v in (defaults or {}).items() if isinstance(v, (int, float)) and (not isinstance(v, bool))}
    return fill_numbers(stats, base)
