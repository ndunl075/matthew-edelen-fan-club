package main

import "encoding/json"

// ---- server -> bot ----

type Hello struct {
	Type     string `json:"type"`
	Protocol string `json:"protocol"`
	Slot     int    `json:"slot"`
	Seed     int64  `json:"seed"`
	Map      struct {
		Dir   string   `json:"dir"`
		Files []string `json:"files"`
	} `json:"map"`
	Config Config `json:"config"`
}

type WeaponCfg struct {
	Damage    float64 `json:"damage"`
	Cooldown  float64 `json:"cooldown"`
	Speed     float64 `json:"speed"`
	Range     float64 `json:"range"`
	SpreadDeg float64 `json:"spread_deg"`
	Mag       float64 `json:"mag"`
	Reload    float64 `json:"reload"`
	Charge    float64 `json:"charge"`
	MinCharge float64 `json:"min_charge"`
	MinFrac   float64 `json:"min_frac"`
	HoldS     float64 `json:"hold_s"`
	Energy    float64 `json:"energy"`
	ConeDeg   float64 `json:"cone_deg"`
	Splash    float64 `json:"splash"`
	SplashR   float64 `json:"splash_radius"`
	Gravity   float64 `json:"gravity"`
}

type MechCfg struct {
	Radius       float64 `json:"radius"`
	Height       float64 `json:"height"`
	EyeHeight    float64 `json:"eye_height"`
	MuzzleHeight float64 `json:"muzzle_height"`
	HP           float64 `json:"hp"`
	WalkSpeed    float64 `json:"walk_speed"`
	BoostSpeed   float64 `json:"boost_speed"`
	StepUp       float64 `json:"step_up"`
	MaxSlopeDeg  float64 `json:"max_slope_deg"`
	Energy       float64 `json:"energy"`
	TurnRate     float64 `json:"turn_rate"`
	PitchRate    float64 `json:"pitch_rate"`
	MinPitch     float64 `json:"min_pitch"`
	MaxPitch     float64 `json:"max_pitch"`
	RegenDelay   float64 `json:"regen_delay"`
	RegenPerS    float64 `json:"regen_per_s"`
	BoostDrain   float64 `json:"boost_drain"`
}

type Config struct {
	Match struct {
		TickHz    float64 `json:"tick_hz"`
		DurationS float64 `json:"duration_s"`
	} `json:"match"`
	Bot struct {
		TickBudgetMs float64 `json:"tick_budget_ms"`
	} `json:"bot"`
	Mech   MechCfg `json:"mech"`
	Vision struct {
		Range   float64 `json:"range"`
		FovDeg  float64 `json:"fov_deg"`
		Near    float64 `json:"near_range"`
		MemoryS float64 `json:"memory_s"`
	} `json:"vision"`
	Weapons map[string]WeaponCfg `json:"weapons"`
	Cubes   struct {
		PickupRadius float64 `json:"pickup_radius"`
		StatCap      float64 `json:"stat_cap"`
	} `json:"cubes"`
	Loot struct {
		PickupRadius float64 `json:"pickup_radius"`
	} `json:"loot"`
	Drops struct {
		Radius   float64 `json:"radius"`
		CaptureS float64 `json:"capture_s"`
	} `json:"drops"`
}

type WeaponState struct {
	Kind     string    `json:"kind"`
	Cooldown float64   `json:"cooldown"`
	Ammo     float64   `json:"ammo"`
	Reload   float64   `json:"reload"`
	Charge   float64   `json:"charge"`
	Stats    WeaponCfg `json:"stats"`
}

type SelfState struct {
	ID        int            `json:"id"`
	Pos       [3]float64     `json:"pos"`
	Vel       [3]float64     `json:"vel"`
	Aim       [2]float64     `json:"aim"`
	HP        float64        `json:"hp"`
	MaxHP     float64        `json:"max_hp"`
	Energy    float64        `json:"energy"`
	MaxEnergy float64        `json:"max_energy"`
	Grounded  bool           `json:"grounded"`
	Cubes     int            `json:"cubes"`
	Upgrades  []string       `json:"upgrades"`
	InZone    bool           `json:"in_zone"`
	Weapons   []*WeaponState `json:"weapons"`
	Stats     map[string]any `json:"stats"`
}

type Mech struct {
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
	ID      int      `json:"id"`
	Kind    string   `json:"kind"`
	Slot    int      `json:"slot"`
	Reason  string   `json:"reason"`
	Cubes   int      `json:"cubes"`
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

type OfferChoice struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

type Offer struct {
	ID      int           `json:"id"`
	Expires float64       `json:"expires"`
	Choices []OfferChoice `json:"choices"`
}

type Obs struct {
	Type        string    `json:"type"`
	Tick        int       `json:"tick"`
	T           float64   `json:"t"`
	Alive       int       `json:"alive"`
	Self        SelfState `json:"self"`
	Mechs       []Mech    `json:"mechs"`
	Projectiles []Proj    `json:"projectiles"`
	Crates      []Crate   `json:"crates"`
	Cubes       []Cube    `json:"cubes"`
	Pickups     []Pickup  `json:"pickups"`
	Sounds      []Sound   `json:"sounds"`
	Beams       []Beam    `json:"beams"`
	Events      []Event   `json:"events"`
	Drops       []Drop    `json:"drops"`
	Zone        Zone      `json:"zone"`
	Offer       *Offer    `json:"offer"`
}

// ---- bot -> server ----

type DebugLine struct {
	A     [3]float64 `json:"a"`
	B     [3]float64 `json:"b"`
	Color string     `json:"color,omitempty"`
}

type DebugPoint struct {
	At    [3]float64 `json:"at"`
	Color string     `json:"color,omitempty"`
	Size  int        `json:"size,omitempty"`
}

type Debug struct {
	Label  string       `json:"label,omitempty"`
	Lines  []DebugLine  `json:"lines,omitempty"`
	Points []DebugPoint `json:"points,omitempty"`
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

type Ready struct {
	Type string `json:"type"`
	Name string `json:"name"`
}

func marshalLine(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		return []byte("{}\n")
	}
	return append(b, '\n')
}
