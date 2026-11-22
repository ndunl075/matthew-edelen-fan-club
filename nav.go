package main

import (
	"math"
	"time"
)

// Nav is a coarse walkability grid (2 m cells) with A* path finding.
type Nav struct {
	w          *World
	nx, ny     int
	ox, oy, cs float64
	free       []bool
	near       []bool // within a few meters of a wall: costs more
	comp       []int32
	pen        map[int]float32 // temporary extra cost (stuck spots)

	// A* scratch
	g      []float32
	parent []int32
	gen    []uint32
	closed []uint32
	cur    uint32
	heap   []heapItem
}

type heapItem struct {
	f float32
	c int32
}

func buildNav(w *World, mc MechCfg) *Nav {
	cs := 2.0
	nv := &Nav{w: w, cs: cs, ox: w.minX, oy: w.minY, pen: map[int]float32{}}
	nv.nx = int(math.Ceil((w.maxX-w.minX)/cs)) + 1
	nv.ny = int(math.Ceil((w.maxY-w.minY)/cs)) + 1
	// fine blocked grid
	blk := make([]bool, w.nx*w.ny)
	stepUp := mc.StepUp
	if stepUp <= 0 {
		stepUp = 1.2
	}
	height := mc.Height
	if height <= 0 {
		height = 9
	}
	slope := math.Tan(mc.MaxSlopeDeg*math.Pi/180) * 0.95
	if mc.MaxSlopeDeg <= 0 {
		slope = 1.1
	}
	for j := 0; j < w.ny; j++ {
		for i := 0; i < w.nx; i++ {
			c := j*w.nx + i
			if w.wallAt(c, stepUp, height) {
				blk[c] = true
				continue
			}
			g := float64(w.ground[c])
			if i+1 < w.nx && math.Abs(float64(w.ground[c+1])-g)/w.step > slope {
				blk[c] = true
			} else if j+1 < w.ny && math.Abs(float64(w.ground[c+w.nx])-g)/w.step > slope {
				blk[c] = true
			}
		}
	}
	rad := mc.Radius
	if rad <= 0 {
		rad = 2.5
	}
	rFree := rad + 0.3
	rNear := rad + 3.5
	n := nv.nx * nv.ny
	nv.free = make([]bool, n)
	nv.near = make([]bool, n)
	test := func(cx, cy, r float64) bool { // true if any blocked fine cell within r
		i0 := int(math.Floor((cx - r - w.x0) / w.step))
		i1 := int(math.Ceil((cx + r - w.x0) / w.step))
		j0 := int(math.Floor((cy - r - w.y0) / w.step))
		j1 := int(math.Ceil((cy + r - w.y0) / w.step))
		r2 := r * r
		for j := j0; j <= j1; j++ {
			for i := i0; i <= i1; i++ {
				if i < 0 || j < 0 || i >= w.nx || j >= w.ny {
					continue
				}
				dx := w.x0 + float64(i)*w.step - cx
				dy := w.y0 + float64(j)*w.step - cy
				if dx*dx+dy*dy <= r2 && blk[j*w.nx+i] {
					return true
				}
			}
		}
		return false
	}
	for j := 0; j < nv.ny; j++ {
		for i := 0; i < nv.nx; i++ {
			cx, cy := nv.center(i, j)
			c := j*nv.nx + i
			if cx < w.minX+rad || cx > w.maxX-rad || cy < w.minY+rad || cy > w.maxY-rad {
				continue
			}
			if test(cx, cy, rFree) {
				continue
			}
			nv.free[c] = true
			nv.near[c] = test(cx, cy, rNear)
		}
	}
	nv.label()
	nv.g = make([]float32, n)
	nv.parent = make([]int32, n)
	nv.gen = make([]uint32, n)
	nv.closed = make([]uint32, n)
	return nv
}

func (nv *Nav) center(i, j int) (float64, float64) {
	return nv.ox + (float64(i)+0.5)*nv.cs, nv.oy + (float64(j)+0.5)*nv.cs
}

