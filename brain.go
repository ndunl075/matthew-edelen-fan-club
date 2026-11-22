package main

import (
	"fmt"
	"math"
	"time"
)

var upgradeOrder = []string{
	"plating", "armor", "nanites", "gun_damage", "gun_rate", "laser_damage", "laser_charge",
	"lightning_efficiency", "servos", "gyros", "gun_velocity", "gun_mag", "reactor", "capacitor",
	"jets", "lightning_chain", "lightning_range", "mortar_damage", "sensors", "laser_pierce",
}

func (b *Brain) handleOffer(a *Action) {
	o := b.obs.Offer
	if o == nil || len(o.Choices) == 0 {
		return
	}
	best, bi := 1000, 0
	for i, c := range o.Choices {
		rank := 500
		for j, id := range upgradeOrder {
			if id == c.ID {
				rank = j
				break
			}
		}
		if rank < best {
			best, bi = rank, i
		}
	}
	v := bi
	a.Pick = &v
}

// Decide is called for every observation.
func (b *Brain) Decide(o *Obs, start time.Time) Action {
	b.start = start
	b.tSpot, b.tPath = 0, 0
	b.deadline = start.Add(24 * time.Millisecond)
	b.ingest(o)
	var a Action
	a.Tick = o.Tick
	b.handleOffer(&a)
	if b.W == nil || b.Nav == nil {
		return b.noMap(&a)
	}
	b.computeThreats()
	b.buildItems()
	b.chooseTarget()
	b.updateMode()
	b.movement(&a)
	b.aimAndFire(&a)
	b.lootTake(&a)
	if a.Debug == nil {
		a.Debug = &Debug{Label: b.mode}
	}
	b.lastAct = a
	b.trace(&a)
	return a
}

// trace writes one line per second of match time to stderr (never read in a real match).
func (b *Brain) trace(a *Action) {
	sec := int(b.t)
	if sec == b.lastTrace {
		return
	}
	b.lastTrace = sec
	near, los := -1.0, false
	if len(b.threats) > 0 {
		near, los = b.threats[0].Dist, b.threats[0].LOS
	}
	ws := ""
	for _, w := range b.me.Weapons {
		if w != nil {
			ws += w.Kind[:2]
		} else {
			ws += "--"
		}
	}
	tg := -1
	if b.target != nil {
		tg = b.target.ID
	}
	pu := ""
	for _, p := range b.pickups {
		pu += fmt.Sprintf("%c%.0f%s ", p.Kind[0], hypot2(b.px, b.py, p.Pos[0], p.Pos[1]), map[bool]string{true: "", false: "^"}[b.groundOK(p.Pos)])
	}
	logf("T %3d %-5s hp=%4.0f/%4.0f pos=(%4.0f,%4.0f) thr=%d near=%.0f los=%v tgt=%d spot=(%.0f,%.0f) w=%s cu=%d al=%d pk=%d lootkind=%s mv=(%.1f,%.1f) en=%.0f fire=%v hos=%d pu=%s",
		sec, b.mode, b.me.HP, b.me.MaxHP, b.px, b.py, len(b.threats), near, los, tg, b.spotX, b.spotY, ws,
		b.me.Cubes, b.obs.Alive, len(b.pickups), curLoot.kind, a.Move[0], a.Move[1], b.me.Energy, a.Fire, len(b.hostiles), pu)
}

func (b *Brain) noMap(a *Action) Action {
	// degraded mode: walk toward the zone centre, shoot what we see
	cx, cy := b.obs.Zone.Center[0], b.obs.Zone.Center[1]
	d := math.Hypot(cx-b.px, cy-b.py)
	if d > 30 {
		a.Move = [2]float64{(cx - b.px) / d, (cy - b.py) / d}
	}
	b.chooseTargetSimple(a)
	b.lastAct = *a
	return *a
}

func (b *Brain) chooseTargetSimple(a *Action) {
	var best *Enemy
	for _, e := range b.enemies {
		if e.InObs && (best == nil || e.Dist < best.Dist) {
			best = e
		}
	}
	if best == nil {
		return
	}
	m := b.muzzle()
	pt := [3]float64{best.Pos[0], best.Pos[1], best.Pos[2] + 4.5}
	aim, _ := intercept(m, pt, best.Vel, 252)
	y, p := aimAngles(m, aim)
	a.Aim = [2]float64{y, clamp(p, -0.7, 1.3)}
	a.Fire[0] = true
}

