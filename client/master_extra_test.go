package client

import (
	"context"
	"encoding/binary"
	"errors"
	"net"
	"strings"
	"testing"
	"time"

	"modbusfixture/mbap"
	"modbusfixture/vectors"
)

// fakeSlave serves one connection, reading frames and replying per a
// callback that returns a raw response PDU (the MBAP header is re-attached
// with the actually received transaction/unit id). It runs over a real
// loopback listener so Dial (not just net.Pipe) is exercised.
func fakeSlave(t *testing.T, fn func(txn uint16, unit byte, pdu []byte) []byte) (addr string, stop func()) {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	done := make(chan struct{})
	go func() {
		defer close(done)
		c, err := ln.Accept()
		if err != nil {
			return
		}
		defer c.Close()
		r := mbap.NewReader(c)
		for {
			h, pdu, err := r.ReadFrame()
			if err != nil {
				return
			}
			respPDU := fn(h.TxnID, h.UnitID, pdu)
			if respPDU == nil {
				return
			}
			frame, encErr := mbap.Encode(nil,
				mbap.Header{TxnID: h.TxnID, UnitID: h.UnitID}, respPDU)
			if encErr != nil {
				return
			}
			if _, err := c.Write(frame); err != nil {
				return
			}
		}
	}()
	// Closing the listener stops new accepts; the accepted handler goroutine
	// exits on its next read/write once the client closes. We deliberately
	// do not block on done so a handler that is intentionally sleeping
	// (cancellation test) cannot stall test teardown.
	return ln.Addr().String(), func() { _ = ln.Close() }
}

func TestDialAndDefaultUnitID(t *testing.T) {
	addr, stop := fakeSlave(t, func(txn uint16, unit byte, pdu []byte) []byte {
		// Raw FC03 PDU: fc, byte count, one big-endian register.
		return []byte{0x03, 0x02, 0x77, 0x77}
	})
	defer stop()

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	m, err := Dial(ctx, addr, 0x03)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer m.Close()
	if m.DefaultUnitID() != 0x03 {
		t.Fatalf("default unit = %02x", m.DefaultUnitID())
	}
	vals, err := m.ReadHoldingRegisters(ctx, 0x03, 0, 1)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if vals[0] != 0x7777 {
		t.Fatalf("val = %04x", vals[0])
	}
}

func TestDialBadAddress(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if _, err := Dial(ctx, "127.0.0.1:1", 1); err == nil {
		t.Fatal("want dial error to closed port")
	}
}

func TestWriteMultipleRegistersRoundTrip(t *testing.T) {
	addr, stop := fakeSlave(t, func(txn uint16, unit byte, pdu []byte) []byte {
		if pdu[0] != 0x10 {
			t.Errorf("want fc10, got %02x", pdu[0])
		}
		qty := binary.BigEndian.Uint16(pdu[3:5])
		if pdu[5] != byte(2*qty) {
			t.Errorf("byte count %d != 2*qty %d", pdu[5], 2*qty)
		}
		// Echo exactly addr+qty.
		return []byte{0x10, pdu[1], pdu[2], pdu[3], pdu[4]}
	})
	defer stop()

	ctx := context.Background()
	m, err := Dial(ctx, addr, 1)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer m.Close()

	if err := m.WriteMultipleRegisters(ctx, 1, 0x000A,
		[]uint16{0x0001, 0x0002, 0xFFFF}); err != nil {
		t.Fatalf("write: %v", err)
	}
}

func TestWriteMultipleQuantityValidation(t *testing.T) {
	m, _ := pipeMaster(t)
	if err := m.WriteMultipleRegisters(context.Background(), 1, 0, nil); err == nil {
		t.Fatal("qty 0 accepted")
	}
	big := make([]uint16, 124)
	if err := m.WriteMultipleRegisters(context.Background(), 1, 0, big); err == nil {
		t.Fatal("qty 124 accepted")
	}
}

func TestReadQuantityValidation(t *testing.T) {
	m, _ := pipeMaster(t)
	if _, err := m.ReadHoldingRegisters(context.Background(), 1, 0, 0); err == nil {
		t.Fatal("qty 0 accepted")
	}
	if _, err := m.ReadHoldingRegisters(context.Background(), 1, 0, 126); err == nil {
		t.Fatal("qty 126 accepted")
	}
}

func TestWriteBadEchoAddressAndQty(t *testing.T) {
	addr, stop := fakeSlave(t, func(txn uint16, unit byte, pdu []byte) []byte {
		// Wrong echoed address.
		return []byte{0x10, 0x99, 0x99, pdu[3], pdu[4]}
	})
	defer stop()
	m, err := Dial(context.Background(), addr, 1)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer m.Close()
	err = m.WriteMultipleRegisters(context.Background(), 1, 0x10, []uint16{1})
	if err == nil || !strings.Contains(err.Error(), "echoed address") {
		t.Fatalf("want echo address error, got %v", err)
	}
}

