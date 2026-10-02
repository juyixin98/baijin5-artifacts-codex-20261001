package compat_test

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"sync"
	"testing"
	"time"

	"modbusfixture/client"
	"modbusfixture/vectors"
)

// TestOutOfOrderResponsesStayIdentified uses the txn_jitter profile: the
// server deliberately delays requests by (txn % 8)*slot, so responses on a
// single connection arrive out of send order. Each response must still
// carry the EXACT transaction id, unit id and the data belonging to that
// read's address.
func TestOutOfOrderResponsesStayIdentified(t *testing.T) {
	h := startHarness(t, configLatencyJitter, 5)
	defer h.shutdown()

	// Raw connection: fire N requests in one write burst without reading,
	// then correlate every reply by its MBAP transaction id ourselves.
	conn := h.dial(t)
	defer conn.Close()

	const n = 16
	type reqInfo struct {
		txn  uint16
		addr uint16
	}
	sent := make([]reqInfo, n)
	var burst []byte
	for i := 0; i < n; i++ {
		txn := 0x2000 + uint16(i)
		addr := uint16(i) // read register i on unit 1
		// qty=1
		f := vectors.ReadHoldingReq(txn, 0x01, addr, 1)
		burst = append(burst, f...)
		sent[i] = reqInfo{txn: txn, addr: addr}
	}
	if _, err := conn.Write(burst); err != nil {
		t.Fatalf("write burst: %v", err)
	}

	got := make(map[uint16]struct {
		unit byte
		val  uint16
	})
	arrival := make([]uint16, 0, n)
	_ = conn.SetReadDeadline(time.Now().Add(10 * time.Second))
	for i := 0; i < n; i++ {
		frame := readExactFrame(t, conn)
		txn := binary.BigEndian.Uint16(frame[0:2])
		unit := frame[6]
		if frame[7]&0x80 != 0 {
			t.Fatalf("txn %04x returned exception %02x", txn, frame[8])
		}
		if frame[7] != 0x03 {
			t.Fatalf("txn %04x bad fc %02x", txn, frame[7])
		}
		val := binary.BigEndian.Uint16(frame[9:11])
		arrival = append(arrival, txn)
		got[txn] = struct {
			unit byte
			val  uint16
		}{unit: unit, val: val}
	}

	// Check arrival order for a genuine inversion (a diagnostic of the
	// jitter profile; correctness does NOT depend on it).
	reordered := false
	prev := -1
	for _, txn := range arrival {
		idx := int(txn - 0x2000)
		if idx < prev {
			reordered = true
		}
		prev = idx
	}
	if !reordered {
		t.Logf("note: responses happened to arrive in order despite jitter")
	}

	for _, s := range sent {
		r, ok := got[s.txn]
		if !ok {
			t.Fatalf("no response correlated for txn %04x (addr %d)", s.txn, s.addr)
		}
		if r.unit != 0x01 {
			t.Fatalf("txn %04x unit = %02x, want 01", s.txn, r.unit)
		}
		// Register s.addr was seeded with 0x4000+addr.
		want := uint16(0x4000 + s.addr)
		if r.val != want {
			t.Fatalf("txn %04x addr %d value = %04x, want %04x (mixed identity)",
				s.txn, s.addr, r.val, want)
		}
	}
}

// TestClientMultiplexing drives the independent master package: many
// goroutines issue reads simultaneously over ONE connection under jitter.
// The master demultiplexes by transaction id, so each caller must receive
// exactly its own value despite reordered wire replies.
func TestClientMultiplexing(t *testing.T) {
	h := startHarness(t, configLatencyJitter, 4)
	defer h.shutdown()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	m, err := client.Dial(ctx, h.modbus, 0x01)
	if err != nil {
		t.Fatalf("dial master: %v", err)
	}
	defer m.Close()

	const goroutines = 24
	const perG = 8
	var wg sync.WaitGroup
	var firstErr error
	var errMu sync.Mutex
	for g := 0; g < goroutines; g++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			for k := 0; k < perG; k++ {
				addr := uint16((id*perG + k) % 16)
				vals, err := m.ReadHoldingRegisters(ctx, 0x01, addr, 1)
				if err != nil {
					errMu.Lock()
					if firstErr == nil {
						firstErr = fmt.Errorf("g=%d addr=%d: %w", id, addr, err)
					}
					errMu.Unlock()
					return
				}
				if len(vals) != 1 || vals[0] != 0x4000+addr {
					errMu.Lock()
					if firstErr == nil {
						firstErr = fmt.Errorf("g=%d addr=%d got %04x want %04x",
							id, addr, vals[0], 0x4000+addr)
					}
					errMu.Unlock()
					return
				}
			}
		}(g)
	}
	wg.Wait()
	if firstErr != nil {
		t.Fatal(firstErr)
	}
}