func (nv *Nav) cellOf(x, y float64) (int, int) {
	i := int((x - nv.ox) / nv.cs)
	j := int((y - nv.oy) / nv.cs)
	if i < 0 {
		i = 0
	} else if i >= nv.nx {
		i = nv.nx - 1
	}
	if j < 0 {
		j = 0
	} else if j >= nv.ny {
		j = nv.ny - 1
	}
	return i, j
}

func (nv *Nav) idx(x, y float64) int {
	i, j := nv.cellOf(x, y)
	return j*nv.nx + i
}

func (nv *Nav) isFree(x, y float64) bool {
	return nv.free[nv.idx(x, y)]
}

func (nv *Nav) label() {
	n := nv.nx * nv.ny
	nv.comp = make([]int32, n)
	var id int32
	stack := make([]int32, 0, 1024)
	for s := 0; s < n; s++ {
		if !nv.free[s] || nv.comp[s] != 0 {
			continue
		}
		id++
		nv.comp[s] = id
		stack = append(stack[:0], int32(s))
		for len(stack) > 0 {
			c := int(stack[len(stack)-1])
			stack = stack[:len(stack)-1]
			ci, cj := c%nv.nx, c/nv.nx
			for d := 0; d < 4; d++ {
				ni, nj := ci+dirs4[d][0], cj+dirs4[d][1]
				if ni < 0 || nj < 0 || ni >= nv.nx || nj >= nv.ny {
					continue
				}
				nc := nj*nv.nx + ni
				if nv.free[nc] && nv.comp[nc] == 0 {
					nv.comp[nc] = id
					stack = append(stack, int32(nc))
				}
			}
		}
	}
}

var dirs4 = [4][2]int{{1, 0}, {-1, 0}, {0, 1}, {0, -1}}
var dirs8 = [8][2]int{{1, 0}, {-1, 0}, {0, 1}, {0, -1}, {1, 1}, {1, -1}, {-1, 1}, {-1, -1}}

// nearestFree finds the free cell closest to (x,y), optionally in component `want` (0 = any).
func (nv *Nav) nearestFree(x, y float64, want int32, maxR int) (int, bool) {
	ci, cj := nv.cellOf(x, y)
	c0 := cj*nv.nx + ci
	if nv.free[c0] && (want == 0 || nv.comp[c0] == want) {
		return c0, true
	}
	best, bestD := -1, math.MaxFloat64
	for r := 1; r <= maxR; r++ {
		for dj := -r; dj <= r; dj++ {
			for di := -r; di <= r; di++ {
				if imax(iabs(di), iabs(dj)) != r {
					continue
				}
				ni, nj := ci+di, cj+dj
				if ni < 0 || nj < 0 || ni >= nv.nx || nj >= nv.ny {
					continue
				}
				c := nj*nv.nx + ni
				if !nv.free[c] || (want != 0 && nv.comp[c] != want) {
					continue
				}
				px, py := nv.center(ni, nj)
				d := (px-x)*(px-x) + (py-y)*(py-y)
				if d < bestD {
					best, bestD = c, d
				}
			}
		}
		if best >= 0 {
			return best, true
		}
	}
	return -1, false
}

func iabs(a int) int {
	if a < 0 {
		return -a
	}
	return a
}

// lineFree checks a straight walk on the nav grid.
func (nv *Nav) lineFree(ax, ay, bx, by float64) bool {
	dx, dy := bx-ax, by-ay
	l := math.Hypot(dx, dy)
	n := int(l/1.0) + 1
	for k := 1; k <= n; k++ {
		t := float64(k) / float64(n)
		if !nv.free[nv.idx(ax+dx*t, ay+dy*t)] {
			return false
		}
	}
	return true
}

