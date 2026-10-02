// Package client is the Modbus TCP master used by the compatibility tests.
// It is deliberately written independently of the slave's core package: it
// only shares the mbap framing module (which is pure byte transport) and
// constructs its own PDUs, so a shared PDU implementation cannot mask a
// protocol disagreement.
//
// The client multiplexes several outstanding requests over ONE TCP
// connection: each request gets the next transaction id, a response is
// delivered to the waiter whose transaction id it carries, and replies are
// allowed to arrive out of request order.
package client

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"sync"

	"modbusfixture/mbap"
)

// ExceptionError is a Modbus exception response: function|0x80 plus the
// exception code. Callers can distinguish it from transport errors with
// errors.As.
type ExceptionError struct {
	Function  byte // original request function code
	Exception byte // exception code (1..4, 0x0B ...)
}

func (e *ExceptionError) Error() string {
	return fmt.Sprintf("modbus: exception response fc=0x%02X code=0x%02X",
		e.Function, e.Exception)
}

// Master is one TCP connection with transaction-id multiplexing. It is
// safe for concurrent use by many goroutines.
type Master struct {
	conn   net.Conn
	unitID byte
	writeM sync.Mutex
	reader *mbap.Reader

	mu      sync.Mutex
	waiters map[uint16]chan response
	nextTxn uint16
	closed  bool

	readErr error
	done    chan struct{}
}

type response struct {
	unitID byte
	pdu    []byte
	err    error
}

// Dial opens a connection and starts the response demultiplexer.
func Dial(ctx context.Context, address string, unitID byte) (*Master, error) {
	var d net.Dialer
	conn, err := d.DialContext(ctx, "tcp", address)
	if err != nil {
		return nil, fmt.Errorf("modbus dial %s: %w", address, err)
	}
	m := &Master{
		conn:    conn,
		unitID:  unitID,
		reader:  mbap.NewReader(conn),
		waiters: make(map[uint16]chan response),
		nextTxn: 1,
		done:    make(chan struct{}),
	}
	go m.readLoop()
	return m, nil
}

// Close terminates the connection and fails every outstanding request.
func (m *Master) Close() error {
	m.mu.Lock()
	if m.closed {
		m.mu.Unlock()
		return nil
	}
	m.closed = true
	err := m.conn.Close()
	waiters := m.waiters
	m.waiters = make(map[uint16]chan response)
	m.mu.Unlock()

	for txn, ch := range waiters {
		ch <- response{err: fmt.Errorf("client closed (txn %d): %w", txn, io.ErrClosedPipe)}
		close(ch)
	}
	return err
}

func (m *Master) readLoop() {
	defer close(m.done)
	for {
		h, pdu, err := m.reader.ReadFrame()
		if err != nil {
			m.failAll(fmt.Errorf("read response frame: %w", err))
			return
		}
		m.deliver(h, pdu)
	}
}

func (m *Master) failAll(err error) {
	m.mu.Lock()
	m.readErr = err
	waiters := m.waiters
	m.waiters = make(map[uint16]chan response)
	m.mu.Unlock()
	for _, ch := range waiters {
		ch <- response{err: err}
		close(ch)
	}
}

func (m *Master) deliver(h mbap.Header, pdu []byte) {
	m.mu.Lock()
	ch, ok := m.waiters[h.TxnID]
	if ok {
		delete(m.waiters, h.TxnID)
	}
	m.mu.Unlock()
	if !ok {
		// Unsolicited or duplicate transaction id: the contract forbids
		// mixing identities, so drop and continue rather than guessing.
		return
	}
	ch <- response{unitID: h.UnitID, pdu: pdu}
	close(ch)
}

// allocate reserves a transaction id and its response channel.
func (m *Master) allocate() (uint16, <-chan response, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return 0, nil, errors.New("modbus: master is closed")
	}
	// 65535 ids; scan handles a fully saturated connection extremely
	// unlikely at fixture scale.
	for i := 0; i < 65536; i++ {
		m.nextTxn++
		if m.nextTxn == 0 {
			m.nextTxn = 1
		}
		if _, taken := m.waiters[m.nextTxn]; !taken {
			ch := make(chan response, 1)
			m.waiters[m.nextTxn] = ch
			return m.nextTxn, ch, nil
		}
	}
	return 0, nil, errors.New("modbus: no free transaction id")
}

