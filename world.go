package main

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
)

// World is a 1 m raster of the static map: terrain height plus the tops of solid prisms.
type World struct {
	nx, ny       int
	x0, y0, step float64
	minX, minY   float64
	maxX, maxY   float64
	ground       []float32 // terrain height per sample
	top          []float32 // top of ground-based solid (-inf if none)
	ob, ot       []float32 // overhang underside / top (+inf / -inf if none)
	hasOver      bool
}

type rawPrism struct {
	Kind  string      `json:"kind"`
	Base  float64     `json:"base"`
	Top   float64     `json:"top"`
	Ring  []float64   `json:"ring"`
	Holes [][]float64 `json:"holes"`
}

type rawWorld struct {
	Bounds struct {
		MinX float64 `json:"minX"`
		MaxX float64 `json:"maxX"`
		MinY float64 `json:"minY"`
		MaxY float64 `json:"maxY"`
	} `json:"bounds"`
	Terrain struct {
		File  string  `json:"file"`
		MinX  float64 `json:"minX"`
		MinY  float64 `json:"minY"`
		Step  float64 `json:"step"`
		Nx    int     `json:"nx"`
		Ny    int     `json:"ny"`
		Scale float64 `json:"scale"`
	} `json:"terrain"`
	Prisms []rawPrism `json:"prisms"`
}

const gapMin = 0.25

var negInf = float32(math.Inf(-1))
var posInf = float32(math.Inf(1))

func loadWorld(dir string, files []string) (*World, error) {
	wp, tp := "", ""
	for _, f := range files {
		switch filepath.Base(f) {
		case "world.json":
			wp = filepath.Join(dir, f)
		case "terrain.i16":
			tp = filepath.Join(dir, f)
		}
	}
	if wp == "" {
		wp = filepath.Join(dir, "v2", "world.json")
	}
	if tp == "" {
		tp = filepath.Join(filepath.Dir(wp), "terrain.i16")
	}
	wb, err := os.ReadFile(wp)
	if err != nil {
		return nil, err
	}
	var rw rawWorld
	if err := json.Unmarshal(wb, &rw); err != nil {
		return nil, err
	}
	tb, err := os.ReadFile(tp)
	if err != nil {
		return nil, err
	}
	nx, ny := rw.Terrain.Nx, rw.Terrain.Ny
	if nx <= 0 || ny <= 0 || len(tb) < nx*ny*2 {
		return nil, errors.New("bad terrain")
	}
	w := &World{nx: nx, ny: ny, x0: rw.Terrain.MinX, y0: rw.Terrain.MinY, step: rw.Terrain.Step,
		minX: rw.Bounds.MinX, minY: rw.Bounds.MinY, maxX: rw.Bounds.MaxX, maxY: rw.Bounds.MaxY}
	if w.step <= 0 {
		w.step = 1
	}
	scale := rw.Terrain.Scale
	if scale == 0 {
		scale = 0.01
	}
	n := nx * ny
	w.ground = make([]float32, n)
	w.top = make([]float32, n)
	w.ob = make([]float32, n)
	w.ot = make([]float32, n)
	for i := 0; i < n; i++ {
		w.ground[i] = float32(float64(int16(binary.LittleEndian.Uint16(tb[2*i:]))) * scale)
		w.top[i] = negInf
		w.ob[i] = posInf
		w.ot[i] = negInf
	}
	for _, pr := range rw.Prisms {
		w.rasterize(pr)
	}
	return w, nil
}

func inRing(ring []float64, px, py float64) bool {
	n := len(ring) / 2
	in := false
	for k := 0; k < n; k++ {
		k0 := (k + n - 1) % n
		x1, y1 := ring[2*k0], ring[2*k0+1]
		x2, y2 := ring[2*k], ring[2*k+1]
		if y1 == y2 {
			continue
		}
		if (y1 > py) != (y2 > py) {
			if px < (x2-x1)*(py-y1)/(y2-y1)+x1 {
				in = !in
			}
		}
	}
	return in
}

