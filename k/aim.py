from __future__ import annotations
import math
from dataclasses import dataclass
from .util import aim_to, clamp, dir3, wrap

@dataclass
class Solution:
    yaw: float
    pitch: float
    flight_s: float
    point: tuple

    @property
    def aim(self) -> list:
        return [self.yaw, self.pitch]

def intercept_time(rel, vel, speed: float) -> float | None:
    a = vel[0] ** 2 + vel[1] ** 2 + vel[2] ** 2 - speed ** 2
    b = 2.0 * (rel[0] * vel[0] + rel[1] * vel[1] + rel[2] * vel[2])
    c = rel[0] ** 2 + rel[1] ** 2 + rel[2] ** 2
    if abs(a) < 1e-09:
        return -c / b if b < -1e-09 else None
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return None
    sq = math.sqrt(disc)
    roots = sorted(((-b - sq) / (2.0 * a), (-b + sq) / (2.0 * a)))
    for r in roots:
        if r > 1e-06:
            return r
    return None

def gun_solution(pos, muzzle_h: float, offset: float, target, target_vel, speed: float, iterations: int=3) -> Solution | None:
    o0 = (pos[0], pos[1], pos[2] + muzzle_h)
    yaw, pitch = aim_to(o0, target)
    t = 0.0
    for _ in range(iterations):
        d = dir3(yaw, pitch)
        origin = (o0[0] + offset * d[0], o0[1] + offset * d[1], o0[2] + offset * d[2])
        rel = (target[0] - origin[0], target[1] - origin[1], target[2] - origin[2])
        t = intercept_time(rel, target_vel, speed)
        if t is None:
            return None
        point = (target[0] + target_vel[0] * t, target[1] + target_vel[1] * t, target[2] + target_vel[2] * t)
        yaw, pitch = aim_to(o0, point)
    return Solution(yaw, pitch, t, point)

def mortar_pitch(dist: float, dz: float, speed: float, gravity: float, high: bool=False) -> float | None:
    if dist < 1e-06:
        return math.pi / 2.0 if high else None
    speed, gravity = (max(speed, 1e-06), max(gravity, 1e-06))
    k = 2.0 * gravity / (speed * speed)
    disc = 1.0 - k * (dz + gravity * dist * dist / (2.0 * speed * speed))
    if disc < 0.0:
        return None
    u = speed * speed / (gravity * dist) * (1.0 + (math.sqrt(disc) if high else -math.sqrt(disc)))
    return math.atan(u)

def mortar_solution(pos, tube_h: float, offset: float, target, target_vel, speed: float, gravity: float, pitch_lo: float, pitch_hi: float, ttl: float, high: bool=False, iterations: int=5) -> Solution | None:
    o0 = (pos[0], pos[1], pos[2] + tube_h)
    point = tuple(target)
    pitch = 0.6
    t = 0.0
    yaw = 0.0
    for _ in range(iterations):
        yaw = math.atan2(point[1] - o0[1], point[0] - o0[0])
        d = dir3(yaw, pitch)
        origin = (o0[0] + offset * d[0], o0[1] + offset * d[1], o0[2] + offset * d[2])
        dist = math.hypot(point[0] - origin[0], point[1] - origin[1])
        new_pitch = mortar_pitch(dist, point[2] - origin[2], speed, gravity, high)
        if new_pitch is None:
            return None
        pitch = new_pitch
        t = dist / max(speed * math.cos(pitch), 1e-06)
        point = (target[0] + target_vel[0] * t, target[1] + target_vel[1] * t, target[2] + target_vel[2] * t)
    if t > ttl or not pitch_lo <= pitch <= pitch_hi:
        return None
    return Solution(yaw, pitch, t, point)

def mortar_impact(pos, vel, gravity: float, plane_z: float) -> tuple[float, float, float] | None:
    a, b, c = (-0.5 * max(gravity, 1e-06), vel[2], pos[2] - plane_z)
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return None
    t = (-b - math.sqrt(disc)) / (2.0 * a)
    if t < 0.0:
        return None
    return (pos[0] + vel[0] * t, pos[1] + vel[1] * t, t)

def step_aim(cur, want, turn_rate: float, pitch_rate: float, dt: float, pitch_lo: float, pitch_hi: float) -> list:
    dyaw = wrap(want[0] - cur[0])
    ystep = turn_rate * dt
    yaw = want[0] if abs(dyaw) <= ystep else cur[0] + math.copysign(ystep, dyaw)
    wp = clamp(want[1], pitch_lo, pitch_hi)
    dp = wp - cur[1]
    pstep = pitch_rate * dt
    pitch = wp if abs(dp) <= pstep else cur[1] + math.copysign(pstep, dp)
    return [wrap(yaw), pitch]

def slew_time(cur, want, turn_rate: float, pitch_rate: float, pitch_lo: float, pitch_hi: float) -> float:
    dyaw = abs(wrap(want[0] - cur[0])) / max(turn_rate, 1e-06)
    dp = abs(clamp(want[1], pitch_lo, pitch_hi) - cur[1]) / max(pitch_rate, 1e-06)
    return max(dyaw, dp)

def hit_tolerance(dist: float, radius: float, frac: float, min_rad: float, max_rad: float) -> float:
    return clamp(math.atan2(radius * frac, max(dist, 1.0)), min_rad, max_rad)

def laser_release(charge: float, full_s: float, min_charge_s: float, aligned: bool, threatened: bool, slack_s: float, early_frac: float, max_extra_s: float) -> bool:
    if charge < min_charge_s:
        return False
    if aligned and charge >= full_s - slack_s:
        return True
    if aligned and threatened and (charge >= early_frac * full_s):
        return True
    return charge >= full_s + max_extra_s
