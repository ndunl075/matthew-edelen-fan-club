from __future__ import annotations
import math
TAU = 2.0 * math.pi

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
