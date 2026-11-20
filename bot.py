"""Mech Royale bot: aggressive kill-snowball.

Team thesis: kills drop cubes, cubes give HP and damage, so the mech that gets the
first few kills snowballs. Hunt the nearest visible mech from the first second and
never stop chasing. Weights live in tuning.json so we can tweak without touching code.
"""
import json
import math
import os

import mechfight as mf

HERE = os.path.dirname(os.path.abspath(__file__))


def load_tuning():
    with open(os.path.join(HERE, "tuning.json")) as f:
        return json.load(f)


def wrap(a):
    """Wrap an angle to [-pi, pi]."""
    return (a + math.pi) % (2 * math.pi) - math.pi


def dist2(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def unit(dx, dy):
    d = math.hypot(dx, dy)
    return (dx / d, dy / d) if d > 1e-6 else (0.0, 0.0)


class Bot:
    name = "snowball-v1"

    def __init__(self, hello):
        self.cfg = hello["config"]
        self.tune = load_tuning()
        self.g = self.cfg["weapons"]["mortar"]["gravity"]
        mech = self.cfg["mech"]
        self.min_pitch = mech.get("min_pitch", -0.6)
        self.max_pitch = mech.get("max_pitch", 1.2)
        self.reach = self.cfg["loot"]["pickup_radius"] - 0.5
        self.last_pos = None
        self.stuck = 0
        self.target_id = None

    # ------------------------------------------------------------------ targeting

    def pick_target(self, obs):
        """Nearest visible mech. Fall back to a nearby NPC drone (2 cubes each)."""
        me = obs["self"]["pos"]
        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        if mechs:
            return min(mechs, key=lambda m: dist2(m["pos"], me))
        npcs = [m for m in obs["mechs"] if m["kind"] == "npc"]
        npcs = [n for n in npcs if dist2(n["pos"], me) < self.tune["npc_target_range"]]
        return min(npcs, key=lambda m: dist2(m["pos"], me)) if npcs else None

    def lead(self, src, tgt, speed):
        """Predicted position of a moving target for a straight-flying projectile."""
        pos, vel = tgt["pos"], tgt.get("vel", [0, 0, 0])
        px, py, pz = pos[0], pos[1], pos[2] + 4.5
        t = 0.0
        for _ in range(3):  # fixed-point iteration is plenty at these speeds
            t = math.dist((px, py, pz), src) / speed
            px, py, pz = pos[0] + vel[0] * t, pos[1] + vel[1] * t, pos[2] + 4.5 + vel[2] * t
        return (px, py, pz)

    def gun_aim(self, me, tgt, stats):
        src = (me["pos"][0], me["pos"][1], me["pos"][2] + 7)
        px, py, pz = self.lead(src, tgt, stats.get("speed", 252))
        d = math.hypot(px - src[0], py - src[1])
        return [math.atan2(py - src[1], px - src[0]), math.atan2(pz - src[2], d)]

    def mortar_pitch(self, d, dz, v):
        """Low-arc pitch that lands a shell d metres away and dz metres above the tube."""
        g = self.g
        disc = v ** 4 - g * (g * d * d + 2 * dz * v * v)
        if disc < 0 or d < 1:
            return None
        return math.atan2(v * v - math.sqrt(disc), g * d)

    def mortar_aim(self, me, tgt, stats):
        v = stats.get("speed", 72)
        src = (me["pos"][0], me["pos"][1], me["pos"][2] + 9.5)
        pos, vel = tgt["pos"], tgt.get("vel", [0, 0, 0])
        px, py, pz = pos[0], pos[1], pos[2] + 2.0
        for _ in range(4):  # shells are slow: iterate the flight time
            d = math.hypot(px - src[0], py - src[1])
            pitch = self.mortar_pitch(d, pz - src[2], v)
            if pitch is None:
                return None
            t = d / (v * math.cos(pitch))
            px, py = pos[0] + vel[0] * t, pos[1] + vel[1] * t
        d = math.hypot(px - src[0], py - src[1])
        if not self.tune["mortar_min_dist"] < d < self.tune["mortar_max_dist"]:
            return None
        return [math.atan2(py - src[1], px - src[0]), pitch]

    # ------------------------------------------------------------------ navigation

    def zone_now(self, obs):
        """Current circle, interpolated through the shrink window."""
        z, t = obs["zone"], obs["t"]
        span = max(z["shrink_end"] - z["shrink_start"], 1e-6)
        k = min(max((t - z["shrink_start"]) / span, 0.0), 1.0)
        cx = z["center"][0] + (z["next_center"][0] - z["center"][0]) * k
        cy = z["center"][1] + (z["next_center"][1] - z["center"][1]) * k
        r = z["radius"] + (z["next_radius"] - z["radius"]) * k
        return (cx, cy), r

    def zone_goal(self, obs):
        """Where to wander when nothing is in sight: the centre, because that's where the fights are."""
        z = obs["zone"]
        return z["center"] if obs["t"] < self.tune["early_center_s"] else z["next_center"]

    def drop_goal(self, obs, me):
        """Every supply drop gets contested (3 cubes, full HP). Take the closest one."""
        drops = [d for d in obs["drops"] if dist2(d["pos"], me["pos"]) < self.tune["drop_chase"]]
        return min(drops, key=lambda d: dist2(d["pos"], me["pos"])) if drops else None

    def check_stuck(self, me):
        if self.last_pos and dist2(me["pos"], self.last_pos) < 0.4:
            self.stuck += 1
        else:
            self.stuck = 0
        self.last_pos = list(me["pos"])
        return self.stuck >= 4

    # ------------------------------------------------------------------ loot and upgrades

    def want_weapon(self, me, kind):
        """Rank-based weapon choice: mortar > lightning > gun, never the laser."""
        rank = self.tune["weapon_rank"]
        if kind in self.tune["never_take"] or rank.get(kind, 0) <= 0:
            return None  # not worth a slot
        held = [w["kind"] if w else None for w in me["weapons"]]
        if kind in held:
            return None
        if None in held:
            return {}  # free slot, no drop needed
        worst = min(range(2), key=lambda i: rank.get(held[i], 0))
        if rank[kind] > rank.get(held[worst], 0):
            return {"drop": worst}
        return None

    def loot_action(self, obs, me, act):
        """Take a pickup in reach. Returns the nearest wanted pickup for the navigator."""
        best = None
        for p in sorted(obs["pickups"], key=lambda p: dist2(p["pos"], me["pos"])):
            plan = self.want_weapon(me, p["kind"])
            if plan is None:
                continue
            if dist2(p["pos"], me["pos"]) <= self.reach and abs(p["pos"][2] - me["pos"][2]) < 3.5:
                act["take"] = p["id"]
                act.update(plan)
                return None
            if best is None:
                best = p
        return best

    def pick_upgrade(self, offer):
        ids = [c["id"] for c in offer["choices"]]
        for want in self.tune["upgrade_prefs"]:
            if want in ids:
                return ids.index(want)
        for i, cid in enumerate(ids):  # nothing on our list: anything that isn't defensive
            if cid not in self.tune["upgrade_avoid"]:
                return i
        return 0

    # ------------------------------------------------------------------ main loop

    def act(self, obs):
        me = obs["self"]
        pos = me["pos"]
        tune = self.tune
        act = {"tick": obs["tick"], "fire": [False, False]}
        if obs["offer"]:
            act["pick"] = 0  # TODO: choose upgrades

        weapons = me["weapons"]
        pickup = None  # TODO: weapon pickups
        tgt = self.pick_target(obs)
        drop = self.drop_goal(obs, me)
        stuck = self.check_stuck(me)
        center, radius = self.zone_now(obs)
        out_dist = dist2(pos, center) - (radius - tune["zone_margin"])
        zone_danger = (not me["in_zone"]) or out_dist > 0

        # --- decide where to go -------------------------------------------------
        goal, why = None, "roam"
        tdist = dist2(tgt["pos"], pos) if tgt else None
        if zone_danger:
            goal, why = center, "zone"
        elif drop and (tgt is None or tdist > 30):
            goal, why = drop["pos"], "drop"
        elif tgt:
            goal, why = tgt["pos"], "hunt"
        elif pickup and dist2(pickup["pos"], pos) < tune["pickup_chase"]:
            goal, why = pickup["pos"], "loot"
        elif obs["cubes"]:
            c = min(obs["cubes"], key=lambda c: dist2(c["pos"], pos))
            if dist2(c["pos"], pos) < tune["cube_chase"]:
                goal, why = c["pos"], "cube"
        if goal is None:
            snd = [s for s in obs["sounds"] if s["kind"] in ("gun", "mortar", "lightning", "explosion")]
            if snd:  # run toward the nearest gunfire
                s = min(snd, key=lambda s: {"near": 0, "mid": 1, "far": 2}[s["dist"]])
                goal = [pos[0] + 60 * math.cos(s["bearing"]), pos[1] + 60 * math.sin(s["bearing"])]
                why = "sound"
            else:
                goal = self.zone_goal(obs)
        # grab a pickup on the way if one is right here and nobody is shooting
        if why == "hunt" and pickup and tdist > tune["pickup_ignore_if_enemy"] \
                and dist2(pickup["pos"], pos) < 25:
            goal, why = pickup["pos"], "loot"

        dx, dy = unit(goal[0] - pos[0], goal[1] - pos[1])
        gdist = dist2(goal, pos)
        if why == "drop" and gdist < 5:
            dx, dy = 0.0, 0.0  # sit in the ring and capture
        elif why == "hunt" and tdist < 12:
            dx, dy = -dx * 0.3, -dy * 0.3  # too close for the mortar: back off a hair
        if why == "hunt" and tune["strafe"]:
            dx, dy = dx - dy * tune["strafe"], dy + dx * tune["strafe"]
        act["move"] = [dx, dy]
        # boost and jet freely to close distance
        far = gdist > tune["chase_boost_dist"]
        act["boost"] = bool((far or zone_danger or stuck) and me["energy"] > tune["min_energy_boost"])
        act["jump"] = bool(stuck and me["grounded"])
        if stuck and not me["grounded"]:
            act["boost"] = me["energy"] > 5

        # --- aim and fire ---------------------------------------------------------
        aim = None
        if tgt:
            aim = self.aim_and_fire(me, tgt, tdist, act)
        else:
            aim = [math.atan2(dy, dx), 0.0] if (dx or dy) else None
            gun = weapons[0] if weapons and weapons[0] and weapons[0]["kind"] == "gun" else None
            if gun and gun.get("ammo", 12) < 4 and gun.get("reload", 0) == 0:
                act["reload"] = True
        if aim:
            aim[1] = min(max(aim[1], self.min_pitch), self.max_pitch)
            act["aim"] = aim

        mf.draw.label(f"{why} hp {me['hp']:.0f} c{me['cubes']:.0f}")
        if tgt:
            mf.draw.line([pos[0], pos[1], pos[2] + 7], [tgt["pos"][0], tgt["pos"][1], tgt["pos"][2] + 4.5], "#ff5040")
        return act

    def aim_and_fire(self, me, tgt, tdist, act):
        """Mortar if it's ready and in range, otherwise lead the gun. Lightning when close."""
        slots = {w["kind"]: (i, w) for i, w in enumerate(me["weapons"]) if w}
        tol = self.tune["fire_tol_rad"]
        aim = None
        mortar = slots.get("mortar")
        if mortar and mortar[1]["cooldown"] < 0.3:
            m_aim = self.mortar_aim(me, tgt, mortar[1]["stats"])
            if m_aim:
                aim = m_aim
        if aim is None and "gun" in slots:
            aim = self.gun_aim(me, tgt, slots["gun"][1]["stats"])
        if aim is None:  # lightning only, or nothing: just face them
            aim = [math.atan2(tgt["pos"][1] - me["pos"][1], tgt["pos"][0] - me["pos"][0]), 0.0]

        cur = me["aim"]
        yaw_err = abs(wrap(aim[0] - cur[0]))
        pitch_err = abs(aim[1] - cur[1])
        ready = yaw_err < tol and pitch_err < tol * 1.5
        for kind, (i, w) in slots.items():
            if kind == "mortar":
                act["fire"][i] = ready and aim is not None and pitch_err < tol and \
                    w["cooldown"] < 0.15 and aim[1] > 0.05
            elif kind == "lightning":
                in_cone = yaw_err < math.radians(self.cfg["weapons"]["lightning"]["cone_deg"] * 0.5)
                act["fire"][i] = tdist < self.tune["lightning_range"] and in_cone and me["energy"] > 6
            elif kind == "gun":
                act["fire"][i] = ready and w.get("reload", 0) == 0 and aim[1] < 0.5 \
                    and not (mortar and aim[1] > 0.4)
        return aim


mf.run(Bot)
