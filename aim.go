package main

import (
	"fmt"
	"math"
)

// Tunables (from local testing: laser fire held to ~110 m beat 140 m and 180 m).
const (
	laserFireRange = 110.0 // metres: start charging / release the laser only inside this
	noFightUntil   = 120.0 // seconds: before this, do not start fights (defend only)
)

// mayFire says whether we are allowed to open fire on enemy mechs now.
func (b *Brain) mayFire() bool {
	if b.t >= noFightUntil {
		return true
	}
	if b.t-b.lastDamage < 8 {
		return true
	}
	return b.target != nil && b.target.hostile(b.t)
}

func angleTo(fx, fy, tx, ty float64) float64 { return math.Atan2(ty-fy, tx-fx) }

// muzzle position of our mech.
func (b *Brain) muzzle() [3]float64 {
	mh := b.me.Stats["muzzle_height"]
	if mh == 0 {
		mh = 7
	}
	return [3]float64{b.px, b.py, b.pz + mh}
}

func aimAngles(from, to [3]float64) (yaw, pitch float64) {
	dx, dy, dz := to[0]-from[0], to[1]-from[1], to[2]-from[2]
	h := math.Hypot(dx, dy)
	return math.Atan2(dy, dx), math.Atan2(dz, h)
}

// intercept returns the point to aim at for a projectile of speed s fired from m at a target
// at p with velocity v, and the flight time.
func intercept(m, p, v [3]float64, s float64) ([3]float64, float64) {
	d := [3]float64{p[0] - m[0], p[1] - m[1], p[2] - m[2]}
	vv := v[0]*v[0] + v[1]*v[1] + v[2]*v[2]
	dv := d[0]*v[0] + d[1]*v[1] + d[2]*v[2]
	dd := d[0]*d[0] + d[1]*d[1] + d[2]*d[2]
	a := vv - s*s
	t := math.Sqrt(dd) / s
	if math.Abs(a) < 1e-6 {
		if dv < 0 {
			t = -dd / (2 * dv)
		}
	} else {
		disc := dv*dv - a*dd
		if disc >= 0 {
			sq := math.Sqrt(disc)
			t1 := (-dv - sq) / a
			t2 := (-dv + sq) / a
			best := math.Inf(1)
			for _, c := range []float64{t1, t2} {
				if c > 0.0 && c < best {
					best = c
				}
			}
			if !math.IsInf(best, 1) {
				t = best
			}
		}
	}
	if t > 1.5 {
		t = 1.5
	}
	// damp the velocity term a little: targets strafe
	return [3]float64{p[0] + v[0]*t*0.85, p[1] + v[1]*t*0.85, p[2] + v[2]*t*0.5}, t
}

func (b *Brain) enemyCenter(e *Enemy, dt float64) [3]float64 {
	p := e.predict(dt, b.t)
	sc := e.Scale
	if sc == 0 {
		sc = 1
	}
	return [3]float64{p[0], p[1], p[2] + 4.5*sc}
}

// chooseTarget picks the enemy mech to shoot at (or nil).
func (b *Brain) chooseTarget() {
	var best *Enemy
	bs := 1e18
	for _, e := range b.threats {
		age := b.t - e.LastSeen
		if age > 4.5 {
			continue
		}
		if e.Dist > b.laserRange {
			continue
		}
		if !e.hostile(b.t) && (b.t < noFightUntil || e.Dist > laserFireRange) {
			continue // a bystander: leave it alone early
		}
		s := e.HP/e.MaxHP*80 + e.Dist*0.35
		if e.LOS && e.InObs {
			s -= 25
		}
		if e.ID == b.curTarget {
			s -= 30
		}
		if s < bs {
			bs, best = s, e
		}
	}
	b.target = best
	if best != nil {
		b.curTarget = best.ID
	}
	// drones and crates only when no mech is near
	b.tdrone, b.tcrate = nil, nil
	if best != nil && best.Dist < 130 {
		return
	}
	var bd *Drone
	bdd := 85.0
	for _, d := range b.drones {
		if b.t-d.LastSeen > 3 {
			continue
		}
		dd := hypot2(b.px, b.py, d.Pos[0], d.Pos[1])
		if dd < bdd && b.ban[fmt.Sprintf("d%d", d.ID)] < b.t && !b.W.Blocked(b.px, b.py, b.eyeZ, d.Pos[0], d.Pos[1], d.Pos[2]+1.7) {
			bd, bdd = d, dd
		}
	}
	if bd != nil && b.noProgress(fmt.Sprintf("d%d", bd.ID), bd.HP) {
		bd = nil
	}
	b.tdrone = bd
	if bd == nil {
		var bc *CrateM
		bcd := 70.0
		for _, c := range b.crates {
			dd := hypot2(b.px, b.py, c.Pos[0], c.Pos[1])
			if dd < bcd && b.ban[fmt.Sprintf("c%d", c.ID)] < b.t && c.Pos[2]-b.W.GroundZ(c.Pos[0], c.Pos[1]) < 3 &&
				!b.W.Blocked(b.px, b.py, b.eyeZ, c.Pos[0], c.Pos[1], c.Pos[2]+1.2) {
				bc, bcd = c, dd
			}
		}
		if bc != nil && b.noProgress(fmt.Sprintf("c%d", bc.ID), bc.HP) {
			bc = nil
		}
		b.tcrate = bc
	} else {
		b.progKey = ""
	}
}