func (m *Master) roundTrip(ctx context.Context, unitID byte, pdu []byte) ([]byte, error) {
	txn, ch, err := m.allocate()
	if err != nil {
		return nil, err
	}
	hdr := mbap.Header{TxnID: txn, ProtocolID: 0, UnitID: unitID}
	frame, err := mbap.Encode(nil, hdr, pdu)
	if err != nil {
		m.removeWaiter(txn)
		return nil, fmt.Errorf("encode request: %w", err)
	}

	m.writeM.Lock()
	_, werr := m.conn.Write(frame)
	m.writeM.Unlock()
	if werr != nil {
		m.removeWaiter(txn)
		return nil, fmt.Errorf("write request: %w", werr)
	}

	select {
	case <-ctx.Done():
		m.removeWaiter(txn)
		return nil, ctx.Err()
	case r := <-ch:
		if r.err != nil {
			return nil, r.err
		}
		if r.unitID != unitID {
			return nil, fmt.Errorf("modbus: response unit id 0x%02X != request 0x%02X",
				r.unitID, unitID)
		}
		if len(r.pdu) == 0 {
			return nil, errors.New("modbus: empty response PDU")
		}
		if r.pdu[0]&0x80 != 0 {
			if len(r.pdu) < 2 {
				return nil, fmt.Errorf("modbus: malformed exception PDU % x", r.pdu)
			}
			return nil, &ExceptionError{
				Function:  r.pdu[0] & 0x7F,
				Exception: r.pdu[1],
			}
		}
		return r.pdu, nil
	}
}

func (m *Master) removeWaiter(txn uint16) {
	m.mu.Lock()
	ch, ok := m.waiters[txn]
	if ok {
		delete(m.waiters, txn)
	}
	m.mu.Unlock()
	if ok {
		close(ch)
	}
}

// ReadHoldingRegisters issues FC03 and returns the decoded uint16 values.
// Byte order is big-endian per the Modbus wire contract.
func (m *Master) ReadHoldingRegisters(ctx context.Context, unitID byte, address, quantity uint16) ([]uint16, error) {
	if quantity < 1 || quantity > 125 {
		return nil, fmt.Errorf("modbus: read quantity %d outside [1,125]", quantity)
	}
	pdu := make([]byte, 5)
	pdu[0] = 0x03
	binary.BigEndian.PutUint16(pdu[1:3], address)
	binary.BigEndian.PutUint16(pdu[3:5], quantity)

	resp, err := m.roundTrip(ctx, unitID, pdu)
	if err != nil {
		return nil, err
	}
	if len(resp) < 2 || resp[0] != 0x03 {
		return nil, fmt.Errorf("modbus: bad FC03 response % x", resp)
	}
	if int(resp[1]) != len(resp)-2 {
		return nil, fmt.Errorf("modbus: FC03 byte count %d != data %d",
			resp[1], len(resp)-2)
	}
	if int(resp[1]) != 2*int(quantity) {
		return nil, fmt.Errorf("modbus: FC03 returned %d bytes for %d registers",
			resp[1], quantity)
	}
	values := make([]uint16, quantity)
	for i := range values {
		values[i] = binary.BigEndian.Uint16(resp[2+2*i : 4+2*i])
	}
	return values, nil
}

// WriteMultipleRegisters issues FC16. The server applies the whole span
// atomically; on the client this is a single request carrying all values.
func (m *Master) WriteMultipleRegisters(ctx context.Context, unitID byte, address uint16, values []uint16) error {
	qty := len(values)
	if qty < 1 || qty > 123 {
		return fmt.Errorf("modbus: write quantity %d outside [1,123]", qty)
	}
	pdu := make([]byte, 0, 6+2*qty)
	pdu = append(pdu, 0x10,
		byte(address>>8), byte(address),
		byte(qty>>8), byte(qty),
		byte(2*qty))
	for _, v := range values {
		pdu = append(pdu, byte(v>>8), byte(v))
	}

	resp, err := m.roundTrip(ctx, unitID, pdu)
	if err != nil {
		return err
	}
	if len(resp) != 5 || resp[0] != 0x10 {
		return fmt.Errorf("modbus: bad FC16 response % x", resp)
	}
	if gotAddr := binary.BigEndian.Uint16(resp[1:3]); gotAddr != address {
		return fmt.Errorf("modbus: FC16 echoed address 0x%04X != 0x%04X",
			gotAddr, address)
	}
	if gotQty := binary.BigEndian.Uint16(resp[3:5]); int(gotQty) != qty {
		return fmt.Errorf("modbus: FC16 echoed quantity %d != %d", gotQty, qty)
	}
	return nil
}

// RawRoundTrip sends a caller-built PDU (first byte must be the function
// code) and returns the raw response PDU. It exists for negative tests:
// malformed quantity fields, wrong byte counts and unsupported function
// codes cannot be produced through the validated typed API.
func (m *Master) RawRoundTrip(ctx context.Context, unitID byte, pdu []byte) ([]byte, error) {
	if len(pdu) == 0 {
		return nil, errors.New("modbus: empty request PDU")
	}
	return m.roundTrip(ctx, unitID, pdu)
}

// DefaultUnitID returns the unit id the master was dialed with.
func (m *Master) DefaultUnitID() byte { return m.unitID }
