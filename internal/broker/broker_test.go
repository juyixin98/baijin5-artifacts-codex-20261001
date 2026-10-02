package broker

import (
	"fmt"
	"sync"
	"testing"
	"time"
)

// TestTotalOrderAcrossObservers proves every observer of one mailbox sees the
// same single sequence of events even when many mutators contend.
func TestTotalOrderAcrossObservers(t *testing.T) {
	b := New()
	const observers = 5
	const writers = 8
	const perWriter = 25
	const total = writers * perWriter

	subs := make([]*Subscription, observers)
	for i := range subs {
		subs[i] = b.Subscribe("INBOX")
	}

	var wwg sync.WaitGroup
	for w := 0; w < writers; w++ {
		wwg.Add(1)
		go func(w int) {
			defer wwg.Done()
			for k := 0; k < perWriter; k++ {
				seq := w*perWriter + k + 1
				_, _ = b.Mutate("INBOX", nil, func() ([]Event, error) {
					return []Event{{Kind: EvExpunge, Seq: seq, UID: uint32(seq)}}, nil
				})
			}
		}(w)
	}
	wwg.Wait()

	// Fan-out is asynchronous; wait for every observer queue to hold all
	// events, then drain them.
	deadline := time.Now().Add(2 * time.Second)
	for _, s := range subs {
		for len(s.Events()) < total {
			if time.Now().After(deadline) {
				t.Fatalf("observer stuck at %d/%d", len(s.Events()), total)
			}
			time.Sleep(time.Millisecond)
		}
	}
	got := make([][]string, observers)
	for i, s := range subs {
		for j := 0; j < total; j++ {
			ev := <-s.Events()
			got[i] = append(got[i], fmt.Sprintf("%d:%d", ev.Kind, ev.Seq))
		}
	}
	for _, s := range subs {
		s.Unsubscribe()
	}

	// All observers must record an identical total order.
	ref := stringsJoin(got[0])
	for i := 1; i < observers; i++ {
		if stringsJoin(got[i]) != ref {
			t.Fatalf("observer %d diverged from the reference order", i)
		}
	}
	// Each sequence number appears exactly once.
	seen := map[string]int{}
	for _, e := range got[0] {
		seen[e]++
	}
	if len(seen) != total {
		t.Fatalf("distinct events want %d got %d", total, len(seen))
	}
}

// TestOriginExcluded verifies the acting observer does not receive its own
// events while others do.
func TestOriginExcluded(t *testing.T) {
	b := New()
	origin := b.Subscribe("INBOX")
	other := b.Subscribe("INBOX")
	events, err := b.Mutate("INBOX", origin, func() ([]Event, error) {
		return []Event{{Kind: EvExists, Exists: 7}}, nil
	})
	if err != nil || len(events) != 1 {
		t.Fatalf("mutate: %+v err=%v", events, err)
	}
	select {
	case ev := <-other.Events():
		if ev.Exists != 7 {
			t.Fatalf("other observer got %+v", ev)
		}
	case <-time.After(time.Second):
		t.Fatal("other observer should have received the event")
	}
	select {
	case ev := <-origin.Events():
		t.Fatalf("origin must not see its own event, got %+v", ev)
	default:
	}
}

func stringsJoin(s []string) string {
	out := ""
	for i, x := range s {
		if i > 0 {
			out += "|"
		}
		out += x
	}
	return out
}