// noProgress bans a drone/crate we keep shooting without hurting it (blocked line, bad angle).
func (b *Brain) noProgress(key string, hp float64) bool {
	if b.progKey != key {
		b.progKey, b.progT, b.progHP = key, b.t, hp
		return false
	}
	if hp < b.progHP-1 {
		b.progT, b.progHP = b.t, hp
	}
	if b.t-b.progT > 5 {
		b.ban[key] = b.t + 120
		b.progKey = ""
		return true
	}
	return false
}

// aimAndFire sets aim and fire flags.
func (b *Brain) aimAndFire(a *Action) {
	gunSlot := b.weaponSlot("gun")
	lasSlot := b.weaponSlot("laser")
	lgtSlot := b.weaponSlot("lightning")
	mor := b.weaponSlot("mortar")
	m := b.muzzle()
	curYaw, curPitch := b.me.Aim[0], b.me.Aim[1]
	a.Aim = [2]float64{curYaw, curPitch}
	var gunW, lasW, lgtW *Weapon
	if gunSlot >= 0 {
		gunW = b.me.Weapons[gunSlot]
	}
	if lasSlot >= 0 {
		lasW = b.me.Weapons[lasSlot]
	}
	if lgtSlot >= 0 {
		lgtW = b.me.Weapons[lgtSlot]
	}
	_ = mor

	var tgtPt [3]float64
	var tgtVel [3]float64
	have := false
	dist := 0.0
	losOK := false
	tscale := 1.0
	switch {
	case b.target != nil:
		e := b.target
		tgtPt = b.enemyCenter(e, 0.0)
		tgtVel = e.Vel
		if b.t-e.LastSeen > 0.3 {
			tgtVel = [3]float64{e.Vel[0] * 0.5, e.Vel[1] * 0.5, 0}
		}
		have = true
		tscale = math.Max(1, e.Scale)
		dist = math.Hypot(tgtPt[0]-m[0], tgtPt[1]-m[1])
		losOK = !b.W.Blocked(m[0], m[1], m[2], tgtPt[0], tgtPt[1], tgtPt[2])
	case b.tdrone != nil:
		d := b.tdrone
		tgtPt = [3]float64{d.Pos[0], d.Pos[1], d.Pos[2] + 1.7}
		have = true
		dist = math.Hypot(tgtPt[0]-m[0], tgtPt[1]-m[1])
		losOK = !b.W.Blocked(m[0], m[1], m[2], tgtPt[0], tgtPt[1], tgtPt[2])
	case b.tcrate != nil:
		c := b.tcrate
		tgtPt = [3]float64{c.Pos[0], c.Pos[1], c.Pos[2] + 1.2}
		have = true
		dist = math.Hypot(tgtPt[0]-m[0], tgtPt[1]-m[1])
		losOK = !b.W.Blocked(m[0], m[1], m[2], tgtPt[0], tgtPt[1], tgtPt[2])
	}

	if !have {
		a.Aim = b.lookAim()
		// idle reload
		if gunW != nil && gunW.Ammo < 6 && gunW.Reload == 0 {
			a.Reload = true
		}
		// let a charging laser go so we never hold it blind
		return
	}

	// pick weapon plan
	laserReady := lasW != nil && lasW.Cooldown < 0.25
	laserCharging := lasW != nil && lasW.Charge > 0.001
	isMech := b.target != nil
	var aimPt [3]float64
	usingLaser := false
	if isMech && lasW != nil && (laserReady || laserCharging) {
		// hitscan: aim at where the target will be at release
		aimPt = [3]float64{tgtPt[0] + tgtVel[0]*0.08, tgtPt[1] + tgtVel[1]*0.08, tgtPt[2]}
		usingLaser = true
	} else {
		sp := 252.0
		if gunW != nil && gunW.Stats["speed"] > 0 {
			sp = gunW.Stats["speed"]
		}
		if isMech || b.tdrone != nil {
			aimPt, _ = intercept(m, tgtPt, tgtVel, sp)
		} else {
			aimPt = tgtPt
		}
	}
	yaw, pitch := aimAngles(m, aimPt)
	pitch = clamp(pitch, b.me.Stats["min_pitch"]-0.0, math.Max(1.3, b.me.Stats["max_pitch"]))
	if b.me.Stats["min_pitch"] == 0 {
		pitch = clamp(pitch, -0.7, 1.3)
	}
	a.Aim = [2]float64{yaw, pitch}
	errYaw := math.Abs(wrapPi(yaw - curYaw))
	errPitch := math.Abs(pitch - curPitch)
	angErr := math.Hypot(errYaw*math.Cos(pitch), errPitch)
	tolBase := math.Atan2(1.9*tscale, math.Max(dist, 8))

	if isMech && !b.mayFire() {
		return
	}
	// gun
	if gunW != nil && gunSlot >= 0 && losOK && gunW.Reload == 0 {
		maxR := 135.0
		if !isMech {
			maxR = 75
		}
		if b.laserLess() {
			maxR = 150
		}
		if dist <= maxR && angErr < clamp(tolBase, 0.012, 0.16) {
			a.Fire[gunSlot] = true
		}
	}
	// lightning: close and in cone
	if lgtW != nil && lgtSlot >= 0 && isMech && dist < b.me.Stats["lightning_range"]+42 && losOK {
		if errYaw < 0.4 && b.me.Energy > 8 {
			a.Fire[lgtSlot] = true
		}
	}
	// laser
	if lasW != nil && lasSlot >= 0 {
		a.Fire[lasSlot] = b.laserTrigger(lasW, isMech, usingLaser, dist, losOK, angErr, tolBase)
	}
}

