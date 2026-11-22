package main

import (
	"math"
	"math/rand"
	"time"
)

type Enemy struct {
	ID           int
	Pos, Vel     [3]float64
	Aim          [2]float64
	HP, MaxHP    float64
	Cubes        int
	Weapons      []string
	Charging     bool
	LastSeen     float64
	FirstSeen    float64
	InObs        bool
	LOS          bool
	Dist         float64
	HasLaser     bool
	HasLight     bool
	HasMortar    bool
	Scale        float64
	AimCount     float64
	HostileUntil float64
	Acc          [2]float64
}

type Drone struct {
	ID       int
	Pos      [3]float64
	HP       float64
	LastSeen float64
	InObs    bool
}

type PickupM struct {
	ID   int
	Kind string
	Pos  [3]float64
	Seen float64
}

type CubeM struct {
	ID   int
	Pos  [3]float64
	Seen float64
}

type CrateM struct {
	ID   int
	Pos  [3]float64
	HP   float64
	Seen float64
}

type Ghost struct { // an unseen attacker known only by bearing
	Bearing float64
	T       float64
	By      int
}

type posSample struct {
	t    float64
	x, y float64
}

type Brain struct {
	W   *World
	Nav *Nav
	rng *rand.Rand

	slot int
	// config numbers
	walk, boostSpd float64
	gunRange       float64
	laserRange     float64
	laserCharge    float64
	holdS          float64

	// per-decision
	obs      *Obs
	me       Self
	t        float64
	start    time.Time
	deadline time.Time
	px, py   float64
	pz       float64
	eyeZ     float64
	hpFrac   float64
	tickDt   float64
	scale    float64

	enemies map[int]*Enemy
	drones  map[int]*Drone
	pickups map[int]*PickupM
	cubes   map[int]*CubeM
	crates  map[int]*CrateM
	ghosts  []Ghost

	threats  []*Enemy // enemies we must reckon with, sorted by closeness
	items    []itemT
	fort     []fortT
	hostiles []*Enemy // those that attack or aim at us (or are very close)
	target   *Enemy
	tdrone   *Drone
	tcrate   *CrateM

	// movement state
	path         [][2]float64
	pathGoal     [2]float64
	pathAt       float64
	wp           int
	spotX        float64
	spotY        float64
	spotValid    bool
	spotAt       float64
	spotScore    float64
	hist         []posSample
	stuckUntil   float64
	stuckDir     [2]float64
	strafeSign   float64
	strafeEnd    float64
	mode         string
	recover      bool
	lastDamage   float64
	lastEnemyLOS float64
	lastHP       float64
	hpAt         float64

	lastAct       Action
	lastAim       [2]float64
	kills         int
	killSites     []killSite
	curTarget     int
	takeFail      map[int]float64
	laserOn       bool
	laserSince    float64
	firstLaserT   float64
	debugLabel    string
	tSpot, tPath  time.Duration
	lastTrace     int
	ban           map[string]float64
	progKey       string
	progT, progHP float64
}

func NewBrainLite(h *Hello) *Brain {
	b := &Brain{rng: rand.New(rand.NewSource(h.Seed + 1)), slot: h.Slot}
	b.initCommon(h)
	return b
}

func (b *Brain) initCommon(h *Hello) {
	b.enemies = map[int]*Enemy{}
	b.drones = map[int]*Drone{}
	b.pickups = map[int]*PickupM{}
	b.cubes = map[int]*CubeM{}
	b.crates = map[int]*CrateM{}
	b.takeFail = map[int]float64{}
	b.ban = map[string]float64{}
	b.walk = h.Config.Mech["walk_speed"]
	if b.walk == 0 {
		b.walk = 11.7
	}
	b.boostSpd = h.Config.Mech["boost_speed"]
	if b.boostSpd == 0 {
		b.boostSpd = 19.5
	}
	b.gunRange = h.Config.Weapons["gun"]["range"]
	if b.gunRange == 0 {
		b.gunRange = 230
	}
	b.laserRange = h.Config.Weapons["laser"]["range"]
	if b.laserRange == 0 {
		b.laserRange = 280
	}
	b.laserCharge = h.Config.Weapons["laser"]["charge"]
	if b.laserCharge == 0 {
		b.laserCharge = 0.75
	}
	b.holdS = h.Config.Weapons["laser"]["hold_s"]
	if b.holdS == 0 {
		b.holdS = 2
	}
	b.strafeSign = 1
	b.mode = "open"
}

func NewBrain(h *Hello) *Brain {
	b := &Brain{rng: rand.New(rand.NewSource(h.Seed + 1)), slot: h.Slot}
	b.initCommon(h)
	dir := h.Map.Dir
	if dir == "" {
		dir = "/arena"
	}
	t0 := time.Now()
	w, err := LoadWorld(dir)
	logf("world loaded in %v", time.Since(t0))
	if err != nil {
		logf("map load failed: %v", err)
		return b
	}
	b.W = w
	b.Nav = NewNav(w)
	logf("nav built at %v", time.Since(t0))
	b.Nav.BuildExposure(5 * time.Second)
	b.buildFortresses()
	logf("exposure done at %v", time.Since(t0))
	return b
}

