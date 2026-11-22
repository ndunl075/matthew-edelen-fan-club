package main

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
)

const negInf = float32(-1e9)
const posInf = float32(1e9)

// World is the static map rasterised on the 1 m terrain grid.
type World struct {
	MinX, MinY float64
	NX, NY     int
	Bounds     [4]float64 // minx miny maxx maxy
	Ground     []float32
	Top        []float32 // top of ground-based solid (building/stadium) or negInf
	OvB, OvT   []float32 // overhang underside/top, OvB=posInf when none
	Wall       []bool    // blocks a walking mech
	WallDist   []float32 // metres to the nearest wall cell (capped)
}

type prismJSON struct {
	Kind  string      `json:"kind"`
	Base  float64     `json:"base"`
	Top   float64     `json:"top"`
	Ring  []float64   `json:"ring"`
	Holes [][]float64 `json:"holes"`
}

type worldJSON struct {
	Bounds struct {
		MinX float64 `json:"minX"`
		MaxX float64 `json:"maxX"`
		MinY float64 `json:"minY"`
		MaxY float64 `json:"maxY"`
	} `json:"bounds"`
	Terrain struct {
		File string  `json:"file"`
		MinX float64 `json:"minX"`
		MinY float64 `json:"minY"`
		Step float64 `json:"step"`
		NX   int     `json:"nx"`
		NY   int     `json:"ny"`
		Sc   float64 `json:"scale"`
	} `json:"terrain"`
	Prisms []prismJSON `json:"prisms"`
}

func pip(ring []float64, x, y float64) bool {
	n := len(ring) / 2
	in := false
	j := n - 1
	for i := 0; i < n; i++ {
		xi, yi := ring[2*i], ring[2*i+1]
		xj, yj := ring[2*j], ring[2*j+1]
		if (yi > y) != (yj > y) && x < (xj-xi)*(y-yi)/(yj-yi)+xi {
			in = !in
		}
		j = i
	}
	return in
}

// LoadWorld reads world.json and terrain.i16 below dir (dir may be the v2 folder or its parent).
func LoadWorld(dir string) (*World, error) {
	var wj worldJSON
	base := dir
	b, err := os.ReadFile(filepath.Join(dir, "v2", "world.json"))
	if err != nil {
		b, err = os.ReadFile(filepath.Join(dir, "world.json"))
		if err != nil {
			return nil, err
		}
	} else {
		base = filepath.Join(dir, "v2")
	}
	if err := json.Unmarshal(b, &wj); err != nil {
		return nil, err
	}
	t := wj.Terrain
	if t.NX <= 0 || t.NY <= 0 {
		return nil, errors.New("bad terrain header")
	}
	raw, err := os.ReadFile(filepath.Join(base, "terrain.i16"))
	if err != nil {
		return nil, err
	}
	if len(raw) < t.NX*t.NY*2 {
		return nil, errors.New("terrain too short")
	}
	w := &World{MinX: t.MinX, MinY: t.MinY, NX: t.NX, NY: t.NY}
	w.Bounds = [4]float64{wj.Bounds.MinX, wj.Bounds.MinY, wj.Bounds.MaxX, wj.Bounds.MaxY}
	n := t.NX * t.NY
	w.Ground = make([]float32, n)
	w.Top = make([]float32, n)
	w.OvB = make([]float32, n)
	w.OvT = make([]float32, n)
	w.Wall = make([]bool, n)
	w.WallDist = make([]float32, n)
	for i := 0; i < n; i++ {
		w.Ground[i] = float32(int16(binary.LittleEndian.Uint16(raw[2*i:]))) * 0.01
		w.Top[i] = negInf
		w.OvB[i] = posInf
		w.OvT[i] = negInf
	}
	for pi := range wj.Prisms {
		w.rasterPrism(&wj.Prisms[pi])
	}
	w.buildWalls()
	return w, nil
}

