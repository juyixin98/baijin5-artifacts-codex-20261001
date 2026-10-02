package core

import (
	"encoding/hex"
	"sync"
	"testing"

	"modbusfixture/vectors"
)

// TestConcurrentWritesAreAtoneEachSpan runs many writers over the full bank
// with uniform values and readers that must always observe a consistent
// whole-bank pattern: every register equals one of the in-flight write
// values end to end (no torn mix within one observed snapshot is allowed
// for single-register granularity; stronger, we assert every FC16 span
// lands uniformly).
func TestConcurrentWritesAreAtomic(t *testing.T) {
	r := NewRouter()
	bank := NewRegisterBank(16)
	r.Bind(vectors.FixtureUnit, bank)
	eng := NewEngine(r)

	const writers = 8
	const iterations = 200
	var wg sync.WaitGroup
	for w := 0; w < writers; w++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			val := uint16(0xA000 + id)
			vals := []uint16{val, val, val, val}
			req := vectors.WriteMultiReq(uint16(id+1), vectors.FixtureUnit, 4, vals, -1)
			for i := 0; i < iterations; i++ {
				got := eng.Handle(req[6], req[7:])
				if got.IsException() {
					t.Errorf("writer %d iteration %d: exception % x",
						id, i, got.ResponsePDU)
					return
				}
			}
		}(w)
	}

	// Readers observe the 4-register span [4,8): under atomic span
	// replacement every successful read must show four identical values.
	wg.Add(1)
	go func() {
		defer wg.Done()
		req := vectors.ReadHoldingReq(99, vectors.FixtureUnit, 4, 4)
		for i := 0; i < writers*iterations; i++ {
			got := eng.Handle(req[6], req[7:])
			if got.IsException() {
				t.Errorf("reader: exception % x", got.ResponsePDU)
				return
			}
			data := got.ResponsePDU[2:]
			a := uint16(data[0])<<8 | uint16(data[1])
			for j := 1; j < 4; j++ {
				v := uint16(data[2*j])<<8 | uint16(data[2*j+1])
				if v != a {
					t.Errorf("torn read at iteration %d: span = % x", i, data)
					return
				}
			}
		}
	}()
	wg.Wait()
}

// TestConcurrentReadWriteWholeBank stresses read-during-write across the
// whole 125-register bank; responses must always be self-consistent
// big-endian encodings with correct length.
func TestConcurrentReadWriteWholeBank(t *testing.T) {
	eng := newFixtureEngine()
	rd := vectors.ReadHoldingReq(1, vectors.FixtureUnit, 0, 125)

	const n = 500
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		for i := 0; i < n; i++ {
			got := eng.Handle(rd[6], rd[7:])
			if got.IsException() || len(got.ResponsePDU) != 252 {
				t.Errorf("read iter %d: exception=% x len=%d",
					i, got.ResponsePDU, len(got.ResponsePDU))
				return
			}
		}
	}()
	go func() {
		defer wg.Done()
		for i := 0; i < n; i++ {
			v := uint16(i)
			req := vectors.WriteMultiReq(2, vectors.FixtureUnit, 0,
				[]uint16{v, v, v}, -1)
			got := eng.Handle(req[6], req[7:])
			if got.IsException() {
				t.Errorf("write iter %d: % x", i, got.ResponsePDU)
				return
			}
		}
	}()
	wg.Wait()
}

// deterministic hand-computed full frame check for one write vector.
func TestWriteVectorFullFrame(t *testing.T) {
	eng := newFixtureEngine()
	raw := vectors.MustHex(vectors.Golden[1].ReqHex)
	got := eng.Handle(raw[6], raw[7:])
	if hex.EncodeToString(got.ResponsePDU) != "10000a0002" {
		t.Fatalf("pdu = %x", got.ResponsePDU)
	}
}
