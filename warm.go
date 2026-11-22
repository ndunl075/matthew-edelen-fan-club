package main

import (
	"encoding/json"
	"fmt"
	"os"
	"time"
)

// warmUp runs a few synthetic decisions on a throw-away brain so the first real decision is not cold
// (lazy runtime initialisation, first path search, JSON type caches).
func warmUp(src *Brain) {
	defer func() { recover() }()
	if src.w == nil || src.nav == nil {
		return
	}
	t0 := time.Now()
	wb := &Brain{hello: src.hello, cfg: src.cfg, w: src.w, nav: src.nav, rng: src.rng, tracks: map[int]*track{},
		pickups: map[int]*item{}, crates: map[int]*item{}, cubeM: map[int]*item{}, badPick: map[int]float64{}}
	wb.vnx, wb.vny = src.vnx, src.vny
	wb.visited = make([]bool, len(src.visited))
	z := src.cfg
	_ = z
	for i := 0; i < 6; i++ {
		o := Obs{Type: "obs", Tick: i * 2, T: float64(i) * 0.1, Alive: 16}
		o.Self = SelfState{ID: 3, Pos: [3]float64{41 + float64(i), -73, 0}, HP: 1400, MaxHP: 1400, Energy: 100, MaxEnergy: 100,
			Grounded: true, InZone: true, Aim: [2]float64{0, 0}, Stats: map[string]any{"muzzle_height": 7.0, "eye_height": 8.0},
			Weapons: []*WeaponState{{Kind: "gun", Ammo: 12, Stats: WeaponCfg{Range: 230, Speed: 252, Mag: 12}}, nil}}
		o.Zone = Zone{Center: [2]float64{41.3, -72.8}, Radius: 560, NextCenter: [2]float64{0, 0}, NextRadius: 380, ShrinkStart: 60, ShrinkEnd: 110, DPS: 20}
		o.Mechs = []Mech{{ID: 5, Kind: "mech", Pos: [3]float64{120, -60, 0}, HP: 1400, MaxHP: 1400, Weapons: []string{"gun"}}}
		o.Pickups = []Pickup{{ID: 8001, Kind: "laser", Pos: [3]float64{-60, -20, 0}}}
		o.Crates = []Crate{{ID: 1001, Pos: [3]float64{90, -30, 0}, HP: 300}}
		line, _ := json.Marshal(o)
		wb.handle(line)
	}
	src.nav.pen = map[int]float32{}
	fmt.Fprintf(os.Stderr, "warm-up %v\n", time.Since(t0))
}