func TestReadBadResponseShapes(t *testing.T) {
	// Wrong function code in response.
	addr, stop := fakeSlave(t, func(txn uint16, unit byte, pdu []byte) []byte {
		return []byte{0x04, 0x02, 0x00, 0x01}
	})
	defer stop()
	m, err := Dial(context.Background(), addr, 1)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer m.Close()
	if _, err := m.ReadHoldingRegisters(context.Background(), 1, 0, 1); err == nil {
		t.Fatal("accepted wrong fc response")
	}
}

func TestReadByteCountMismatch(t *testing.T) {
	addr, stop := fakeSlave(t, func(txn uint16, unit byte, pdu []byte) []byte {
		// Declares 4 data bytes but provides 2.
		return []byte{0x03, 0x04, 0x00, 0x01}
	})
	defer stop()
	m, err := Dial(context.Background(), addr, 1)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer m.Close()
	if _, err := m.ReadHoldingRegisters(context.Background(), 1, 0, 1); err == nil {
		t.Fatal("accepted byte-count/data mismatch")
	}
}

func TestReadWrongQuantityData(t *testing.T) {
	addr, stop := fakeSlave(t, func(txn uint16, unit byte, pdu []byte) []byte {
		// One register of data while two were requested.
		return []byte{0x03, 0x02, 0x00, 0x01}
	})
	defer stop()
	m, err := Dial(context.Background(), addr, 1)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer m.Close()
	if _, err := m.ReadHoldingRegisters(context.Background(), 1, 0, 2); err == nil {
		t.Fatal("accepted short data for qty 2")
	}
}

func TestMalformedExceptionPDU(t *testing.T) {
	addr, stop := fakeSlave(t, func(txn uint16, unit byte, pdu []byte) []byte {
		return []byte{0x83} // exception bit set but no code
	})
	defer stop()
	m, err := Dial(context.Background(), addr, 1)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer m.Close()
	if _, err := m.ReadHoldingRegisters(context.Background(), 1, 0, 1); err == nil {
		t.Fatal("accepted malformed exception PDU")
	}
}

func TestRoundTripContextCancel(t *testing.T) {
	// Slave never replies; cancel the request.
	addr, stop := fakeSlave(t, func(txn uint16, unit byte, pdu []byte) []byte {
		time.Sleep(2 * time.Second)
		return nil
	})
	defer stop()
	m, err := Dial(context.Background(), addr, 1)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer m.Close()

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, e := m.ReadHoldingRegisters(ctx, 1, 0, 1)
		done <- e
	}()
	time.Sleep(50 * time.Millisecond)
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("want context.Canceled, got %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("request did not honor cancellation")
	}
}

func TestRawRoundTripEmptyPDU(t *testing.T) {
	m, _ := pipeMaster(t)
	if _, err := m.RawRoundTrip(context.Background(), 1, nil); err == nil {
		t.Fatal("empty PDU accepted")
	}
}

func TestUnsolicitedResponseDropped(t *testing.T) {
	// A response whose txn id no waiter holds must be dropped, not crash.
	m, srv := pipeMaster(t)

	// Send one request, then answer it with a DIFFERENT txn (unsolicited),
	// then answer correctly.
	go func() {
		req := readOneRaw(t, srv)
		txn, unit, _ := parseFrame(req)
		// Unsolicited frame for an unused txn.
		writeRaw(t, srv, vectors.ReadHoldingResp(0xBEEF, uint16(unit), []uint16{0x0001}))
		// Correct reply.
		writeRaw(t, srv, vectors.ReadHoldingResp(txn, uint16(unit), []uint16{0x0002}))
	}()

	vals, err := m.ReadHoldingRegisters(context.Background(), 0x01, 0, 1)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if vals[0] != 0x0002 {
		t.Fatalf("val = %04x, want 0002 (unsolicited frame mishandled)", vals[0])
	}
}

func TestReadLoopErrorFailsWaiters(t *testing.T) {
	clientConn, serverConn := net.Pipe()
	m := &Master{
		conn:    clientConn,
		reader:  mbap.NewReader(clientConn),
		waiters: make(map[uint16]chan response),
		done:    make(chan struct{}),
	}
	go m.readLoop()

	errc := make(chan error, 1)
	go func() {
		_, err := m.ReadHoldingRegisters(context.Background(), 1, 0, 1)
		errc <- err
	}()
	time.Sleep(30 * time.Millisecond)
	// Write garbage that fails framing, then close; readLoop must fail waiters.
	_, _ = serverConn.Write([]byte{0x00, 0x01})
	_ = serverConn.Close()
	select {
	case err := <-errc:
		if err == nil {
			t.Fatal("want error after read loop failure")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("waiter not failed after read error")
	}
}