func (b *Brain) updateMode() {
	nVis, nNear := 0, 0
	for _, e := range b.hostiles {
		if e.LOS && b.t-e.LastSeen < 1.5 && e.Dist < 210 {
			nVis++
		}
		if e.Dist < 90 && b.t-e.LastSeen < 3 {
			nNear++
		}
	}
	if nVis > 0 {
		b.lastEnemyLOS = b.t
	}
	// recovery hysteresis
	threatNow := len(b.hostiles) > 0 && b.hostiles[0].Dist < 200 && b.t-b.hostiles[0].LastSeen < 1.0 || b.t-b.lastDamage < 3
	finish := b.target != nil && b.target.HP < 0.6*b.obs.Self.HP && b.t-b.target.LastSeen < 0.3 && nVis <= 1
	if b.hpFrac < 0.38 && threatNow && !finish {
		b.recover = true
	}
	if b.hpFrac < 0.65 && nVis >= 2 {
		b.recover = true
	}
	if b.recover && b.hpFrac > 0.80 {
		b.recover = false
	}
	b.mode = "loot"
	switch {
	case b.recover && (len(b.hostiles) > 0 || len(b.ghosts) > 0):
		b.mode = "evade"
	case b.recover:
		b.mode = "heal"
	case nVis >= 2 && nNear >= 1:
		b.mode = "evade"
	case b.target != nil && b.mayFire():
		b.mode = "fight"
		// a long-range duel does not stop us collecting cubes (aim and fire run regardless)
		if b.target.Dist > 70 && b.hpFrac > 0.5 && b.cubeNear(90) {
			b.mode = "loot"
		}
	}
	// the zone beats everything: outside the hard circle we walk back in
	cx, cy, r := b.hardCircle()
	if out := math.Hypot(b.px-cx, b.py-cy) - r; out > 1 {
		// Walking back in beats everything, unless hostiles are close and the zone still hurts little:
		// then the cover/kiting spot search (which only picks inside spots) pulls us in step by step.
		nearHost := len(b.hostiles) > 0 && b.hostiles[0].Dist < 90 && b.t-b.hostiles[0].LastSeen < 3
		if !nearHost || out > 12 || b.obs.Zone.DPS >= 30 {
			b.mode = "zone"
		}
	}
}

// ---- movement ----

func (b *Brain) followTo(gx, gy float64) (dx, dy, remain float64) {
	pg := b.pathGoal
	if len(b.path) == 0 || math.Hypot(gx-pg[0], gy-pg[1]) > 8 || b.t-b.pathAt > 1.6 ||
		b.wp >= len(b.path) && math.Hypot(gx-b.px, gy-b.py) > 6 {
		tp := time.Now()
		b.path = b.Nav.Path(b.px, b.py, gx, gy, b.deadline.Add(-10*time.Millisecond), b.avoidCost())
		b.tPath += time.Since(tp)
		b.wp = 0
		b.pathAt = b.t
		b.pathGoal = [2]float64{gx, gy}
	}
	for b.wp < len(b.path) {
		w := b.path[b.wp]
		if math.Hypot(w[0]-b.px, w[1]-b.py) < 3.5 {
			b.wp++
			continue
		}
		if b.wp+1 < len(b.path) {
			n := b.path[b.wp+1]
			if b.W.WalkClear(b.px, b.py, n[0], n[1], 2.3) {
				b.wp++
				continue
			}
		}
		break
	}
	tx, ty := gx, gy
	if b.wp < len(b.path) {
		tx, ty = b.path[b.wp][0], b.path[b.wp][1]
	}
	d := math.Hypot(tx-b.px, ty-b.py)
	remain = math.Hypot(gx-b.px, gy-b.py)
	if d < 0.5 {
		return 0, 0, remain
	}
	return (tx - b.px) / d, (ty - b.py) / d, remain
}