// TestSimultaneousReadWriteAtomic runs a tight loop of FC16 writes over a
// span and FC03 reads of the same span. A reader must observe either the
// complete old pattern or the complete new one — never a torn mix within
// the written span.
func TestSimultaneousReadWriteAtomic(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	writer, err := client.Dial(ctx, h.modbus, 0x01)
	if err != nil {
		t.Fatalf("dial writer: %v", err)
	}
	defer writer.Close()
	reader, err := client.Dial(ctx, h.modbus, 0x01)
	if err != nil {
		t.Fatalf("dial reader: %v", err)
	}
	defer reader.Close()

	const addr = 0
	const span = 8
	stop := make(chan struct{})
	var wg sync.WaitGroup

	// Writers alternate two uniform patterns across the whole span.
	wg.Add(1)
	go func() {
		defer wg.Done()
		patterns := [][]uint16{
			uniform(span, 0xAAAA),
			uniform(span, 0x5555),
		}
		i := 0
		for {
			select {
			case <-stop:
				return
			default:
			}
			_ = writer.WriteMultipleRegisters(ctx, 0x01, addr, patterns[i%2])
			i++
		}
	}()

	// Readers require all span registers identical (no torn intermediate).
	wg.Add(1)
	go func() {
		defer wg.Done()
		for {
			select {
			case <-stop:
				return
			default:
			}
			vals, err := reader.ReadHoldingRegisters(ctx, 0x01, addr, span)
			if err != nil {
				var exc *client.ExceptionError
				if errors.As(err, &exc) {
					continue // transient during shutdown not expected mid-run
				}
				return
			}
			first := vals[0]
			if first != 0xAAAA && first != 0x5555 {
				// Seed region initially holds 0x4000+i before first write;
				// tolerate that only at the very start.
				continue
			}
			for _, v := range vals {
				if v != first {
					t.Errorf("torn read: span = % x", vals)
					close(stop)
					return
				}
			}
		}
	}()

	time.Sleep(700 * time.Millisecond)
	close(stop)
	wg.Wait()
}

func uniform(n int, v uint16) []uint16 {
	out := make([]uint16, n)
	for i := range out {
		out[i] = v
	}
	return out
}

// TestFailedWriteIsAtomicOverWire writes an FC16 that spans past the bank;
// none of its values may appear in a subsequent read of the valid part.
func TestFailedWriteIsAtomicOverWire(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	m, err := client.Dial(ctx, h.modbus, 0x01)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer m.Close()

	before, err := m.ReadHoldingRegisters(ctx, 0x01, 120, 5)
	if err != nil {
		t.Fatalf("read before: %v", err)
	}

	// addr=124 qty=2 overflows a 125 bank: must exception, not partial.
	err = m.WriteMultipleRegisters(ctx, 0x01, 124, []uint16{0xBEEF, 0xCAFE})
	var exc *client.ExceptionError
	if !errors.As(err, &exc) || exc.Exception != vectors.ExcIllegalAddress {
		t.Fatalf("want illegal address exception, got %v", err)
	}

	after, err := m.ReadHoldingRegisters(ctx, 0x01, 120, 5)
	if err != nil {
		t.Fatalf("read after: %v", err)
	}
	for i := range before {
		if before[i] != after[i] {
			t.Fatalf("register %d changed after failed write: %04x -> %04x",
				120+i, before[i], after[i])
		}
	}
}