func (w *World) rasterPrism(p *prismJSON) {
	if len(p.Ring) < 6 {
		return
	}
	minx, miny := math.Inf(1), math.Inf(1)
	maxx, maxy := math.Inf(-1), math.Inf(-1)
	for i := 0; i+1 < len(p.Ring); i += 2 {
		minx = math.Min(minx, p.Ring[i])
		maxx = math.Max(maxx, p.Ring[i])
		miny = math.Min(miny, p.Ring[i+1])
		maxy = math.Max(maxy, p.Ring[i+1])
	}
	i0 := int(math.Floor(minx - w.MinX))
	i1 := int(math.Ceil(maxx - w.MinX))
	j0 := int(math.Floor(miny - w.MinY))
	j1 := int(math.Ceil(maxy - w.MinY))
	if i0 < 0 {
		i0 = 0
	}
	if j0 < 0 {
		j0 = 0
	}
	if i1 >= w.NX {
		i1 = w.NX - 1
	}
	if j1 >= w.NY {
		j1 = w.NY - 1
	}
	for j := j0; j <= j1; j++ {
		y := w.MinY + float64(j)
		for i := i0; i <= i1; i++ {
			x := w.MinX + float64(i)
			if !pip(p.Ring, x, y) {
				continue
			}
			inHole := false
			for _, h := range p.Holes {
				if len(h) >= 6 && pip(h, x, y) {
					inHole = true
					break
				}
			}
			if inHole {
				continue
			}
			c := j*w.NX + i
			g := float64(w.Ground[c])
			if p.Base <= g+0.25 {
				if float32(p.Top) > w.Top[c] {
					w.Top[c] = float32(p.Top)
				}
			} else if float32(p.Base) < w.OvB[c] {
				w.OvB[c] = float32(p.Base)
				w.OvT[c] = float32(p.Top)
			}
		}
	}
}

func (w *World) buildWalls() {
	nx, ny := w.NX, w.NY
	for j := 0; j < ny; j++ {
		for i := 0; i < nx; i++ {
			c := j*nx + i
			g := w.Ground[c]
			wall := false
			if w.Top[c] > negInf/2 && w.Top[c]-g > 1.15 {
				wall = true
			}
			if w.OvB[c] < posInf/2 && w.OvB[c]-g < 9.4 {
				wall = true
			}
			if !wall {
				// steep terrain
				for _, d := range [4][2]int{{1, 0}, {-1, 0}, {0, 1}, {0, -1}} {
					ii, jj := i+d[0], j+d[1]
					if ii < 0 || jj < 0 || ii >= nx || jj >= ny {
						continue
					}
					if math.Abs(float64(w.Ground[jj*nx+ii]-g)) > 1.15 {
						wall = true
						break
					}
				}
			}
			x := w.MinX + float64(i)
			y := w.MinY + float64(j)
			if x < w.Bounds[0]+0.5 || x > w.Bounds[2]-0.5 || y < w.Bounds[1]+0.5 || y > w.Bounds[3]-0.5 {
				wall = true
			}
			w.Wall[c] = wall
		}
	}
	// chamfer distance transform, capped
	const capD = 40
	d := w.WallDist
	for c := range d {
		if w.Wall[c] {
			d[c] = 0
		} else {
			d[c] = capD
		}
	}
	const dg = 1.4142
	for j := 0; j < ny; j++ {
		for i := 0; i < nx; i++ {
			c := j*nx + i
			v := d[c]
			if i > 0 {
				v = min32(v, d[c-1]+1)
			}
			if j > 0 {
				v = min32(v, d[c-nx]+1)
				if i > 0 {
					v = min32(v, d[c-nx-1]+dg)
				}
				if i < nx-1 {
					v = min32(v, d[c-nx+1]+dg)
				}
			}
			d[c] = v
		}
	}
	for j := ny - 1; j >= 0; j-- {
		for i := nx - 1; i >= 0; i-- {
			c := j*nx + i
			v := d[c]
			if i < nx-1 {
				v = min32(v, d[c+1]+1)
			}
			if j < ny-1 {
				v = min32(v, d[c+nx]+1)
				if i < nx-1 {
					v = min32(v, d[c+nx+1]+dg)
				}
				if i > 0 {
					v = min32(v, d[c+nx-1]+dg)
				}
			}
			d[c] = v
		}
	}
}

func min32(a, b float32) float32 {
	if a < b {
		return a
	}
	return b
}

func (w *World) cell(x, y float64) int {
	i := int(math.Floor(x - w.MinX + 0.5))
	j := int(math.Floor(y - w.MinY + 0.5))
	if i < 0 {
		i = 0
	} else if i >= w.NX {
		i = w.NX - 1
	}
	if j < 0 {
		j = 0
	} else if j >= w.NY {
		j = w.NY - 1
	}
	return j*w.NX + i
}

func (w *World) inMap(x, y float64) bool {
	return x > w.Bounds[0] && x < w.Bounds[2] && y > w.Bounds[1] && y < w.Bounds[3]
}