func hypot2(ax, ay, bx, by float64) float64 { return math.Hypot(ax-bx, ay-by) }

func hasKind(ws []string, k string) bool {
	for _, w := range ws {
		if w == k {
			return true
		}
	}
	return false
}

// predicted position of an enemy `dt` seconds from now (capped extrapolation).
func (e *Enemy) predict(dt float64, now float64) [3]float64 {
	age := now - e.LastSeen
	if age < 0 {
		age = 0
	}
	span := dt + age
	if span > 2.0 {
		span = 2.0
	}
	return [3]float64{e.Pos[0] + e.Vel[0]*span, e.Pos[1] + e.Vel[1]*span, e.Pos[2]}
}

func (b *Brain) ingest(o *Obs) {
	if b.obs != nil && o.T > b.t {
		b.tickDt = o.T - b.t
	}
	b.obs = o
	b.me = o.Self
	b.t = o.T
	b.px, b.py, b.pz = o.Self.Pos[0], o.Self.Pos[1], o.Self.Pos[2]
	eye := o.Self.Stats["eye_height"]
	if eye == 0 {
		eye = 8
	}
	b.eyeZ = b.pz + eye
	b.scale = eye / 8
	if o.Self.MaxHP > 0 {
		b.hpFrac = o.Self.HP / o.Self.MaxHP
	} else {
		b.hpFrac = 1
	}
	if ws := o.Self.Stats["walk_speed"]; ws > 0 {
		b.walk = ws
	}
	if bs := o.Self.Stats["boost_speed"]; bs > 0 {
		b.boostSpd = bs
	}
	b.hist = append(b.hist, posSample{o.T, b.px, b.py})
	for len(b.hist) > 0 && o.T-b.hist[0].t > 2.5 {
		b.hist = b.hist[1:]
	}

	for _, e := range b.enemies {
		e.InObs = false
	}
	for _, d := range b.drones {
		d.InObs = false
	}
	for i := range o.Mechs {
		m := &o.Mechs[i]
		if m.ID == b.me.ID {
			continue
		}
		if m.Kind == "npc" {
			d := b.drones[m.ID]
			if d == nil {
				d = &Drone{ID: m.ID}
				b.drones[m.ID] = d
			}
			d.Pos, d.HP, d.LastSeen, d.InObs = m.Pos, m.HP, o.T, true
			continue
		}
		e := b.enemies[m.ID]
		if e == nil {
			e = &Enemy{ID: m.ID, FirstSeen: o.T}
			b.enemies[m.ID] = e
		}
		if dtv := o.T - e.LastSeen; e.InObs && dtv > 0.001 && dtv < 0.3 {
			ax, ay := (m.Vel[0]-e.Vel[0])/dtv, (m.Vel[1]-e.Vel[1])/dtv
			if h := math.Hypot(ax, ay); h > 40 {
				ax, ay = ax*40/h, ay*40/h
			}
			e.Acc = [2]float64{0.5*e.Acc[0] + 0.5*ax, 0.5*e.Acc[1] + 0.5*ay}
		} else {
			e.Acc = [2]float64{}
		}
		e.Pos, e.Vel, e.Aim = m.Pos, m.Vel, m.Aim
		e.HP, e.MaxHP, e.Cubes = m.HP, m.MaxHP, m.Cubes
		if e.MaxHP == 0 {
			e.MaxHP = 1400
		}
		e.Weapons, e.Charging = m.Weapons, m.Charging
		e.LastSeen, e.InObs = o.T, true
		e.HasLaser = hasKind(m.Weapons, "laser")
		e.HasLight = hasKind(m.Weapons, "lightning")
		e.HasMortar = hasKind(m.Weapons, "mortar")
		e.Scale = 1 + 0.05*float64(m.Cubes)
		// aim detector: a hunter keeps its torso on us
		dd := hypot2(b.px, b.py, m.Pos[0], m.Pos[1])
		bear := angleTo(m.Pos[0], m.Pos[1], b.px, b.py)
		if dd < 230 && math.Abs(wrapPi(m.Aim[0]-bear)) < math.Max(0.17, math.Atan2(4, dd)) {
			e.AimCount += 1
		} else {
			e.AimCount = math.Max(0, e.AimCount-0.5)
		}
		if e.AimCount >= 4 || (m.Charging && e.AimCount >= 1) {
			e.HostileUntil = math.Max(e.HostileUntil, o.T+5)
		}
	}
	// LOS and distance for all known enemies
	for id, e := range b.enemies {
		if o.T-e.LastSeen > 25 {
			delete(b.enemies, id)
			continue
		}
		p := e.predict(0, o.T)
		e.Dist = hypot2(b.px, b.py, p[0], p[1])
		e.LOS = false
		if b.W != nil {
			sc := e.Scale
			if sc == 0 {
				sc = 1
			}
			e.LOS = !b.W.Blocked(b.px, b.py, b.eyeZ, p[0], p[1], p[2]+4.5*sc)
		} else {
			e.LOS = e.InObs
		}
	}
	for id, d := range b.drones {
		if o.T-d.LastSeen > 20 {
			delete(b.drones, id)
		}
	}
	// loot memory
	seenP := map[int]bool{}
	for _, p := range o.Pickups {
		b.pickups[p.ID] = &PickupM{ID: p.ID, Kind: p.Kind, Pos: p.Pos, Seen: o.T}
		seenP[p.ID] = true
	}
	for id, p := range b.pickups {
		if !seenP[id] && hypot2(b.px, b.py, p.Pos[0], p.Pos[1]) < 40 && b.W != nil &&
			!b.W.Blocked(b.px, b.py, b.eyeZ, p.Pos[0], p.Pos[1], p.Pos[2]+1) {
			delete(b.pickups, id)
		}
	}
	seenC := map[int]bool{}
	for _, c := range o.Cubes {
		b.cubes[c.ID] = &CubeM{ID: c.ID, Pos: c.Pos, Seen: o.T}
		seenC[c.ID] = true
	}
	for id, c := range b.cubes {
		if !seenC[id] && hypot2(b.px, b.py, c.Pos[0], c.Pos[1]) < 40 && b.W != nil &&
			!b.W.Blocked(b.px, b.py, b.eyeZ, c.Pos[0], c.Pos[1], c.Pos[2]+1) {
			delete(b.cubes, id)
		}
	}
	seenR := map[int]bool{}
	for _, c := range o.Crates {
		b.crates[c.ID] = &CrateM{ID: c.ID, Pos: c.Pos, HP: c.HP, Seen: o.T}
		seenR[c.ID] = true
	}
	for id, c := range b.crates {
		if !seenR[id] && hypot2(b.px, b.py, c.Pos[0], c.Pos[1]) < 40 && b.W != nil &&
			!b.W.Blocked(b.px, b.py, b.eyeZ, c.Pos[0], c.Pos[1], c.Pos[2]+1) {
			delete(b.crates, id)
		}
	}
	// events
	for _, ev := range o.Events {
		switch ev.Type {
		case "damaged":
			if ev.By != nil {
				b.lastDamage = o.T
				if e := b.enemies[*ev.By]; e != nil {
					e.HostileUntil = math.Max(e.HostileUntil, o.T+12)
				}
				if e := b.enemies[*ev.By]; e == nil || !e.InObs {
					if ev.Bearing != nil {
						b.ghosts = append(b.ghosts, Ghost{Bearing: *ev.Bearing, T: o.T, By: *ev.By})
					}
				}
			} else {
				b.lastDamage = o.T
			}
		case "kill":
			b.kills++
			if b.target != nil {
				b.killSites = append(b.killSites, killSite{b.target.Pos[0], b.target.Pos[1], o.T})
			}
		}
	}
	gs := b.ghosts[:0]
	for _, g := range b.ghosts {
		if o.T-g.T < 4 {
			gs = append(gs, g)
		}
	}
	b.ghosts = gs
}

