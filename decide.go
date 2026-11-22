package main

import (
	"fmt"
	"math"
	"time"
)

// ---------------------------------------------------------------- zone

type zoneInfo struct {
	cx, cy, r float64 // circle we want to be inside (current or the next one)
	safe      float64 // usable radius with margin
	inside    bool    // we are inside the safe part
	dist      float64
	endgame   bool
}

func (b *Brain) zoneState(o *Obs) zoneInfo {
	z := o.Zone
	// Steer by the NEXT circle (always entirely inside the current one) once the shrink is near;
	// the lead time grows with how far we still have to walk to get inside it.
	cx, cy, r := z.Center[0], z.Center[1], z.Radius
	nc, nr := z.NextCenter, z.NextRadius
	if nr <= 0 {
		nr = 12
	}
	outside := math.Hypot(o.Self.Pos[0]-nc[0], o.Self.Pos[1]-nc[1]) - nr
	lead := 30.0 + fmax(outside, 0)/8
	if nr <= z.Radius && o.T >= z.ShrinkStart-lead {
		cx, cy, r = nc[0], nc[1], nr
	}
	margin := 16.0
	if r < 120 {
		margin = 8
	}
	safe := fmax(r-margin, 6)
	d := math.Hypot(o.Self.Pos[0]-cx, o.Self.Pos[1]-cy)
	return zoneInfo{cx: cx, cy: cy, r: r, safe: safe, inside: d < safe && o.Self.InZone, dist: d, endgame: r <= 100}
}

func (zi zoneInfo) contains(x, y, extra float64) bool {
	return math.Hypot(x-zi.cx, y-zi.cy) < zi.safe-extra
}

// holdSpot picks a covered cell near the center of the final zone.
func (b *Brain) holdSpot(zi zoneInfo, o *Obs) (float64, float64, bool) {
	key := [3]float64{math.Round(zi.cx), math.Round(zi.cy), math.Round(zi.r)}
	if b.spotOK && key == b.spotKey {
		return b.spot[0], b.spot[1], true
	}
	if b.nav == nil {
		return zi.cx, zi.cy, false
	}
	best, bx, by := 1e9, zi.cx, zi.cy
	rr := fmax(zi.safe*0.7, 8)
	for r := 0.0; r <= rr; r += 6 {
		if b.left() < 14*time.Millisecond {
			break
		}
		n := 1
		if r > 0 {
			n = int(2 * math.Pi * r / 6)
		}
		for k := 0; k < n; k++ {
			a := 2 * math.Pi * float64(k) / float64(n)
			x, y := zi.cx+r*math.Cos(a), zi.cy+r*math.Sin(a)
			if !b.w.inBounds(x, y) || !b.nav.isFree(x, y) {
				continue
			}
			z := b.w.groundZ(x, y) + 5
			open := 0
			for d := 0; d < 12; d++ {
				ang := float64(d) * math.Pi / 6
				if !b.w.segBlocked([3]float64{x, y, z}, [3]float64{x + 70*math.Cos(ang), y + 70*math.Sin(ang), z}) {
					open++
				}
			}
			score := float64(open)*6 + r*0.25
			if score < best {
				best, bx, by = score, x, y
			}
		}
	}
	b.spotKey, b.spot, b.spotOK = key, [2]float64{bx, by}, true
	return bx, by, true
}

// ---------------------------------------------------------------- targets

func (b *Brain) buildTargets(o *Obs) []*target {
	var out []*target
	mz := b.muzzle(o)
	for _, tr := range b.tracks {
		if tr.seen != o.T || tr.hp <= 0 {
			continue
		}
		c := centerOf(tr)
		d := dist3(mz, c)
		t := &target{tr: tr, d: d, center: c, loot: tr.kind == "npc"}
		t.los = b.visible(mz, c)
		out = append(out, t)
	}
	return out
}

func (b *Brain) attackedRecently(window float64) bool {
	return b.t-b.lastDmgT < window
}

func (b *Brain) fightAllowed(o *Obs, t *target) bool {
	if t.tr.kind == "npc" {
		return true
	}
	if b.attackedRecently(6) {
		return true
	}
	if t.d < 75 {
		return true
	}
	if o.T > 110 {
		return true
	}
	if t.tr.maxHP > 0 && t.tr.hp/t.tr.maxHP < 0.35 && t.d < 170 {
		return true
	}
	return false
}

