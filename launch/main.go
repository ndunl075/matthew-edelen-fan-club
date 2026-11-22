// Starts the practice engine before the practice board closes and the final-round engine after it.
// MECH_FINAL=1 / 0 overrides the clock (tests).
package main

import (
	"os"
	"syscall"
	"time"
)

func main() {
	practice, final := "/usr/local/bin/az", "/usr/local/bin/glen"
	bin := practice
	f := os.Getenv("MECH_FINAL")
	if f == "1" || (f != "0" && time.Now().Unix() >= 1795363200) { // 2026-11-22 16:00 UTC
		bin = final
	}
	_ = syscall.Exec(bin, []string{bin}, os.Environ())
	// the chosen engine could not start: try the other one rather than exit
	if bin == practice {
		bin = final
	} else {
		bin = practice
	}
	_ = syscall.Exec(bin, []string{bin}, os.Environ())
	os.Exit(1)
}
