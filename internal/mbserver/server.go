// Package mbserver implements the Modbus TCP slave (server) half of the
// fixture. Each accepted connection runs a frame loop; every well-formed
// request is dispatched to its own goroutine, so responses on one
// connection may be emitted out of order — request identity is preserved
// exclusively through the echoed transaction ID. All writes to a
// connection are serialized through a per-connection mutex so frames can
// never be interleaved on the wire.
package mbserver

import (
	"errors"
	"fmt"
	"io"
	"net"
	"sync"
	"time"

	"mbfixture/internal/mbcodec"
	"mbfixture/internal/mblog"
	"mbfixture/internal/mbproto"
	"mbfixture/internal/mbstore"
)

// Server is a Modbus TCP slave fixture.
type Server struct {
	units  map[uint8]bool
	store  *mbstore.Store
	log    *mblog.Logger
	mu     sync.Mutex // guards ln
	ln     net.Listener
	wg     sync.WaitGroup
	closed chan struct{}
	once   sync.Once

	// DelayFunc, when non-nil, is consulted before handling each request
	// and the returned duration is slept. Tests use it to force response
	// reordering; production wiring leaves it nil.
	DelayFunc func(h mbcodec.Header, pdu []byte) time.Duration
}

// New creates a server answering for the given unit IDs, backed by store.
func New(unitIDs []uint8, store *mbstore.Store, logger *mblog.Logger) (*Server, error) {
	if len(unitIDs) == 0 {
		return nil, fmt.Errorf("server needs at least one unit id")
	}
	s := &Server{
		units:  map[uint8]bool{},
		store:  store,
		log:    logger,
		closed: make(chan struct{}),
	}
	for _, u := range unitIDs {
		s.units[u] = true
		if err := store.EnsureUnit(u); err != nil {
			return nil, fmt.Errorf("provision unit %d: %w", u, err)
		}
	}
	return s, nil
}

// Serve accepts connections on ln until Close is called. The listener is
// adopted by the server.
func (s *Server) Serve(ln net.Listener) error {
	s.mu.Lock()
	s.ln = ln
	s.mu.Unlock()
	s.log.Event("listen", "addr", ln.Addr().String())
	for {
		conn, err := ln.Accept()
		if err != nil {
			select {
			case <-s.closed:
				s.wg.Wait()
				return nil
			default:
				return fmt.Errorf("accept: %w", err)
			}
		}
		s.wg.Add(1)
		go s.handleConn(conn)
	}
}

// Addr returns the bound address, or "" if not serving yet.
func (s *Server) Addr() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.ln == nil {
		return ""
	}
	return s.ln.Addr().String()
}

// Close stops accepting and waits for in-flight connections. It is
// idempotent.
func (s *Server) Close() error {
	s.once.Do(func() {
		close(s.closed)
		s.mu.Lock()
		ln := s.ln
		s.mu.Unlock()
		if ln != nil {
			ln.Close()
		}
	})
	return nil
}

// connState serializes writes to one connection.
type connState struct {
	conn net.Conn
	wmu  sync.Mutex
}

func (s *Server) handleConn(conn net.Conn) {
	defer s.wg.Done()
	defer conn.Close()
	cs := &connState{conn: conn}
	remote := conn.RemoteAddr().String()
	s.log.Event("conn_open", "remote", remote)
	defer s.log.Event("conn_close", "remote", remote)

	var reqWG sync.WaitGroup
	defer reqWG.Wait() // drain in-flight requests before closing

	for {
		frame, err := mbcodec.ReadFrame(conn)
		if err != nil {
			classifyReadError(s.log, remote, err)
			return // unrecoverable framing state: close the connection
		}
		s.log.Event("frame_rx",
			"remote", remote,
			"txid", frame.Header.TxID,
			"unit", frame.Header.UnitID,
			"func", fmt.Sprintf("0x%02X", frame.PDU[0]),
			"pdu_len", len(frame.PDU),
			"frame_fp", mblog.FrameFingerprint(frameBytes(frame)),
		)
		reqWG.Add(1)
		go func() {
			defer reqWG.Done()
			s.serveRequest(cs, remote, frame)
		}()
	}
}

// frameBytes rebuilds the raw ADU for fingerprinting.
func frameBytes(f mbcodec.Frame) []byte {
	hb := mbcodec.EncodeHeader(f.Header)
	out := make([]byte, 0, len(hb)+len(f.PDU))
	out = append(out, hb[:]...)
	return append(out, f.PDU...)
}

// classifyReadError separates clean disconnects from protocol violations so
// the log states the failure category explicitly.
func classifyReadError(log *mblog.Logger, remote string, err error) {
	switch {
	case errors.Is(err, io.EOF), errors.Is(err, io.ErrUnexpectedEOF),
		errors.Is(err, net.ErrClosed):
		log.Event("conn_eof", "remote", remote, "reason", err.Error())
	case errors.Is(err, mbcodec.ErrProtocolID):
		log.Event("conn_protocol_error", "remote", remote,
			"category", "protocol_id", "reason", err.Error())
	case errors.Is(err, mbcodec.ErrLengthRange):
		log.Event("conn_protocol_error", "remote", remote,
			"category", "mbap_length", "reason", err.Error())
	default:
		log.Event("conn_read_error", "remote", remote,
			"category", "io", "reason", err.Error())
	}
}