func (b *Brain) feasible(dx, dy float64) (float64, float64, bool) {
	if dx == 0 && dy == 0 {
		return 0, 0, true
	}
	base := math.Atan2(dy, dx)
	for _, off := range []float64{0, 0.35, -0.35, 0.7, -0.7, 1.1, -1.1, 1.6, -1.6} {
		a := base + off
		ux, uy := math.Cos(a), math.Sin(a)
		if b.W.WalkClear(b.px, b.py, b.px+ux*7, b.py+uy*7, 2.0) && b.zoneOK(b.px+ux*7, b.py+uy*7) {
			return ux, uy, true
		}
	}
	return 0, 0, false
}

func (b *Brain) strafeDir(tx, ty float64) (float64, float64) {
	if b.t >= b.strafeEnd {
		if b.rng.Float64() < 0.5 {
			b.strafeSign = -b.strafeSign
		}
		b.strafeEnd = b.t + 0.5 + b.rng.Float64()*1.1
	}
	ang := angleTo(b.px, b.py, tx, ty) + b.strafeSign*math.Pi/2
	ux, uy := math.Cos(ang), math.Sin(ang)
	if !b.W.WalkClear(b.px, b.py, b.px+ux*7, b.py+uy*7, 2.0) {
		b.strafeSign = -b.strafeSign
		ux, uy = -ux, -uy
		if !b.W.WalkClear(b.px, b.py, b.px+ux*7, b.py+uy*7, 2.0) {
			return 0, 0
		}
	}
	return ux, uy
}

// fight band (metres to the target)
const (
	fightLo = 38.0
	fightHi = 68.0
)

func (b *Brain) fightParams() spotParams {
	sp := spotParams{wPath: 0.022, wThreat: 1.7, wBand: 1.0, wCover: 0.3, wZone: 1.0, wTLOS: 0.9,
		wApproach: 0.5, maxDist: 60, wantTLOS: true, wRing: 0.9}
	// closer than the old 65-105 m: our gun hits about 85% at 30-60 m and the laser can still reach
	if b.laserLess() {
		sp.bandLo, sp.bandHi = fightLo, fightHi
	} else {
		sp.bandLo, sp.bandHi = fightLo, fightHi+10
	}
	if b.fireReady() {
		sp.tgtW, sp.wTLOS = 0.25, 1.8
	} else {
		sp.tgtW, sp.wTLOS = 1.6, 0
	}
	if b.hpFrac < 0.7 {
		sp.bandLo += 25
		sp.bandHi += 25
		sp.wThreat += 0.8
	}
	return sp
}

func (b *Brain) evadeParams() spotParams {
	sp := spotParams{wPath: 0.03, wThreat: 3.2, wBand: 0.15, wCover: 0.8, wZone: 1.2, wTLOS: 0,
		wApproach: 0.8, maxDist: 75, bandLo: 150, bandHi: 400, wRing: 1.6, farMax: 150}
	return sp
}

func (b *Brain) holdParams() spotParams {
	cx, cy, _ := b.safeCircle()
	return spotParams{wPath: 0.02, wThreat: 2.4, wBand: 0.8, wCover: 1.1, wZone: 1.2, wApproach: 0.6,
		maxDist: 70, wToward: 0.6, towardX: cx, towardY: cy, bandLo: 135, bandHi: 400, wRing: 1.4, farMax: 260}
}