func (b *Brain) laserLess() bool {
	for _, w := range b.me.Weapons {
		if w != nil && w.Kind == "laser" {
			return false
		}
	}
	return true
}

// laserTrigger decides whether the laser trigger is held this decision. Holding charges the laser,
// letting go fires it.
func (b *Brain) laserTrigger(w *Weapon, isMech, using bool, dist float64, losOK bool, angErr, tolBase float64) bool {
	full := w.Stats["charge"]
	if full == 0 {
		full = b.laserCharge
	}
	if !isMech || !using {
		// no mech target: release a charge that is building (it fires along the aim)
		return false
	}
	if w.Cooldown > 0.25 {
		return false
	}
	charging := w.Charge > 0.001
	if !charging {
		if dist < 25 || dist > laserFireRange {
			return false
		}
		if b.recover && b.hpFrac < 0.35 {
			return false
		}
		return true
	}
	// charging: release when ready and aligned on a clear line
	tol := clamp(tolBase*0.8, 0.008, 0.07)
	if w.Charge >= full-0.04 {
		if losOK && angErr < tol && dist <= laserFireRange+15 {
			return false // let go: fires now
		}
		if w.Charge >= full+b.holdS-0.45 {
			return false // about to auto-fire
		}
		return true // keep holding at full charge
	}
	// partial charge: if hit chance is poor and I'm in danger, still keep building
	return true
}

// lookAim returns a torso aim for when there is nothing to shoot at: face threats, else scan.
func (b *Brain) lookAim() [2]float64 {
	if len(b.ghosts) > 0 {
		g := b.ghosts[len(b.ghosts)-1]
		return [2]float64{g.Bearing, 0}
	}
	if len(b.threats) > 0 {
		e := b.threats[0]
		p := e.predict(0.3, b.t)
		return [2]float64{angleTo(b.px, b.py, p[0], p[1]), 0}
	}
	// scan around the direction of travel
	base := b.lastAct.Move
	yaw := b.me.Aim[0]
	if math.Hypot(base[0], base[1]) > 0.2 {
		yaw = math.Atan2(base[1], base[0])
	}
	return [2]float64{yaw + 2.2*math.Sin(1.15*b.t), 0}
}
