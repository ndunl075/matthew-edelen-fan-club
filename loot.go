package main

import (
	"fmt"
	"math"
)

type lootGoalT struct {
	id   int
	kind string
	x, y float64
	set  bool
}

var curLoot lootGoalT
var exploreGoal [2]float64
var exploreAt = -100.0
var visited = map[int]bool{}

func visitKey(x, y float64) int {
	return int(math.Floor(x/30))*10007 + int(math.Floor(y/30))
}

// pickupValue: how much we want a weapon lying on the floor (0 = not at all).
func (b *Brain) pickupValue(kind string) float64 {
	slotKind := ""
	if len(b.me.Weapons) > 1 && b.me.Weapons[1] != nil {
		slotKind = b.me.Weapons[1].Kind
	}
	if b.weaponSlot(kind) >= 0 {
		return 0
	}
	switch kind {
	case "laser":
		return 100
	case "lightning":
		if slotKind == "" {
			return 42
		}
	case "mortar":
		if slotKind == "" && b.t > 90 {
			return 8
		}
	}
	return 0
}

func (b *Brain) groundOK(p [3]float64) bool {
	return p[2]-b.W.GroundZ(p[0], p[1]) < 3.2
}

func (b *Brain) dropOK(d *Drop) bool {
	if d.Holder != nil && *d.Holder != b.me.ID {
		return false
	}
	dist := hypot2(b.px, b.py, d.Pos[0], d.Pos[1])
	if dist > 230 {
		return false
	}
	travel := dist * 1.3 / 10
	if d.LandsIn-travel > 22 {
		return false
	}
	cx, cy, r := b.safeCircle()
	if math.Hypot(d.Pos[0]-cx, d.Pos[1]-cy) > r-5 {
		return false
	}
	for _, e := range b.hostiles {
		if hypot2(e.Pos[0], e.Pos[1], d.Pos[0], d.Pos[1]) < 60 && b.t-e.LastSeen < 8 {
			return false
		}
	}
	return true
}

func (b *Brain) lootGoal() (float64, float64, bool) {
	// remember where we have been
	for dx := -1; dx <= 1; dx++ {
		for dy := -1; dy <= 1; dy++ {
			visited[visitKey(b.px+float64(dx)*30, b.py+float64(dy)*30)] = true
		}
	}
	best := lootGoalT{}
	bv := -1e18
	consider := func(id int, kind string, x, y, v float64) {
		if kind == "drone" && b.ban[fmt.Sprintf("d%d", id)] > b.t || kind == "crate" && b.ban[fmt.Sprintf("c%d", id)] > b.t {
			return
		}
		for _, e := range b.hostiles {
			if hypot2(e.Pos[0], e.Pos[1], x, y) < 55 {
				return
			}
		}
		if curLoot.set && curLoot.id == id && curLoot.kind == kind {
			v += 12 // hysteresis
		}
		if v > bv {
			bv = v
			best = lootGoalT{id: id, kind: kind, x: x, y: y, set: true}
		}
	}
	for _, p := range b.pickups {
		if b.takeFail[p.ID] > b.t || !b.groundOK(p.Pos) {
			continue
		}
		v := b.pickupValue(p.Kind)
		if v <= 0 {
			continue
		}
		d := hypot2(b.px, b.py, p.Pos[0], p.Pos[1])
		if d > 330 {
			continue
		}
		cx, cy, r := b.safeCircle()
		if math.Hypot(p.Pos[0]-cx, p.Pos[1]-cy) > r+10 {
			continue
		}
		consider(p.ID, "pickup", p.Pos[0], p.Pos[1], v-d*0.22)
	}
	{
		for _, c := range b.cubes {
			if !b.groundOK(c.Pos) {
				continue
			}
			d := hypot2(b.px, b.py, c.Pos[0], c.Pos[1])
			if d > 130 {
				continue
			}
			consider(c.ID, "cube", c.Pos[0], c.Pos[1], 38-d*0.3)
		}
	}
	if b.t < 200 {
		for _, dr := range b.drones {
			if b.t-dr.LastSeen > 8 {
				continue
			}
			d := hypot2(b.px, b.py, dr.Pos[0], dr.Pos[1])
			if d > 150 {
				continue
			}
			consider(dr.ID, "drone", dr.Pos[0], dr.Pos[1], 34-d*0.25)
		}
		for _, c := range b.crates {
			d := hypot2(b.px, b.py, c.Pos[0], c.Pos[1])
			if d > 110 {
				continue
			}
			consider(c.ID, "crate", c.Pos[0], c.Pos[1], 26-d*0.28)
		}
	}
	for i := range b.obs.Drops {
		d := &b.obs.Drops[i]
		if !b.dropOK(d) {
			continue
		}
		v := 55.0
		if b.hpFrac < 0.7 {
			v += 25
		}
		consider(d.ID, "drop", d.Pos[0], d.Pos[1], v-hypot2(b.px, b.py, d.Pos[0], d.Pos[1])*0.2)
	}
	if best.set && bv > -10 {
		curLoot = best
		return best.x, best.y, true
	}
	curLoot = lootGoalT{}
	return b.explore()
}

