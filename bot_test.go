package main

import (
	"math"
	"os"
	"testing"
	"time"
)

func testWorld(t *testing.T) (*World, *Nav) {
	dir := os.Getenv("MAP_DIR")
	if dir == "" {
		t.Skip("MAP_DIR not set")
	}
	t0 := time.Now()
	w, err := loadWorld(dir, []string{"v2/world.json", "v2/terrain.i16"})
	if err != nil {
		t.Fatal(err)
	}
	t1 := time.Now()
	nv := buildNav(w, MechCfg{Radius: 2.5, Height: 9, StepUp: 1.2, MaxSlopeDeg: 50})
	t.Logf("world load %v, nav build %v, nav %dx%d", t1.Sub(t0), time.Since(t1), nv.nx, nv.ny)
	return w, nv
}

func TestNavPaths(t *testing.T) {
	w, nv := testWorld(t)
	free := 0
	for _, f := range nv.free {
		if f {
			free++
		}
	}
	t.Logf("free cells %d of %d", free, len(nv.free))
	// components
	counts := map[int32]int{}
	for i, c := range nv.comp {
		if nv.free[i] {
			counts[c]++
		}
	}
	big := 0
	for _, n := range counts {
		if n > big {
			big = n
		}
	}
	t.Logf("components %d, biggest %d", len(counts), big)
	pairs := [][4]float64{{41, -72, 200, 100}, {-300, -200, 300, 300}, {-400, 300, 400, -250}, {0, 0, 500, 350}}
	for _, p := range pairs {
		t0 := time.Now()
		path := nv.findPath(p[0], p[1], p[2], p[3], 50*time.Millisecond)
		el := time.Since(t0)
		if len(path) == 0 {
			t.Logf("no path %v", p)
			continue
		}
		l := 0.0
		for i := 1; i < len(path); i++ {
			l += math.Hypot(path[i][0]-path[i-1][0], path[i][1]-path[i-1][1])
		}
		t.Logf("path %v: %d nodes, len %.0f (straight %.0f) in %v", p, len(path), l, math.Hypot(p[2]-p[0], p[3]-p[1]), el)
		if el > 20*time.Millisecond {
			t.Errorf("A* too slow: %v", el)
		}
	}
	// LOS sanity: ground-level ray in open ground is clear, ray through a stadium wall is not
	if w.segBlocked([3]float64{0, 0, 50}, [3]float64{10, 0, 50}) {
		t.Errorf("sky ray blocked")
	}
}

func TestLOSSpeed(t *testing.T) {
	w, _ := testWorld(t)
	t0 := time.Now()
	n := 0
	for i := 0; i < 2000; i++ {
		x, y := float64(i%50)*10-200, float64(i/50)*10-150
		a := [3]float64{x, y, w.groundZ(x, y) + 5}
		b := [3]float64{x + 150, y + 60, w.groundZ(x+150, y+60) + 5}
		if w.segBlocked(a, b) {
			n++
		}
	}
	t.Logf("2000 rays in %v, %d blocked", time.Since(t0), n)
}

func TestLeadPoint(t *testing.T) {
	mz := [3]float64{0, 0, 7}
	p := leadPoint(mz, [3]float64{100, 0, 4}, [3]float64{0, 10, 0}, 252)
	if p[1] < 3 || p[1] > 6 {
		t.Errorf("lead y = %v", p[1])
	}
}
