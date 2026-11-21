from __future__ import annotations
import math
from dataclasses import dataclass
from .params import Params
from .util import clamp, dist2, lerp
MAX_MARGIN_FRAC = 0.5

@dataclass(frozen=True)
class Circle:
    cx: float
    cy: float
    r: float

    @property
    def c(self) -> tuple[float, float]:
        return (self.cx, self.cy)

    def margin_for(self, margin: float) -> float:
        return min(margin, MAX_MARGIN_FRAC * self.r)

    def contains(self, p, margin: float=0.0) -> bool:
        return dist2(p, self.c) <= self.r - self.margin_for(margin) + 1e-09

def next_circle(zone: dict) -> Circle:
    return Circle(zone['next_center'][0], zone['next_center'][1], zone['next_radius'])

def circle_at(zone: dict, t: float) -> Circle:
    a = Circle(zone['center'][0], zone['center'][1], zone['radius'])
    b = next_circle(zone)
    s0, s1 = (zone['shrink_start'], zone['shrink_end'])
    if t <= s0 or s1 <= s0:
        return a
    s = clamp((t - s0) / (s1 - s0), 0.0, 1.0)
    return Circle(lerp(a.cx, b.cx, s), lerp(a.cy, b.cy, s), lerp(a.r, b.r, s))

def clamp_into(circle: Circle, p, margin: float) -> tuple[float, float]:
    m = circle.margin_for(margin)
    limit = max(0.0, circle.r - m)
    dx, dy = (p[0] - circle.cx, p[1] - circle.cy)
    d = math.hypot(dx, dy)
    if d <= limit:
        return (p[0], p[1])
    if d < 1e-09:
        return circle.c
    return (circle.cx + dx / d * limit, circle.cy + dy / d * limit)

def exposure_time(zone: dict, t: float, pos, margin: float, step: float) -> float:
    end = max(zone['shrink_end'], t)
    tau = t
    while True:
        if not circle_at(zone, tau).contains(pos, margin):
            return tau - t
        if tau >= end:
            return math.inf
        tau = min(tau + step, end)

def phase_index(zone: dict, phases: list) -> int:
    for i, ph in enumerate(phases):
        if abs(ph['radius'] - zone['next_radius']) < 0.5:
            return i
    return -1

@dataclass
class ZonePlan:
    outside_now: bool
    unsafe_now: bool
    center_mode: bool
    exposure_s: float
    need_m: float
    urgent: bool
    hurry: bool
    goal: tuple[float, float]
    next: Circle
    now: Circle
    seconds_left: float

def plan(zone: dict, t: float, pos, walk_speed: float, phases: list, p: Params, in_zone: bool=True) -> ZonePlan:
    z = p.zone
    now, nxt = (circle_at(zone, t), next_circle(zone))
    outside = not in_zone or dist2(pos, now.c) > now.r
    unsafe = not now.contains(pos, z.margin_m)
    exposure = exposure_time(zone, t, pos, z.margin_m, z.sample_s)
    d_next = dist2(pos, nxt.c)
    need = max(0.0, d_next - (nxt.r - nxt.margin_for(z.margin_m)))
    idx = phase_index(zone, phases)
    center_mode = idx >= len(phases) - int(z.center_phases) if idx >= 0 else nxt.r <= z.center_radius_m
    travel = need / max(walk_speed * z.speed_frac, 1e-06)
    far_from_center = d_next > max(z.center_hold_m, z.center_hold_frac * nxt.r)
    urgent = outside or (center_mode and far_from_center) or (exposure < math.inf and exposure - travel < z.buffer_s)
    hurry = outside or (exposure < math.inf and exposure - travel < z.hurry_s)
    goal = nxt.c if center_mode else clamp_into(nxt, pos, z.margin_m * z.goal_depth_mult)
    return ZonePlan(outside, unsafe, center_mode, exposure, need, urgent, hurry, goal, nxt, now, max(0.0, zone['shrink_end'] - t))

def hazard_at(hazards: list, c) -> float:
    total = 0.0
    for hx, hy, w, rad in hazards:
        d = math.hypot(c[0] - hx, c[1] - hy)
        if d < rad:
            total += w * (1.0 - d / rad)
    return total

def pick_spot(nxt: Circle, pos, hazards: list, p: Params) -> tuple[float, float]:
    q = p.reposition
    m = p.zone.margin_m
    limit = max(0.0, nxt.r - nxt.margin_for(m))
    cands = [clamp_into(nxt, pos, m)]
    for frac in q.ring_fracs:
        for k in range(int(q.ring_points)):
            a = 2.0 * math.pi * k / q.ring_points
            cands.append((nxt.cx + math.cos(a) * limit * frac, nxt.cy + math.sin(a) * limit * frac))
    for k in range(int(q.local_points)):
        a = 2.0 * math.pi * k / q.local_points
        cands.append(clamp_into(nxt, (pos[0] + math.cos(a) * q.local_step_m, pos[1] + math.sin(a) * q.local_step_m), m))
    best, best_score = (cands[0], -math.inf)
    for c in cands:
        score = -q.w_travel * dist2(c, pos) / q.travel_unit_m
        score -= q.w_center * dist2(c, nxt.c) / max(nxt.r, 1.0)
        score -= q.w_hazard * hazard_at(hazards, c)
        if score > best_score:
            best, best_score = (c, score)
    return clamp_into(nxt, best, m)