func (b *Brain) pickTarget(o *Obs, ts []*target) *target {
	var best *target
	for _, t := range ts {
		if t.tr.kind != "mech" || !t.los || t.d > 250 {
			continue
		}
		hpf := 1.0
		if t.tr.maxHP > 0 {
			hpf = t.tr.hp / t.tr.maxHP
		}
		s := -t.d*0.03 - hpf*5 + 0
		if t.tr.hp < 450 {
			s += 4
		}
		if t.d < 150 {
			s += 2
		}
		if t.tr.charging {
			s += 1
		}
		// someone who attacked us
		for _, r := range b.dmg {
			if r.by == t.tr.id {
				s += 3
				break
			}
		}
		// stationary targets are easier
		if math.Hypot(t.tr.vel[0], t.tr.vel[1]) < 2 {
			s += 1.5
		}
		t.score = s
		if best == nil || s > best.score {
			best = t
		}
	}
	return best
}

// lootTarget: drone or crate worth shooting right now.
func (b *Brain) lootTarget(o *Obs, ts []*target) *target {
	mz := b.muzzle(o)
	var best *target
	for _, t := range ts {
		if t.tr.kind == "npc" && t.los && t.d < 90 {
			if best == nil || t.d < best.d {
				best = t
			}
		}
	}
	if best != nil {
		return best
	}
	for _, c := range b.crates {
		if o.T-c.seen > 0.01 {
			continue // only crates in view right now
		}
		cc := [3]float64{c.pos[0], c.pos[1], c.pos[2] + 1.2}
		d := dist3(mz, cc)
		if d < 100 && b.visible(mz, cc) {
			if best == nil || d < best.d {
				tr := &track{id: c.id, kind: "crate", pos: c.pos, hp: c.hp, maxHP: 300, seen: o.T}
				best = &target{tr: tr, d: d, center: cc, los: true, loot: true}
			}
		}
	}
	return best
}

// ---------------------------------------------------------------- aiming

// laserMaxRange is the longest distance at which we release a charged laser shot (tunable).
const laserMaxRange = 110.0

func leadPoint(mz, tp, tv [3]float64, speed float64) [3]float64 {
	if speed <= 1 {
		return tp
	}
	t := dist3(mz, tp) / speed
	p := tp
	for i := 0; i < 3; i++ {
		p = [3]float64{tp[0] + tv[0]*t, tp[1] + tv[1]*t, tp[2] + tv[2]*t}
		t = dist3(mz, p) / speed
	}
	return p
}

// aimErr returns yaw and pitch error between the current aim and the direction to p.
func aimErr(cur [2]float64, mz, p [3]float64) (float64, float64, float64, float64) {
	yaw, pit := aimAt(mz, p)
	return yaw, pit, math.Abs(wrapAng(yaw - cur[0])), math.Abs(pit - cur[1])
}

