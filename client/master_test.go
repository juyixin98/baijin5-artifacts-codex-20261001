package client

import (
	"context"
	"encoding/binary"
	"errors"
	"io"
	"net"
	"strings"
	"sync"
	"testing"
	"time"

	"modbusfixture/mbap"
	"modbusfixture/vectors"
)

// pipeMaster returns a Master backed by net.Pipe plus the server end. The
// test acts as the slave using only vectors (independent construction).
func pipeMaster(t *testing.T) (*Master, net.Conn) {
	t.Helper()
	clientConn, serverConn := net.Pipe()
	m := &Master{
		conn:    clientConn,
		unitID:  0x01,
		reader:  mbap.NewReader(clientConn),
		waiters: make(map[uint16]chan response),
		nextTxn: 1,
		done:    make(chan struct{}),
	}
	go m.readLoop()
	t.Cleanup(func() {
		_ = m.Close()
		_ = serverConn.Close()
	})
	return m, serverConn
}

// TestReadHoldingDecodesBigEndian verifies decoding with a slave that
// answers an FC03 read by independently assembled bytes.
func TestReadHoldingDecodesBigEndian(t *testing.T) {
	m, srv := pipeMaster(t)
	ctx := context.Background()

	go func() {
		req := readOneRaw(t, srv)
		txn, unit, pdu := parseFrame(req)
		if unit != 0x01 || pdu[0] != 0x03 {
			t.Errorf("bad request: unit=%02x pdu=%x", unit, pdu)
		}
		addr := binary.BigEndian.Uint16(pdu[1:3])
		qty := binary.BigEndian.Uint16(pdu[3:5])
		if addr != 10 || qty != 2 {
			t.Errorf("addr/qty = %d/%d", addr, qty)
		}
		resp := vectors.ReadHoldingResp(txn, uint16(unit), []uint16{0x1122, 0x3344})
		writeRaw(t, srv, resp)
	}()

	vals, err := m.ReadHoldingRegisters(ctx, 0x01, 10, 2)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if vals[0] != 0x1122 || vals[1] != 0x3344 {
		t.Fatalf("values = %x, want [1122 3344]", vals)
	}
}

// TestExceptionErrorType ensures a slave exception surfaces as
// *ExceptionError with the right code and is not mistaken for data.
func TestExceptionErrorType(t *testing.T) {
	m, srv := pipeMaster(t)
	go func() {
		req := readOneRaw(t, srv)
		txn, unit, _ := parseFrame(req)
		writeRaw(t, srv, vectors.ExceptionResp(txn, unit, 0x03, vectors.ExcIllegalAddress))
	}()
	_, err := m.ReadHoldingRegisters(context.Background(), 0x01, 999, 1)
	var exc *ExceptionError
	if !errors.As(err, &exc) {
		t.Fatalf("want *ExceptionError, got %v", err)
	}
	if exc.Function != 0x03 || exc.Exception != 0x02 {
		t.Fatalf("exception = fc %02x code %02x", exc.Function, exc.Exception)
	}
}

