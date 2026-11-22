package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"runtime"
	"runtime/debug"
	"sync"
	"time"
)

const botName = "vcv"

type inbox struct {
	mu     sync.Mutex
	cond   *sync.Cond
	lines  [][]byte
	closed bool
}

func newInbox() *inbox {
	ib := &inbox{}
	ib.cond = sync.NewCond(&ib.mu)
	return ib
}

func (ib *inbox) push(l []byte) {
	ib.mu.Lock()
	ib.lines = append(ib.lines, l)
	ib.mu.Unlock()
	ib.cond.Signal()
}

func (ib *inbox) close() {
	ib.mu.Lock()
	ib.closed = true
	ib.mu.Unlock()
	ib.cond.Broadcast()
}

// take blocks until something is queued and returns everything queued.
func (ib *inbox) take() ([][]byte, bool) {
	ib.mu.Lock()
	defer ib.mu.Unlock()
	for len(ib.lines) == 0 && !ib.closed {
		ib.cond.Wait()
	}
	out := ib.lines
	ib.lines = nil
	return out, ib.closed && len(out) == 0
}

func main() {
	debug.SetGCPercent(400)
	runtime.GOMAXPROCS(2)
	out := bufio.NewWriterSize(os.Stdout, 1<<16)
	send := func(b []byte) {
		out.Write(b)
		out.Flush()
	}
	rd := bufio.NewReaderSize(os.Stdin, 1<<20)

	var helloLine []byte
	for {
		l, err := rd.ReadBytes('\n')
		if len(bytes.TrimSpace(l)) > 0 {
			helloLine = l
			break
		}
		if err != nil {
			return
		}
	}
	var hello Hello
	if err := json.Unmarshal(helloLine, &hello); err != nil {
		fmt.Fprintln(os.Stderr, "bad hello:", err)
	}
	brain := safeNewBrain(&hello)
	send(marshalLine(Ready{Type: "ready", Name: botName}))

	ib := newInbox()
	go func() {
		for {
			l, err := rd.ReadBytes('\n')
			if len(bytes.TrimSpace(l)) > 0 {
				ib.push(l)
			}
			if err != nil {
				if err != io.EOF {
					fmt.Fprintln(os.Stderr, "read:", err)
				}
				ib.close()
				return
			}
		}
	}()

	for {
		lines, done := ib.take()
		if done {
			return
		}
		var last []byte
		for _, l := range lines {
			if bytes.Contains(l, []byte(`"type":"end"`)) || bytes.Contains(l, []byte(`"type": "end"`)) {
				return
			}
			if bytes.Contains(l, []byte(`"obs"`)) {
				last = l
			}
		}
		if last == nil {
			continue
		}
		t0 := time.Now()
		act := brain.handle(last)
		if act != nil {
			send(marshalLine(act))
		}
		if el := time.Since(t0); el > 35*time.Millisecond {
			fmt.Fprintf(os.Stderr, "slow decision: %v tick=%d %s\n", el, brain.tick, brain.label)
		}
	}
}