// findPath runs A* from (sx,sy) to (gx,gy). Returns waypoints (cell centers) or nil.
// Gives up after the time budget or expansion cap.
func (nv *Nav) findPath(sx, sy, gx, gy float64, budget time.Duration) [][2]float64 {
	si, sj := nv.cellOf(sx, sy)
	s := sj*nv.nx + si
	if !nv.free[s] {
		var ok bool
		s, ok = nv.nearestFree(sx, sy, 0, 12)
		if !ok {
			return nil
		}
	}
	tgt, ok := nv.nearestFree(gx, gy, nv.comp[s], 60)
	if !ok {
		return nil
	}
	if s == tgt {
		x, y := nv.center(tgt%nv.nx, tgt/nv.nx)
		return [][2]float64{{x, y}}
	}
	start := time.Now()
	nv.cur++
	cur := nv.cur
	ti, tj := tgt%nv.nx, tgt/nv.nx
	h := func(i, j int) float32 {
		dx := float32(iabs(i - ti))
		dy := float32(iabs(j - tj))
		mn, mx := dx, dy
		if mn > mx {
			mn, mx = mx, mn
		}
		return (mx + 0.414*mn) * 1.12
	}
	nv.heap = nv.heap[:0]
	nv.g[s] = 0
	nv.gen[s] = cur
	nv.parent[s] = -1
	nv.hpush(heapItem{h(s%nv.nx, s/nv.nx), int32(s)})
	exp := 0
	found := false
	bestC, bestH := s, h(s%nv.nx, s/nv.nx)
	for len(nv.heap) > 0 {
		it := nv.hpop()
		c := int(it.c)
		if nv.closed[c] == cur {
			continue
		}
		nv.closed[c] = cur
		if c == tgt {
			found = true
			break
		}
		exp++
		if exp&1023 == 0 && time.Since(start) > budget {
			break
		}
		ci, cj := c%nv.nx, c/nv.nx
		if hh := h(ci, cj); hh < bestH {
			bestH, bestC = hh, c
		}
		for d := 0; d < 8; d++ {
			ni, nj := ci+dirs8[d][0], cj+dirs8[d][1]
			if ni < 0 || nj < 0 || ni >= nv.nx || nj >= nv.ny {
				continue
			}
			nc := nj*nv.nx + ni
			if !nv.free[nc] || nv.closed[nc] == cur {
				continue
			}
			cost := float32(1)
			if d >= 4 {
				if !nv.free[cj*nv.nx+ni] || !nv.free[nj*nv.nx+ci] {
					continue
				}
				cost = 1.414
			}
			if nv.near[nc] {
				cost *= 1.5
			}
			if len(nv.pen) > 0 {
				if p, ok := nv.pen[nc]; ok {
					cost += p
				}
			}
			ng := nv.g[c] + cost
			if nv.gen[nc] != cur || ng < nv.g[nc] {
				nv.gen[nc] = cur
				nv.g[nc] = ng
				nv.parent[nc] = int32(c)
				nv.hpush(heapItem{ng + h(ni, nj), int32(nc)})
			}
		}
	}
	end := tgt
	if !found {
		end = bestC // partial path toward the goal
		if end == s {
			return nil
		}
	}
	var rev [][2]float64
	for c := end; c >= 0; c = int(nv.parent[c]) {
		x, y := nv.center(c%nv.nx, c/nv.nx)
		rev = append(rev, [2]float64{x, y})
		if nv.parent[c] < 0 {
			break
		}
	}
	for i, j := 0, len(rev)-1; i < j; i, j = i+1, j-1 {
		rev[i], rev[j] = rev[j], rev[i]
	}
	return rev
}

func (nv *Nav) hpush(it heapItem) {
	nv.heap = append(nv.heap, it)
	i := len(nv.heap) - 1
	for i > 0 {
		p := (i - 1) / 2
		if nv.heap[p].f <= nv.heap[i].f {
			break
		}
		nv.heap[p], nv.heap[i] = nv.heap[i], nv.heap[p]
		i = p
	}
}

func (nv *Nav) hpop() heapItem {
	top := nv.heap[0]
	last := len(nv.heap) - 1
	nv.heap[0] = nv.heap[last]
	nv.heap = nv.heap[:last]
	i := 0
	for {
		l, r := 2*i+1, 2*i+2
		m := i
		if l < last && nv.heap[l].f < nv.heap[m].f {
			m = l
		}
		if r < last && nv.heap[r].f < nv.heap[m].f {
			m = r
		}
		if m == i {
			break
		}
		nv.heap[m], nv.heap[i] = nv.heap[i], nv.heap[m]
		i = m
	}
	return top
}
