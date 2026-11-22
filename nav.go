package main

import (
	"math"
	"time"
)

const navStep = 3.0 // metres per nav node

var ndx = [8]int{1, 1, 0, -1, -1, -1, 0, 1}
var ndy = [8]int{0, 1, 1, 1, 0, -1, -1, -1}
var nlen = [8]float32{3, 4.243, 3, 4.243, 3, 4.243, 3, 4.243}

type hItem struct {
	f   float32
	idx int32
}

type Nav struct {
	w      *World
	NA, NB int
	Walk   []bool
	Adj    []uint8
	Mul    []float32
	Expo   []float32 // 0 = enclosed, 1 = open to the horizon
	Ring   []float32 // share of directions with a clear line of 55 m or more (where a shooter could stand)

	gen    int32
	stamp  []int32
	cstamp []int32
	g      []float32
	parent []int32
	heap   []hItem
	order  []int32 // nodes reached by the last Reach call
}

func NewNav(w *World) *Nav {
	n := &Nav{w: w}
	n.NA = (w.NX - 2) / 3
	n.NB = (w.NY - 2) / 3
	N := n.NA * n.NB
	n.Walk = make([]bool, N)
	n.Adj = make([]uint8, N)
	n.Mul = make([]float32, N)
	n.Expo = make([]float32, N)
	n.Ring = make([]float32, N)
	n.stamp = make([]int32, N)
	n.cstamp = make([]int32, N)
	n.g = make([]float32, N)
	n.parent = make([]int32, N)
	for b := 0; b < n.NB; b++ {
		for a := 0; a < n.NA; a++ {
			x, y := n.Center(a, b)
			wd := w.WD(x, y)
			id := b*n.NA + a
			if wd >= 2.8 {
				n.Walk[id] = true
				pen := (7 - wd) / 7
				if pen < 0 {
					pen = 0
				}
				n.Mul[id] = float32(1 + 0.8*pen)
			}
		}
	}
	for b := 0; b < n.NB; b++ {
		for a := 0; a < n.NA; a++ {
			id := b*n.NA + a
			if !n.Walk[id] {
				continue
			}
			x, y := n.Center(a, b)
			var m uint8
			for d := 0; d < 8; d++ {
				a2, b2 := a+ndx[d], b+ndy[d]
				if a2 < 0 || b2 < 0 || a2 >= n.NA || b2 >= n.NB {
					continue
				}
				id2 := b2*n.NA + a2
				if !n.Walk[id2] {
					continue
				}
				x2, y2 := n.Center(a2, b2)
				if !w.WalkClear(x, y, x2, y2, 2.4) {
					continue
				}
				// slope between nodes
				if math.Abs(w.SurfZ(x2, y2)-w.SurfZ(x, y)) > 4.2 {
					continue
				}
				m |= 1 << uint(d)
			}
			n.Adj[id] = m
		}
	}
	return n
}

func (n *Nav) Center(a, b int) (float64, float64) {
	return n.w.MinX + 1 + navStep*float64(a), n.w.MinY + 1 + navStep*float64(b)
}

func (n *Nav) NodeOf(x, y float64) (int, int) {
	a := int(math.Floor((x-n.w.MinX-1)/navStep + 0.5))
	b := int(math.Floor((y-n.w.MinY-1)/navStep + 0.5))
	if a < 0 {
		a = 0
	} else if a >= n.NA {
		a = n.NA - 1
	}
	if b < 0 {
		b = 0
	} else if b >= n.NB {
		b = n.NB - 1
	}
	return a, b
}

// Snap returns the walkable node nearest to (x,y), or -1.
func (n *Nav) Snap(x, y float64) int {
	a, b := n.NodeOf(x, y)
	if n.Walk[b*n.NA+a] {
		return b*n.NA + a
	}
	best, bd := -1, 1e18
	for r := 1; r <= 8; r++ {
		for db := -r; db <= r; db++ {
			for da := -r; da <= r; da++ {
				if iabs(da) != r && iabs(db) != r {
					continue
				}
				a2, b2 := a+da, b+db
				if a2 < 0 || b2 < 0 || a2 >= n.NA || b2 >= n.NB {
					continue
				}
				id := b2*n.NA + a2
				if !n.Walk[id] {
					continue
				}
				cx, cy := n.Center(a2, b2)
				d := (cx-x)*(cx-x) + (cy-y)*(cy-y)
				if d < bd {
					bd, best = d, id
				}
			}
		}
		if best >= 0 {
			return best
		}
	}
	return -1
}

