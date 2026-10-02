package compat

import (
	"errors"
	"fmt"
	"net"
	"sync"
	"testing"
	"time"

	"mbfixture/internal/mbclient"
	"mbfixture/internal/mbcodec"
	"mbfixture/internal/mbproto"
)

// dialClient connects a master to the fixture.
func dialClient(t *testing.T, addr string) *mbclient.Client {
	t.Helper()
	c, err := mbclient.Dial(addr, 2*time.Second, testLogger("client"))
	if err != nil {
		t.Fatalf("dial client: %v", err)
	}
	t.Cleanup(func() { c.Close() })
	return c
}

// TestClientRoundTrip exercises the master API end to end: write a pattern,
// read it back, and confirm an out-of-range read surfaces as a typed
// Modbus exception, not as data.
func TestClientRoundTrip(t *testing.T) {
	addr := startServer(t, 16, nil)
	c := dialClient(t, addr)

	want := []uint16{0x0001, 0x00FF, 0xFF00, 0xABCD}
	if err := c.WriteMultipleRegisters(1, 4, want); err != nil {
		t.Fatalf("write: %v", err)
	}
	got, err := c.ReadHoldingRegisters(1, 4, 4)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("reg %d: got 0x%04X want 0x%04X", i, got[i], want[i])
		}
	}

	_, err = c.ReadHoldingRegisters(1, 15, 2) // 15+2 > 16 registers
	var exc *mbproto.ExceptionError
	if !errors.As(err, &exc) {
		t.Fatalf("out-of-range read: got %T %v, want ExceptionError", err, err)
	}
	if exc.Code != mbproto.ExcIllegalDataAddress {
		t.Fatalf("exception code: got 0x%02X want 0x%02X", exc.Code, mbproto.ExcIllegalDataAddress)
	}
}

// TestClientConcurrentIdentity fires many concurrent requests over one
// connection while the server reorders responses. Every caller must receive
// exactly the answer to its own request.
func TestClientConcurrentIdentity(t *testing.T) {
	// Delay reads of even addresses so responses interleave.
	delay := func(h mbcodec.Header, pdu []byte) time.Duration {
		if len(pdu) >= 3 && pdu[0] == mbproto.FuncReadHoldingRegisters {
			if pdu[2]%2 == 0 {
				return 30 * time.Millisecond
			}
		}
		return 0
	}
	addr := startServer(t, 64, delay)
	c := dialClient(t, addr)

	const workers = 24
	var wg sync.WaitGroup
	errs := make(chan error, workers*2)
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			regAddr := uint16(w % 32)
			marker := uint16(0xC000 | w)
			if err := c.WriteMultipleRegisters(1, regAddr, []uint16{marker}); err != nil {
				errs <- fmt.Errorf("worker %d write: %w", w, err)
				return
			}
			// Workers sharing an address race with each other, so only
			// verify reads on addresses this worker owns exclusively:
			// use the upper half as private scratch (one per worker).
			privAddr := uint16(32 + w%16)
			if w < 16 {
				if err := c.WriteMultipleRegisters(1, privAddr, []uint16{marker}); err != nil {
					errs <- fmt.Errorf("worker %d private write: %w", w, err)
					return
				}
				got, err := c.ReadHoldingRegisters(1, privAddr, 1)
				if err != nil {
					errs <- fmt.Errorf("worker %d read: %w", w, err)
					return
				}
				if got[0] != marker {
					errs <- fmt.Errorf("worker %d: got 0x%04X want 0x%04X (identity mix-up)",
						w, got[0], marker)
				}
			}
		}(w)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Error(err)
	}
}

// TestClientIgnoresForeignTxID runs the master against a fake slave that
// first answers with the wrong transaction ID and then with the right one;
// the client must ignore the foreign frame and still complete the request.
func TestClientIgnoresForeignTxID(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	t.Cleanup(func() { ln.Close() })

	go func() {
		conn, err := ln.Accept()
		if err != nil {
			return
		}
		defer conn.Close()
		frame, err := mbcodec.ReadFrame(conn)
		if err != nil {
			return
		}
		// 1) response with an unknown transaction ID: must be dropped.
		mbcodec.WriteFrame(conn, mbcodec.Header{
			TxID: frame.Header.TxID + 100, UnitID: frame.Header.UnitID,
		}, mbcodec.EncodeReadHoldingResponse([]uint16{0xDEAD}))
		// 2) correct response.
		mbcodec.WriteFrame(conn, mbcodec.Header{
			TxID: frame.Header.TxID, UnitID: frame.Header.UnitID,
		}, mbcodec.EncodeReadHoldingResponse([]uint16{0x00BE}))
	}()

	c := dialClient(t, ln.Addr().String())
	got, err := c.ReadHoldingRegisters(1, 0, 1)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if len(got) != 1 || got[0] != 0x00BE {
		t.Fatalf("got %v, want [0x00BE]; foreign txid frame was misdelivered", got)
	}
}

// TestWireAtomicity hammers the fixture over TCP with uniform-generation
// writes while a concurrent reader checks it never observes a torn window.
func TestWireAtomicity(t *testing.T) {
	addr := startServer(t, 8, nil)
	writer := dialClient(t, addr)
	reader := dialClient(t, addr)

	stop := make(chan struct{})
	var wwg sync.WaitGroup
	for g := 0; g < 2; g++ {
		wwg.Add(1)
		go func(gen uint16) {
			defer wwg.Done()
			v := gen*0x100 + 0x11 // 0x0011 and 0x0111: uniform per generation
			for {
				select {
				case <-stop:
					return
				default:
				}
				if err := writer.WriteMultipleRegisters(1, 0,
					[]uint16{v, v, v, v}); err != nil {
					return // connection closing during shutdown
				}
			}
		}(uint16(g))
	}

	for i := 0; i < 300; i++ {
		vals, err := reader.ReadHoldingRegisters(1, 0, 4)
		if err != nil {
			t.Fatalf("read: %v", err)
		}
		for _, v := range vals[1:] {
			if v != vals[0] {
				t.Fatalf("torn read over the wire: %v", vals)
			}
		}
	}
	close(stop)
	wwg.Wait()
}

// TestLogRunIDStable checks that the log path actually runs: every record
// carries a non-empty run ID and logging must not panic.
func TestLogRunIDStable(t *testing.T) {
	l := testLogger("x")
	if l.RunID() == "" {
		t.Fatal("empty run id")
	}
	l.Event("selftest", "k", "v") // must not panic
}