// TestOutOfOrderDemultiplex: the slave answers request 2 BEFORE request 1
// with deliberately different delays. Both callers must get their own
// value, correlated by transaction id.
func TestOutOfOrderDemultiplex(t *testing.T) {
	m, srv := pipeMaster(t)

	// Slave reads two requests, answers the second first.
	go func() {
		r1 := readOneRaw(t, srv)
		r2 := readOneRaw(t, srv)
		txn2, unit2, _ := parseFrame(r2)
		addr2 := binary.BigEndian.Uint16(r2[8:10])
		writeRaw(t, srv, vectors.ReadHoldingResp(txn2, uint16(unit2),
			[]uint16{0x2000 + addr2}))
		txn1, unit1, _ := parseFrame(r1)
		addr1 := binary.BigEndian.Uint16(r1[8:10])
		writeRaw(t, srv, vectors.ReadHoldingResp(txn1, uint16(unit1),
			[]uint16{0x1000 + addr1}))
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	var wg sync.WaitGroup
	results := make([]uint16, 2)
	wg.Add(2)
	go func() {
		defer wg.Done()
		v, err := m.ReadHoldingRegisters(ctx, 0x01, 0x0001, 1)
		if err != nil {
			t.Errorf("req1: %v", err)
			return
		}
		results[0] = v[0]
	}()
	// Ensure req1 is dispatched first.
	time.Sleep(20 * time.Millisecond)
	go func() {
		defer wg.Done()
		v, err := m.ReadHoldingRegisters(ctx, 0x01, 0x0002, 1)
		if err != nil {
			t.Errorf("req2: %v", err)
			return
		}
		results[1] = v[0]
	}()
	wg.Wait()
	if results[0] != 0x1001 {
		t.Fatalf("request1 got %04x, want 1001", results[0])
	}
	if results[1] != 0x2002 {
		t.Fatalf("request2 got %04x, want 2002", results[1])
	}
}

// TestUnitIDMismatchRejected: a response that carries a different unit id
// than the request is a protocol error, not a delivered value.
func TestUnitIDMismatchRejected(t *testing.T) {
	m, srv := pipeMaster(t)
	go func() {
		req := readOneRaw(t, srv)
		txn, _, _ := parseFrame(req)
		writeRaw(t, srv, vectors.ReadHoldingResp(txn, 0x09, []uint16{0x0001}))
	}()
	_, err := m.ReadHoldingRegisters(context.Background(), 0x01, 0, 1)
	if err == nil || !strings.Contains(err.Error(), "unit id") {
		t.Fatalf("want unit id mismatch error, got %v", err)
	}
}

// TestRawRoundTripUnsupportedFunction sends FC04 and expects an exception.
func TestRawRoundTripUnsupportedFunction(t *testing.T) {
	m, srv := pipeMaster(t)
	go func() {
		req := readOneRaw(t, srv)
		txn, unit, pdu := parseFrame(req)
		if pdu[0] != 0x04 {
			t.Errorf("want fc04, got %02x", pdu[0])
		}
		writeRaw(t, srv, vectors.ExceptionResp(txn, unit, 0x04, vectors.ExcIllegalFunction))
	}()
	resp, err := m.RawRoundTrip(context.Background(), 0x01,
		[]byte{0x04, 0x00, 0x00, 0x00, 0x01})
	if err == nil {
		t.Fatalf("want exception, got data %x", resp)
	}
	var exc *ExceptionError
	if !errors.As(err, &exc) || exc.Exception != 0x01 {
		t.Fatalf("want illegal function, got %v", err)
	}
}

// TestConnectionCloseFailsWaiters verifies outstanding requests fail when
// the peer disappears rather than hanging forever.
func TestConnectionCloseFailsWaiters(t *testing.T) {
	clientConn, serverConn := net.Pipe()
	m := &Master{
		conn:    clientConn,
		reader:  mbap.NewReader(clientConn),
		waiters: make(map[uint16]chan response),
		nextTxn: 1,
		done:    make(chan struct{}),
	}
	go m.readLoop()

	done := make(chan error, 1)
	go func() {
		_, err := m.ReadHoldingRegisters(context.Background(), 0x01, 0, 1)
		done <- err
	}()
	time.Sleep(30 * time.Millisecond)
	_ = serverConn.Close()

	select {
	case err := <-done:
		if err == nil {
			t.Fatal("want error after peer close")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("outstanding request hung after close")
	}
}

// ---- minimal independent wire helpers for the fake slave ----

func readOneRaw(t *testing.T, c net.Conn) []byte {
	t.Helper()
	_ = c.SetReadDeadline(time.Now().Add(3 * time.Second))
	hdr := make([]byte, 7)
	if _, err := io.ReadFull(c, hdr); err != nil {
		t.Fatalf("fake slave read header: %v", err)
	}
	length := binary.BigEndian.Uint16(hdr[4:])
	body := make([]byte, int(length)-1)
	if _, err := io.ReadFull(c, body); err != nil {
		t.Fatalf("fake slave read body: %v", err)
	}
	return append(hdr, body...)
}

func writeRaw(t *testing.T, c net.Conn, b []byte) {
	t.Helper()
	if _, err := c.Write(b); err != nil {
		t.Fatalf("fake slave write: %v", err)
	}
}

func parseFrame(f []byte) (txn uint16, unit byte, pdu []byte) {
	txn = binary.BigEndian.Uint16(f[0:2])
	unit = f[6]
	pdu = f[7:]
	return
}
