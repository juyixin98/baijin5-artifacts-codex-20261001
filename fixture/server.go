package fixture

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"sync"
	"time"

	"modbusfixture/config"
	"modbusfixture/core"
	"modbusfixture/mbap"
)

// Server is the controlled Modbus TCP slave fixture.
//
// Concurrency model:
//
//	one reader goroutine per connection frames MBAP requests;
//	each request runs on a bounded worker pool (cfg.Workers) so responses on
//	a single connection may complete out of request order under the
//	txn_jitter profile;
//	a per-connection write mutex guarantees whole frames never interleave;
//	every response carries the EXACT transaction id and unit id of its
//	request — identity correlation is the mechanism that makes out-of-order
//	delivery unambiguous.
type Server struct {
	cfg    config.Config
	engine *core.Engine
	audit  *AuditStore
	log    *slog.Logger

	listener net.Listener

	wg      sync.WaitGroup
	connsMu sync.Mutex
	conns   map[*connState]struct{}

	closeMu sync.Mutex
	closed  bool

	stats *stats
}

// NewServer constructs the server. Call Listen/Addr/Serve to run it.
func NewServer(cfg config.Config, engine *core.Engine, audit *AuditStore, log *slog.Logger) *Server {
	if log == nil {
		log = slog.New(slog.NewTextHandler(io.Discard, nil))
	}
	return &Server{
		cfg:    cfg,
		engine: engine,
		audit:  audit,
		log:    log.With("component", "modbus-server", "version", config.Version),
		conns:  make(map[*connState]struct{}),
		stats:  newStats(),
	}
}

// Listen binds the TCP socket.
func (s *Server) Listen() error {
	ln, err := net.Listen("tcp", s.cfg.Listen)
	if err != nil {
		return fmt.Errorf("listen on %s: %w", s.cfg.Listen, err)
	}
	s.listener = ln
	s.log.Info("listening", "addr", ln.Addr().String())
	return nil
}

// Addr reports the bound address ( useful with listen port 0).
func (s *Server) Addr() net.Addr {
	if s.listener == nil {
		return nil
	}
	return s.listener.Addr()
}

// Serve accepts connections until the listener fails or ctx is canceled.
func (s *Server) Serve(ctx context.Context) error {
	serveErr := make(chan error, 1)
	go func() {
		for {
			c, err := s.listener.Accept()
			if err != nil {
				serveErr <- err
				return
			}
			s.wg.Add(1)
			go s.handleConn(ctx, c)
		}
	}()

	select {
	case <-ctx.Done():
		return nil
	case err := <-serveErr:
		s.closeMu.Lock()
		closed := s.closed
		s.closeMu.Unlock()
		if closed || errors.Is(err, net.ErrClosed) {
			return nil
		}
		return fmt.Errorf("accept: %w", err)
	}
}

// Close stops accepting, closes every connection, and waits for in-flight
// requests (bounded by a short grace period) to finish.
func (s *Server) Close() error {
	s.closeMu.Lock()
	if s.closed {
		s.closeMu.Unlock()
		return nil
	}
	s.closed = true
	s.closeMu.Unlock()

	var firstErr error
	if s.listener != nil {
		firstErr = s.listener.Close()
	}
	s.connsMu.Lock()
	for c := range s.conns {
		_ = c.conn.Close()
	}
	s.connsMu.Unlock()

	done := make(chan struct{})
	go func() { s.wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		s.log.Warn("grace period elapsed with in-flight requests")
	}
	return firstErr
}

// connState is one accepted TCP connection.
type connState struct {
	id     string
	conn   net.Conn
	writeM sync.Mutex
	wg     sync.WaitGroup
	// sem bounds concurrent in-flight requests on this connection.
	sem chan struct{}
}

func newConnID() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		// crypto/rand failure is environmental and fatal-grade; the
		// fallback keeps identity unique within a process.
		return fmt.Sprintf("fallback-%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(b[:])
}

func (s *Server) handleConn(ctx context.Context, c net.Conn) {
	defer s.wg.Done()

	cs := &connState{
		id:   newConnID(),
		conn: c,
		sem:  make(chan struct{}, s.cfg.Workers),
	}
	s.connsMu.Lock()
	s.conns[cs] = struct{}{}
	s.connsMu.Unlock()

	s.stats.connOpened()
	s.log.Info("connection opened",
		"conn_id", cs.id, "remote", c.RemoteAddr().String())

	defer func() {
		// Wait for dispatched requests before tearing the socket down.
		cs.wg.Wait()
		_ = c.Close()
		s.connsMu.Lock()
		delete(s.conns, cs)
		s.connsMu.Unlock()
		s.stats.connClosed()
		s.log.Info("connection closed", "conn_id", cs.id)
	}()

	reader := mbap.NewReader(c)
	for {
		h, pdu, err := reader.ReadFrame()
		if err != nil {
			if kind, partial := reader.PartialFrame(); partial {
				// Half packet: header or body truncated mid-frame.
				s.onFramingFailure(cs, h, kind, err)
			} else if errors.Is(err, mbap.ErrStreamClosed) || errors.Is(err, io.EOF) {
				return // peer closed cleanly with no partial frame
			} else {
				kind := mbap.Kind("stream_io_error")
				var fe *mbap.FrameError
				if errors.As(err, &fe) {
					kind = fe.Kind // e.g. length_too_large, wrong_protocol_id
				}
				s.onFramingFailure(cs, h, kind, err)
			}
			return
		}
		// Dispatch under the connection's concurrency bound. Holding a
		// token from the read loop applies natural backpressure: when all
		// workers are busy, the read loop stops consuming.
		select {
		case cs.sem <- struct{}{}:
		case <-ctx.Done():
			return
		}
		cs.wg.Add(1)
		go s.processRequest(ctx, cs, h, pdu)
	}
}