func (w *World) rasterize(pr rawPrism) {
	if len(pr.Ring) < 6 {
		return
	}
	minx, maxx := pr.Ring[0], pr.Ring[0]
	miny, maxy := pr.Ring[1], pr.Ring[1]
	for k := 0; k+1 < len(pr.Ring); k += 2 {
		minx = math.Min(minx, pr.Ring[k])
		maxx = math.Max(maxx, pr.Ring[k])
		miny = math.Min(miny, pr.Ring[k+1])
		maxy = math.Max(maxy, pr.Ring[k+1])
	}
	i0 := imax(0, int(math.Floor((minx-w.x0)/w.step)))
	i1 := imin(w.nx-1, int(math.Ceil((maxx-w.x0)/w.step)))
	j0 := imax(0, int(math.Floor((miny-w.y0)/w.step)))
	j1 := imin(w.ny-1, int(math.Ceil((maxy-w.y0)/w.step)))
	if i0 > i1 || j0 > j1 {
		return
	}
	var cells []int
	for j := j0; j <= j1; j++ {
		py := w.y0 + float64(j)*w.step
		for i := i0; i <= i1; i++ {
			px := w.x0 + float64(i)*w.step
			if !inRing(pr.Ring, px, py) {
				continue
			}
			held := false
			for _, h := range pr.Holes {
				if len(h) >= 6 && inRing(h, px, py) {
					held = true
					break
				}
			}
			if !held {
				cells = append(cells, j*w.nx+i)
			}
		}
	}
	if len(cells) == 0 { // sliver smaller than a cell: mark the cell nearest its center
		var cx, cy float64
		m := len(pr.Ring) / 2
		for k := 0; k < m; k++ {
			cx += pr.Ring[2*k]
			cy += pr.Ring[2*k+1]
		}
		cx /= float64(m)
		cy /= float64(m)
		if inRing(pr.Ring, cx, cy) {
			ci := int(math.Round((cx - w.x0) / w.step))
			cj := int(math.Round((cy - w.y0) / w.step))
			if ci >= 0 && ci < w.nx && cj >= 0 && cj < w.ny {
				cells = append(cells, cj*w.nx+ci)
			}
		}
	}
	base, top := float32(pr.Base), float32(pr.Top)
	for _, c := range cells {
		g := w.ground[c]
		tol := float32(gapMin)
		if pr.Kind == "bridge" {
			tol = 0.05
		}
		if base <= g+tol {
			if top > w.top[c] {
				w.top[c] = top
			}
		} else {
			if math.IsInf(float64(w.ob[c]), 1) {
				w.ob[c], w.ot[c] = base, top
				w.hasOver = true
			} else if base <= w.ot[c] && top >= w.ob[c] {
				w.ob[c] = f32min(w.ob[c], base)
				w.ot[c] = f32max(w.ot[c], top)
			} else if base < w.ob[c] {
				w.ob[c], w.ot[c] = base, top
			}
		}
	}
}

func (w *World) inBounds(x, y float64) bool {
	return x >= w.minX && x <= w.maxX && y >= w.minY && y <= w.maxY
}

func (w *World) cell(x, y float64) int {
	i := int((x-w.x0)/w.step + 0.5)
	j := int((y-w.y0)/w.step + 0.5)
	if i < 0 {
		i = 0
	} else if i >= w.nx {
		i = w.nx - 1
	}
	if j < 0 {
		j = 0
	} else if j >= w.ny {
		j = w.ny - 1
	}
	return j*w.nx + i
}

// groundZ is the bilinear terrain height.
func (w *World) groundZ(x, y float64) float64 {
	fx := (x - w.x0) / w.step
	fy := (y - w.y0) / w.step
	fx = math.Min(math.Max(fx, 0), float64(w.nx-1)-1e-9)
	fy = math.Min(math.Max(fy, 0), float64(w.ny-1)-1e-9)
	i, j := int(fx), int(fy)
	tx, ty := fx-float64(i), fy-float64(j)
	k := j*w.nx + i
	g := w.ground
	a := float64(g[k])*(1-tx) + float64(g[k+1])*tx
	b := float64(g[k+w.nx])*(1-tx) + float64(g[k+w.nx+1])*tx
	return a*(1-ty) + b*ty
}

// solidAt is true strictly inside terrain, a building or an overhang.
func (w *World) solidAt(x, y, z float64) bool {
	c := w.cell(x, y)
	const tol = 1e-3
	if z < w.groundZ(x, y)-tol {
		return true
	}
	if z < float64(w.top[c])-tol {
		return true
	}
	if w.hasOver {
		if z > float64(w.ob[c])+tol && z < float64(w.ot[c])-tol {
			return true
		}
	}
	return false
}

// segBlocked is true when the segment a->b enters a solid (buildings, terrain).
func (w *World) segBlocked(a, b [3]float64) bool {
	dx, dy, dz := b[0]-a[0], b[1]-a[1], b[2]-a[2]
	l := math.Sqrt(dx*dx + dy*dy + dz*dz)
	if l < 1e-6 {
		return false
	}
	n := int(math.Ceil(l / 1.0))
	for k := 1; k <= n; k++ {
		t := float64(k) / float64(n)
		x, y, z := a[0]+dx*t, a[1]+dy*t, a[2]+dz*t
		if !w.inBounds(x, y) {
			continue
		}
		if w.solidAt(x, y, z) {
			return true
		}
	}
	return false
}

// wallAt: a cell a mech cannot walk onto (building/stadium wall taller than a step, or low overhang).
func (w *World) wallAt(c int, stepUp, height float64) bool {
	g := float64(w.ground[c])
	t := float64(w.top[c])
	if t-g > stepUp-0.1 && !math.IsInf(t, -1) {
		return true
	}
	if w.hasOver && !math.IsInf(float64(w.ob[c]), 1) {
		// overhang: blocked unless the mech fits under it
		if float64(w.ob[c])-g < height+0.5 && float64(w.ot[c]) > g {
			return true
		}
	}
	return false
}

func imin(a, b int) int {
	if a < b {
		return a
	}
	return b
}
func imax(a, b int) int {
	if a > b {
		return a
	}
	return b
}
func f32min(a, b float32) float32 {
	if a < b {
		return a
	}
	return b
}
func f32max(a, b float32) float32 {
	if a > b {
		return a
	}
	return b
}
