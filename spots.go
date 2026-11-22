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

// safe circle: where we want to be (next circle once a shrink is near).
func (b *Brain) safeCircle() (cx, cy, r float64) {
	z := b.obs.Zone
	cx, cy, r = z.Center[0], z.Center[1], z.Radius
	if b.t >= z.ShrinkStart-18 && z.NextRadius < z.Radius && z.ShrinkEnd > z.ShrinkStart {
		cx, cy, r = z.NextCenter[0], z.NextCenter[1], z.NextRadius
	}
	return
}

func (b *Brain) zonePen(x, y float64) float64 {
	cx, cy, r := b.safeCircle()
	margin := 10 + 0.08*r
	ex := math.Hypot(x-cx, y-cy) - (r - margin)
	if ex <= 0 {
		return 0
	}
	dps := b.obs.Zone.DPS
	if dps < 20 {
		dps = 20
	}
	return ex / 15 * math.Min(4, dps/20)
}

func (b *Brain) scoreSpot(sp *spotParams, tl []tpos, x, y, pd float64) float64 {
	s := -sp.wPath * pd
	nid := b.Nav.Snap(x, y)
	if nid >= 0 {
		s += sp.wCover * float64(1-b.Nav.Expo[nid])
	}
	s -= sp.wZone * b.zonePen(x, y)
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
		if i&31 == 0 && time.Now().After(dl) {
			break
		}
	}
	if curValid {
		cs := b.scoreSpot(&sp, tl, curX, curY, math.Hypot(curX-b.px, curY-b.py)*1.25)
		if cs+0.35 >= bestS {
			return curX, curY, cs, true
		}
	}
	return bx, by, bestS, true
}