func iabs(a int) int {
	if a < 0 {
		return -a
	}
	return a
}

// ---- binary heap ----

func (n *Nav) hpush(it hItem) {
	n.heap = append(n.heap, it)
	i := len(n.heap) - 1
	for i > 0 {
		p := (i - 1) / 2
		if n.heap[p].f <= n.heap[i].f {
			break
		}
		n.heap[p], n.heap[i] = n.heap[i], n.heap[p]
		i = p
	}
}

func (n *Nav) hpop() hItem {
	top := n.heap[0]
	last := len(n.heap) - 1
	n.heap[0] = n.heap[last]
	n.heap = n.heap[:last]
	i := 0
	for {
		l, r := 2*i+1, 2*i+2
		m := i
		if l < last && n.heap[l].f < n.heap[m].f {
			m = l
		}
		if r < last && n.heap[r].f < n.heap[m].f {
			m = r
		}
		if m == i {
			break
		}
		n.heap[m], n.heap[i] = n.heap[i], n.heap[m]
		i = m
	}
	return top
}

// Path runs A* between two points and returns smoothed waypoints (excluding the start).
// extra, when not nil, adds a per-node cost multiplier (>=1).
func (n *Nav) Path(sx, sy, gx, gy float64, deadline time.Time, extra func(id int) float32) [][2]float64 {
	s := n.Snap(sx, sy)
	t := n.Snap(gx, gy)
	if s < 0 || t < 0 {
		return nil
	}
	n.gen++
	n.heap = n.heap[:0]
	tx, ty := n.Center(t%n.NA, t/n.NA)
	n.stamp[s] = n.gen
	n.g[s] = 0
	n.parent[s] = -1
	n.hpush(hItem{0, int32(s)})
	found := false
	iter := 0
	best, bestH := int32(s), float32(1e18)
	for len(n.heap) > 0 {
		it := n.hpop()
		id := int(it.idx)
		a, b := id%n.NA, id/n.NA
		cx, cy := n.Center(a, b)
		h := float32(math.Hypot(cx-tx, cy-ty))
		if it.f-h > n.g[id]+0.01 && n.stamp[id] == n.gen {
			// stale entry (g improved since push)
			continue
		}
		if id == t {
			found = true
			break
		}
		if h < bestH {
			bestH, best = h, it.idx
		}
		iter++
		if iter&255 == 0 && time.Now().After(deadline) {
			break
		}
		m := n.Adj[id]
		for d := 0; d < 8; d++ {
			if m&(1<<uint(d)) == 0 {
				continue
			}
			id2 := (b+ndy[d])*n.NA + (a + ndx[d])
			mul := n.Mul[id2]
			if extra != nil {
				mul *= extra(id2)
			}
			ng := n.g[id] + nlen[d]*mul
			if n.stamp[id2] != n.gen || ng < n.g[id2] {
				n.stamp[id2] = n.gen
				n.g[id2] = ng
				n.parent[id2] = int32(id)
				cx2, cy2 := n.Center(a+ndx[d], b+ndy[d])
				h2 := float32(math.Hypot(cx2-tx, cy2-ty))
				n.hpush(hItem{ng + h2, int32(id2)})
			}
		}
	}
	end := t
	if !found {
		end = int(best)
		if end == s {
			return nil
		}
	}
	// reconstruct
	var rev []int32
	for c := int32(end); c >= 0; c = n.parent[c] {
		rev = append(rev, c)
		if len(rev) > 20000 {
			break
		}
	}
	pts := make([][2]float64, 0, len(rev))
	for i := len(rev) - 2; i >= 0; i-- { // skip start node
		x, y := n.Center(int(rev[i])%n.NA, int(rev[i])/n.NA)
		pts = append(pts, [2]float64{x, y})
	}
	if found {
		pts = append(pts, [2]float64{gx, gy})
	}
	return n.smooth(sx, sy, pts)
}

