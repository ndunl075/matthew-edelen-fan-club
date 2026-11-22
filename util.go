package main

import "math"

func hyp2(a, b float64) float64 { return math.Hypot(a, b) }

func dist2(a, b [3]float64) float64 { return math.Hypot(a[0]-b[0], a[1]-b[1]) }

func dist3(a, b [3]float64) float64 {
	dx, dy, dz := a[0]-b[0], a[1]-b[1], a[2]-b[2]
	return math.Sqrt(dx*dx + dy*dy + dz*dz)
}

func wrapAng(a float64) float64 {
	for a > math.Pi {
		a -= 2 * math.Pi
	}
	for a < -math.Pi {
		a += 2 * math.Pi
	}
	return a
}

func clamp(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func fmin(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}

func fmax(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func unit2(x, y float64) (float64, float64) {
	l := math.Hypot(x, y)
	if l < 1e-9 {
		return 0, 0
	}
	return x / l, y / l
}

// aimAt returns yaw, pitch from p to q.
func aimAt(p, q [3]float64) (float64, float64) {
	dx, dy, dz := q[0]-p[0], q[1]-p[1], q[2]-p[2]
	return math.Atan2(dy, dx), math.Atan2(dz, math.Hypot(dx, dy))
}

func finite(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) }