// serveRequest handles one request frame and writes the response under the
// connection write lock. Because requests run in separate goroutines, the
// response order on the wire may differ from the request order; identity is
// carried by the echoed TxID.
func (s *Server) serveRequest(cs *connState, remote string, frame mbcodec.Frame) {
	h := frame.Header
	if s.DelayFunc != nil {
		if d := s.DelayFunc(h, frame.PDU); d > 0 {
			time.Sleep(d)
		}
	}
	respPDU, respond := s.dispatch(h, frame.PDU, remote)
	if !respond {
		return
	}
	cs.wmu.Lock()
	err := mbcodec.WriteFrame(cs.conn, mbcodec.Header{TxID: h.TxID, UnitID: h.UnitID}, respPDU)
	cs.wmu.Unlock()
	if err != nil {
		s.log.Event("frame_tx_error", "remote", remote, "txid", h.TxID, "reason", err.Error())
		return
	}
	s.log.Event("frame_tx",
		"remote", remote,
		"txid", h.TxID,
		"unit", h.UnitID,
		"func", fmt.Sprintf("0x%02X", respPDU[0]),
		"pdu_len", len(respPDU),
	)
}

// dispatch validates unit ID and function code and routes to the handler.
// It returns the response PDU and whether to send it at all.
func (s *Server) dispatch(h mbcodec.Header, pdu []byte, remote string) ([]byte, bool) {
	if len(pdu) == 0 {
		// Cannot happen: MBAP length >= 2 guarantees at least one PDU byte.
		return nil, false
	}
	funcCode := pdu[0]
	if !s.units[h.UnitID] {
		// Unit ID validated independently of everything else: this slave
		// does not emulate the requested unit, so it answers with the
		// gateway-style exception instead of silently dropping.
		s.log.Event("request_rejected", "remote", remote, "txid", h.TxID,
			"unit", h.UnitID, "func", fmt.Sprintf("0x%02X", funcCode),
			"reason", "unknown_unit_id",
			"exception", mbproto.ExceptionCodeName(mbproto.ExcGatewayTargetNotResponding))
		return mbcodec.EncodeException(funcCode, mbproto.ExcGatewayTargetNotResponding), true
	}
	switch funcCode {
	case mbproto.FuncReadHoldingRegisters:
		return s.handleRead(h, funcCode, pdu, remote), true
	case mbproto.FuncWriteMultipleRegisters:
		return s.handleWriteMultiple(h, funcCode, pdu, remote), true
	default:
		s.log.Event("request_rejected", "remote", remote, "txid", h.TxID,
			"unit", h.UnitID, "func", fmt.Sprintf("0x%02X", funcCode),
			"reason", "unsupported_function",
			"exception", mbproto.ExceptionCodeName(mbproto.ExcIllegalFunction))
		return mbcodec.EncodeException(funcCode, mbproto.ExcIllegalFunction), true
	}
}

func (s *Server) handleRead(h mbcodec.Header, funcCode uint8, pdu []byte, remote string) []byte {
	addr, qty, err := mbcodec.DecodeReadHoldingRequest(pdu)
	if err != nil {
		return s.reject(h, funcCode, remote, "malformed_read_request", err, mbproto.ExcIllegalDataValue)
	}
	if qty == 0 || qty > mbproto.MaxReadQuantity {
		return s.reject(h, funcCode, remote, "read_quantity_out_of_range",
			fmt.Errorf("qty=%d not in [1,%d]", qty, mbproto.MaxReadQuantity),
			mbproto.ExcIllegalDataValue)
	}
	values, err := s.store.Read(h.UnitID, addr, qty)
	if err != nil {
		if errors.Is(err, mbstore.ErrAddressRange) {
			return s.reject(h, funcCode, remote, "read_address_out_of_range", err, mbproto.ExcIllegalDataAddress)
		}
		return s.reject(h, funcCode, remote, "read_store_error", err, mbproto.ExcServerDeviceFailure)
	}
	s.log.Event("read_ok", "remote", remote, "txid", h.TxID, "unit", h.UnitID,
		"addr", addr, "qty", qty)
	return mbcodec.EncodeReadHoldingResponse(values)
}

func (s *Server) handleWriteMultiple(h mbcodec.Header, funcCode uint8, pdu []byte, remote string) []byte {
	addr, values, err := mbcodec.DecodeWriteMultipleRequest(pdu)
	if err != nil {
		// Covers quantity/byte-count mismatch and truncated PDUs.
		return s.reject(h, funcCode, remote, "malformed_write_request", err, mbproto.ExcIllegalDataValue)
	}
	if len(values) == 0 || len(values) > mbproto.MaxWriteQuantity {
		return s.reject(h, funcCode, remote, "write_quantity_out_of_range",
			fmt.Errorf("qty=%d not in [1,%d]", len(values), mbproto.MaxWriteQuantity),
			mbproto.ExcIllegalDataValue)
	}
	// The store applies the whole range in one transaction: a concurrent
	// reader observes either all old or all new values, never a mixture.
	if err := s.store.WriteMultiple(h.UnitID, addr, values); err != nil {
		if errors.Is(err, mbstore.ErrAddressRange) {
			return s.reject(h, funcCode, remote, "write_address_out_of_range", err, mbproto.ExcIllegalDataAddress)
		}
		return s.reject(h, funcCode, remote, "write_store_error", err, mbproto.ExcServerDeviceFailure)
	}
	s.log.Event("write_ok", "remote", remote, "txid", h.TxID, "unit", h.UnitID,
		"addr", addr, "qty", len(values))
	return mbcodec.EncodeWriteMultipleResponse(addr, uint16(len(values)))
}

// reject logs the rejection with its category and builds the exception PDU.
func (s *Server) reject(h mbcodec.Header, funcCode uint8, remote, reason string, err error, exc uint8) []byte {
	s.log.Event("request_rejected", "remote", remote, "txid", h.TxID,
		"unit", h.UnitID, "func", fmt.Sprintf("0x%02X", funcCode),
		"reason", reason, "detail", err.Error(),
		"exception", mbproto.ExceptionCodeName(exc))
	return mbcodec.EncodeException(funcCode, exc)
}