// GroundZ is the bilinear terrain height.
func (w *World) GroundZ(x, y float64) float64 {
	fx := x - w.MinX
	fy := y - w.MinY
	i := int(math.Floor(fx))
	j := int(math.Floor(fy))
	if i < 0 {
		i, fx = 0, 0
	} else if i >= w.NX-1 {
		i, fx = w.NX-2, float64(w.NX-1)
	}
	if j < 0 {
		j, fy = 0, 0
	} else if j >= w.NY-1 {
		j, fy = w.NY-2, float64(w.NY-1)
	}
	tx := fx - float64(i)
	ty := fy - float64(j)
	c := j*w.NX + i
	a := float64(w.Ground[c])
	b := float64(w.Ground[c+1])
	cc := float64(w.Ground[c+w.NX])
	d := float64(w.Ground[c+w.NX+1])
	return (a*(1-tx)+b*tx)*(1-ty) + (cc*(1-tx)+d*tx)*ty
}

// SurfZ is the height a walker stands on at (x,y): ground, or a low ledge top.
func (w *World) SurfZ(x, y float64) float64 {
	c := w.cell(x, y)
	g := w.GroundZ(x, y)
	if w.Top[c] > negInf/2 && float64(w.Top[c])-g <= 1.2 && float64(w.Top[c]) > g {
		return float64(w.Top[c])
	}
	return g
}

// Blocked tells whether a straight line (shot or sight, trees ignored) is stopped by
// terrain, a building or an overhang.
func (w *World) Blocked(ax, ay, az, bx, by, bz float64) bool {
	return w.BlockedStep(ax, ay, az, bx, by, bz, 1.0)
}

// BlockedStep is Blocked with a chosen sampling step (coarser = faster, may miss thin walls).
func (w *World) BlockedStep(ax, ay, az, bx, by, bz, step float64) bool {
	dx, dy, dz := bx-ax, by-ay, bz-az
	l := math.Sqrt(dx*dx + dy*dy + dz*dz)
	n := int(l/step) + 1
	if n < 2 {
		n = 2
	}
	inv := 1 / float64(n)
	for k := 1; k < n; k++ {
		t := float64(k) * inv
		x := ax + dx*t
		y := ay + dy*t
		z := az + dz*t
		if !w.inMap(x, y) {
			continue
		}
		c := w.cell(x, y)
		g := float64(w.Ground[c])
		if z < g+1.5 && z < w.GroundZ(x, y)-0.05 {
			return true
		}
		if tp := w.Top[c]; tp > negInf/2 && z < float64(tp)-0.05 {
			return true
		}
		if ob := w.OvB[c]; ob < posInf/2 && z >= float64(ob) && z <= float64(w.OvT[c]) {
			return true
		}
	}
	return false
}

// WalkClear is true if a mech with clearance minD can walk the straight line a->b.
func (w *World) WalkClear(ax, ay, bx, by, minD float64) bool {
	dx, dy := bx-ax, by-ay
	l := math.Sqrt(dx*dx + dy*dy)
	n := int(l/0.9) + 1
	for k := 1; k <= n; k++ {
		t := float64(k) / float64(n)
		c := w.cell(ax+dx*t, ay+dy*t)
		if float64(w.WallDist[c]) < minD {
			return false
		}
	}
	return true
}

func (w *World) WD(x, y float64) float64 {
	return float64(w.WallDist[w.cell(x, y)])
}

// OpenStanding is true when a point is not inside a wall cell.
func (w *World) IsWall(x, y float64) bool { return w.Wall[w.cell(x, y)] }

// FirstBlock marches horizontally from (x,y,z) along (dx,dy) and returns the distance to the
// first terrain/building/overhang hit, or maxD.
func (w *World) FirstBlock(x, y, z, dx, dy, maxD float64) float64 {
	for r := 1.0; r <= maxD; r += 1.5 {
		px, py := x+dx*r, y+dy*r
		if !w.inMap(px, py) {
			return maxD
		}
		c := w.cell(px, py)
		if tp := w.Top[c]; tp > negInf/2 && z < float64(tp)-0.05 {
			return r
		}
		if ob := w.OvB[c]; ob < posInf/2 && z >= float64(ob) && z <= float64(w.OvT[c]) {
			return r
		}
		if z < float64(w.Ground[c]) {
			return r
		}
	}
	return maxD
}
