package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"runtime"
	"runtime/debug"
	"sync"
	"time"
)

const botName = "vcv"

// inbox keeps every pending line; the main loop answers only the newest observation.
type inbox struct {
	mu    sync.Mutex
	cond  *sync.Cond
	lines [][]byte
	done  bool
}

func newInbox() *inbox {
	in := &inbox{}
	in.cond = sync.NewCond(&in.mu)
	return in
}

func (in *inbox) run() {
	r := bufio.NewReaderSize(os.Stdin, 1<<20)
	for {
		line, err := r.ReadBytes('\n')
		if len(bytes.TrimSpace(line)) > 0 {
			in.mu.Lock()
			in.lines = append(in.lines, line)
			in.mu.Unlock()
			in.cond.Signal()
		}
		if err != nil {
			in.mu.Lock()
			in.done = true
			in.mu.Unlock()
			in.cond.Broadcast()
			return
		}
	}
}

// next returns all queued lines (blocking until at least one, or EOF).
func (in *inbox) next() ([][]byte, bool) {
	in.mu.Lock()
	defer in.mu.Unlock()
	for len(in.lines) == 0 && !in.done {
		in.cond.Wait()
	}
	if len(in.lines) == 0 {
		return nil, false
	}
	l := in.lines
	in.lines = nil
	return l, true
}

func isObs(b []byte) bool {
	return bytes.HasPrefix(b, []byte(`{"type":"obs"`)) || bytes.HasPrefix(b, []byte(`{"type": "obs"`))
}

func peekType(b []byte) string {
	var t struct {
		Type string `json:"type"`
	}
	_ = json.Unmarshal(b, &t)
	return t.Type
}

func logf(format string, a ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", a...)
}

func main() {
	debug.SetGCPercent(400)
	runtime.GOMAXPROCS(1)
	in := newInbox()
	go in.run()
	out := bufio.NewWriterSize(os.Stdout, 1<<16)
	send := func(v any) {
		b, err := json.Marshal(v)
		if err != nil {
			return
		}
		out.Write(b)
		out.WriteByte('\n')
		out.Flush()
	}

	var brain *Brain
	var lastSent Action
	var busy chan struct{} // closed when the previous worker finished
	ended := false
	for !ended {
		lines, ok := in.next()
		if !ok {
			return
		}
		// pick the newest observation; handle other message types in order
		var newest []byte
		for _, l := range lines {
			if isObs(l) {
				newest = l
				continue
			}
			switch peekType(l) {
			case "hello":
				var h Hello
				if err := json.Unmarshal(l, &h); err != nil {
					logf("bad hello: %v", err)
				}
				brain = safeNewBrain(&h)
				send(map[string]any{"type": "ready", "name": botName})
			case "obs":
				newest = l
			case "end":
				ended = true
			}
		}
		if newest != nil && brain != nil && !ended {
			var o Obs
			if err := json.Unmarshal(newest, &o); err != nil {
				continue
			}
			if busy != nil {
				<-busy // previous decision still running: let it finish before touching brain state
			}
			start := time.Now()
			res := make(chan Action, 1)
			done := make(chan struct{})
			busy = done
			go func(o Obs) {
				defer close(done)
				res <- brain.safeDecide(&o)
			}(o)
			var act Action
			timer := time.NewTimer(34 * time.Millisecond)
			select {
			case act = <-res:
				timer.Stop()
			case <-timer.C:
				// late: repeat the last move/aim/fire with the new tick; drop one-shot commands
				act = lastSent
				act.Tick = o.Tick
				act.Pick, act.Take, act.Drop, act.Debug = nil, nil, nil, nil
				act.Reload = false
				act.Jump = false
				logf("watchdog fallback tick %d after %v", o.Tick, time.Since(start))
			}
			lastSent = act
			send(act)
		}
	}
}

func safeNewBrain(h *Hello) (b *Brain) {
	defer func() {
		if r := recover(); r != nil {
			logf("panic in hello: %v\n%s", r, debug.Stack())
			if b == nil {
				b = NewBrainLite(h)
			}
		}
	}()
	return NewBrain(h)
}

func (b *Brain) safeDecide(o *Obs) (act Action) {
	start := time.Now()
	defer func() {
		if r := recover(); r != nil {
			logf("panic in decide: %v\n%s", r, debug.Stack())
			act = b.fallback(o)
		}
		act.Tick = o.Tick
		act.sanitize()
		if d := time.Since(start); d > 40*time.Millisecond {
			logf("slow decision %v at tick %d (spot %v path %v mode %s)", d, o.Tick, b.tSpot, b.tPath, b.mode)
		}
	}()
	act = b.Decide(o, start)
	return act
}
