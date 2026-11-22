package main

import (
	"math"
	"time"
)

type spotParams struct {
	wPath, wThreat, wBand, wCover, wZone, wTLOS, wApproach float64
	bandLo, bandHi                                         float64
	maxDist                                                float64
	wantTLOS                                               bool
	farMax                                                 float64
	wRing                                                  float64
	tgtW                                                   float64
	wToward                                                float64
	towardX, towardY                                       float64
}

type tpos struct {
	x, y, z  float64
	eyeZ     float64
	danger   func(d float64) float64
	isTarget bool
	dist     float64
}

func wrapPi(a float64) float64 {
	for a > math.Pi {
		a -= 2 * math.Pi
	}
	for a < -math.Pi {
		a += 2 * math.Pi
	}
	return a
}

func clamp(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// danger of an enemy at distance d: roughly the chance it hurts us.
func dangerOf(e *Enemy, now float64) func(d float64) float64 {
	age := now - e.LastSeen
	fresh := 1.0
	if age > 1 {
		fresh = math.Max(0.25, 1-age/10)
	}
	laser := e.HasLaser
	return func(d float64) float64 {
		var v float64
		switch {
		case d < 60:
			v = 1.0
		case d < 100:
			v = 0.75
		case d < 150:
			v = 0.4
		case d < 210:
			v = 0.22
		default:
			v = 0.1
		}
		if laser && d < 270 && v < 0.7 {
			v = 0.7
		}
		return v * fresh
	}
}

func (b *Brain) buildThreatList(maxN int) []tpos {
	var out []tpos
	for _, e := range b.hostiles {
		if len(out) >= maxN {
			break
		}
		p := e.predict(0.5, b.t)
		sc := e.Scale
		if sc == 0 {
			sc = 1
		}
		out = append(out, tpos{x: p[0], y: p[1], z: p[2], eyeZ: p[2] + 8*sc,
			danger: dangerOf(e, b.t), isTarget: e == b.target, dist: e.Dist})
	}
	for _, g := range b.ghosts {
		if len(out) >= maxN+2 {
			break
		}
		gp := b.ghostPos(g, 70)
		gz := b.W.SurfZ(gp[0], gp[1])
		out = append(out, tpos{x: gp[0], y: gp[1], z: gz, eyeZ: gz + 8,
			danger: func(d float64) float64 { return 0.6 }, dist: 70})
	}
	return out
}

// hiddenFrom reports whether a mech standing at (x,y) cannot be seen by the threat.
func (b *Brain) exposedTo(t *tpos, x, y float64) bool {
	z := b.W.SurfZ(x, y)
	if b.W.BlockedStep(t.x, t.y, t.eyeZ, x, y, z+8.5, 2) == false {
		return true
	}
	if b.W.BlockedStep(t.x, t.y, t.eyeZ, x, y, z+4.5, 2) == false {
		return true
	}
	return false
}

// safeCircle: the circle we must stay in. From 30 s before a shrink starts (and during it) that is the
// NEXT circle, which always lies inside the current one.
func (b *Brain) safeCircle() (cx, cy, r float64) {
	z := b.obs.Zone
	cx, cy, r = z.Center[0], z.Center[1], z.Radius
	if b.t >= z.ShrinkStart-zoneLead && z.ShrinkEnd > z.ShrinkStart {
		if z.NextRadius < z.Radius {
			cx, cy, r = z.NextCenter[0], z.NextCenter[1], z.NextRadius
		}
	}
	return
}

// zoneLead: seconds before a shrink starts at which we already treat the next circle as the limit.
const zoneLead = 30.0

// zoneMargin: metres we keep inside the circle edge.
const zoneMargin = 17.0

// hardCircle is the area we never choose to leave: the safe circle minus the margin.
func (b *Brain) hardCircle() (cx, cy, r float64) {
	cx, cy, r = b.safeCircle()
	r -= zoneMargin
	if r < 4 {
		r = 4
	}
	return
}

// zoneOK tells whether stepping to (x,y) respects the zone: inside the hard circle, or, when
// already outside it, at least not farther out than now.
func (b *Brain) zoneOK(x, y float64) bool {
	cx, cy, r := b.hardCircle()
	nd := math.Hypot(x-cx, y-cy)
	if nd <= r {
		return true
	}
	cur := math.Hypot(b.px-cx, b.py-cy)
	if b.mode == "zone" {
		return cur > r && nd <= cur+4 // walking round an obstacle on the way back in
	}
	return cur > r && nd <= cur-0.5
}

func (b *Brain) zonePen(x, y float64) float64 {
	cx, cy, r := b.hardCircle()
	ex := math.Hypot(x-cx, y-cy) - r
	if ex <= 0 {
		return 0
	}
	dps := b.obs.Zone.DPS
	if dps < 20 {
		dps = 20
	}
	return (4 + ex/10) * math.Min(4, dps/20)
}

func (b *Brain) scoreSpot(sp *spotParams, tl []tpos, x, y, pd float64) float64 {
	s := -sp.wPath * pd
	nid := b.Nav.Snap(x, y)
	if nid >= 0 {
		s += sp.wCover * float64(1-b.Nav.Expo[nid])
		s -= sp.wRing * float64(b.Nav.Ring[nid])
	}
	s -= math.Max(sp.wZone, 2.5) * b.zonePen(x, y)
	for i := range tl {
		t := &tl[i]
		d := math.Hypot(t.x-x, t.y-y)
		if b.exposedTo(t, x, y) && d < 280 {
			w := 1.0
			if t.isTarget {
				w = sp.tgtW
				if w == 0 {
					w = 0.45
				}
			}
			s -= sp.wThreat * w * t.danger(d)
		}
		if sp.wApproach > 0 && d < t.dist {
			s -= sp.wApproach * (t.dist - d) / 50
		}
		if t.isTarget {
			if d < sp.bandLo {
				s -= sp.wBand * (sp.bandLo - d) / 10
			} else if d > sp.bandHi {
				s -= sp.wBand * (d - sp.bandHi) / 25
			}
			if sp.wantTLOS && d <= sp.bandHi+40 && !b.W.BlockedStep(x, y, b.W.SurfZ(x, y)+7, t.x, t.y, t.z+4.5, 2) {
				s += sp.wTLOS
			}
		}
	}
	for i := range b.items {
		it := &b.items[i]
		if math.Hypot(it.x-x, it.y-y) < 4 {
			s += it.val
		}
	}
	if sp.wToward > 0 {
		s -= sp.wToward * math.Hypot(x-sp.towardX, y-sp.towardY) / 100
	}
	return s
}

// pickSpot searches the nodes within reach and returns the best standing point.
// If cur is valid it only switches when the new spot is clearly better.
func (b *Brain) pickSpot(sp spotParams, curValid bool, curX, curY float64) (float64, float64, float64, bool) {
	tl := b.buildThreatList(4)
	dl := b.deadline.Add(-8 * time.Millisecond)
	order := b.Nav.Reach(b.px, b.py, sp.maxDist, dl)
	if len(order) == 0 {
		return 0, 0, 0, false
	}
	stride := len(order)/110 + 1
	bestS, bx, by := -1e18, 0.0, 0.0
	for i := 0; i < len(order); i += stride {
		id := order[i]
		x, y := b.Nav.Center(int(id)%b.Nav.NA, int(id)/b.Nav.NA)
		s := b.scoreSpot(&sp, tl, x, y, float64(b.Nav.Dist(id)))
		if s > bestS {
			bestS, bx, by = s, x, y
		}
		if i&7 == 0 && time.Now().After(dl) {
			break
		}
	}
	if sp.farMax > 0 {
		for i := range b.fort {
			if i&15 == 0 && time.Now().After(dl) {
				break
			}
			f := &b.fort[i]
			d := math.Hypot(f.x-b.px, f.y-b.py)
			if d > sp.farMax || b.zonePen(f.x, f.y) > 0 {
				continue
			}
			pd := d * 1.35
			if pd > 60 {
				pd = 60 + 0.3*(pd-60)
			}
			if s := b.scoreSpot(&sp, tl, f.x, f.y, pd); s > bestS {
				bestS, bx, by = s, f.x, f.y
			}
		}
	}
	for i := range b.items {
		if time.Now().After(dl) {
			break
		}
		it := &b.items[i]
		id := b.Nav.Snap(it.x, it.y)
		if id < 0 {
			continue
		}
		x, y := b.Nav.Center(id%b.Nav.NA, id/b.Nav.NA)
		s := b.scoreSpot(&sp, tl, it.x, it.y, math.Hypot(it.x-b.px, it.y-b.py)*1.25+3)
		if s > bestS && math.Hypot(it.x-b.px, it.y-b.py) < sp.maxDist {
			bestS, bx, by = s, it.x, it.y
		}
		_ = x
		_ = y
	}
	if curValid {
		cs := b.scoreSpot(&sp, tl, curX, curY, math.Hypot(curX-b.px, curY-b.py)*1.25)
		if cs+0.35 >= bestS {
			return curX, curY, cs, true
		}
	}
	return bx, by, bestS, true
}

type itemT struct{ x, y, val float64 }

// buildItems lists cubes and wanted weapons close enough to grab during a fight.
func (b *Brain) buildItems() {
	b.items = b.items[:0]
	for _, c := range b.cubes {
		d := hypot2(b.px, b.py, c.Pos[0], c.Pos[1])
		if d < 60 && b.groundOK(c.Pos) {
			b.items = append(b.items, itemT{c.Pos[0], c.Pos[1], 1.1})
		}
		if len(b.items) > 8 {
			break
		}
	}
	for _, p := range b.pickups {
		d := hypot2(b.px, b.py, p.Pos[0], p.Pos[1])
		v := b.pickupValue(p.Kind)
		if v > 0 && d < 80 && b.groundOK(p.Pos) && b.takeFail[p.ID] <= b.t {
			b.items = append(b.items, itemT{p.Pos[0], p.Pos[1], 0.9 + v/40})
		}
		if len(b.items) > 12 {
			break
		}
	}
}

type fortT struct{ x, y, ring float64 }

// buildFortresses lists well enclosed standing points (thinned to one per 25 m): spots that few
// lines of sight reach, where a shooter ring of 55+ m has almost nothing to aim along.
func (b *Brain) buildFortresses() {
	type c struct{ x, y, r float64 }
	var cs []c
	n := b.Nav
	for id := range n.Walk {
		if n.Walk[id] && n.Ring[id] <= 0.2 && n.Expo[id] <= 0.5 {
			x, y := n.Center(id%n.NA, id/n.NA)
			cs = append(cs, c{x, y, float64(n.Ring[id])})
		}
	}
	for i := 1; i < len(cs); i++ { // insertion sort would be slow; use a simple shell sort
		_ = i
	}
	sortC := func() {
		gap := len(cs) / 2
		for gap > 0 {
			for i := gap; i < len(cs); i++ {
				t := cs[i]
				j := i
				for ; j >= gap && cs[j-gap].r > t.r; j -= gap {
					cs[j] = cs[j-gap]
				}
				cs[j] = t
			}
			gap /= 2
		}
	}
	sortC()
	for _, a := range cs {
		ok := true
		for _, f := range b.fort {
			if math.Hypot(a.x-f.x, a.y-f.y) < 25 {
				ok = false
				break
			}
		}
		if ok {
			b.fort = append(b.fort, fortT{a.x, a.y, a.r})
		}
	}
}
