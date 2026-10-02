package transport_test

import (
	"testing"
	"time"

	"coaplab/internal/ids"
	"coaplab/internal/transport"
)

// The FIRST (endpoint, MID) datagram is processed; identical retransmissions
// are classified duplicates and replay the stored response. Different
// endpoints with the same MID are independent (no false dedup).
func TestDedup_FirstThenReplay(t *testing.T) {
	d := transport.NewDedupCache(time.Hour)
	mid := ids.MID(0x1000)

	d1 := d.Observe("10.0.0.1:1111", mid, []byte("first"))
	if d1.Duplicate {
		t.Fatal("first sighting must not be a duplicate")
	}
	d.StoreResponse("10.0.0.1:1111", mid, []byte("response"))

	d2 := d.Observe("10.0.0.1:1111", mid, []byte("first"))
	if !d2.Duplicate || string(d2.Replay) != "response" {
		t.Fatalf("retransmit must replay stored response, got dup=%v replay=%q", d2.Duplicate, d2.Replay)
	}

	// Same MID, different remote endpoint: distinct transaction.
	d3 := d.Observe("10.0.0.2:2222", mid, []byte("other"))
	if d3.Duplicate {
		t.Fatal("MID reuse from a different endpoint is not a duplicate")
	}
}

// A duplicate arriving while the first response is not ready (separate
// response model) asks for another empty ACK, never re-invokes processing.
func TestDedup_DuplicateBeforeResponseReACKs(t *testing.T) {
	d := transport.NewDedupCache(time.Hour)
	mid := ids.MID(7)
	if d.Observe("h", mid, nil).Duplicate {
		t.Fatal("first")
	}
	d.MarkEmptyACK("h", mid)
	dec := d.Observe("h", mid, nil)
	if !dec.Duplicate || !dec.SendEmptyACK || dec.Replay != nil {
		t.Fatalf("want re-ACK without replay, got %+v", dec)
	}
}

func TestDedup_Expiry(t *testing.T) {
	clk := newFakeClock(time.Unix(0, 0))
	d := transport.NewDedupCache(10 * time.Millisecond)
	d.SetClock(clk.now)
	d.Observe("h", 1, nil)
	clk.advance(20 * time.Millisecond)
	if d.Observe("h", 1, nil).Duplicate {
		t.Fatal("entry must expire after TTL")
	}
}

func TestRetransmit_ExponentialBackoffDeterministic(t *testing.T) {
	r := transport.Retransmit{ACKTimeout: 2 * time.Second, ACKRandomFactor: 1.0, MaxRetransmit: 4}
	want := []time.Duration{2 * time.Second, 4 * time.Second, 8 * time.Second, 16 * time.Second, 32 * time.Second}
	for i, w := range want {
		if got := r.Timeout(i, nil); got != w {
			t.Errorf("timeout[%d] = %s, want %s", i, got, w)
		}
	}
	if r.MaxTransmissions() != 5 {
		t.Errorf("max transmissions = %d, want 5", r.MaxTransmissions())
	}
	// Window = 2 * (2^4 - 1) * 1.0 = 30s.
	if w := r.RetransmissionWindow(); w != 30*time.Second {
		t.Errorf("retransmission window = %s, want 30s", w)
	}
}

func TestRetransmit_RejectsFactorBelowOne(t *testing.T) {
	r := transport.Retransmit{ACKTimeout: time.Second, ACKRandomFactor: 0.9}
	if err := r.Validate(); err == nil {
		t.Fatal("ACK_RANDOM_FACTOR < 1.0 must be rejected (RFC 7252 §4.8.1)")
	}
}
