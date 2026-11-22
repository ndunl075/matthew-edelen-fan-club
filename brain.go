package main

import (
	"encoding/json"
	"fmt"
	"math"
	"math/rand"
	"os"
	"runtime/debug"
	"time"
)

type track struct {
	id        int
	kind      string
	pos, vel  [3]float64
	aim       [2]float64
	hp, maxHP float64
	cubes     int
	weapons   []string
	charging  bool
	seen      float64 // last time it was listed
	first     float64
	prevPos   [3]float64
	prevT     float64
}

type item struct {
	id   int
	kind string
	pos  [3]float64
	seen float64
	hp   float64
}

type dmgRec struct {
	t       float64
	amount  float64
	by      int // -1 zone
	bearing float64
	hasB    bool
	weapon  string
}

type target struct {
	tr     *track
	d      float64
	center [3]float64
	los    bool
	score  float64
	loot   bool // crate or drone
}

type Brain struct {
	hello *Hello
	cfg   *Config
	w     *World
	nav   *Nav
	rng   *rand.Rand
	slot  int

	t       float64
	tick    int
	lastAct Action
	haveAct bool

	tracks  map[int]*track
	pickups map[int]*item
	crates  map[int]*item
	cubeM   map[int]*item
	badPick map[int]float64

	dmg          []dmgRec
	lastDmgT     float64
	lastAlive    int
	deathsOthers int
	kills        int
	mode         int // 0 unknown, 1 practice-like, 2 free-for-all

	path      [][2]float64
	pi        int
	goal      [2]float64
	pathT     float64
	replan    bool
	pathFails int

	refT, refX, refY float64
	stuckN           int
	unstickUntil     float64
	unstickDir       [2]float64
	lastMoved        float64

	strafe      float64
	strafeUntil float64

	retreat   bool
	retreatT  float64
	coverT    float64
	cover     [2]float64
	coverOK   bool
	holdSince float64

	boosting bool

	visited   []bool
	vnx, vny  int
	visitT    float64
	explore   [2]float64
	exploreOK bool

	laserSince float64
	scanYaw    float64
	alertYaw   float64
	alertT     float64

	spotKey [3]float64
	spot    [2]float64
	spotOK  bool
	label   string
	tookT   float64
	prevHP  float64
	startT  float64
	t0      time.Time
	trace   *os.File
	dbg     string
}

