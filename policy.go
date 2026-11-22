package main

import (
	"fmt"
	"math"
)

func (b *Brain) dangerAt(o *Obs, x, y, r float64) bool {
	for _, tr := range b.tracks {
		if tr.kind != "mech" || o.T-tr.seen > 8 || tr.hp <= 0 {
			continue
		}
		if hyp2(tr.pos[0]-x, tr.pos[1]-y) < r {
			return true
		}
	}
	return false
}

// pickExplore chooses the nearest unvisited coarse cell inside the safe zone.
func (b *Brain) pickExplore(o *Obs, zi zoneInfo) (float64, float64, bool) {
	if b.visited == nil || b.nav == nil {
		return 0, 0, false
	}
	me := o.Self.Pos
	if b.exploreOK && !b.visited[b.vIndex(b.explore[0], b.explore[1])] &&
		zi.contains(b.explore[0], b.explore[1], 10) && o.T-b.visitT < 25 {
		return b.explore[0], b.explore[1], true
	}
	best, bx, by := 1e18, 0.0, 0.0
	found := false
	for pass := 0; pass < 2 && !found; pass++ {
		for j := 0; j < b.vny; j++ {
			for i := 0; i < b.vnx; i++ {
				if b.visited[j*b.vnx+i] {
					continue
				}
				x := b.w.minX + (float64(i)+0.5)*24
				y := b.w.minY + (float64(j)+0.5)*24
				if !zi.contains(x, y, 15) {
					continue
				}
				if !b.nav.isFree(x, y) {
					continue
				}
				if b.dangerAt(o, x, y, 80) {
					continue
				}
				d := math.Hypot(x-me[0], y-me[1])
				// small bias toward the zone center so we do not wander to the rim
				d += 0.15 * math.Hypot(x-zi.cx, y-zi.cy)
				if d < best {
					best, bx, by, found = d, x, y, true
				}
			}
		}
		if !found && pass == 0 {
			for k := range b.visited {
				b.visited[k] = false // everything seen: start over
			}
		}
	}
	if found {
		b.explore = [2]float64{bx, by}
		b.exploreOK = true
		b.visitT = o.T
	}
	return bx, by, found
}

func (b *Brain) vIndex(x, y float64) int {
	i, j := int((x-b.w.minX)/24), int((y-b.w.minY)/24)
	i = imin(imax(i, 0), b.vnx-1)
	j = imin(imax(j, 0), b.vny-1)
	return j*b.vnx + i
}