func (b *Brain) aimAndFire(o *Obs, act *Action, tg *target) {
	mz := b.muzzle(o)
	cfg := b.cfg
	gi := b.slotOf(o, "gun")
	li := b.slotOf(o, "laser")
	lti := b.slotOf(o, "lightning")
	minP, maxP := cfg.Mech.MinPitch, cfg.Mech.MaxPitch
	if minP == 0 && maxP == 0 {
		minP, maxP = -0.7, 1.3
	}

	// laser state bookkeeping
	laserReady, laserCharging := false, false
	var lw *WeaponState
	if li >= 0 {
		lw = o.Self.Weapons[li]
		laserCharging = lw.Charge > 0
		laserReady = lw.Cooldown <= 0.06
	}
	laserFull := 0.75
	if lw != nil && lw.Stats.Charge > 0 {
		laserFull = lw.Stats.Charge
	}

	if tg == nil {
		// no target: finish a charge in progress after a short wait, scan otherwise
		if laserCharging {
			if b.laserSince == 0 {
				b.laserSince = o.T
			}
			if o.T-b.laserSince < 0.8 && lw.Charge < laserFull {
				act.Fire[li] = true
			} else if lw.Charge < laserFull {
				act.Fire[li] = false // release
			} else {
				act.Fire[li] = true // full: autofire after hold
			}
		} else {
			b.laserSince = 0
		}
		return
	}
	b.laserSince = 0

	scale := 1 + 0.05*float64(tg.tr.cubes)
	rad := 2.4 * scale
	if tg.tr.kind == "npc" {
		rad = 1.8
	} else if tg.tr.kind == "crate" {
		rad = 1.4
	}
	d := tg.d
	tolYaw := fmax(math.Atan2(rad*1.0, d), 0.012)
	tolPit := fmax(math.Atan2(rad*1.6, d), 0.02)

	tv := tg.tr.vel
	if tg.tr.kind == "crate" {
		tv = [3]float64{}
	}

	// gun aim
	gunSpeed := 252.0
	if gi >= 0 && o.Self.Weapons[gi].Stats.Speed > 0 {
		gunSpeed = o.Self.Weapons[gi].Stats.Speed
	}
	gp := leadPoint(mz, tg.center, tv, gunSpeed)
	gyaw, gpit, gey, gep := aimErr(o.Self.Aim, mz, gp)

	useLaser := false
	if li >= 0 && tg.tr.kind == "mech" && d < laserMaxRange {
		if laserCharging {
			useLaser = true
		} else if laserReady && (d > 40 || tg.tr.hp > 300) {
			useLaser = true
		}
	}

	if useLaser {
		// hitscan: lead only by the time to release (~0.08 s)
		lp := [3]float64{tg.center[0] + tv[0]*0.08, tg.center[1] + tv[1]*0.08, tg.center[2] + tv[2]*0.08}
		lyaw, lpit, ley, lep := aimErr(o.Self.Aim, mz, lp)
		charge := 0.0
		if lw != nil {
			charge = lw.Charge
		}
		if charge >= laserFull*0.55 || (charge > 0 && charge >= laserFull*0.3 && tg.tr.hp < 160) {
			// aim for the laser
			act.Aim = [2]float64{lyaw, clamp(lpit, minP, maxP)}
			aligned := ley < tolYaw*1.1 && lep < tolPit*1.2
			full := charge >= laserFull*0.97
			act.Fire[li] = true
			if aligned && (full || charge >= laserFull*0.6) {
				act.Fire[li] = false // release -> beam fires
			}
			if gi >= 0 {
				act.Fire[gi] = false
			}
			return
		}
		// charging up: keep aiming with the gun lead and hold the trigger
		act.Fire[li] = true
		_ = lyaw
		_ = lpit
		_ = ley
		_ = lep
	}

	act.Aim = [2]float64{gyaw, clamp(gpit, minP, maxP)}
	b.dbg = fmt.Sprintf("d=%.0f gey=%.3f/%.3f gep=%.3f/%.3f laser=%v", d, gey, tolYaw, gep, tolPit, useLaser)
	if gi >= 0 {
		gw := o.Self.Weapons[gi]
		if gw.Reload <= 0 && gw.Ammo > 0 && d < gw.Stats.Range*0.93 && gey < tolYaw && gep < tolPit {
			act.Fire[gi] = true
		}
		if gw.Ammo <= 0 && gw.Reload <= 0 {
			act.Reload = true
		}
	}
	if lti >= 0 && tg.tr.kind != "crate" && d < 40 {
		yawErr := math.Abs(wrapAng(gyaw - o.Self.Aim[0]))
		if yawErr < 0.35 && o.Self.Energy > 6 {
			act.Fire[lti] = true
		}
	}
}

// ---------------------------------------------------------------- weapons on the ground

func rankKind(k string) int {
	switch k {
	case "laser":
		return 3
	case "lightning":
		return 2
	}
	return 0
}

// wantedPickup returns the pickup we should walk to, if any.
func (b *Brain) wantedPickup(o *Obs, zi zoneInfo) *item {
	li := b.slotOf(o, "laser")
	if li >= 0 {
		return nil
	}
	me := o.Self.Pos
	empty := b.emptySlot(o)
	hasLight := b.slotOf(o, "lightning") >= 0
	var best *item
	bestS := 1e9
	for _, p := range b.pickups {
		if _, bad := b.badPick[p.id]; bad {
			continue
		}
		r := rankKind(p.kind)
		if r == 0 {
			continue
		}
		if b.w != nil && p.pos[2]-b.w.groundZ(p.pos[0], p.pos[1]) > 3 {
			continue // roof loot: not reachable on foot
		}
		d := dist2(p.pos, me)
		if r == 3 {
			if d > 320 {
				continue
			}
		} else { // lightning: only as a filler for an empty slot
			if empty < 0 || hasLight {
				continue
			}
			lim := 70.0
			if o.T > 45 {
				lim = 150
			}
			if d > lim {
				continue
			}
		}
		if !zi.contains(p.pos[0], p.pos[1], 0) && zi.r > 0 {
			continue
		}
		s := d
		if r == 3 {
			s -= 400
		}
		if s < bestS {
			best, bestS = p, s
		}
	}
	return best
}