func (b *Brain) movement(a *Action) {
	var dx, dy float64
	speed := 1.0
	hold := false
	boost := false
	jump := false
	var tgx, tgy float64 // direction toward the nearest threat (for strafing)
	haveThreat := false
	if len(b.hostiles) > 0 {
		p := b.hostiles[0].predict(0.2, b.t)
		tgx, tgy = p[0], p[1]
		haveThreat = true
	} else if len(b.ghosts) > 0 {
		g := b.ghostPos(b.ghosts[len(b.ghosts)-1], 60)
		tgx, tgy = g[0], g[1]
		haveThreat = true
	}
	switch b.mode {
	case "fight", "evade", "hold", "heal":
		var sp spotParams
		switch b.mode {
		case "fight":
			sp = b.fightParams()
		case "evade":
			sp = b.evadeParams()
		default:
			sp = b.holdParams()
		}
		chasing := false
		if b.mode == "fight" && b.target != nil && b.target.Dist > sp.bandHi+15 && b.zonePen(b.target.Pos[0], b.target.Pos[1]) == 0 {
			tp := b.target.predict(0.3, b.t)
			dx, dy, _ = b.followTo(tp[0], tp[1])
			chasing = true
		}
		if chasing {
			// close the distance on a target that is out of range
		} else if !b.spotValid || b.t-b.spotAt > 0.45 {
			ts := time.Now()
			x, y, s, ok := b.pickSpot(sp, b.spotValid, b.spotX, b.spotY)
			b.tSpot += time.Since(ts)
			if ok {
				b.spotX, b.spotY, b.spotScore, b.spotValid = x, y, s, true
				b.spotAt = b.t
			}
		}
		if b.spotValid && !chasing {
			var rem float64
			dx, dy, rem = b.followTo(b.spotX, b.spotY)
			if rem < 4 {
				dx, dy = 0, 0
				hold = true
			}
			if b.mode == "evade" && rem > 6 && b.me.Energy > 35 && b.lastEnemyLOS > b.t-2 {
				boost = true
			}
		}
		// strafe when something can see us or we are fighting
		if haveThreat && (b.mode == "fight" || b.exposedNow()) {
			sx, sy := b.strafeDir(tgx, tgy)
			if hold {
				dx, dy = sx*0.8, sy*0.8
				if b.mode != "fight" && !b.exposedNow() {
					dx, dy = 0, 0
				}
			} else {
				dx, dy = dx+sx*0.7, dy+sy*0.7
			}
		}
	case "zone":
		cx, cy, r := b.hardCircle()
		// aim for a point well inside the circle
		d := math.Hypot(b.px-cx, b.py-cy)
		k := 0.6
		if d < 1 {
			d = 1
		}
		gx := cx + (b.px-cx)/d*r*k
		gy := cy + (b.py-cy)/d*r*k
		var rem float64
		dx, dy, rem = b.followTo(gx, gy)
		_ = rem
		if math.Hypot(b.px-cx, b.py-cy) > r+25 && b.me.Energy > 30 {
			boost = true
		}
		b.spotValid = false
	default: // loot / explore
		b.spotValid = false
		gx, gy, ok := b.lootGoal()
		if ok {
			var rem float64
			dx, dy, rem = b.followTo(gx, gy)
			if rem < 1.5 {
				dx, dy = 0, 0
			}
			if b.stopToShoot() {
				dx, dy = 0, 0
			}
		}
	}
	// incoming fire
	ddx, ddy := b.dodge()
	if ddx != 0 || ddy != 0 {
		dx, dy = dx+ddx*1.6, dy+ddy*1.6
		if b.me.Energy > 25 {
			boost = b.mode == "evade" || b.mode == "fight" && b.hpFrac < 0.6
		}
	}
	// stuck handling
	if b.t < b.stuckUntil {
		dx, dy = b.stuckDir[0], b.stuckDir[1]
		jump = b.t < b.stuckUntil-0.4
	} else if b.isStuck(dx, dy) {
		ang := b.rng.Float64() * 2 * math.Pi
		b.stuckDir = [2]float64{math.Cos(ang), math.Sin(ang)}
		if ux, uy, ok := b.feasible(b.stuckDir[0], b.stuckDir[1]); ok {
			b.stuckDir = [2]float64{ux, uy}
		}
		b.stuckUntil = b.t + 0.9
		b.path = nil
		dx, dy = b.stuckDir[0], b.stuckDir[1]
		jump = true
	}
	m := math.Hypot(dx, dy)
	if m > 1 {
		dx, dy = dx/m, dy/m
		m = 1
	}
	if m > 0.05 {
		if b.t >= b.stuckUntil {
			if ux, uy, ok := b.feasible(dx, dy); ok {
				dx, dy = ux*m, uy*m
			} else {
				dx, dy = 0, 0
			}
		}
	}
	a.Move = [2]float64{dx * speed, dy * speed}
	a.Boost = boost
	a.Jump = jump
}