// chooseGoal picks where to walk when nothing urgent is going on.
func (b *Brain) chooseGoal(o *Obs, zi zoneInfo, hpf float64) (gx, gy, stop float64, label string, ok bool) {
	me := o.Self.Pos
	// 1. weapon on the ground (laser first)
	if p := b.wantedPickup(o, zi); p != nil && !b.dangerAt(o, p.pos[0], p.pos[1], 60) {
		return p.pos[0], p.pos[1], 0, "weapon:" + p.kind, true
	}
	// 2. supply drop
	if hpf > 0.5 && !zi.endgame || hpf > 0.5 && zi.endgame {
		var bd *Drop
		bestD := 1e9
		for i := range o.Drops {
			d := &o.Drops[i]
			dd := dist2(d.Pos, me)
			if dd > 340 {
				continue
			}
			if d.Holder != nil && *d.Holder != o.Self.ID {
				continue
			}
			eta := dd / 11.0
			if d.LandsIn-eta > 12 {
				continue
			}
			if b.dangerAt(o, d.Pos[0], d.Pos[1], 55) {
				continue
			}
			if dd < bestD {
				bd, bestD = d, dd
			}
		}
		if bd != nil {
			st := 0.0
			if bestD < bd.Radius-3 {
				st = 1e9 // inside the ring: stand still
			}
			return bd.Pos[0], bd.Pos[1], st, "drop", true
		}
	}
	// 3. cubes
	if o.Self.Cubes < 24 {
		var bc *item
		bestD := 90.0
		for _, c := range b.cubeM {
			d := dist2(c.pos, me)
			if d < bestD && zi.contains(c.pos[0], c.pos[1], 0) && !b.dangerAt(o, c.pos[0], c.pos[1], 50) {
				bc, bestD = c, d
			}
		}
		if bc != nil {
			return bc.pos[0], bc.pos[1], 0, "cube", true
		}
		// 4. drones and crates: walk into gun range
		var bt *track
		bestD = 100
		for _, tr := range b.tracks {
			if tr.kind == "npc" && o.T-tr.seen < 6 {
				d := dist2(tr.pos, me)
				if d < bestD && zi.contains(tr.pos[0], tr.pos[1], 0) && !b.dangerAt(o, tr.pos[0], tr.pos[1], 60) {
					bt, bestD = tr, d
				}
			}
		}
		if bt != nil && hpf > 0.6 {
			return bt.pos[0], bt.pos[1], 38, "drone", true
		}
		var bcr *item
		bestD = 130
		for _, c := range b.crates {
			d := dist2(c.pos, me)
			if b.w != nil && c.pos[2]-b.w.groundZ(c.pos[0], c.pos[1]) > 3 {
				// roof crate: still shootable from the ground, stop at range
			}
			if d < bestD && zi.contains(c.pos[0], c.pos[1], 0) && !b.dangerAt(o, c.pos[0], c.pos[1], 60) {
				bcr, bestD = c, d
			}
		}
		if bcr != nil {
			return bcr.pos[0], bcr.pos[1], 40, "crate", true
		}
	}
	// 5. explore for loot while it is still early or we lack a laser
	if (b.slotOf(o, "laser") < 0 && o.T < 200) || o.T < 70 {
		if x, y, f := b.pickExplore(o, zi); f {
			return x, y, 10, "explore", true
		}
	}
	// 6. zone: go to / hold a covered spot
	if zi.endgame {
		if x, y, f := b.holdSpot(zi, o); f {
			return x, y, 4, "hold", true
		}
	}
	if !zi.inside {
		ux, uy := unit2(me[0]-zi.cx, me[1]-zi.cy)
		return zi.cx + ux*zi.safe*0.5, zi.cy + uy*zi.safe*0.5, 5, "zone", true
	}
	if x, y, f := b.holdSpot(zi, o); f && zi.r < 260 {
		return x, y, 6, "hold", true
	}
	if x, y, f := b.pickExplore(o, zi); f {
		return x, y, 10, "explore", true
	}
	return zi.cx, zi.cy, 6, "center", true
}