func (b *Brain) takeIfNear(o *Obs, act *Action) {
	reach := b.cfg.Loot.PickupRadius
	if reach <= 0 {
		reach = 4
	}
	me := o.Self.Pos
	li := b.slotOf(o, "laser")
	for _, pk := range o.Pickups {
		if _, bad := b.badPick[pk.ID]; bad {
			continue
		}
		if dist2(pk.Pos, me) > reach-0.6 || math.Abs(pk.Pos[2]-me[2]) > 3.5 {
			continue
		}
		r := rankKind(pk.Kind)
		if r == 0 || li >= 0 {
			continue
		}
		id := pk.ID
		empty := b.emptySlot(o)
		if empty >= 0 {
			if r >= 2 {
				act.Take = &id
				return
			}
			continue
		}
		// full: swap in a laser over a non-gun weapon
		if r == 3 {
			for s := 0; s < 2; s++ {
				if o.Self.Weapons[s] != nil && o.Self.Weapons[s].Kind != "gun" {
					ds := s
					act.Take = &id
					act.Drop = &ds
					return
				}
			}
		}
	}
}

// ---------------------------------------------------------------- movement

// pathStep returns a unit-ish move direction toward (gx,gy) along a walkable path.
func (b *Brain) pathStep(o *Obs, gx, gy float64) (mx, my, remain float64, ok bool) {
	me := o.Self.Pos
	dx, dy := gx-me[0], gy-me[1]
	straight := math.Hypot(dx, dy)
	if b.nav == nil {
		ux, uy := unit2(dx, dy)
		return ux, uy, straight, true
	}
	if b.replan || len(b.path) == 0 || math.Hypot(gx-b.goal[0], gy-b.goal[1]) > 6 || o.T-b.pathT > 3 {
		b.replan = false
		b.goal = [2]float64{gx, gy}
		b.pathT = o.T
		b.path = b.nav.findPath(me[0], me[1], gx, gy, b.pathBudget())
		b.pi = 0
		if len(b.path) == 0 {
			b.pathFails++
			ux, uy := unit2(dx, dy)
			return ux, uy, straight, false
		}
		b.pathFails = 0
	}
	// advance index
	for b.pi < len(b.path)-1 && math.Hypot(b.path[b.pi][0]-me[0], b.path[b.pi][1]-me[1]) < 3.2 {
		b.pi++
	}
	last := len(b.path) - 1
	tgt := b.path[b.pi]
	hi := imin(last, b.pi+30)
	for k := hi; k > b.pi; k-- {
		if b.nav.lineFree(me[0], me[1], b.path[k][0], b.path[k][1]) {
			tgt = b.path[k]
			break
		}
	}
	// remaining length (approx)
	rem := 0.0
	px, py := me[0], me[1]
	for k := b.pi; k <= last; k++ {
		rem += math.Hypot(b.path[k][0]-px, b.path[k][1]-py)
		px, py = b.path[k][0], b.path[k][1]
	}
	ux, uy := unit2(tgt[0]-me[0], tgt[1]-me[1])
	return ux, uy, rem, true
}

func (b *Brain) wantBoost(o *Obs, travel bool, urgent bool) bool {
	en := o.Self.Energy
	if b.boosting {
		if en < 14 {
			b.boosting = false
		}
	} else if en > 65 || (urgent && en > 30) {
		b.boosting = true
	}
	return b.boosting && (travel || urgent)
}

// threatsNear: recent enemy mech tracks sorted by nothing in particular.
func (b *Brain) threats(o *Obs, maxAge float64) []*track {
	var out []*track
	for _, tr := range b.tracks {
		if tr.kind == "mech" && o.T-tr.seen <= maxAge && tr.hp > 0 {
			out = append(out, tr)
		}
	}
	return out
}

// exposedTo counts known enemies that have a clear line to the point.
func (b *Brain) exposedAt(x, y, z float64, ths []*track) int {
	n := 0
	for _, tr := range ths {
		eye := [3]float64{tr.pos[0], tr.pos[1], tr.pos[2] + 8}
		if hyp2(tr.pos[0]-x, tr.pos[1]-y) > 230 {
			continue
		}
		if b.visible(eye, [3]float64{x, y, z + 4.5}) || b.visible(eye, [3]float64{x, y, z + 8}) {
			n++
		}
	}
	return n
}

func (b *Brain) findCover(o *Obs, zi zoneInfo, ths []*track) (float64, float64, bool) {
	if b.nav == nil || len(ths) == 0 {
		return 0, 0, false
	}
	me := o.Self.Pos
	bestS, bx, by := 1e9, 0.0, 0.0
	found := false
	for r := 8.0; r <= 64; r += 8 {
		if b.left() < 10*time.Millisecond {
			break
		}
		n := int(2 * math.Pi * r / 9)
		off := b.rng.Float64() * 6
		for k := 0; k < n; k++ {
			a := 2*math.Pi*float64(k)/float64(n) + off
			x, y := me[0]+r*math.Cos(a), me[1]+r*math.Sin(a)
			if !b.w.inBounds(x, y) || !b.nav.isFree(x, y) || !zi.contains(x, y, 0) {
				continue
			}
			z := b.w.groundZ(x, y)
			if b.exposedAt(x, y, z, ths) > 0 {
				continue
			}
			s := r
			for _, tr := range ths {
				if hyp2(tr.pos[0]-x, tr.pos[1]-y) < 50 {
					s += 60
				}
			}
			if s < bestS {
				bestS, bx, by, found = s, x, y, true
			}
		}
		if found && r >= 24 && bestS < r {
			break
		}
	}
	return bx, by, found
}