func safeNewBrain(h *Hello) (b *Brain) {
	b = &Brain{hello: h, cfg: &h.Config, tracks: map[int]*track{}, pickups: map[int]*item{},
		crates: map[int]*item{}, cubeM: map[int]*item{}, badPick: map[int]float64{}}
	b.slot = h.Slot
	if p := os.Getenv("BOT_TRACE"); p != "" {
		b.trace, _ = os.OpenFile(p, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	}
	b.rng = rand.New(rand.NewSource(h.Seed + 12345))
	defer func() {
		if r := recover(); r != nil {
			fmt.Fprintln(os.Stderr, "init panic:", r, string(debug.Stack()))
		}
	}()
	t0 := time.Now()
	w, err := loadWorld(h.Map.Dir, h.Map.Files)
	if err != nil {
		fmt.Fprintln(os.Stderr, "world load failed:", err)
		return b
	}
	b.w = w
	b.nav = buildNav(w, h.Config.Mech)
	b.vnx, b.vny = int((w.maxX-w.minX)/24)+1, int((w.maxY-w.minY)/24)+1
	b.visited = make([]bool, b.vnx*b.vny)
	warmUp(b)
	fmt.Fprintf(os.Stderr, "init ok in %v\n", time.Since(t0))
	return b
}

// handle parses one observation line and returns the action line (never nil for a valid obs).
func (b *Brain) handle(line []byte) (out *Action) {
	var o Obs
	if err := json.Unmarshal(line, &o); err != nil {
		fmt.Fprintln(os.Stderr, "bad obs:", err)
		var tk struct {
			Tick int `json:"tick"`
		}
		if json.Unmarshal(line, &tk) == nil {
			return b.fallback(tk.Tick)
		}
		return nil
	}
	b.t0 = time.Now()
	defer func() {
		if r := recover(); r != nil {
			fmt.Fprintln(os.Stderr, "decide panic:", r, string(debug.Stack()))
			out = b.fallback(o.Tick)
		}
	}()
	out = b.decide(&o)
	if out == nil {
		out = b.fallback(o.Tick)
	}
	sanitize(out)
	b.lastAct = *out
	b.haveAct = true
	return out
}

func sanitize(a *Action) {
	for i := range a.Move {
		if !finite(a.Move[i]) {
			a.Move[i] = 0
		}
	}
	for i := range a.Aim {
		if !finite(a.Aim[i]) {
			a.Aim[i] = 0
		}
	}
	l := math.Hypot(a.Move[0], a.Move[1])
	if l > 1 {
		a.Move[0] /= l
		a.Move[1] /= l
	}
}

func (b *Brain) fallback(tick int) *Action {
	a := &Action{Tick: tick}
	if b.haveAct {
		a.Move = b.lastAct.Move
		a.Aim = b.lastAct.Aim
		a.Boost = b.lastAct.Boost
	}
	return a
}

// ---------------------------------------------------------------- helpers

func (b *Brain) stat(o *Obs, key string, def float64) float64 {
	if v, ok := o.Self.Stats[key].(float64); ok && v > 0 {
		return v
	}
	return def
}

func (b *Brain) slotOf(o *Obs, kind string) int {
	for i, w := range o.Self.Weapons {
		if w != nil && w.Kind == kind {
			return i
		}
	}
	return -1
}

func (b *Brain) emptySlot(o *Obs) int {
	for i := 0; i < 2; i++ {
		if i >= len(o.Self.Weapons) || o.Self.Weapons[i] == nil {
			return i
		}
	}
	return -1
}

func (b *Brain) eye(o *Obs) [3]float64 {
	return [3]float64{o.Self.Pos[0], o.Self.Pos[1], o.Self.Pos[2] + b.stat(o, "eye_height", 8)}
}

func (b *Brain) muzzle(o *Obs) [3]float64 {
	return [3]float64{o.Self.Pos[0], o.Self.Pos[1], o.Self.Pos[2] + b.stat(o, "muzzle_height", 7)}
}

func (b *Brain) visible(a, c [3]float64) bool {
	if b.w == nil {
		return true
	}
	return !b.w.segBlocked(a, c)
}

func centerOf(tr *track) [3]float64 {
	switch tr.kind {
	case "npc":
		return [3]float64{tr.pos[0], tr.pos[1], tr.pos[2] + 1.75}
	}
	s := 1 + 0.05*float64(tr.cubes)
	return [3]float64{tr.pos[0], tr.pos[1], tr.pos[2] + 4.5*s}
}

func (b *Brain) hpFrac(o *Obs) float64 {
	if o.Self.MaxHP <= 0 {
		return 1
	}
	return o.Self.HP / o.Self.MaxHP
}

// ---------------------------------------------------------------- memory update

func (b *Brain) update(o *Obs) {
	b.t = o.T
	b.tick = o.Tick
	if b.startT == 0 {
		b.startT = o.T
	}
	me := o.Self.Pos
	eye := b.eye(o)
	// enemies
	for i := range o.Mechs {
		m := &o.Mechs[i]
		if m.ID == o.Self.ID {
			continue
		}
		tr := b.tracks[m.ID]
		if tr == nil {
			tr = &track{id: m.ID, first: o.T}
			b.tracks[m.ID] = tr
		}
		tr.kind = m.Kind
		tr.prevPos, tr.prevT = tr.pos, tr.seen
		tr.pos, tr.vel, tr.aim = m.Pos, m.Vel, m.Aim
		tr.hp, tr.maxHP, tr.cubes = m.HP, m.MaxHP, m.Cubes
		tr.weapons, tr.charging = m.Weapons, m.Charging
		tr.seen = o.T
	}
	for id, tr := range b.tracks {
		if o.T-tr.seen > 12 {
			delete(b.tracks, id)
		}
	}
	// loot memory
	for _, p := range o.Pickups {
		b.pickups[p.ID] = &item{id: p.ID, kind: p.Kind, pos: p.Pos, seen: o.T}
	}
	for _, c := range o.Crates {
		b.crates[c.ID] = &item{id: c.ID, pos: c.Pos, hp: c.HP, seen: o.T}
	}
	for _, c := range o.Cubes {
		b.cubeM[c.ID] = &item{id: c.ID, pos: c.Pos, seen: o.T}
	}
	cleanup := func(m map[int]*item) {
		for id, it := range m {
			if it.seen == o.T {
				continue
			}
			if dist2(it.pos, me) < 38 && b.visible(eye, [3]float64{it.pos[0], it.pos[1], it.pos[2] + 0.6}) {
				delete(m, id)
			} else if o.T-it.seen > 150 {
				delete(m, id)
			}
		}
	}
	cleanup(b.pickups)
	cleanup(b.crates)
	cleanup(b.cubeM)
	for id, until := range b.badPick {
		if o.T > until {
			delete(b.badPick, id)
		}
	}
	// events
	killed := false
	for _, e := range o.Events {
		switch e.Type {
		case "damaged":
			by := -1
			if e.By != nil {
				by = *e.By
			}
			r := dmgRec{t: o.T, amount: e.Amount, by: by, weapon: e.Weapon}
			if e.Bearing != nil {
				r.bearing, r.hasB = *e.Bearing, true
			}
			b.dmg = append(b.dmg, r)
			if e.Weapon != "zone" {
				b.lastDmgT = o.T
				if r.hasB {
					b.alertYaw, b.alertT = r.bearing, o.T
				}
			}
		case "kill":
			killed = true
			b.kills++
		case "took":
			delete(b.pickups, e.ID)
		case "take_failed":
			b.badPick[e.ID] = o.T + 8
			if e.Reason == "gone" {
				delete(b.pickups, e.ID)
			}
		}
	}
	for len(b.dmg) > 0 && o.T-b.dmg[0].t > 8 {
		b.dmg = b.dmg[1:]
	}
	// mode detection: deaths we did not cause before the zone bites mean free-for-all
	if b.lastAlive > 0 && o.Alive < b.lastAlive && !killed && o.T < 140 {
		b.deathsOthers += b.lastAlive - o.Alive
		b.mode = 2
	}
	b.lastAlive = o.Alive
	if b.mode == 0 && o.T > 100 && b.deathsOthers == 0 {
		b.mode = 1
	}
	// sounds -> alert direction
	for _, s := range o.Sounds {
		if (s.Kind == "gun" || s.Kind == "laser" || s.Kind == "laser_charge" || s.Kind == "lightning") && s.Dist != "far" {
			b.alertYaw, b.alertT = s.Bearing, o.T
		}
	}
	// exploration bookkeeping
	if b.visited != nil {
		for dj := -2; dj <= 2; dj++ {
			for di := -2; di <= 2; di++ {
				x := me[0] + float64(di)*24
				y := me[1] + float64(dj)*24
				if math.Hypot(x-me[0], y-me[1]) > 50 {
					continue
				}
				i, j := int((x-b.w.minX)/24), int((y-b.w.minY)/24)
				if i >= 0 && j >= 0 && i < b.vnx && j < b.vny {
					b.visited[j*b.vnx+i] = true
				}
			}
		}
	}
}

// ---------------------------------------------------------------- upgrades

var upgradeOrder = []string{"plating", "armor", "nanites", "gun_damage", "gun_rate", "laser_damage", "laser_charge",
	"gyros", "servos", "gun_mag", "sensors", "reactor", "capacitor", "gun_velocity", "jets", "laser_pierce",
	"lightning_efficiency", "lightning_range", "lightning_chain", "mortar_reload", "mortar_velocity", "mortar_splash"}

func (b *Brain) pickOffer(o *Obs, act *Action) {
	if o.Offer == nil || len(o.Offer.Choices) == 0 {
		return
	}
	best, bestR := 0, 1<<30
	for i, c := range o.Offer.Choices {
		r := len(upgradeOrder) + 1
		for k, id := range upgradeOrder {
			if id == c.ID {
				r = k
				break
			}
		}
		// laser upgrades only matter when we hold a laser
		if (c.ID == "laser_damage" || c.ID == "laser_charge") && b.slotOf(o, "laser") < 0 {
			r += 6
		}
		if r < bestR {
			best, bestR = i, r
		}
	}
	act.Pick = &best
}

// left is the time remaining in this decision's internal budget (30 ms).
func (b *Brain) left() time.Duration {
	return 30*time.Millisecond - time.Since(b.t0)
}