// smooth drops waypoints that can be skipped with a clear straight walk.
func (n *Nav) smooth(sx, sy float64, pts [][2]float64) [][2]float64 {
	if len(pts) <= 1 {
		return pts
	}
	out := make([][2]float64, 0, len(pts)/2+1)
	cx, cy := sx, sy
	i := 0
	for i < len(pts) {
		j := i
		limit := i + 40
		if limit > len(pts)-1 {
			limit = len(pts) - 1
		}
		for k := limit; k > i; k-- {
			if n.w.WalkClear(cx, cy, pts[k][0], pts[k][1], 2.5) {
				j = k
				break
			}
		}
		out = append(out, pts[j])
		cx, cy = pts[j][0], pts[j][1]
		i = j + 1
	}
	return out
}

// Reach explores outward from (x,y) up to maxDist of path length (Dijkstra) and returns the
// visited node ids in order of distance. Distances are read with Dist, paths with Trace.
func (n *Nav) Reach(x, y, maxDist float64, deadline time.Time) []int32 {
	s := n.Snap(x, y)
	n.order = n.order[:0]
	if s < 0 {
		return n.order
	}
	n.gen++
	n.heap = n.heap[:0]
	n.stamp[s] = n.gen
	n.g[s] = 0
	n.parent[s] = -1
	n.hpush(hItem{0, int32(s)})
	iter := 0
	for len(n.heap) > 0 {
		it := n.hpop()
		id := it.idx
		if n.cstamp[id] == n.gen {
			continue
		}
		n.cstamp[id] = n.gen
		n.order = append(n.order, id)
		iter++
		if iter&255 == 0 && time.Now().After(deadline) {
			break
		}
		a, b := int(id)%n.NA, int(id)/n.NA
		m := n.Adj[id]
		for d := 0; d < 8; d++ {
			if m&(1<<uint(d)) == 0 {
				continue
			}
			id2 := int32((b+ndy[d])*n.NA + (a + ndx[d]))
			ng := n.g[id] + nlen[d]*n.Mul[id2]
			if ng > float32(maxDist) {
				continue
			}
			if n.stamp[id2] != n.gen || ng < n.g[id2] {
				n.stamp[id2] = n.gen
				n.g[id2] = ng
				n.parent[id2] = id
				n.hpush(hItem{ng, id2})
			}
		}
	}
	return n.order
}

func (n *Nav) Dist(id int32) float32 { return n.g[id] }

// Trace returns the waypoints from the Reach origin to node id (excluding origin).
func (n *Nav) Trace(id int32) [][2]float64 {
	var rev []int32
	for c := id; c >= 0; c = n.parent[c] {
		rev = append(rev, c)
		if len(rev) > 4000 {
			break
		}
	}
	pts := make([][2]float64, 0, len(rev))
	for i := len(rev) - 2; i >= 0; i-- {
		x, y := n.Center(int(rev[i])%n.NA, int(rev[i])/n.NA)
		pts = append(pts, [2]float64{x, y})
	}
	return pts
}

// BuildExposure computes, for each walkable node, how open the surroundings are: the mean over
// 16 directions of (free line of sight at eye height up to 80 m) / 80 m.
func (n *Nav) BuildExposure(budget time.Duration) {
	w := n.w
	deadline := time.Now().Add(budget)
	const dirs = 16
	var cs, sn [dirs]float64
	for k := 0; k < dirs; k++ {
		a := 2 * math.Pi * float64(k) / dirs
		cs[k], sn[k] = math.Cos(a), math.Sin(a)
	}
	for id := range n.Expo {
		n.Expo[id] = 0.5
		n.Ring[id] = 0.5
	}
	for id := range n.Walk {
		if !n.Walk[id] {
			continue
		}
		if id&1023 == 0 && time.Now().After(deadline) {
			return
		}
		x, y := n.Center(id%n.NA, id/n.NA)
		z := w.SurfZ(x, y) + 8
		sum := 0.0
		ring := 0
		for k := 0; k < dirs; k++ {
			f := w.FirstBlock(x, y, z, cs[k], sn[k], 80)
			sum += f / 80
			if f >= 55 {
				ring++
			}
		}
		n.Expo[id] = float32(sum / dirs)
		n.Ring[id] = float32(ring) / dirs
	}
}