type moveDecision struct {
	mx, my float64
	boost  bool
	jump   bool
}

func (b *Brain) strafeMove(o *Obs, tg *target, lo, hi float64) (float64, float64) {
	me := o.Self.Pos
	dx, dy := tg.center[0]-me[0], tg.center[1]-me[1]
	d := math.Hypot(dx, dy)
	ux, uy := unit2(dx, dy)
	rad := 0.0
	if d < lo {
		rad = -1
	} else if d > hi {
		rad = 0.8
	} else if d < (lo+hi)/2 {
		rad = -0.25
	}
	if o.T > b.strafeUntil {
		if b.rng.Float64() < 0.5 {
			b.strafe = -b.strafe
		}
		if b.strafe == 0 {
			b.strafe = 1
		}
		b.strafeUntil = o.T + 0.5 + b.rng.Float64()*0.9
	}
	try := func(sg, rd float64) (float64, float64, bool) {
		mx := ux*rd + (-uy)*sg
		my := uy*rd + ux*sg
		mx, my = unit2(mx, my)
		if b.nav == nil {
			return mx, my, true
		}
		for _, s := range []float64{4, 8} {
			if !b.nav.isFree(me[0]+mx*s, me[1]+my*s) {
				return mx, my, false
			}
		}
		return mx, my, true
	}
	if mx, my, ok := try(b.strafe, rad); ok {
		return mx, my
	}
	b.strafe = -b.strafe
	b.strafeUntil = o.T + 0.8
	if mx, my, ok := try(b.strafe, rad); ok {
		return mx, my
	}
	if mx, my, ok := try(0, rad); ok {
		return mx, my
	}
	if mx, my, ok := try(0, -rad); ok {
		return mx, my
	}
	return 0, 0
}

// stuck detection: returns true if we should override with an unstick move.
func (b *Brain) checkStuck(o *Obs, wantMove bool) {
	me := o.Self.Pos
	if b.refT == 0 {
		b.refT, b.refX, b.refY = o.T, me[0], me[1]
		return
	}
	if o.T-b.refT >= 1.2 {
		moved := math.Hypot(me[0]-b.refX, me[1]-b.refY)
		if wantMove && moved < 2.0 {
			b.stuckN++
			b.replan = true
			if b.nav != nil {
				ux, uy := unit2(b.lastAct.Move[0], b.lastAct.Move[1])
				for k := 1; k <= 2; k++ {
					b.nav.pen[b.nav.idx(me[0]+ux*3*float64(k), me[1]+uy*3*float64(k))] += 25
				}
			}
			if b.stuckN >= 2 {
				a := b.rng.Float64() * 2 * math.Pi
				b.unstickDir = [2]float64{math.Cos(a), math.Sin(a)}
				b.unstickUntil = o.T + 1.0
			}
		} else if moved > 6 {
			b.stuckN = 0
		}
		b.refT, b.refX, b.refY = o.T, me[0], me[1]
	}
}

func (b *Brain) pathBudget() time.Duration {
	l := b.left() - 8*time.Millisecond
	if l > 9*time.Millisecond {
		l = 9 * time.Millisecond
	}
	if l < 1*time.Millisecond {
		l = 1 * time.Millisecond
	}
	return l
}

// zoneClamp removes any outward part of a move once we are near the edge of the circle we must stay in.
func (b *Brain) zoneClamp(o *Obs, zi zoneInfo, mx, my float64) (float64, float64) {
	me := o.Self.Pos
	ox, oy := me[0]-zi.cx, me[1]-zi.cy
	d := math.Hypot(ox, oy)
	if d < zi.safe-22 || d < 1e-6 {
		return mx, my
	}
	ox, oy = ox/d, oy/d
	if comp := mx*ox + my*oy; comp > 0 {
		mx -= comp * ox
		my -= comp * oy
	}
	if d > zi.safe {
		k := clamp((d-zi.safe)/10, 0.3, 1)
		mx, my = unit2(mx-ox*k, my-oy*k)
	}
	return mx, my
}
