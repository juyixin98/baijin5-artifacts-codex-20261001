package client

import (
	"testing"
	"time"

	"stunlab/internal/stun"
)

func makePacket(txn stun.TransactionID) []byte {
	b := make([]byte, 20)
	// Binding request, zero length, magic cookie.
	b[0], b[1] = 0x00, 0x01
	b[4], b[5], b[6], b[7] = 0x21, 0x12, 0xa4, 0x42
	copy(b[8:20], txn[:])
	return b
}

func TestTransactionDeliverMatchedOnce(t *testing.T) {
	tbl := NewTransactionTable(4)
	id := stun.TransactionID{1, 2, 3}
	ch, err := tbl.Add(id, "127.0.0.1:4000", time.Second)
	if err != nil {
		t.Fatal(err)
	}
	pkt := &inbound{raw: makePacket(id), source: "127.0.0.1:4000"}
	if v := tbl.Deliver(pkt); v != "matched" {
		t.Fatalf("first deliver = %q, want matched", v)
	}
	// One-shot: a replayed copy must be stale and must not block/deliver again.
	if v := tbl.Deliver(pkt); v != "stale" {
		t.Fatalf("replay deliver = %q, want stale", v)
	}
	if tbl.Size() != 0 {
		t.Fatalf("table size = %d, want 0", tbl.Size())
	}
	select {
	case got := <-ch:
		if got.source != pkt.source {
			t.Fatal("wrong packet handed to waiter")
		}
	default:
		t.Fatal("waiter channel did not receive packet")
	}
}

func TestTransactionSourceMismatchDoesNotComplete(t *testing.T) {
	tbl := NewTransactionTable(4)
	id := stun.TransactionID{9}
	ch, _ := tbl.Add(id, "127.0.0.1:4000", time.Second)

	// Same transaction id, different source: an old/forged reply from another
	// address. It must neither complete the request nor consume it.
	v := tbl.Deliver(&inbound{raw: makePacket(id), source: "10.0.0.9:5555"})
	if v != "source_mismatch" {
		t.Fatalf("verdict = %q, want source_mismatch", v)
	}
	if tbl.Size() != 1 {
		t.Fatalf("entry consumed by mismatched reply, size=%d", tbl.Size())
	}
	select {
	case <-ch:
		t.Fatal("mismatched reply completed the waiter")
	default:
	}

	// The genuine source still completes it.
	if v := tbl.Deliver(&inbound{raw: makePacket(id), source: "127.0.0.1:4000"}); v != "matched" {
		t.Fatalf("genuine reply verdict = %q, want matched", v)
	}
}

func TestTransactionStaleUnknownID(t *testing.T) {
	tbl := NewTransactionTable(4)
	old := stun.TransactionID{0xaa}
	new := stun.TransactionID{0xbb}
	chNew, _ := tbl.Add(new, "127.0.0.1:1", time.Second)

	// A late datagram for an already-finished/never-seen transaction.
	if v := tbl.Deliver(&inbound{raw: makePacket(old), source: "127.0.0.1:1"}); v != "stale" {
		t.Fatalf("verdict = %q, want stale", v)
	}
	// And it must not have completed the new request.
	select {
	case <-chNew:
		t.Fatal("old response completed the new request")
	default:
	}
}

func TestTransactionAddDuplicateAndCapacity(t *testing.T) {
	tbl := NewTransactionTable(2)
	id := stun.TransactionID{1}
	if _, err := tbl.Add(id, "a", time.Second); err != nil {
		t.Fatal(err)
	}
	if _, err := tbl.Add(id, "a", time.Second); stun.ErrorOf(err) != stun.KindStateConflict {
		t.Fatalf("duplicate add: %v", err)
	}
	if _, err := tbl.Add(stun.TransactionID{2}, "a", time.Second); err != nil {
		t.Fatal(err)
	}
	if _, err := tbl.Add(stun.TransactionID{3}, "a", time.Second); stun.ErrorOf(err) != stun.KindResourceExhausted {
		t.Fatalf("over-capacity add: %v", err)
	}
}

func TestTransactionMalformedPacket(t *testing.T) {
	tbl := NewTransactionTable(4)
	if v := tbl.Deliver(&inbound{raw: []byte{0, 1, 2}, source: "x"}); v != "malformed" {
		t.Fatalf("verdict = %q, want malformed", v)
	}
}