func (b *Brain) decide(o *Obs) *Action {
	b.update(o)
	act := &Action{Tick: o.Tick}
	act.Aim = o.Self.Aim
	me := o.Self.Pos
	b.pickOffer(o, act)
	b.takeIfNear(o, act)

	zi := b.zoneState(o)
	ts := b.buildTargets(o)
	tg := b.pickTarget(o, ts)
	fight := tg != nil && b.fightAllowed(o, tg)
	hpf := b.hpFrac(o)
	ths := b.threats(o, 6)
	practice := b.mode != 2 && o.T > 40

	// retreat flag
	engaged := len(ths) > 0 || b.attackedRecently(5)
	if engaged {
		b.holdSince = o.T
	}
	lo, hi := 0.50, 0.80
	if !b.retreat && hpf < lo && engaged {
		b.retreat = true
		b.coverT = -10
	}
	if b.retreat && (hpf > hi || (hpf > 0.65 && o.T-b.holdSince > 12)) {
		b.retreat = false
	}
	// visible enemies we may not fight yet: stay out of their sight
	avoid := false
	visCount := 0
	for _, t := range ts {
		if t.tr.kind == "mech" && t.los && t.d < 200 {
			visCount++
		}
	}
	if tg != nil && !fight {
		avoid = true
	}
	if visCount >= 3 {
		avoid = true
	}

	// first minute: keep away from every contact unless attacked
	early := false
	var nearest *track
	if o.T < 60 && !b.attackedRecently(6) {
		nd := 130.0
		for _, tr := range ths {
			if tr.seen > o.T-4 {
				if d := dist2(tr.pos, me); d < nd {
					nd, nearest = d, tr
				}
			}
		}
		early = nearest != nil
	}
	// shooting target
	var shoot *target
	if fight {
		shoot = tg
	} else if tg == nil && !b.attackedRecently(3) {
		shoot = b.lootTarget(o, ts)
	}

	var mv moveDecision
	label := ""
	fightMove := func() moveDecision {
		lo, hi := 70.0, 108.0
		if b.slotOf(o, "laser") < 0 {
			lo, hi = 58, 95
		}
		if practice {
			lo, hi = lo+12, hi+8
		}
		if tg.tr.maxHP > 0 && tg.tr.hp/tg.tr.maxHP < 0.3 && b.mode == 2 {
			lo, hi = 35, 90
		}
		mx, my := b.strafeMove(o, tg, lo, hi)
		if mx == 0 && my == 0 && tg.d > hi {
			mx, my, _, _ = b.pathStep(o, tg.tr.pos[0], tg.tr.pos[1])
		}
		if !zi.inside {
			ux, uy := unit2(zi.cx-me[0], zi.cy-me[1])
			mx, my = unit2(mx*0.5+ux*0.9, my*0.5+uy*0.9)
		}
		return moveDecision{mx: mx, my: my, boost: b.wantBoost(o, false, true)}
	}
	switch {
	case !o.Self.InZone || zi.dist > zi.r-12:
		ux, uy := unit2(zi.cx-me[0], zi.cy-me[1])
		mv = moveDecision{mx: ux, my: uy}
		tx, ty := zi.cx, zi.cy
		if b.nav != nil {
			mx, my, _, _ := b.pathStep(o, tx, ty)
			mv.mx, mv.my = mx, my
		}
		mv.boost = b.wantBoost(o, true, true)
		label = "zone!"
	case early:
		label = "evade"
		ex, ey := unit2(me[0]-nearest.pos[0], me[1]-nearest.pos[1])
		gx, gy := me[0]+ex*70, me[1]+ey*70
		if !zi.contains(gx, gy, 0) {
			ux, uy := unit2(zi.cx-me[0], zi.cy-me[1])
			gx, gy = me[0]+(ex*0.4+ux*0.8)*70, me[1]+(ey*0.4+uy*0.8)*70
		}
		mx, my, _, _ := b.pathStep(o, gx, gy)
		mv = moveDecision{mx: mx, my: my, boost: b.wantBoost(o, true, true)}
	case b.retreat || avoid:
		label = "cover"
		if b.retreat {
			label = "retreat"
		}
		exposed := b.exposedAt(me[0], me[1], me[2], ths) > 0 || b.attackedRecently(1.2)
		if exposed || !b.coverOK {
			if o.T-b.coverT > 1.2 {
				b.coverT = o.T
				cx, cy, f := b.findCover(o, zi, ths)
				b.coverOK = f
				if f {
					b.cover = [2]float64{cx, cy}
					b.replan = true
				}
			}
			if b.coverOK {
				mx, my, rem, _ := b.pathStep(o, b.cover[0], b.cover[1])
				if rem > 3 && (rem < 40 || tg == nil || !fight) {
					mv = moveDecision{mx: mx, my: my, boost: b.wantBoost(o, true, true)}
				} else if rem > 3 {
					label = "fight-cornered" // cover too far while being shot: strafe and shoot
					mv = fightMove()
				}
			} else if tg != nil {
				// nowhere to hide: back away from the target while shooting
				ux, uy := unit2(me[0]-tg.center[0], me[1]-tg.center[1])
				mv = moveDecision{mx: ux, my: uy}
			}
		} else {
			// hidden: hold still and regenerate (stay inside the zone)
			b.coverOK = true
			label += "-hold"
			if !zi.inside {
				mx, my, _, _ := b.pathStep(o, zi.cx, zi.cy)
				mv = moveDecision{mx: mx, my: my}
			}
		}
	case fight && tg != nil:
		label = "fight"
		mv = fightMove()
	default:
		gx, gy, stop, lb, ok := b.chooseGoal(o, zi, hpf)
		label = lb
		if ok {
			if stop >= 1e8 {
				// standing in the ring
			} else {
				mx, my, rem, _ := b.pathStep(o, gx, gy)
				dd := math.Hypot(gx-me[0], gy-me[1])
				if (lb == "hold" || lb == "center") && dd <= stop {
					mx, my = b.orbit(o, zi)
					rem = 0
					stop = -1
				}
				if dd > 3 && dd < 9 && (lb == "cube" || lb[:min(len(lb), 6)] == "weapon") {
					mx, my = unit2(gx-me[0], gy-me[1]) // final approach: straight in
				}
				if dd > stop || stop < 0 {
					mv = moveDecision{mx: mx, my: my}
					mv.boost = b.wantBoost(o, rem > 35 && len(ths) == 0, false)
				}
			}
		}
	}

	// stuck handling
	b.checkStuck(o, math.Hypot(mv.mx, mv.my) > 0.3)
	if b.unstickUntil > o.T {
		mv.mx, mv.my = b.unstickDir[0], b.unstickDir[1]
		mv.jump = true
		mv.boost = o.Self.Energy > 20
	}

	if label != "zone!" {
		mv.mx, mv.my = b.zoneClamp(o, zi, mv.mx, mv.my)
	}
	act.Move = [2]float64{mv.mx, mv.my}
	act.Boost = mv.boost
	act.Jump = mv.jump

	// aim and fire
	if shoot != nil || len(o.Self.Weapons) > 0 {
		b.aimAndFire(o, act, shoot)
	}
	if shoot == nil {
		// scan: toward a recent alert, else sweep
		if o.T-b.alertT < 1.5 {
			act.Aim = [2]float64{b.alertYaw, 0}
		} else {
			act.Aim = [2]float64{wrapAng(o.Self.Aim[0] + 0.5), 0}
		}
		if gi := b.slotOf(o, "gun"); gi >= 0 {
			gw := o.Self.Weapons[gi]
			if gw.Reload <= 0 && gw.Ammo < gw.Stats.Mag*0.6 && len(ths) == 0 {
				act.Reload = true
			}
		}
		// keep a charged laser from wasting itself
		if li := b.slotOf(o, "laser"); li >= 0 && o.Self.Weapons[li].Charge > 0 {
			// aimAndFire handled the hold/release for tg == nil
		}
	}
	if b.trace != nil {
		fmt.Fprintf(b.trace, "%.1f %s m%d hp%.0f pos(%.0f,%.0f) vis%d tg=%v fight=%v aim=%.2f,%.2f fire=%v mv=%.1f,%.1f %s\n", o.T, label, b.mode, o.Self.HP, me[0], me[1], visCount, tg != nil, fight, act.Aim[0], act.Aim[1], act.Fire, act.Move[0], act.Move[1], b.dbg)
	}
	b.label = label
	act.Debug = &Debug{Label: fmt.Sprintf("%s m%d hp%.0f", label, b.mode, o.Self.HP)}
	return act
}

