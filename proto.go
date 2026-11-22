package main

import (
	"encoding/json"
	"math"
)

// ---- messages from the server ----

type Weapon struct {
	Kind     string             `json:"kind"`
	Cooldown float64            `json:"cooldown"`
	Ammo     float64            `json:"ammo"`
	Reload   float64            `json:"reload"`
	Charge   float64            `json:"charge"`
	Stats    map[string]float64 `json:"stats"`
}

type Self struct {
	ID        int                `json:"id"`
	Pos       [3]float64         `json:"pos"`
	Vel       [3]float64         `json:"vel"`
	Aim       [2]float64         `json:"aim"`
	HP        float64            `json:"hp"`
	MaxHP     float64            `json:"max_hp"`
	Energy    float64            `json:"energy"`
	MaxEnergy float64            `json:"max_energy"`
	Grounded  bool               `json:"grounded"`
	Cubes     int                `json:"cubes"`
	InZone    bool               `json:"in_zone"`
	Weapons   []*Weapon          `json:"weapons"`
	Stats     map[string]float64 `json:"stats"`
}

type Ent struct {
	ID       int        `json:"id"`
	Kind     string     `json:"kind"`
	Pos      [3]float64 `json:"pos"`
	Vel      [3]float64 `json:"vel"`
	Aim      [2]float64 `json:"aim"`
	HP       float64    `json:"hp"`
	MaxHP    float64    `json:"max_hp"`
	Cubes    int        `json:"cubes"`
	Weapons  []string   `json:"weapons"`
	Charging bool       `json:"charging"`
}

type Proj struct {
	ID   int        `json:"id"`
	Kind string     `json:"kind"`
	Pos  [3]float64 `json:"pos"`
	Vel  [3]float64 `json:"vel"`
}

type Crate struct {
	ID  int        `json:"id"`
	Pos [3]float64 `json:"pos"`
	HP  float64    `json:"hp"`
}

type Cube struct {
	ID  int        `json:"id"`
	Pos [3]float64 `json:"pos"`
}

type Pickup struct {
	ID   int        `json:"id"`
	Kind string     `json:"kind"`
	Pos  [3]float64 `json:"pos"`
}

type Sound struct {
	Kind    string  `json:"kind"`
	Bearing float64 `json:"bearing"`
	Dist    string  `json:"dist"`
	Age     float64 `json:"age"`
}

type Beam struct {
	By   int          `json:"by"`
	Kind string       `json:"kind"`
	Path [][3]float64 `json:"path"`
	Age  float64      `json:"age"`
}

type Event struct {
	Type    string   `json:"type"`
	Amount  float64  `json:"amount"`
	Weapon  string   `json:"weapon"`
	Bearing *float64 `json:"bearing"`
	By      *int     `json:"by"`
	Rear    bool     `json:"rear"`
	Target  int      `json:"target"`
	Effect  string   `json:"effect"`
	ID      int      `json:"id"`
	Cubes   int      `json:"cubes"`
	Kind    string   `json:"kind"`
	Slot    int      `json:"slot"`
	Reason  string   `json:"reason"`
}

type Drop struct {
	ID       int        `json:"id"`
	Pos      [3]float64 `json:"pos"`
	Radius   float64    `json:"radius"`
	LandsIn  float64    `json:"lands_in"`
	CaptureS float64    `json:"capture_s"`
	Progress float64    `json:"progress"`
	Holder   *int       `json:"holder"`
}

type Zone struct {
	Center      [2]float64 `json:"center"`
	Radius      float64    `json:"radius"`
	NextCenter  [2]float64 `json:"next_center"`
	NextRadius  float64    `json:"next_radius"`
	ShrinkStart float64    `json:"shrink_start"`
	ShrinkEnd   float64    `json:"shrink_end"`
	DPS         float64    `json:"dps"`
}

type Choice struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Desc string `json:"desc"`
}

type Offer struct {
	ID      int      `json:"id"`
	Expires float64  `json:"expires"`
	Choices []Choice `json:"choices"`
}

type Obs struct {
	Type        string   `json:"type"`
	Tick        int      `json:"tick"`
	T           float64  `json:"t"`
	Alive       int      `json:"alive"`
	Self        Self     `json:"self"`
	Mechs       []Ent    `json:"mechs"`
	Projectiles []Proj   `json:"projectiles"`
	Crates      []Crate  `json:"crates"`
	Cubes       []Cube   `json:"cubes"`
	Pickups     []Pickup `json:"pickups"`
	Sounds      []Sound  `json:"sounds"`
	Beams       []Beam   `json:"beams"`
	Events      []Event  `json:"events"`
	Drops       []Drop   `json:"drops"`
	Zone        Zone     `json:"zone"`
	Offer       *Offer   `json:"offer"`
}

type MapInfo struct {
	Dir   string   `json:"dir"`
	Files []string `json:"files"`
}

type ZonePhase struct {
	HoldS   float64 `json:"hold_s"`
	ShrinkS float64 `json:"shrink_s"`
	Radius  float64 `json:"radius"`
	DPS     float64 `json:"dps"`
}

type Config struct {
	Mech    map[string]float64            `json:"mech"`
	Weapons map[string]map[string]float64 `json:"weapons"`
	Cubes   map[string]json.RawMessage    `json:"cubes"`
	Zone    struct {
		StartCenter [2]float64  `json:"start_center"`
		StartRadius float64     `json:"start_radius"`
		Phases      []ZonePhase `json:"phases"`
	} `json:"zone"`
	Drops map[string]json.RawMessage `json:"drops"`
}

type Hello struct {
	Type     string  `json:"type"`
	Protocol string  `json:"protocol"`
	Slot     int     `json:"slot"`
	Seed     int64   `json:"seed"`
	Map      MapInfo `json:"map"`
	Config   Config  `json:"config"`
}

// ---- messages to the server ----

type Debug struct {
	Label  string      `json:"label,omitempty"`
	Lines  []DebugLine `json:"lines,omitempty"`
	Points []DebugPt   `json:"points,omitempty"`
}

type DebugLine struct {
	A     [3]float64 `json:"a"`
	B     [3]float64 `json:"b"`
	Color string     `json:"color,omitempty"`
}

type DebugPt struct {
	At    [3]float64 `json:"at"`
	Color string     `json:"color,omitempty"`
	Size  int        `json:"size,omitempty"`
}

type Action struct {
	Tick   int        `json:"tick"`
	Move   [2]float64 `json:"move"`
	Boost  bool       `json:"boost"`
	Jump   bool       `json:"jump"`
	Aim    [2]float64 `json:"aim"`
	Fire   [2]bool    `json:"fire"`
	Reload bool       `json:"reload,omitempty"`
	Pick   *int       `json:"pick,omitempty"`
	Take   *int       `json:"take,omitempty"`
	Drop   *int       `json:"drop,omitempty"`
	Debug  *Debug     `json:"debug,omitempty"`
}

func fin(v float64) float64 {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return 0
	}
	return math.Round(v*1000) / 1000
}

func (a *Action) sanitize() {
	a.Move[0], a.Move[1] = fin(a.Move[0]), fin(a.Move[1])
	a.Aim[0], a.Aim[1] = fin(a.Aim[0]), fin(a.Aim[1])
}