// activeThreats lists enemy mechs we treat as dangerous now.
func (b *Brain) computeThreats() {
	b.threats = b.threats[:0]
	b.hostiles = b.hostiles[:0]
	for _, e := range b.enemies {
		age := b.t - e.LastSeen
		if age > 8 {
			continue
		}
		b.threats = append(b.threats, e)
	}
	// sort by distance (small n)
	for i := 1; i < len(b.threats); i++ {
		for j := i; j > 0 && b.threats[j].Dist < b.threats[j-1].Dist; j-- {
			b.threats[j], b.threats[j-1] = b.threats[j-1], b.threats[j]
		}
	}
	for _, e := range b.threats {
		if e.hostile(b.t) {
			b.hostiles = append(b.hostiles, e)
		}
	}
}

func (b *Brain) ghostPos(g Ghost, d float64) [3]float64 {
	return [3]float64{b.px + math.Cos(g.Bearing)*d, b.py + math.Sin(g.Bearing)*d, b.pz}
}

func (b *Brain) weaponSlot(kind string) int {
	for i, w := range b.me.Weapons {
		if w != nil && w.Kind == kind {
			return i
		}
	}
	return -1
}

func (b *Brain) fallback(o *Obs) Action {
	a := b.lastAct
	a.Tick = o.Tick
	a.Fire = [2]bool{}
	a.Jump = false
	a.Pick, a.Take, a.Drop = nil, nil, nil
	a.Debug = nil
	return a
}

func (e *Enemy) hostile(now float64) bool {
	return e.HostileUntil > now || e.Dist < 32
}

type killSite struct{ x, y, t float64 }