func (b *Brain) exposedNow() bool {
	for _, e := range b.hostiles {
		if e.LOS && b.t-e.LastSeen < 2 && e.Dist < 220 {
			return true
		}
	}
	return len(b.ghosts) > 0
}

func (b *Brain) isStuck(dx, dy float64) bool {
	if math.Hypot(dx, dy) < 0.4 || len(b.hist) < 8 {
		return false
	}
	first := b.hist[0]
	if b.t-first.t < 1.6 {
		return false
	}
	return math.Hypot(b.px-first.x, b.py-first.y) < 1.2
}

// dodge returns a sideways push away from incoming bullets and charging lasers aimed at us.
func (b *Brain) dodge() (float64, float64) {
	var sx, sy float64
	for _, p := range b.obs.Projectiles {
		rx, ry := b.px-p.Pos[0], b.py-p.Pos[1]
		vx, vy := p.Vel[0], p.Vel[1]
		v2 := vx*vx + vy*vy
		if v2 < 1 {
			continue
		}
		tc := (rx*vx + ry*vy) / v2
		if tc < 0 || tc > 0.55 {
			continue
		}
		cx, cy := p.Pos[0]+vx*tc-b.px, p.Pos[1]+vy*tc-b.py
		if math.Hypot(cx, cy) > 4.2 {
			continue
		}
		// push perpendicular to the bullet, away from where it will pass
		px, py := -vy, vx
		l := math.Hypot(px, py)
		px, py = px/l, py/l
		if px*cx+py*cy > 0 {
			px, py = -px, -py
		}
		sx += px
		sy += py
	}
	for _, e := range b.threats {
		if !e.Charging || !e.LOS || b.t-e.LastSeen > 0.5 {
			continue
		}
		bear := angleTo(e.Pos[0], e.Pos[1], b.px, b.py)
		diff := wrapPi(e.Aim[0] - bear)
		tol := math.Atan2(3.6, math.Max(e.Dist, 10)) + 0.06
		if math.Abs(diff) < tol {
			// step out of the line: perpendicular to the beam, toward the side we are already on
			ang := bear + math.Pi/2
			if diff > 0 {
				ang = bear - math.Pi/2
			} else if diff == 0 {
				ang = bear + math.Pi/2*b.strafeSign
			}
			sx += math.Cos(ang) * 1.5
			sy += math.Sin(ang) * 1.5
		}
	}
	m := math.Hypot(sx, sy)
	if m > 1 {
		sx, sy = sx/m, sy/m
	}
	return sx, sy
}

// avoidCost makes path nodes near hostile mechs expensive so loot runs go around them.
func (b *Brain) avoidCost() func(id int) float32 {
	type pt struct{ x, y, r float64 }
	var pts []pt
	for _, e := range b.threats {
		p := e.predict(0.5, b.t)
		if e.hostile(b.t) {
			pts = append(pts, pt{p[0], p[1], 110})
		} else if e.Dist < 80 {
			pts = append(pts, pt{p[0], p[1], 45})
		}
		if len(pts) >= 5 {
			break
		}
	}
	if len(pts) == 0 {
		return nil
	}
	return func(id int) float32 {
		x, y := b.Nav.Center(id%b.Nav.NA, id/b.Nav.NA)
		var m float32 = 1
		for _, p := range pts {
			d := math.Hypot(x-p.x, y-p.y)
			if d < p.r {
				m += float32(2.2 * (p.r - d) / p.r)
			}
		}
		return m
	}
}

// fireReady: the main weapon can fire now (laser charged, or gun loaded) so peeking out is worth it.
func (b *Brain) fireReady() bool {
	if ls := b.weaponSlot("laser"); ls >= 0 {
		w := b.me.Weapons[ls]
		full := w.Stats["charge"]
		if full == 0 {
			full = b.laserCharge
		}
		if w.Cooldown < 0.3 && w.Charge >= full-0.12 {
			return true
		}
	}
	if gs := b.weaponSlot("gun"); gs >= 0 {
		w := b.me.Weapons[gs]
		return w.Reload == 0 && w.Ammo >= 3
	}
	return true
}