// stopToShoot: when the goal is a drone or crate and it is in range and visible, stand and shoot.
func (b *Brain) stopToShoot() bool {
	if curLoot.set && (curLoot.kind == "drone" || curLoot.kind == "crate") {
		if b.tdrone != nil || b.tcrate != nil {
			var p [3]float64
			if b.tdrone != nil {
				p = b.tdrone.Pos
			} else {
				p = b.tcrate.Pos
			}
			return hypot2(b.px, b.py, p[0], p[1]) < 55
		}
	}
	return false
}

func (b *Brain) explore() (float64, float64, bool) {
	cx, cy, r := b.safeCircle()
	need := b.t-exploreAt > 28 || math.Hypot(b.px-exploreGoal[0], b.py-exploreGoal[1]) < 14 ||
		math.Hypot(exploreGoal[0]-cx, exploreGoal[1]-cy) > r*0.8
	if need {
		bestS := -1e18
		var bx, by float64
		for i := 0; i < 40; i++ {
			ang := b.rng.Float64() * 2 * math.Pi
			rad := math.Sqrt(b.rng.Float64()) * r * 0.65
			x, y := cx+math.Cos(ang)*rad, cy+math.Sin(ang)*rad
			if !b.W.inMap(x, y) {
				continue
			}
			id := b.Nav.Snap(x, y)
			if id < 0 {
				continue
			}
			x, y = b.Nav.Center(id%b.Nav.NA, id/b.Nav.NA)
			d := math.Hypot(x-b.px, y-b.py)
			s := -math.Abs(d-120) * 0.01
			if !visited[visitKey(x, y)] {
				s += 1.0
			}
			if s > bestS {
				bestS, bx, by = s, x, y
			}
		}
		if bestS > -1e17 {
			exploreGoal = [2]float64{bx, by}
		} else {
			exploreGoal = [2]float64{cx, cy}
		}
		exploreAt = b.t
	}
	return exploreGoal[0], exploreGoal[1], true
}

// lootTake issues a take for a weapon within reach.
func (b *Brain) lootTake(a *Action) {
	for _, ev := range b.obs.Events {
		if ev.Type == "take_failed" {
			b.takeFail[ev.ID] = b.t + 6
		}
	}
	bestV := 0.0
	var bp *Pickup
	for i := range b.obs.Pickups {
		p := &b.obs.Pickups[i]
		if b.takeFail[p.ID] > b.t {
			continue
		}
		if hypot2(b.px, b.py, p.Pos[0], p.Pos[1]) > 3.6 || math.Abs(p.Pos[2]-b.pz) > 3.8 {
			continue
		}
		v := b.pickupValue(p.Kind)
		if v > bestV {
			bestV, bp = v, p
		}
	}
	if bp == nil {
		return
	}
	id := bp.ID
	a.Take = &id
	// need a free slot?
	if len(b.me.Weapons) > 1 && b.me.Weapons[1] != nil {
		s := 1
		a.Drop = &s
	}
	if len(b.me.Weapons) > 0 && b.me.Weapons[0] == nil {
		s := 0
		a.Drop = nil
		_ = s
	}
}