// onFramingFailure classifies a stream/framing failure, audits it and
// closes the connection. h may be partial: TxnID/UnitID are populated when
// the corresponding bytes had arrived.
func (s *Server) onFramingFailure(cs *connState, h mbap.Header, kind mbap.Kind, cause error) {
	s.stats.framingError()
	s.log.Warn("framing failure; closing connection",
		"conn_id", cs.id, "category", string(kind),
		"txn_id", fmt.Sprintf("0x%04X", h.TxnID),
		"unit_id", fmt.Sprintf("0x%02X", h.UnitID),
		"detail", cause.Error())
	_ = s.audit.Insert(AuditRecord{
		TS:         nowTS(),
		ConnID:     cs.id,
		RemoteAddr: cs.conn.RemoteAddr().String(),
		TxnID:      h.TxnID,
		UnitID:     h.UnitID,
		Category:   string(kind),
		RawPDUHex:  "",
	})
	// Any framing failure means stream boundaries can no longer be
	// trusted, so the connection is closed by the caller.
}

// processRequest runs one PDU on the worker pool, optionally reordered by
// the deterministic jitter profile, then writes the correlated response.
func (s *Server) processRequest(ctx context.Context, cs *connState, h mbap.Header, pdu []byte) {
	defer cs.wg.Done()
	defer func() { <-cs.sem }()

	start := time.Now()
	if s.cfg.Latency == config.LatencyTxnJitter && s.cfg.JitterSlotMS > 0 {
		buckets := 8
		wait := time.Duration(int(h.TxnID)%buckets) *
			time.Duration(s.cfg.JitterSlotMS) * time.Millisecond
		select {
		case <-time.After(wait):
		case <-ctx.Done():
			return
		}
	}

	result := s.engine.Handle(h.UnitID, pdu)
	duration := time.Since(start)

	category := ""
	if result.IsException() {
		category = exceptionCategory(result.Exception)
		s.stats.exception(result.Exception)
	} else {
		s.stats.success()
	}

	respHeader := mbap.Header{
		TxnID:      h.TxnID, // exact echo: cross-request identity
		ProtocolID: 0,
		UnitID:     h.UnitID, // unit id is echoed, never remapped
	}
	frame, err := mbap.Encode(nil, respHeader, result.ResponsePDU)
	if err != nil {
		// Encode only fails on oversize PDU; a 0x04 exception is the
		// honest fallback.
		s.log.Error("response encode failed",
			"conn_id", cs.id, "txn_id", h.TxnID, "err", err)
		return
	}

	cs.writeM.Lock()
	_, werr := cs.conn.Write(frame)
	cs.writeM.Unlock()
	if werr != nil {
		s.log.Warn("response write failed",
			"conn_id", cs.id, "txn_id", h.TxnID, "err", werr)
		return
	}

	rec := AuditRecord{
		TS:         nowTS(),
		ConnID:     cs.id,
		RemoteAddr: cs.conn.RemoteAddr().String(),
		TxnID:      h.TxnID,
		UnitID:     h.UnitID,
		Function:   result.Function,
		Exception:  result.Exception,
		Category:   category,
		DurationUS: duration.Microseconds(),
		RawPDUHex:  hex.EncodeToString(pdu),
	}
	if result.Address != 0 || result.Quantity != 0 {
		addr := int(result.Address)
		qty := int(result.Quantity)
		rec.Address, rec.Quantity = &addr, &qty
	}
	if err := s.audit.Insert(rec); err != nil {
		s.log.Error("audit insert failed", "conn_id", cs.id, "err", err)
	}

	s.log.Info("processed",
		"conn_id", cs.id, "txn_id", fmt.Sprintf("0x%04X", h.TxnID),
		"unit_id", fmt.Sprintf("0x%02X", h.UnitID),
		"fc", fmt.Sprintf("0x%02X", result.Function),
		"addr", result.Address, "qty", result.Quantity,
		"exception", exceptionLogValue(result.Exception),
		"category", category,
		"duration_us", duration.Microseconds())
}

// exceptionCategory maps a wire exception code to the audit category. The
// engine owns the canonical category on its error path, but the engine
// interface returns PDUs; this map is the single server-side translation.
func exceptionCategory(exception byte) string {
	switch exception {
	case core.ExcIllegalFunction:
		return string(core.CatBadFunction)
	case core.ExcIllegalAddress:
		return string(core.CatBadAddress)
	case core.ExcIllegalValue:
		return string(core.CatBadValue)
	case core.ExcGatewayTarget:
		return string(core.CatGatewayFailure)
	default:
		return string(core.CatServerFailure)
	}
}

func exceptionLogValue(exc byte) any {
	if exc == 0 {
		return nil
	}
	return fmt.Sprintf("0x%02X", exc)
}