// orbit keeps us moving around the middle of the circle instead of standing still.
func (b *Brain) orbit(o *Obs, zi zoneInfo) (float64, float64) {
	me := o.Self.Pos
	if o.T > b.strafeUntil {
		if b.rng.Float64() < 0.4 {
			b.strafe = -b.strafe
		}
		if b.strafe == 0 {
			b.strafe = 1
		}
		b.strafeUntil = o.T + 2 + b.rng.Float64()*3
	}
	rx, ry := me[0]-zi.cx, me[1]-zi.cy
	ux, uy := unit2(-ry, rx)
	for _, sg := range []float64{b.strafe, -b.strafe} {
		mx, my := ux*sg, uy*sg
		// drift toward 0.45 of the radius
		want := zi.safe * 0.45
		d := math.Hypot(rx, ry)
		cx, cy := unit2(zi.cx-me[0], zi.cy-me[1])
		pull := clamp((d-want)/40, -0.6, 0.6)
		mx, my = unit2(mx+cx*pull, my+cy*pull)
		if b.nav == nil || (b.nav.isFree(me[0]+mx*5, me[1]+my*5) && b.nav.isFree(me[0]+mx*10, me[1]+my*10)) {
			return mx * 0.6, my * 0.6
		}
		b.strafe = -b.strafe
	}
	return 0, 0
}
