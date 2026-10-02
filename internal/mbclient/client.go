// Package mbclient implements the Modbus TCP master (client) half of the
// fixture. Multiple requests may be in flight on one connection; responses
// are matched to requests strictly by transaction ID, so out-of-order
// responses are handled correctly and a response can never be delivered to
// the wrong caller.
package mbclient

import (
	"errors"
	"fmt"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"mbfixture/internal/mbcodec"
	"mbfixture/internal/mblog"
	"mbfixture/internal/mbproto"
)

// ErrTimeout is returned when no matching response arrives in time.
var ErrTimeout = errors.New("modbus: response timeout")

// ErrClosed is returned when the connection is closed underneath a caller.
var ErrClosed = errors.New("modbus: connection closed")

// DefaultTimeout applies when Dial is given a zero timeout.
const DefaultTimeout = 3 * time.Second

// request captures what a pending caller expects, so the response can be
// validated against the originating request, not just delivered.
type request struct {
	unit uint8
	fn   func(f mbcodec.Frame) error // validates and decodes the response
	ch   chan error
}

// Client is a Modbus TCP master. Safe for concurrent use.
type Client struct {
	conn     net.Conn
	log      *mblog.Logger
	timeout  time.Duration
	wmu      sync.Mutex // serializes frame writes
	mu       sync.Mutex // guards pending
	pending  map[uint16]*request
	nextTx   atomic.Uint32
	done     chan struct{}
	closeErr error
	once     sync.Once
}

// Dial connects to a Modbus TCP slave at addr ("host:port").
func Dial(addr string, timeout time.Duration, logger *mblog.Logger) (*Client, error) {
	if timeout <= 0 {
		timeout = DefaultTimeout
	}
	conn, err := net.DialTimeout("tcp", addr, timeout)
	if err != nil {
		return nil, fmt.Errorf("dial %s: %w", addr, err)
	}
	c := &Client{
		conn:    conn,
		log:     logger,
		timeout: timeout,
		pending: map[uint16]*request{},
		done:    make(chan struct{}),
	}
	go c.readLoop()
	return c, nil
}

// Close shuts the connection down.
func (c *Client) Close() error {
	c.once.Do(func() {
		close(c.done)
		c.conn.Close()
	})
	return nil
}

// ReadHoldingRegisters issues FC03 and returns qty register values.
func (c *Client) ReadHoldingRegisters(unit uint8, addr, qty uint16) ([]uint16, error) {
	if qty == 0 || qty > mbproto.MaxReadQuantity {
		return nil, fmt.Errorf("qty %d out of range [1,%d]", qty, mbproto.MaxReadQuantity)
	}
	var out []uint16
	err := c.roundTrip(unit, mbcodec.EncodeReadHoldingRequest(addr, qty),
		func(f mbcodec.Frame) error {
			if err := checkException(f, mbproto.FuncReadHoldingRegisters); err != nil {
				return err
			}
			v, err := mbcodec.DecodeReadHoldingResponse(f.PDU, qty)
			if err != nil {
				return err
			}
			out = v
			return nil
		})
	return out, err
}

// WriteMultipleRegisters issues FC16. The slave applies the write
// atomically; the response is validated as an echo of addr and quantity.
func (c *Client) WriteMultipleRegisters(unit uint8, addr uint16, values []uint16) error {
	if len(values) == 0 || len(values) > mbproto.MaxWriteQuantity {
		return fmt.Errorf("qty %d out of range [1,%d]", len(values), mbproto.MaxWriteQuantity)
	}
	return c.roundTrip(unit, mbcodec.EncodeWriteMultipleRequest(addr, values),
		func(f mbcodec.Frame) error {
			if err := checkException(f, mbproto.FuncWriteMultipleRegisters); err != nil {
				return err
			}
			rAddr, rQty, err := mbcodec.DecodeWriteMultipleResponse(f.PDU)
			if err != nil {
				return err
			}
			if rAddr != addr || int(rQty) != len(values) {
				return fmt.Errorf("fc16: echo mismatch addr=%d qty=%d, want addr=%d qty=%d",
					rAddr, rQty, addr, len(values))
			}
			return nil
		})
}

// checkException maps an exception response to *mbproto.ExceptionError and
// rejects responses for a different function than the request.
func checkException(f mbcodec.Frame, wantFunc uint8) error {
	if fn, code, ok := mbcodec.DecodeException(f.PDU); ok {
		return &mbproto.ExceptionError{Func: fn, Code: code}
	}
	if f.PDU[0] != wantFunc {
		return fmt.Errorf("response func 0x%02X, want 0x%02X", f.PDU[0], wantFunc)
	}
	return nil
}

// roundTrip registers a pending request, sends the frame, and waits for the
// matching response or a timeout.
func (c *Client) roundTrip(unit uint8, pdu []byte, fn func(mbcodec.Frame) error) error {
	txID := uint16(c.nextTx.Add(1))
	req := &request{unit: unit, fn: fn, ch: make(chan error, 1)}
	c.mu.Lock()
	if c.closeErr != nil {
		c.mu.Unlock()
		return c.closeErr
	}
	c.pending[txID] = req
	c.mu.Unlock()
	defer func() {
		c.mu.Lock()
		delete(c.pending, txID)
		c.mu.Unlock()
	}()

	h := mbcodec.Header{TxID: txID, UnitID: unit}
	c.wmu.Lock()
	err := mbcodec.WriteFrame(c.conn, h, pdu)
	c.wmu.Unlock()
	if err != nil {
		return fmt.Errorf("write request txid=%d: %w", txID, err)
	}
	c.log.Event("request_tx", "txid", txID, "unit", unit,
		"func", fmt.Sprintf("0x%02X", pdu[0]))

	select {
	case err := <-req.ch:
		return err
	case <-time.After(c.timeout):
		return fmt.Errorf("txid=%d unit=%d: %w", txID, unit, ErrTimeout)
	case <-c.done:
		return ErrClosed
	}
}

// readLoop is the only reader of the connection. It routes each response to
// the pending request with the same transaction ID; responses for unknown
// transaction IDs are logged and dropped, never misdelivered.
func (c *Client) readLoop() {
	for {
		frame, err := mbcodec.ReadFrame(c.conn)
		if err != nil {
			c.failAll(fmt.Errorf("read loop: %w", err))
			return
		}
		txID := frame.Header.TxID
		c.mu.Lock()
		req, ok := c.pending[txID]
		c.mu.Unlock()
		if !ok {
			c.log.Event("response_orphan", "txid", txID,
				"reason", "no pending request for transaction id")
			continue
		}
		if frame.Header.UnitID != req.unit {
			req.ch <- fmt.Errorf("txid=%d: response unit %d, want %d",
				txID, frame.Header.UnitID, req.unit)
			continue
		}
		c.log.Event("response_rx", "txid", txID, "unit", frame.Header.UnitID,
			"func", fmt.Sprintf("0x%02X", frame.PDU[0]))
		req.ch <- req.fn(frame)
	}
}

// failAll wakes every pending caller with err.
func (c *Client) failAll(err error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.closeErr = err
	for id, req := range c.pending {
		req.ch <- err
		delete(c.pending, id)
	}
}
